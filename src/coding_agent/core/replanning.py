"""Conservative live replanning within the already approved requirement and scope."""

from .knowledge import KnowledgeSnapshot
from .models import DomainModel, RiskLevel
from .planning import ComplexityAssessment, PlanDraft, PlanVersion, validate_draft
from .workflow.events import WorkflowEvent


class SessionUsage(DomainModel):
    attempts: int
    review_fixes: int
    replans: int
    tool_calls: int
    model_calls: int

    @classmethod
    def from_events(cls, events: tuple[WorkflowEvent, ...]) -> "SessionUsage":
        return cls(
            attempts=sum(e.kind == "coder_started" for e in events),
            review_fixes=sum(
                e.kind == "coder_started" and e.reason.startswith("review_fix attempt ")
                for e in events
            ),
            replans=sum(e.kind == "replan_requested" for e in events),
            tool_calls=sum(
                e.tool_request is not None
                and e.tool_request.invocation.kind
                in {"read", "search", "patch", "shell", "git", "review_rules"}
                for e in events
            ),
            model_calls=sum(e.model_request is not None for e in events),
        )


class ReplanRecord(DomainModel):
    previous_revision: str
    previous_assessment: ComplexityAssessment
    proposed: PlanVersion
    retired_task_ids: tuple[str, ...]
    usage: SessionUsage
    disposition: str = "Keep prior work/history; rerun all current tasks and applicable checks"


def validate_replan(
    draft: PlanDraft, previous: PlanVersion, knowledge: KnowledgeSnapshot
) -> tuple[str, ...]:
    """Only operational refinements inherit approval; material changes stop for review."""
    old = previous.draft
    if (
        draft.requirement != old.requirement
        or draft.acceptance != old.acceptance
        or draft.refactor != old.refactor
        or draft.invariant_criterion_ids != old.invariant_criterion_ids
        or draft.baseline_check_ids != old.baseline_check_ids
        or draft.coverage != old.coverage
    ):
        raise ValueError("replanning must preserve requirement, acceptance and invariants")
    milestones = {m.id: m for m in draft.milestones}
    if milestones.keys() != {m.id for m in old.milestones}:
        raise ValueError("replanning must retain all milestones")
    for milestone in old.milestones:
        new = milestones[milestone.id]
        if (
            new.status != milestone.status
            or new.criterion_ids != milestone.criterion_ids
            or new.unknowns != milestone.unknowns
            or (milestone.status == "pending" and new != milestone)
        ):
            raise ValueError("pending scope and unresolved milestone decisions must survive")
    old_questions = [*old.assessment.unknowns]
    new_questions = [*draft.assessment.unknowns]
    for item in old.tasks:
        if item.assessment:
            old_questions.extend(item.assessment.unknowns)
    for item in draft.tasks:
        if item.assessment:
            new_questions.extend(item.assessment.unknowns)
    if any(q.required and q not in new_questions for q in old_questions):
        raise ValueError("replanning cannot drop required unresolved decisions")
    levels = tuple(RiskLevel)
    for item in draft.tasks:
        related = [t for t in old.tasks if set(t.criterion_ids) & set(item.criterion_ids)]
        authorized = [
            t
            for t in related
            if set(item.task.scope.allowed) <= set(t.task.scope.allowed)
            and item.task.scope.forbidden == t.task.scope.forbidden
            and item.task.permissions == t.task.permissions
            and item.task.max_attempts <= t.task.max_attempts
        ]
        if not authorized:
            raise ValueError("replan needs new authorization for scope, permissions or attempts")
        for prior in related:
            if not set(prior.rule_ids) <= set(item.rule_ids):
                raise ValueError("replanning cannot relax applicable explicit rules")
            for dimension in ("level", "security", "database", "public_api", "architecture"):
                floor = getattr(prior.task.risk, dimension)
                actual = getattr(item.task.risk, dimension)
                if floor is not None and (
                    actual is None or levels.index(actual) < levels.index(floor)
                ):
                    raise ValueError("replanning cannot downgrade task risk")
            criteria = {c.id: c for c in item.task.acceptance.criteria}
            checks = {c.id: c for c in item.task.acceptance.checks}
            for criterion in prior.task.acceptance.criteria:
                if criteria.get(criterion.id) != criterion or any(
                    checks.get(check.id) != check
                    for check in prior.task.acceptance.checks
                    if check.id in criterion.required_check_ids
                ):
                    raise ValueError("replacement tasks must retain prior checks and criteria")
        same_id = next((t for t in old.tasks if t.task.id == item.task.id), None)
        if same_id and item.criterion_ids != same_id.criterion_ids:
            raise ValueError("retained task IDs cannot change criterion identity")
    return validate_draft(draft, knowledge, previous.settings)
