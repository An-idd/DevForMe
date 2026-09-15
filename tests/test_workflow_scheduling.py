from collections.abc import Callable

import pytest

from coding_agent.core import TaskGraph, TaskSpec, TaskState
from coding_agent.core.workflow import (
    GateIssue,
    GateResult,
    StateMachine,
    TaskScheduler,
    WorkflowMode,
    effective_mode,
    review_required,
)


@pytest.mark.parametrize("requested", list(WorkflowMode))
@pytest.mark.parametrize("risk", ["low", "medium", "high"])
def test_risk_sets_a_floor_on_execution_mode(
    make_task: Callable[..., TaskSpec],
    requested: WorkflowMode,
    risk: str,
) -> None:
    task = TaskSpec.model_validate(make_task("t").model_dump() | {"risk": {"level": risk}})
    actual = effective_mode(task, requested)
    assert actual is (
        WorkflowMode.STRICT
        if risk == "high"
        else WorkflowMode.STANDARD
        if risk == "medium" and requested is WorkflowMode.FAST
        else requested
    )
    assert review_required(task, requested)  # An explicit review always remains required.


def test_scheduler_is_serial_and_dependency_aware(make_task: Callable[..., TaskSpec]) -> None:
    graph = TaskGraph(tasks=(make_task("a"), make_task("b", "a"), make_task("c")))
    scheduler = TaskScheduler()
    states = {"a": TaskState.PENDING, "b": TaskState.PENDING, "c": TaskState.PENDING}
    assert scheduler.next_task(graph, states) == graph.tasks[0]
    states["a"] = TaskState.RUNNING
    assert scheduler.next_task(graph, states) is None
    states["a"] = TaskState.VERIFIED
    assert scheduler.next_task(graph, states) == graph.tasks[1]
    states["a"] = TaskState.FAILED
    assert scheduler.next_task(graph, states) == graph.tasks[2]
    assert scheduler.next_task(TaskGraph(), {}) is None


@pytest.mark.parametrize(
    "target,current",
    [
        (TaskState.READY, TaskState.PENDING),
        (TaskState.RUNNING, TaskState.READY),
        (TaskState.FIXING, TaskState.REVIEWING),
    ],
)
def test_transition_requires_dependencies_and_authorization(
    make_task: Callable[..., TaskSpec],
    target: TaskState,
    current: TaskState,
) -> None:
    task = make_task("b", "a")
    states = {"a": TaskState.VERIFIED, "b": current}
    machine = StateMachine()
    with pytest.raises(ValueError, match="authorized"):
        machine.validate(task, states, target, attempt_available=True)
    with pytest.raises(ValueError, match="dependencies"):
        machine.validate(
            task, states | {"a": TaskState.FAILED}, target, authorized=True, attempt_available=True
        )
    machine.validate(task, states, target, authorized=True, attempt_available=True)


def test_transition_checks_budget_concurrency_and_gate(make_task: Callable[..., TaskSpec]) -> None:
    task = make_task("t")
    machine = StateMachine()
    with pytest.raises(ValueError, match="budget"):
        machine.validate(task, {"t": TaskState.READY}, TaskState.RUNNING, authorized=True)
    with pytest.raises(ValueError, match="another task"):
        machine.validate(
            task,
            {"t": TaskState.READY, "other": TaskState.VERIFYING},
            TaskState.RUNNING,
            authorized=True,
            attempt_available=True,
        )
    for gate in [None, GateResult(issues=(GateIssue(code="missing", message="no tests"),))]:
        with pytest.raises(ValueError, match="QualityGate"):
            machine.validate(task, {"t": TaskState.REVIEWING}, TaskState.VERIFIED, gate=gate)
    states = {"t": TaskState.REVIEWING}
    machine.validate(task, states, TaskState.VERIFIED, gate=GateResult())
    assert states["t"] is TaskState.REVIEWING
