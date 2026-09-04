import os
import uuid

import pytest

pytest_bdd = pytest.importorskip("pytest_bdd")
from pytest_bdd import given, scenarios, then, when

from openstack_perf.connection import create_connection
from openstack_perf.models import Environment, ExecutionStatus, WorkflowRunResult
from openstack_perf.vm_lifecycle import run_vm_lifecycle


pytestmark = pytest.mark.integration
scenarios("features/vm_lifecycle.feature")


def _required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required live-test setting is missing: {name}")
    return value


def _resolve_resource_id(label: str, resolver, name: str) -> str:
    try:
        resource = resolver(name, ignore_missing=False)
    except Exception as exc:
        raise RuntimeError(
            f"Unable to resolve {label} {name!r}: {type(exc).__name__}"
        ) from None
    resource_id = getattr(resource, "id", None)
    if not resource_id:
        raise RuntimeError(f"Unable to resolve {label} {name!r}: no ID returned")
    return resource_id


@given("a configured OpenStack test environment", target_fixture="scenario_context")
def configured_openstack_environment():
    if os.getenv("OPENSTACK_PERF_RUN_LIVE") != "1":
        pytest.skip("Set OPENSTACK_PERF_RUN_LIVE=1 to run live OpenStack tests")

    cloud_name = _required_setting("OPENSTACK_PERF_CLOUD")
    return {
        "connection": create_connection(cloud_name),
        "cloud_name": cloud_name,
    }


@given("a usable image, flavor, and network", target_fixture="scenario_context")
def usable_openstack_resources(scenario_context):
    connection = scenario_context["connection"]
    image_name = _required_setting("OPENSTACK_PERF_IMAGE")
    flavor_name = _required_setting("OPENSTACK_PERF_FLAVOR")
    network_name = _required_setting("OPENSTACK_PERF_NETWORK")
    return {
        **scenario_context,
        "image_id": _resolve_resource_id(
            "image", connection.image.find_image, image_name
        ),
        "flavor_id": _resolve_resource_id(
            "flavor", connection.compute.find_flavor, flavor_name
        ),
        "network_id": _resolve_resource_id(
            "network", connection.network.find_network, network_name
        ),
    }


@when("the consumer runs the virtual machine lifecycle", target_fixture="result")
def run_consumer_vm_lifecycle(scenario_context) -> WorkflowRunResult:
    environment = Environment(
        cloud=scenario_context["cloud_name"],
        region=getattr(scenario_context["connection"].config, "region_name", None)
        or "unknown",
        platform_release="unknown",
    )
    return run_vm_lifecycle(
        connection=scenario_context["connection"],
        environment=environment,
        server_name=f"openstack-perf-bdd-{uuid.uuid4().hex}",
        image_id=scenario_context["image_id"],
        flavor_id=scenario_context["flavor_id"],
        network_id=scenario_context["network_id"],
    )


@then("the lifecycle should succeed")
def lifecycle_succeeded(result: WorkflowRunResult):
    assert result.status is ExecutionStatus.SUCCESS, result.error_message
