from itertools import pairwise

import pytest

from coding_agent.core import TaskState, validate_transition


def test_normal_lifecycle() -> None:
    path = [
        TaskState.PENDING,
        TaskState.READY,
        TaskState.RUNNING,
        TaskState.IMPLEMENTATION_FINISHED,
        TaskState.VERIFYING,
        TaskState.REVIEWING,
        TaskState.VERIFIED,
    ]
    for current, target in pairwise(path):
        assert validate_transition(current, target) is None


def test_verification_and_review_repairs_must_reverify() -> None:
    for current, target in [
        (TaskState.VERIFYING, TaskState.DEBUGGING),
        (TaskState.DEBUGGING, TaskState.RUNNING),
        (TaskState.REVIEWING, TaskState.FIXING),
        (TaskState.FIXING, TaskState.VERIFYING),
        (TaskState.BLOCKED, TaskState.READY),
    ]:
        validate_transition(current, target)
    for current, target in [
        (TaskState.DEBUGGING, TaskState.VERIFIED),
        (TaskState.FIXING, TaskState.REVIEWING),
        (TaskState.FIXING, TaskState.VERIFIED),
    ]:
        with pytest.raises(ValueError, match="illegal"):
            validate_transition(current, target)


@pytest.mark.parametrize(
    "current",
    [
        TaskState.PENDING,
        TaskState.READY,
        TaskState.RUNNING,
        TaskState.IMPLEMENTATION_FINISHED,
        TaskState.VERIFYING,
        TaskState.REVIEWING,
        TaskState.DEBUGGING,
        TaskState.FIXING,
    ],
)
@pytest.mark.parametrize(
    "target", [TaskState.BLOCKED, TaskState.REPLAN_REQUIRED, TaskState.FAILED, TaskState.CANCELLED]
)
def test_active_work_has_explicit_stop_paths(current: TaskState, target: TaskState) -> None:
    validate_transition(current, target)


@pytest.mark.parametrize("current", list(TaskState))
def test_no_self_transitions(current: TaskState) -> None:
    with pytest.raises(ValueError, match="illegal"):
        validate_transition(current, current)


@pytest.mark.parametrize("current", [TaskState.VERIFIED, TaskState.FAILED, TaskState.CANCELLED])
@pytest.mark.parametrize("target", list(TaskState))
def test_terminal_states_cannot_be_reopened(current: TaskState, target: TaskState) -> None:
    with pytest.raises(ValueError, match="illegal"):
        validate_transition(current, target)


@pytest.mark.parametrize(
    "current", [state for state in TaskState if state is not TaskState.REVIEWING]
)
def test_only_reviewing_can_transition_to_verified(current: TaskState) -> None:
    with pytest.raises(ValueError, match="illegal"):
        validate_transition(current, TaskState.VERIFIED)


@pytest.mark.parametrize(
    "target", [TaskState.RUNNING, TaskState.READY, TaskState.PENDING, TaskState.VERIFYING]
)
def test_replan_cannot_silently_restart_same_plan(target: TaskState) -> None:
    with pytest.raises(ValueError, match="illegal"):
        validate_transition(TaskState.REPLAN_REQUIRED, target)


def test_pending_cannot_skip_readiness_and_unknown_states_are_rejected() -> None:
    with pytest.raises(ValueError, match="illegal"):
        validate_transition(TaskState.PENDING, TaskState.RUNNING)
    with pytest.raises(ValueError, match="TaskState"):
        validate_transition("PENDING", TaskState.READY)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="TaskState"):
        validate_transition(TaskState.PENDING, "READY")  # type: ignore[arg-type]
