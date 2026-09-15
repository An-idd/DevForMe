from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from coding_agent.core import Evidence, EvidenceStatus, TaskSpec
from coding_agent.core.workflow import QualityGate, ReviewResult, Revision, WorkflowMode


@pytest.fixture
def revision() -> Revision:
    return Revision(
        plan_version=1, context_revision="project-001", workspace_revision="snapshot-001"
    )


@pytest.fixture
def review(revision: Revision) -> ReviewResult:
    return ReviewResult(
        task_id="task-001",
        revision=revision,
        status=EvidenceStatus.PASSED,
        summary="explicit fake review",
        source="fake-review-001",
        timestamp=datetime(2026, 9, 15, tzinfo=UTC),
    )


def test_gate_requires_both_verification_and_review(
    make_task: Callable[..., TaskSpec],
    revision: Revision,
    evidence: Evidence,
    review: ReviewResult,
) -> None:
    gate = QualityGate()
    task = make_task("task-001")
    assert gate.verification(task, revision, (evidence,)).passed
    assert gate.evaluate(task, revision, (evidence,), review).passed
    assert not gate.evaluate(task, revision, (evidence,), None).passed
    assert not gate.evaluate(task, revision, (), review).passed


@pytest.mark.parametrize("status", [s for s in EvidenceStatus if s is not EvidenceStatus.PASSED])
def test_every_nonpassing_status_fails_gate(
    make_task: Callable[..., TaskSpec],
    revision: Revision,
    evidence: Evidence,
    review: ReviewResult,
    status: EvidenceStatus,
) -> None:
    bad = Evidence.model_validate(evidence.model_dump() | {"status": status})
    assert not QualityGate().evaluate(make_task("task-001"), revision, (bad,), review).passed
    bad_review = ReviewResult.model_validate(review.model_dump() | {"status": status})
    assert (
        not QualityGate().evaluate(make_task("task-001"), revision, (evidence,), bad_review).passed
    )


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "other"},
        {"criterion_id": "other"},
        {"check_id": "other"},
        {"criterion": "different behavior"},
        {"evidence_type": "review"},
        {"command": ["python", "-m", "pytest", "wrong/"]},
        {"plan_version": 2},
        {"context_revision": "project-002"},
        {"workspace_revision": "snapshot-002"},
    ],
)
def test_evidence_ownership_contract_and_freshness(
    make_task: Callable[..., TaskSpec],
    revision: Revision,
    evidence: Evidence,
    review: ReviewResult,
    change: dict[str, object],
) -> None:
    bad = Evidence.model_validate(evidence.model_dump() | change)
    assert not QualityGate().evaluate(make_task("task-001"), revision, (bad,), review).passed


def test_conflicting_duplicate_or_old_pass_cannot_hide_failure(
    make_task: Callable[..., TaskSpec],
    revision: Revision,
    evidence: Evidence,
    review: ReviewResult,
) -> None:
    failed = Evidence.model_validate(evidence.model_dump() | {"id": "failure", "status": "failed"})
    gate = QualityGate()
    task = make_task("task-001")
    assert not gate.evaluate(task, revision, (evidence, evidence), review).passed
    assert not gate.evaluate(task, revision, (evidence, failed), review).passed
    assert not gate.evaluate(task, revision, (failed, evidence), review).passed


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "other"},
        {"blocking": ["unsafe access"]},
        {"major": ["requirement missing"]},
        {
            "revision": {
                "plan_version": 2,
                "context_revision": "project-001",
                "workspace_revision": "snapshot-001",
            }
        },
        {
            "revision": {
                "plan_version": 1,
                "context_revision": "project-002",
                "workspace_revision": "snapshot-001",
            }
        },
        {
            "revision": {
                "plan_version": 1,
                "context_revision": "project-001",
                "workspace_revision": "snapshot-002",
            }
        },
    ],
)
def test_review_cannot_override_findings_or_wrong_revision(
    make_task: Callable[..., TaskSpec],
    revision: Revision,
    evidence: Evidence,
    review: ReviewResult,
    change: dict[str, object],
) -> None:
    bad = ReviewResult.model_validate(review.model_dump() | change)
    assert not QualityGate().evaluate(make_task("task-001"), revision, (evidence,), bad).passed


def test_review_minor_findings_are_preserved_without_blocking(
    make_task: Callable[..., TaskSpec],
    revision: Revision,
    evidence: Evidence,
    review: ReviewResult,
) -> None:
    report = ReviewResult.model_validate(
        review.model_dump() | {"minor": ["optional naming suggestion"]}
    )
    assert QualityGate().evaluate(make_task("task-001"), revision, (evidence,), report).passed


def test_review_only_contract_and_unexecuted_review_commands_are_rejected(
    make_task: Callable[..., TaskSpec],
    revision: Revision,
    evidence: Evidence,
    review: ReviewResult,
) -> None:
    task = make_task("task-001")
    data = task.model_dump()
    data["acceptance"]["criteria"][0]["required_check_ids"] = ("review",)
    data["acceptance"]["checks"] = (data["acceptance"]["checks"][1],)
    review_only = TaskSpec.model_validate(data)
    assert not QualityGate().evaluate(review_only, revision, (), review).passed
    data = task.model_dump()
    data["acceptance"]["checks"][1]["command"] = ("review-tool",)
    command_review = TaskSpec.model_validate(data)
    assert not QualityGate().evaluate(command_review, revision, (evidence,), review).passed


def test_multiple_criteria_require_distinct_coverage(
    make_task: Callable[..., TaskSpec],
    revision: Revision,
    evidence: Evidence,
    review: ReviewResult,
) -> None:
    data = make_task("task-001").model_dump()
    data["acceptance"]["criteria"] += (
        {"id": "errors", "description": "preserve errors", "required_check_ids": ["unit"]},
    )
    task = TaskSpec.model_validate(data)
    assert not QualityGate().evaluate(task, revision, (evidence,), review).passed
    other = Evidence.model_validate(
        evidence.model_dump()
        | {"id": "errors-evidence", "criterion_id": "errors", "criterion": "preserve errors"}
    )
    assert QualityGate().evaluate(task, revision, (evidence, other), review).passed


def test_fast_cannot_drop_explicit_review(
    make_task: Callable[..., TaskSpec],
    revision: Revision,
    evidence: Evidence,
) -> None:
    task = TaskSpec.model_validate(make_task("task-001").model_dump() | {"risk": {"level": "low"}})
    assert not QualityGate().evaluate(task, revision, (evidence,), None, WorkflowMode.FAST).passed
