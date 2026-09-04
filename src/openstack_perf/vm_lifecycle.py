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
    verify_network_attachment: bool = False,
) -> WorkflowRunResult:
    """Create, validate, and delete one OpenStack server."""
    start = time.perf_counter()
    created_server = None
    server = None
    network_port = None
    primary_error = None
    cleanup_errors = []

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
        elif verify_network_attachment:
            try:
                matching_ports = [
                    port
                    for port in connection.network.ports(device_id=server.id)
                    if getattr(port, "network_id", None) == network_id
                ]
            except Exception as exc:
                primary_error = f"network validation {type(exc).__name__}: {exc}"
            else:
                if len(matching_ports) != 1:
                    primary_error = (
                        "network validation failed: expected exactly one port "
                        f"on requested network, found {len(matching_ports)}"
                    )
                else:
                    matching_port = matching_ports[0]
                    if not getattr(matching_port, "id", None):
                        primary_error = (
                            "network validation failed: matching port has no usable ID"
                        )
                    elif not any(
                        isinstance(fixed_ip, dict) and fixed_ip.get("ip_address")
                        for fixed_ip in getattr(matching_port, "fixed_ips", None) or []
                    ):
                        primary_error = (
                            "network validation failed: matching port has no usable "
                            "fixed IP"
                        )
                    else:
                        network_port = matching_port
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
            cleanup_errors.append(f"cleanup {type(exc).__name__}: {exc}")
        else:
            if network_port is not None:
                try:
                    connection.network.wait_for_delete(
                        network_port,
                        wait=cleanup_timeout,
                    )
                except Exception as exc:
                    cleanup_errors.append(
                        f"network cleanup {type(exc).__name__}: {exc}"
                    )
    elif created_server is not None:
        cleanup_errors.append(
            "cleanup could not be attempted: created server has no usable ID"
        )

    errors = [error for error in [primary_error, *cleanup_errors] if error]

    return WorkflowRunResult(
        workflow_id="vm.lifecycle",
        workflow_name="VM lifecycle",
        environment=environment,
        status=ExecutionStatus.FAILED if errors else ExecutionStatus.SUCCESS,
        duration_seconds=stop - start,
        error_message="; ".join(errors) or None,
    )
