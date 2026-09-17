"""Planner proposals and deterministic compilation; no execution or approval side effects."""

from hashlib import sha256
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self
from unicodedata import normalize

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    model_serializer,
    model_validator,
)

from .graph import TaskGraph
from .knowledge import Digest, KnowledgeSnapshot, OpenQuestion, RepoSummary, SourceReference, Text
from .models import (
    AcceptanceSpec,
    DomainModel,
    Identifier,
    PermissionPolicy,
    PositiveInt,
    RequirementContract,
    RiskLevel,
    ScopePolicy,
    TaskSpec,
)
from .paths import path_permitted, relative_parts
from .workspace import WorkspaceSnapshot

PlanId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class PlanningRevision(DomainModel):
    phase: Literal["planning"] = "planning"
    context_revision: str
    workspace_revision: str


class PlanningOperation(DomainModel):
    kind: Literal["planning"] = "planning"
    operation: Literal["inspect", "input", "snapshot", "publish"]
    input_sha256: Digest


class ComplexityAssessment(DomainModel):
    complexity: Literal["small", "medium", "large"]
    execution_strategy: Literal["single_task", "task_graph", "staged"]
    scope: Text
    coupling: Text
    uncertainty: Text
    verification_difficulty: Text
    reasons: Annotated[tuple[Text, ...], Field(min_length=1)]
    unknowns: tuple[OpenQuestion, ...] = ()
    sources: Annotated[tuple[SourceReference, ...], Field(min_length=1, max_length=8)]


class RequirementCoverage(DomainModel):
    requirement: PositiveInt
    criterion_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]


class PlannedTask(DomainModel):
    task: TaskSpec
    criterion_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    rule_ids: tuple[Identifier, ...] = ()
    context_ids: tuple[Identifier, ...] = ()
    sources: tuple[SourceReference, ...] = ()
    assessment: ComplexityAssessment | None = None


class Milestone(DomainModel):
    id: Identifier
    title: Text
    status: Literal["current", "pending"]
    criterion_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    task_ids: tuple[Identifier, ...] = ()
    unknowns: tuple[OpenQuestion, ...] = ()

    @model_validator(mode="after")
    def pending_is_not_executable(self) -> Self:
        if self.status == "pending" and self.task_ids:
            raise ValueError("pending milestones cannot contain executable task placeholders")
        return self


class PlanDraft(DomainModel):
    requirement: RequirementContract
    assessment: ComplexityAssessment
    acceptance: AcceptanceSpec
    coverage: Annotated[tuple[RequirementCoverage, ...], Field(min_length=1)]
    tasks: Annotated[tuple[PlannedTask, ...], Field(min_length=1, max_length=32)]
    milestones: Annotated[tuple[Milestone, ...], Field(min_length=1, max_length=32)]
    refactor: StrictBool = False
    invariant_criterion_ids: tuple[Identifier, ...] = ()
    baseline_check_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def structurally_complete(self) -> Self:
        graph = TaskGraph(tasks=tuple(item.task for item in self.tasks))
        criteria = {c.id for c in self.acceptance.criteria}
        requirements = [c.requirement for c in self.coverage]
        if sorted(requirements) != list(
            range(1, len(self.requirement.functional_requirements) + 1)
        ):
            raise ValueError("coverage must map every functional requirement exactly once")
        if any(set(c.criterion_ids) - criteria for c in self.coverage):
            raise ValueError("coverage references unknown requirement-wide criteria")
        if any(set(t.criterion_ids) - criteria for t in self.tasks):
            raise ValueError("task references unknown requirement-wide criteria")
        milestones = {m.id: m for m in self.milestones}
        if len(milestones) != len(self.milestones):
            raise ValueError("duplicate milestone IDs")
        current = [m for m in self.milestones if m.status == "current"]
        if len(current) != 1 or set(current[0].task_ids) != {t.id for t in graph.tasks}:
            raise ValueError("one current milestone must identify exactly the compiled tasks")
        if any(set(m.criterion_ids) - criteria for m in self.milestones):
            raise ValueError("milestone references unknown requirement-wide criteria")
        if set().union(*(set(m.criterion_ids) for m in self.milestones)) != criteria:
            raise ValueError("milestones must retain all requirement-wide acceptance")
        if set(current[0].criterion_ids) != set().union(
            *(set(t.criterion_ids) for t in self.tasks)
        ):
            raise ValueError("current milestone acceptance must match its task coverage")
        strategy = self.assessment.execution_strategy
        if strategy == "single_task" and len(self.tasks) != 1:
            raise ValueError("single_task strategy requires exactly one task")
        if self.assessment.complexity == "large" and strategy != "staged":
            raise ValueError("large work requires a staged roadmap")
        if strategy != "staged" and len(self.milestones) != 1:
            raise ValueError("pending milestones require staged execution")
        checks = {c.id: c for c in self.acceptance.checks}
        if (
            set(self.invariant_criterion_ids) - criteria
            or set(self.baseline_check_ids) - checks.keys()
        ):
            raise ValueError("unknown invariant or baseline check IDs")
        if self.refactor:
            if not self.invariant_criterion_ids or not self.baseline_check_ids:
                raise ValueError("refactors require behavior invariants and baseline checks")
            needed = {
                check_id
                for c in self.acceptance.criteria
                if c.id in self.invariant_criterion_ids
                for check_id in c.required_check_ids
                if checks[check_id].command is not None
            }
            if not needed or not needed <= set(self.baseline_check_ids):
                raise ValueError("baseline checks must cover executable invariant checks")
        for task in graph.tasks:
            if not task.scope.allowed:
                raise ValueError("task write scope must enumerate concrete files")
            for path in task.scope.allowed:
                relative_parts(path)
                if any(c in path for c in "*?["):
                    raise ValueError("task write scope cannot use broad glob patterns")
        return self


class PlanSettings(DomainModel):
    scope: ScopePolicy = ScopePolicy()
    permissions: PermissionPolicy = PermissionPolicy(shell="restricted")
    mode: Literal["fast", "standard", "strict"] = "standard"
    max_total_attempts: PositiveInt = 30
    max_tool_calls: PositiveInt = 30
    max_model_calls: PositiveInt = 31
    max_replans: Annotated[int, Field(strict=True, ge=0)] = 2
    max_review_fixes: Annotated[int, Field(strict=True, ge=0)] = 2
    worker_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 60.0

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        # Keep pre-budget plan revisions and existing approvals valid at legacy defaults.
        data: dict[str, object] = handler(self)
        if self.max_tool_calls == 30:
            data.pop("max_tool_calls", None)
        if self.max_model_calls == 31:
            data.pop("max_model_calls", None)
        if self.max_replans == 2:
            data.pop("max_replans", None)
        return data


class PlanVersion(DomainModel):
    format_version: Literal[1] = 1
    plan_id: PlanId
    version: PositiveInt
    previous_revision: Digest | None
    change_reason: Text
    original_request: Text
    context_revision: Digest
    workspace: WorkspaceSnapshot
    settings: PlanSettings
    draft: PlanDraft
    blockers: tuple[Text, ...] = ()
    imported_from: Digest | None = None

    @model_validator(mode="after")
    def valid_history(self) -> Self:
        if (self.version == 1) != (self.previous_revision is None):
            raise ValueError("plan version and predecessor disagree")
        if self.original_request != self.draft.requirement.goal:
            raise ValueError("plan goal disagrees with the original request")
        return self

    @property
    def revision(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()

    @property
    def pending_milestones(self) -> tuple[str, ...]:
        return tuple(m.id for m in self.draft.milestones if m.status == "pending")

    @property
    def filename(self) -> str:
        return f"plan-{self.plan_id}-v{self.version}.json"


class PlanIndex(DomainModel):
    plan_id: PlanId
    version: PositiveInt
    revision: Digest


def applicable_rules(knowledge: KnowledgeSnapshot, paths: tuple[str, ...]) -> tuple[str, ...]:
    def applies(ref: SourceReference) -> bool:
        parent = str(PurePosixPath(ref.path).parent)
        return (
            ref.path == "user:guidance"
            or parent == "."
            or any(p.startswith(parent + "/") for p in paths)
        )

    return tuple(
        e.id
        for e in knowledge.summary.entries
        if e.authority == "rule" and any(applies(ref) for ref in e.sources)
    )


def compile_graph(draft: PlanDraft, settings: PlanSettings) -> TaskGraph:
    """Carry controller read restrictions into each task's runtime scope."""
    return TaskGraph(
        tasks=tuple(
            TaskSpec.model_validate(
                item.task.model_dump()
                | {
                    "scope": ScopePolicy(
                        allowed=item.task.scope.allowed,
                        forbidden=tuple(
                            dict.fromkeys((*settings.scope.forbidden, *item.task.scope.forbidden))
                        ),
                    )
                }
            )
            for item in draft.tasks
        )
    )


def validate_draft(
    draft: PlanDraft,
    knowledge: KnowledgeSnapshot,
    settings: PlanSettings,
    *,
    requirement: RequirementContract | None = None,
    previous: PlanVersion | None = None,
) -> tuple[str, ...]:
    """Structural/scope checks are deterministic; semantic judgments remain proposals."""
    draft = PlanDraft.model_validate(draft)
    if requirement is not None and draft.requirement != requirement:
        raise ValueError("planner changed the supplied requirement contract")
    graph = TaskGraph(tasks=tuple(t.task for t in draft.tasks))
    entries = {e.id: e for e in knowledge.summary.entries}
    observed = {s.path for s in knowledge.repository.sources}
    present = set(knowledge.repository.paths)
    blockers = [q.text for q in knowledge.summary.questions if q.blocking]
    blockers.extend(q.text for q in draft.assessment.unknowns if q.blocking)
    references = list(draft.assessment.sources)
    questions = list(draft.assessment.unknowns)
    for milestone in draft.milestones:
        questions.extend(milestone.unknowns)
        if milestone.status == "current":
            blockers.extend(q.text for q in milestone.unknowns if q.blocking)
    for item in draft.tasks:
        task = item.task
        references.extend(item.sources)
        if item.assessment is not None:
            references.extend(item.assessment.sources)
            questions.extend(item.assessment.unknowns)
            blockers.extend(q.text for q in item.assessment.unknowns if q.blocking)
        levels = tuple(RiskLevel)
        for dimension in ("level", "security", "database", "public_api", "architecture"):
            floor = getattr(draft.requirement.risk, dimension)
            actual = getattr(task.risk, dimension)
            if floor is not None and (actual is None or levels.index(actual) < levels.index(floor)):
                raise ValueError("task risk cannot downgrade requirement risk")
        for path in task.scope.allowed:
            aliases = [
                p
                for p in present
                if normalize("NFC", p).casefold() == normalize("NFC", path).casefold()
            ]
            if aliases and path not in aliases:
                raise ValueError("task path spelling aliases an existing source")
            if not path_permitted(path, knowledge.scope) or not path_permitted(
                path, task.scope, write=True
            ):
                raise ValueError(f"task scope is forbidden: {path}")
            if not path_permitted(path, settings.scope):
                raise ValueError(f"task scope exceeds controller boundary: {path}")
            if settings.scope.allowed and not path_permitted(path, settings.scope, write=True):
                raise ValueError(f"task scope exceeds controller boundary: {path}")
            if path in present and path not in observed:
                blockers.append(f"Inspect task source with --focus before execution: {path}")
        requested, ceiling = task.permissions, settings.permissions
        if (
            requested.network
            and not ceiling.network
            or requested.shell != "deny"
            and ceiling.shell == "deny"
            or requested.database != "deny"
            and ceiling.database == "deny"
        ):
            raise ValueError("task permissions exceed the planning boundary")
        if any(i not in entries or entries[i].authority != "rule" for i in item.rule_ids):
            raise ValueError("task references unknown explicit rule IDs")
        if set(applicable_rules(knowledge, task.scope.allowed)) - set(item.rule_ids):
            raise ValueError("task omits applicable project rules")
        if set(item.context_ids) - entries.keys():
            raise ValueError("task references unknown knowledge IDs")
    for i, item in enumerate(draft.tasks):
        for other in draft.tasks[i + 1 :]:
            left = {normalize("NFC", p).casefold() for p in item.task.scope.allowed}
            right = {normalize("NFC", p).casefold() for p in other.task.scope.allowed}
            if left & right:
                if item.task.id not in graph.ancestors(
                    other.task.id
                ) and other.task.id not in graph.ancestors(item.task.id):
                    raise ValueError("overlapping task writes require explicit dependency ordering")
    if sum(t.task.max_attempts for t in draft.tasks) > settings.max_total_attempts:
        raise ValueError("task attempt allocation exceeds the session limit")
    RepoSummary(
        questions=(*questions, OpenQuestion(text="Assessment sources", sources=tuple(references)))
    ).check_sources(knowledge.repository.sources)
    if previous is not None:
        if (
            draft.requirement != previous.draft.requirement
            or draft.acceptance != previous.draft.acceptance
        ):
            raise ValueError(
                "revisions must preserve the requirement and acceptance; reconciliation "
                "belongs to P11"
            )
        if (
            draft.invariant_criterion_ids != previous.draft.invariant_criterion_ids
            or draft.baseline_check_ids != previous.draft.baseline_check_ids
        ):
            raise ValueError("revisions cannot remove behavior invariants or baseline checks")
        if settings != previous.settings:
            raise ValueError("revisions cannot silently change authorization boundaries or budgets")
        old_milestones = {m.id: m for m in previous.draft.milestones}
        new_milestones = {m.id: m for m in draft.milestones}
        for key, old in old_milestones.items():
            new = new_milestones.get(key)
            if new is None or new.criterion_ids != old.criterion_ids or new.status != old.status:
                raise ValueError(
                    "pending scope cannot be dropped or advanced without runtime evidence"
                )
        old_tasks = {t.task.id: t for t in previous.draft.tasks}
        new_tasks = {t.task.id: t for t in draft.tasks}
        for key, old_task in old_tasks.items():
            new_task = new_tasks.get(key)
            if (
                new_task is None
                or new_task.task.acceptance != old_task.task.acceptance
                or new_task.criterion_ids != old_task.criterion_ids
            ):
                raise ValueError("task and criterion IDs/acceptance must survive plan revisions")
        old_questions = {"assessment": previous.draft.assessment.unknowns}
        new_questions = {"assessment": draft.assessment.unknowns}
        old_questions.update({f"milestone:{m.id}": m.unknowns for m in previous.draft.milestones})
        new_questions.update({f"milestone:{m.id}": m.unknowns for m in draft.milestones})
        old_questions.update(
            {
                f"task:{t.task.id}": t.assessment.unknowns
                for t in previous.draft.tasks
                if t.assessment is not None
            }
        )
        new_questions.update(
            {
                f"task:{t.task.id}": t.assessment.unknowns
                for t in draft.tasks
                if t.assessment is not None
            }
        )
        for location, old_items in old_questions.items():
            for question in old_items:
                if question.required and not any(
                    q.text == question.text and q.required for q in new_questions.get(location, ())
                ):
                    raise ValueError("revisions cannot remove or downgrade unresolved decisions")
        if previous.draft.refactor and not draft.refactor:
            raise ValueError("revisions cannot remove refactor baseline requirements")
    return tuple(dict.fromkeys(blockers))
