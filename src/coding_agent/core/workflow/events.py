"""Controller-owned events sharing the writer's session sequence with tool records."""

from typing import Literal, Protocol, Self

from pydantic import AwareDatetime, model_validator

from ..models import DomainModel, Identifier, NonEmptyStr, PositiveInt
from ..state import TaskState, validate_transition
from ..tools import ArtifactRef, ToolRequest, ToolResult
from .contracts import Revision


class WorkflowEvent(DomainModel):
    event_id: Identifier
    sequence: PositiveInt
    timestamp: AwareDatetime
    session_id: Identifier
    task_id: Identifier | None
    revision: Revision
    kind: Literal[
        "session_started",
        "plan_accepted",
        "task_state_changed",
        "coder_started",
        "coder_finished",
        "verification_started",
        "verification_finished",
        "review_started",
        "review_finished",
        "review_not_required",
        "gate_evaluated",
        "worker_error",
        "session_finished",
        "session_interrupted",
        "plan_registered",
        "tool_requested",
        "tool_finished",
    ]
    reason: NonEmptyStr
    previous_state: TaskState | None = None
    state: TaskState | None = None
    evidence_ids: tuple[Identifier, ...] = ()
    source: NonEmptyStr | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    tool_request: ToolRequest | None = None
    tool_result: ToolResult | None = None

    @model_validator(mode="after")
    def validate_state_event(self) -> Self:
        if self.kind == "task_state_changed":
            if self.task_id is None or self.previous_state is None or self.state is None:
                raise ValueError("state events require task identity and both transition endpoints")
            validate_transition(self.previous_state, self.state)
        elif self.previous_state is not None or self.state is not None:
            raise ValueError("only state-change events may contain transition endpoints")
        if (
            self.kind.startswith(("coder_", "verification_", "review_"))
            or self.kind == "gate_evaluated"
        ):
            if self.task_id is None:
                raise ValueError("worker and gate events require task identity")
        if self.kind in {"plan_accepted", "review_finished"} and self.source is None:
            raise ValueError("authorization and review results require source references")
        if (self.tool_request is not None) != (self.kind == "tool_requested"):
            raise ValueError("request payload belongs only to tool_requested")
        if (self.tool_result is not None) != (self.kind == "tool_finished"):
            raise ValueError("result payload belongs only to tool_finished")
        if self.kind in {"tool_requested", "tool_finished"} and self.task_id is None:
            raise ValueError("tool events require task identity")
        return self


class EventWriter(Protocol):
    @property
    def next_sequence(self) -> int:
        """The session writer is the sole allocator shared by workflows and tools."""
        ...

    def write(self, event: WorkflowEvent) -> None:
        """Append or raise; a failed append prohibits further worker dispatch."""
        ...


class EventWriteError(RuntimeError):
    """Recording failed; caller must inspect the writer before any recovery."""
