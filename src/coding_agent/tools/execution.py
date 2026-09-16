"""Controller execution records and read-only access to recorded history."""

import hashlib
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..core.models import DomainModel
from ..core.planning import PlanVersion
from ..core.state import TaskState
from ..core.tools import ArtifactRef
from ..core.workflow import Revision, WorkflowEvent
from ..runtime import _fileio as io
from ..session.records import JournalInspection, Sanitizer, inspect_journal
from .planning import PlanningRuntime


class ExecutionRuntime(PlanningRuntime):
    def __init__(self, root: Path, plan: PlanVersion, *, sanitizer: Sanitizer) -> None:
        super().__init__(root, plan.settings.scope, sanitizer=sanitizer)
        # Revisions of one plan cannot silently create a fresh attempt budget.
        self.session_id = "run-" + plan.plan_id

    def record(
        self,
        kind: str,
        value: DomainModel,
        revision: Revision,
        *,
        task_id: str | None = None,
        source: str | None = None,
    ) -> None:
        artifact = self.journal.artifact("execution", value.model_dump_json())
        if artifact.truncated:
            self.journal.invalidate()
            raise ValueError("execution record exceeds persistence limit")
        sequence = self.journal.next_sequence
        self.journal.write(
            WorkflowEvent.model_validate(
                {
                    "event_id": f"{self.session_id}:{sequence}",
                    "sequence": sequence,
                    "timestamp": datetime.now(UTC),
                    "session_id": self.session_id,
                    "task_id": task_id,
                    "revision": revision,
                    "kind": kind,
                    "reason": kind.replace("_", " "),
                    "source": source,
                    "artifacts": (artifact,),
                }
            )
        )


class RunHistory(DomainModel):
    status: Literal["recorded", "incomplete"]
    session_id: str
    journal: str
    states: dict[str, TaskState]
    records: JournalInspection
    diff: str
    diff_recorded: bool
    diff_truncated: bool
    requirement_complete: Literal[False] = False


def read_artifact(directory: Path, artifact: ArtifactRef) -> str:
    if not re.fullmatch(r"[a-z]+-[a-f0-9]{32}\.txt", artifact.path):
        raise ValueError("invalid execution artifact path")
    with_fd = io.open_directory(directory)
    try:
        fd = io.open_at(with_fd, artifact.path, os.O_RDONLY | io.NOFOLLOW | io.NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("execution artifacts cannot have aliases")
            data = stream.read(1024 * 1024 + 64)
    finally:
        os.close(with_fd)
    if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != artifact.sha256:
        raise ValueError("execution artifact changed")
    return data.decode("utf-8")


def inspect_execution(root: Path, session_id: str | None = None) -> RunHistory:
    with PlanningRuntime.read_only(root) as runtime:
        if session_id is None:
            saved, _ = runtime.current()
            if saved is None:
                raise ValueError("no saved plan; specify --session for older execution history")
            session_id = "run-" + saved.plan_id
        if not re.fullmatch(r"run-[a-f0-9]{32}", session_id):
            raise ValueError("invalid execution session ID")
        directory = runtime.control / session_id
        view = inspect_journal(directory / "events.jsonl")
        if any(event.session_id != session_id for event in view.events):
            raise ValueError("execution journal belongs to another session")
        states = {e.task_id: e.state for e in view.events if e.task_id and e.state}
        requests = {e.event_id: e.tool_request for e in view.events if e.tool_request}
        diff = ""
        recorded = truncated = False
        for event in view.events:
            result = event.tool_result
            if result is None or result.status != "succeeded":
                continue
            request = requests[result.request_event_id]
            if request.invocation.kind == "workspace" and request.invocation.operation == "diff":
                for artifact in result.artifacts:
                    if artifact.path.startswith("diff-"):
                        diff = read_artifact(directory, artifact)
                        recorded = True
                        truncated = artifact.truncated
        finished = any(e.kind in {"session_finished", "session_interrupted"} for e in view.events)
        return RunHistory(
            status="recorded"
            if finished and not view.unresolved and not view.incomplete_tail
            else "incomplete",
            session_id=session_id,
            journal=str(directory / "events.jsonl"),
            states=states,
            records=view,
            diff=diff,
            diff_recorded=recorded,
            diff_truncated=truncated,
        )
