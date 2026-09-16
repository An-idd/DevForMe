"""One approved, retained Worktree execution; verification and recovery stay explicit."""

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from ..context.coder import build_context
from ..core.knowledge import KnowledgeSnapshot
from ..core.models import DomainModel, Evidence, EvidenceStatus, TaskSpec
from ..core.planning import PlanVersion
from ..core.tools import Shell
from ..core.workflow import (
    CoderResult,
    PlanApproval,
    ReviewResult,
    Revision,
    RunSpec,
    VerificationResult,
    WorkflowEngine,
    WorkflowResult,
)
from ..core.workspace import WorkspaceOperation
from ..executors.codex import CodexCoder, CodexSettings
from ..runtime._snapshots import capture
from ..runtime.workspace import Workspace
from ..session.records import Sanitizer, inspect_journal
from ..tools.execution import ExecutionRuntime
from ..tools.initialization import EXCLUDED
from ..tools.runtime import ToolRuntime
from .initialization import initialize
from .planning import inspect_plan, run_spec


class ExecutionAuthorization(DomainModel):
    approval: PlanApproval
    executor: CodexSettings
    workspace: str


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


class _UnavailableStages:
    async def verify(self, task: TaskSpec, revision: Revision) -> VerificationResult:
        # No execution happened; absent evidence must remain absent.
        return VerificationResult(evidence=())

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


def execution_spec(plan: PlanVersion, config: CodexSettings, directory: Path) -> RunSpec:
    identity = config.model_dump_json() + "\n" + str(directory)
    return RunSpec.model_validate(
        run_spec(plan).model_dump()
        | {
            "session_id": "run-" + plan.plan_id,
            "executor_revision": sha256(identity.encode()).hexdigest(),
        }
    )


async def run(
    root: Path,
    *,
    config: CodexSettings,
    workspace: Path,
    approve: str | None = None,
    secrets: tuple[str, ...] = (),
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
    if plan.draft.refactor or plan.draft.baseline_check_ids:
        return RunResult(
            status="blocked",
            reason="Required baseline verification is not configured; implementation is blocked.",
        )
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
    managed = Workspace(root, workspace, plan.settings.scope, excluded=EXCLUDED)
    spec = execution_spec(plan, config, workspace)
    if approve != spec.fingerprint:
        return RunResult(
            status="approval_required",
            reason="Review the saved plan; execute with --approve " + spec.fingerprint,
            fingerprint=spec.fingerprint,
            spec=spec,
            session_id=spec.session_id,
        )
    sanitizer = Sanitizer(secrets)
    with ExecutionRuntime(root, plan, sanitizer=sanitizer) as owner:
        state = owner.load()
        knowledge = owner.check_knowledge(state)

        def guard() -> None:
            saved, _ = owner.current()
            if saved != plan:
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
            if capture(root, plan.settings.scope, EXCLUDED)[0] != plan.workspace:
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
            ExecutionAuthorization(approval=approval, executor=config, workspace=str(workspace)),
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
        unavailable = _UnavailableStages()
        engine = WorkflowEngine(
            spec,
            coder=coder,
            verifier=unavailable,
            reviewer=unavailable,
            read_revision=runtime.read_revision,
            writer=owner.journal,
        )
        try:
            result = await engine.run(approval)
        finally:
            # Stop new side effects if a request/result pair or journal is unavailable.
            owner.journal.check_writable()
            for operation in ("snapshot", "diff"):
                recorded = await runtime.workspace_operation(
                    "final-" + operation, WorkspaceOperation(operation=operation)
                )
                if recorded.status != "succeeded":
                    raise ValueError("final inspection failed; retain workspace and journal")
        return RunResult(
            status=result.outcome,
            reason=result.reason,
            fingerprint=spec.fingerprint,
            session_id=spec.session_id,
            journal=str(owner.journal.directory / "events.jsonl"),
            worktree=str(managed.path),
            workflow=result,
        )
