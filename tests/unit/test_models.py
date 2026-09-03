import pytest
from dataclasses import FrozenInstanceError
from openstack_perf.models import Environment, ExecutionStatus, WorkflowRunResult


def test_execution_status_values():
    """ExecutionStatus provides stable values for serialization."""
    assert ExecutionStatus.SUCCESS.value == "success"
    assert ExecutionStatus.FAILED.value == "failed"


def test_environment_captures_comparison_metadata():
    """Environment carries the identifying metadata needed for regression analysis."""
    env = Environment(
        cloud="pf9-prod-us-west",
        region="RegionOne",
        platform_release="OpenStack 2024.1"
    )
    assert env.cloud == "pf9-prod-us-west"
    assert env.region == "RegionOne"
    assert env.platform_release == "OpenStack 2024.1"


def test_environment_equality_for_result_comparison():
    """Identical environments compare equal, enabling result grouping by deployment."""
    env1 = Environment("cloud1", "RegionOne", "OpenStack 2024.1")
    env2 = Environment("cloud1", "RegionOne", "OpenStack 2024.1")
    assert env1 == env2

    env3 = Environment("cloud2", "RegionOne", "OpenStack 2024.1")
    assert env1 != env3


def test_successful_workflow_result():
    """Successful execution is recorded with environment and duration."""
    env = Environment("cloud1", "RegionOne", "OpenStack 2024.1")
    result = WorkflowRunResult(
        workflow_id="nova_vm_lifecycle",
        workflow_name="Nova VM Lifecycle",
        environment=env,
        status=ExecutionStatus.SUCCESS,
        duration_seconds=42.5
    )
    assert result.workflow_id == "nova_vm_lifecycle"
    assert result.workflow_name == "Nova VM Lifecycle"
    assert result.environment == env
    assert result.status == ExecutionStatus.SUCCESS
    assert result.duration_seconds == 42.5
    assert result.error_message is None


def test_failed_workflow_result_with_error_context():
    """Failed execution captures error details for investigation."""
    env = Environment("cloud1", "RegionOne", "OpenStack 2024.1")
    result = WorkflowRunResult(
        workflow_id="neutron_network_create",
        workflow_name="Neutron Network Creation",
        environment=env,
        status=ExecutionStatus.FAILED,
        duration_seconds=5.2,
        error_message="Network creation timeout after 5s"
    )
    assert result.status == ExecutionStatus.FAILED
    assert result.error_message == "Network creation timeout after 5s"


def test_workflow_results_are_immutable():
    """Frozen dataclass prevents accidental mutation of recorded execution data."""
    env = Environment("cloud1", "RegionOne", "OpenStack 2024.1")
    result = WorkflowRunResult(
        workflow_id="test",
        workflow_name="Test",
        environment=env,
        status=ExecutionStatus.SUCCESS,
        duration_seconds=1.0
    )
    with pytest.raises(FrozenInstanceError):
        setattr(result, "workflow_id", "different")

