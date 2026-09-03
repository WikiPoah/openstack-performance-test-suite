from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from openstack_perf.models import Environment, ExecutionStatus
from openstack_perf.vm_lifecycle import run_vm_lifecycle


def test_run_vm_lifecycle_success():
    """The happy path measures provisioning and cleans up the refreshed server."""
    created_server = SimpleNamespace(id="created-id", status="BUILD")
    refreshed_server = SimpleNamespace(id="refreshed-id", status="ACTIVE")
    connection = MagicMock()
    connection.compute.create_server.return_value = created_server
    connection.compute.wait_for_server.return_value = refreshed_server
    environment = Environment(
        cloud="test-cloud",
        region="RegionOne",
        platform_release="devstack",
    )

    with patch(
        "openstack_perf.vm_lifecycle.time.perf_counter",
        side_effect=[10.0, 14.5],
    ):
        result = run_vm_lifecycle(
            connection=connection,
            environment=environment,
            server_name="performance-vm",
            image_id="image-id",
            flavor_id="flavor-id",
            network_id="network-id",
            provisioning_timeout=90,
            cleanup_timeout=45,
        )

    connection.compute.create_server.assert_called_once_with(
        name="performance-vm",
        image_id="image-id",
        flavor_id="flavor-id",
        networks=[{"uuid": "network-id"}],
    )
    connection.compute.wait_for_server.assert_called_once_with(
        created_server,
        status="ACTIVE",
        wait=90,
    )
    connection.compute.delete_server.assert_called_once_with(
        "refreshed-id",
        ignore_missing=True,
    )
    connection.compute.wait_for_delete.assert_called_once_with(
        refreshed_server,
        wait=45,
    )
    assert result.workflow_id == "vm.lifecycle"
    assert result.workflow_name == "VM lifecycle"
    assert result.environment is environment
    assert result.status is ExecutionStatus.SUCCESS
    assert result.duration_seconds == 4.5
    assert result.error_message is None


def test_run_vm_lifecycle_create_failure():
    """Creation failure returns a measured failure without cleanup."""
    connection = MagicMock()
    connection.compute.create_server.side_effect = RuntimeError("quota exceeded")

    with patch(
        "openstack_perf.vm_lifecycle.time.perf_counter",
        side_effect=[10.0, 10.25],
    ):
        result = run_vm_lifecycle(
            connection,
            Environment("test-cloud", "RegionOne", "devstack"),
            "performance-vm",
            "image-id",
            "flavor-id",
            "network-id",
        )

    assert result.status is ExecutionStatus.FAILED
    assert result.duration_seconds == 0.25
    assert "RuntimeError: quota exceeded" in result.error_message
    connection.compute.delete_server.assert_not_called()
    connection.compute.wait_for_delete.assert_not_called()


def test_run_vm_lifecycle_provisioning_failure_cleans_up():
    """Provisioning failure retains the created server for cleanup."""
    created_server = SimpleNamespace(id="created-id", status="BUILD")
    connection = MagicMock()
    connection.compute.create_server.return_value = created_server
    connection.compute.wait_for_server.side_effect = TimeoutError("timed out")

    with patch(
        "openstack_perf.vm_lifecycle.time.perf_counter",
        side_effect=[20.0, 24.0],
    ):
        result = run_vm_lifecycle(
            connection,
            Environment("test-cloud", "RegionOne", "devstack"),
            "performance-vm",
            "image-id",
            "flavor-id",
            "network-id",
        )

    assert result.status is ExecutionStatus.FAILED
    assert result.duration_seconds == 4.0
    assert "TimeoutError: timed out" in result.error_message
    connection.compute.delete_server.assert_called_once_with(
        "created-id", ignore_missing=True
    )
    connection.compute.wait_for_delete.assert_called_once_with(
        created_server, wait=120
    )


def test_run_vm_lifecycle_validation_failure_cleans_up():
    """Invalid ACTIVE resource state returns failure after cleanup."""
    created_server = SimpleNamespace(id="created-id", status="BUILD")
    refreshed_server = SimpleNamespace(id="refreshed-id", status="ERROR")
    connection = MagicMock()
    connection.compute.create_server.return_value = created_server
    connection.compute.wait_for_server.return_value = refreshed_server

    with patch(
        "openstack_perf.vm_lifecycle.time.perf_counter",
        side_effect=[30.0, 33.0],
    ):
        result = run_vm_lifecycle(
            connection,
            Environment("test-cloud", "RegionOne", "devstack"),
            "performance-vm",
            "image-id",
            "flavor-id",
            "network-id",
        )

    assert result.status is ExecutionStatus.FAILED
    assert "validation failed" in result.error_message
    connection.compute.delete_server.assert_called_once_with(
        "refreshed-id", ignore_missing=True
    )
    connection.compute.wait_for_delete.assert_called_once_with(
        refreshed_server, wait=120
    )


def test_run_vm_lifecycle_cleanup_failure_fails_result():
    """Cleanup failure changes an otherwise successful run to failure."""
    created_server = SimpleNamespace(id="created-id", status="BUILD")
    refreshed_server = SimpleNamespace(id="refreshed-id", status="ACTIVE")
    connection = MagicMock()
    connection.compute.create_server.return_value = created_server
    connection.compute.wait_for_server.return_value = refreshed_server
    connection.compute.delete_server.side_effect = RuntimeError("delete denied")

    with patch(
        "openstack_perf.vm_lifecycle.time.perf_counter",
        side_effect=[40.0, 45.0],
    ):
        result = run_vm_lifecycle(
            connection,
            Environment("test-cloud", "RegionOne", "devstack"),
            "performance-vm",
            "image-id",
            "flavor-id",
            "network-id",
        )

    assert result.status is ExecutionStatus.FAILED
    assert "cleanup RuntimeError: delete denied" in result.error_message
    assert result.duration_seconds == 5.0
    connection.compute.wait_for_delete.assert_not_called()


def test_run_vm_lifecycle_preserves_provisioning_and_cleanup_failures():
    """Primary and cleanup failures are both retained in the result."""
    created_server = SimpleNamespace(id="created-id", status="BUILD")
    connection = MagicMock()
    connection.compute.create_server.return_value = created_server
    connection.compute.wait_for_server.side_effect = RuntimeError("build failed")
    connection.compute.delete_server.side_effect = RuntimeError("delete failed")

    with patch(
        "openstack_perf.vm_lifecycle.time.perf_counter",
        side_effect=[50.0, 51.5],
    ):
        result = run_vm_lifecycle(
            connection,
            Environment("test-cloud", "RegionOne", "devstack"),
            "performance-vm",
            "image-id",
            "flavor-id",
            "network-id",
        )

    assert result.status is ExecutionStatus.FAILED
    assert "RuntimeError: build failed" in result.error_message
    assert "cleanup RuntimeError: delete failed" in result.error_message
    assert result.duration_seconds == 1.5


def test_run_vm_lifecycle_created_server_without_id_reports_cleanup_failure():
    """A created resource without an ID cannot be safely cleaned up."""
    created_server = SimpleNamespace(id=None, status="BUILD")
    refreshed_server = SimpleNamespace(id=None, status="ACTIVE")
    connection = MagicMock()
    connection.compute.create_server.return_value = created_server
    connection.compute.wait_for_server.return_value = refreshed_server

    with patch(
        "openstack_perf.vm_lifecycle.time.perf_counter",
        side_effect=[60.0, 61.0],
    ):
        result = run_vm_lifecycle(
            connection,
            Environment("test-cloud", "RegionOne", "devstack"),
            "performance-vm",
            "image-id",
            "flavor-id",
            "network-id",
        )

    assert result.status is ExecutionStatus.FAILED
    assert "validation failed: server has no ID" in result.error_message
    assert "cleanup could not be attempted" in result.error_message
    connection.compute.delete_server.assert_not_called()
    connection.compute.wait_for_delete.assert_not_called()


def test_run_vm_lifecycle_wait_for_delete_failure_fails_result():
    """Wait-for-delete failure is reported without changing provisioning time."""
    created_server = SimpleNamespace(id="created-id", status="BUILD")
    refreshed_server = SimpleNamespace(id="refreshed-id", status="ACTIVE")
    connection = MagicMock()
    connection.compute.create_server.return_value = created_server
    connection.compute.wait_for_server.return_value = refreshed_server
    connection.compute.wait_for_delete.side_effect = TimeoutError("delete timed out")

    with patch(
        "openstack_perf.vm_lifecycle.time.perf_counter",
        side_effect=[70.0, 74.0],
    ):
        result = run_vm_lifecycle(
            connection,
            Environment("test-cloud", "RegionOne", "devstack"),
            "performance-vm",
            "image-id",
            "flavor-id",
            "network-id",
        )

    assert result.status is ExecutionStatus.FAILED
    assert "cleanup TimeoutError: delete timed out" in result.error_message
    assert result.duration_seconds == 4.0
    connection.compute.delete_server.assert_called_once_with(
        "refreshed-id", ignore_missing=True
    )
    connection.compute.wait_for_delete.assert_called_once_with(
        refreshed_server, wait=120
    )
    assert connection.compute.method_calls == [
        call.create_server(
            name="performance-vm",
            image_id="image-id",
            flavor_id="flavor-id",
            networks=[{"uuid": "network-id"}],
        ),
        call.wait_for_server(created_server, status="ACTIVE", wait=180),
        call.delete_server("refreshed-id", ignore_missing=True),
        call.wait_for_delete(refreshed_server, wait=120),
    ]
