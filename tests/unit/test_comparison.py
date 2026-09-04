import pytest

from openstack_perf.comparison import (
    ComparisonPolicy,
    Metric,
    MetricTolerance,
    compare_runs,
)
from openstack_perf.results import (
    AssertionResult,
    CleanSnapshotStatus,
    EnvironmentFingerprint,
    FunctionalVerdict,
    OverallVerdict,
    PerformanceVerdict,
    RegressionRunResult,
    RunMetadata,
    RunRole,
    ScenarioObservation,
    TimingSample,
    TimingStatistics,
)
from openstack_perf.statistics import calculate_timing_statistics


def _observation(
    *,
    scenario_id="vm.lifecycle",
    target_id="default",
    name="VM lifecycle",
    verdict=FunctionalVerdict.PASS,
    count=3,
    p50=10.0,
    p95=12.0,
):
    durations = [p50] * count
    if count == 3:
        durations[-1] = (p95 - (0.1 * p50)) / 0.9
    samples = tuple(
        TimingSample(
            sequence,
            duration,
            successful=verdict is FunctionalVerdict.PASS,
            error_message=(
                None if verdict is FunctionalVerdict.PASS else "workflow failed"
            ),
        )
        for sequence, duration in enumerate(durations, start=1)
    )
    statistics = (
        calculate_timing_statistics(durations)
        if verdict is FunctionalVerdict.PASS
        else None
    )
    return ScenarioObservation(
        scenario_id=scenario_id,
        target_id=target_id,
        name=name,
        functional_verdict=verdict,
        assertions=(
            AssertionResult("workflow.success", verdict is FunctionalVerdict.PASS),
        ),
        samples=samples,
        statistics=statistics,
        error_message="workflow failed" if verdict is FunctionalVerdict.FAILURE else None,
    )


def _run(role, run_id, observations):
    return RegressionRunResult(
        metadata=RunMetadata(
            run_id=run_id,
            role=role,
            started_at="2026-09-04T10:00:00Z",
            completed_at="2026-09-04T10:01:00Z",
            clean_snapshot=CleanSnapshotStatus.CLEAN,
        ),
        environment=EnvironmentFingerprint(
            cloud="test-cloud",
            region="RegionOne",
            platform_release="OpenStack 2026.1",
            source_branch="stable/2026.1",
            application_release="baseline" if role is RunRole.BASELINE else "candidate",
        ),
        observations=tuple(observations),
    )


def _policy(minimum=3, *, relative=0.10, absolute=1.0):
    return ComparisonPolicy(
        scenario_id="vm.lifecycle",
        target_id="default",
        minimum_sample_count=minimum,
        tolerances=(
            MetricTolerance(
                Metric.P50,
                relative=relative,
                absolute_seconds=absolute,
            ),
        ),
    )


def test_comparison_matches_stable_ids_not_names():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation(name="Old name")])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation(name="New name")])

    comparison = compare_runs(baseline, candidate, (_policy(),))

    assert comparison.verdict is OverallVerdict.PASS
    assert comparison.observations[0].scenario_id == "vm.lifecycle"


def test_allowed_delta_uses_larger_relative_or_absolute_allowance():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation(p50=10.0)])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation(p50=12.0)])

    comparison = compare_runs(
        baseline,
        candidate,
        (_policy(relative=0.10, absolute=3.0),),
    )

    metric = comparison.observations[0].metrics[0]
    assert metric.allowed_delta_seconds == 3.0
    assert metric.applied_tolerance.relative == 0.10
    assert metric.verdict is PerformanceVerdict.PASS


def test_relative_allowance_can_be_larger_than_absolute_allowance():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation(p50=10.0)])
    candidate = _run(
        RunRole.CANDIDATE,
        "candidate",
        [_observation(p50=14.0, p95=14.0)],
    )

    comparison = compare_runs(
        baseline,
        candidate,
        (_policy(relative=0.50, absolute=1.0),),
    )

    assert comparison.observations[0].metrics[0].allowed_delta_seconds == 5.0
    assert comparison.verdict is OverallVerdict.PASS


def test_candidate_exactly_on_tolerance_boundary_passes():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation(p50=10.0)])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation(p50=12.0)])

    comparison = compare_runs(
        baseline,
        candidate,
        (_policy(relative=0.20, absolute=0.5),),
    )

    assert comparison.verdict is OverallVerdict.PASS


def test_candidate_performance_improvement_passes():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation(p50=10.0)])
    candidate = _run(
        RunRole.CANDIDATE,
        "candidate",
        [_observation(p50=8.0, p95=9.0)],
    )

    comparison = compare_runs(baseline, candidate, (_policy(),))

    metric = comparison.observations[0].metrics[0]
    assert metric.delta_seconds < 0
    assert comparison.verdict is OverallVerdict.PASS


def test_candidate_beyond_tolerance_is_performance_regression():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation(p50=10.0)])
    candidate = _run(
        RunRole.CANDIDATE,
        "candidate",
        [_observation(p50=12.01, p95=12.01)],
    )

    comparison = compare_runs(
        baseline,
        candidate,
        (_policy(relative=0.20, absolute=0.5),),
    )

    assert comparison.verdict is OverallVerdict.PERFORMANCE_REGRESSION
    assert (
        comparison.observations[0].performance_verdict
        is PerformanceVerdict.REGRESSION
    )


def test_functional_failure_dominates_performance_comparison():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation()])
    candidate = _run(
        RunRole.CANDIDATE,
        "candidate",
        [_observation(verdict=FunctionalVerdict.FAILURE)],
    )

    comparison = compare_runs(baseline, candidate, (_policy(),))

    assert comparison.verdict is OverallVerdict.FUNCTIONAL_FAILURE
    assert (
        comparison.observations[0].performance_verdict
        is PerformanceVerdict.NOT_EVALUATED
    )
    assert comparison.observations[0].metrics == ()


def test_small_sample_sets_are_insufficient_evidence():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation(count=2)])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation(count=2)])

    comparison = compare_runs(baseline, candidate, (_policy(minimum=3),))

    assert comparison.verdict is OverallVerdict.INSUFFICIENT_EVIDENCE
    assert "configured minimum of 3" in comparison.observations[0].message


def test_missing_target_is_not_silently_treated_as_pass():
    baseline = _run(
        RunRole.BASELINE,
        "baseline",
        [_observation(target_id="expected-image")],
    )
    candidate = _run(
        RunRole.CANDIDATE,
        "candidate",
        [_observation(target_id="different-image")],
    )

    comparison = compare_runs(baseline, candidate, (_policy(),))

    assert comparison.verdict is OverallVerdict.INSUFFICIENT_EVIDENCE
    assert {item.message for item in comparison.observations} == {
        "observation is missing from baseline",
        "observation is missing from baseline and candidate",
        "observation is missing from candidate",
    }


def test_baseline_functional_failure_is_insufficient_for_performance():
    baseline = _run(
        RunRole.BASELINE,
        "baseline",
        [_observation(verdict=FunctionalVerdict.FAILURE)],
    )
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation()])

    comparison = compare_runs(baseline, candidate, (_policy(),))

    assert comparison.verdict is OverallVerdict.INSUFFICIENT_EVIDENCE
    assert "baseline has a functional failure" in comparison.observations[0].message


def test_missing_comparison_policy_is_insufficient_evidence():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation()])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation()])

    comparison = compare_runs(baseline, candidate, ())

    assert comparison.verdict is OverallVerdict.INSUFFICIENT_EVIDENCE
    assert "no comparison policy" in comparison.observations[0].message


def test_policy_missing_from_both_artifacts_is_explicitly_insufficient():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation()])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation()])
    missing_policy = ComparisonPolicy(
        scenario_id="image.discover",
        target_id="image-1",
        minimum_sample_count=1,
        tolerances=(MetricTolerance(Metric.P50, relative=0.1),),
    )

    comparison = compare_runs(baseline, candidate, (_policy(), missing_policy))

    missing = next(
        item for item in comparison.observations if item.scenario_id == "image.discover"
    )
    assert missing.performance_verdict is PerformanceVerdict.INSUFFICIENT_EVIDENCE
    assert missing.message == "observation is missing from baseline and candidate"


def test_missing_statistics_is_insufficient_evidence():
    baseline_observation = _observation()
    candidate_observation = ScenarioObservation(
        scenario_id="vm.lifecycle",
        target_id="default",
        name="VM lifecycle",
        functional_verdict=FunctionalVerdict.PASS,
        assertions=(AssertionResult("workflow.success", True),),
        samples=(),
        statistics=None,
    )
    baseline = _run(RunRole.BASELINE, "baseline", [baseline_observation])
    candidate = _run(RunRole.CANDIDATE, "candidate", [candidate_observation])

    comparison = compare_runs(baseline, candidate, (_policy(),))

    assert comparison.verdict is OverallVerdict.INSUFFICIENT_EVIDENCE
    assert comparison.observations[0].message == "timing statistics are missing"


def test_each_configured_metric_is_compared_independently():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation(p50=10, p95=12)])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation(p50=10, p95=15)])
    policy = ComparisonPolicy(
        scenario_id="vm.lifecycle",
        target_id="default",
        minimum_sample_count=3,
        tolerances=(
            MetricTolerance(Metric.P50, relative=0.10),
            MetricTolerance(Metric.P95, absolute_seconds=2.0),
        ),
    )

    comparison = compare_runs(baseline, candidate, (policy,))

    assert [metric.verdict for metric in comparison.observations[0].metrics] == [
        PerformanceVerdict.PASS,
        PerformanceVerdict.REGRESSION,
    ]
    assert comparison.verdict is OverallVerdict.PERFORMANCE_REGRESSION


def test_tolerance_requires_an_allowance():
    with pytest.raises(ValueError, match="at least one"):
        MetricTolerance(Metric.P50)


@pytest.mark.parametrize("value", [-0.1, float("inf"), float("nan"), True])
def test_tolerance_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="finite and non-negative"):
        MetricTolerance(Metric.P50, relative=value)


def test_policy_rejects_duplicate_metrics():
    with pytest.raises(ValueError, match="must be unique"):
        ComparisonPolicy(
            scenario_id="vm.lifecycle",
            target_id="default",
            minimum_sample_count=1,
            tolerances=(
                MetricTolerance(Metric.P50, relative=0.1),
                MetricTolerance(Metric.P50, absolute_seconds=1.0),
            ),
        )


@pytest.mark.parametrize("minimum", [True, 1.5, "1"])
def test_policy_rejects_non_integer_minimum_sample_count(minimum):
    with pytest.raises(TypeError, match="minimum_sample_count must be an integer"):
        ComparisonPolicy(
            scenario_id="vm.lifecycle",
            target_id="default",
            minimum_sample_count=minimum,
            tolerances=(MetricTolerance(Metric.P50, relative=0.1),),
        )


def test_comparison_collections_are_protected_from_caller_mutation():
    tolerances = [MetricTolerance(Metric.P50, relative=0.1)]
    policy = ComparisonPolicy("vm.lifecycle", "default", 1, tolerances)
    tolerances.clear()

    baseline = _run(RunRole.BASELINE, "baseline", [_observation()])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation()])
    comparison = compare_runs(baseline, candidate, (policy,))
    observations = list(comparison.observations)
    rebuilt = type(comparison)(
        comparison.baseline_run_id,
        comparison.candidate_run_id,
        comparison.verdict,
        observations,
    )
    observations.clear()

    assert len(policy.tolerances) == 1
    assert len(rebuilt.observations) == 1


def test_comparison_rejects_statistics_that_contradict_raw_samples():
    inconsistent = ScenarioObservation(
        scenario_id="vm.lifecycle",
        target_id="default",
        name="VM lifecycle",
        functional_verdict=FunctionalVerdict.PASS,
        assertions=(AssertionResult("workflow.success", True),),
        samples=(TimingSample(1, 10.0),),
        statistics=TimingStatistics(1, 11.0, 11.0, 11.0, 11.0),
    )
    baseline = _run(RunRole.BASELINE, "baseline", [inconsistent])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation()])

    with pytest.raises(ValueError, match="do not match successful raw samples"):
        compare_runs(baseline, candidate, (_policy(),))


def test_comparison_rejects_duplicate_stable_policy_keys():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation()])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation()])
    policy = _policy()

    with pytest.raises(ValueError, match="unique stable IDs"):
        compare_runs(baseline, candidate, (policy, policy))


def test_comparison_requires_correct_run_roles():
    baseline = _run(RunRole.CANDIDATE, "wrong-role", [_observation()])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation()])

    with pytest.raises(ValueError, match="baseline role"):
        compare_runs(baseline, candidate, (_policy(),))


def test_functional_only_observation_passes_without_performance_policy():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation()])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation()])

    comparison = compare_runs(
        baseline, candidate, (), (("vm.lifecycle", "default"),)
    )

    assert comparison.verdict is OverallVerdict.PASS
    assert comparison.observations[0].performance_verdict is (
        PerformanceVerdict.NOT_EVALUATED
    )


def test_functional_only_candidate_failure_dominates():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation()])
    candidate = _run(
        RunRole.CANDIDATE,
        "candidate",
        [_observation(verdict=FunctionalVerdict.FAILURE)],
    )

    comparison = compare_runs(
        baseline, candidate, (), (("vm.lifecycle", "default"),)
    )

    assert comparison.verdict is OverallVerdict.FUNCTIONAL_FAILURE


def test_functional_only_missing_or_invalid_baseline_is_insufficient():
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation()])
    empty_baseline_observation = _observation(
        scenario_id="other", target_id="other"
    )
    missing = compare_runs(
        _run(RunRole.BASELINE, "baseline", [empty_baseline_observation]),
        candidate,
        (),
        (("vm.lifecycle", "default"),),
    )
    invalid = compare_runs(
        _run(
            RunRole.BASELINE,
            "baseline",
            [_observation(verdict=FunctionalVerdict.FAILURE)],
        ),
        candidate,
        (),
        (("vm.lifecycle", "default"),),
    )

    assert missing.verdict is OverallVerdict.INSUFFICIENT_EVIDENCE
    assert invalid.verdict is OverallVerdict.INSUFFICIENT_EVIDENCE


def test_comparison_rejects_duplicate_or_overlapping_functional_only_keys():
    baseline = _run(RunRole.BASELINE, "baseline", [_observation()])
    candidate = _run(RunRole.CANDIDATE, "candidate", [_observation()])
    key = ("vm.lifecycle", "default")

    with pytest.raises(ValueError, match="functional-only keys must be unique"):
        compare_runs(baseline, candidate, (), (key, key))
    with pytest.raises(ValueError, match="both performance and functional-only"):
        compare_runs(baseline, candidate, (_policy(),), (key,))
