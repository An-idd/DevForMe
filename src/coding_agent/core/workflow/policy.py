"""D03 mode selection never removes a declared acceptance check."""

from ..models import EvidenceType, RiskLevel, TaskSpec
from .contracts import WorkflowMode


def effective_mode(task: TaskSpec, requested: WorkflowMode) -> WorkflowMode:
    if task.risk.level is RiskLevel.HIGH:
        return WorkflowMode.STRICT
    if task.risk.level is RiskLevel.MEDIUM and requested is WorkflowMode.FAST:
        return WorkflowMode.STANDARD
    return requested


def review_required(task: TaskSpec, mode: WorkflowMode) -> bool:
    return effective_mode(task, mode) is not WorkflowMode.FAST or any(
        check.evidence_type is EvidenceType.REVIEW for check in task.acceptance.checks
    )
