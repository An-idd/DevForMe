from collections.abc import Callable

import pytest
from pydantic import ValidationError

from coding_agent.core import TaskGraph, TaskSpec, TaskState


def ids(tasks: tuple[TaskSpec, ...]) -> list[str]:
    return [task.id for task in tasks]


def test_empty_graph() -> None:
    graph = TaskGraph()
    assert graph.ready_tasks({}) == ()
    assert graph.validate_graph() is graph


def test_independent_tasks_are_ready_in_plan_order(make_task: Callable[..., TaskSpec]) -> None:
    graph = TaskGraph(tasks=(make_task("z"), make_task("a")))
    states = {"a": TaskState.READY, "z": TaskState.PENDING}
    assert ids(graph.ready_tasks(states)) == ["z", "a"]
    assert states == {"a": TaskState.READY, "z": TaskState.PENDING}


def test_diamond_and_out_of_order_construction(make_task: Callable[..., TaskSpec]) -> None:
    graph = TaskGraph(
        tasks=(make_task("d", "b", "c"), make_task("b", "a"), make_task("a"), make_task("c", "a"))
    )
    states = {task.id: TaskState.PENDING for task in graph.tasks}
    assert ids(graph.ready_tasks(states)) == ["a"]
    states["a"] = TaskState.VERIFIED
    assert ids(graph.ready_tasks(states)) == ["b", "c"]
    states["b"] = TaskState.VERIFIED
    assert ids(graph.ready_tasks(states)) == ["c"]
    states["c"] = TaskState.VERIFIED
    assert ids(graph.ready_tasks(states)) == ["d"]
    states["d"] = TaskState.VERIFIED
    assert not graph.ready_tasks(states)
    assert TaskGraph.model_validate_json(graph.model_dump_json()) == graph


@pytest.mark.parametrize("state", [state for state in TaskState if state is not TaskState.VERIFIED])
def test_only_verified_dependencies_unlock_work(
    make_task: Callable[..., TaskSpec], state: TaskState
) -> None:
    graph = TaskGraph(tasks=(make_task("parent"), make_task("child", "parent")))
    assert "child" not in ids(graph.ready_tasks({"parent": state, "child": TaskState.READY}))


@pytest.mark.parametrize(
    "state", [state for state in TaskState if state not in {TaskState.PENDING, TaskState.READY}]
)
def test_active_blocked_and_terminal_tasks_are_not_ready(
    make_task: Callable[..., TaskSpec], state: TaskState
) -> None:
    graph = TaskGraph(tasks=(make_task("a"),))
    assert graph.ready_tasks({"a": state}) == ()


@pytest.mark.parametrize(
    "states",
    [{}, {"a": TaskState.PENDING, "unknown": TaskState.VERIFIED}, {"a": "VERIFIED"}, {"a": None}],
)
def test_ready_queries_reject_incomplete_or_unknown_state(
    make_task: Callable[..., TaskSpec], states: dict[str, TaskState]
) -> None:
    with pytest.raises(ValueError):
        TaskGraph(tasks=(make_task("a"),)).ready_tasks(states)


def test_duplicate_tasks_and_missing_dependencies(make_task: Callable[..., TaskSpec]) -> None:
    with pytest.raises(ValidationError, match="duplicate task IDs"):
        TaskGraph(tasks=(make_task("a"), make_task("a")))
    with pytest.raises(ValidationError, match="missing dependencies"):
        TaskGraph(tasks=(make_task("a", "missing"),))


def test_self_cycle(make_task: Callable[..., TaskSpec]) -> None:
    with pytest.raises(ValidationError, match="itself"):
        make_task("a", "a")


@pytest.mark.parametrize(
    "edges",
    [
        [("a", "b"), ("b", "a")],
        [("a", "b"), ("b", "c"), ("c", "a")],
        [("independent",), ("a", "b"), ("b", "a")],
    ],
)
def test_multi_node_cycles(
    make_task: Callable[..., TaskSpec], edges: list[tuple[str, ...]]
) -> None:
    with pytest.raises(ValidationError, match="dependency cycle"):
        TaskGraph(tasks=tuple(make_task(*edge) for edge in edges))


def test_transitive_queries_and_disconnected_nodes(make_task: Callable[..., TaskSpec]) -> None:
    graph = TaskGraph(
        tasks=(
            make_task("a"),
            make_task("b", "a"),
            make_task("c", "a"),
            make_task("d", "b", "c"),
            make_task("isolated"),
        )
    )
    assert graph.ancestors("d") == {"a", "b", "c"}
    assert graph.descendants("a") == {"b", "c", "d"}
    assert graph.ancestors("a") == graph.descendants("d") == frozenset()
    assert graph.ancestors("isolated") == graph.descendants("isolated") == frozenset()
    with pytest.raises(ValueError, match="unknown task"):
        graph.ancestors("missing")
    with pytest.raises(ValueError, match="unknown task"):
        graph.descendants("missing")


def test_graph_edits_are_validated_and_preserve_original(
    make_task: Callable[..., TaskSpec],
) -> None:
    original = TaskGraph(tasks=(make_task("a"),))
    expanded = original.add_task(make_task("b"))
    linked = expanded.add_dependency("b", "a")
    assert ids(original.tasks) == ["a"]
    assert expanded.tasks[1].depends_on == ()
    assert linked.tasks[1].depends_on == ("a",)
    with pytest.raises(ValidationError, match="duplicate task"):
        original.add_task(make_task("a"))
    with pytest.raises(ValidationError, match="missing dependencies"):
        original.add_task(make_task("c", "missing"))
    with pytest.raises(ValidationError, match="cycle"):
        linked.add_dependency("a", "b")
    with pytest.raises(ValidationError, match="duplicate dependency"):
        linked.add_dependency("b", "a")
    with pytest.raises(ValueError, match="unknown task"):
        linked.add_dependency("b", "missing")
    with pytest.raises(ValueError, match="unknown task"):
        linked.add_dependency("missing", "a")
    with pytest.raises(ValidationError, match="itself"):
        original.add_dependency("a", "a")
    assert linked.tasks[0].depends_on == ()
    assert linked.tasks[1].depends_on == ("a",)


def test_long_chain_uses_no_python_recursion(make_task: Callable[..., TaskSpec]) -> None:
    tasks = (make_task("t0"), *(make_task(f"t{i}", f"t{i - 1}") for i in range(1, 1100)))
    graph = TaskGraph(tasks=tasks)
    assert len(graph.ancestors("t1099")) == len(graph.descendants("t0")) == 1099
    assert ids(graph.ready_tasks({task.id: TaskState.PENDING for task in tasks})) == ["t0"]
