from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math


SCHEMA_VERSION = "1.0"


class RunRole(Enum):
    BASELINE = "baseline"
    CANDIDATE = "candidate"


class CleanSnapshotStatus(Enum):
    CLEAN = "clean"
    NOT_CLEAN = "not_clean"
    UNKNOWN = "unknown"


class FunctionalVerdict(Enum):
    PASS = "pass"
    FAILURE = "functional_failure"


class PerformanceVerdict(Enum):
    PASS = "pass"
    REGRESSION = "performance_regression"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_EVALUATED = "not_evaluated"


class OverallVerdict(Enum):
    PASS = "pass"
    FUNCTIONAL_FAILURE = "functional_failure"
    PERFORMANCE_REGRESSION = "performance_regression"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class ServiceVersion:
    service: str
    version: str

    def __post_init__(self):
        if not self.service or not self.version:
            raise ValueError("service and version must be non-empty")


@dataclass(frozen=True)
class EnvironmentFingerprint:
    cloud: str
    region: str
    platform_release: str
    source_branch: str
    application_release: str
    service_versions: tuple[ServiceVersion, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "service_versions", tuple(self.service_versions))
        identifying_fields = (
            self.cloud,
            self.region,
            self.platform_release,
            self.source_branch,
            self.application_release,
        )
        if any(not value for value in identifying_fields):
            raise ValueError("environment fingerprint fields must be non-empty")
        services = [item.service for item in self.service_versions]
        if len(services) != len(set(services)):
            raise ValueError("service version names must be unique")


@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    role: RunRole
    started_at: str
    completed_at: str
    clean_snapshot: CleanSnapshotStatus

    def __post_init__(self):
        if not self.run_id or not self.started_at or not self.completed_at:
            raise ValueError("run metadata fields must be non-empty")
        started_at = _parse_timestamp(self.started_at, "started_at")
        completed_at = _parse_timestamp(self.completed_at, "completed_at")
        if completed_at < started_at:
            raise ValueError("completed_at must not be earlier than started_at")


@dataclass(frozen=True)
class TimingSample:
    sequence: int
    duration_seconds: float
    successful: bool = True
    error_message: str | None = None

    def __post_init__(self):
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sample sequence must be an integer")
        if self.sequence < 1:
            raise ValueError("sample sequence must be at least 1")
        if not isinstance(self.successful, bool):
            raise TypeError("sample successful must be boolean")
        if isinstance(self.duration_seconds, bool) or not isinstance(
            self.duration_seconds, (int, float)
        ):
            raise TypeError("sample duration must be a number")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError("sample duration must be finite and non-negative")
        if self.successful and self.error_message is not None:
            raise ValueError("successful samples cannot contain an error message")


@dataclass(frozen=True)
class TimingStatistics:
    sample_count: int
    p50_seconds: float
    p95_seconds: float
    minimum_seconds: float
    maximum_seconds: float

    def __post_init__(self):
        if isinstance(self.sample_count, bool) or not isinstance(
            self.sample_count, int
        ):
            raise TypeError("statistics sample_count must be an integer")
        if self.sample_count < 1:
            raise ValueError("statistics require at least one sample")
        values = (
            self.p50_seconds,
            self.p95_seconds,
            self.minimum_seconds,
            self.maximum_seconds,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("statistics values must be finite and non-negative")
        if not (
            self.minimum_seconds
            <= self.p50_seconds
            <= self.p95_seconds
            <= self.maximum_seconds
        ):
            raise ValueError("statistics values must be ordered from minimum to maximum")


@dataclass(frozen=True)
class AssertionResult:
    assertion_id: str
    passed: bool
    message: str | None = None

    def __post_init__(self):
        if not self.assertion_id:
            raise ValueError("assertion_id must be non-empty")
        if not isinstance(self.passed, bool):
            raise TypeError("assertion passed must be boolean")


@dataclass(frozen=True)
class ScenarioObservation:
    scenario_id: str
    target_id: str
    name: str
    functional_verdict: FunctionalVerdict
    assertions: tuple[AssertionResult, ...]
    samples: tuple[TimingSample, ...]
    statistics: TimingStatistics | None
    error_message: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "assertions", tuple(self.assertions))
        object.__setattr__(self, "samples", tuple(self.samples))
        if not self.scenario_id or not self.target_id:
            raise ValueError("scenario_id and target_id must be non-empty")
        if self.functional_verdict is FunctionalVerdict.PASS and any(
            not assertion.passed for assertion in self.assertions
        ):
            raise ValueError("a passing observation cannot contain failed assertions")
        if self.functional_verdict is FunctionalVerdict.PASS and any(
            not sample.successful for sample in self.samples
        ):
            raise ValueError("a passing observation cannot contain failed samples")
        if self.functional_verdict is FunctionalVerdict.FAILURE and not (
            any(not assertion.passed for assertion in self.assertions)
            or any(not sample.successful for sample in self.samples)
            or bool(self.error_message and self.error_message.strip())
        ):
            raise ValueError("a failed observation must contain failure evidence")
        successful_count = sum(sample.successful for sample in self.samples)
        if self.statistics and self.statistics.sample_count != successful_count:
            raise ValueError(
                "statistics sample_count must match successful raw samples"
            )
        sequences = [sample.sequence for sample in self.samples]
        if len(sequences) != len(set(sequences)):
            raise ValueError("sample sequence values must be unique")


@dataclass(frozen=True)
class RegressionRunResult:
    metadata: RunMetadata
    environment: EnvironmentFingerprint
    observations: tuple[ScenarioObservation, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        object.__setattr__(self, "observations", tuple(self.observations))
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema version: {self.schema_version!r}"
            )
        if not self.observations:
            raise ValueError("a regression run requires at least one observation")
        keys = [
            (observation.scenario_id, observation.target_id)
            for observation in self.observations
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("scenario_id and target_id pairs must be unique")

    @property
    def functional_verdict(self) -> FunctionalVerdict:
        if any(
            observation.functional_verdict is FunctionalVerdict.FAILURE
            for observation in self.observations
        ):
            return FunctionalVerdict.FAILURE
        return FunctionalVerdict.PASS


def _parse_timestamp(value: str, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"{field_name} must be a valid ISO-8601 timestamp"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed
