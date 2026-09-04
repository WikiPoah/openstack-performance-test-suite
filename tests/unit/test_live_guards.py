import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _load_bdd_module():
    path = (
        Path(__file__).parents[1]
        / "integration"
        / "test_platform_regression_bdd.py"
    )
    spec = importlib.util.spec_from_file_location(
        "platform_regression_bdd_for_unit_tests", path
    )
    module = importlib.util.module_from_spec(spec)
    with patch("pytest_bdd.scenarios"):
        spec.loader.exec_module(module)
    return module


def test_live_guard_prevents_connection_creation(monkeypatch):
    module = _load_bdd_module()
    create_connection = MagicMock()
    monkeypatch.setattr(module, "create_connection", create_connection)
    monkeypatch.delenv("OPENSTACK_PERF_RUN_LIVE", raising=False)

    with pytest.raises(pytest.skip.Exception):
        module.configured_consumer_environment()
    with pytest.raises(pytest.skip.Exception):
        module.configured_infrastructure_environment()

    create_connection.assert_not_called()


def test_infrastructure_fixture_rejects_unapproved_cloud_before_connection(
    monkeypatch,
):
    module = _load_bdd_module()
    create_connection = MagicMock()
    monkeypatch.setattr(module, "create_connection", create_connection)
    settings = {
        "OPENSTACK_PERF_RUN_LIVE": "1",
        "OPENSTACK_PERF_CORP_CLOUD": "devstack-perf",
        "OPENSTACK_PERF_CORP_PROJECT": "corp",
        "OPENSTACK_PERF_CORP_SERVER": "corp-db",
        "OPENSTACK_PERF_CORP_NETWORK": "corp-network",
        "OPENSTACK_PERF_CORP_FIXED_IP": "10.20.1.10",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="approved read-only cloud"):
        module.configured_infrastructure_environment()

    create_connection.assert_not_called()
