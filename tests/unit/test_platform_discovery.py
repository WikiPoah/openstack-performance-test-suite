from types import SimpleNamespace
from unittest.mock import MagicMock, call

from openstack import exceptions
import pytest

from openstack_perf.artifacts import deserialize_run, serialize_run
from openstack_perf.platform_discovery import (
    observe_boot_image,
    observe_service_discovery,
)
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
    connection.current_project_id = "project-id"
    connection.identity.get_project.return_value = SimpleNamespace(
        id="project-id", name="perf"
    )
    connection.endpoint_for.side_effect = {
        "compute": "https://compute.example/v2",
        "network": "https://network.example/v2",
        "image": "https://image.example/v2",
    }.get
    return connection


def _clock(*values):
    iterator = iter(values)
    return lambda: next(iterator)


def test_service_discovery_uses_a_fresh_connection_for_every_sample():
    connections = [_connection(), _connection()]
    factory = MagicMock(side_effect=connections)

    observation = observe_service_discovery(
        factory,
        expected_project_name="perf",
        sample_count=2,
        clock=_clock(10.0, 11.0, 20.0, 22.0),
    )

    assert factory.call_count == 2
    assert observation.functional_verdict is FunctionalVerdict.PASS
    assert [sample.duration_seconds for sample in observation.samples] == [1.0, 2.0]
    assert observation.statistics.sample_count == 2
    for connection in connections:
        connection.authorize.assert_called_once_with()
        connection.identity.get_project.assert_called_once_with("project-id")
        assert connection.endpoint_for.call_args_list == [
            call("compute"),
            call("network"),
            call("image"),
        ]


def test_service_discovery_discards_authorization_token():
    connection = _connection()
    connection.authorize.return_value = "sensitive-token"

    observation = observe_service_discovery(
        lambda: connection,
        expected_project_name="perf",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert "sensitive-token" not in repr(observation)


def test_service_discovery_reports_project_scope_failure():
    connection = _connection()
    connection.identity.get_project.return_value = SimpleNamespace(
        id="project-id", name="wrong-project"
    )

    observation = observe_service_discovery(
        lambda: connection,
        expected_project_name="perf",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert observation.statistics is None
    assert "project name" in observation.error_message


def test_service_discovery_sanitizes_authorization_failure():
    connection = _connection()
    connection.authorize.side_effect = RuntimeError("token=sensitive-token")

    observation = observe_service_discovery(
        lambda: connection,
        expected_project_name="perf",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert observation.error_message == (
        "sample 1: service discovery failed: RuntimeError"
    )
    assert "sensitive-token" not in repr(observation)


def test_service_discovery_reports_missing_current_project_id():
    connection = _connection()
    connection.current_project_id = None

    observation = observe_service_discovery(
        lambda: connection,
        expected_project_name="perf",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert "no project ID" in observation.error_message
    connection.identity.get_project.assert_not_called()


def test_service_discovery_reports_project_id_mismatch():
    connection = _connection()
    connection.identity.get_project.return_value.id = "other-project-id"

    observation = observe_service_discovery(
        lambda: connection,
        expected_project_name="perf",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert "does not match authenticated project ID" in observation.error_message


def test_service_discovery_sanitizes_endpoint_resolution_failure():
    connection = _connection()
    connection.endpoint_for.side_effect = RuntimeError("token=sensitive-token")

    observation = observe_service_discovery(
        lambda: connection,
        expected_project_name="perf",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert "service discovery failed: RuntimeError" in observation.error_message
    assert "sensitive-token" not in repr(observation)


def test_service_discovery_reports_missing_required_endpoint():
    connection = _connection()
    connection.endpoint_for.side_effect = ["compute", None, "image"]

    observation = observe_service_discovery(
        lambda: connection,
        expected_project_name="perf",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert "network" in observation.error_message


def test_connection_construction_is_outside_service_timing():
    factory = MagicMock(side_effect=RuntimeError("configuration unavailable"))
    clock = MagicMock()

    observation = observe_service_discovery(
        factory,
        expected_project_name="perf",
        sample_count=1,
        clock=clock,
    )

    clock.assert_not_called()
    assert observation.samples[0].duration_seconds == 0.0
    assert observation.samples[0].successful is False
    assert observation.samples[0].error_message == (
        "connection setup failed: RuntimeError"
    )
    assert "configuration unavailable" not in repr(observation)


def test_default_service_sample_count_uses_ten_fresh_connections():
    connections = [_connection() for _ in range(10)]
    factory = MagicMock(side_effect=connections)

    observation = observe_service_discovery(
        factory,
        expected_project_name="perf",
        clock=_clock(*range(20)),
    )

    assert factory.call_count == 10
    assert len({id(connection) for connection in connections}) == 10
    assert len(observation.samples) == 10


def test_service_timing_stops_before_validation_and_result_construction():
    connection = _connection()
    connection.identity.get_project.return_value.name = "wrong-project"
    clock = MagicMock(side_effect=[10.0, 12.0])

    observation = observe_service_discovery(
        lambda: connection,
        expected_project_name="perf",
        sample_count=1,
        clock=clock,
    )

    assert observation.samples[0].duration_seconds == 2.0
    assert clock.call_count == 2


def test_boot_image_discovery_retrieves_and_validates_metadata():
    connection = MagicMock()
    connection.image.find_image.return_value = SimpleNamespace(id="image-id")
    connection.image.get_image.return_value = SimpleNamespace(
        id="image-id",
        name="test-image",
        status="active",
        disk_format="qcow2",
        container_format="bare",
        size=1024,
    )

    observation = observe_boot_image(
        connection,
        expected_image_name="test-image",
        sample_count=1,
        clock=_clock(5.0, 6.5),
    )

    connection.image.find_image.assert_called_once_with(
        "test-image", ignore_missing=False
    )
    connection.image.get_image.assert_called_once_with("image-id")
    connection.authorize.assert_not_called()
    assert observation.functional_verdict is FunctionalVerdict.PASS
    assert observation.samples[0].duration_seconds == 1.5


@pytest.mark.parametrize(
    "error",
    [
        exceptions.NotFoundException("image missing"),
        exceptions.DuplicateResource("duplicate image"),
    ],
)
def test_boot_image_discovery_handles_missing_or_ambiguous_image(error):
    connection = MagicMock()
    connection.image.find_image.side_effect = error

    observation = observe_boot_image(
        connection,
        expected_image_name="test-image",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert type(error).__name__ in observation.error_message
    assert str(error) not in observation.error_message


def test_boot_image_discovery_requires_discovered_image_id():
    connection = MagicMock()
    connection.image.find_image.return_value = SimpleNamespace(id=None)

    observation = observe_boot_image(
        connection,
        expected_image_name="test-image",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert "no usable ID" in observation.error_message
    connection.image.get_image.assert_not_called()


def test_boot_image_discovery_sanitizes_metadata_failure():
    connection = MagicMock()
    connection.image.find_image.return_value = SimpleNamespace(id="image-id")
    connection.image.get_image.side_effect = RuntimeError("password=secret")

    observation = observe_boot_image(
        connection,
        expected_image_name="test-image",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert "image discovery failed: RuntimeError" in observation.error_message
    assert "password=secret" not in repr(observation)


@pytest.mark.parametrize(
    "attribute, value, message",
    [
        ("id", "different-id", "image ID"),
        ("name", "other-image", "image name"),
        ("status", "queued", "not ACTIVE"),
        ("disk_format", "", "disk format"),
        ("container_format", None, "container format"),
        ("size", 0, "greater than zero"),
        ("size", float("nan"), "greater than zero"),
        ("size", float("inf"), "greater than zero"),
        ("size", -1, "greater than zero"),
        ("size", True, "greater than zero"),
        ("size", "large", "greater than zero"),
    ],
)
def test_boot_image_discovery_reports_unusable_metadata(attribute, value, message):
    connection = MagicMock()
    connection.image.find_image.return_value = SimpleNamespace(id="image-id")
    metadata = {
        "id": "image-id",
        "name": "test-image",
        "status": "ACTIVE",
        "disk_format": "qcow2",
        "container_format": "bare",
        "size": 1024,
    }
    metadata[attribute] = value
    connection.image.get_image.return_value = SimpleNamespace(**metadata)

    observation = observe_boot_image(
        connection,
        expected_image_name="test-image",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )

    assert observation.functional_verdict is FunctionalVerdict.FAILURE
    assert observation.statistics is None
    assert message in observation.error_message


@pytest.mark.parametrize("sample_count", [0, True, 1.5])
def test_discovery_rejects_invalid_sample_count(sample_count):
    error = TypeError if sample_count is True or sample_count == 1.5 else ValueError
    with pytest.raises(error, match="sample_count"):
        observe_service_discovery(
            _connection,
            expected_project_name="perf",
            sample_count=sample_count,
        )


def test_default_image_sample_count_is_ten():
    connection = MagicMock()
    connection.image.find_image.return_value = SimpleNamespace(id="image-id")
    connection.image.get_image.return_value = SimpleNamespace(
        id="image-id",
        name="test-image",
        status="ACTIVE",
        disk_format="qcow2",
        container_format="bare",
        size=1024,
    )

    observation = observe_boot_image(
        connection,
        expected_image_name="test-image",
        clock=_clock(*range(20)),
    )

    assert len(observation.samples) == 10
    assert connection.image.find_image.call_count == 10
    assert connection.image.get_image.call_count == 10


def test_image_observation_survives_artifact_round_trip():
    connection = MagicMock()
    connection.image.find_image.return_value = SimpleNamespace(id="image-id")
    connection.image.get_image.return_value = SimpleNamespace(
        id="image-id",
        name="test-image",
        status="ACTIVE",
        disk_format="qcow2",
        container_format="bare",
        size=1024,
    )
    observation = observe_boot_image(
        connection,
        expected_image_name="test-image",
        sample_count=1,
        clock=_clock(1.0, 2.0),
    )
    run = RegressionRunResult(
        metadata=RunMetadata(
            "run-1",
            RunRole.CANDIDATE,
            "2026-09-04T10:00:00Z",
            "2026-09-04T10:01:00Z",
            CleanSnapshotStatus.CLEAN,
        ),
        environment=EnvironmentFingerprint(
            "test-cloud", "RegionOne", "test", "main", "candidate"
        ),
        observations=(observation,),
    )

    assert deserialize_run(serialize_run(run)) == run
