"""Deterministic, attempt-local completion checks over trusted adapter records."""

from ..models import Evidence, EvidenceStatus, EvidenceType, TaskSpec
from .contracts import GateIssue, GateResult, ReviewResult, Revision, WorkflowMode
from .policy import review_required


class QualityGate:
    def verification(
        self, task: TaskSpec, revision: Revision, evidence: tuple[Evidence, ...]
    ) -> GateResult:
        """Validate non-review checks before invoking the independent reviewer."""
        issues: list[GateIssue] = []
        checks = {check.id: check for check in task.acceptance.checks}
        required = {
            (criterion.id, check_id)
            for criterion in task.acceptance.criteria
            for check_id in criterion.required_check_ids
            if checks[check_id].evidence_type is not EvidenceType.REVIEW
        }
        if not required:
            issues.append(GateIssue(code="missing", message="no required non-review checks"))
        seen: set[tuple[str, str]] = set()
        ids: set[str] = set()
        for record in evidence:
            pair = (record.criterion_id, record.check_id)
            if pair not in required or pair in seen or record.id in ids:
                issues.append(
                    GateIssue(
                        code="invalid", message=f"unexpected or duplicate evidence: {record.id}"
                    )
                )
            seen.add(pair)
            ids.add(record.id)
            try:
                task.acceptance.validate_evidence(record)
            except ValueError as error:
                issues.append(GateIssue(code="invalid", message=str(error)))
            if record.task_id != task.id:
                issues.append(GateIssue(code="invalid", message=f"wrong task: {record.id}"))
            if (
                record.plan_version != revision.plan_version
                or record.context_revision != revision.context_revision
                or record.workspace_revision != revision.workspace_revision
            ):
                issues.append(GateIssue(code="stale", message=f"stale evidence: {record.id}"))
            if not record.passed:
                issues.append(
                    GateIssue(
                        code="failed" if record.status is EvidenceStatus.FAILED else "unavailable",
                        message=f"{record.id}: {record.status.value}",
                    )
                )
        for criterion_id, check_id in sorted(required - seen):
            issues.append(
                GateIssue(code="missing", message=f"missing evidence: {criterion_id}/{check_id}")
            )
        return GateResult(issues=tuple(issues))

    def evaluate(
        self,
        task: TaskSpec,
        revision: Revision,
        evidence: tuple[Evidence, ...],
        review: ReviewResult | None,
        mode: WorkflowMode = WorkflowMode.STANDARD,
    ) -> GateResult:
        """Review checks are supported by the controller's matching ReviewResult.

        ReviewResult cannot supply test evidence. The engine projects explicit review
        checks to ledger entries only after recording the returned review report.
        """
        issues = list(self.verification(task, revision, evidence).issues)
        if review is None:
            if review_required(task, mode):
                issues.append(GateIssue(code="missing", message="required review missing"))
        else:
            if review.task_id != task.id:
                issues.append(GateIssue(code="invalid", message="review belongs to another task"))
            if review.revision != revision:
                issues.append(GateIssue(code="stale", message="review revision is stale"))
            if review.status is not EvidenceStatus.PASSED:
                issues.append(
                    GateIssue(
                        code="failed" if review.status is EvidenceStatus.FAILED else "unavailable",
                        message=f"review: {review.status.value}",
                    )
                )
            if review.blocking or review.major:
                issues.append(
                    GateIssue(code="failed", message="review contains blocking or major findings")
                )
            for check in task.acceptance.checks:
                if check.evidence_type is EvidenceType.REVIEW and check.command is not None:
                    issues.append(
                        GateIssue(
                            code="invalid",
                            message=f"review command execution is unsupported: {check.id}",
                        )
                    )
        return GateResult(issues=tuple(issues))
