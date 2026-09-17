"""One approved, retained Worktree execution; verification and recovery stay explicit."""

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from ..context.coder import build_context
from ..core.knowledge import KnowledgeSnapshot
from ..core.models import DomainModel, Evidence, EvidenceStatus, ScopePolicy, TaskSpec
from ..core.planning import PlanVersion
from ..core.provider import ModelProvider, ProviderError
from ..core.replanning import SessionUsage
from ..core.tools import Shell
from ..core.verification import VerificationSettings
from ..core.workflow import (
    CoderResult,
    PlanApproval,
    QualityGate,
    ReviewResult,
    Revision,
    RunSpec,
    VerificationResult,
    WorkflowEngine,
    WorkflowResult,
)
from ..core.workflow.contracts import FailureDiagnosis
from ..core.workflow.policy import review_required
from ..core.workspace import WorkspaceOperation
from ..executors.codex import CodexCoder, CodexSettings
from ..runtime._snapshots import capture
from ..runtime.workspace import Workspace
from ..session.records import Sanitizer, inspect_journal
from ..tools.execution import ExecutionRuntime
from ..tools.initialization import EXCLUDED
from ..tools.review import ReviewRunner, reviewer_identity
from ..tools.runtime import ToolRuntime
from ..tools.verification import VerificationRunner
from .initialization import initialize
from .planning import inspect_plan, run_spec
from .replanning import replan


class ExecutionAuthorization(DomainModel):
    approval: PlanApproval
    executor: CodexSettings
    workspace: str
    reviewer_identity: str


class ImplementationResult(DomainModel):
    task_id: str
    draft: CoderResult
    before: Revision
    after: Revision
    modified_files: tuple[str, ...]
    executed_commands: tuple[Shell, ...]
    tool_request_ids: tuple[str, ...]


class RunResult(DomainModel):
    status: Literal[
        "approval_required",
        "blocked",
        "stale",
        "failed",
        "cancelled",
        "replan_required",
        "tasks_verified",
    ]
    reason: str
    fingerprint: str | None = None
    spec: RunSpec | None = None
    session_id: str | None = None
    journal: str | None = None
    worktree: str | None = None
    workflow: WorkflowResult | None = None
    requirement_complete: Literal[False] = False
    baseline: VerificationResult | None = None
    final_verification: VerificationResult | None = None
    final_reviews: tuple[ReviewResult, ...] = ()
    usage: SessionUsage | None = None
    plan_revision: str | None = None


class _UnavailableStages:
    async def review(
        self, task: TaskSpec, revision: Revision, evidence: tuple[Evidence, ...]
    ) -> ReviewResult:
        return ReviewResult(
            task_id=task.id,
            revision=revision,
            status=EvidenceStatus.UNAVAILABLE,
            summary="Independent reviewer is not configured",
            source="controller:unavailable-reviewer",
            timestamp=datetime.now(UTC),
        )


class _SessionCoder:
    def __init__(
        self,
        owner: ExecutionRuntime,
        plan: PlanVersion,
        knowledge: KnowledgeSnapshot,
        spec: RunSpec,
        approval: PlanApproval,
        workspace: Workspace,
        config: CodexSettings,
        guard: Callable[[], None],
    ) -> None:
        self.owner, self.plan, self.knowledge = owner, plan, knowledge
        self.spec, self.approval, self.workspace, self.config = spec, approval, workspace, config
        self.guard = guard

    async def implement(
        self,
        task: TaskSpec,
        revision: Revision,
        *,
        attempt: int,
        purpose: Literal["implement", "debug", "review_fix"],
        feedback: tuple[str, ...],
    ) -> CoderResult:
        runtime = ToolRuntime(
            self.spec,
            task.id,
            root=self.workspace.path,
            journal=self.owner.journal,
            approval=self.approval,
            workspace=self.workspace,
            guard=self.guard,
        )
        context = await build_context(runtime, self.plan, self.knowledge)
        self.owner.record("context_built", context, revision, task_id=task.id)
        before = self.workspace.snapshot()
        sequence = self.owner.journal.next_sequence
        result = await CodexCoder(self.config, runtime, context=context).implement(
            task,
            revision,
            attempt=attempt,
            purpose=purpose,
            feedback=feedback,
        )
        result = CoderResult.model_validate(
            self.owner.sanitizer.tree(result.model_dump(mode="json"))
        )
        if result.outcome == "replan_required" and result.replan is None:
            result = CoderResult(
                outcome="blocked", summary="structured replanning details required"
            )
        after = self.workspace.snapshot()
        original, current = {f.path: f for f in before.files}, {f.path: f for f in after.files}
        view = inspect_journal(self.owner.journal.directory / "events.jsonl")
        requests = {
            e.event_id: e.tool_request
            for e in view.events
            if e.sequence >= sequence and e.tool_request is not None
        }
        executed = tuple(
            requests[e.tool_result.request_event_id]
            for e in view.events
            if e.tool_result is not None
            and e.tool_result.executed
            and e.tool_result.request_event_id in requests
        )
        report = ImplementationResult(
            task_id=task.id,
            draft=result,
            before=revision,
            after=runtime.read_revision(),
            modified_files=tuple(
                sorted(
                    p for p in original.keys() | current.keys() if original.get(p) != current.get(p)
                )
            ),
            executed_commands=tuple(
                r.invocation for r in executed if isinstance(r.invocation, Shell)
            ),
            tool_request_ids=tuple(r.request_id for r in executed),
        )
        self.owner.record("implementation_recorded", report, report.after, task_id=task.id)
        return result


def execution_spec(
    plan: PlanVersion,
    config: CodexSettings,
    directory: Path,
    verification: VerificationSettings | None = None,
    reviewer: ModelProvider | None = None,
    replanner: ModelProvider | None = None,
) -> RunSpec:
    verification = verification or VerificationSettings()
    identity = (
        config.model_dump_json()
        + "\n"
        + str(directory)
        + verification.model_dump_json()
        + reviewer_identity(reviewer)
    )
    if replanner is not None:
        identity += "\nreplanner:" + reviewer_identity(replanner)
    global_task = TaskSpec(
        id="requirement-verification",
        title="Requirement verification",
        goal=plan.original_request,
        scope=ScopePolicy(forbidden=plan.settings.scope.forbidden),
        requirements=plan.draft.requirement.functional_requirements,
        acceptance=plan.draft.acceptance,
        risk=plan.draft.requirement.risk,
        permissions=plan.settings.permissions,
    )
    return RunSpec.model_validate(
        run_spec(plan).model_dump()
        | {
            "session_id": "run-" + plan.plan_id,
            "executor_revision": sha256(identity.encode()).hexdigest(),
            "verification_tasks": (global_task,),
        }
    )


async def run(
    root: Path,
    *,
    config: CodexSettings,
    workspace: Path,
    approve: str | None = None,
    secrets: tuple[str, ...] = (),
    verification: VerificationSettings | None = None,
    reviewer: ModelProvider | None = None,
    replanner: ModelProvider | None = None,
) -> RunResult:
    root = root.resolve(strict=True)
    try:
        inspection = inspect_plan(root)
    except ValueError as error:
        if str(error) != "no saved plan; run agent plan first":
            raise
        initialized = await initialize(root, secrets=secrets)
        return RunResult(
            status="blocked",
            reason="Initialize/refresh knowledge and save a reviewed plan with agent plan first.",
            journal=initialized.journal,
        )
    plan = inspection.plan
    if inspection.status != "proposed":
        return RunResult(status=inspection.status, reason="; ".join(inspection.blockers))
    graph = run_spec(plan).graph
    if any(t.scope.forbidden != plan.settings.scope.forbidden for t in graph.tasks):
        return RunResult(
            status="blocked",
            reason="One workspace requires identical task read restrictions; revise the plan.",
        )
    config = CodexSettings.model_validate(config)
    workspace = workspace.absolute()
    for path in (config.executable, config.home):
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError("Codex executable/profile must be absolute and canonical")
        if path.is_relative_to(root) or path.is_relative_to(workspace):
            raise ValueError("Codex executable/profile must be outside source and workspace")
    verification = VerificationSettings.model_validate(verification or {})
    for path in verification.runtime_roots:
        if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_dir():
            raise ValueError("verification runtime roots must be canonical absolute directories")
        if any(
            path.is_relative_to(p) or p.is_relative_to(path) for p in (root, workspace, config.home)
        ):
            raise ValueError("verification runtime roots must be separate from project and profile")
    managed = Workspace(root, workspace, plan.settings.scope, excluded=EXCLUDED)
    spec = execution_spec(plan, config, workspace, verification, reviewer, replanner)
    if approve != spec.fingerprint:
        return RunResult(
            status="approval_required",
            reason="Review the saved plan; execute with --approve " + spec.fingerprint,
            fingerprint=spec.fingerprint,
            spec=spec,
            session_id=spec.session_id,
        )
    original_plan = plan
    replanner_identity = reviewer_identity(replanner)
    sanitizer = Sanitizer(secrets)
    with ExecutionRuntime(root, plan, sanitizer=sanitizer) as owner:
        state = owner.load()
        knowledge = owner.check_knowledge(state)

        def guard() -> None:
            saved, _ = owner.current()
            if saved != original_plan:
                raise ValueError("saved plan changed; reconciliation required")
            owner.check_knowledge(state)
            if managed.prepared:
                current = {f.path: f.sha256 for f in managed.snapshot().files}
                if any(
                    current.get(source.path) != source.sha256
                    for source in knowledge.repository.sources
                    if source.role == "instruction"
                ):
                    raise ValueError("project rules changed inside worktree; replan required")
            if capture(root, original_plan.settings.scope, EXCLUDED)[0] != original_plan.workspace:
                raise ValueError("source workspace changed; reconciliation required")

        guard()
        approval = PlanApproval(
            session_id=spec.session_id,
            plan_fingerprint=spec.fingerprint,
            source="controller:explicit-run-fingerprint",
            timestamp=datetime.now(UTC),
        )
        owner.journal.register_plan(spec)
        owner.record(
            "execution_authorized",
            ExecutionAuthorization(
                approval=approval,
                executor=config,
                workspace=str(workspace),
                reviewer_identity=reviewer_identity(reviewer),
            ),
            spec.revision,
            source=approval.source,
        )
        runtime = ToolRuntime(
            spec,
            graph.tasks[0].id,
            root=root,
            journal=owner.journal,
            approval=approval,
            workspace=managed,
            guard=guard,
        )
        prepared = await runtime.workspace_operation(
            "prepare", WorkspaceOperation(operation="prepare")
        )
        if prepared.status != "succeeded":
            return RunResult(
                status="blocked",
                reason=prepared.reason,
                fingerprint=spec.fingerprint,
                session_id=spec.session_id,
                journal=str(owner.journal.directory / "events.jsonl"),
            )
        coder = _SessionCoder(owner, plan, knowledge, spec, approval, managed, config, guard)

        def verification_runtime(task_id: str) -> ToolRuntime:
            return ToolRuntime(
                spec,
                task_id,
                root=managed.path,
                journal=owner.journal,
                approval=approval,
                workspace=managed,
                guard=guard,
            )

        review_runner = (
            ReviewRunner(owner, plan, knowledge, reviewer, verification_runtime)
            if reviewer is not None
            else None
        )
        verifier = VerificationRunner(owner, verification, verification_runtime)
        baseline = final_verification = None
        final_reviews: list[ReviewResult] = []
        global_task = spec.verification_tasks[0]
        try:
            if plan.draft.baseline_check_ids:
                baseline = await verifier.check(
                    global_task,
                    runtime.read_revision(),
                    phase="baseline",
                    check_ids=plan.draft.baseline_check_ids,
                )
                expected = {c for c in plan.draft.baseline_check_ids}
                if (
                    not baseline.evidence
                    or {e.check_id for e in baseline.evidence} != expected
                    or any(not e.passed for e in baseline.evidence)
                ):
                    category: Literal["environment", "pre_existing", "unknown"] = (
                        "environment"
                        if any(e.status is EvidenceStatus.UNAVAILABLE for e in baseline.evidence)
                        else "pre_existing"
                        if any(e.status is EvidenceStatus.FAILED for e in baseline.evidence)
                        else "unknown"
                    )
                    owner.record(
                        "failure_diagnosed",
                        FailureDiagnosis(
                            category=category,
                            reason="Required baseline did not pass before coding",
                            evidence_ids=tuple(e.id for e in baseline.evidence if not e.passed),
                        ),
                        runtime.read_revision(),
                    )
                    owner.record("execution_blocked", baseline, runtime.read_revision())
                    failures = list(
                        dict.fromkeys(
                            f"{e.check_id} ({e.status}): {e.result}"
                            for e in baseline.evidence
                            if not e.passed
                        )
                    )
                    missing = expected - {e.check_id for e in baseline.evidence}
                    if missing:
                        failures.append("Missing checks: " + ", ".join(sorted(missing)))
                    return RunResult(
                        status="blocked",
                        reason="Required baseline checks did not pass: " + "; ".join(failures),
                        fingerprint=spec.fingerprint,
                        session_id=spec.session_id,
                        journal=str(owner.journal.directory / "events.jsonl"),
                        worktree=str(managed.path),
                        baseline=baseline,
                    )
            if review_runner is not None and baseline is not None:
                review_runner.baseline_evidence = baseline.evidence
            initial_purpose: Literal["implement", "debug", "review_fix"] = "implement"
            initial_feedback: tuple[str, ...] = ()
            while True:
                history = inspect_journal(owner.journal.directory / "events.jsonl")
                engine = WorkflowEngine(
                    spec,
                    coder=coder,
                    verifier=verifier,
                    reviewer=review_runner or _UnavailableStages(),
                    read_revision=runtime.read_revision,
                    writer=owner.journal,
                    history=history.events,
                    baseline_evidence=baseline.evidence if baseline else (),
                    initial_purpose=initial_purpose,
                    initial_feedback=initial_feedback,
                )
                result = await engine.run(approval)
                status, reason = result.outcome, result.reason
                if status != "replan_required" or replanner is None:
                    break
                try:
                    if reviewer_identity(replanner) != replanner_identity:
                        raise ValueError("replanner configuration changed")
                    stopped = next(t for t in result.tasks if t.state.value == "REPLAN_REQUIRED")
                    proposed = await replan(
                        owner,
                        plan,
                        knowledge,
                        verification_runtime(stopped.task_id),
                        replanner,
                        result,
                    )
                    if reviewer_identity(replanner) != replanner_identity:
                        raise ValueError("replanner configuration changed during dispatch")
                except (ValueError, ProviderError, TimeoutError) as error:
                    reason = (
                        "Planner timed out; retain work for reconciliation"
                        if isinstance(error, TimeoutError)
                        else str(error)
                    )
                    owner.record(
                        "replan_rejected",
                        FailureDiagnosis(
                            category="unknown",
                            reason=reason,
                        ),
                        runtime.read_revision(),
                    )
                    break
                if any(d.category == "review" for d in result.diagnoses):
                    initial_purpose = "review_fix"
                elif any(d.category == "code_failure" for d in result.diagnoses):
                    initial_purpose = "debug"
                initial_feedback = (
                    (result.reason,)
                    + tuple(e.result for e in result.evidence if not e.passed)
                    + tuple(finding for r in result.reviews for finding in (*r.blocking, *r.major))
                )
                previous_spec = spec
                plan = proposed
                spec = execution_spec(plan, config, workspace, verification, reviewer, replanner)
                owner.journal.register_plan(spec, previous=previous_spec)
                approval = PlanApproval(
                    session_id=spec.session_id,
                    plan_fingerprint=spec.fingerprint,
                    source="controller:inherited-operational-replan:" + previous_spec.fingerprint,
                    timestamp=datetime.now(UTC),
                )
                owner.record(
                    "execution_authorized",
                    ExecutionAuthorization(
                        approval=approval,
                        executor=config,
                        workspace=str(workspace),
                        reviewer_identity=reviewer_identity(reviewer),
                    ),
                    spec.revision,
                    source=approval.source,
                )
                runtime = verification_runtime(spec.graph.tasks[0].id)
                coder = _SessionCoder(
                    owner, plan, knowledge, spec, approval, managed, config, guard
                )
                review_runner = (
                    ReviewRunner(owner, plan, knowledge, reviewer, verification_runtime)
                    if reviewer is not None
                    else None
                )
                if review_runner is not None and baseline is not None:
                    review_runner.baseline_evidence = baseline.evidence
                global_task = spec.verification_tasks[0]
            if result.outcome == "tasks_verified":
                final_revision = runtime.read_revision()
                records: list[Evidence] = []
                gate = QualityGate()
                for task in (*spec.graph.tasks, global_task):
                    checked = await verifier.check(task, final_revision, phase="final")
                    records.extend(checked.evidence)
                    if not gate.verification(
                        task, runtime.read_revision(), checked.evidence
                    ).passed:
                        status, reason = (
                            "blocked",
                            "Required final-snapshot verification did not pass",
                        )
                        continue
                    review = None
                    if review_required(task, spec.mode):
                        review = (
                            await review_runner.check(
                                task, final_revision, checked.evidence, phase="final"
                            )
                            if review_runner is not None
                            else await _UnavailableStages().review(
                                task, final_revision, checked.evidence
                            )
                        )
                        final_reviews.append(review)
                    if not gate.evaluate(
                        task, runtime.read_revision(), checked.evidence, review, spec.mode
                    ).passed:
                        status, reason = "blocked", "Required final-snapshot review did not pass"
                final_verification = VerificationResult(evidence=tuple(records))
        finally:
            # Stop new side effects if a request/result pair or journal is unavailable.
            owner.journal.check_writable()
            for operation in ("snapshot", "diff"):
                recorded = await runtime.workspace_operation(
                    "final-" + operation, WorkspaceOperation(operation=operation)
                )
                if recorded.status != "succeeded":
                    raise ValueError("final inspection failed; retain workspace and journal")
        if result.outcome == "tasks_verified" and runtime.read_revision() != final_revision:
            status, reason = "blocked", "Final snapshot changed after verification/review"
        return RunResult(
            status=status,
            reason=reason,
            fingerprint=spec.fingerprint,
            session_id=spec.session_id,
            journal=str(owner.journal.directory / "events.jsonl"),
            worktree=str(managed.path),
            workflow=result,
            baseline=baseline,
            final_verification=final_verification,
            final_reviews=tuple(final_reviews),
            plan_revision=plan.revision,
            usage=SessionUsage.from_events(
                inspect_journal(owner.journal.directory / "events.jsonl").events
            ),
        )
