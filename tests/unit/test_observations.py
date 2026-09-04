import pytest

from openstack_perf.models import Environment, ExecutionStatus, WorkflowRunResult
from openstack_perf.observations import workflow_results_to_observation
from openstack_perf.results import FunctionalVerdict


def _result(status, duration, error=None):
    return WorkflowRunResult(
        workflow_id="vm.lifecycle",
        workflow_name="VM lifecycle",
        environment=Environment("test-cloud", "RegionOne", "test-release"),
        status=status,
        duration_seconds=duration,
        error_message=error,
    )


def test_workflow_results_are_aggregated_in_input_order():
    observation = workflow_results_to_observation(
        [
            _result(ExecutionStatus.SUCCESS, 10.0),
            _result(ExecutionStatus.SUCCESS, 12.0),
            _result(ExecutionStatus.SUCCESS, 20.0),
        ],
        scenario_id="vm.lifecycle",
        target_id="small-workload",
        name="VM lifecycle",
    )

    assert observation.functional_verdict is FunctionalVerdict.PASS
    assert [sample.sequence for sample in observation.samples] == [1, 2, 3]
    assert observation.statistics.sample_count == 3
    assert observation.statistics.p50_seconds == 12.0
    assert observation.statistics.p95_seconds == pytest.approx(19.2)


def test_failed_workflow_is_retained_but_excluded_from_statistics():
    observation = workflow_results_to_observation(
        [
            _result(ExecutionStatus.SUCCESS, 10.0),
            _result(
                ExecutionStatus.FAILED,
                12.0,
                "cleanup RuntimeError: delete failed",
            ),
            _result(ExecutionStatus.SUCCESS, 14.0),
        ],
        scenario_id="vm.network_attachment",
        target_id="small-workload",
        name="VM network attachment",
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert observation.samples[1].duration_seconds == 12.0
    assert observation.samples[1].successful is False
    assert "cleanup RuntimeError" in observation.samples[1].error_message
    assert observation.statistics.sample_count == 2
    assert observation.statistics.p50_seconds == 12.0
    assert observation.assertions[1].passed is False


def test_all_failed_workflows_have_no_statistics():
    observation = workflow_results_to_observation(
        [_result(ExecutionStatus.FAILED, 1.0, None)],
        scenario_id="vm.lifecycle",
        target_id="small-workload",
        name="VM lifecycle",
    )

    assert observation.statistics is None
    assert observation.error_message == "execution 1: workflow failed"


def test_workflow_aggregation_requires_an_execution():
    with pytest.raises(ValueError, match="at least one workflow result"):
        workflow_results_to_observation(
            [],
            scenario_id="vm.lifecycle",
            target_id="small-workload",
            name="VM lifecycle",
        )


def test_workflow_aggregation_rejects_mixed_workflow_ids():
    first = _result(ExecutionStatus.SUCCESS, 1.0)
    second = WorkflowRunResult(
        workflow_id="other.workflow",
        workflow_name="Other workflow",
        environment=first.environment,
        status=ExecutionStatus.SUCCESS,
        duration_seconds=1.0,
    )

    with pytest.raises(ValueError, match="same workflow_id"):
        workflow_results_to_observation(
            [first, second],
            scenario_id="vm.lifecycle",
            target_id="small-workload",
            name="VM lifecycle",
        )


def test_workflow_aggregation_rejects_mixed_environments():
    first = _result(ExecutionStatus.SUCCESS, 1.0)
    second = WorkflowRunResult(
        workflow_id=first.workflow_id,
        workflow_name=first.workflow_name,
        environment=Environment("other-cloud", "RegionOne", "test-release"),
        status=ExecutionStatus.SUCCESS,
        duration_seconds=1.0,
    )

    with pytest.raises(ValueError, match="same environment"):
        workflow_results_to_observation(
            [first, second],
            scenario_id="vm.lifecycle",
            target_id="small-workload",
            name="VM lifecycle",
        )


def test_successful_workflow_result_cannot_hide_error_context():
    result = _result(ExecutionStatus.SUCCESS, 1.0, "unexpected error")

    with pytest.raises(ValueError, match="cannot contain errors"):
        workflow_results_to_observation(
            [result],
            scenario_id="vm.lifecycle",
            target_id="small-workload",
            name="VM lifecycle",
        )
