from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ExecutionStatus(Enum):
    """Functional outcome of a workflow execution."""
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True)
class Environment:
    """
    Deployment environment metadata for workflow execution.

    Carries identifying information needed for comparisons across regions
    and releases. Does not contain credentials or sensitive configuration.
    """
    cloud: str
    region: str
    platform_release: str


@dataclass(frozen=True)
class WorkflowRunResult:
    """
    Complete result of a single workflow execution.

    Represents a workflow execution attempt in a specific environment,
    including functional outcome and total duration. Can be persisted for
    later comparison to detect regressions or performance changes.
    """
    workflow_id: str
    workflow_name: str
    environment: Environment
    status: ExecutionStatus
    duration_seconds: float
    error_message: Optional[str] = None
