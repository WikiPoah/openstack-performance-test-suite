import json
import os
from pathlib import Path
import tempfile
from typing import Any

from openstack_perf.results import (
    SCHEMA_VERSION,
    AssertionResult,
    CleanSnapshotStatus,
    EnvironmentFingerprint,
    FunctionalVerdict,
    RegressionRunResult,
    RunMetadata,
    RunRole,
    ScenarioObservation,
    ServiceVersion,
    TimingSample,
    TimingStatistics,
)
from openstack_perf.statistics import validate_timing_statistics


class ArtifactError(ValueError):
    """Raised when a regression artifact is malformed or incompatible."""


def serialize_run(run: RegressionRunResult) -> str:
    """Serialize a regression run deterministically without sensitive fields."""
    for observation in run.observations:
        try:
            validate_timing_statistics(
                observation.samples, observation.statistics
            )
        except ValueError as exc:
            raise ArtifactError(str(exc)) from None
    document = {
        "schema_version": run.schema_version,
        "run": {
            "run_id": run.metadata.run_id,
            "role": run.metadata.role.value,
            "started_at": run.metadata.started_at,
            "completed_at": run.metadata.completed_at,
            "clean_snapshot": run.metadata.clean_snapshot.value,
        },
        "environment": {
            "cloud": run.environment.cloud,
            "region": run.environment.region,
            "platform_release": run.environment.platform_release,
            "source_branch": run.environment.source_branch,
            "application_release": run.environment.application_release,
            "service_versions": [
                {"service": item.service, "version": item.version}
                for item in run.environment.service_versions
            ],
        },
        "observations": [
            _serialize_observation(observation)
            for observation in run.observations
        ],
    }
    return json.dumps(
        document,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def deserialize_run(serialized: str) -> RegressionRunResult:
    """Deserialize and validate a regression run artifact."""
    try:
        document = json.loads(serialized)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ArtifactError(f"invalid JSON artifact: {exc}") from None

    try:
        document = _mapping(document, "artifact")
        schema_version = _value(document, "schema_version", str)
        if schema_version != SCHEMA_VERSION:
            raise ArtifactError(
                f"unsupported schema version: {schema_version!r}"
            )
        run_data = _mapping(_value(document, "run", dict), "run")
        environment_data = _mapping(
            _value(document, "environment", dict), "environment"
        )
        observations_data = _value(document, "observations", list)

        metadata = RunMetadata(
            run_id=_value(run_data, "run_id", str),
            role=RunRole(_value(run_data, "role", str)),
            started_at=_value(run_data, "started_at", str),
            completed_at=_value(run_data, "completed_at", str),
            clean_snapshot=CleanSnapshotStatus(
                _value(run_data, "clean_snapshot", str)
            ),
        )
        service_versions_data = _value(
            environment_data, "service_versions", list
        )
        environment = EnvironmentFingerprint(
            cloud=_value(environment_data, "cloud", str),
            region=_value(environment_data, "region", str),
            platform_release=_value(
                environment_data, "platform_release", str
            ),
            source_branch=_value(environment_data, "source_branch", str),
            application_release=_value(
                environment_data, "application_release", str
            ),
            service_versions=tuple(
                _deserialize_service_version(item)
                for item in service_versions_data
            ),
        )
        observations = tuple(
            _deserialize_observation(item) for item in observations_data
        )
        return RegressionRunResult(
            metadata=metadata,
            environment=environment,
            observations=observations,
            schema_version=schema_version,
        )
    except ArtifactError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactError(f"malformed regression artifact: {exc}") from None


def write_run_artifact(path: str | Path, run: RegressionRunResult) -> None:
    """Atomically write a regression run artifact."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(serialize_run(run))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def read_run_artifact(path: str | Path) -> RegressionRunResult:
    return deserialize_run(Path(path).read_text(encoding="utf-8"))


def _serialize_observation(observation: ScenarioObservation) -> dict[str, Any]:
    statistics = observation.statistics
    return {
        "scenario_id": observation.scenario_id,
        "target_id": observation.target_id,
        "name": observation.name,
        "functional_verdict": observation.functional_verdict.value,
        "assertions": [
            {
                "assertion_id": assertion.assertion_id,
                "passed": assertion.passed,
                "message": assertion.message,
            }
            for assertion in observation.assertions
        ],
        "samples": [
            {
                "sequence": sample.sequence,
                "duration_seconds": sample.duration_seconds,
                "successful": sample.successful,
                "error_message": sample.error_message,
            }
            for sample in observation.samples
        ],
        "statistics": (
            {
                "sample_count": statistics.sample_count,
                "p50_seconds": statistics.p50_seconds,
                "p95_seconds": statistics.p95_seconds,
                "minimum_seconds": statistics.minimum_seconds,
                "maximum_seconds": statistics.maximum_seconds,
            }
            if statistics is not None
            else None
        ),
        "error_message": observation.error_message,
    }


def _deserialize_service_version(data: Any) -> ServiceVersion:
    data = _mapping(data, "service version")
    return ServiceVersion(
        service=_value(data, "service", str),
        version=_value(data, "version", str),
    )


def _deserialize_observation(data: Any) -> ScenarioObservation:
    data = _mapping(data, "observation")
    assertions_data = _value(data, "assertions", list)
    samples_data = _value(data, "samples", list)
    statistics_data = data.get("statistics")
    statistics = None
    if statistics_data is not None:
        statistics_data = _mapping(statistics_data, "statistics")
        statistics = TimingStatistics(
            sample_count=_value(statistics_data, "sample_count", int),
            p50_seconds=_number(statistics_data, "p50_seconds"),
            p95_seconds=_number(statistics_data, "p95_seconds"),
            minimum_seconds=_number(statistics_data, "minimum_seconds"),
            maximum_seconds=_number(statistics_data, "maximum_seconds"),
        )

    observation = ScenarioObservation(
        scenario_id=_value(data, "scenario_id", str),
        target_id=_value(data, "target_id", str),
        name=_value(data, "name", str),
        functional_verdict=FunctionalVerdict(
            _value(data, "functional_verdict", str)
        ),
        assertions=tuple(
            _deserialize_assertion(item) for item in assertions_data
        ),
        samples=tuple(_deserialize_sample(item) for item in samples_data),
        statistics=statistics,
        error_message=_optional_string(data, "error_message"),
    )
    if statistics is not None:
        try:
            validate_timing_statistics(observation.samples, statistics)
        except ValueError as exc:
            raise ArtifactError(str(exc)) from None
    return observation


def _deserialize_assertion(data: Any) -> AssertionResult:
    data = _mapping(data, "assertion")
    return AssertionResult(
        assertion_id=_value(data, "assertion_id", str),
        passed=_value(data, "passed", bool),
        message=_optional_string(data, "message"),
    )


def _deserialize_sample(data: Any) -> TimingSample:
    data = _mapping(data, "sample")
    return TimingSample(
        sequence=_integer(data, "sequence"),
        duration_seconds=_number(data, "duration_seconds"),
        successful=_value(data, "successful", bool),
        error_message=_optional_string(data, "error_message"),
    )


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactError(f"{label} must be an object")
    return value


def _value(
    data: dict[str, Any],
    key: str,
    expected_type: type | tuple[type, ...],
):
    if key not in data:
        raise ArtifactError(f"missing required field: {key}")
    value = data[key]
    if not isinstance(value, expected_type):
        if isinstance(expected_type, tuple):
            type_name = " or ".join(item.__name__ for item in expected_type)
        else:
            type_name = expected_type.__name__
        raise ArtifactError(f"field {key} must be {type_name}")
    return value


def _optional_string(data: dict[str, Any], key: str) -> str | None:
    if key not in data:
        raise ArtifactError(f"missing required field: {key}")
    value = data[key]
    if value is not None and not isinstance(value, str):
        raise ArtifactError(f"field {key} must be string or null")
    return value


def _integer(data: dict[str, Any], key: str) -> int:
    value = _value(data, key, int)
    if isinstance(value, bool):
        raise ArtifactError(f"field {key} must be int")
    return value


def _number(data: dict[str, Any], key: str) -> float:
    value = _value(data, key, (int, float))
    if isinstance(value, bool):
        raise ArtifactError(f"field {key} must be numeric")
    return value
