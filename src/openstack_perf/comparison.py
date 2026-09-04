from dataclasses import dataclass
from enum import Enum
import math

from openstack_perf.results import (
    FunctionalVerdict,
    OverallVerdict,
    PerformanceVerdict,
    RegressionRunResult,
    RunRole,
    ScenarioObservation,
)
from openstack_perf.statistics import validate_timing_statistics


class Metric(Enum):
    P50 = "p50_seconds"
    P95 = "p95_seconds"


@dataclass(frozen=True)
class MetricTolerance:
    metric: Metric
    relative: float | None = None
    absolute_seconds: float | None = None

    def __post_init__(self):
        if self.relative is None and self.absolute_seconds is None:
            raise ValueError("at least one tolerance allowance is required")
        for name, value in (
            ("relative tolerance", self.relative),
            ("absolute tolerance", self.absolute_seconds),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class ComparisonPolicy:
    scenario_id: str
    target_id: str
    minimum_sample_count: int
    tolerances: tuple[MetricTolerance, ...]

    def __post_init__(self):
        object.__setattr__(self, "tolerances", tuple(self.tolerances))
        if not self.scenario_id or not self.target_id:
            raise ValueError("policy scenario_id and target_id must be non-empty")
        if isinstance(self.minimum_sample_count, bool) or not isinstance(
            self.minimum_sample_count, int
        ):
            raise TypeError("minimum_sample_count must be an integer")
        if self.minimum_sample_count < 1:
            raise ValueError("minimum_sample_count must be at least 1")
        if not self.tolerances:
            raise ValueError("at least one metric tolerance is required")
        metrics = [tolerance.metric for tolerance in self.tolerances]
        if len(metrics) != len(set(metrics)):
            raise ValueError("metric tolerances must be unique")


@dataclass(frozen=True)
class MetricComparison:
    metric: Metric
    baseline_seconds: float
    candidate_seconds: float
    delta_seconds: float
    allowed_delta_seconds: float
    applied_tolerance: MetricTolerance
    verdict: PerformanceVerdict


@dataclass(frozen=True)
class ObservationComparison:
    scenario_id: str
    target_id: str
    functional_verdict: FunctionalVerdict | None
    performance_verdict: PerformanceVerdict
    metrics: tuple[MetricComparison, ...] = ()
    message: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "metrics", tuple(self.metrics))


@dataclass(frozen=True)
class RunComparison:
    baseline_run_id: str
    candidate_run_id: str
    verdict: OverallVerdict
    observations: tuple[ObservationComparison, ...]

    def __post_init__(self):
        object.__setattr__(self, "observations", tuple(self.observations))


def compare_runs(
    baseline: RegressionRunResult,
    candidate: RegressionRunResult,
    policies: tuple[ComparisonPolicy, ...],
) -> RunComparison:
    """Compare runs by stable scenario and target IDs."""
    if baseline.metadata.role is not RunRole.BASELINE:
        raise ValueError("baseline artifact must have baseline role")
    if candidate.metadata.role is not RunRole.CANDIDATE:
        raise ValueError("candidate artifact must have candidate role")

    for run in (baseline, candidate):
        for observation in run.observations:
            validate_timing_statistics(
                observation.samples, observation.statistics
            )

    baseline_by_key = _observations_by_key(baseline)
    candidate_by_key = _observations_by_key(candidate)
    policies_by_key = {
        (policy.scenario_id, policy.target_id): policy for policy in policies
    }
    if len(policies_by_key) != len(policies):
        raise ValueError("comparison policies must have unique stable IDs")
    comparisons = tuple(
        _compare_observation(
            key,
            baseline_by_key.get(key),
            candidate_by_key.get(key),
            policies_by_key.get(key),
        )
        for key in sorted(
            set(baseline_by_key) | set(candidate_by_key) | set(policies_by_key)
        )
    )

    if any(
        comparison.functional_verdict is FunctionalVerdict.FAILURE
        for comparison in comparisons
    ):
        verdict = OverallVerdict.FUNCTIONAL_FAILURE
    elif any(
        comparison.performance_verdict is PerformanceVerdict.REGRESSION
        for comparison in comparisons
    ):
        verdict = OverallVerdict.PERFORMANCE_REGRESSION
    elif any(
        comparison.performance_verdict is PerformanceVerdict.INSUFFICIENT_EVIDENCE
        for comparison in comparisons
    ):
        verdict = OverallVerdict.INSUFFICIENT_EVIDENCE
    else:
        verdict = OverallVerdict.PASS

    return RunComparison(
        baseline_run_id=baseline.metadata.run_id,
        candidate_run_id=candidate.metadata.run_id,
        verdict=verdict,
        observations=comparisons,
    )


def _observations_by_key(
    run: RegressionRunResult,
) -> dict[tuple[str, str], ScenarioObservation]:
    return {
        (observation.scenario_id, observation.target_id): observation
        for observation in run.observations
    }


def _compare_observation(
    key: tuple[str, str],
    baseline: ScenarioObservation | None,
    candidate: ScenarioObservation | None,
    policy: ComparisonPolicy | None,
) -> ObservationComparison:
    scenario_id, target_id = key
    if candidate and candidate.functional_verdict is FunctionalVerdict.FAILURE:
        return ObservationComparison(
            scenario_id=scenario_id,
            target_id=target_id,
            functional_verdict=FunctionalVerdict.FAILURE,
            performance_verdict=PerformanceVerdict.NOT_EVALUATED,
            message="candidate has a functional failure",
        )
    if baseline is None or candidate is None:
        if baseline is None and candidate is None:
            missing = "baseline and candidate"
        else:
            missing = "baseline" if baseline is None else "candidate"
        return ObservationComparison(
            scenario_id=scenario_id,
            target_id=target_id,
            functional_verdict=(
                candidate.functional_verdict if candidate is not None else None
            ),
            performance_verdict=PerformanceVerdict.INSUFFICIENT_EVIDENCE,
            message=f"observation is missing from {missing}",
        )
    if baseline.functional_verdict is FunctionalVerdict.FAILURE:
        return ObservationComparison(
            scenario_id=scenario_id,
            target_id=target_id,
            functional_verdict=candidate.functional_verdict,
            performance_verdict=PerformanceVerdict.INSUFFICIENT_EVIDENCE,
            message="baseline has a functional failure",
        )
    if policy is None:
        return ObservationComparison(
            scenario_id=scenario_id,
            target_id=target_id,
            functional_verdict=candidate.functional_verdict,
            performance_verdict=PerformanceVerdict.INSUFFICIENT_EVIDENCE,
            message="no comparison policy is configured for this observation",
        )
    if baseline.statistics is None or candidate.statistics is None:
        return ObservationComparison(
            scenario_id=scenario_id,
            target_id=target_id,
            functional_verdict=candidate.functional_verdict,
            performance_verdict=PerformanceVerdict.INSUFFICIENT_EVIDENCE,
            message="timing statistics are missing",
        )
    if (
        baseline.statistics.sample_count < policy.minimum_sample_count
        or candidate.statistics.sample_count < policy.minimum_sample_count
    ):
        return ObservationComparison(
            scenario_id=scenario_id,
            target_id=target_id,
            functional_verdict=candidate.functional_verdict,
            performance_verdict=PerformanceVerdict.INSUFFICIENT_EVIDENCE,
            message=(
                "sample count is below the configured minimum of "
                f"{policy.minimum_sample_count}"
            ),
        )

    metrics = tuple(
        _compare_metric(baseline, candidate, tolerance)
        for tolerance in policy.tolerances
    )
    performance_verdict = (
        PerformanceVerdict.REGRESSION
        if any(metric.verdict is PerformanceVerdict.REGRESSION for metric in metrics)
        else PerformanceVerdict.PASS
    )
    return ObservationComparison(
        scenario_id=scenario_id,
        target_id=target_id,
        functional_verdict=candidate.functional_verdict,
        performance_verdict=performance_verdict,
        metrics=metrics,
    )


def _compare_metric(
    baseline: ScenarioObservation,
    candidate: ScenarioObservation,
    tolerance: MetricTolerance,
) -> MetricComparison:
    baseline_value = getattr(baseline.statistics, tolerance.metric.value)
    candidate_value = getattr(candidate.statistics, tolerance.metric.value)
    relative_allowance = baseline_value * float(tolerance.relative or 0)
    absolute_allowance = float(tolerance.absolute_seconds or 0)
    allowed_delta = max(relative_allowance, absolute_allowance)
    delta = candidate_value - baseline_value
    verdict = (
        PerformanceVerdict.REGRESSION
        if delta > allowed_delta
        else PerformanceVerdict.PASS
    )
    return MetricComparison(
        metric=tolerance.metric,
        baseline_seconds=baseline_value,
        candidate_seconds=candidate_value,
        delta_seconds=delta,
        allowed_delta_seconds=allowed_delta,
        applied_tolerance=tolerance,
        verdict=verdict,
    )
