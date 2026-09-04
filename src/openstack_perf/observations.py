from collections.abc import Iterable

from openstack_perf.models import ExecutionStatus, WorkflowRunResult
from openstack_perf.results import (
    AssertionResult,
    FunctionalVerdict,
    ScenarioObservation,
    TimingSample,
)
from openstack_perf.statistics import calculate_timing_statistics


def workflow_results_to_observation(
    results: Iterable[WorkflowRunResult],
    *,
    scenario_id: str,
    target_id: str,
    name: str,
) -> ScenarioObservation:
    """Aggregate existing workflow results into one regression observation."""
    executions = tuple(results)
    if not executions:
        raise ValueError("at least one workflow result is required")
    workflow_id = executions[0].workflow_id
    environment = executions[0].environment
    if any(result.workflow_id != workflow_id for result in executions):
        raise ValueError("workflow results must have the same workflow_id")
    if any(result.environment != environment for result in executions):
        raise ValueError("workflow results must have the same environment")
    if any(
        result.status is ExecutionStatus.SUCCESS
        and result.error_message is not None
        for result in executions
    ):
        raise ValueError("successful workflow results cannot contain errors")

    samples = tuple(
        _timing_sample(sequence, result)
        for sequence, result in enumerate(executions, start=1)
    )
    assertions = tuple(
        AssertionResult(
            assertion_id=f"{scenario_id}.execution.{sequence}",
            passed=sample.successful,
            message=sample.error_message,
        )
        for sequence, sample in enumerate(samples, start=1)
    )
    successful_durations = tuple(
        sample.duration_seconds for sample in samples if sample.successful
    )
    errors = tuple(
        f"execution {sample.sequence}: {sample.error_message or 'workflow failed'}"
        for sample in samples
        if not sample.successful
    )

    return ScenarioObservation(
        scenario_id=scenario_id,
        target_id=target_id,
        name=name,
        functional_verdict=(
            FunctionalVerdict.FAILURE
            if errors
            else FunctionalVerdict.PASS
        ),
        assertions=assertions,
        samples=samples,
        statistics=(
            calculate_timing_statistics(successful_durations)
            if successful_durations
            else None
        ),
        error_message="; ".join(errors) or None,
    )


def _timing_sample(
    sequence: int, result: WorkflowRunResult
) -> TimingSample:
    successful = result.status is ExecutionStatus.SUCCESS
    return TimingSample(
        sequence=sequence,
        duration_seconds=result.duration_seconds,
        successful=successful,
        error_message=(
            None
            if successful
            else result.error_message or "workflow failed"
        ),
    )
