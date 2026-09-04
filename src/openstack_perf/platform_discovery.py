from collections.abc import Callable
import math
import time

import openstack

from openstack_perf.platform_errors import (
    PlatformValidationError,
    platform_failure_message,
)
from openstack_perf.results import (
    AssertionResult,
    FunctionalVerdict,
    ScenarioObservation,
    TimingSample,
)
from openstack_perf.statistics import calculate_timing_statistics


def observe_service_discovery(
    connection_factory: Callable[[], openstack.connection.Connection],
    *,
    expected_project_name: str,
    sample_count: int = 10,
    clock: Callable[[], float] = time.perf_counter,
) -> ScenarioObservation:
    """Measure fresh-session authentication, project scope, and endpoints."""
    _require_sample_count(sample_count)
    samples = []

    for sequence in range(1, sample_count + 1):
        try:
            connection = connection_factory()
        except Exception as exc:
            samples.append(
                TimingSample(
                    sequence,
                    0.0,
                    successful=False,
                    error_message=platform_failure_message(
                        "connection setup", exc
                    ),
                )
            )
            continue

        start = clock()
        stop = None
        try:
            connection.authorize()
            project_id = connection.current_project_id
            if not project_id:
                raise PlatformValidationError(
                    "authenticated session has no project ID"
                )
            project = connection.identity.get_project(project_id)
            endpoints = {
                service_type: connection.endpoint_for(service_type)
                for service_type in ("compute", "network", "image")
            }
            stop = clock()
            validate_project_scope(project_id, project, expected_project_name)
            missing_services = [
                name for name, endpoint in endpoints.items() if not endpoint
            ]
            if missing_services:
                raise PlatformValidationError(
                    "required service endpoints are missing: "
                    + ", ".join(missing_services)
                )
        except Exception as exc:
            if stop is None:
                stop = clock()
            samples.append(
                TimingSample(
                    sequence,
                    stop - start,
                    successful=False,
                    error_message=platform_failure_message(
                        "service discovery", exc
                    ),
                )
            )
        else:
            samples.append(TimingSample(sequence, stop - start))

    return _observation_from_samples(
        scenario_id="identity.service_discovery",
        target_id=expected_project_name,
        name="Required service discovery",
        samples=tuple(samples),
    )


def observe_boot_image(
    connection: openstack.connection.Connection,
    *,
    expected_image_name: str,
    sample_count: int = 10,
    clock: Callable[[], float] = time.perf_counter,
) -> ScenarioObservation:
    """Measure discovery and metadata retrieval for an expected boot image."""
    _require_sample_count(sample_count)
    samples = []

    for sequence in range(1, sample_count + 1):
        start = clock()
        stop = None
        try:
            discovered = connection.image.find_image(
                expected_image_name, ignore_missing=False
            )
            discovered_id = getattr(discovered, "id", None)
            if not discovered_id:
                raise PlatformValidationError(
                    "discovered image has no usable ID"
                )
            image = connection.image.get_image(discovered_id)
            stop = clock()
            _validate_image(image, discovered_id, expected_image_name)
        except Exception as exc:
            if stop is None:
                stop = clock()
            samples.append(
                TimingSample(
                    sequence,
                    stop - start,
                    successful=False,
                    error_message=platform_failure_message(
                        "image discovery", exc
                    ),
                )
            )
        else:
            samples.append(TimingSample(sequence, stop - start))

    return _observation_from_samples(
        scenario_id="image.boot_discovery",
        target_id=expected_image_name,
        name="Boot image discovery",
        samples=tuple(samples),
    )


def validate_project_scope(project_id, project, expected_project_name: str) -> None:
    if not project_id:
        raise PlatformValidationError("authenticated session has no project ID")
    if getattr(project, "id", None) != project_id:
        raise PlatformValidationError(
            "retrieved project does not match authenticated project ID"
        )
    if getattr(project, "name", None) != expected_project_name:
        raise PlatformValidationError(
            "authenticated project name does not match expected project"
        )


def _validate_image(image, discovered_id: str, expected_name: str) -> None:
    if getattr(image, "id", None) != discovered_id:
        raise PlatformValidationError(
            "retrieved image ID does not match discovered image ID"
        )
    if getattr(image, "name", None) != expected_name:
        raise PlatformValidationError(
            "retrieved image name does not match expected image"
        )
    status = getattr(image, "status", None)
    if not isinstance(status, str) or status.lower() != "active":
        raise PlatformValidationError("expected image is not ACTIVE")
    if not getattr(image, "disk_format", None):
        raise PlatformValidationError("expected image has no disk format")
    if not getattr(image, "container_format", None):
        raise PlatformValidationError("expected image has no container format")
    size = getattr(image, "size", None)
    if (
        isinstance(size, bool)
        or not isinstance(size, (int, float))
        or not math.isfinite(size)
        or size <= 0
    ):
        raise PlatformValidationError(
            "expected image size must be numeric and greater than zero"
        )


def _observation_from_samples(
    *, scenario_id: str, target_id: str, name: str, samples: tuple[TimingSample, ...]
) -> ScenarioObservation:
    assertions = tuple(
        AssertionResult(
            assertion_id=f"{scenario_id}.sample.{sample.sequence}",
            passed=sample.successful,
            message=sample.error_message,
        )
        for sample in samples
    )
    successful_durations = tuple(
        sample.duration_seconds for sample in samples if sample.successful
    )
    errors = tuple(
        f"sample {sample.sequence}: {sample.error_message}"
        for sample in samples
        if not sample.successful
    )
    return ScenarioObservation(
        scenario_id=scenario_id,
        target_id=target_id,
        name=name,
        functional_verdict=(
            FunctionalVerdict.FAILURE
            if errors
            else FunctionalVerdict.PASS
        ),
        assertions=assertions,
        samples=samples,
        statistics=(
            calculate_timing_statistics(successful_durations)
            if successful_durations
            else None
        ),
        error_message="; ".join(errors) or None,
    )


def _require_sample_count(sample_count: int) -> None:
    if isinstance(sample_count, bool) or not isinstance(sample_count, int):
        raise TypeError("sample_count must be an integer")
    if sample_count < 1:
        raise ValueError("sample_count must be at least 1")
