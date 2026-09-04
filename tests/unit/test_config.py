from pathlib import Path
from unittest.mock import patch

import pytest

from openstack_perf.comparison import Metric
from openstack_perf.config import ConfigurationError, load_config


EXAMPLE = Path(__file__).parents[2] / "config" / "regression.example.toml"


def _load_text(tmp_path, text):
    path = tmp_path / "regression.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def _example_text():
    return EXAMPLE.read_text(encoding="utf-8")


def test_example_configuration_is_complete_and_maps_policies():
    config = load_config(EXAMPLE)

    assert config.name == "devstack-release-regression"
    assert config.consumer.cloud == "devstack-perf"
    assert config.corp.cloud == "devstack-corp-ro"
    assert config.product.backends[0].target_id == "backend.mariadb"
    assert config.scenarios.page_delivery.samples == 10
    assert config.scenarios.vm_lifecycle.samples == 3
    assert [item.metric for item in config.comparison_policies[0].tolerances] == [
        Metric.P50,
        Metric.P95,
    ]
    assert ("infrastructure.server_attachment", "corp-db") in (
        config.functional_only_keys
    )
    assert ("product.page_delivery", "wordpress.home") not in (
        config.functional_only_keys
    )
    assert len(config.functional_only_keys) == 18
    page_policy = next(
        policy
        for policy in config.comparison_policies
        if policy.scenario_id == "product.page_delivery"
    )
    assert page_policy.target_id == "wordpress.home"
    assert page_policy.minimum_sample_count == 10
    assert [item.relative for item in page_policy.tolerances] == [0.50, 0.50]
    assert [item.absolute_seconds for item in page_policy.tolerances] == [0.25, 0.50]


def test_config_validation_performs_no_external_activity():
    with patch("openstack.connect") as connect, patch("subprocess.run") as run:
        load_config(EXAMPLE)

    connect.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("schema_version = 1", "schema_version = 2", "schema version"),
        ("name = \"devstack-release-regression\"", "unknown = true\nname = \"x\"", "unknown field"),
        ("samples = 10", "samples = true", "must be an integer"),
        ("samples = 10", "samples = 0", "at least 1"),
        ("http_timeout_seconds = 10", "http_timeout_seconds = 11", "at most 10"),
        ("maximum_body_bytes = 2097152", "maximum_body_bytes = true", "must be an integer"),
        ("port = 3306", "port = true", "must be an integer"),
        ("host = \"10.20.1.10\"", "host = \"not-an-ip\"", "not-an-ip"),
        ("base_url = \"http://172.24.4.10\"", "base_url = \"http://user:password@172.24.4.10\"", "credentials"),
        ("cloud = \"devstack-corp-ro\"", "cloud = \"devstack-perf\"", "read-only corp"),
        ("bastion = \"wiki@172.24.4.20\"", "bastion = \"wiki@other\"", "approved bastion"),
    ],
)
def test_invalid_configuration_boundaries(tmp_path, old, new, match):
    with pytest.raises((ConfigurationError, ValueError), match=match):
        _load_text(tmp_path, _example_text().replace(old, new, 1))


def test_secret_bearing_key_is_rejected(tmp_path):
    text = _example_text().replace(
        '[release]\n', '[release]\npassword = "do-not-store"\n', 1
    )

    with pytest.raises(ConfigurationError, match="secret-bearing"):
        _load_text(tmp_path, text)


def test_duplicate_backend_ids_are_rejected(tmp_path):
    text = _example_text().replace("backend.apache", "backend.mariadb", 1)

    with pytest.raises(ConfigurationError, match="must be unique"):
        _load_text(tmp_path, text)


def test_duplicate_policy_keys_are_rejected(tmp_path):
    policy = '''
[[comparison.policies]]
scenario_id = "identity.service_discovery"
target_id = "perf"
minimum_sample_count = 10
p50_relative = 0.1
'''

    with pytest.raises(ConfigurationError, match="policy keys must be unique"):
        _load_text(tmp_path, _example_text() + policy)


def test_performance_scenario_requires_policy(tmp_path):
    text = _example_text().replace(
        'scenario_id = "identity.service_discovery"',
        'scenario_id = "unknown.scenario"',
        1,
    )

    with pytest.raises(ConfigurationError, match="disabled or unknown"):
        _load_text(tmp_path, text)


def test_page_delivery_performance_scenario_requires_policy(tmp_path):
    text = _example_text()
    start = text.index(
        '[[comparison.policies]]\nscenario_id = "product.page_delivery"'
    )
    end = text.index('[[comparison.policies]]', start + 1)

    with pytest.raises(ConfigurationError, match="require comparison policies"):
        _load_text(tmp_path, text[:start] + text[end:])


def test_functional_only_scenario_rejects_performance_policy(tmp_path):
    policy = '''
[[comparison.policies]]
scenario_id = "infrastructure.server_attachment"
target_id = "corp-db"
minimum_sample_count = 1
p50_relative = 0.1
'''

    with pytest.raises(ConfigurationError, match="functional-only"):
        _load_text(tmp_path, _example_text() + policy)


def test_disabled_scenarios_do_not_require_conditional_sections(tmp_path):
    text = _example_text()
    text = text.replace("[corp]\n", "[unused_corp]\n", 1)
    text = text.replace(
        "[scenarios.infrastructure_state]\nenabled = true",
        "[scenarios.infrastructure_state]\nenabled = false",
    )
    # Remove the now-unknown table rather than accepting unused arbitrary data.
    start = text.index("[unused_corp]")
    end = text.index("[product]")
    text = text[:start] + text[end:]

    config = _load_text(tmp_path, text)

    assert config.corp is None


def test_missing_required_conditional_section_is_rejected(tmp_path):
    text = _example_text()
    start = text.index("[corp]")
    end = text.index("[product]")

    with pytest.raises(ConfigurationError, match="missing required section: corp"):
        _load_text(tmp_path, text[:start] + text[end:])


def _without_backend_tables(text):
    start = text.index("[[product.backends]]")
    end = text.index("[scenarios.service_discovery]")
    return text[:start] + text[end:]


def test_web_only_product_configuration_omits_service_only_fields(tmp_path):
    text = _example_text().replace(
        "[scenarios.application_services]\nenabled = true",
        "[scenarios.application_services]\nenabled = false",
    )
    text = text.replace('tomcat_base_url = "http://172.24.4.10:8080"\n', "")
    text = text.replace('bastion = "wiki@172.24.4.20"\n', "")
    text = _without_backend_tables(text)

    config = _load_text(tmp_path, text)

    assert config.product.tomcat_base_url is None
    assert config.product.bastion is None
    assert config.product.backends == ()


def test_application_services_only_omits_web_only_title(tmp_path):
    text = _example_text().replace(
        "[scenarios.web_application]\nenabled = true",
        "[scenarios.web_application]\nenabled = false",
    )
    text = text.replace('expected_release_title = "Release notes 1.0"\n', "")

    config = _load_text(tmp_path, text)

    assert config.product.expected_release_title is None


@pytest.mark.parametrize(
    "scenario,field_line,match",
    [
        (
            "web_application",
            'expected_release_title = "Release notes 1.0"\n',
            "expected_release_title",
        ),
        (
            "application_services",
            'tomcat_base_url = "http://172.24.4.10:8080"\n',
            "tomcat_base_url",
        ),
        (
            "application_services",
            'bastion = "wiki@172.24.4.20"\n',
            "bastion",
        ),
    ],
)
def test_enabled_product_scenario_rejects_its_required_missing_field(
    tmp_path, scenario, field_line, match
):
    text = _example_text()
    other = (
        "application_services" if scenario == "web_application" else "web_application"
    )
    text = text.replace(
        f"[scenarios.{other}]\nenabled = true",
        f"[scenarios.{other}]\nenabled = false",
    )
    text = text.replace(field_line, "")

    with pytest.raises(ConfigurationError, match=match):
        _load_text(tmp_path, text)


def test_application_services_requires_backend_definitions(tmp_path):
    text = _example_text().replace(
        "[scenarios.web_application]\nenabled = true",
        "[scenarios.web_application]\nenabled = false",
    )
    text = _without_backend_tables(text)

    with pytest.raises(ConfigurationError, match="backends must not be empty"):
        _load_text(tmp_path, text)


@pytest.mark.parametrize(
    "name,valid",
    [
        ("normal-name", True),
        ("../example", True),
        ("!!!", False),
        ("Δοκιμή", False),
        ("x" * 96, True),
        ("x" * 97, False),
    ],
)
def test_configuration_name_is_safe_for_artifact_filename(tmp_path, name, valid):
    text = _example_text().replace(
        'name = "devstack-release-regression"', f'name = "{name}"'
    )

    if valid:
        assert _load_text(tmp_path, text).name == name
    else:
        with pytest.raises(ConfigurationError, match="configuration name"):
            _load_text(tmp_path, text)


@pytest.mark.parametrize("minimum", [9, 10])
def test_policy_minimum_at_or_below_configured_samples_is_valid(tmp_path, minimum):
    text = _example_text().replace(
        "minimum_sample_count = 10",
        f"minimum_sample_count = {minimum}",
        1,
    )

    assert _load_text(tmp_path, text).comparison_policies[0].minimum_sample_count == minimum


def test_policy_minimum_above_configured_samples_is_rejected(tmp_path):
    text = _example_text().replace(
        "minimum_sample_count = 10", "minimum_sample_count = 11", 1
    )

    with pytest.raises(ConfigurationError, match="exceeds configured sample count"):
        _load_text(tmp_path, text)
