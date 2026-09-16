"""Validated tool requests, decisions and results; no filesystem or process access."""

from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, StrictBool, model_validator

from .knowledge import InitializationOperation
from .models import Command, DomainModel, Identifier, NonEmptyStr
from .planning import PlanningOperation
from .workspace import WorkspaceOperation

# Paths are operation data: stripping whitespace can redirect an approved action.
ToolPath = Annotated[str, Field(min_length=1)]


class Read(DomainModel):
    kind: Literal["read"] = "read"
    path: ToolPath


class Search(DomainModel):
    kind: Literal["search"] = "search"
    path: ToolPath = "."
    query: NonEmptyStr


class Patch(DomainModel):
    """Replace UTF-8 text after a content hash check; None means create-only."""

    kind: Literal["patch"] = "patch"
    path: ToolPath
    expected_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None
    content: str


class Shell(DomainModel):
    kind: Literal["shell"] = "shell"
    argv: Command
    cwd: ToolPath = "."


class Git(DomainModel):
    kind: Literal["git"] = "git"
    operation: Literal["status", "diff"]


Invocation = Annotated[
    Read
    | Search
    | Patch
    | Shell
    | Git
    | WorkspaceOperation
    | InitializationOperation
    | PlanningOperation,
    Field(discriminator="kind"),
]


class ToolRequest(DomainModel):
    request_id: Identifier
    invocation: Invocation


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PolicyDecision(DomainModel):
    decision: Decision
    reason: NonEmptyStr


class ToolApproval(DomainModel):
    """Controller-created authorization for a concrete operation, not a broad grant."""

    fingerprint: NonEmptyStr
    source: NonEmptyStr
    timestamp: AwareDatetime


class ArtifactRef(DomainModel):
    path: NonEmptyStr
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    size_bytes: Annotated[int, Field(strict=True, ge=0)]
    truncated: StrictBool = False


class ToolResult(DomainModel):
    request_id: Identifier
    request_event_id: Identifier
    decision: Decision
    status: Literal["succeeded", "failed", "denied", "needs_approval", "timed_out", "interrupted"]
    executed: StrictBool
    reason: NonEmptyStr
    exit_code: int | None = None
    output: str = ""
    truncated: StrictBool = False
    artifacts: tuple[ArtifactRef, ...] = ()
    before_sha256: str | None = None
    after_sha256: str | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finish precedes start")
        if self.status in {"denied", "needs_approval"} and self.executed:
            raise ValueError("unapproved operations cannot execute")
        expected = {Decision.DENY: "denied", Decision.ASK: "needs_approval"}
        if self.decision in expected and self.status != expected[self.decision]:
            raise ValueError("rejected or pending decisions cannot record execution outcomes")
        if self.decision is Decision.ALLOW and self.status in {"denied", "needs_approval"}:
            raise ValueError("allowed operations require an execution outcome")
        if not self.executed and self.exit_code is not None:
            raise ValueError("unexecuted operations cannot have process exit codes")
        if self.status == "succeeded" and (not self.executed or self.exit_code not in {None, 0}):
            raise ValueError("success requires execution and a successful exit when applicable")
        return self


def operation_fingerprint(
    request: ToolRequest, *, plan_fingerprint: str, task_id: str, revision_json: str
) -> str:
    # Request IDs identify attempts; a pending approval may be resubmitted with a new ID.
    payload = "\n".join(
        (plan_fingerprint, task_id, revision_json, request.invocation.model_dump_json())
    )
    return sha256(payload.encode()).hexdigest()
