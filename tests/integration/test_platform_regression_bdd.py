import os

import pytest

pytest_bdd = pytest.importorskip("pytest_bdd")
from pytest_bdd import given, scenarios, then, when

from openstack_perf.connection import create_connection
from openstack_perf.infrastructure_state import observe_server_attachment
from openstack_perf.platform_discovery import (
    observe_boot_image,
    observe_service_discovery,
)
from openstack_perf.results import FunctionalVerdict


pytestmark = pytest.mark.integration
scenarios(
    "features/platform_discovery.feature",
    "features/infrastructure_state.feature",
)


def _require_live_opt_in() -> None:
    if os.getenv("OPENSTACK_PERF_RUN_LIVE") != "1":
        pytest.skip("Set OPENSTACK_PERF_RUN_LIVE=1 to run live OpenStack tests")


def _required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required live-test setting is missing: {name}")
    return value


@given(
    "a configured writable OpenStack consumer environment",
    target_fixture="consumer_context",
)
def configured_consumer_environment():
    _require_live_opt_in()
    return {
        "cloud_name": _required_setting("OPENSTACK_PERF_CLOUD"),
        "project_name": _required_setting("OPENSTACK_PERF_PROJECT"),
        "image_name": _required_setting("OPENSTACK_PERF_IMAGE"),
    }


@when(
    "the consumer discovers required services and the expected boot image",
    target_fixture="discovery_observations",
)
def discover_services_and_image(consumer_context):
    cloud_name = consumer_context["cloud_name"]
    service_observation = observe_service_discovery(
        lambda: create_connection(cloud_name),
        expected_project_name=consumer_context["project_name"],
    )
    image_connection = create_connection(cloud_name)
    image_connection.authorize()
    image_observation = observe_boot_image(
        image_connection,
        expected_image_name=consumer_context["image_name"],
    )
    return service_observation, image_observation


@then("the required services and boot image should be available")
def required_services_and_image_available(discovery_observations):
    for observation in discovery_observations:
        assert observation.functional_verdict is FunctionalVerdict.PASS, (
            observation.error_message
        )


@given(
    "a configured read-only infrastructure environment",
    target_fixture="infrastructure_context",
)
def configured_infrastructure_environment():
    _require_live_opt_in()
    cloud_name = _required_setting("OPENSTACK_PERF_CORP_CLOUD")
    project_name = _required_setting("OPENSTACK_PERF_CORP_PROJECT")
    server_name = _required_setting("OPENSTACK_PERF_CORP_SERVER")
    network_name = _required_setting("OPENSTACK_PERF_CORP_NETWORK")
    fixed_ip = _required_setting("OPENSTACK_PERF_CORP_FIXED_IP")
    if cloud_name != "devstack-corp-ro":
        raise RuntimeError(
            "The infrastructure scenario requires the approved read-only cloud"
        )
    return {
        "connection": create_connection(cloud_name),
        "project_name": project_name,
        "server_name": server_name,
        "network_name": network_name,
        "fixed_ip": fixed_ip,
    }


@when(
    "the consumer inspects the critical server attachment",
    target_fixture="infrastructure_observation",
)
def inspect_critical_server_attachment(infrastructure_context):
    return observe_server_attachment(
        infrastructure_context["connection"],
        expected_project_name=infrastructure_context["project_name"],
        server_name=infrastructure_context["server_name"],
        network_name=infrastructure_context["network_name"],
        expected_fixed_ip=infrastructure_context["fixed_ip"],
    )


@then("the critical server should be active and correctly attached")
def critical_server_available(infrastructure_observation):
    assert (
        infrastructure_observation.functional_verdict
        is FunctionalVerdict.PASS
    ), infrastructure_observation.error_message
