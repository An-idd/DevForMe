"""Content identity and controller-only workspace operations."""

from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .models import DomainModel
from .paths import relative_parts

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class WorkspaceOperation(DomainModel):
    kind: Literal["workspace"] = "workspace"
    operation: Literal["prepare", "status", "diff", "snapshot", "reset", "cleanup"]
    expected_revision: Digest | None = None
    target_revision: Digest | None = None

    @model_validator(mode="after")
    def validate_revisions(self) -> Self:
        if (self.operation in {"reset", "cleanup"}) != (self.expected_revision is not None):
            raise ValueError("reset/cleanup require the observed workspace revision")
        if (self.operation == "reset") != (self.target_revision is not None):
            raise ValueError("only reset requires a saved target revision")
        return self


class SnapshotFile(DomainModel):
    path: str
    sha256: Digest
    size_bytes: Annotated[int, Field(strict=True, ge=0)]
    mode: Literal[0o100644, 0o100755]

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        relative_parts(self.path)
        return self


class WorkspaceSnapshot(DomainModel):
    """The selection policy is part of identity; timestamps and Git HEAD are not."""

    files: tuple[SnapshotFile, ...]
    excluded: tuple[str, ...]
    forbidden: tuple[str, ...]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        paths = tuple(item.path for item in self.files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("snapshot paths must be unique and sorted")
        return self

    @property
    def revision(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


class WorkspaceStatus(DomainModel):
    baseline_revision: Digest
    current_revision: Digest
    added: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]
