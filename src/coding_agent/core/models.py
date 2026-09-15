"""Validated contracts; execution and authoritative records belong to the runtime."""

from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]


def _command(arguments: tuple[str, ...]) -> tuple[str, ...]:
    if not arguments[0].strip():
        raise ValueError("command executable must not be blank")
    return arguments


Command = Annotated[tuple[str, ...], Field(min_length=1), AfterValidator(_command)]


def _relative_pattern(value: str) -> str:
    if (
        PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or "\\" in value
        or ":" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("scope patterns must use repository-relative paths with '/' separators")
    return value


ScopePattern = Annotated[NonEmptyStr, AfterValidator(_relative_pattern)]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, revalidate_instances="always"
    )


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskProfile(DomainModel):
    level: RiskLevel
    security: RiskLevel | None = None
    database: RiskLevel | None = None
    public_api: RiskLevel | None = None
    architecture: RiskLevel | None = None

    @model_validator(mode="after")
    def validate_level(self) -> Self:
        levels = tuple(RiskLevel)
        dimensions = (self.security, self.database, self.public_api, self.architecture)
        if any(
            value is not None and levels.index(value) > levels.index(self.level)
            for value in dimensions
        ):
            raise ValueError("overall risk level must cover every assessed dimension")
        return self


class RequirementContract(DomainModel):
    id: Identifier
    title: NonEmptyStr
    goal: NonEmptyStr
    functional_requirements: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
    constraints: tuple[NonEmptyStr, ...] = ()
    non_goals: tuple[NonEmptyStr, ...] = ()
    risk: RiskProfile
    suggested_validation: tuple[NonEmptyStr, ...] = ()


class ScopePolicy(DomainModel):
    """Declarative file patterns, not a filesystem sandbox. Forbidden wins in P03."""

    allowed: tuple[ScopePattern, ...] = ()
    forbidden: tuple[ScopePattern, ...] = ()


class PermissionPolicy(DomainModel):
    """Requested capabilities; enforcement and approval are deferred to P03."""

    network: StrictBool = False
    shell: Literal["deny", "restricted"] = "deny"
    database: Literal["deny", "test_only"] = "deny"


class EvidenceType(StrEnum):
    TEST = "test"
    BUILD = "build"
    LINT = "lint"
    STATIC_ANALYSIS = "static_analysis"
    REVIEW = "review"
    COMMAND = "command"
    DIFF = "diff"


class EvidenceStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    INCONCLUSIVE = "inconclusive"


class AcceptanceCheck(DomainModel):
    id: Identifier
    description: NonEmptyStr
    evidence_type: EvidenceType
    command: Command | None = None

    @model_validator(mode="after")
    def validate_command(self) -> Self:
        if self.evidence_type not in {EvidenceType.REVIEW, EvidenceType.DIFF} and not self.command:
            raise ValueError("executable acceptance checks require command arguments")
        return self


class AcceptanceCriterion(DomainModel):
    id: Identifier
    description: NonEmptyStr
    required_check_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_check_ids(self) -> Self:
        if len(set(self.required_check_ids)) != len(self.required_check_ids):
            raise ValueError("duplicate required check IDs")
        return self


class Evidence(DomainModel):
    id: Identifier
    task_id: Identifier
    plan_version: PositiveInt
    context_revision: NonEmptyStr
    workspace_revision: NonEmptyStr
    criterion_id: Identifier
    criterion: NonEmptyStr
    check_id: Identifier
    evidence_type: EvidenceType
    command: Command | None
    result: NonEmptyStr
    status: EvidenceStatus
    artifacts: tuple[NonEmptyStr, ...] = ()
    source: NonEmptyStr
    timestamp: AwareDatetime

    @property
    def passed(self) -> bool:
        return self.status is EvidenceStatus.PASSED


class AcceptanceSpec(DomainModel):
    criteria: Annotated[tuple[AcceptanceCriterion, ...], Field(min_length=1)]
    checks: Annotated[tuple[AcceptanceCheck, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        criterion_ids = [criterion.id for criterion in self.criteria]
        check_ids = [check.id for check in self.checks]
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("duplicate criterion IDs")
        if len(set(check_ids)) != len(check_ids):
            raise ValueError("duplicate check IDs")
        referenced = {check_id for item in self.criteria for check_id in item.required_check_ids}
        if missing := referenced - set(check_ids):
            raise ValueError(f"missing acceptance checks: {sorted(missing)}")
        if unused := set(check_ids) - referenced:
            raise ValueError(f"acceptance checks must belong to a criterion: {sorted(unused)}")
        return self

    def validate_evidence(self, evidence: Evidence) -> None:
        """Check contract linkage only; this neither authenticates nor passes evidence."""
        criterion = next((item for item in self.criteria if item.id == evidence.criterion_id), None)
        if criterion is None:
            raise ValueError(f"unknown criterion ID: {evidence.criterion_id}")
        if evidence.check_id not in criterion.required_check_ids:
            raise ValueError(f"check {evidence.check_id} does not cover {criterion.id}")
        check = next(item for item in self.checks if item.id == evidence.check_id)
        if evidence.criterion != criterion.description:
            raise ValueError("evidence criterion description does not match the contract")
        if evidence.evidence_type != check.evidence_type:
            raise ValueError("evidence type does not match the required check")
        if check.command is not None and evidence.command != check.command:
            raise ValueError("evidence command does not match the required check")


class TaskSpec(DomainModel):
    id: Identifier
    title: NonEmptyStr
    goal: NonEmptyStr
    depends_on: tuple[Identifier, ...] = ()
    scope: ScopePolicy
    requirements: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
    acceptance: AcceptanceSpec
    risk: RiskProfile
    permissions: PermissionPolicy
    max_attempts: PositiveInt = 3

    @model_validator(mode="after")
    def validate_dependencies(self) -> Self:
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("duplicate dependency IDs")
        if self.id in self.depends_on:
            raise ValueError(f"task {self.id} cannot depend on itself")
        return self
