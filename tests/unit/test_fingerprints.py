from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from openstack_perf.config import load_config
from openstack_perf.fingerprints import collect_environment_fingerprint


EXAMPLE = Path(__file__).parents[2] / "config" / "regression.example.toml"


def test_fingerprint_preserves_explicit_identity_and_sorts_versions():
    config = load_config(EXAMPLE)
    cloud_config = MagicMock()
    cloud_config.get_api_version.side_effect = {
        "compute": "2.1",
        "identity": "3",
        "image": None,
        "network": "2",
    }.get
    cloud_config.get_default_microversion.side_effect = {
        "compute": "2.90",
        "identity": None,
        "image": None,
        "network": None,
    }.get

    fingerprint = collect_environment_fingerprint(
        config, SimpleNamespace(config=cloud_config)
    )

    assert fingerprint.cloud == "devstack-perf"
    assert fingerprint.region == "RegionOne"
    assert fingerprint.platform_release == "OpenStack 2026.1"
    assert fingerprint.source_branch == "stable/2026.1"
    assert fingerprint.application_release == "1.0"
    assert [(item.service, item.version) for item in fingerprint.service_versions] == [
        ("compute.api", "2.1"),
        ("compute.microversion", "2.90"),
        ("identity.api", "3"),
        ("network.api", "2"),
    ]


def test_missing_or_failed_optional_versions_are_omitted_and_sanitized():
    config = load_config(EXAMPLE)
    cloud_config = MagicMock()
    cloud_config.get_api_version.side_effect = RuntimeError("token=secret")
    cloud_config.get_default_microversion.return_value = None

    fingerprint = collect_environment_fingerprint(
        config, SimpleNamespace(config=cloud_config, token="secret")
    )

    assert fingerprint.service_versions == ()
    assert "secret" not in repr(fingerprint)
    assert "SimpleNamespace" not in repr(fingerprint)
