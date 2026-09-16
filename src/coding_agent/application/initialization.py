"""Initialize or refresh source-backed knowledge without invoking a coding workflow."""

import re
from dataclasses import replace
from pathlib import Path
from typing import Literal

from ..context.explorer import END, START, explore
from ..core.knowledge import (
    USER_SOURCE,
    Digest,
    InitializationRevision,
    KnowledgeSnapshot,
    OpenQuestion,
    SourceFile,
)
from ..core.models import DomainModel, ScopePolicy
from ..core.provider import ModelProvider
from ..providers.runtime import ModelRuntime
from ..session.records import Sanitizer
from ..tools.initialization import InitializationRuntime, digest, user_text


class InitializationResult(DomainModel):
    status: Literal["initialized", "refreshed", "reused", "stale", "blocked"]
    revision: Digest
    guide: str
    journal: str
    changed_sources: tuple[str, ...] = ()
    questions: tuple[OpenQuestion, ...] = ()


async def initialize(
    root: Path,
    *,
    refresh: bool = False,
    rules: tuple[str, ...] = (),
    focus: tuple[str, ...] = (),
    scope: ScopePolicy | None = None,
    provider: ModelProvider | None = None,
    secrets: tuple[str, ...] = (),
) -> InitializationResult:
    """Shared first-use entry point. Stale/required decisions never become silent success."""
    for rule in rules:
        if not rule.strip() or len(rule) > 4000 or START in rule or END in rule:
            raise ValueError(
                "user rules must be nonempty, at most 4000 characters and contain no guide markers"
            )
    scope = scope or ScopePolicy()
    sanitizer = Sanitizer(secrets)
    with InitializationRuntime(root, scope, sanitizer=sanitizer) as runtime:
        state = runtime.load()
        old = state.snapshot
        if rules:
            existing = set(re.split(r"\r?\n\s*\r?\n", user_text(state.prefix, state.suffix)))
            additions = "\n\n".join(
                rule for rule in dict.fromkeys(r.strip() for r in rules) if rule not in existing
            )
            if additions:
                state = replace(state, prefix=state.prefix + "\n\n" + additions + "\n\n")
            refresh = True
        if old is None and not state.prefix and not state.suffix:
            state = replace(state, suffix="\n")
        repository = runtime.inspect(focus, old.repository if old is not None else None)
        raw_user = user_text(state.prefix, state.suffix)
        if raw_user.strip():
            sanitized = sanitizer.text(raw_user)
            if len(sanitized.encode()) > 32768:
                raise ValueError(
                    "user guidance exceeds 32 KiB; use referenced project instruction files"
                )
            source = SourceFile(
                path=USER_SOURCE, sha256=digest(raw_user), role="user", text=sanitized
            )
            repository = repository.model_copy(update={"sources": (*repository.sources, source)})
        runtime.revision = InitializationRevision(
            context_revision=old.revision if old is not None else "uninitialized",
            workspace_revision=digest(repository.model_dump_json()),
        )
        current = {s.path: s.sha256 for s in repository.sources}
        earlier = {s.path: s.sha256 for s in old.repository.sources} if old is not None else {}
        changed = sorted(
            path
            for path in current.keys() | earlier.keys()
            if current.get(path) != earlier.get(path)
        )
        if old is not None:
            if repository.paths != old.repository.paths:
                changed.append("repository inventory")
            if scope != old.scope or focus != old.repository.focus:
                changed.append("exploration scope")
        same = (
            old is not None
            and old.repository == repository
            and old.scope == scope
            and old.user_prefix == sanitizer.text(state.prefix)
            and old.user_suffix == sanitizer.text(state.suffix)
        )
        mode = "model" if provider is not None else "offline"

        def result(status: str, snapshot: KnowledgeSnapshot) -> InitializationResult:
            return InitializationResult.model_validate(
                dict(
                    status=status,
                    revision=snapshot.revision,
                    guide=str(runtime.control / "project.md"),
                    journal=str(runtime.journal.directory / "events.jsonl"),
                    changed_sources=tuple(changed),
                    questions=snapshot.summary.questions,
                )
            )

        if old is not None and same and (old.mode == mode or not refresh and provider is None):
            return result(
                "blocked" if any(q.blocking for q in old.summary.questions) else "reused", old
            )
        if old is not None and not refresh:
            return result("stale", old)
        model = (
            ModelRuntime(provider, journal=runtime.journal, read_revision=lambda: runtime.revision)
            if provider is not None
            else None
        )
        summary = await explore(repository, model=model, previous=old)
        snapshot = KnowledgeSnapshot.model_validate(
            dict(
                previous_revision=old.revision if old is not None else None,
                summary=summary,
                repository=repository,
                scope=scope,
                mode=mode,
                user_prefix=sanitizer.text(state.prefix),
                user_suffix=sanitizer.text(state.suffix),
            )
        )
        runtime.publish(snapshot, state)
        status = (
            "blocked"
            if any(q.blocking for q in summary.questions)
            else ("refreshed" if old is not None else "initialized")
        )
        return result(status, snapshot)
