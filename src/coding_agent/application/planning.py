"""Plan-only orchestration and read-only inspection; execution requires a separate approval."""

from pathlib import Path
from typing import Literal
from uuid import uuid4

from ..agents.planner import propose_plan
from ..core.models import DomainModel, RequirementContract
from ..core.planning import (
    PlanDraft,
    PlanningRevision,
    PlanSettings,
    PlanVersion,
    compile_graph,
    validate_draft,
)
from ..core.provider import ModelProvider
from ..core.workflow import PlanApproval, Revision, RunSpec, WorkflowMode
from ..core.workflow.policy import effective_mode
from ..providers.runtime import ModelRuntime
from ..runtime._snapshots import capture
from ..session.records import Sanitizer
from ..tools.initialization import EXCLUDED
from ..tools.planning import PlanningRuntime
from .initialization import initialize


class PlanningResult(DomainModel):
    status: Literal["proposed", "blocked", "stale"]
    plan: PlanVersion | None = None
    path: str | None = None
    journal: str | None = None
    blockers: tuple[str, ...] = ()
    summary: str = ""


class PlanInspection(DomainModel):
    status: Literal["proposed", "blocked", "stale"]
    plan: PlanVersion
    authorization: Literal["required", "matching", "invalid"]
    blockers: tuple[str, ...]
    execution_status: Literal["not_tracked"] = "not_tracked"
    requirement_complete: Literal[False] = False


def run_spec(plan: PlanVersion) -> RunSpec:
    """Build a proposed RunSpec. WorkflowEngine still requires matching PlanApproval."""
    plan = PlanVersion.model_validate(plan)
    if plan.blockers:
        raise ValueError("blocked plan cannot produce an executable graph")
    return RunSpec(
        session_id=plan.plan_id,
        graph=compile_graph(plan.draft, plan.settings),
        revision=Revision(
            plan_version=plan.version,
            context_revision=plan.context_revision,
            workspace_revision=plan.workspace.revision,
        ),
        mode=WorkflowMode(plan.settings.mode),
        max_review_fixes=plan.settings.max_review_fixes,
        max_total_attempts=plan.settings.max_total_attempts,
        max_tool_calls=plan.settings.max_tool_calls,
        max_model_calls=plan.settings.max_model_calls,
        worker_timeout_seconds=plan.settings.worker_timeout_seconds,
        plan_revision=plan.revision,
        pending_milestones=plan.pending_milestones,
    )


def render_plan(plan: PlanVersion) -> str:
    draft = plan.draft
    assessment = draft.assessment
    lines = [
        f"# Plan {plan.plan_id} / version {plan.version}",
        "",
        draft.requirement.title,
        "",
        draft.requirement.goal,
        "",
        "Status: blocked"
        if plan.blockers
        else "Status: proposed; execution authorization required",
        "This planning operation did not implement code or run baseline/verification checks.",
        "",
        f"Knowledge: {plan.context_revision}",
        f"Workspace: {plan.workspace.revision}",
        f"Change reason: {plan.change_reason}",
        "",
        f"Complexity: {assessment.complexity}; strategy: {assessment.execution_strategy}",
        f"Risk: {draft.requirement.risk.level}",
        "",
        f"Scope: {assessment.scope}",
        f"Coupling: {assessment.coupling}",
        f"Uncertainty: {assessment.uncertainty}",
        f"Verification difficulty: {assessment.verification_difficulty}",
        *(f"- Reason: {reason}" for reason in assessment.reasons),
        "",
        "## Current tasks",
        "",
    ]
    for item in draft.tasks:
        task = item.task
        lines.extend(
            (
                f"### {task.id}: {task.title}",
                "",
                task.goal,
                "",
                "Files: " + ", ".join(task.scope.allowed),
                "Depends on: " + (", ".join(task.depends_on) or "none"),
                f"Risk: {task.risk.level}; "
                f"mode: {effective_mode(task, WorkflowMode(plan.settings.mode))}",
                f"Requested permissions: {task.permissions.model_dump_json()}",
                "Rules: " + (", ".join(item.rule_ids) or "none supplied"),
                "Sources: "
                + (", ".join(f"{r.path}:{r.line}-{r.end_line}" for r in item.sources) or "none"),
            )
        )
        for criterion in task.acceptance.criteria:
            lines.append(
                f"- {criterion.id}: {criterion.description}; "
                f"checks={', '.join(criterion.required_check_ids)}"
            )
        for check in task.acceptance.checks:
            lines.append(f"  - {check.id}: {check.description}; command={check.command}")
        lines.append("")
    lines.extend(("## Required roadmap", ""))
    for milestone in draft.milestones:
        lines.append(
            f"- {milestone.id} [{milestone.status}]: {milestone.title}; "
            f"criteria={', '.join(milestone.criterion_ids)}"
        )
        lines.extend(f"  - Unknown: {q.text}" for q in milestone.unknowns if q.answer is None)
    lines.extend(("", "## Requirement-wide acceptance / final integration", ""))
    for criterion in draft.acceptance.criteria:
        lines.append(
            f"- {criterion.id}: {criterion.description}; "
            f"checks={', '.join(criterion.required_check_ids)}"
        )
    for check in draft.acceptance.checks:
        lines.append(f"  - {check.id}: {check.description}; command={check.command}")
    lines.extend(
        (
            "",
            "Behavior invariants: "
            + (", ".join(draft.invariant_criterion_ids) or "not a declared refactor"),
            "Baseline checks (not run): " + (", ".join(draft.baseline_check_ids) or "none"),
            f"Attempt limit: {plan.settings.max_total_attempts}; "
            f"tool request limit: {plan.settings.max_tool_calls}; "
            f"model segment limit: {plan.settings.max_model_calls}; "
            f"replan limit: {plan.settings.max_replans}; "
            "no execution budget consumed by planning.",
            "",
            "## Blockers",
            "",
            *(f"- {message}" for message in plan.blockers),
            "",
            "A verified current batch cannot complete pending milestones or final integration.",
            "",
        )
    )
    return "\n".join(lines)


async def plan(
    root: Path,
    request: str | RequirementContract | None = None,
    *,
    draft: PlanDraft | None = None,
    draft_path: str | None = None,
    requirement_path: str | None = None,
    import_path: str | None = None,
    provider: ModelProvider | None = None,
    settings: PlanSettings | None = None,
    focus: tuple[str, ...] = (),
    refresh: bool = False,
    reason: str | None = None,
    new_plan: bool = False,
    secrets: tuple[str, ...] = (),
) -> PlanningResult:
    if sum(x is not None for x in (draft, draft_path, import_path)) > 1:
        raise ValueError("choose one draft or saved-plan input")
    if request is not None and requirement_path is not None:
        raise ValueError("choose request text or a requirement contract")
    settings = settings or PlanSettings()
    sanitizer = Sanitizer(secrets)
    initialized = await initialize(
        root, refresh=refresh, focus=focus, scope=settings.scope, secrets=secrets
    )
    if initialized.status in {"stale", "blocked"}:
        return PlanningResult(
            status="stale" if initialized.status == "stale" else "blocked",
            journal=initialized.journal,
            blockers=("Refresh project knowledge and resolve required questions before planning.",),
        )
    with PlanningRuntime(root, settings.scope, sanitizer=sanitizer) as runtime:
        state = runtime.load()
        assert state.snapshot is not None
        runtime.revision = PlanningRevision(
            context_revision=state.snapshot.revision, workspace_revision="not-yet-captured"
        )
        knowledge = runtime.inspect_knowledge(state)
        current, index = runtime.current()
        previous = None if new_plan else current
        imported = None
        if requirement_path is not None:
            request = RequirementContract.model_validate_json(runtime.read_input(requirement_path))
        if draft_path is not None:
            draft = PlanDraft.model_validate_json(runtime.read_input(draft_path))
        if import_path is not None:
            imported = PlanVersion.model_validate_json(runtime.read_input(import_path))
            original_data = imported.model_dump(mode="json")
            if sanitizer.tree(original_data) != original_data:
                raise ValueError(
                    "imported plan contains credentials; redact its source before importing"
                )
            draft = imported.draft
            if imported.settings != settings:
                raise ValueError("imported plan settings do not match the controller boundary")
            reason = reason or "Import saved plan"
        if request is None:
            request = (
                previous.draft.requirement if previous else draft.requirement if draft else None
            )
        if request is None:
            return PlanningResult(
                status="blocked",
                journal=str(runtime.journal.directory / "events.jsonl"),
                blockers=("Supply request text, --requirement, --draft or --import.",),
            )
        if isinstance(request, str):
            if not request.strip() or len(request) > 4000:
                raise ValueError("request must be nonempty and at most 4000 characters")
            request = sanitizer.text(request)
        else:
            request = RequirementContract.model_validate(
                sanitizer.tree(request.model_dump(mode="json"))
            )
        original = request if isinstance(request, str) else request.goal
        if previous is not None:
            if original != previous.original_request:
                raise ValueError("different requirement; use --new for a separate plan history")
            if reason is None or not reason.strip():
                raise ValueError("a new plan version requires --reason")
        workspace = runtime.snapshot()
        runtime.revision = PlanningRevision(
            context_revision=knowledge.revision, workspace_revision=workspace.revision
        )
        if imported is not None and (
            imported.context_revision != knowledge.revision or imported.workspace != workspace
        ):
            raise ValueError(
                "imported plan is stale; regenerate against current knowledge and workspace"
            )
        if draft is None:
            if provider is None:
                return PlanningResult(
                    status="blocked",
                    journal=str(runtime.journal.directory / "events.jsonl"),
                    blockers=(
                        "Supply --model for planning or --draft for offline validation; no plan "
                        "was invented.",
                    ),
                )
            model = ModelRuntime(
                provider, journal=runtime.journal, read_revision=lambda: runtime.revision
            )
            draft = await propose_plan(
                previous.draft.requirement if previous else request,
                knowledge,
                settings,
                model,
                previous=previous,
            )
        else:
            draft = PlanDraft.model_validate(sanitizer.tree(draft.model_dump(mode="json")))
        if draft.requirement.goal != original:
            raise ValueError("planner changed the original goal")
        blockers = validate_draft(
            draft,
            knowledge,
            settings,
            requirement=request if isinstance(request, RequirementContract) else None,
            previous=previous,
        )
        version = PlanVersion(
            plan_id=previous.plan_id if previous else uuid4().hex,
            version=previous.version + 1 if previous else 1,
            previous_revision=previous.revision if previous else None,
            change_reason=reason or "Initial plan proposal",
            original_request=original,
            context_revision=knowledge.revision,
            workspace=workspace,
            settings=settings,
            draft=draft,
            blockers=blockers,
            imported_from=imported.revision if imported else None,
        )
        summary = render_plan(version)
        runtime.publish_plan(version, state, index, summary, imported)
        return PlanningResult(
            status="blocked" if blockers else "proposed",
            plan=version,
            path=str(runtime.control / version.filename),
            journal=str(runtime.journal.directory / "events.jsonl"),
            blockers=blockers,
            summary=summary,
        )


def inspect_plan(root: Path, *, approval: PlanApproval | None = None) -> PlanInspection:
    """Read-only: never bootstrap a project, call a model, write a journal or approve a plan."""
    with PlanningRuntime.read_only(root) as runtime:
        saved, _ = runtime.current()
        if saved is None:
            raise ValueError("no saved plan; run agent plan first")
        runtime.scope = saved.settings.scope
        state = runtime.load()
        knowledge = state.snapshot
        blockers: list[str] = []
        stale = False
        try:
            knowledge = runtime.check_knowledge(state)
            if knowledge.revision != saved.context_revision:
                raise ValueError("plan knowledge revision is stale")
            if capture(runtime.root, saved.settings.scope, EXCLUDED)[0] != saved.workspace:
                raise ValueError("plan workspace revision is stale")
        except ValueError as error:
            stale = True
            blockers.append(str(error))
        if knowledge is not None and not stale:
            blockers.extend(validate_draft(saved.draft, knowledge, saved.settings))
            if tuple(blockers) != saved.blockers:
                raise ValueError("saved plan diagnostics do not match deterministic validation")
        authorization: Literal["required", "matching", "invalid"] = "required"
        if approval is not None:
            authorization = (
                "matching"
                if not stale and not blockers and approval.covers(run_spec(saved))
                else "invalid"
            )
        return PlanInspection(
            status="stale" if stale else "blocked" if blockers else "proposed",
            plan=saved,
            authorization=authorization,
            blockers=tuple(blockers),
        )


def render_graph(plan: PlanVersion) -> str:
    lines = ["Current batch (planned; no task execution state):"]
    for item in plan.draft.tasks:
        lines.append(f"{item.task.id} <- {', '.join(item.task.depends_on) or '(root)'}")
    lines.append("Pending milestones: " + (", ".join(plan.pending_milestones) or "none"))
    return "\n".join(lines)
