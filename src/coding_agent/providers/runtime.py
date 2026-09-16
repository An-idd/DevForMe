"""Persist each model attempt before network dispatch; never retry implicitly."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel

from ..core.knowledge import InitializationRevision
from ..core.planning import PlanningRevision
from ..core.provider import (
    GenerationSettings,
    Message,
    ModelCallRequest,
    ModelCallResult,
    ModelFailure,
    ModelProvider,
    ModelResponse,
    ProviderError,
    ToolSchema,
    validate_input,
    validate_output,
)
from ..core.tools import ArtifactRef
from ..core.workflow import Revision, WorkflowEvent
from ..session.records import JsonlJournal


class ModelRuntime:
    """Application-facing ModelProvider using the same single writer as ToolRuntime.

    The controller supplies the context revision and, when available, task identity.
    A completed call is a model draft, never task completion or verification evidence.
    """

    def __init__(
        self,
        provider: ModelProvider,
        *,
        journal: JsonlJournal,
        read_revision: Callable[[], Revision | InitializationRevision | PlanningRevision],
        task_id: str | None = None,
    ) -> None:
        self._provider = provider
        self.journal = journal
        self.read_revision = read_revision
        self.task_id = task_id

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def settings(self) -> GenerationSettings:
        return self._provider.settings

    async def generate(
        self,
        messages: tuple[Message, ...],
        *,
        tools: tuple[ToolSchema, ...] = (),
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        validate_input(messages, tools, response_schema)
        request = ModelCallRequest.describe(
            f"model-{uuid4().hex}",
            self._provider,
            messages,
            tools,
            response_schema,
        )
        revision = self.read_revision()
        self.journal.begin_operation(request.request_id)
        try:
            sequence = self.journal.next_sequence
            event = WorkflowEvent(
                event_id=f"{self.journal.session_id}:{sequence}",
                sequence=sequence,
                timestamp=datetime.now(UTC),
                session_id=self.journal.session_id,
                task_id=self.task_id,
                revision=revision,
                kind="model_requested",
                reason="model generation requested",
                model_request=request,
            )
            self.journal.write(event)
            self.journal.check_writable()
            artifacts: tuple[ArtifactRef, ...] = ()
            error: BaseException | None = None
            response = None
            try:
                async with asyncio.timeout(self.settings.timeout_seconds):
                    response = await self._provider.generate(
                        messages,
                        tools=tools,
                        response_schema=response_schema,
                    )
                response = validate_output(response, tools, response_schema)
                seen = {call.call_id for m in messages for call in m.tool_calls}
                if any(call.call_id in seen for call in response.message.tool_calls):
                    raise ValueError("reused tool call ID")
            except asyncio.CancelledError as caught:
                error = caught
                result = ModelCallResult(
                    request_id=request.request_id,
                    request_event_id=event.event_id,
                    status="interrupted",
                )
            except Exception as caught:
                failure = (
                    caught.failure
                    if isinstance(caught, ProviderError)
                    else ModelFailure(
                        code="timeout" if isinstance(caught, TimeoutError) else "invalid_response",
                        retryable=isinstance(caught, TimeoutError),
                        response_id=response.response_id
                        if isinstance(response, ModelResponse)
                        else None,
                        model=response.model if isinstance(response, ModelResponse) else None,
                        usage=response.usage if isinstance(response, ModelResponse) else None,
                    )
                )
                error = ProviderError(failure)
                result = ModelCallResult(
                    request_id=request.request_id,
                    request_event_id=event.event_id,
                    status="failed",
                    failure=failure,
                    response_id=failure.response_id,
                    model=failure.model,
                    usage=failure.usage,
                )
            else:
                # Replay state may contain reasoning; store only the visible draft.
                artifact = self.journal.artifact(
                    "model",
                    response.model_dump_json(
                        exclude={"message": {"continuation"}},
                    ),
                )
                artifacts = (artifact,)
                result = ModelCallResult(
                    request_id=request.request_id,
                    request_event_id=event.event_id,
                    status="completed",
                    response_id=response.response_id,
                    model=response.model,
                    usage=response.usage,
                )
            sequence = self.journal.next_sequence
            self.journal.write(
                WorkflowEvent(
                    event_id=f"{self.journal.session_id}:{sequence}",
                    sequence=sequence,
                    timestamp=datetime.now(UTC),
                    session_id=self.journal.session_id,
                    task_id=self.task_id,
                    revision=revision,
                    kind="model_finished",
                    reason=f"model generation {result.status}",
                    model_result=result,
                    artifacts=artifacts,
                )
            )
            if error is not None:
                raise error from None
            assert response is not None
            return response
        finally:
            self.journal.end_operation()
