from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import uuid

from openstack_perf.artifacts import read_run_artifact, write_new_run_artifact
from openstack_perf.backend_reachability import (
    BackendTarget,
    observe_backend_reachability,
    require_approved_bastion,
)
from openstack_perf.comparison import RunComparison, compare_runs
from openstack_perf.config import RuntimeConfig, artifact_name_slug
from openstack_perf.connection import create_connection
from openstack_perf.fingerprints import collect_environment_fingerprint
from openstack_perf.infrastructure_state import observe_server_attachment
from openstack_perf.models import Environment, ExecutionStatus
from openstack_perf.observations import workflow_results_to_observation
from openstack_perf.platform_discovery import (
    observe_boot_image,
    observe_service_discovery,
    validate_project_scope,
)
from openstack_perf.product_http import (
    observe_corporate_web_application,
    observe_page_delivery,
    observe_service_http_endpoints,
)
from openstack_perf.results import (
    AssertionResult,
    FunctionalVerdict,
    RegressionRunResult,
    RunMetadata,
    RunRole,
    ScenarioObservation,
)
from openstack_perf.vm_lifecycle import run_vm_lifecycle


class RunnerError(RuntimeError):
    """Raised when a complete regression run cannot be executed safely."""


class LiveAuthorizationError(RunnerError):
    """Raised when both explicit live authorization gates are not present."""


@dataclass(frozen=True)
class RunOutcome:
    run: RegressionRunResult
    artifact_path: Path
    comparison: RunComparison | None = None


def require_live_authorization(live: bool, environ=None) -> None:
    environment = os.environ if environ is None else environ
    if live is not True or environment.get("OPENSTACK_PERF_RUN_LIVE") != "1":
        raise LiveAuthorizationError(
            "live execution requires --live and OPENSTACK_PERF_RUN_LIVE=1"
        )


def run_regression(
    config: RuntimeConfig,
    *,
    role: RunRole,
    output_dir: str | Path,
    live: bool,
    baseline_path: str | Path | None = None,
    environ=None,
    now=None,
    run_uuid=None,
) -> RunOutcome:
    """Coordinate one complete regression run using existing capabilities."""
    if role is RunRole.BASELINE and baseline_path is not None:
        raise RunnerError("a baseline run cannot compare against another baseline")
    if baseline_path is not None and role is not RunRole.CANDIDATE:
        raise RunnerError("--baseline is valid only for a candidate run")
    _validate_run_paths(output_dir, baseline_path)
    require_live_authorization(live, environ)

    clock = now or (lambda: datetime.now(timezone.utc))
    identifier = run_uuid or uuid.uuid4
    started_at = _aware_utc(clock(), "run start")
    run_id = str(identifier())
    artifact_path = _artifact_path(
        output_dir, config.name, role, started_at, run_id
    )
    if artifact_path.exists():
        raise RunnerError(f"artifact already exists: {artifact_path}")
    if baseline_path is not None and (
        artifact_path.resolve() == Path(baseline_path).resolve()
    ):
        raise RunnerError("baseline input cannot be used as the output artifact")
    observations = []
    consumer = config.consumer

    consumer_connection = None
    try:
        consumer_connection = create_connection(consumer.cloud)
        consumer_connection.authorize()
        project_id = consumer_connection.current_project_id
        if not project_id:
            raise RunnerError("authenticated consumer session has no project ID")
        project = consumer_connection.identity.get_project(project_id)
        validate_project_scope(project_id, project, consumer.project)
    except Exception as exc:
        connection_error = _external_failure("consumer connection", exc)
    else:
        connection_error = None

    if connection_error is not None:
        raise RunnerError(connection_error)

    if config.scenarios.service_discovery.enabled:
        observations.append(
            observe_service_discovery(
                lambda: create_connection(consumer.cloud),
                expected_project_name=consumer.project,
                sample_count=config.scenarios.service_discovery.samples,
            )
        )

    fingerprint = collect_environment_fingerprint(config, consumer_connection)

    if config.scenarios.boot_image.enabled:
        observations.append(
            observe_boot_image(
                consumer_connection,
                expected_image_name=consumer.image,
                sample_count=config.scenarios.boot_image.samples,
            )
        )

    if config.scenarios.infrastructure_state.enabled:
        corp = config.corp
        try:
            corp_connection = create_connection(corp.cloud)
        except Exception as exc:
            observations.append(
                _failed_observation(
                    "infrastructure.server_attachment",
                    corp.server,
                    "Critical server attachment",
                    _external_failure("corp connection", exc),
                )
            )
        else:
            observations.append(
                observe_server_attachment(
                    corp_connection,
                    expected_project_name=corp.project,
                    server_name=corp.server,
                    network_name=corp.network,
                    expected_fixed_ip=corp.fixed_ip,
                )
            )

    product = config.product
    if config.scenarios.web_application.enabled:
        observations.extend(
            observe_corporate_web_application(
                product.base_url,
                expected_release_title=product.expected_release_title,
                sample_count=config.scenarios.web_application.samples,
                timeout_seconds=product.http_timeout_seconds,
                maximum_body_bytes=product.maximum_body_bytes,
            )
        )
    if config.scenarios.page_delivery.enabled:
        observations.append(
            observe_page_delivery(
                product.base_url,
                sample_count=config.scenarios.page_delivery.samples,
                timeout_seconds=product.http_timeout_seconds,
                maximum_body_bytes=product.maximum_body_bytes,
            )
        )
    if config.scenarios.application_services.enabled:
        observations.extend(
            observe_service_http_endpoints(
                product.base_url,
                product.tomcat_base_url,
                sample_count=config.scenarios.application_services.samples,
                timeout_seconds=product.http_timeout_seconds,
                maximum_body_bytes=product.maximum_body_bytes,
            )
        )
        require_approved_bastion(product.bastion)
        observations.extend(
            observe_backend_reachability(
                product.bastion,
                tuple(
                    BackendTarget(item.target_id, item.name, item.host, item.port)
                    for item in product.backends
                ),
            )
        )

    if config.scenarios.vm_lifecycle.enabled:
        observations.append(_run_vm_samples(config, consumer_connection))

    completed_at = _aware_utc(clock(), "run completion")
    run = RegressionRunResult(
        metadata=RunMetadata(
            run_id=run_id,
            role=role,
            started_at=_timestamp(started_at),
            completed_at=_timestamp(completed_at),
            clean_snapshot=config.release.clean_snapshot,
            configuration_name=config.name,
        ),
        environment=fingerprint,
        observations=tuple(observations),
    )
    write_new_run_artifact(artifact_path, run)

    comparison = None
    if baseline_path is not None:
        baseline = read_run_artifact(baseline_path)
        _validate_comparable_environments(config, baseline, run)
        comparison = compare_runs(
            baseline,
            run,
            config.comparison_policies,
            config.functional_only_keys,
        )
    return RunOutcome(run, artifact_path, comparison)


def compare_artifacts(
    config: RuntimeConfig,
    baseline_path: str | Path,
    candidate_path: str | Path,
) -> RunComparison:
    baseline = read_run_artifact(baseline_path)
    candidate = read_run_artifact(candidate_path)
    _validate_comparable_environments(config, baseline, candidate)
    return compare_runs(
        baseline,
        candidate,
        config.comparison_policies,
        config.functional_only_keys,
    )


def _run_vm_samples(config, connection):
    consumer = config.consumer
    vm_config = config.scenarios.vm_lifecycle
    scenario_id = (
        "vm.network_attachment_lifecycle"
        if vm_config.verify_network_attachment
        else "vm.lifecycle"
    )
    try:
        image_id = _resolve_resource_id(
            "image", connection.image.find_image, consumer.image
        )
        flavor_id = _resolve_resource_id(
            "flavor", connection.compute.find_flavor, consumer.flavor
        )
        network_id = _resolve_resource_id(
            "network", connection.network.find_network, consumer.network
        )
    except Exception as exc:
        return _failed_observation(
            scenario_id,
            consumer.network,
            "VM network attachment lifecycle",
            _external_failure("resource resolution", exc),
        )

    environment = Environment(
        cloud=consumer.cloud,
        region=consumer.region,
        platform_release=config.release.platform_release,
    )
    results = []
    for _sequence in range(1, vm_config.samples + 1):
        result = run_vm_lifecycle(
            connection=connection,
            environment=environment,
            server_name=f"openstack-perf-run-{uuid.uuid4().hex}",
            image_id=image_id,
            flavor_id=flavor_id,
            network_id=network_id,
            provisioning_timeout=vm_config.provisioning_timeout_seconds,
            cleanup_timeout=vm_config.cleanup_timeout_seconds,
            verify_network_attachment=vm_config.verify_network_attachment,
        )
        if result.status is ExecutionStatus.FAILED:
            result = replace(
                result,
                error_message="VM lifecycle execution failed; inspect live logs",
            )
        results.append(result)
        if result.status is ExecutionStatus.FAILED:
            break
    return workflow_results_to_observation(
        results,
        scenario_id=scenario_id,
        target_id=consumer.network,
        name="VM network attachment lifecycle",
    )


def _resolve_resource_id(label, resolver, name):
    resource = resolver(name, ignore_missing=False)
    resource_id = getattr(resource, "id", None)
    if not resource_id:
        raise RunnerError(f"resolved {label} has no usable ID")
    resource_name = getattr(resource, "name", None)
    if resource_name is not None and resource_name != name:
        raise RunnerError(f"resolved {label} name does not match configured name")
    return resource_id


def _failed_observation(scenario_id, target_id, name, error):
    return ScenarioObservation(
        scenario_id=scenario_id,
        target_id=target_id,
        name=name,
        functional_verdict=FunctionalVerdict.FAILURE,
        assertions=(AssertionResult(f"{scenario_id}.available", False, error),),
        samples=(),
        statistics=None,
        error_message=error,
    )


def _external_failure(context, error):
    if isinstance(error, RunnerError):
        return f"{context} failed: {error}"
    return f"{context} failed: {type(error).__name__}"


def _artifact_path(output_dir, configuration_name, role, started_at, run_id):
    slug = artifact_name_slug(configuration_name)
    timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    return Path(output_dir) / f"{slug}-{role.value}-{timestamp}-{run_id}.json"


def _aware_utc(value, label):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RunnerError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _timestamp(value):
    return value.isoformat().replace("+00:00", "Z")


def _validate_comparable_environments(config, baseline, candidate):
    for label, run in (("baseline", baseline), ("candidate", candidate)):
        configuration_name = run.metadata.configuration_name
        if configuration_name is None:
            raise RunnerError(
                f"{label} artifact has no configuration name and is not compatible "
                "with runner comparison"
            )
        if configuration_name != config.name:
            raise RunnerError(
                f"{label} artifact configuration does not match selected configuration"
            )
    if baseline.environment.cloud != candidate.environment.cloud:
        raise RunnerError("artifacts use different consumer clouds")
    if baseline.environment.region != candidate.environment.region:
        raise RunnerError("artifacts use different regions")


def _validate_run_paths(output_dir, baseline_path):
    output = Path(output_dir)
    if output.exists() and not output.is_dir():
        raise RunnerError("output directory path is not a directory")
    try:
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output):
            pass
    except OSError as exc:
        raise RunnerError(
            f"output directory is not writable: {type(exc).__name__}"
        ) from None
    if baseline_path is not None:
        baseline = Path(baseline_path)
        if not baseline.is_file():
            raise RunnerError("baseline artifact does not exist or is not a file")
