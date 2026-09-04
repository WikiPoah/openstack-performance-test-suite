import pytest

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


def _metadata():
    return RunMetadata(
        run_id="run-1",
        role=RunRole.BASELINE,
        started_at="2026-09-04T10:00:00Z",
        completed_at="2026-09-04T10:01:00Z",
        clean_snapshot=CleanSnapshotStatus.CLEAN,
    )


def _environment():
    return EnvironmentFingerprint(
        cloud="test-cloud",
        region="RegionOne",
        platform_release="OpenStack 2026.1",
        source_branch="stable/2026.1",
        application_release="baseline",
        service_versions=(ServiceVersion("compute", "v2.1"),),
    )


def _observation(scenario_id="vm.lifecycle", target_id="default"):
    samples = (
        TimingSample(1, 10.0),
        TimingSample(2, 12.0),
    )
    return ScenarioObservation(
        scenario_id=scenario_id,
        target_id=target_id,
        name="VM lifecycle",
        functional_verdict=FunctionalVerdict.PASS,
        assertions=(AssertionResult("server.active", True),),
        samples=samples,
        statistics=TimingStatistics(2, 11.0, 11.9, 10.0, 12.0),
    )


def test_regression_run_captures_versioned_environment_and_observations():
    run = RegressionRunResult(
        metadata=_metadata(),
        environment=_environment(),
        observations=(_observation(),),
    )

    assert run.schema_version == SCHEMA_VERSION
    assert run.metadata.clean_snapshot is CleanSnapshotStatus.CLEAN
    assert run.environment.source_branch == "stable/2026.1"
    assert run.observations[0].statistics.sample_count == 2


def test_run_rejects_unsupported_schema_version():
    with pytest.raises(ValueError, match="unsupported schema version"):
        RegressionRunResult(
            metadata=_metadata(),
            environment=_environment(),
            observations=(),
            schema_version="99.0",
        )


def test_environment_fingerprint_requires_release_identity():
    with pytest.raises(ValueError, match="must be non-empty"):
        EnvironmentFingerprint(
            cloud="test-cloud",
            region="RegionOne",
            platform_release="",
            source_branch="stable/2026.1",
            application_release="baseline",
        )


def test_environment_fingerprint_rejects_duplicate_service_names():
    with pytest.raises(ValueError, match="must be unique"):
        EnvironmentFingerprint(
            cloud="test-cloud",
            region="RegionOne",
            platform_release="OpenStack 2026.1",
            source_branch="stable/2026.1",
            application_release="baseline",
            service_versions=(
                ServiceVersion("compute", "v2.1"),
                ServiceVersion("compute", "v2.2"),
            ),
        )


def test_run_rejects_duplicate_stable_observation_keys():
    with pytest.raises(ValueError, match="must be unique"):
        RegressionRunResult(
            metadata=_metadata(),
            environment=_environment(),
            observations=(_observation(), _observation()),
        )


def test_run_rejects_empty_observation_set():
    with pytest.raises(ValueError, match="at least one observation"):
        RegressionRunResult(
            metadata=_metadata(),
            environment=_environment(),
            observations=(),
        )


@pytest.mark.parametrize("duration", [-1.0, float("inf"), float("nan")])
def test_timing_sample_rejects_invalid_duration(duration):
    with pytest.raises(ValueError, match="finite and non-negative"):
        TimingSample(1, duration)


def test_timing_sample_rejects_boolean_duration():
    with pytest.raises(TypeError, match="must be a number"):
        TimingSample(1, True)


def test_successful_sample_rejects_error_message():
    with pytest.raises(ValueError, match="cannot contain an error"):
        TimingSample(1, 1.0, successful=True, error_message="failed")


@pytest.mark.parametrize("sequence", [True, 1.5, "1"])
def test_timing_sample_rejects_non_integer_sequence(sequence):
    with pytest.raises(TypeError, match="sequence must be an integer"):
        TimingSample(sequence, 1.0)


def test_timing_sample_requires_boolean_successful():
    with pytest.raises(TypeError, match="successful must be boolean"):
        TimingSample(1, 1.0, successful=1)


@pytest.mark.parametrize("sample_count", [True, 1.5, "1"])
def test_statistics_reject_non_integer_sample_count(sample_count):
    with pytest.raises(TypeError, match="sample_count must be an integer"):
        TimingStatistics(sample_count, 1.0, 1.0, 1.0, 1.0)


@pytest.mark.parametrize(
    "started_at, completed_at, message",
    [
        ("not-a-timestamp", "2026-09-04T10:01:00Z", "valid ISO-8601"),
        (
            "2026-09-04T10:00:00",
            "2026-09-04T10:01:00Z",
            "include a timezone",
        ),
        (
            "2026-09-04T10:02:00Z",
            "2026-09-04T10:01:00Z",
            "must not be earlier",
        ),
    ],
)
def test_run_metadata_rejects_invalid_timestamps(started_at, completed_at, message):
    with pytest.raises(ValueError, match=message):
        RunMetadata(
            run_id="run-1",
            role=RunRole.BASELINE,
            started_at=started_at,
            completed_at=completed_at,
            clean_snapshot=CleanSnapshotStatus.CLEAN,
        )


def test_observation_rejects_passing_failed_assertion():
    with pytest.raises(ValueError, match="passing observation"):
        ScenarioObservation(
            scenario_id="image.discover",
            target_id="image-1",
            name="Image discovery",
            functional_verdict=FunctionalVerdict.PASS,
            assertions=(AssertionResult("image.active", False),),
            samples=(TimingSample(1, 0.1),),
            statistics=TimingStatistics(1, 0.1, 0.1, 0.1, 0.1),
        )


def test_observation_rejects_passing_failed_sample():
    with pytest.raises(ValueError, match="passing observation.*failed samples"):
        ScenarioObservation(
            scenario_id="image.discover",
            target_id="image-1",
            name="Image discovery",
            functional_verdict=FunctionalVerdict.PASS,
            assertions=(),
            samples=(TimingSample(1, 0.1, False, "failed"),),
            statistics=None,
        )


def test_failed_observation_requires_failure_evidence():
    with pytest.raises(ValueError, match="must contain failure evidence"):
        ScenarioObservation(
            scenario_id="image.discover",
            target_id="image-1",
            name="Image discovery",
            functional_verdict=FunctionalVerdict.FAILURE,
            assertions=(),
            samples=(),
            statistics=None,
        )


def test_observation_rejects_statistics_count_mismatch():
    with pytest.raises(ValueError, match="must match successful raw samples"):
        ScenarioObservation(
            scenario_id="image.discover",
            target_id="image-1",
            name="Image discovery",
            functional_verdict=FunctionalVerdict.FAILURE,
            assertions=(),
            samples=(TimingSample(1, 0.1), TimingSample(2, 0.2, False, "failed")),
            statistics=TimingStatistics(2, 0.1, 0.1, 0.1, 0.1),
        )


def test_statistics_reject_inconsistent_percentile_order():
    with pytest.raises(ValueError, match="must be ordered"):
        TimingStatistics(2, 12.0, 11.0, 10.0, 12.0)


def test_frozen_models_normalize_caller_collections_to_tuples():
    service_versions = [ServiceVersion("compute", "v2.1")]
    environment = EnvironmentFingerprint(
        cloud="test-cloud",
        region="RegionOne",
        platform_release="OpenStack 2026.1",
        source_branch="stable/2026.1",
        application_release="baseline",
        service_versions=service_versions,
    )
    assertions = [AssertionResult("server.active", True)]
    samples = [TimingSample(1, 1.0)]
    observation = ScenarioObservation(
        scenario_id="vm.lifecycle",
        target_id="default",
        name="VM lifecycle",
        functional_verdict=FunctionalVerdict.PASS,
        assertions=assertions,
        samples=samples,
        statistics=TimingStatistics(1, 1.0, 1.0, 1.0, 1.0),
    )
    observations = [observation]
    run = RegressionRunResult(_metadata(), environment, observations)

    service_versions.clear()
    assertions.clear()
    samples.clear()
    observations.clear()

    assert environment.service_versions == (ServiceVersion("compute", "v2.1"),)
    assert observation.assertions == (AssertionResult("server.active", True),)
    assert observation.samples == (TimingSample(1, 1.0),)
    assert run.observations == (observation,)
