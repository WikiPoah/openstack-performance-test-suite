from collections.abc import Callable
import time

import openstack

from openstack_perf.platform_errors import (
    PlatformValidationError,
    platform_failure_message,
)
from openstack_perf.results import (
    AssertionResult,
    FunctionalVerdict,
    ScenarioObservation,
    TimingSample,
)
from openstack_perf.platform_discovery import validate_project_scope
from openstack_perf.statistics import calculate_timing_statistics


def observe_server_attachment(
    connection: openstack.connection.Connection,
    *,
    expected_project_name: str,
    server_name: str,
    network_name: str,
    expected_fixed_ip: str,
    clock: Callable[[], float] = time.perf_counter,
) -> ScenarioObservation:
    """Inspect one existing server attachment using read-only SDK operations."""
    start = clock()
    stop = None
    try:
        connection.authorize()
        project_id = connection.current_project_id
        if not project_id:
            raise PlatformValidationError(
                "authenticated session has no project ID"
            )
        project = connection.identity.get_project(project_id)
        validate_project_scope(project_id, project, expected_project_name)

        server = connection.compute.find_server(
            server_name,
            ignore_missing=False,
            all_projects=False,
        )
        server_id = getattr(server, "id", None)
        if not server_id:
            raise PlatformValidationError("expected server has no usable ID")
        if getattr(server, "name", None) != server_name:
            raise PlatformValidationError(
                "resolved server name does not match expected server"
            )
        if getattr(server, "status", None) != "ACTIVE":
            raise PlatformValidationError("expected server is not ACTIVE")

        network = connection.network.find_network(
            network_name, ignore_missing=False
        )
        network_id = getattr(network, "id", None)
        if not network_id:
            raise PlatformValidationError("expected network has no usable ID")

        ports = tuple(connection.network.ports(device_id=server_id))
        stop = clock()
        matching_ports = [
            port
            for port in ports
            if getattr(port, "device_id", None) == server_id
            and getattr(port, "network_id", None) == network_id
            and any(
                isinstance(fixed_ip, dict)
                and fixed_ip.get("ip_address") == expected_fixed_ip
                for fixed_ip in getattr(port, "fixed_ips", None) or ()
            )
        ]
        if len(matching_ports) != 1:
            raise PlatformValidationError(
                "expected exactly one port for the configured server, network, "
                f"and fixed IP; found {len(matching_ports)}"
            )
        if not getattr(matching_ports[0], "id", None):
            raise PlatformValidationError("matching port has no usable ID")
    except Exception as exc:
        if stop is None:
            stop = clock()
        error = platform_failure_message("infrastructure inspection", exc)
        sample = TimingSample(1, stop - start, False, error)
        return ScenarioObservation(
            scenario_id="infrastructure.server_attachment",
            target_id=server_name,
            name="Critical server attachment",
            functional_verdict=FunctionalVerdict.FAILURE,
            assertions=(
                AssertionResult(
                    "infrastructure.server_attachment.available",
                    False,
                    error,
                ),
            ),
            samples=(sample,),
            statistics=None,
            error_message=error,
        )

    sample = TimingSample(1, stop - start)
    return ScenarioObservation(
        scenario_id="infrastructure.server_attachment",
        target_id=server_name,
        name="Critical server attachment",
        functional_verdict=FunctionalVerdict.PASS,
        assertions=(
            AssertionResult(
                "infrastructure.server_attachment.available",
                True,
            ),
        ),
        samples=(sample,),
        statistics=calculate_timing_statistics((sample.duration_seconds,)),
    )
