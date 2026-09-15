"""Explicitly scripted, in-memory P02 doubles. Never execute commands or certify code."""

from collections.abc import Callable, Iterable, Iterator
from typing import Literal

from .core import Evidence, TaskSpec
from .core.workflow import (
    CoderResult,
    ReviewResult,
    Revision,
    VerificationResult,
    WorkflowEvent,
)


def _next[T](script: Iterator[T | BaseException]) -> T:
    try:
        value = next(script)
    except StopIteration as error:
        raise RuntimeError("fake script exhausted; supply an explicit result") from error
    if isinstance(value, BaseException):
        raise value
    return value


class FakeCoder:
    def __init__(
        self,
        results: Iterable[CoderResult | BaseException],
        *,
        on_call: Callable[[TaskSpec, Revision], None] | None = None,
    ) -> None:
        self._script = iter(results)
        self._on_call = on_call
        self.calls: list[tuple[str, int, str, tuple[str, ...]]] = []

    async def implement(
        self,
        task: TaskSpec,
        revision: Revision,
        *,
        attempt: int,
        purpose: Literal["implement", "debug", "review_fix"],
        feedback: tuple[str, ...],
    ) -> CoderResult:
        self.calls.append((task.id, attempt, purpose, feedback))
        if self._on_call is not None:
            self._on_call(task, revision)
        return _next(self._script)


class FakeVerifier:
    def __init__(
        self,
        results: Iterable[VerificationResult | BaseException],
        *,
        on_call: Callable[[TaskSpec, Revision], None] | None = None,
    ) -> None:
        self._script = iter(results)
        self._on_call = on_call
        self.calls: list[tuple[str, Revision]] = []

    async def verify(self, task: TaskSpec, revision: Revision) -> VerificationResult:
        self.calls.append((task.id, revision))
        if self._on_call is not None:
            self._on_call(task, revision)
        return _next(self._script)


class FakeReviewer:
    def __init__(
        self,
        results: Iterable[ReviewResult | BaseException],
        *,
        on_call: Callable[[TaskSpec, Revision], None] | None = None,
    ) -> None:
        self._script = iter(results)
        self._on_call = on_call
        self.calls: list[tuple[str, Revision, tuple[Evidence, ...]]] = []

    async def review(
        self, task: TaskSpec, revision: Revision, evidence: tuple[Evidence, ...]
    ) -> ReviewResult:
        self.calls.append((task.id, revision, evidence))
        if self._on_call is not None:
            self._on_call(task, revision)
        return _next(self._script)


class FakeEventWriter:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self._events: list[WorkflowEvent] = []
        self.fail_at = fail_at

    @property
    def next_sequence(self) -> int:
        return len(self._events) + 1

    @property
    def events(self) -> tuple[WorkflowEvent, ...]:
        return tuple(self._events)

    def write(self, event: WorkflowEvent) -> None:
        if event.sequence == self.fail_at:
            raise OSError("simulated event storage failure")
        if event.sequence != len(self._events) + 1:
            raise ValueError("non-contiguous event sequence")
        self._events.append(event)
