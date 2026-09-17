"""Versioned supplemental guidance; never review findings or acceptance evidence."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from .knowledge import Digest
from .models import DomainModel
from .paths import relative_parts


class ReviewRulesOperation(DomainModel):
    kind: Literal["review_rules"] = "review_rules"
    paths: Annotated[tuple[str, ...], Field(min_length=1, max_length=256)]
    bundle_sha256: Digest

    @field_validator("paths")
    @classmethod
    def valid_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        for path in paths:
            relative_parts(path)
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate review paths")
        return paths


class ReviewRuleGroup(DomainModel):
    paths: tuple[str, ...]
    pattern: str
    source_path: str
    source_sha256: Digest
    text: str


class ReviewGuidance(DomainModel):
    provider: Literal["open-code-review"] = "open-code-review"
    authority: Literal["supplemental"] = "supplemental"
    source_commit: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    bundle_sha256: Digest
    limitations: tuple[str, ...]
    groups: tuple[ReviewRuleGroup, ...]
