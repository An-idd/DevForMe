"""Opt-in P08 application smoke; synthetic project and bounded native Codex attempt."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest
from test_planning import project as project

from coding_agent.application.execution import run
from coding_agent.application.planning import plan
from coding_agent.core.planning import PlanDraft
from coding_agent.executors.codex import CodexSettings
from coding_agent.tools.execution import inspect_execution


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("CODING_AGENT_RUN_LIVE") != "1",
    reason="P08 application/account smoke requires explicit CODING_AGENT_RUN_LIVE=1",
)
def test_live_context_to_retained_worktree(project, tmp_path):
    executable = shutil.which("codex")
    home = os.environ.get("CODING_AGENT_CODEX_HOME")
    model = os.environ.get("CODING_AGENT_CODEX_MODEL")
    if not executable or not home or not model:
        pytest.fail("explicit smoke requires native codex and CODING_AGENT_CODEX_HOME/MODEL")
    root, _, draft = project
    expected = "# verified-runtime smoke\ndef run(): return 1\n"
    data = draft.model_dump(mode="json")
    goal = "Add a smoke-test comment to service.py while preserving its public behavior"
    data["requirement"]["goal"] = goal
    data["tasks"][0]["task"]["goal"] = goal
    data["tasks"][0]["task"]["requirements"] = [
        "Read service.py using context or verified_read. Use verified_patch to write exactly "
        + repr(expected)
        + ". Do not use shell, Git or run tests. Return implemented after the patch succeeds."
    ]
    saved = asyncio.run(plan(root, draft=PlanDraft.model_validate(data))).plan
    assert saved is not None
    config = CodexSettings(
        executable=Path(executable).resolve(),
        home=Path(home).resolve(),
        model=model,
        max_tool_calls=3,
        timeout_seconds=60,
    )
    directory = tmp_path / "retained-workspace"
    preview = asyncio.run(run(root, config=config, workspace=directory))
    result = asyncio.run(run(root, config=config, workspace=directory, approve=preview.fingerprint))
    assert result.status == "blocked" and "missing evidence" in result.reason
    assert result.workflow is not None and result.workflow.tasks[0].attempts == 1
    assert result.worktree is not None
    assert (Path(result.worktree) / "service.py").read_bytes() == expected.encode()
    assert (root / "service.py").read_bytes() == b"def run(): return 1\n"
    history = inspect_execution(root)
    assert not history.requirement_complete and not history.records.unresolved
    assert any(e.kind == "context_built" for e in history.records.events)
    assert any(e.model_result and e.model_result.usage for e in history.records.events)
    assert history.diff_recorded and "+# verified-runtime smoke" in history.diff
    print({"model": model, "journal": result.journal, "worktree": result.worktree})
