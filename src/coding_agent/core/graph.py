"""Immutable task DAG and deterministic queries, independent of execution state."""

from collections.abc import Mapping
from graphlib import CycleError, TopologicalSorter
from typing import Self

from pydantic import model_validator

from .models import DomainModel, TaskSpec
from .state import TaskState


class TaskGraph(DomainModel):
    tasks: tuple[TaskSpec, ...] = ()

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        task_ids = {task.id for task in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise ValueError("duplicate task IDs")
        for task in self.tasks:
            if missing := set(task.depends_on) - task_ids:
                raise ValueError(f"task {task.id} has missing dependencies: {sorted(missing)}")
        try:
            TopologicalSorter({task.id: task.depends_on for task in self.tasks}).prepare()
        except CycleError as error:
            raise ValueError(f"dependency cycle: {error.args[1]}") from error
        return self

    def add_task(self, task: TaskSpec) -> Self:
        """Return a validated new graph; failed additions leave this graph unchanged."""
        return type(self)(tasks=(*self.tasks, task))

    def add_dependency(self, task_id: str, dependency_id: str) -> Self:
        task = self._task(task_id)
        self._task(dependency_id)
        updated = TaskSpec.model_validate(
            task.model_dump() | {"depends_on": (*task.depends_on, dependency_id)}
        )
        return type(self)(
            tasks=tuple(updated if item.id == task_id else item for item in self.tasks)
        )

    def ready_tasks(self, states: Mapping[str, TaskState]) -> tuple[TaskSpec, ...]:
        """Return candidates in plan order, requiring a complete, typed state snapshot."""
        task_ids = {task.id for task in self.tasks}
        if missing := task_ids - states.keys():
            raise ValueError(f"missing task states: {sorted(missing)}")
        if unknown := states.keys() - task_ids:
            raise ValueError(f"unknown task states: {sorted(unknown)}")
        if any(not isinstance(state, TaskState) for state in states.values()):
            raise ValueError("task states must be TaskState values")
        return tuple(
            task
            for task in self.tasks
            if states[task.id] in {TaskState.PENDING, TaskState.READY}
            and all(states[dep] is TaskState.VERIFIED for dep in task.depends_on)
        )

    def ancestors(self, task_id: str) -> frozenset[str]:
        self._task(task_id)
        return self._reachable(task_id, {task.id: task.depends_on for task in self.tasks})

    def descendants(self, task_id: str) -> frozenset[str]:
        self._task(task_id)
        children: dict[str, list[str]] = {task.id: [] for task in self.tasks}
        for task in self.tasks:
            for dependency in task.depends_on:
                children[dependency].append(task.id)
        return self._reachable(task_id, children)

    def _task(self, task_id: str) -> TaskSpec:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise ValueError(f"unknown task ID: {task_id}")

    @staticmethod
    def _reachable(
        task_id: str, edges: Mapping[str, tuple[str, ...] | list[str]]
    ) -> frozenset[str]:
        visited: set[str] = set()
        pending = list(edges[task_id])
        while pending:
            current = pending.pop()
            if current not in visited:
                visited.add(current)
                pending.extend(edges[current])
        return frozenset(visited)
