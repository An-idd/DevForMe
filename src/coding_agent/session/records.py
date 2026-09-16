"""Single-writer JSONL journal and immutable, sanitized artifacts."""

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.models import DomainModel
from ..core.tools import ArtifactRef
from ..core.workflow import EventWriteError, RunSpec, WorkflowEvent
from ..runtime import _fileio as io


class Sanitizer:
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        # Diff/search prefixes and partial output can separate a multiline secret.
        fragments = {part for secret in secrets for part in (secret, *secret.splitlines()) if part}
        self._secrets = tuple(sorted(fragments, key=len, reverse=True))

    def text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(bearer\s+)[\w.+/=-]+", r"\1[REDACTED]", value)
        value = re.sub(
            r"(?i)((?:api[_-]?key|password|secret|access[_-]?token)\s*[=:]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            value,
        )
        return value

    def tree(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.tree(item) for item in value]
        if isinstance(value, dict):
            return {key: self.tree(item) for key, item in value.items()}
        return value


class JournalInspection(DomainModel):
    events: tuple[WorkflowEvent, ...]
    unresolved: tuple[str, ...]
    incomplete_tail: bool


def inspect_journal(path: Path, *, max_bytes: int = 64 * 1024 * 1024) -> JournalInspection:
    fd = io.open_read(path)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise EventWriteError("journal must be a regular, unaliased file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(max_bytes + 1)
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise EventWriteError("journal exceeds inspection limit")
    boundary = data.rfind(b"\n") + 1
    lines = data[:boundary].splitlines()
    tail = data[boundary:]
    events: list[WorkflowEvent] = []
    pending: dict[str, WorkflowEvent] = {}
    session: str | None = None
    for line in lines:
        event = WorkflowEvent.model_validate_json(line)
        if event.sequence != len(events) + 1 or (session and event.session_id != session):
            raise EventWriteError("invalid journal sequence or session")
        session = event.session_id
        if event.event_id != f"{session}:{event.sequence}":
            raise EventWriteError("invalid event identity")
        if event.tool_request is not None:
            pending[event.event_id] = event
        if event.tool_result is not None:
            request_event_id = event.tool_result.request_event_id
            _check_result(event, pending.get(request_event_id))
            del pending[request_event_id]
        events.append(event)
    return JournalInspection(
        events=tuple(events), unresolved=tuple(pending), incomplete_tail=bool(tail)
    )


def _check_result(event: WorkflowEvent, request: WorkflowEvent | None) -> None:
    assert event.tool_result is not None
    if (
        request is None
        or request.tool_request is None
        or request.tool_request.request_id != event.tool_result.request_id
        or request.task_id != event.task_id
        or request.revision != event.revision
    ):
        raise EventWriteError("uncorrelated or duplicate tool result")


class JsonlJournal:
    def __init__(
        self, directory: Path, session_id: str, *, sanitizer: Sanitizer | None = None
    ) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = directory.resolve(strict=True)
        self.session_id = session_id
        self.sanitizer = sanitizer or Sanitizer()
        self._directory_fd = io.open_directory(self.directory)
        fd: int | None = None
        try:
            # Exclusive creation rejects simultaneous writers and implicit resume/truncation.
            fd = io.open_at(
                self._directory_fd,
                "events.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | io.NOFOLLOW,
                0o600,
            )
            io.sync_directory(self._directory_fd)
        except BaseException:
            if fd is not None:
                os.close(fd)
            os.close(self._directory_fd)
            raise
        self._fd = fd
        self._sequence = 0
        self._poisoned = False
        self._closed = False
        self._active = False
        self._registered_plan: str | None = None
        self._request_ids: set[str] = set()
        self._pending: dict[str, WorkflowEvent] = {}

    @property
    def next_sequence(self) -> int:
        self.check_writable()
        return self._sequence + 1

    def check_writable(self) -> None:
        if self._poisoned or self._closed:
            raise EventWriteError("journal unavailable; reconcile before further side effects")
        try:
            current = io.stat_at(self._directory_fd, "events.jsonl")
            opened = os.fstat(self._fd)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise OSError("journal path changed")
        except OSError as error:
            self._poisoned = True
            raise EventWriteError("journal path unavailable") from error

    def write(self, event: WorkflowEvent) -> None:
        self.check_writable()
        try:
            event = WorkflowEvent.model_validate(self.sanitizer.tree(event.model_dump(mode="json")))
            if self._pending and event.kind != "tool_finished":
                raise EventWriteError("resolve the pending tool before recording further work")
            if event.session_id != self.session_id or event.sequence != self._sequence + 1:
                raise ValueError("journal session or sequence mismatch")
            if event.event_id != f"{self.session_id}:{event.sequence}":
                raise ValueError("journal event identity mismatch")
            if event.tool_result is not None:
                _check_result(event, self._pending.get(event.tool_result.request_event_id))
            data = (event.model_dump_json() + "\n").encode()
            if len(data) > 4 * 1024 * 1024:
                raise ValueError("event exceeds size limit")
            view = memoryview(data)
            while view:
                written = os.write(self._fd, view)
                if written <= 0:
                    raise OSError("short event write")
                view = view[written:]
            os.fsync(self._fd)
        except Exception as error:
            self._poisoned = True
            raise EventWriteError(
                "durable event append failed; operation outcome may be unresolved"
            ) from error
        self._sequence = event.sequence
        if event.tool_request is not None:
            self._pending[event.event_id] = event
        if event.tool_result is not None:
            del self._pending[event.tool_result.request_event_id]

    def artifact(self, label: str, content: str, *, limit: int = 1_048_576) -> ArtifactRef:
        self.check_writable()
        content = self.sanitizer.text(content)
        data = content.encode()
        truncated = len(data) > limit
        if truncated:
            data = data[:limit].decode("utf-8", errors="ignore").encode() + b"\n[TRUNCATED]\n"
        name = f"{label}-{uuid4().hex}.txt"
        if not re.fullmatch(r"[a-z]+-[a-f0-9]{32}\.txt", name):
            raise ValueError("invalid artifact label")
        try:
            fd = io.open_at(
                self._directory_fd,
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | io.NOFOLLOW,
                0o600,
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            io.sync_directory(self._directory_fd)
        except OSError as error:
            self._poisoned = True
            raise EventWriteError(
                "artifact persistence failed; stop further side effects"
            ) from error
        return ArtifactRef(
            path=name,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            truncated=truncated,
        )

    def register_plan(self, spec: RunSpec) -> None:
        if spec.session_id != self.session_id:
            raise ValueError("plan belongs to another session")
        if self._registered_plan is not None:
            if self._registered_plan != spec.fingerprint:
                raise ValueError("P03 journal cannot silently replace a registered plan")
            return
        artifact = self.artifact(
            "plan", json.dumps(self.sanitizer.tree(spec.model_dump(mode="json")))
        )
        if artifact.truncated:
            self.invalidate()
            raise EventWriteError("plan exceeds persistence limit")
        sequence = self.next_sequence
        self.write(
            WorkflowEvent(
                event_id=f"{self.session_id}:{sequence}",
                sequence=sequence,
                timestamp=datetime.now(UTC),
                session_id=self.session_id,
                task_id=None,
                revision=spec.revision,
                kind="plan_registered",
                reason=spec.fingerprint,
                artifacts=(artifact,),
            )
        )
        self._registered_plan = spec.fingerprint

    def begin_operation(self, request_id: str) -> None:
        self.check_writable()
        if self._pending:
            raise EventWriteError("unresolved tool request; inspect actual state before continuing")
        if self._active or request_id in self._request_ids:
            raise ValueError("concurrent or repeated tool request; inspect existing outcome")
        self._active = True
        self._request_ids.add(request_id)

    def end_operation(self) -> None:
        self._active = False

    def invalidate(self) -> None:
        self._poisoned = True

    def close(self) -> None:
        if not self._closed:
            os.close(self._fd)
            os.close(self._directory_fd)
            self._closed = True

    def __enter__(self) -> "JsonlJournal":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
