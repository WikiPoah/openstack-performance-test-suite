from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

import pytest

from openstack_perf.artifacts import ArtifactError, read_run_artifact
from openstack_perf.config import load_config
from openstack_perf.models import Environment, ExecutionStatus, WorkflowRunResult
from openstack_perf.results import (
    AssertionResult,
    FunctionalVerdict,
    RunRole,
    ScenarioObservation,
    TimingSample,
)
from openstack_perf.statistics import calculate_timing_statistics
from openstack_perf.runner import (
    LiveAuthorizationError,
    RunnerError,
    _resolve_resource_id,
    _validate_comparable_environments,
    compare_artifacts,
    run_regression,
)


EXAMPLE = Path(__file__).parents[2] / "config" / "regression.example.toml"
NOW = datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc)
RUN_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _observation(scenario_id, target_id, *, passed=True):
    sample = TimingSample(
        1,
        1.0,
        passed,
        None if passed else "controlled failure",
    )
    return ScenarioObservation(
        scenario_id,
        target_id,
        f"{scenario_id} observation",
        FunctionalVerdict.PASS if passed else FunctionalVerdict.FAILURE,
        (AssertionResult(f"{scenario_id}.available", passed),),
        (sample,),
        calculate_timing_statistics((1.0,)) if passed else None,
        None if passed else "controlled failure",
    )


def _connection():
    connection = MagicMock()
    connection.current_project_id = "project-id"
    connection.identity.get_project.return_value = SimpleNamespace(
        id="project-id", name="perf"
    )
    connection.config.get_api_version.return_value = None
    connection.config.get_default_microversion.return_value = None
    connection.image.find_image.return_value = SimpleNamespace(
        id="image-id", name="cirros-0.6.3-x86_64-disk"
    )
    connection.compute.find_flavor.return_value = SimpleNamespace(
        id="flavor-id", name="m1.tiny"
    )
    connection.network.find_network.return_value = SimpleNamespace(
        id="network-id", name="perf-net"
    )
    return connection


def _successful_workflow():
    return WorkflowRunResult(
        "vm.lifecycle",
        "VM lifecycle",
        Environment("devstack-perf", "RegionOne", "OpenStack 2026.1"),
        ExecutionStatus.SUCCESS,
        12.0,
    )


def _runner_patches(events=None, workflow=None):
    config = load_config(EXAMPLE)
    writable = _connection()
    corp = _connection()
    connections = MagicMock(side_effect=[writable, corp])

    def record(name, value):
        def implementation(*args, **kwargs):
            if events is not None:
                events.append(name)
            return value
        return implementation

    web = tuple(
        _observation("product.wordpress", target)
        for target in (
            "wordpress.home", "wordpress.search.release",
            "wordpress.rest.posts", "wordpress.login",
        )
    ) + tuple(
        _observation("product.static_site", target)
        for target in (
            "static.home", "static.about", "static.products",
            "static.team", "static.contact",
        )
    )
    services = tuple(
        _observation("product.service_http", target)
        for target in (
            "nginx.status", "tomcat.home", "tomcat.examples",
            "tomcat.hello_world",
        )
    )
    backends = tuple(
        _observation("product.backend_reachability", item.target_id)
        for item in config.product.backends
    )
    vm_mock = MagicMock(
        side_effect=record("vm", workflow or _successful_workflow())
    )
    patches = (
        patch("openstack_perf.runner.create_connection", connections),
        patch(
            "openstack_perf.runner.observe_service_discovery",
            side_effect=record(
                "service", _observation("identity.service_discovery", "perf")
            ),
        ),
        patch(
            "openstack_perf.runner.collect_environment_fingerprint",
            wraps=__import__(
                "openstack_perf.fingerprints", fromlist=["collect_environment_fingerprint"]
            ).collect_environment_fingerprint,
        ),
        patch(
            "openstack_perf.runner.observe_boot_image",
            side_effect=record(
                "image",
                _observation("image.boot_discovery", config.consumer.image),
            ),
        ),
        patch(
            "openstack_perf.runner.observe_server_attachment",
            side_effect=record(
                "corp",
                _observation("infrastructure.server_attachment", "corp-db"),
            ),
        ),
        patch(
            "openstack_perf.runner.observe_corporate_web_application",
            side_effect=record("web", web),
        ),
        patch(
            "openstack_perf.runner.observe_service_http_endpoints",
            side_effect=record("http-services", services),
        ),
        patch(
            "openstack_perf.runner.observe_backend_reachability",
            side_effect=record("backends", backends),
        ),
        patch(
            "openstack_perf.runner.run_vm_lifecycle",
            new=vm_mock,
        ),
    )
    return config, writable, corp, connections, patches, vm_mock


def _run_with_patches(tmp_path, config, patches, **kwargs):
    entered = []
    try:
        for item in patches:
            entered.append(item)
            item.start()
        return run_regression(
            config,
            role=kwargs.pop("role", RunRole.BASELINE),
            output_dir=tmp_path,
            live=True,
            environ={"OPENSTACK_PERF_RUN_LIVE": "1"},
            now=MagicMock(side_effect=[NOW, NOW]),
            run_uuid=lambda: RUN_UUID,
            **kwargs,
        )
    finally:
        for item in reversed(entered):
            item.stop()


@pytest.mark.parametrize(
    "live,environ",
    [(False, {"OPENSTACK_PERF_RUN_LIVE": "1"}), (True, {})],
)
def test_dual_gate_precedes_every_external_action(tmp_path, live, environ):
    config = load_config(EXAMPLE)
    with patch("openstack_perf.runner.create_connection") as connection, patch(
        "openstack_perf.runner.observe_corporate_web_application"
    ) as http, patch("openstack_perf.runner.observe_backend_reachability") as ssh:
        with pytest.raises(LiveAuthorizationError):
            run_regression(
                config,
                role=RunRole.BASELINE,
                output_dir=tmp_path,
                live=live,
                environ=environ,
            )
    connection.assert_not_called()
    http.assert_not_called()
    ssh.assert_not_called()


def test_offline_path_validation_precedes_live_gate(tmp_path):
    config = load_config(EXAMPLE)
    invalid_output = tmp_path / "result.json"
    invalid_output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RunnerError, match="not a directory"):
        run_regression(
            config,
            role=RunRole.BASELINE,
            output_dir=invalid_output,
            live=False,
            environ={},
        )


def test_unwritable_output_is_rejected_before_external_activity(tmp_path):
    config = load_config(EXAMPLE)
    with patch(
        "openstack_perf.runner.tempfile.NamedTemporaryFile",
        side_effect=PermissionError("private path detail"),
    ), patch("openstack_perf.runner.create_connection") as connection:
        with pytest.raises(RunnerError) as raised:
            run_regression(
                config,
                role=RunRole.BASELINE,
                output_dir=tmp_path,
                live=True,
                environ={"OPENSTACK_PERF_RUN_LIVE": "1"},
            )

    assert "PermissionError" in str(raised.value)
    assert "private path detail" not in str(raised.value)
    connection.assert_not_called()


def test_runner_uses_deterministic_read_only_first_vm_last_order(tmp_path):
    events = []
    config, writable, corp, connections, patches, vm_mock = _runner_patches(events)
    patches = list(patches)

    outcome = _run_with_patches(tmp_path, config, patches)

    assert events == [
        "service", "image", "corp", "web", "http-services", "backends",
        "vm", "vm", "vm",
    ]
    assert connections.call_args_list == [
        (("devstack-perf",),),
        (("devstack-corp-ro",),),
    ]
    assert outcome.run.metadata.configuration_name == config.name
    assert outcome.run.environment.cloud == "devstack-perf"
    assert outcome.run.metadata.started_at.endswith("Z")
    assert outcome.artifact_path.name == (
        "devstack-release-regression-baseline-20260904T123000Z-"
        "12345678-1234-5678-1234-567812345678.json"
    )
    assert read_run_artifact(outcome.artifact_path) == outcome.run


def test_primary_scope_is_established_before_fresh_service_connections(tmp_path):
    events = []
    config, writable, corp, connections, patches, vm_mock = _runner_patches(events)
    fresh_connections = [_connection() for _ in range(10)]
    supplied_connections = iter((writable, *fresh_connections, corp))

    def create(cloud):
        connection = next(supplied_connections)
        events.append(f"connect:{cloud}")
        return connection

    writable.authorize.side_effect = lambda: events.append("primary-authorize")
    project = writable.identity.get_project.return_value

    def get_project(project_id):
        events.append("primary-project")
        return project

    writable.identity.get_project.side_effect = get_project

    def service_discovery(factory, **_kwargs):
        events.append("service-start")
        observed = [factory() for _ in range(10)]
        assert observed == fresh_connections
        return _observation("identity.service_discovery", "perf")

    from openstack_perf.platform_discovery import validate_project_scope

    def validate(project_id, observed_project, expected_name):
        events.append("primary-validate")
        return validate_project_scope(project_id, observed_project, expected_name)

    patches = list(patches)
    patches[0] = patch("openstack_perf.runner.create_connection", side_effect=create)
    patches[1] = patch(
        "openstack_perf.runner.observe_service_discovery",
        side_effect=service_discovery,
    )
    patches.append(
        patch(
            "openstack_perf.runner.validate_project_scope",
            side_effect=validate,
        )
    )

    _run_with_patches(tmp_path, config, patches)

    assert events[:5] == [
        "connect:devstack-perf",
        "primary-authorize",
        "primary-project",
        "primary-validate",
        "service-start",
    ]
    assert events[5:15] == ["connect:devstack-perf"] * 10
    assert events[-3:] == ["vm", "vm", "vm"]


def test_vm_receives_exact_resolved_ids_and_runs_sequentially(tmp_path):
    config, writable, corp, connections, patches, vm_mock = _runner_patches()

    _run_with_patches(tmp_path, config, patches)

    assert vm_mock.call_count == 3
    for call_args in vm_mock.call_args_list:
        assert call_args.kwargs["connection"] is writable
        assert call_args.kwargs["image_id"] == "image-id"
        assert call_args.kwargs["flavor_id"] == "flavor-id"
        assert call_args.kwargs["network_id"] == "network-id"
        assert call_args.kwargs["verify_network_attachment"] is True
        assert call_args.kwargs["provisioning_timeout"] == 180
        assert call_args.kwargs["cleanup_timeout"] == 120
    writable.image.find_image.assert_called_once_with(
        config.consumer.image, ignore_missing=False
    )
    writable.compute.find_flavor.assert_called_once_with(
        config.consumer.flavor, ignore_missing=False
    )
    writable.network.find_network.assert_called_once_with(
        config.consumer.network, ignore_missing=False
    )


def test_primary_connection_scope_is_validated_before_resource_resolution(tmp_path):
    config, writable, corp, connections, patches, vm_mock = _runner_patches()

    _run_with_patches(tmp_path, config, patches)

    writable.identity.get_project.assert_called_once_with("project-id")
    assert writable.identity.get_project.return_value.name == config.consumer.project


@pytest.mark.parametrize("project_id,project_name", [(None, "perf"), ("project-id", "other")])
def test_invalid_primary_scope_blocks_resource_lookup_and_vm(
    tmp_path, project_id, project_name
):
    events = []
    config, writable, corp, connections, patches, vm_mock = _runner_patches(events)
    writable.current_project_id = project_id
    writable.identity.get_project.return_value.name = project_name

    with pytest.raises(RunnerError, match="consumer connection failed"):
        _run_with_patches(tmp_path, config, patches)

    writable.image.find_image.assert_not_called()
    writable.compute.find_flavor.assert_not_called()
    writable.network.find_network.assert_not_called()
    vm_mock.assert_not_called()
    assert events == []
    assert connections.call_count == 1


def test_disabled_service_discovery_does_not_bypass_scope_validation(tmp_path):
    config, writable, corp, connections, patches, vm_mock = _runner_patches()
    config = replace(
        config,
        scenarios=replace(
            config.scenarios,
            service_discovery=replace(
                config.scenarios.service_discovery, enabled=False
            ),
        ),
    )
    writable.identity.get_project.return_value.name = "wrong-project"

    with pytest.raises(RunnerError):
        _run_with_patches(tmp_path, config, patches)

    vm_mock.assert_not_called()


def test_project_lookup_failure_is_sanitized_and_blocks_mutation(tmp_path):
    events = []
    config, writable, corp, connections, patches, vm_mock = _runner_patches(events)
    writable.identity.get_project.side_effect = RuntimeError("token=private")

    with pytest.raises(RunnerError) as raised:
        _run_with_patches(tmp_path, config, patches)

    assert "RuntimeError" in str(raised.value)
    assert "private" not in str(raised.value)
    vm_mock.assert_not_called()
    assert events == []
    assert connections.call_count == 1


def test_connection_or_authorization_failure_blocks_mutation(tmp_path):
    events = []
    config, writable, corp, connections, patches, vm_mock = _runner_patches(events)
    writable.authorize.side_effect = RuntimeError("password=private")

    with pytest.raises(RunnerError) as raised:
        _run_with_patches(tmp_path, config, patches)

    assert "RuntimeError" in str(raised.value)
    assert "private" not in str(raised.value)
    vm_mock.assert_not_called()
    assert events == []
    assert connections.call_count == 1


def test_connection_creation_failure_blocks_mutation(tmp_path):
    events = []
    config, writable, corp, connections, patches, vm_mock = _runner_patches(events)
    patches = list(patches)
    patches[0] = patch(
        "openstack_perf.runner.create_connection",
        side_effect=RuntimeError("token=private"),
    )

    with pytest.raises(RunnerError) as raised:
        _run_with_patches(tmp_path, config, patches)

    assert "RuntimeError" in str(raised.value)
    assert "private" not in str(raised.value)
    vm_mock.assert_not_called()
    assert events == []


def test_resource_resolution_requires_exact_name_and_stops_vm():
    resolver = MagicMock(return_value=SimpleNamespace(id="id", name="other"))

    with pytest.raises(RunnerError, match="name does not match"):
        _resolve_resource_id("image", resolver, "expected")


def test_resource_resolution_failure_stops_vm_samples(tmp_path):
    config, writable, corp, connections, patches, vm_mock = _runner_patches()
    writable.image.find_image.side_effect = RuntimeError("secret detail")

    outcome = _run_with_patches(tmp_path, config, patches)

    assert outcome.run.observations[-1].functional_verdict is FunctionalVerdict.FAILURE
    assert "RuntimeError" in outcome.run.observations[-1].error_message
    assert "secret detail" not in outcome.run.observations[-1].error_message
    vm_mock.assert_not_called()


def test_first_vm_failure_stops_remaining_mutating_samples(tmp_path):
    failure = replace(
        _successful_workflow(),
        status=ExecutionStatus.FAILED,
        error_message="external sensitive detail",
    )
    config, writable, corp, connections, patches, vm_mock = _runner_patches(
        workflow=failure
    )

    outcome = _run_with_patches(tmp_path, config, patches)

    vm_observation = outcome.run.observations[-1]
    assert len(vm_observation.samples) == 1
    assert vm_mock.call_count == 1
    assert vm_observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "sensitive" not in repr(vm_observation)


def test_read_only_failure_does_not_stop_independent_observations(tmp_path):
    events = []
    config, writable, corp, connections, patches, vm_mock = _runner_patches(events)
    patches = list(patches)
    failed_web = (_observation("product.wordpress", "wordpress.home", passed=False),)
    patches[5] = patch(
        "openstack_perf.runner.observe_corporate_web_application",
        side_effect=lambda *args, **kwargs: (
            events.append("web") or failed_web
        ),
    )

    outcome = _run_with_patches(tmp_path, config, patches)

    assert outcome.run.functional_verdict is FunctionalVerdict.FAILURE
    assert "http-services" in events
    assert "backends" in events
    assert events[-1] == "vm"


def test_disabled_scenarios_are_not_invoked(tmp_path):
    config, writable, corp, connections, patches, vm_mock = _runner_patches()
    disabled = replace(
        config,
        scenarios=replace(
            config.scenarios,
            infrastructure_state=replace(
                config.scenarios.infrastructure_state, enabled=False
            ),
            web_application=replace(config.scenarios.web_application, enabled=False),
            application_services=replace(
                config.scenarios.application_services, enabled=False
            ),
        ),
    )

    outcome = _run_with_patches(tmp_path, disabled, patches)

    keys = {(item.scenario_id, item.target_id) for item in outcome.run.observations}
    assert ("infrastructure.server_attachment", "corp-db") not in keys
    assert not any(key[0].startswith("product.") for key in keys)


def test_artifact_collision_is_never_overwritten(tmp_path):
    config, writable, corp, connections, patches, vm_mock = _runner_patches()
    first = _run_with_patches(tmp_path, config, patches)
    original = first.artifact_path.read_bytes()
    config, writable, corp, connections, patches, vm_mock = _runner_patches()

    with pytest.raises(RunnerError, match="already exists"):
        _run_with_patches(tmp_path, config, patches)

    assert first.artifact_path.read_bytes() == original
    connections.assert_not_called()
    vm_mock.assert_not_called()


def test_candidate_artifact_is_written_before_baseline_is_loaded(tmp_path):
    baseline_config, *_rest, baseline_patches, _vm = _runner_patches()
    baseline = _run_with_patches(tmp_path / "baseline", baseline_config, baseline_patches)
    config, writable, corp, connections, patches, vm_mock = _runner_patches()
    events = []
    from openstack_perf import runner as runner_module

    real_write = runner_module.write_new_run_artifact
    real_read = runner_module.read_run_artifact

    def write(path, run):
        events.append("write")
        return real_write(path, run)

    def read(path):
        events.append("read-baseline")
        return real_read(path)

    with patch("openstack_perf.runner.write_new_run_artifact", side_effect=write), patch(
        "openstack_perf.runner.read_run_artifact", side_effect=read
    ):
        outcome = _run_with_patches(
            tmp_path / "candidate",
            config,
            patches,
            role=RunRole.CANDIDATE,
            baseline_path=baseline.artifact_path,
        )

    assert events[:2] == ["write", "read-baseline"]
    assert outcome.artifact_path.exists()


def test_compare_artifacts_is_offline(tmp_path):
    baseline_config, *_rest, baseline_patches, _vm = _runner_patches()
    baseline = _run_with_patches(tmp_path, baseline_config, baseline_patches)
    candidate_config, *_rest, candidate_patches, _vm = _runner_patches()
    candidate = _run_with_patches(
        tmp_path / "candidate",
        candidate_config,
        candidate_patches,
        role=RunRole.CANDIDATE,
    )

    with patch("openstack.connect") as connection, patch("subprocess.run") as ssh:
        comparison = compare_artifacts(
            baseline_config, baseline.artifact_path, candidate.artifact_path
        )

    connection.assert_not_called()
    ssh.assert_not_called()
    assert comparison.baseline_run_id == str(RUN_UUID)


def test_artifact_write_failure_remains_visible(tmp_path):
    config, writable, corp, connections, patches, vm_mock = _runner_patches()
    patches = tuple(patches) + (
        patch(
            "openstack_perf.runner.write_new_run_artifact",
            side_effect=ArtifactError("publication failed"),
        ),
    )

    with pytest.raises(ArtifactError, match="publication failed"):
        _run_with_patches(tmp_path, config, patches)


def test_comparison_requires_selected_configuration_name():
    config, *_ = _runner_patches()
    baseline = SimpleNamespace(
        metadata=SimpleNamespace(configuration_name=config.name),
        environment=SimpleNamespace(cloud="devstack-perf", region="RegionOne"),
    )
    candidate = SimpleNamespace(
        metadata=SimpleNamespace(configuration_name=config.name),
        environment=SimpleNamespace(cloud="devstack-perf", region="RegionOne"),
    )

    _validate_comparable_environments(config, baseline, candidate)
    for label, value in (
        ("baseline", "wrong"),
        ("candidate", "wrong"),
        ("baseline", None),
    ):
        changed_baseline = baseline
        changed_candidate = candidate
        if label == "baseline":
            changed_baseline = SimpleNamespace(
                metadata=SimpleNamespace(configuration_name=value),
                environment=baseline.environment,
            )
        else:
            changed_candidate = SimpleNamespace(
                metadata=SimpleNamespace(configuration_name=value),
                environment=candidate.environment,
            )
        with pytest.raises(RunnerError, match=label):
            _validate_comparable_environments(
                config, changed_baseline, changed_candidate
            )


def test_both_artifacts_with_same_wrong_configuration_are_rejected():
    config, *_ = _runner_patches()
    artifact = SimpleNamespace(
        metadata=SimpleNamespace(configuration_name="other"),
        environment=SimpleNamespace(cloud="devstack-perf", region="RegionOne"),
    )

    with pytest.raises(RunnerError, match="baseline artifact configuration"):
        _validate_comparable_environments(config, artifact, artifact)


def test_release_label_differences_are_allowed_for_comparison():
    config, *_ = _runner_patches()
    baseline = SimpleNamespace(
        metadata=SimpleNamespace(configuration_name=config.name),
        environment=SimpleNamespace(
            cloud="devstack-perf", region="RegionOne",
            platform_release="old", source_branch="old", application_release="old",
        ),
    )
    candidate = SimpleNamespace(
        metadata=SimpleNamespace(configuration_name=config.name),
        environment=SimpleNamespace(
            cloud="devstack-perf", region="RegionOne",
            platform_release="new", source_branch="new", application_release="new",
        ),
    )

    _validate_comparable_environments(config, baseline, candidate)
