import time

import openstack

from openstack_perf.models import Environment, ExecutionStatus, WorkflowRunResult


def run_vm_lifecycle(
    connection: openstack.connection.Connection,
    environment: Environment,
    server_name: str,
    image_id: str,
    flavor_id: str,
    network_id: str,
    provisioning_timeout: float = 180,
    cleanup_timeout: float = 120,
) -> WorkflowRunResult:
    """Create, validate, and delete one OpenStack server."""
    start = time.perf_counter()
    created_server = None
    server = None
    primary_error = None
    cleanup_error = None

    try:
        created_server = connection.compute.create_server(
            name=server_name,
            image_id=image_id,
            flavor_id=flavor_id,
            networks=[{"uuid": network_id}],
        )
        server = created_server
        server = connection.compute.wait_for_server(
            server,
            status="ACTIVE",
            wait=provisioning_timeout,
        )
        stop = time.perf_counter()
        if not server.id:
            primary_error = "validation failed: server has no ID"
        elif server.status != "ACTIVE":
            primary_error = (
                f"validation failed: server status is {server.status!r}, "
                "expected 'ACTIVE'"
            )
    except Exception as exc:
        stop = time.perf_counter()
        primary_error = f"{type(exc).__name__}: {exc}"

    cleanup_server = server
    if not getattr(cleanup_server, "id", None):
        cleanup_server = created_server

    if getattr(cleanup_server, "id", None):
        try:
            connection.compute.delete_server(
                cleanup_server.id,
                ignore_missing=True,
            )
            connection.compute.wait_for_delete(
                cleanup_server,
                wait=cleanup_timeout,
            )
        except Exception as exc:
            cleanup_error = f"cleanup {type(exc).__name__}: {exc}"
    elif created_server is not None:
        cleanup_error = "cleanup could not be attempted: created server has no usable ID"

    errors = [error for error in (primary_error, cleanup_error) if error]

    return WorkflowRunResult(
        workflow_id="vm.lifecycle",
        workflow_name="VM lifecycle",
        environment=environment,
        status=ExecutionStatus.FAILED if errors else ExecutionStatus.SUCCESS,
        duration_seconds=stop - start,
        error_message="; ".join(errors) or None,
    )
