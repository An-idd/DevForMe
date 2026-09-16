"""Scripted, in-memory workflow and model doubles; never execute or certify code."""

from collections.abc import Callable, Iterable, Iterator
from typing import Literal

from pydantic import BaseModel

from .core import Evidence, TaskSpec
from .core.provider import GenerationSettings, Message, ModelResponse, ToolSchema
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


class FakeModelProvider:
    """Script raw model drafts/failures, including invalid drafts for boundary tests."""

    name = "fake"

    def __init__(
        self,
        results: Iterable[ModelResponse | BaseException],
        *,
        settings: GenerationSettings | None = None,
    ) -> None:
        self.settings = settings or GenerationSettings(model="fake")
        self._script = iter(results)
        self.calls: list[
            tuple[tuple[Message, ...], tuple[ToolSchema, ...], type[BaseModel] | None]
        ] = []

    async def generate(
        self,
        messages: tuple[Message, ...],
        *,
        tools: tuple[ToolSchema, ...] = (),
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        self.calls.append((messages, tools, response_schema))
        return _next(self._script)
