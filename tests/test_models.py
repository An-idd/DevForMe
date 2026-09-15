from collections.abc import Callable

import pytest
from pydantic import ValidationError

from coding_agent.core import (
    AcceptanceCheck,
    AcceptanceCriterion,
    AcceptanceSpec,
    Evidence,
    EvidenceStatus,
    PermissionPolicy,
    RequirementContract,
    RiskLevel,
    RiskProfile,
    ScopePolicy,
    TaskSpec,
)


def test_requirement_round_trip_and_risk() -> None:
    contract = RequirementContract.model_validate(
        {
            "id": "req-001",
            "title": "Extract inventory",
            "goal": "Separate inventory responsibilities",
            "functional_requirements": ["preserve public API results and error behavior"],
            "constraints": ["no new dependencies"],
            "non_goals": ["change data formats"],
            "risk": {"level": "high", "public_api": "medium"},
            "suggested_validation": ["existing regression tests"],
        }
    )
    assert contract.risk.level is RiskLevel.HIGH
    assert contract.risk.security is None
    assert contract.functional_requirements == ("preserve public API results and error behavior",)
    assert RequirementContract.model_validate_json(contract.model_dump_json()) == contract


@pytest.mark.parametrize(
    "change",
    [
        {"id": ""},
        {"id": "req with spaces"},
        {"title": "  "},
        {"goal": ""},
        {"functional_requirements": []},
        {"functional_requirements": [""]},
        {"risk": {}},
        {"unknown": "discarding this could hide a rule"},
    ],
)
def test_invalid_requirement(change: dict[str, object]) -> None:
    data = {
        "id": "req-001",
        "title": "A change",
        "goal": "Preserve behavior",
        "functional_requirements": ["preserve behavior"],
        "risk": {"level": "low"},
    }
    with pytest.raises(ValidationError):
        RequirementContract.model_validate(data | change)


@pytest.mark.parametrize("dimension", ["security", "database", "public_api", "architecture"])
def test_risk_cannot_understate_any_dimension(dimension: str) -> None:
    with pytest.raises(ValidationError, match="overall risk"):
        RiskProfile.model_validate({"level": "low", dimension: "high"})
    assert (
        RiskProfile.model_validate({"level": "high", dimension: "medium"}).level is RiskLevel.HIGH
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"level": "critical"},
        {"level": "high", "security": True},
        {"level": "high", "api": "medium"},
    ],
)
def test_invalid_risk(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RiskProfile.model_validate(data)


def test_permission_defaults_are_closed() -> None:
    policy = PermissionPolicy()
    assert not policy.network
    assert policy.shell == policy.database == "deny"
    assert ScopePolicy().allowed == ()
    assert PermissionPolicy(network=True, shell="restricted", database="test_only").network


@pytest.mark.parametrize(
    "data",
    [
        {"network": "false"},
        {"network": 1},
        {"shell": "allow"},
        {"database": "production"},
        {"external_directory": True},
    ],
)
def test_invalid_permissions(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PermissionPolicy.model_validate(data)


@pytest.mark.parametrize(
    "pattern",
    [
        "../secret",
        "src/../../secret",
        "/etc",
        "C:/repo",
        "C:repo",
        "\\\\server\\share",
        "src\\x",
        ".",
        "./src/**",
        "src//x",
        "src/.",
        "src/x:stream",
        "",
    ],
)
@pytest.mark.parametrize("field", ["allowed", "forbidden"])
def test_scope_rejects_non_relative_patterns(field: str, pattern: str) -> None:
    with pytest.raises(ValidationError):
        ScopePolicy.model_validate({field: [pattern]})


def test_scope_patterns_are_preserved() -> None:
    scope = ScopePolicy(
        allowed=("src/**", "tests/test_*.py", "目录/*.py"), forbidden=("src/private/**",)
    )
    assert scope.allowed == ("src/**", "tests/test_*.py", "目录/*.py")
    assert scope.forbidden == ("src/private/**",)


@pytest.mark.parametrize(
    "change",
    [
        {"id": ""},
        {"requirements": []},
        {"max_attempts": 0},
        {"max_attempts": True},
        {"max_attempts": "3"},
        {"depends_on": ["other", "other"]},
        {"depends_on": ["task-001"]},
        {"state": "VERIFIED"},
    ],
)
def test_invalid_task(make_task: Callable[..., TaskSpec], change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(make_task("task-001").model_dump() | change)


def test_task_contract_round_trip_is_immutable(make_task: Callable[..., TaskSpec]) -> None:
    task = make_task("task-001")
    assert TaskSpec.model_validate_json(task.model_dump_json()) == task
    assert task.max_attempts == 3
    with pytest.raises(ValidationError, match="frozen"):
        task.goal = "silently broaden scope"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="frozen"):
        task.scope.allowed = ("**",)  # type: ignore[misc]
    assert isinstance(task.depends_on, tuple)
    assert isinstance(task.acceptance.criteria, tuple)


@pytest.mark.parametrize("field", ["criteria", "checks"])
def test_acceptance_rejects_empty_and_duplicate_ids(acceptance: AcceptanceSpec, field: str) -> None:
    data = acceptance.model_dump(mode="json")
    items = data[field]
    with pytest.raises(ValidationError):
        AcceptanceSpec.model_validate(data | {field: []})
    with pytest.raises(ValidationError, match="duplicate"):
        AcceptanceSpec.model_validate(data | {field: [*items, items[0]]})


@pytest.mark.parametrize("check_ids", [(), ("missing",), ("unit", "unit"), ("unit",)])
def test_acceptance_references_are_complete(
    acceptance: AcceptanceSpec, check_ids: tuple[str, ...]
) -> None:
    data = acceptance.model_dump()
    data["criteria"] = [
        {
            "id": "behavior",
            "description": "preserve public behavior",
            "required_check_ids": check_ids,
        }
    ]
    with pytest.raises(ValidationError):
        AcceptanceSpec.model_validate(data)


def test_checks_can_support_multiple_criteria(acceptance: AcceptanceSpec) -> None:
    other = AcceptanceCriterion(
        id="errors", description="preserve errors", required_check_ids=("unit",)
    )
    spec = AcceptanceSpec(criteria=(*acceptance.criteria, other), checks=acceptance.checks)
    assert spec.criteria[1].required_check_ids == ("unit",)


@pytest.mark.parametrize("command", [None, [], [""], ["  "], [23], "python -m pytest"])
def test_executable_check_requires_argument_array(command: object) -> None:
    with pytest.raises(ValidationError):
        AcceptanceCheck.model_validate(
            {"id": "test", "description": "tests", "evidence_type": "test", "command": command}
        )


def test_command_arguments_preserve_spaces_and_empty_strings() -> None:
    check = AcceptanceCheck(
        id="cmd",
        description="argument preservation",
        evidence_type="command",
        command=("python", "-c", "", " leading and trailing "),
    )
    assert check.command == ("python", "-c", "", " leading and trailing ")


@pytest.mark.parametrize("status", list(EvidenceStatus))
def test_evidence_outcomes_round_trip(
    evidence_data: dict[str, object], acceptance: AcceptanceSpec, status: EvidenceStatus
) -> None:
    record = Evidence.model_validate(evidence_data | {"status": status, "result": status.value})
    assert record.passed is (status is EvidenceStatus.PASSED)
    assert "passed" not in record.model_dump()
    assert Evidence.model_validate_json(record.model_dump_json()) == record
    acceptance.validate_evidence(record)  # Linkage is valid even when the check did not pass.


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "task_id",
        "plan_version",
        "context_revision",
        "workspace_revision",
        "criterion_id",
        "criterion",
        "check_id",
        "evidence_type",
        "command",
        "result",
        "status",
        "source",
        "timestamp",
    ],
)
def test_evidence_does_not_invent_required_facts(
    evidence_data: dict[str, object], field: str
) -> None:
    evidence_data.pop(field)
    with pytest.raises(ValidationError):
        Evidence.model_validate(evidence_data)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "success"},
        {"status": True},
        {"status": "stale"},
        {"plan_version": 0},
        {"plan_version": -1},
        {"plan_version": True},
        {"plan_version": "1"},
        {"context_revision": ""},
        {"workspace_revision": "  "},
        {"criterion_id": "bad id"},
        {"check_id": ""},
        {"task_id": ""},
        {"id": ""},
        {"criterion": " "},
        {"evidence_type": "model_claim"},
        {"command": []},
        {"result": ""},
        {"source": ""},
        {"timestamp": "2026-09-15T09:00:00"},
        {"timestamp": "not-a-time"},
        {"artifacts": [""]},
        {"passed": True},
    ],
)
def test_invalid_evidence(evidence_data: dict[str, object], change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(evidence_data | change)


@pytest.mark.parametrize(
    "change",
    [
        {"criterion_id": "other"},
        {"check_id": "other"},
        {"criterion": "changed criterion"},
        {"evidence_type": "review"},
        {"command": ["python", "-m", "pytest", "wrong_tests/"]},
    ],
)
def test_evidence_must_match_acceptance(
    evidence_data: dict[str, object], acceptance: AcceptanceSpec, change: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        acceptance.validate_evidence(Evidence.model_validate(evidence_data | change))


def test_review_is_distinct_from_test_execution(
    evidence_data: dict[str, object], acceptance: AcceptanceSpec
) -> None:
    review = Evidence.model_validate(
        evidence_data
        | {
            "check_id": "review",
            "evidence_type": "review",
            "command": None,
            "source": "review-results/001",
        }
    )
    acceptance.validate_evidence(review)
    assert review.evidence_type == "review"
    assert review.command is None


def test_evidence_cannot_be_mutated_into_a_pass(evidence_data: dict[str, object]) -> None:
    record = Evidence.model_validate(evidence_data | {"status": "failed", "result": "1 failed"})
    with pytest.raises(ValidationError, match="frozen"):
        record.status = EvidenceStatus.PASSED  # type: ignore[misc]
    assert not record.passed
