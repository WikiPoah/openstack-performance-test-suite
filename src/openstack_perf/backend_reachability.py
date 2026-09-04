from collections.abc import Callable, Iterable
from dataclasses import dataclass
import ipaddress
import json
import math
import re
import subprocess

from openstack_perf.product_http import product_failure_message
from openstack_perf.results import (
    AssertionResult,
    FunctionalVerdict,
    ScenarioObservation,
    TimingSample,
)
from openstack_perf.statistics import calculate_timing_statistics


APPROVED_BASTION = "wiki@172.24.4.20"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
MAX_RESULT_BYTES = 16 * 1024


@dataclass(frozen=True)
class BackendTarget:
    target_id: str
    name: str
    host: str
    port: int

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", self.target_id):
            raise ValueError("target_id must be a stable lowercase identifier")
        if not self.name:
            raise ValueError("target name must be non-empty")
        ipaddress.ip_address(self.host)
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise TypeError("target port must be an integer")
        if not 1 <= self.port <= 65535:
            raise ValueError("target port must be between 1 and 65535")


def require_approved_bastion(bastion: str) -> None:
    if bastion != APPROVED_BASTION:
        raise RuntimeError("The product scenario requires the approved bastion")


def observe_backend_reachability(
    bastion: str,
    targets: Iterable[BackendTarget],
    *,
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[ScenarioObservation, ...]:
    """Probe backend TCP listeners once through a single SSH session."""
    target_tuple = tuple(targets)
    if not target_tuple:
        raise ValueError("at least one backend target is required")
    if len({target.target_id for target in target_tuple}) != len(target_tuple):
        raise ValueError("backend target IDs must be unique")
    if isinstance(connect_timeout_seconds, bool) or not isinstance(
        connect_timeout_seconds, int
    ):
        raise TypeError("connect_timeout_seconds must be an integer")
    if connect_timeout_seconds < 1:
        raise ValueError("connect_timeout_seconds must be at least 1")
    if connect_timeout_seconds > DEFAULT_CONNECT_TIMEOUT_SECONDS:
        raise ValueError("connect_timeout_seconds must be at most 10")

    program = _remote_program(target_tuple, connect_timeout_seconds)
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={connect_timeout_seconds}",
        bastion,
        "python3 -",
    ]
    try:
        completed = run(
            command,
            input=program,
            text=True,
            capture_output=True,
            timeout=(
                connect_timeout_seconds
                + len(target_tuple) * connect_timeout_seconds
                + 5
            ),
            check=True,
            shell=False,
        )
        results = _parse_results(completed.stdout, target_tuple)
    except Exception as exc:
        error = product_failure_message("backend reachability", exc)
        return tuple(
            _backend_observation(target, None, error) for target in target_tuple
        )
    return tuple(
        _backend_observation(
            target,
            results[target.target_id],
            None,
        )
        for target in target_tuple
    )


def _remote_program(targets, timeout):
    payload = json.dumps(
        [
            {
                "target_id": target.target_id,
                "host": target.host,
                "port": target.port,
            }
            for target in targets
        ],
        separators=(",", ":"),
    )
    return f'''import json
import socket
import time

targets = json.loads({payload!r})
results = []
for target in targets:
    start = time.perf_counter()
    try:
        connection = socket.create_connection((target["host"], target["port"]), timeout={timeout})
        connection.close()
    except Exception as error:
        results.append({{"target_id": target["target_id"], "successful": False, "duration_seconds": max(0.0, time.perf_counter() - start), "error_type": type(error).__name__}})
    else:
        results.append({{"target_id": target["target_id"], "successful": True, "duration_seconds": max(0.0, time.perf_counter() - start)}})
print(json.dumps(results, separators=(",", ":")))
'''


def _parse_results(output, targets):
    if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("backend result output is invalid")
    document = json.loads(output)
    if not isinstance(document, list) or len(document) != len(targets):
        raise ValueError("backend result count is invalid")
    expected_ids = {target.target_id for target in targets}
    parsed = {}
    for item in document:
        if not isinstance(item, dict):
            raise ValueError("backend result schema is invalid")
        target_id = item.get("target_id")
        successful = item.get("successful")
        duration = item.get("duration_seconds")
        if target_id not in expected_ids or target_id in parsed:
            raise ValueError("backend result target is invalid")
        if not isinstance(successful, bool):
            raise TypeError("backend result successful must be boolean")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
        ):
            raise ValueError("backend result duration is invalid")
        required_keys = {"target_id", "successful", "duration_seconds"}
        if successful:
            if set(item) != required_keys:
                raise ValueError("backend result schema is invalid")
        else:
            if set(item) != required_keys | {"error_type"}:
                raise ValueError("backend result schema is invalid")
            error_type = item.get("error_type")
            if not isinstance(error_type, str) or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]*", error_type
            ):
                raise ValueError("backend result error type is invalid")
        parsed[target_id] = item
    return parsed


def _backend_observation(target, result, shared_error):
    if result is None:
        successful = False
        duration = 0.0
        error = shared_error
    else:
        successful = result["successful"]
        duration = float(result["duration_seconds"])
        error = (
            None
            if successful
            else f"backend connection failed: {result['error_type']}"
        )
    sample = TimingSample(1, duration, successful, error)
    return ScenarioObservation(
        scenario_id="product.backend_reachability",
        target_id=target.target_id,
        name=target.name,
        functional_verdict=(
            FunctionalVerdict.PASS if successful else FunctionalVerdict.FAILURE
        ),
        assertions=(
            AssertionResult(f"{target.target_id}.reachable", successful, error),
        ),
        samples=(sample,),
        statistics=calculate_timing_statistics((duration,)) if successful else None,
        error_message=error,
    )
