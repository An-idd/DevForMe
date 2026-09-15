"""Pure scheduling and transition preconditions; no writable task state."""

from collections.abc import Mapping

from ..graph import TaskGraph
from ..models import TaskSpec
from ..state import TaskState, validate_transition
from .contracts import GateResult

ACTIVE_STATES = frozenset(
    {
        TaskState.RUNNING,
        TaskState.IMPLEMENTATION_FINISHED,
        TaskState.VERIFYING,
        TaskState.REVIEWING,
        TaskState.DEBUGGING,
        TaskState.FIXING,
    }
)


class TaskScheduler:
    def next_task(self, graph: TaskGraph, states: Mapping[str, TaskState]) -> TaskSpec | None:
        candidates = graph.ready_tasks(states)
        if any(state in ACTIVE_STATES for state in states.values()):
            return None
        return next(iter(candidates), None)


class StateMachine:
    def validate(
        self,
        task: TaskSpec,
        states: Mapping[str, TaskState],
        target: TaskState,
        *,
        authorized: bool = False,
        attempt_available: bool = False,
        gate: GateResult | None = None,
    ) -> None:
        validate_transition(states[task.id], target)
        if target in {TaskState.READY, TaskState.RUNNING, TaskState.FIXING}:
            if any(
                states.get(dependency) is not TaskState.VERIFIED for dependency in task.depends_on
            ):
                raise ValueError("dependencies are not VERIFIED")
            if not authorized:
                raise ValueError("plan execution is not authorized")
        if target in {TaskState.RUNNING, TaskState.FIXING}:
            if not attempt_available:
                raise ValueError("attempt budget exhausted")
            if any(key != task.id and state in ACTIVE_STATES for key, state in states.items()):
                raise ValueError("another task is active")
        if target is TaskState.VERIFIED and (gate is None or not gate.passed):
            raise ValueError("VERIFIED requires a passing QualityGate")
