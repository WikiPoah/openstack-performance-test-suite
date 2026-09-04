from pathlib import Path

from openstack_perf.comparison import RunComparison
from openstack_perf.results import (
    FunctionalVerdict,
    OverallVerdict,
    PerformanceVerdict,
    RegressionRunResult,
)


EXIT_PASS = 0
EXIT_FUNCTIONAL_FAILURE = 1
EXIT_PERFORMANCE_REGRESSION = 2
EXIT_ERROR = 3


def run_exit_code(run: RegressionRunResult) -> int:
    return (
        EXIT_FUNCTIONAL_FAILURE
        if run.functional_verdict is FunctionalVerdict.FAILURE
        else EXIT_PASS
    )


def comparison_exit_code(comparison: RunComparison) -> int:
    return {
        OverallVerdict.PASS: EXIT_PASS,
        OverallVerdict.FUNCTIONAL_FAILURE: EXIT_FUNCTIONAL_FAILURE,
        OverallVerdict.PERFORMANCE_REGRESSION: EXIT_PERFORMANCE_REGRESSION,
        OverallVerdict.INSUFFICIENT_EVIDENCE: EXIT_ERROR,
    }[comparison.verdict]


def render_run_summary(
    run: RegressionRunResult,
    artifact_path: str | Path | None = None,
) -> str:
    metadata = run.metadata
    environment = run.environment
    lines = [
        f"Configuration: {metadata.configuration_name or '—'}",
        f"Role: {metadata.role.value}",
        f"Platform: {environment.platform_release}",
        f"Application: {environment.application_release}",
        f"Region: {environment.region}",
        f"Run ID: {metadata.run_id}",
    ]
    if artifact_path is not None:
        lines.append(f"Artifact: {artifact_path}")
    lines.extend(("", "SCENARIO | TARGET | FUNCTIONAL | N | P50 | P95"))
    for observation in run.observations:
        statistics = observation.statistics
        lines.append(
            " | ".join(
                (
                    observation.scenario_id,
                    observation.target_id,
                    observation.functional_verdict.value.upper(),
                    str(len(observation.samples)),
                    _seconds(statistics.p50_seconds if statistics else None),
                    _seconds(statistics.p95_seconds if statistics else None),
                )
            )
        )
        if observation.error_message:
            lines.append(f"  Error: {observation.error_message}")
        if observation.scenario_id in {
            "vm.lifecycle",
            "vm.network_attachment_lifecycle",
        }:
            cleanup = (
                "confirmed for every completed execution"
                if observation.functional_verdict is FunctionalVerdict.PASS
                else "failed or could not be confirmed; see workflow error"
            )
            lines.append(f"  Cleanup: {cleanup}")
    lines.extend(("", f"Overall: {run.functional_verdict.value.upper()}"))
    return "\n".join(lines)


def render_comparison_summary(comparison: RunComparison) -> str:
    lines = [
        f"Baseline run: {comparison.baseline_run_id}",
        f"Candidate run: {comparison.candidate_run_id}",
        "",
        "SCENARIO | TARGET | FUNCTIONAL | PERFORMANCE | DELTAS",
    ]
    for observation in comparison.observations:
        deltas = ", ".join(
            f"{metric.metric.name}: {_delta(metric.delta_seconds, metric.baseline_seconds)}"
            for metric in observation.metrics
        ) or "—"
        lines.append(
            " | ".join(
                (
                    observation.scenario_id,
                    observation.target_id,
                    (
                        observation.functional_verdict.value.upper()
                        if observation.functional_verdict
                        else "—"
                    ),
                    observation.performance_verdict.value.upper(),
                    deltas,
                )
            )
        )
        if observation.message:
            lines.append(f"  Note: {observation.message}")
    lines.extend(("", f"Overall: {comparison.verdict.value.upper()}"))
    return "\n".join(lines)


def _seconds(value):
    return "—" if value is None else f"{value:.3f}s"


def _delta(delta, baseline):
    percentage = "—" if baseline == 0 else f"{delta / baseline:+.1%}"
    return f"{delta:+.3f}s ({percentage})"
