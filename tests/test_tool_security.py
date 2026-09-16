"""Scope/redaction regressions; real temporary Git repos, no sandbox claim."""

import asyncio
import os
import shutil
import subprocess
from difflib import unified_diff
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from coding_agent.core import ScopePolicy
from coding_agent.core.tools import Git
from coding_agent.core.workflow import EventWriteError
from coding_agent.runtime.filesystem import FileBoundaryError
from coding_agent.runtime.process import MacReadOnlyProcess, ProcessOutcome
from coding_agent.session.records import JsonlJournal, Sanitizer
from coding_agent.tools import ToolRuntime


@pytest.fixture
def git_runtime(tmp_path, make_task, monkeypatch):
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("Git executable unavailable")
    runtime = object.__new__(ToolRuntime)
    runtime.root = tmp_path
    runtime.git_executable = Path(executable).resolve()
    runtime.task = make_task("git-scope")
    runtime.timeout = 10
    runtime.max_output_bytes = 65_536
    runtime.journal = Mock(spec=JsonlJournal)
    runtime.journal.directory = tmp_path / ".agent"
    runtime.backend = MacReadOnlyProcess()
    # This adapter tests the controller's actual Git argv on any host. The real
    # macOS sandbox and durable journal have separate integration tests.
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    } | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}

    def git(*args):
        return subprocess.run(
            [executable, *args],
            cwd=tmp_path,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )

    git("init", "-q")
    git("config", "core.autocrlf", "false")

    async def run(argv, *, max_output_bytes, **kwargs):
        completed = subprocess.run(
            argv,
            cwd=kwargs["cwd"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=kwargs["timeout"],
        )
        return ProcessOutcome(
            completed.returncode,
            completed.stdout[:max_output_bytes].decode("utf-8", errors="replace"),
            len(completed.stdout) > max_output_bytes,
            False,
        )

    monkeypatch.setattr(runtime.backend, "run", AsyncMock(side_effect=run))
    return runtime, git


@pytest.mark.parametrize(
    "forbidden,path",
    [
        ("secrets/**", "secrets/key.txt"),
        ("secrets", "secrets/nested/key.txt"),
        ("secrets/**", "SECRETS/key.txt"),
        ("caf\u00e9/**", "cafe\u0301/key.txt"),
        ("strasse/**", "stra\u00dfe/key.txt"),
        ("secrets/**", ".agent/state.txt"),
        ("secrets/**", ".agents/rules.txt"),
        ("secrets/**", "src/.CODEX/nested/key.txt"),
    ],
)
def test_git_diff_cannot_reveal_deleted_forbidden_index_data(git_runtime, forbidden, path):
    runtime, git = git_runtime
    runtime.task = runtime.task.model_copy(
        update={"scope": ScopePolicy(allowed=("**",), forbidden=(forbidden,))}
    )
    protected = runtime.root / path
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("forbidden-index-content\n", encoding="utf-8")
    visible = runtime.root / "visible.txt"
    visible.write_text("before\n", encoding="utf-8")
    git("add", "--", path, "visible.txt")
    protected.unlink()
    visible.write_text("permitted-change\n", encoding="utf-8")
    result = asyncio.run(runtime._run_git(Git(operation="diff")))
    assert result.exit_code == 0, result.output
    assert "permitted-change" in result.output
    assert "forbidden-index-content" not in result.output
    assert path not in result.output
    assert runtime.backend.run.await_count == 2


def test_git_diff_empty_scope_never_falls_back_to_all_paths(git_runtime):
    runtime, git = git_runtime
    protected = runtime.root / "secrets/key"
    protected.parent.mkdir()
    protected.write_text("must-not-leak\n", encoding="utf-8")
    git("add", "--", "secrets/key")
    protected.unlink()
    result = asyncio.run(runtime._run_git(Git(operation="diff")))
    assert result.exit_code == 0 and result.output == ""
    assert runtime.backend.run.await_count == 1


def test_git_diff_paths_are_literal_and_preserve_permitted_deletions(git_runtime):
    runtime, git = git_runtime
    path = runtime.root / "visible[1] file.txt"
    path.write_text("permitted-deletion\n", encoding="utf-8")
    git("add", "--", path.name)
    path.unlink()
    result = asyncio.run(runtime._run_git(Git(operation="diff")))
    assert result.exit_code == 0 and "-permitted-deletion" in result.output


@pytest.mark.parametrize(
    "inventory",
    [
        ProcessOutcome(1, "private-error", False, False),
        ProcessOutcome(0, "visible.txt\0", True, False),
        ProcessOutcome(0, "visible.txt\0", False, True),
        ProcessOutcome(0, "partial-path", False, False),
        ProcessOutcome(0, "bad\ufffdpath\0", False, False),
    ],
)
def test_git_diff_stops_on_incomplete_inventory(git_runtime, inventory):
    runtime, _ = git_runtime
    runtime.backend.run.side_effect = None
    runtime.backend.run.return_value = inventory
    if inventory.exit_code != 0 or inventory.timed_out:
        result = asyncio.run(runtime._run_git(Git(operation="diff")))
        assert result.exit_code == inventory.exit_code
        assert result.timed_out == inventory.timed_out
        assert "private-error" not in result.output
    else:
        with pytest.raises(FileBoundaryError, match="incomplete"):
            asyncio.run(runtime._run_git(Git(operation="diff")))
    assert runtime.backend.run.await_count == 1


def test_git_diff_stops_if_journal_fails_between_processes(git_runtime):
    runtime, git = git_runtime
    (runtime.root / "visible.txt").write_text("visible\n", encoding="utf-8")
    git("add", "--", "visible.txt")
    runtime.journal.check_writable.side_effect = EventWriteError("journal unavailable")
    with pytest.raises(EventWriteError):
        asyncio.run(runtime._run_git(Git(operation="diff")))
    assert runtime.backend.run.await_count == 1


@pytest.mark.parametrize("operation", ["add", "remove", "replace", "context"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_known_multiline_secrets_are_redacted_in_diff(operation, newline):
    secret = newline.join(
        ["-----BEGIN PRIVATE KEY-----", "SYNTHETIC-KEY-BODY", "-----END PRIVATE KEY-----", ""]
    )
    sanitizer = Sanitizer((secret,))
    before, after = {
        "add": ("before\n", secret),
        "remove": (secret, "after\n"),
        "replace": (secret, secret.replace("SYNTHETIC-KEY-BODY", "replacement")),
        "context": ("before\n" + secret, "after\n" + secret),
    }[operation]
    diff = "".join(unified_diff(before.splitlines(True), after.splitlines(True)))
    result = sanitizer.text(diff)
    assert "[REDACTED]" in result
    assert all(line not in result for line in secret.splitlines())
    if operation == "replace":
        assert "replacement" in result
    assert "[REDACTED]" in sanitizer.text(secret)


def test_multiline_secret_fragments_in_search_and_nested_records_are_redacted():
    sanitizer = Sanitizer(("first-secret-line\n\nsecond-secret-line\n",))
    result = sanitizer.tree({"output": ["file:12:second-secret-line\n", "+first-secret-line\n"]})
    assert result == {"output": ["file:12:[REDACTED]\n", "+[REDACTED]\n"]}
    assert sanitizer.text("ordinary output\n") == "ordinary output\n"


def test_git_inventory_and_diff_share_one_timeout(git_runtime, monkeypatch):
    runtime, _ = git_runtime
    runtime.backend.run.side_effect = None
    runtime.backend.run.return_value = ProcessOutcome(0, "visible.txt\0", False, False)
    ticks = iter([0, 0, runtime.timeout])
    monkeypatch.setattr("coding_agent.tools.runtime.monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError):
        asyncio.run(runtime._run_git(Git(operation="diff")))
    assert runtime.backend.run.await_count == 1
