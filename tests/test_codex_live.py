"""Opt-in local CLI/account smoke: 60 seconds, four runtime tools, synthetic files only."""

import asyncio
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coding_agent.core import TaskGraph, TaskSpec
from coding_agent.core.workflow import PlanApproval, Revision, RunSpec
from coding_agent.executors.codex import CodexCoder, CodexSettings
from coding_agent.session.records import JsonlJournal, inspect_journal
from coding_agent.tools import ToolRuntime


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("CODING_AGENT_CODEX_LIVE") != "1",
    reason="local Codex/account smoke requires explicit CODING_AGENT_CODEX_LIVE=1",
)
def test_codex_live_reads_and_patches_synthetic_project(tmp_path, make_task):
    executable = shutil.which("codex")
    home = os.environ.get("CODING_AGENT_CODEX_HOME")
    model = os.environ.get("CODING_AGENT_CODEX_MODEL")
    if not executable or not home or not model:
        pytest.fail(
            "explicit smoke requires native codex, "
            "CODING_AGENT_CODEX_HOME and CODING_AGENT_CODEX_MODEL"
        )
    root = tmp_path / "synthetic-repo"
    (root / "src").mkdir(parents=True)
    (root / "src/input.txt").write_text("codex live fixture", encoding="utf-8")

    def read_revision():
        contents = [
            (p.relative_to(root).as_posix(), p.read_bytes().hex())
            for p in sorted(root.rglob("*"))
            if p.is_file()
        ]
        return Revision(
            plan_version=1,
            context_revision="synthetic-codex-smoke",
            workspace_revision=hashlib.sha256(json.dumps(contents).encode()).hexdigest(),
        )

    task = TaskSpec.model_validate(
        make_task("smoke").model_dump()
        | {
            "title": "Copy one synthetic text file",
            "goal": "Copy src/input.txt to src/result.txt",
            "requirements": [
                "Read src/input.txt through verified_read; "
                "create src/result.txt with identical text "
                "using verified_patch. Do not run tests, shell or Git. "
                "Return implemented after the patch tool succeeds."
            ],
            "scope": {"allowed": ["src/result.txt"], "forbidden": []},
            "permissions": {"network": False, "shell": "deny", "database": "deny"},
        }
    )
    revision = read_revision()
    spec = RunSpec(session_id="codex-live", graph=TaskGraph(tasks=(task,)), revision=revision)
    approval = PlanApproval(
        session_id=spec.session_id,
        plan_fingerprint=spec.fingerprint,
        source="explicit-codex-live-smoke",
        timestamp=datetime.now(UTC),
    )
    config = CodexSettings(
        executable=Path(executable).resolve(),
        home=Path(home).resolve(),
        model=model,
        max_tool_calls=4,
        timeout_seconds=60,
    )
    with JsonlJournal(tmp_path / "records", spec.session_id) as journal:
        runtime = ToolRuntime(
            spec,
            task.id,
            root=root,
            journal=journal,
            approval=approval,
            read_revision=read_revision,
        )
        result = asyncio.run(
            CodexCoder(config, runtime).implement(
                task,
                revision,
                attempt=1,
                purpose="implement",
                feedback=(),
            )
        )
    assert result.outcome == "implemented", result.summary
    assert (root / "src/result.txt").read_text(encoding="utf-8") == "codex live fixture"
    inspected = inspect_journal(tmp_path / "records/events.jsonl")
    assert not inspected.unresolved
    operations = [e.tool_request.invocation.kind for e in inspected.events if e.tool_request]
    assert operations == ["read", "patch"]
    assert inspected.events[-1].model_result.usage is not None
    print(
        {
            "model": model,
            "usage": inspected.events[-1].model_result.usage.model_dump(),
            "records": str(tmp_path / "records"),
            "revision": read_revision().model_dump(),
        }
    )
