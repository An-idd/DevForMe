"""Sourced project knowledge. Discovery never represents verification evidence."""

from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, field_validator, model_validator

from .models import Command, DomainModel, NonEmptyStr, ScopePolicy
from .paths import relative_parts

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=4000)]
USER_SOURCE = "user:guidance"


def source_path(value: str) -> str:
    if value != USER_SOURCE:
        relative_parts(value)
    return value


class InitializationRevision(DomainModel):
    """Pre-plan records cannot be confused with executable workflow revisions."""

    phase: Literal["initialization"] = "initialization"
    context_revision: NonEmptyStr
    workspace_revision: NonEmptyStr


class InitializationOperation(DomainModel):
    kind: Literal["initialization"] = "initialization"
    operation: Literal["inspect", "publish"]
    input_sha256: Digest


class SourceFile(DomainModel):
    path: str
    sha256: Digest
    role: Literal["instruction", "document", "config", "code", "user"]
    text: Annotated[str, Field(max_length=65536)]
    truncated: StrictBool = False

    _path = field_validator("path")(source_path)


class SourceReference(DomainModel):
    path: str
    line: Annotated[int, Field(strict=True, ge=1)]
    end_line: Annotated[int, Field(strict=True, ge=1)]
    quote: Text

    _path = field_validator("path")(source_path)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_line < self.line:
            raise ValueError("source range ends before it starts")
        return self


class KnowledgeEntry(DomainModel):
    authority: Literal["fact", "rule", "assumption"]
    area: Literal["overview", "modules", "development", "boundaries", "scope", "coupling"]
    text: Text
    sources: Annotated[tuple[SourceReference, ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def sourced(self) -> Self:
        if self.authority != "assumption" and not self.sources:
            raise ValueError("facts and explicit rules require sources")
        return self

    @property
    def id(self) -> str:
        # Line movement leaves a rule's reference stable; content/source changes do not.
        identity = [self.authority, self.text, *(s.path for s in self.sources)]
        return f"{self.authority}-{sha256(chr(0).join(identity).encode()).hexdigest()[:16]}"


class ValidationCommand(DomainModel):
    purpose: Literal["test", "lint", "typecheck", "build"]
    argv: Command
    cwd: str = "."
    prerequisites: tuple[Text, ...] = ()
    sources: Annotated[tuple[SourceReference, ...], Field(min_length=1, max_length=8)]
    status: Literal["discovered"] = "discovered"

    @field_validator("cwd")
    @classmethod
    def relative_directory(cls, value: str) -> str:
        relative_parts(value, root_allowed=True)
        return value


class OpenQuestion(DomainModel):
    text: Text
    required: StrictBool = False
    sources: tuple[SourceReference, ...] = ()
    answer: Text | None = None

    @model_validator(mode="after")
    def sourced_answer(self) -> Self:
        if self.answer is not None and not self.sources:
            raise ValueError("an answer requires explicit instruction or user sources")
        return self

    @property
    def blocking(self) -> bool:
        return self.required and self.answer is None


class RepoSummary(DomainModel):
    entries: Annotated[tuple[KnowledgeEntry, ...], Field(max_length=256)] = ()
    commands: Annotated[tuple[ValidationCommand, ...], Field(max_length=32)] = ()
    questions: Annotated[tuple[OpenQuestion, ...], Field(max_length=64)] = ()
    gaps: Annotated[tuple[Text, ...], Field(max_length=64)] = ()

    def check_sources(self, sources: tuple[SourceFile, ...]) -> None:
        available = {item.path: item for item in sources}
        if len(available) != len(sources):
            raise ValueError("duplicate source paths")
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("duplicate knowledge entries")
        items: tuple[KnowledgeEntry | ValidationCommand | OpenQuestion, ...] = (
            *self.entries,
            *self.commands,
            *self.questions,
        )
        for item in items:
            for reference in item.sources:
                source = available.get(reference.path)
                if source is None:
                    raise ValueError(f"unknown source: {reference.path}")
                lines = source.text.splitlines()
                if reference.end_line > len(lines):
                    raise ValueError("source range exceeds observed content")
                selected = "\n".join(lines[reference.line - 1 : reference.end_line])
                if reference.quote not in selected:
                    raise ValueError("source quote is not present in its cited range")
                if isinstance(item, KnowledgeEntry) and item.authority == "rule":
                    if source.role not in {"instruction", "user"} or item.text != reference.quote:
                        raise ValueError("explicit rules must quote instruction or user sources")
                if isinstance(item, OpenQuestion) and item.answer is not None:
                    if source.role not in {"instruction", "user"} or item.answer != reference.quote:
                        raise ValueError("answers must quote instruction or user sources")


class RepositoryInput(DomainModel):
    paths: tuple[str, ...]
    sources: tuple[SourceFile, ...]
    gaps: tuple[Text, ...] = ()
    focus: tuple[str, ...] = ()

    @field_validator("paths", "focus")
    @classmethod
    def relative_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            relative_parts(value)
        if len(set(values)) != len(values):
            raise ValueError("duplicate repository paths")
        return values


class KnowledgeSnapshot(DomainModel):
    previous_revision: Digest | None
    summary: RepoSummary
    repository: RepositoryInput
    scope: ScopePolicy
    mode: Literal["offline", "model"]
    user_prefix: str
    user_suffix: str

    @model_validator(mode="after")
    def valid_sources(self) -> Self:
        self.summary.check_sources(self.repository.sources)
        return self

    @property
    def revision(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


class KnowledgeMetadata(DomainModel):
    """An index of an immutable snapshot, never another editable rule document."""

    format_version: Literal[1] = 1
    revision: Digest
    generated_sha256: Digest
    user_sha256: Digest
    source_fingerprints: dict[str, Digest]
