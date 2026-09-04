from types import SimpleNamespace
from unittest.mock import MagicMock

from openstack import exceptions
import pytest

from openstack_perf.artifacts import deserialize_run, serialize_run
from openstack_perf.infrastructure_state import observe_server_attachment
from openstack_perf.results import (
    CleanSnapshotStatus,
    EnvironmentFingerprint,
    FunctionalVerdict,
    RegressionRunResult,
    RunMetadata,
    RunRole,
)


def _connection():
    connection = MagicMock()
    connection.current_project_id = "corp-project-id"
    connection.identity.get_project.return_value = SimpleNamespace(
        id="corp-project-id", name="corp"
    )
    connection.compute.find_server.return_value = SimpleNamespace(
        id="server-id", name="corp-db", status="ACTIVE"
    )
    connection.network.find_network.return_value = SimpleNamespace(
        id="network-id", name="corp-network"
    )
    connection.network.ports.return_value = [
        SimpleNamespace(
            id="port-id",
            device_id="server-id",
            network_id="network-id",
            fixed_ips=[{"ip_address": "10.20.1.10"}],
        )
    ]
    return connection


def _clock(*values):
    iterator = iter(values)
    return lambda: next(iterator)


def test_server_attachment_is_checked_with_read_only_exact_lookups():
    connection = _connection()

    observation = observe_server_attachment(
        connection,
        expected_project_name="corp",
        server_name="corp-db",
        network_name="corp-network",
        expected_fixed_ip="10.20.1.10",
        clock=_clock(10.0, 11.5),
    )

    assert observation.functional_verdict is FunctionalVerdict.PASS
    assert observation.target_id == "corp-db"
    assert observation.samples[0].duration_seconds == 1.5
    connection.authorize.assert_called_once_with()
    connection.identity.get_project.assert_called_once_with("corp-project-id")
    connection.compute.find_server.assert_called_once_with(
        "corp-db", ignore_missing=False, all_projects=False
    )
    connection.network.find_network.assert_called_once_with(
        "corp-network", ignore_missing=False
    )
    connection.network.ports.assert_called_once_with(device_id="server-id")
    connection.compute.create_server.assert_not_called()
    connection.compute.delete_server.assert_not_called()
    connection.network.create_port.assert_not_called()
    connection.network.update_port.assert_not_called()
    connection.network.delete_port.assert_not_called()


def test_wrong_project_scope_stops_before_infrastructure_lookup():
    connection = _connection()
    connection.identity.get_project.return_value = SimpleNamespace(
        id="corp-project-id", name="perf"
    )

    observation = observe_server_attachment(
        connection,
        expected_project_name="corp",
        server_name="corp-db",
        network_name="corp-network",
        expected_fixed_ip="10.20.1.10",
        clock=_clock(1.0, 1.1),
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "project name" in observation.error_message
    connection.compute.find_server.assert_not_called()
    connection.network.find_network.assert_not_called()
    connection.network.ports.assert_not_called()


def test_authorization_failure_text_is_not_retained():
    connection = _connection()
    connection.authorize.side_effect = RuntimeError("token=sensitive-token")

    observation = _observe(connection)

    assert observation.error_message == (
        "infrastructure inspection failed: RuntimeError"
    )
    assert "sensitive-token" not in repr(observation)


def test_server_must_be_active():
    connection = _connection()
    connection.compute.find_server.return_value.status = "SHUTOFF"

    observation = observe_server_attachment(
        connection,
        expected_project_name="corp",
        server_name="corp-db",
        network_name="corp-network",
        expected_fixed_ip="10.20.1.10",
        clock=_clock(1.0, 1.2),
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "not ACTIVE" in observation.error_message


@pytest.mark.parametrize(
    "error",
    [
        exceptions.NotFoundException("server missing"),
        exceptions.DuplicateResource("duplicate server"),
    ],
)
def test_missing_or_ambiguous_server_is_safely_reported(error):
    connection = _connection()
    connection.compute.find_server.side_effect = error

    observation = _observe(connection)

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert type(error).__name__ in observation.error_message
    assert str(error) not in observation.error_message
    connection.network.find_network.assert_not_called()


@pytest.mark.parametrize(
    "server, message",
    [
        (SimpleNamespace(id=None, name="corp-db", status="ACTIVE"), "no usable ID"),
        (
            SimpleNamespace(id="server-id", name="other-server", status="ACTIVE"),
            "name does not match",
        ),
    ],
)
def test_server_identity_must_match_configuration(server, message):
    connection = _connection()
    connection.compute.find_server.return_value = server

    observation = _observe(connection)

    assert message in observation.error_message
    connection.network.find_network.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        exceptions.NotFoundException("network missing"),
        exceptions.DuplicateResource("duplicate network"),
    ],
)
def test_missing_or_ambiguous_network_is_safely_reported(error):
    connection = _connection()
    connection.network.find_network.side_effect = error

    observation = _observe(connection)

    assert type(error).__name__ in observation.error_message
    assert str(error) not in observation.error_message
    connection.network.ports.assert_not_called()


def test_network_requires_an_id():
    connection = _connection()
    connection.network.find_network.return_value.id = None

    observation = _observe(connection)

    assert "network has no usable ID" in observation.error_message
    connection.network.ports.assert_not_called()


def test_attachment_requires_one_exact_matching_port():
    connection = _connection()
    connection.network.ports.return_value = [
        SimpleNamespace(
            id="wrong-network-port",
            device_id="server-id",
            network_id="other-network",
            fixed_ips=[{"ip_address": "10.20.1.10"}],
        ),
        SimpleNamespace(
            id="wrong-ip-port",
            device_id="server-id",
            network_id="network-id",
            fixed_ips=[{"ip_address": "10.20.1.11"}],
        ),
    ]

    observation = observe_server_attachment(
        connection,
        expected_project_name="corp",
        server_name="corp-db",
        network_name="corp-network",
        expected_fixed_ip="10.20.1.10",
        clock=_clock(1.0, 1.5),
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "found 0" in observation.error_message
    assert observation.statistics is None


def test_attachment_rejects_multiple_exact_matching_ports():
    connection = _connection()
    connection.network.ports.return_value.append(
        SimpleNamespace(
            id="second-port",
            device_id="server-id",
            network_id="network-id",
            fixed_ips=[{"ip_address": "10.20.1.10"}],
        )
    )

    observation = _observe(connection)

    assert "found 2" in observation.error_message


@pytest.mark.parametrize(
    "port",
    [
        SimpleNamespace(
            id="port-id",
            device_id="server-id",
            network_id="network-id",
            fixed_ips="malformed",
        ),
        SimpleNamespace(
            id="port-id",
            device_id=None,
            network_id="network-id",
            fixed_ips=[{"ip_address": "10.20.1.10"}],
        ),
        SimpleNamespace(
            id="port-id",
            device_id="other-server",
            network_id="network-id",
            fixed_ips=[{"ip_address": "10.20.1.10"}],
        ),
    ],
)
def test_malformed_or_wrong_server_port_does_not_match(port):
    connection = _connection()
    connection.network.ports.return_value = [port]

    observation = _observe(connection)

    assert "found 0" in observation.error_message


def test_matching_port_requires_an_id_without_requiring_port_status():
    connection = _connection()
    connection.network.ports.return_value[0].id = None
    connection.network.ports.return_value[0].status = "DOWN"

    observation = observe_server_attachment(
        connection,
        expected_project_name="corp",
        server_name="corp-db",
        network_name="corp-network",
        expected_fixed_ip="10.20.1.10",
        clock=_clock(1.0, 1.5),
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "port has no usable ID" in observation.error_message


def test_port_status_is_not_a_required_assertion():
    connection = _connection()
    connection.network.ports.return_value[0].status = "DOWN"

    observation = _observe(connection)

    assert observation.functional_verdict is FunctionalVerdict.PASS


def test_infrastructure_observation_survives_artifact_round_trip():
    observation = _observe(_connection())
    run = RegressionRunResult(
        metadata=RunMetadata(
            "run-1",
            RunRole.CANDIDATE,
            "2026-09-04T10:00:00Z",
            "2026-09-04T10:01:00Z",
            CleanSnapshotStatus.CLEAN,
        ),
        environment=EnvironmentFingerprint(
            "read-only-cloud", "RegionOne", "test", "main", "candidate"
        ),
        observations=(observation,),
    )

    assert deserialize_run(serialize_run(run)) == run


def _observe(connection):
    return observe_server_attachment(
        connection,
        expected_project_name="corp",
        server_name="corp-db",
        network_name="corp-network",
        expected_fixed_ip="10.20.1.10",
        clock=_clock(1.0, 1.5),
    )
