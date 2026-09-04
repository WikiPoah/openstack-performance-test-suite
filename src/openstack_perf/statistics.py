import math
from collections.abc import Iterable

from openstack_perf.results import TimingSample, TimingStatistics


def calculate_timing_statistics(samples: Iterable[float]) -> TimingStatistics:
    """Calculate deterministic linearly interpolated timing percentiles."""
    values = list(samples)
    if not values:
        raise ValueError("at least one timing sample is required")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("timing samples must be numbers")
        if not math.isfinite(value) or value < 0:
            raise ValueError("timing samples must be finite and non-negative")

    ordered = sorted(float(value) for value in values)
    return TimingStatistics(
        sample_count=len(ordered),
        p50_seconds=_linear_percentile(ordered, 0.50),
        p95_seconds=_linear_percentile(ordered, 0.95),
        minimum_seconds=ordered[0],
        maximum_seconds=ordered[-1],
    )


def validate_timing_statistics(
    samples: Iterable[TimingSample], statistics: TimingStatistics | None
) -> None:
    """Validate stored statistics against successful raw timing samples."""
    if statistics is None:
        return
    calculated = calculate_timing_statistics(
        sample.duration_seconds for sample in samples if sample.successful
    )
    if statistics != calculated:
        raise ValueError("stored statistics do not match successful raw samples")


def _linear_percentile(ordered: list[float], quantile: float) -> float:
    rank = (len(ordered) - 1) * quantile
    lower_index = math.floor(rank)
    upper_index = math.ceil(rank)
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = rank - lower_index
    return ordered[lower_index] + (
        ordered[upper_index] - ordered[lower_index]
    ) * fraction
