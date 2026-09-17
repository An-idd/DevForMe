"""Immutable workflow inputs and worker results; workers receive no state writer."""

from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, Field, model_validator

from ..graph import TaskGraph
from ..models import (
    DomainModel,
    Evidence,
    EvidenceStatus,
    Identifier,
    NonEmptyStr,
    PositiveInt,
    TaskSpec,
)
from ..state import TaskState


class WorkflowMode(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    STRICT = "strict"


class Revision(DomainModel):
    plan_version: PositiveInt
    context_revision: NonEmptyStr
    workspace_revision: NonEmptyStr


class RunSpec(DomainModel):
    session_id: Identifier
    graph: TaskGraph
    verification_tasks: tuple[TaskSpec, ...] = ()
    revision: Revision
    mode: WorkflowMode = WorkflowMode.STANDARD
    plan_revision: NonEmptyStr | None = None
    executor_revision: NonEmptyStr | None = None
    max_tool_calls: PositiveInt = 30
    max_model_calls: PositiveInt = 31
    pending_milestones: tuple[Identifier, ...] = ()
    max_review_fixes: Annotated[int, Field(strict=True, ge=0)] = 2
    max_total_attempts: PositiveInt = 30
    worker_timeout_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)] = 60.0

    @model_validator(mode="after")
    def unique_verification_tasks(self) -> "RunSpec":
        ids = [task.id for task in (*self.graph.tasks, *self.verification_tasks)]
        if len(ids) != len(set(ids)):
            raise ValueError("verification and business task IDs must be distinct")
        return self

    @property
    def fingerprint(self) -> str:
        """Bind approval to the full ordered graph, revisions, mode and limits."""
        return sha256(self.model_dump_json().encode()).hexdigest()


class PlanApproval(DomainModel):
    """Controller-supplied execution authorization, never an Agent assertion."""

    session_id: Identifier
    plan_fingerprint: NonEmptyStr
    source: NonEmptyStr
    timestamp: AwareDatetime

    def covers(self, spec: RunSpec) -> bool:
        return self.session_id == spec.session_id and self.plan_fingerprint == spec.fingerprint


class ReplanRequest(DomainModel):
    trigger: Literal["scope", "coupling", "uncertainty", "verification", "dependency"]
    reason: NonEmptyStr
    proposed_complexity: Literal["small", "medium", "large"]
    needed_changes: NonEmptyStr


class CoderResult(DomainModel):
    outcome: Literal["implemented", "blocked", "replan_required", "failed", "cancelled"]
    summary: NonEmptyStr
    replan: ReplanRequest | None = None

    @model_validator(mode="after")
    def valid_replan(self) -> "CoderResult":
        if self.replan is not None and self.outcome != "replan_required":
            raise ValueError("replanning details require replan_required")
        return self


class VerificationResult(DomainModel):
    evidence: tuple[Evidence, ...]


class ReviewResult(DomainModel):
    task_id: Identifier
    revision: Revision
    status: EvidenceStatus
    summary: NonEmptyStr
    blocking: tuple[NonEmptyStr, ...] = ()
    major: tuple[NonEmptyStr, ...] = ()
    minor: tuple[NonEmptyStr, ...] = ()
    source: NonEmptyStr
    timestamp: AwareDatetime


class GateIssue(DomainModel):
    code: Literal["missing", "invalid", "stale", "failed", "unavailable"]
    message: NonEmptyStr


class GateResult(DomainModel):
    issues: tuple[GateIssue, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.issues


class TaskOutcome(DomainModel):
    task_id: Identifier
    state: TaskState
    attempts: Annotated[int, Field(strict=True, ge=0)]
    review_fixes: Annotated[int, Field(strict=True, ge=0)]


class FailureDiagnosis(DomainModel):
    category: Literal["code_failure", "pre_existing", "environment", "unknown", "stale", "review"]
    reason: NonEmptyStr
    evidence_ids: tuple[Identifier, ...] = ()
    repair_allowed: bool = False


class WorkflowResult(DomainModel):
    """Task-local outcome. Revision is last observed, not a delivery certification."""

    session_id: Identifier
    outcome: Literal["tasks_verified", "blocked", "replan_required", "failed", "cancelled"]
    tasks: tuple[TaskOutcome, ...]
    evidence: tuple[Evidence, ...]
    reviews: tuple[ReviewResult, ...]
    diagnoses: tuple[FailureDiagnosis, ...] = ()
    replan: ReplanRequest | None = None
    revision: Revision
    reason: NonEmptyStr
    pending_milestones: tuple[Identifier, ...] = ()


class Coder(Protocol):
    async def implement(
        self,
        task: TaskSpec,
        revision: Revision,
        *,
        attempt: int,
        purpose: Literal["implement", "debug", "review_fix"],
        feedback: tuple[str, ...],
    ) -> CoderResult: ...


class Verifier(Protocol):
    async def verify(self, task: TaskSpec, revision: Revision) -> VerificationResult: ...


class Reviewer(Protocol):
    async def review(
        self, task: TaskSpec, revision: Revision, evidence: tuple[Evidence, ...]
    ) -> ReviewResult: ...


class RevisionReader(Protocol):
    def __call__(self) -> Revision: ...
