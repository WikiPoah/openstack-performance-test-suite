import json

import pytest

from openstack_perf.artifacts import (
    ArtifactError,
    deserialize_run,
    read_run_artifact,
    serialize_run,
    write_run_artifact,
)
from openstack_perf.results import (
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


def _run():
    return RegressionRunResult(
        metadata=RunMetadata(
            run_id="run-1",
            role=RunRole.BASELINE,
            started_at="2026-09-04T10:00:00Z",
            completed_at="2026-09-04T10:01:00Z",
            clean_snapshot=CleanSnapshotStatus.CLEAN,
        ),
        environment=EnvironmentFingerprint(
            cloud="test-cloud",
            region="RegionOne",
            platform_release="OpenStack 2026.1",
            source_branch="stable/2026.1",
            application_release="release-1",
            service_versions=(
                ServiceVersion("compute", "v2.1"),
                ServiceVersion("network", "v2.0"),
            ),
        ),
        observations=(
            ScenarioObservation(
                scenario_id="vm.lifecycle",
                target_id="default",
                name="VM lifecycle",
                functional_verdict=FunctionalVerdict.PASS,
                assertions=(AssertionResult("server.active", True),),
                samples=(TimingSample(1, 10.0), TimingSample(2, 12.0)),
                statistics=TimingStatistics(2, 11.0, 11.9, 10.0, 12.0),
            ),
        ),
    )


def test_json_round_trip_preserves_complete_run():
    run = _run()

    restored = deserialize_run(serialize_run(run))

    assert restored == run


def test_serialization_is_deterministic_and_versioned():
    first = serialize_run(_run())
    second = serialize_run(_run())

    assert first == second
    assert json.loads(first)["schema_version"] == "1.0"
    assert first.endswith("\n")


def test_serialization_preserves_raw_samples_and_statistics():
    document = json.loads(serialize_run(_run()))
    observation = document["observations"][0]

    assert observation["samples"] == [
        {
            "duration_seconds": 10.0,
            "error_message": None,
            "sequence": 1,
            "successful": True,
        },
        {
            "duration_seconds": 12.0,
            "error_message": None,
            "sequence": 2,
            "successful": True,
        },
    ]
    assert observation["statistics"]["sample_count"] == 2
    assert observation["statistics"]["p50_seconds"] == 11.0
    assert observation["statistics"]["p95_seconds"] == 11.9


def test_schema_has_no_credential_or_response_body_fields():
    serialized = serialize_run(_run())

    for forbidden in (
        "password",
        "token",
        "authorization",
        "response_body",
    ):
        assert forbidden not in serialized.lower()


def test_atomic_file_write_and_read_round_trip(tmp_path):
    destination = tmp_path / "nested" / "run.json"

    write_run_artifact(destination, _run())

    assert read_run_artifact(destination) == _run()
    assert list(destination.parent.iterdir()) == [destination]


def test_atomic_write_replaces_existing_artifact(tmp_path):
    destination = tmp_path / "run.json"
    destination.write_text("old content", encoding="utf-8")

    write_run_artifact(destination, _run())

    assert read_run_artifact(destination) == _run()
    assert "old content" not in destination.read_text(encoding="utf-8")


def test_atomic_write_failure_preserves_destination_and_removes_temporary_file(
    tmp_path, monkeypatch
):
    destination = tmp_path / "run.json"
    destination.write_text("old content", encoding="utf-8")

    def fail_replace(source, target):
        raise OSError("replacement failed")

    monkeypatch.setattr("openstack_perf.artifacts.os.replace", fail_replace)

    with pytest.raises(OSError, match="replacement failed"):
        write_run_artifact(destination, _run())

    assert destination.read_text(encoding="utf-8") == "old content"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("serialized", ["not JSON", "[]", "null"])
def test_deserialization_rejects_malformed_json_document(serialized):
    with pytest.raises(ArtifactError):
        deserialize_run(serialized)


def test_deserialization_rejects_incompatible_schema():
    document = json.loads(serialize_run(_run()))
    document["schema_version"] = "2.0"

    with pytest.raises(ArtifactError, match="unsupported schema version"):
        deserialize_run(json.dumps(document))


def test_deserialization_rejects_missing_required_field():
    document = json.loads(serialize_run(_run()))
    del document["environment"]["platform_release"]

    with pytest.raises(ArtifactError, match="missing required field"):
        deserialize_run(json.dumps(document))


def test_deserialization_rejects_invalid_enum_value():
    document = json.loads(serialize_run(_run()))
    document["run"]["role"] = "unknown-role"

    with pytest.raises(ArtifactError, match="malformed regression artifact"):
        deserialize_run(json.dumps(document))


def test_deserialization_rejects_statistics_raw_sample_mismatch():
    document = json.loads(serialize_run(_run()))
    document["observations"][0]["statistics"]["sample_count"] = 99

    with pytest.raises(ArtifactError, match="must match successful raw samples"):
        deserialize_run(json.dumps(document))


def test_deserialization_rejects_statistics_value_mismatch():
    document = json.loads(serialize_run(_run()))
    document["observations"][0]["statistics"]["p50_seconds"] = 10.5

    with pytest.raises(ArtifactError, match="do not match successful raw samples"):
        deserialize_run(json.dumps(document))


def test_serialization_rejects_statistics_value_mismatch():
    run = _run()
    observation = run.observations[0]
    inconsistent = ScenarioObservation(
        scenario_id=observation.scenario_id,
        target_id=observation.target_id,
        name=observation.name,
        functional_verdict=observation.functional_verdict,
        assertions=observation.assertions,
        samples=observation.samples,
        statistics=TimingStatistics(2, 10.5, 11.9, 10.0, 12.0),
    )
    invalid_run = RegressionRunResult(
        metadata=run.metadata,
        environment=run.environment,
        observations=(inconsistent,),
    )

    with pytest.raises(ArtifactError, match="do not match successful raw samples"):
        serialize_run(invalid_run)


def test_deserialization_rejects_invalid_numeric_field_cleanly():
    document = json.loads(serialize_run(_run()))
    document["observations"][0]["samples"][0]["duration_seconds"] = "slow"

    with pytest.raises(ArtifactError, match="must be int or float"):
        deserialize_run(json.dumps(document))
