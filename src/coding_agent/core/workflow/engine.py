"""Serial, one-shot workflow controller. P02 adapters execute no real tools."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

from ..models import Evidence, EvidenceStatus, EvidenceType, TaskSpec
from ..state import TaskState
from .contracts import (
    Coder,
    CoderResult,
    GateResult,
    PlanApproval,
    Reviewer,
    ReviewResult,
    Revision,
    RevisionReader,
    RunSpec,
    TaskOutcome,
    VerificationResult,
    Verifier,
    WorkflowResult,
)
from .events import EventWriteError, EventWriter, WorkflowEvent
from .gate import QualityGate
from .policy import effective_mode, review_required
from .scheduling import StateMachine, TaskScheduler

_TERMINAL = {TaskState.VERIFIED, TaskState.FAILED, TaskState.CANCELLED}


class WorkflowEngine:
    def __init__(
        self,
        spec: RunSpec,
        *,
        coder: Coder,
        verifier: Verifier,
        reviewer: Reviewer,
        read_revision: RevisionReader,
        writer: EventWriter,
    ) -> None:
        self._spec = RunSpec.model_validate(spec)
        self._coder = coder
        self._verifier = verifier
        self._reviewer = reviewer
        self._read_revision = read_revision
        self._writer = writer
        self._gate = QualityGate()
        self._machine = StateMachine()
        self._scheduler = TaskScheduler()
        self._states = {task.id: TaskState.PENDING for task in self._spec.graph.tasks}
        self._attempts = dict.fromkeys(self._states, 0)
        self._review_fixes = dict.fromkeys(self._states, 0)
        self._evidence: list[Evidence] = []
        self._reviews: list[ReviewResult] = []
        self._revision = self._spec.revision
        self._started = False
        self._authorized = False
        self._result: WorkflowResult | None = None

    @property
    def states(self) -> Mapping[str, TaskState]:
        return MappingProxyType(dict(self._states))

    @property
    def result(self) -> WorkflowResult | None:
        return self._result

    def _emit(
        self,
        kind: str,
        reason: str,
        task: TaskSpec | None = None,
        *,
        previous_state: TaskState | None = None,
        state: TaskState | None = None,
        evidence_ids: tuple[str, ...] = (),
        source: str | None = None,
    ) -> None:
        sequence = self._writer.next_sequence
        event = WorkflowEvent.model_validate(
            {
                "event_id": f"{self._spec.session_id}:{sequence}",
                "sequence": sequence,
                "timestamp": datetime.now(UTC),
                "session_id": self._spec.session_id,
                "task_id": task.id if task else None,
                "revision": self._revision,
                "kind": kind,
                "reason": reason,
                "previous_state": previous_state,
                "state": state,
                "evidence_ids": evidence_ids,
                "source": source,
            }
        )
        try:
            self._writer.write(event)
        except Exception as error:
            raise EventWriteError(
                "event append failed; stop and reconcile recorded state"
            ) from error

    def _attempt_available(self, task: TaskSpec) -> bool:
        return (
            self._attempts[task.id] < task.max_attempts
            and sum(self._attempts.values()) < self._spec.max_total_attempts
        )

    def _move(
        self, task: TaskSpec, target: TaskState, reason: str, gate: GateResult | None = None
    ) -> None:
        self._machine.validate(
            task,
            self._states,
            target,
            authorized=self._authorized,
            attempt_available=self._attempt_available(task),
            gate=gate,
        )
        self._emit(
            "task_state_changed", reason, task, previous_state=self._states[task.id], state=target
        )
        self._states[task.id] = target

    def _refresh_revision(self) -> Revision:
        self._revision = Revision.model_validate(self._read_revision())
        return self._revision

    def _same_plan(self) -> bool:
        return (
            self._revision.plan_version == self._spec.revision.plan_version
            and self._revision.context_revision == self._spec.revision.context_revision
        )

    def _stop_remaining(self, reason: str, *, cancelled: bool = False) -> None:
        for task in self._spec.graph.tasks:
            if self._states[task.id] in {TaskState.PENDING, TaskState.READY}:
                self._move(task, TaskState.CANCELLED if cancelled else TaskState.BLOCKED, reason)

    def _finish(self, outcome: str, reason: str, *, interrupted: bool = False) -> WorkflowResult:
        self._emit("session_interrupted" if interrupted else "session_finished", reason)
        self._result = WorkflowResult.model_validate(
            {
                "session_id": self._spec.session_id,
                "outcome": outcome,
                "tasks": tuple(
                    TaskOutcome(
                        task_id=task.id,
                        state=self._states[task.id],
                        attempts=self._attempts[task.id],
                        review_fixes=self._review_fixes[task.id],
                    )
                    for task in self._spec.graph.tasks
                ),
                "evidence": tuple(self._evidence),
                "reviews": tuple(self._reviews),
                "revision": self._revision,
                "reason": reason,
            }
        )
        return self._result

    async def run(self, approval: PlanApproval | None = None) -> WorkflowResult:
        """Consume this engine once; resume/replanning require future reconciliation.

        External asyncio cancellation is re-raised after recording a cancelled result.
        Recording failures propagate immediately and never produce a success result.
        """
        if self._started:
            raise RuntimeError("workflow already started; this engine cannot reset or resume")
        self._started = True
        active: TaskSpec | None = None
        try:
            self._emit("session_started", "workflow started")
            if approval is None or not PlanApproval.model_validate(approval).covers(self._spec):
                self._stop_remaining("matching plan approval required")
                return self._finish("blocked", "matching plan approval required")
            self._authorized = True
            self._emit("plan_accepted", self._spec.fingerprint, source=approval.source)
            if self._refresh_revision() != self._spec.revision:
                self._stop_remaining("starting revision changed; plan reconciliation required")
                return self._finish("replan_required", "starting revision changed")
            while active := self._scheduler.next_task(self._spec.graph, self._states):
                self._move(active, TaskState.READY, "dependencies verified and plan authorized")
                reason = await self._execute(active)
                state = self._states[active.id]
                if state is not TaskState.VERIFIED:
                    self._stop_remaining(reason, cancelled=state is TaskState.CANCELLED)
                    return self._finish(state.value.lower(), reason)
            return self._finish(
                "tasks_verified", "task-local gates passed; final integration and delivery not run"
            )
        except EventWriteError:
            raise
        except asyncio.CancelledError:
            if active is not None and self._states[active.id] not in _TERMINAL:
                self._move(active, TaskState.CANCELLED, "workflow cancelled during worker call")
            self._stop_remaining("workflow cancelled", cancelled=True)
            self._finish(
                "cancelled", "workflow cancelled; inspect interrupted worker", interrupted=True
            )
            raise
        except Exception as error:
            # Arbitrary adapter exception messages may contain credentials or raw output.
            reason = f"{type(error).__name__}: adapter failed; reconciliation required"
            self._emit("worker_error", reason, active)
            if active is not None and self._states[active.id] not in _TERMINAL:
                self._move(active, TaskState.BLOCKED, reason)
            self._stop_remaining(reason)
            return self._finish("blocked", reason)

    def _record_evidence(self, records: tuple[Evidence, ...]) -> None:
        known = {record.id for record in self._evidence}
        ids = [record.id for record in records]
        if len(set(ids)) != len(ids) or known.intersection(ids):
            raise ValueError("evidence IDs must be unique across attempts and tasks")
        self._evidence.extend(records)

    def _gate_failure(self, task: TaskSpec, result: GateResult) -> str | None:
        """Only definite check failures can trigger code repair."""
        reason = "; ".join(issue.message for issue in result.issues)
        codes = {issue.code for issue in result.issues}
        if "stale" in codes:
            self._move(task, TaskState.REPLAN_REQUIRED, reason)
        elif codes - {"failed"}:
            self._move(task, TaskState.BLOCKED, reason)
        elif not self._attempt_available(task):
            reason = f"attempt budget exhausted: {reason}"
            self._move(task, TaskState.REPLAN_REQUIRED, reason)
        else:
            return None
        return reason

    def _review_evidence(self, task: TaskSpec, review: ReviewResult) -> tuple[Evidence, ...]:
        if review.task_id != task.id:
            return ()
        checks = {check.id: check for check in task.acceptance.checks}
        records: list[Evidence] = []
        for criterion in task.acceptance.criteria:
            for check_id in criterion.required_check_ids:
                check = checks[check_id]
                if check.evidence_type is not EvidenceType.REVIEW or check.command is not None:
                    continue
                records.append(
                    Evidence(
                        id=f"{self._spec.session_id}:review:{self._writer.next_sequence}:{len(records)}",
                        task_id=task.id,
                        plan_version=review.revision.plan_version,
                        context_revision=review.revision.context_revision,
                        workspace_revision=review.revision.workspace_revision,
                        criterion_id=criterion.id,
                        criterion=criterion.description,
                        check_id=check_id,
                        evidence_type=EvidenceType.REVIEW,
                        command=None,
                        result=review.summary,
                        status=EvidenceStatus.FAILED
                        if review.blocking or review.major
                        else review.status,
                        source=review.source,
                        timestamp=review.timestamp,
                    )
                )
        return tuple(records)

    async def _execute(self, task: TaskSpec) -> str:
        purpose: Literal["implement", "debug", "review_fix"] = "implement"
        feedback: tuple[str, ...] = ()
        while True:
            expected = self._revision
            if self._refresh_revision() != expected or not self._same_plan():
                self._move(
                    task, TaskState.REPLAN_REQUIRED, "revision changed before implementation"
                )
                return "revision changed before implementation"
            if not self._attempt_available(task):
                self._move(task, TaskState.REPLAN_REQUIRED, "attempt budget exhausted")
                return "attempt budget exhausted"
            target = TaskState.FIXING if purpose == "review_fix" else TaskState.RUNNING
            self._move(task, target, purpose)
            attempt = self._attempts[task.id] + 1
            self._emit("coder_started", f"{purpose} attempt {attempt}", task)
            self._attempts[task.id] = attempt
            if purpose == "review_fix":
                self._review_fixes[task.id] += 1
            implemented = CoderResult.model_validate(
                await asyncio.wait_for(
                    self._coder.implement(
                        task, self._revision, attempt=attempt, purpose=purpose, feedback=feedback
                    ),
                    timeout=self._spec.worker_timeout_seconds,
                )
            )
            self._refresh_revision()
            self._emit("coder_finished", implemented.summary, task)
            if not self._same_plan():
                self._move(task, TaskState.REPLAN_REQUIRED, "plan or project knowledge changed")
                return "plan or project knowledge changed"
            if implemented.outcome != "implemented":
                self._move(task, TaskState(implemented.outcome.upper()), implemented.summary)
                return implemented.summary
            if target is TaskState.RUNNING:
                self._move(task, TaskState.IMPLEMENTATION_FINISHED, implemented.summary)
            self._move(task, TaskState.VERIFYING, "implementation requires verification")
            verified_revision = self._revision
            self._emit("verification_started", "execute declared non-review checks", task)
            verification = VerificationResult.model_validate(
                await asyncio.wait_for(
                    self._verifier.verify(task, verified_revision),
                    timeout=self._spec.worker_timeout_seconds,
                )
            )
            self._emit(
                "verification_finished",
                "verification results received",
                task,
                evidence_ids=tuple(record.id for record in verification.evidence),
            )
            self._record_evidence(verification.evidence)
            self._refresh_revision()
            precheck = self._gate.verification(task, self._revision, verification.evidence)
            if self._revision != verified_revision:
                self._emit("gate_evaluated", "verification: False; revision changed", task)
                self._move(task, TaskState.REPLAN_REQUIRED, "revision changed during verification")
                return "revision changed during verification"
            self._emit("gate_evaluated", "verification: " + str(precheck.passed), task)
            if not precheck.passed:
                if stopped := self._gate_failure(task, precheck):
                    return stopped
                self._move(task, TaskState.DEBUGGING, "verification failed; bounded repair")
                purpose = "debug"
                feedback = tuple(issue.message for issue in precheck.issues)
                feedback += tuple(
                    record.result for record in verification.evidence if not record.passed
                )
                continue
            self._move(task, TaskState.REVIEWING, "verification passed; evaluate review policy")
            review = None
            if review_required(task, self._spec.mode):
                self._emit("review_started", effective_mode(task, self._spec.mode).value, task)
                review = ReviewResult.model_validate(
                    await asyncio.wait_for(
                        self._reviewer.review(task, verified_revision, verification.evidence),
                        timeout=self._spec.worker_timeout_seconds,
                    )
                )
                records = self._review_evidence(task, review)
                self._emit(
                    "review_finished",
                    review.summary,
                    task,
                    evidence_ids=tuple(record.id for record in records),
                    source=review.source,
                )
                self._reviews.append(review)
                self._record_evidence(records)
            else:
                self._emit(
                    "review_not_required", "FAST low-risk task without declared review", task
                )
            self._refresh_revision()
            decision = self._gate.evaluate(
                task, self._revision, verification.evidence, review, self._spec.mode
            )
            if self._revision != verified_revision:
                self._emit("gate_evaluated", "completion: False; revision changed", task)
                self._move(task, TaskState.REPLAN_REQUIRED, "revision changed during review")
                return "revision changed during review"
            self._emit("gate_evaluated", "completion: " + str(decision.passed), task)
            if decision.passed:
                self._move(
                    task, TaskState.VERIFIED, "required evidence and review passed", decision
                )
                return "task verified"
            if stopped := self._gate_failure(task, decision):
                return stopped
            if self._review_fixes[task.id] >= self._spec.max_review_fixes:
                self._move(task, TaskState.REPLAN_REQUIRED, "review repair budget exhausted")
                return "review repair budget exhausted"
            purpose = "review_fix"
            feedback = tuple(issue.message for issue in decision.issues)
            if review is not None:
                feedback += (review.summary,) + review.blocking + review.major + review.minor
