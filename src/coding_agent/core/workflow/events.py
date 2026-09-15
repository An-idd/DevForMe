"""Controller-owned lifecycle events; P03 provides a durable EventWriter."""

from typing import Literal, Protocol, Self

from pydantic import AwareDatetime, model_validator

from ..models import DomainModel, Identifier, NonEmptyStr, PositiveInt
from ..state import TaskState, validate_transition
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
    ]
    reason: NonEmptyStr
    previous_state: TaskState | None = None
    state: TaskState | None = None
    evidence_ids: tuple[Identifier, ...] = ()
    source: NonEmptyStr | None = None

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
        return self


class EventWriter(Protocol):
    def write(self, event: WorkflowEvent) -> None:
        """Append or raise; a failed append prohibits further worker dispatch."""
        ...


class EventWriteError(RuntimeError):
    """Recording failed; caller must inspect the writer before any recovery."""
