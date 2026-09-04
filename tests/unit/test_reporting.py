from openstack_perf.comparison import (
    Metric,
    MetricComparison,
    MetricTolerance,
    ObservationComparison,
    RunComparison,
)
from openstack_perf.reporting import (
    EXIT_ERROR,
    EXIT_FUNCTIONAL_FAILURE,
    EXIT_PASS,
    EXIT_PERFORMANCE_REGRESSION,
    comparison_exit_code,
    render_comparison_summary,
    render_run_summary,
    run_exit_code,
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
)


def _run(verdict=FunctionalVerdict.PASS):
    return RegressionRunResult(
        RunMetadata(
            "run-1",
            RunRole.CANDIDATE,
            "2026-09-04T10:00:00Z",
            "2026-09-04T10:01:00Z",
            CleanSnapshotStatus.UNKNOWN,
            "example",
        ),
        EnvironmentFingerprint(
            "devstack-perf", "RegionOne", "2026.1", "stable/2026.1", "1.0"
        ),
        (
            ScenarioObservation(
                "product.test",
                "target",
                "Product test",
                verdict,
                (AssertionResult("available", verdict is FunctionalVerdict.PASS),),
                (),
                None,
                "failed" if verdict is FunctionalVerdict.FAILURE else None,
            ),
        ),
    )


def test_run_summary_shows_identity_missing_statistics_and_failure():
    summary = render_run_summary(_run(FunctionalVerdict.FAILURE), "result.json")

    assert "Configuration: example" in summary
    assert "Platform: 2026.1" in summary
    assert "product.test | target | FUNCTIONAL_FAILURE | 0 | — | —" in summary
    assert "Artifact: result.json" in summary
    assert "Error: failed" in summary


def test_run_summary_reports_attempted_samples_and_vm_cleanup_outcome():
    failed_vm = ScenarioObservation(
        "vm.network_attachment_lifecycle",
        "test-network",
        "VM lifecycle",
        FunctionalVerdict.FAILURE,
        (AssertionResult("execution.1", False),),
        (TimingSample(1, 2.0, False, "cleanup failed"),),
        None,
        "cleanup failed",
    )
    run = RegressionRunResult(
        _run().metadata,
        _run().environment,
        (failed_vm,),
    )

    summary = render_run_summary(run)

    assert "vm.network_attachment_lifecycle | test-network | FUNCTIONAL_FAILURE | 1" in summary
    assert "Cleanup: failed or could not be confirmed" in summary


def test_comparison_summary_shows_delta_and_verdicts():
    metric = MetricComparison(
        Metric.P50,
        1.0,
        1.2,
        0.2,
        0.1,
        MetricTolerance(Metric.P50, relative=0.1),
        PerformanceVerdict.REGRESSION,
    )
    comparison = RunComparison(
        "baseline",
        "candidate",
        OverallVerdict.PERFORMANCE_REGRESSION,
        (
            ObservationComparison(
                "scenario",
                "target",
                FunctionalVerdict.PASS,
                PerformanceVerdict.REGRESSION,
                (metric,),
            ),
        ),
    )

    summary = render_comparison_summary(comparison)

    assert "scenario | target | PASS | PERFORMANCE_REGRESSION" in summary
    assert "+0.200s (+20.0%)" in summary
    assert "Overall: PERFORMANCE_REGRESSION" in summary


def test_exit_code_contract():
    assert run_exit_code(_run()) == EXIT_PASS
    assert run_exit_code(_run(FunctionalVerdict.FAILURE)) == EXIT_FUNCTIONAL_FAILURE
    expected = {
        OverallVerdict.PASS: EXIT_PASS,
        OverallVerdict.FUNCTIONAL_FAILURE: EXIT_FUNCTIONAL_FAILURE,
        OverallVerdict.PERFORMANCE_REGRESSION: EXIT_PERFORMANCE_REGRESSION,
        OverallVerdict.INSUFFICIENT_EVIDENCE: EXIT_ERROR,
    }
    for verdict, exit_code in expected.items():
        comparison = RunComparison("baseline", "candidate", verdict, ())
        assert comparison_exit_code(comparison) == exit_code
