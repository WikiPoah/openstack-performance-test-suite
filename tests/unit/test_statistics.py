import pytest

from openstack_perf.statistics import calculate_timing_statistics


def test_statistics_preserve_count_and_calculate_linear_percentiles():
    statistics = calculate_timing_statistics([1.0, 2.0, 3.0, 4.0])

    assert statistics.sample_count == 4
    assert statistics.p50_seconds == 2.5
    assert statistics.p95_seconds == pytest.approx(3.85)
    assert statistics.minimum_seconds == 1.0
    assert statistics.maximum_seconds == 4.0


def test_statistics_are_deterministic_for_unsorted_samples():
    first = calculate_timing_statistics([4.0, 1.0, 3.0, 2.0])
    second = calculate_timing_statistics([1.0, 2.0, 3.0, 4.0])

    assert first == second


def test_single_sample_percentiles_equal_the_observation():
    statistics = calculate_timing_statistics([7.5])

    assert statistics.sample_count == 1
    assert statistics.p50_seconds == 7.5
    assert statistics.p95_seconds == 7.5


def test_two_sample_percentiles_use_linear_interpolation():
    statistics = calculate_timing_statistics([10.0, 20.0])

    assert statistics.p50_seconds == 15.0
    assert statistics.p95_seconds == pytest.approx(19.5)


def test_zero_duration_is_valid():
    statistics = calculate_timing_statistics([0.0])

    assert statistics.minimum_seconds == 0.0
    assert statistics.maximum_seconds == 0.0


def test_small_sample_p95_remains_visible_with_its_sample_count():
    statistics = calculate_timing_statistics([10.0, 12.0, 20.0])

    assert statistics.sample_count == 3
    assert statistics.p50_seconds == 12.0
    assert statistics.p95_seconds == pytest.approx(19.2)


def test_statistics_reject_empty_samples():
    with pytest.raises(ValueError, match="at least one"):
        calculate_timing_statistics([])


@pytest.mark.parametrize("samples", [[-1.0], [float("inf")], [float("nan")]])
def test_statistics_reject_invalid_samples(samples):
    with pytest.raises(ValueError, match="finite and non-negative"):
        calculate_timing_statistics(samples)


@pytest.mark.parametrize("samples", [[True], ["1.0"]])
def test_statistics_reject_non_numeric_samples(samples):
    with pytest.raises(TypeError, match="must be numbers"):
        calculate_timing_statistics(samples)
