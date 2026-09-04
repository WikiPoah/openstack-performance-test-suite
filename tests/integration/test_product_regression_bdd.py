import os

import pytest

pytest_bdd = pytest.importorskip("pytest_bdd")
from pytest_bdd import given, scenarios, then, when

from openstack_perf.backend_reachability import (
    BackendTarget,
    observe_backend_reachability,
    require_approved_bastion,
)
from openstack_perf.product_http import (
    observe_corporate_web_application,
    observe_service_http_endpoints,
)
from openstack_perf.results import FunctionalVerdict


pytestmark = pytest.mark.integration
scenarios("features/product_regression.feature")


def _require_live_opt_in() -> None:
    if os.getenv("OPENSTACK_PERF_RUN_LIVE") != "1":
        pytest.skip("Set OPENSTACK_PERF_RUN_LIVE=1 to run live product tests")


def _required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required live-test setting is missing: {name}")
    return value


@given(
    "a configured corporate web application",
    target_fixture="web_application_context",
)
def configured_web_application():
    _require_live_opt_in()
    return {
        "base_url": _required_setting("OPENSTACK_PERF_PRODUCT_BASE_URL"),
        "release_title": _required_setting(
            "OPENSTACK_PERF_WORDPRESS_RELEASE_TITLE"
        ),
        "application_release": _required_setting(
            "OPENSTACK_PERF_APPLICATION_RELEASE"
        ),
    }


@when(
    "the consumer checks the supported web application paths",
    target_fixture="web_application_observations",
)
def check_web_application(web_application_context):
    return observe_corporate_web_application(
        web_application_context["base_url"],
        expected_release_title=web_application_context["release_title"],
    )


@then("the corporate web application should remain usable")
def web_application_is_usable(web_application_observations):
    _assert_observations_pass(web_application_observations)


@given(
    "a configured application service environment",
    target_fixture="application_service_context",
)
def configured_application_services():
    _require_live_opt_in()
    frontend_base_url = _required_setting("OPENSTACK_PERF_PRODUCT_BASE_URL")
    tomcat_base_url = _required_setting("OPENSTACK_PERF_TOMCAT_BASE_URL")
    application_release = _required_setting("OPENSTACK_PERF_APPLICATION_RELEASE")
    bastion = _required_setting("OPENSTACK_PERF_PRODUCT_BASTION")
    require_approved_bastion(bastion)
    return {
        "frontend_base_url": frontend_base_url,
        "tomcat_base_url": tomcat_base_url,
        "application_release": application_release,
        "bastion": bastion,
    }


@when(
    "the consumer checks the public endpoints and backend listeners",
    target_fixture="application_service_observations",
)
def check_application_services(application_service_context):
    http_observations = observe_service_http_endpoints(
        application_service_context["frontend_base_url"],
        application_service_context["tomcat_base_url"],
    )
    backend_observations = observe_backend_reachability(
        application_service_context["bastion"],
        (
            BackendTarget(
                "backend.database", "MariaDB listener", "10.20.1.10", 3306
            ),
            BackendTarget(
                "backend.apache", "Apache listener", "10.20.1.20", 80
            ),
            BackendTarget(
                "backend.tomcat", "Tomcat listener", "10.20.1.30", 8080
            ),
            BackendTarget(
                "backend.nginx", "nginx listener", "10.20.1.40", 80
            ),
        ),
    )
    return http_observations + backend_observations


@then("the application services should remain reachable")
def application_services_are_reachable(application_service_observations):
    _assert_observations_pass(application_service_observations)


def _assert_observations_pass(observations):
    failures = tuple(
        observation.error_message
        for observation in observations
        if observation.functional_verdict is FunctionalVerdict.FAILURE
    )
    assert not failures, "; ".join(error for error in failures if error)
