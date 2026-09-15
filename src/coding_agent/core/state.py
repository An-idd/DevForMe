"""Pure transition constraints. Only WorkflowEngine writes task state."""

from enum import StrEnum
from types import MappingProxyType


class TaskState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    IMPLEMENTATION_FINISHED = "IMPLEMENTATION_FINISHED"
    VERIFYING = "VERIFYING"
    REVIEWING = "REVIEWING"
    DEBUGGING = "DEBUGGING"
    FIXING = "FIXING"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_INTERRUPTIONS = frozenset(
    {TaskState.BLOCKED, TaskState.REPLAN_REQUIRED, TaskState.FAILED, TaskState.CANCELLED}
)

ALLOWED_TRANSITIONS = MappingProxyType(
    {
        TaskState.PENDING: frozenset({TaskState.READY}) | _INTERRUPTIONS,
        TaskState.READY: frozenset({TaskState.RUNNING}) | _INTERRUPTIONS,
        TaskState.RUNNING: frozenset({TaskState.IMPLEMENTATION_FINISHED}) | _INTERRUPTIONS,
        TaskState.IMPLEMENTATION_FINISHED: frozenset({TaskState.VERIFYING}) | _INTERRUPTIONS,
        TaskState.VERIFYING: frozenset({TaskState.REVIEWING, TaskState.DEBUGGING}) | _INTERRUPTIONS,
        TaskState.REVIEWING: frozenset({TaskState.VERIFIED, TaskState.FIXING}) | _INTERRUPTIONS,
        TaskState.DEBUGGING: frozenset({TaskState.RUNNING}) | _INTERRUPTIONS,
        TaskState.FIXING: frozenset({TaskState.VERIFYING}) | _INTERRUPTIONS,
        TaskState.BLOCKED: frozenset(
            {TaskState.READY, TaskState.REPLAN_REQUIRED, TaskState.FAILED, TaskState.CANCELLED}
        ),
        TaskState.REPLAN_REQUIRED: frozenset({TaskState.FAILED, TaskState.CANCELLED}),
        TaskState.VERIFIED: frozenset(),
        TaskState.FAILED: frozenset(),
        TaskState.CANCELLED: frozenset(),
    }
)


def validate_transition(current: TaskState, target: TaskState) -> None:
    """Reject illegal edges without mutating state or granting execution permission."""
    if not isinstance(current, TaskState) or not isinstance(target, TaskState):
        raise ValueError("transition endpoints must be TaskState values")
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"illegal task state transition: {current.value} -> {target.value}")
