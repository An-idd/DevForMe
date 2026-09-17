"""Task-scoped, versioned context assembled through recorded runtime reads."""

from ..core.knowledge import (
    USER_SOURCE,
    KnowledgeEntry,
    KnowledgeSnapshot,
    OpenQuestion,
    SourceFile,
)
from ..core.models import AcceptanceSpec, DomainModel, RequirementContract
from ..core.planning import ComplexityAssessment, Milestone, PlanVersion, applicable_rules
from ..core.tools import Read, ToolRequest
from ..core.workflow import Revision
from ..tools.runtime import ToolRuntime


class ContextEntry(DomainModel):
    id: str
    entry: KnowledgeEntry


class TaskContextPack(DomainModel):
    task_id: str
    plan_revision: str
    revision: Revision
    requirement: RequirementContract
    assessment: ComplexityAssessment
    acceptance: AcceptanceSpec
    milestones: tuple[Milestone, ...]
    entries: tuple[ContextEntry, ...]
    knowledge_sources: tuple[SourceFile, ...]
    current_sources: tuple[SourceFile, ...]
    questions: tuple[OpenQuestion, ...]
    gaps: tuple[str, ...]


async def build_context(
    runtime: ToolRuntime, plan: PlanVersion, knowledge: KnowledgeSnapshot, *, review: bool = False
) -> TaskContextPack:
    if runtime.workspace is None or knowledge.revision != plan.context_revision:
        raise ValueError("context requires a managed workspace and matching knowledge")
    task = runtime.task
    item = next((t for t in plan.draft.tasks if t.task.id == task.id), None)
    if item is None and task not in runtime.spec.verification_tasks:
        raise ValueError("context task does not belong to the plan")
    items = (item,) if item is not None else plan.draft.tasks
    revision = runtime.read_revision()
    if (
        runtime.spec.plan_revision != plan.revision
        or revision.plan_version != plan.version
        or revision.context_revision != knowledge.revision
    ):
        raise ValueError("context plan or knowledge revision changed")
    required = (
        {e.id for e in knowledge.summary.entries if e.authority == "rule"}
        if review
        else set(applicable_rules(knowledge, task.scope.allowed))
    )
    selected = required | {r for t in items for r in (*t.rule_ids, *t.context_ids)}
    entries = tuple(
        ContextEntry(id=e.id, entry=e)
        for e in knowledge.summary.entries
        if e.id in selected or (e.authority == "assumption" and not e.sources)
    )
    references = (
        *(r for t in items for r in t.sources),
        *plan.draft.assessment.sources,
        *(r for e in entries for r in e.entry.sources),
    )
    paths = set(task.scope.allowed) | {r.path for r in references}
    sources = tuple(s for s in knowledge.repository.sources if s.path in paths)
    snapshot = runtime.workspace.snapshot()
    actual = {f.path: f for f in snapshot.files}
    current: list[SourceFile] = []
    for source in sources:
        if source.path == USER_SOURCE:
            continue
        if source.path not in actual:
            if review and source.role != "instruction":
                continue
            raise ValueError("context source disappeared; replan required")
        result = await runtime.execute(
            ToolRequest(
                request_id=f"context-{runtime.journal.next_sequence}",
                invocation=Read(path=source.path),
            )
        )
        if result.status != "succeeded":
            raise ValueError("context source read blocked; inspect tool records")
        encoded = result.output.encode("utf-8")
        current.append(
            SourceFile(
                path=source.path,
                sha256=actual[source.path].sha256,
                role=source.role,
                text=encoded[:32768].decode("utf-8", errors="ignore"),
                truncated=result.truncated or len(encoded) > 32768,
            )
        )
    if runtime.read_revision() != revision:
        raise ValueError("workspace changed while building context")
    context = TaskContextPack(
        task_id=task.id,
        plan_revision=plan.revision,
        revision=revision,
        requirement=plan.draft.requirement,
        assessment=(item.assessment if item else None) or plan.draft.assessment,
        acceptance=plan.draft.acceptance,
        milestones=plan.draft.milestones,
        entries=entries,
        knowledge_sources=sources,
        current_sources=tuple(current),
        questions=(*knowledge.summary.questions, *plan.draft.assessment.unknowns),
        gaps=knowledge.summary.gaps,
    )
    if len(context.model_dump_json().encode()) > 512 * 1024:
        raise ValueError("task context exceeds 512 KiB; refine focus and plan")
    return context
