import json
import subprocess
from unittest.mock import MagicMock

import pytest

from openstack_perf.artifacts import deserialize_run, serialize_run
from openstack_perf.backend_reachability import (
    APPROVED_BASTION,
    BackendTarget,
    observe_backend_reachability,
    require_approved_bastion,
)
from openstack_perf.results import (
    CleanSnapshotStatus,
    EnvironmentFingerprint,
    FunctionalVerdict,
    RegressionRunResult,
    RunMetadata,
    RunRole,
)


def _targets():
    return (
        BackendTarget("backend.database", "MariaDB listener", "10.20.1.10", 3306),
        BackendTarget("backend.apache", "Apache listener", "10.20.1.20", 80),
        BackendTarget("backend.tomcat", "Tomcat listener", "10.20.1.30", 8080),
        BackendTarget("backend.nginx", "nginx listener", "10.20.1.40", 80),
    )


def _completed(results):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(results), stderr="")


def _successful_results():
    return [
        {"target_id": target.target_id, "successful": True, "duration_seconds": index / 1000}
        for index, target in enumerate(_targets(), start=1)
    ]


def _run_result(observations):
    return RegressionRunResult(
        RunMetadata(
            "run-1",
            RunRole.CANDIDATE,
            "2026-09-04T10:00:00Z",
            "2026-09-04T10:01:00Z",
            CleanSnapshotStatus.CLEAN,
        ),
        EnvironmentFingerprint("test", "RegionOne", "release", "main", "app-1"),
        observations,
    )


def test_backend_probe_uses_one_safe_ssh_process_and_no_payload_protocol():
    run = MagicMock(return_value=_completed(_successful_results()))

    observations = observe_backend_reachability(APPROVED_BASTION, _targets(), run=run)

    assert all(item.functional_verdict is FunctionalVerdict.PASS for item in observations)
    assert [item.target_id for item in observations] == [target.target_id for target in _targets()]
    command = run.call_args.args[0]
    assert command[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    assert command[5:] == [APPROVED_BASTION, "python3 -"]
    assert run.call_args.kwargs["shell"] is False
    program = run.call_args.kwargs["input"]
    assert "socket.create_connection" in program
    assert ".close()" in program
    assert "send" not in program
    assert "StrictHostKeyChecking" not in repr(command)


def test_backend_failure_is_one_failed_observation_for_exact_target():
    results = _successful_results()
    results[0] = {
        "target_id": "backend.database",
        "successful": False,
        "duration_seconds": 0.25,
        "error_type": "TimeoutError",
    }

    observations = observe_backend_reachability(
        APPROVED_BASTION, _targets(), run=MagicMock(return_value=_completed(results))
    )

    assert observations[0].functional_verdict is FunctionalVerdict.FAILURE
    assert observations[0].samples[0].duration_seconds == 0.25
    assert observations[0].error_message == "backend connection failed: TimeoutError"
    assert all(item.functional_verdict is FunctionalVerdict.PASS for item in observations[1:])


def test_bastion_failure_is_sanitized_and_propagated_to_every_target_once():
    run = MagicMock(side_effect=subprocess.TimeoutExpired("ssh secret", 10))

    observations = observe_backend_reachability(APPROVED_BASTION, _targets(), run=run)

    run.assert_called_once()
    assert len(observations) == 4
    assert {item.error_message for item in observations} == {
        "backend reachability failed: TimeoutExpired"
    }
    assert "secret" not in repr(observations)
    assert all(item.functional_verdict is FunctionalVerdict.FAILURE for item in observations)


def test_nonzero_ssh_exit_fails_every_target_without_exposing_output():
    error = subprocess.CalledProcessError(
        255,
        ["ssh"],
        output="private stdout",
        stderr="private stderr",
    )
    run = MagicMock(side_effect=error)

    observations = observe_backend_reachability(
        APPROVED_BASTION, _targets(), run=run
    )

    run.assert_called_once()
    assert {item.error_message for item in observations} == {
        "backend reachability failed: CalledProcessError"
    }
    assert "private" not in repr(observations)


@pytest.mark.parametrize(
    "output",
    [
        "not JSON secret",
        "[]",
        json.dumps([{"target_id": "unapproved", "successful": True, "duration_seconds": 1.0}] * 4),
        json.dumps([{"target_id": "backend.database", "successful": "yes", "duration_seconds": 1.0}] * 4),
        json.dumps([{"target_id": "backend.database", "successful": True, "duration_seconds": float("nan")}] * 4),
    ],
)
def test_malformed_remote_output_fails_all_targets_without_retaining_output(output):
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0, output, ""))

    observations = observe_backend_reachability(APPROVED_BASTION, _targets(), run=run)

    assert all(item.functional_verdict is FunctionalVerdict.FAILURE for item in observations)
    assert output not in repr(observations)


@pytest.mark.parametrize(
    "replacement",
    [
        {
            "target_id": "backend.database",
            "successful": True,
            "duration_seconds": 0.1,
            "unexpected": "field",
        },
        {"target_id": "backend.database", "successful": True},
    ],
)
def test_backend_result_rejects_extra_or_missing_fields(replacement):
    results = _successful_results()
    results[0] = replacement

    observations = observe_backend_reachability(
        APPROVED_BASTION,
        _targets(),
        run=MagicMock(return_value=_completed(results)),
    )

    assert all(
        item.functional_verdict is FunctionalVerdict.FAILURE
        for item in observations
    )


def test_backend_result_rejects_duplicate_target_ids():
    results = _successful_results()
    results[1]["target_id"] = results[0]["target_id"]

    observations = observe_backend_reachability(
        APPROVED_BASTION,
        _targets(),
        run=MagicMock(return_value=_completed(results)),
    )

    assert all(
        item.functional_verdict is FunctionalVerdict.FAILURE
        for item in observations
    )


def test_backend_observations_round_trip_through_artifact_schema():
    observations = observe_backend_reachability(
        APPROVED_BASTION,
        _targets(),
        run=MagicMock(return_value=_completed(_successful_results())),
    )

    restored = deserialize_run(serialize_run(_run_result(observations)))

    assert restored.observations == observations


def test_failed_backend_observations_round_trip_through_artifact_schema():
    observations = observe_backend_reachability(
        APPROVED_BASTION,
        _targets(),
        run=MagicMock(side_effect=subprocess.TimeoutExpired("ssh", 10)),
    )

    restored = deserialize_run(serialize_run(_run_result(observations)))

    assert restored.observations == observations


@pytest.mark.parametrize("bastion", ["wiki", "root@172.24.4.20", "wiki@other"])
def test_live_boundary_rejects_unapproved_bastion(bastion):
    with pytest.raises(RuntimeError, match="approved bastion"):
        require_approved_bastion(bastion)


def test_live_boundary_accepts_only_approved_bastion():
    require_approved_bastion(APPROVED_BASTION)


@pytest.mark.parametrize(
    "args,error",
    [
        (("Bad ID", "name", "10.20.1.10", 80), ValueError),
        (("backend.x", "name", "not-an-ip", 80), ValueError),
        (("backend.x", "name", "10.20.1.10", True), TypeError),
        (("backend.x", "name", "10.20.1.10", 0), ValueError),
    ],
)
def test_backend_target_validation(args, error):
    with pytest.raises(error):
        BackendTarget(*args)


def test_duplicate_or_empty_target_sets_are_rejected_without_ssh():
    run = MagicMock()
    with pytest.raises(ValueError, match="at least one"):
        observe_backend_reachability(APPROVED_BASTION, (), run=run)
    target = _targets()[0]
    with pytest.raises(ValueError, match="unique"):
        observe_backend_reachability(APPROVED_BASTION, (target, target), run=run)
    run.assert_not_called()


@pytest.mark.parametrize("timeout", [True, 0, 11, 1.5])
def test_connect_timeout_is_a_bounded_integer(timeout):
    with pytest.raises((TypeError, ValueError)):
        observe_backend_reachability(
            APPROVED_BASTION,
            _targets(),
            connect_timeout_seconds=timeout,
            run=MagicMock(),
        )


def test_oversized_remote_output_is_rejected_without_retaining_it():
    output = "sensitive" * 3000
    observations = observe_backend_reachability(
        APPROVED_BASTION,
        _targets(),
        run=MagicMock(
            return_value=subprocess.CompletedProcess([], 0, output, "")
        ),
    )

    assert all(item.functional_verdict is FunctionalVerdict.FAILURE for item in observations)
    assert output not in repr(observations)
