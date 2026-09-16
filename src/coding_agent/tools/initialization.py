"""Controller-only initialization tools with the existing durable request/result journal."""

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from ..context.explorer import END, START, render_summary
from ..core.knowledge import (
    USER_SOURCE,
    InitializationOperation,
    InitializationRevision,
    KnowledgeMetadata,
    KnowledgeSnapshot,
    RepositoryInput,
    SourceFile,
)
from ..core.models import ScopePolicy
from ..core.paths import CONTROL_NAMES, path_permitted, relative_parts
from ..core.tools import Decision, ToolRequest, ToolResult
from ..core.workflow import EventWriteError, WorkflowEvent
from ..runtime import _fileio as io
from ..runtime._snapshots import DEFAULT_EXCLUDED, excluded_path
from ..runtime.filesystem import SafeFiles
from ..session.records import JsonlJournal, Sanitizer, inspect_journal

EXCLUDED = (
    *DEFAULT_EXCLUDED,
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/dist/**",
    "**/build/**",
    "**/target/**",
    "**/*.egg-info/**",
    "**/.env*",
    "**/*.pem",
    "**/*.key",
    "**/id_rsa*",
    "**/.npmrc",
    "**/auth.json",
    "**/credentials*",
    "**/secrets/**",
    "**/.ssh/**",
    "**/.aws/**",
)
MAX_ENTRIES = 10_000
MAX_SOURCES = 24
MAX_CONTEXT = 196_608
CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cs"}
INSTRUCTIONS = {"agents.md", "contributing.md", "claude.md"}
CONFIGS = {"pyproject.toml", "package.json", "cargo.toml", "go.mod", "pytest.ini", "setup.cfg"}


def digest(value: str | bytes) -> str:
    return sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def user_text(prefix: str, suffix: str) -> str:
    return prefix + "\n" + suffix


@dataclass(frozen=True)
class KnowledgeState:
    snapshot: KnowledgeSnapshot | None
    prefix: str
    suffix: str
    guide: bytes | None
    metadata: bytes | None


class InitializationRuntime:
    """No plan required; the application owns this capability, never the Explorer."""

    def __init__(
        self, root: Path, scope: ScopePolicy, *, sanitizer: Sanitizer | None = None
    ) -> None:
        self.files = SafeFiles(root, scope)
        self.root = self.files.root
        self.scope = scope
        self.sanitizer = sanitizer or Sanitizer()
        self.control = self.root / ".agent"
        self.session_id = "init-" + uuid4().hex
        self.revision = InitializationRevision(
            context_revision="uninitialized", workspace_revision="not-yet-inspected"
        )
        self._agent_fd: int | None = None
        self._lock_fd: int | None = None
        self.journal: JsonlJournal

    def __enter__(self) -> "InitializationRuntime":
        try:
            with self.files.directory() as root_fd:
                try:
                    io.mkdir_at(root_fd, ".agent")
                except FileExistsError:
                    pass
                self._agent_fd = io.directory_at(root_fd, ".agent")
            try:
                self._lock_fd = io.open_at(
                    self._agent_fd, "init.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | io.NOFOLLOW
                )
            except FileExistsError:
                raise ValueError(
                    "initialization lock exists; inspect .agent/init-* records and the owning "
                    "process before removing a stale init.lock"
                ) from None
            os.write(self._lock_fd, self.session_id.encode())
            os.fsync(self._lock_fd)
            io.mkdir_at(self._agent_fd, self.session_id)
            self.journal = JsonlJournal(
                self.control / self.session_id, self.session_id, sanitizer=self.sanitizer
            )
            return self
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *args: object) -> None:
        retain_lock = False
        if hasattr(self, "journal"):
            self.journal.close()
            try:
                view = inspect_journal(self.journal.directory / "events.jsonl")
                retain_lock = bool(view.unresolved or view.incomplete_tail)
            except (OSError, ValueError, EventWriteError):
                retain_lock = True
        try:
            if not retain_lock and self._lock_fd is not None and self._agent_fd is not None:
                opened = os.fstat(self._lock_fd)
                current = io.stat_at(self._agent_fd, "init.lock")
                if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    raise ValueError("initialization lock changed; preserve it for inspection")
                io.unlink(self._agent_fd, "init.lock")
        finally:
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            if self._agent_fd is not None:
                os.close(self._agent_fd)
                self._agent_fd = None

    def _check_control(self) -> None:
        if self._agent_fd is None:
            raise ValueError("initialization runtime is not open")
        with self.files.directory() as root_fd:
            current = io.directory_at(root_fd, ".agent")
            try:
                opened, now = os.fstat(self._agent_fd), os.fstat(current)
                if (opened.st_dev, opened.st_ino) != (now.st_dev, now.st_ino):
                    raise ValueError("controller directory changed")
            finally:
                os.close(current)

    def _optional(self, name: str) -> bytes | None:
        self._check_control()
        assert self._agent_fd is not None
        try:
            fd = io.open_at(self._agent_fd, name, os.O_RDONLY | io.NOFOLLOW | io.NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("controller input must be a regular unaliased file")
            data = stream.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("controller input exceeds 8 MiB")
        return data

    def load(self) -> KnowledgeState:
        guide = self._optional("project.md")
        metadata = self._optional("project.json")
        text = guide.decode("utf-8") if guide is not None else ""
        if metadata is None:
            if START in text or END in text:
                raise ValueError(
                    "guide has generated markers but no metadata; inspect the last init journal "
                    "and preserved knowledge snapshots before reconciling"
                )
            return KnowledgeState(None, text, "", guide, metadata)
        if guide is None or text.count(START) != 1 or text.count(END) != 1:
            raise ValueError("project guide/metadata disagree; inspect the last init journal")
        prefix, rest = text.split(START)
        generated, suffix = rest.split(END)
        # Text editors may change line endings without editing generated content.
        generated = (START + generated + END).replace("\r\n", "\n")
        index = KnowledgeMetadata.model_validate_json(metadata)
        data = self._optional(f"knowledge-{index.revision}.json")
        if data is None:
            raise ValueError("referenced knowledge snapshot is missing")
        snapshot = KnowledgeSnapshot.model_validate_json(data)
        if digest(data) != index.revision or snapshot.revision != index.revision:
            raise ValueError("immutable knowledge snapshot changed")
        expected_user = next(
            (s.sha256 for s in snapshot.repository.sources if s.path == USER_SOURCE),
            digest(user_text(snapshot.user_prefix, snapshot.user_suffix)),
        )
        if (
            index.user_sha256 != expected_user
            or digest(generated) != index.generated_sha256
            or generated != render_summary(snapshot.summary)
            or index.source_fingerprints != {s.path: s.sha256 for s in snapshot.repository.sources}
        ):
            raise ValueError(
                "generated guide or metadata changed; preserve edits outside the generated markers "
                "and restore the generated section from its immutable snapshot before refresh"
            )
        self.revision = InitializationRevision(
            context_revision=index.revision, workspace_revision="not-yet-inspected"
        )
        return KnowledgeState(snapshot, prefix, suffix, guide, metadata)

    def _record[T](self, operation: InitializationOperation, action: Callable[[], T]) -> T:
        request = ToolRequest(request_id="init-" + uuid4().hex, invocation=operation)
        journal = self.journal
        journal.begin_operation(request.request_id)
        try:
            revision = self.revision
            sequence = journal.next_sequence
            started = datetime.now(UTC)
            requested = WorkflowEvent(
                event_id=f"{journal.session_id}:{sequence}",
                sequence=sequence,
                timestamp=started,
                session_id=journal.session_id,
                task_id=None,
                revision=revision,
                kind="tool_requested",
                reason=f"controller initialization {operation.operation}",
                tool_request=request,
            )
            journal.write(requested)
            journal.check_writable()
            error: BaseException | None = None
            value = None
            try:
                value = action()
                status, reason = "succeeded", f"initialization {operation.operation} completed"
            except EventWriteError:
                journal.invalidate()
                raise
            except (OSError, ValueError) as caught:
                error = caught
                status, reason = "failed", self.sanitizer.text(str(caught))
            except (KeyboardInterrupt, SystemExit) as caught:
                error = caught
                status, reason = "interrupted", "initialization interrupted; inspect actual state"
            result = ToolResult.model_validate(
                dict(
                    request_id=request.request_id,
                    request_event_id=requested.event_id,
                    decision=Decision.ALLOW,
                    status=status,
                    executed=True,
                    reason=reason,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                )
            )
            sequence = journal.next_sequence
            journal.write(
                WorkflowEvent(
                    event_id=f"{journal.session_id}:{sequence}",
                    sequence=sequence,
                    timestamp=result.finished_at,
                    session_id=journal.session_id,
                    task_id=None,
                    revision=revision,
                    kind="tool_finished",
                    reason=result.reason,
                    tool_result=result,
                )
            )
            if error is not None:
                raise error
            assert value is not None
            return value
        finally:
            journal.end_operation()

    def inspect(
        self, focus: tuple[str, ...], previous: RepositoryInput | None = None
    ) -> RepositoryInput:
        parameters = repr((self.scope.model_dump(), focus, previous.paths if previous else ()))
        result = self._record(
            InitializationOperation(operation="inspect", input_sha256=digest(parameters)),
            lambda: self._capture(focus, previous),
        )
        self.revision = InitializationRevision(
            context_revision=self.revision.context_revision,
            workspace_revision=digest(result.model_dump_json()),
        )
        return result

    def _capture(self, focus: tuple[str, ...], previous: RepositoryInput | None) -> RepositoryInput:
        for path in focus:
            relative_parts(path)
            if not path_permitted(path, self.scope) or excluded_path(path, EXCLUDED):
                raise ValueError(f"focus is outside readable scope: {path}")
        paths: list[str] = []
        gaps: list[str] = []
        count = 0

        def walk(directory: str) -> None:
            nonlocal count
            with self.files.directory(directory) as fd:
                with io.entries(fd) as listing:
                    names = []
                    for entry in listing:
                        count += 1
                        if count > MAX_ENTRIES:
                            raise ValueError(
                                "repository inventory exceeds 10000 entries; narrow forbidden scope"
                            )
                        names.append(entry.name)
                for name in sorted(names):
                    path = name if directory == "." else f"{directory}/{name}"
                    if (
                        name.casefold() in CONTROL_NAMES
                        or not path_permitted(path, self.scope)
                        or excluded_path(path, EXCLUDED)
                    ):
                        continue
                    info = io.stat_at(fd, name)
                    if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
                        raise ValueError(f"repository link/reparse point is unsupported: {path}")
                    if stat.S_ISDIR(info.st_mode):
                        walk(path)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        paths.append(path)
                    else:
                        raise ValueError(f"repository requires regular unaliased files: {path}")

        walk(".")
        paths.sort()
        missing = [p for p in focus if not any(f == p or f.startswith(p + "/") for f in paths)]
        if missing:
            raise ValueError("focus paths are missing or excluded: " + ", ".join(missing))
        mandatory = {p for p in paths if Path(p).name.casefold() in INSTRUCTIONS}
        mandatory.update(p for p in paths if any(p == f or p.startswith(f + "/") for f in focus))
        if previous is not None:
            mandatory.update(s.path for s in previous.sources if s.path in paths)
        if len(mandatory) > MAX_SOURCES:
            raise ValueError(
                "required exploration exceeds 24 files; narrow task focus or readable scope"
            )

        def priority(path: str) -> tuple[int, int, str]:
            name = Path(path).name.lower()
            parts = Path(path).parts
            rank = (
                0
                if name in INSTRUCTIONS
                else 1
                if name in CONFIGS
                else 2
                if name == "readme.md"
                else 3
                if any(path == f or path.startswith(f + "/") for f in focus)
                else 4
                if Path(path).suffix.lower() in CODE_SUFFIXES and "tests" not in parts
                else 5
                if path.lower().endswith(".md")
                else 6
            )
            return rank, path.count("/"), path

        candidates = sorted(
            (
                p
                for p in paths
                if p not in mandatory
                and (
                    Path(p).suffix.lower() in CODE_SUFFIXES | {".md"}
                    or Path(p).name.lower() in CONFIGS
                )
            ),
            key=priority,
        )
        chosen = sorted(mandatory | set(candidates[: max(0, 12 - len(mandatory))]), key=priority)
        sources: list[SourceFile] = []
        size = 0
        for path in chosen:
            name = Path(path).name.lower()
            role = (
                "instruction"
                if name in INSTRUCTIONS
                else (
                    "config" if name in CONFIGS else "document" if path.endswith(".md") else "code"
                )
            )
            try:
                raw = self.files.read(path)
            except UnicodeDecodeError:
                if role == "instruction":
                    raise ValueError(f"instruction source must be UTF-8: {path}") from None
                gaps.append(f"Non-UTF-8 source omitted: {path}")
                continue
            sanitized = self.sanitizer.text(raw)
            # Keep original fingerprints while retaining only bounded, sanitized source excerpts.
            limit = min(32768, MAX_CONTEXT - size)
            encoded = sanitized.encode()
            text = encoded[:limit].decode("utf-8", errors="ignore")
            size += len(text.encode())
            truncated = len(encoded) > limit
            if truncated and role == "instruction":
                raise ValueError(f"instruction exceeds context budget; narrow scope: {path}")
            if truncated:
                gaps.append(f"Source excerpt truncated: {path}")
            sources.append(
                SourceFile.model_validate(
                    dict(path=path, sha256=digest(raw), role=role, text=text, truncated=truncated)
                )
            )
        gaps.insert(
            0, f"Read {len(sources)} representative sources from {len(paths)} visible files."
        )
        return RepositoryInput(
            paths=tuple(paths), sources=tuple(sources), gaps=tuple(gaps), focus=focus
        )

    def _replace(self, name: str, data: bytes, expected: bytes | None) -> None:
        self._check_control()
        assert self._agent_fd is not None
        if self._optional(name) != expected:
            raise ValueError(f"{name} changed during initialization; preserve edits and retry")
        temporary = "init-pending-" + uuid4().hex
        fd = io.open_at(self._agent_fd, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if self._optional(name) != expected:
                raise ValueError(f"{name} changed while preparing publication")
            io.install(self._agent_fd, temporary, name, replace=expected is not None)
            io.sync_directory(self._agent_fd)
        finally:
            try:
                io.unlink(self._agent_fd, temporary)
            except FileNotFoundError:
                pass

    def publish(self, snapshot: KnowledgeSnapshot, state: KnowledgeState) -> str:
        def action() -> str:
            previous = state.snapshot.revision if state.snapshot is not None else None
            if snapshot.scope != self.scope or snapshot.previous_revision != previous:
                raise ValueError("publication scope or predecessor does not match the controller")
            if (self._optional("project.md"), self._optional("project.json")) != (
                state.guide,
                state.metadata,
            ):
                raise ValueError(
                    "project guidance changed during exploration; refresh before publishing"
                )
            current = self._capture(snapshot.repository.focus, snapshot.repository)
            expected = snapshot.repository.model_copy(
                update={
                    "sources": tuple(
                        s for s in snapshot.repository.sources if s.path != USER_SOURCE
                    )
                }
            )
            if current != expected:
                raise ValueError(
                    "repository sources changed during exploration; refresh before publishing"
                )
            generated = render_summary(snapshot.summary)
            guide = (state.prefix + generated + state.suffix).encode()
            if max(len(guide), len(snapshot.model_dump_json().encode())) > 8 * 1024 * 1024:
                raise ValueError("project knowledge exceeds the 8 MiB persistence limit")
            metadata = KnowledgeMetadata(
                revision=snapshot.revision,
                generated_sha256=digest(generated),
                user_sha256=digest(user_text(state.prefix, state.suffix)),
                source_fingerprints={s.path: s.sha256 for s in snapshot.repository.sources},
            )
            self.journal.check_writable()
            name = f"knowledge-{snapshot.revision}.json"
            data = snapshot.model_dump_json().encode()
            existing = self._optional(name)
            if existing is None:
                self._replace(name, data, None)
            elif existing != data:
                raise ValueError("immutable knowledge snapshot changed")
            # JSON is the commit point. Mismatched guide/metadata is detected on load.
            self._replace("project.md", guide, state.guide)
            self.journal.check_writable()
            self._replace("project.json", metadata.model_dump_json().encode(), state.metadata)
            return snapshot.revision

        return self._record(
            InitializationOperation(operation="publish", input_sha256=snapshot.revision), action
        )
