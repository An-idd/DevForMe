import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from coding_agent.core import ScopePolicy, TaskGraph, TaskSpec
from coding_agent.core.tools import Git, Patch, Read, ToolRequest
from coding_agent.core.workflow import EventWriteError, PlanApproval, RunSpec
from coding_agent.core.workspace import WorkspaceOperation
from coding_agent.runtime.workspace import Workspace
from coding_agent.session.records import JsonlJournal, inspect_journal
from coding_agent.tools import ToolRuntime


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        [shutil.which("git") or "git", "-c", f"safe.directory={root}", "-C", str(root), *args],
        check=True,
        capture_output=True,
    ).stdout


class Harness:
    def __init__(self, root: Path, directory: Path, journal: JsonlJournal, task: TaskSpec):
        self.source, self.journal = root, journal
        self.workspace = Workspace(root, directory, task.scope)
        self.spec = RunSpec(
            session_id=journal.session_id,
            graph=TaskGraph(tasks=(task,)),
            revision=self.workspace.revision_reader(plan_version=1, context_revision="rules")(),
        )
        self.runtime = ToolRuntime(
            self.spec,
            task.id,
            root=root,
            journal=journal,
            workspace=self.workspace,
            approval=PlanApproval(
                session_id=journal.session_id,
                plan_fingerprint=self.spec.fingerprint,
                source="controller",
                timestamp=datetime.now(UTC),
            ),
        )
        self.counter = 0

    def operation(self, name: str, **kwargs: str):
        self.counter += 1
        return asyncio.run(
            self.runtime.workspace_operation(
                f"op-{self.counter}",
                WorkspaceOperation.model_validate({"operation": name, **kwargs}),
            )
        )

    def tool(self, invocation):
        self.counter += 1
        return asyncio.run(
            self.runtime.execute(
                ToolRequest(
                    request_id=f"tool-{self.counter}",
                    invocation=invocation,
                )
            )
        )

    def prepare(self):
        result = self.operation("prepare")
        assert result.status == "succeeded", result.reason
        return result


@pytest.fixture
def h(tmp_path: Path, make_task: Callable[..., TaskSpec]) -> Iterator[Harness]:
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src/main.py").write_bytes(b"committed\n")
    git(source, "init", "--initial-branch=main")
    git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "base")
    (source / "src/main.py").write_bytes(b"staged\n")
    git(source, "add", "src/main.py")
    (source / "src/main.py").write_bytes(b"user dirty\n")
    (source / "src/untracked.py").write_bytes(b"user untracked\n")
    task = TaskSpec.model_validate(
        make_task("task").model_dump()
        | {
            "permissions": {"network": False, "shell": "restricted", "database": "deny"},
        }
    )
    with JsonlJournal(tmp_path / "records", "workspace") as journal:
        yield Harness(source, tmp_path / "session", journal, task)


def test_prepare_preserves_main_index_and_dirty_inputs(h: Harness) -> None:
    index = (h.source / ".git/index").read_bytes()
    original = git(h.source, "status", "--porcelain=v1")
    h.prepare()
    root = h.workspace.path
    assert root != h.source and (root / ".git").is_file()
    assert git(root, "rev-parse", "--git-common-dir").strip()
    assert (root / "src/main.py").read_bytes() == b"user dirty\n"
    assert (root / "src/untracked.py").read_bytes() == b"user untracked\n"
    assert h.workspace.snapshot().revision == h.spec.revision.workspace_revision
    assert not h.workspace.status().modified
    assert (h.source / ".git/index").read_bytes() == index
    assert git(h.source, "status", "--porcelain=v1") == original


def test_patch_snapshot_and_git_diff_share_actual_revision(h: Harness) -> None:
    h.prepare()
    old = h.workspace.snapshot().revision
    result = h.tool(
        Patch(
            path="src/main.py",
            expected_sha256=hashlib.sha256(b"user dirty\n").hexdigest(),
            content="agent change\n",
        )
    )
    assert result.status == "succeeded"
    assert h.runtime.read_revision().workspace_revision != old
    status = json.loads(h.tool(Git(operation="status")).output)
    assert status["modified"] == ["src/main.py"]
    diff = h.tool(Git(operation="diff"))
    assert diff.status == "succeeded" and "-user dirty" in diff.output
    assert "+agent change" in diff.output
    assert h.operation("snapshot").status == "succeeded"
    assert (h.source / "src/main.py").read_bytes() == b"user dirty\n"
    inspected = inspect_journal(h.journal.directory / "events.jsonl")
    assert not inspected.unresolved
    for event in inspected.events:
        if event.tool_request and isinstance(event.tool_request.invocation, Git):
            assert event.revision.workspace_revision != old


def test_reset_retains_user_edits_and_restores_saved_binary_snapshot(h: Harness) -> None:
    h.prepare()
    root = h.workspace.path
    (root / "src/data.bin").write_bytes(b"\x00\xffdata")
    saved = h.workspace.snapshot().revision
    assert h.operation("snapshot").status == "succeeded"
    (root / "src/main.py").unlink()
    (root / "src/data.bin").write_bytes(b"manual edit")
    (root / ".pytest_cache").mkdir()
    (root / ".pytest_cache/private").write_bytes(b"also retain")
    current = h.workspace.snapshot().revision
    reset = h.operation("reset", expected_revision=current, target_revision=saved)
    assert reset.status == "succeeded", reset.reason
    assert h.workspace.path != root and root.is_dir()
    assert (root / "src/data.bin").read_bytes() == b"manual edit"
    assert (root / ".pytest_cache/private").read_bytes() == b"also retain"
    assert (h.workspace.path / "src/data.bin").read_bytes() == b"\x00\xffdata"
    assert h.runtime.root == h.workspace.path
    assert h.workspace.snapshot().revision == saved
    assert h.tool(Read(path="src/main.py")).output == "user dirty\n"


def test_cleanup_removes_only_pristine_owned_worktree(h: Harness) -> None:
    h.prepare()
    root = h.workspace.path
    snapshot = h.workspace.snapshot()
    result = h.operation("cleanup", expected_revision=snapshot.revision)
    assert result.status == "succeeded", result.reason
    assert not root.exists()
    assert h.source.is_dir() and (h.source / "src/untracked.py").is_file()
    assert (h.workspace.directory / "snapshots" / (snapshot.revision + ".json")).is_file()


@pytest.mark.parametrize(
    "extra", ["src/new.py", ".pytest_cache/log", "secrets/private", ".agent/record"]
)
def test_cleanup_preserves_unknown_or_modified_files(h: Harness, extra: str) -> None:
    h.prepare()
    path = h.workspace.path / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"user data")
    result = h.operation("cleanup", expected_revision=h.workspace.snapshot().revision)
    assert result.status == "failed"
    assert path.read_bytes() == b"user data"


def test_stale_reset_cannot_replace_current_workspace(h: Harness) -> None:
    h.prepare()
    previous = h.workspace.path
    stale = h.workspace.snapshot().revision
    (previous / "src/main.py").write_bytes(b"later edit")
    result = h.operation("reset", expected_revision=stale, target_revision=stale)
    assert result.status == "failed" and "revision changed" in result.reason
    assert h.workspace.path == previous
    assert (previous / "src/main.py").read_bytes() == b"later edit"


def test_agent_cannot_invoke_workspace_lifecycle_or_edit_main(h: Harness) -> None:
    assert h.tool(Patch(path="src/new.py", expected_sha256=None, content="bad")).status == "denied"
    assert h.tool(WorkspaceOperation(operation="prepare")).status == "denied"
    assert not h.workspace.directory.exists()
    h.prepare()
    current = h.workspace.snapshot().revision
    assert (
        h.tool(WorkspaceOperation(operation="cleanup", expected_revision=current)).status
        == "denied"
    )
    assert h.workspace.path.is_dir()


def test_source_change_after_approval_refuses_prepare(h: Harness) -> None:
    (h.source / "src/main.py").write_bytes(b"changed after approval")
    result = h.operation("prepare")
    assert result.status == "failed" and "authorization" in result.reason
    assert not h.workspace.directory.exists()


def test_journal_failure_prevents_workspace_side_effects(h: Harness, monkeypatch) -> None:
    def unavailable(event):
        raise EventWriteError("simulated disk failure")

    monkeypatch.setattr(h.journal, "write", unavailable)
    with pytest.raises(EventWriteError):
        h.operation("prepare")
    assert not h.workspace.directory.exists()


def test_snapshot_ignores_records_and_generated_outputs_but_includes_ignored_source(
    h: Harness,
) -> None:
    h.prepare()
    root = h.workspace.path
    original = h.workspace.snapshot().revision
    for path in (
        root / ".agent/log",
        root / "src/__pycache__/cache",
        root / ".pytest_cache/result",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"new output")
    assert h.workspace.snapshot().revision == original
    (root / ".gitignore").write_text("src/ignored.py\n")
    before = h.workspace.snapshot().revision
    (root / "src/ignored.py").write_bytes(b"still relevant")
    assert h.workspace.snapshot().revision != before
    (root / "src/ignored.py").unlink()
    assert h.workspace.snapshot().revision == before


def test_corrupt_saved_blob_cannot_destroy_current_worktree(h: Harness) -> None:
    h.prepare()
    baseline = h.workspace.snapshot()
    digest = baseline.files[0].sha256
    (h.workspace.directory / "blobs" / digest).write_bytes(b"corrupt")
    root = h.workspace.path
    result = h.operation(
        "reset", expected_revision=baseline.revision, target_revision=baseline.revision
    )
    assert result.status == "failed"
    assert root == h.workspace.path and (root / "src/main.py").read_bytes() == b"user dirty\n"


def test_snapshot_rejects_hardlinks(h: Harness) -> None:
    os.link(h.source / "src/main.py", h.source / "src/alias")
    with pytest.raises(ValueError, match="aliases"):
        h.workspace.snapshot()


def test_empty_and_binary_sources(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    scope = ScopePolicy(allowed=("**",))
    first = Workspace(source, tmp_path / "session", scope)
    empty = first.snapshot()
    (source / "bytes.bin").write_bytes(b"\x00\xff")
    assert first.snapshot().revision != empty.revision
    (source / "bytes.bin").touch()
    unchanged = first.snapshot().revision
    assert first.snapshot().revision == unchanged
    other = Workspace(source, tmp_path / "other", scope, excluded=())
    assert other.snapshot().revision != unchanged


@pytest.mark.parametrize(
    "values",
    [
        {"operation": "reset"},
        {"operation": "cleanup"},
        {"operation": "snapshot", "expected_revision": "a" * 64},
        {"operation": "reset", "expected_revision": "a" * 64, "target_revision": "../bad"},
    ],
)
def test_workspace_requests_validate_revision_requirements(values) -> None:
    with pytest.raises(ValidationError):
        WorkspaceOperation.model_validate(values)


def test_git_steps_are_recorded_with_real_exit_codes(h: Harness) -> None:
    result = h.prepare()
    requests = [a for a in result.artifacts if a.path.startswith("gitrequest-")]
    results = [a for a in result.artifacts if a.path.startswith("gitresult-")]
    assert len(requests) == len(results) == 4
    for artifact in results:
        recorded = json.loads((h.journal.directory / artifact.path).read_text())
        assert recorded["exit_code"] == 0
    events = inspect_journal(h.journal.directory / "events.jsonl").events
    request = next(e for e in events if e.kind == "tool_requested")
    assert request.tool_request.invocation.operation == "prepare"
    assert events[-1].tool_result.artifacts == result.artifacts


def test_prepare_failure_retains_baseline_and_stops_implicit_retry(h: Harness, monkeypatch) -> None:
    original = h.workspace._run

    def fail(*args, **kwargs):
        if args[0] == "worktree":
            raise OSError("injected creation failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(h.workspace, "_run", fail)
    result = h.operation("prepare")
    assert result.status == "failed"
    assert list((h.workspace.directory / "snapshots").glob("*.json"))
    assert (h.source / "src/main.py").read_bytes() == b"user dirty\n"
    with pytest.raises(ValueError, match="unavailable"):
        h.operation("prepare")


def test_reset_failure_keeps_current_worktree_usable(h: Harness, monkeypatch) -> None:
    h.prepare()
    root = h.workspace.path
    target = h.workspace.snapshot().revision
    (root / "src/main.py").write_bytes(b"manual value")
    current = h.workspace.snapshot().revision

    def fail(*args, **kwargs):
        raise OSError("injected worktree failure")

    monkeypatch.setattr(h.workspace, "_create_tree", fail)
    result = h.operation("reset", expected_revision=current, target_revision=target)
    assert result.status == "failed"
    assert h.workspace.path == root
    assert h.tool(Read(path="src/main.py")).output == "manual value"
    saved = h.workspace.directory / "snapshots" / (current + ".json")
    assert saved.is_file()


def test_result_write_loss_leaves_inspectable_worktree_and_unresolved_intent(
    h: Harness, monkeypatch
):
    original = h.journal.write

    def fail(event):
        if event.kind == "tool_finished":
            raise EventWriteError("injected lost result")
        original(event)

    monkeypatch.setattr(h.journal, "write", fail)
    with pytest.raises(EventWriteError):
        h.prepare()
    assert (h.workspace.path / "src/main.py").read_bytes() == b"user dirty\n"
    inspection = inspect_journal(h.journal.directory / "events.jsonl")
    assert inspection.unresolved
    with pytest.raises(EventWriteError):
        h.operation("snapshot")


def test_write_failure_between_git_steps_stops_further_effects(h: Harness, monkeypatch):
    original = h.journal.artifact

    def fail(label, content, **kwargs):
        if label == "gitresult":
            raise EventWriteError("injected result storage failure")
        return original(label, content, **kwargs)

    monkeypatch.setattr(h.journal, "artifact", fail)
    with pytest.raises(EventWriteError):
        h.prepare()
    assert h.workspace.repository.is_dir()  # init completed, fast-import did not run
    assert not (h.workspace.repository / "refs/heads/baseline").exists()
    assert not list(h.workspace.directory.glob("worktree-*"))


def test_metadata_redirection_is_rejected_without_following_it(h: Harness, tmp_path):
    h.prepare()
    with (h.workspace.path / ".git").open("r+b") as stream:
        stream.write(f"gitdir: {tmp_path}\n".encode())
        stream.truncate()
    with pytest.raises(ValueError, match="metadata changed"):
        h.operation("cleanup", expected_revision=h.spec.revision.workspace_revision)
    assert h.workspace.path.is_dir()


def test_unknown_empty_directory_prevents_cleanup(h: Harness):
    h.prepare()
    extra = h.workspace.path / "user-empty"
    extra.mkdir()
    result = h.operation("cleanup", expected_revision=h.workspace.snapshot().revision)
    assert result.status == "failed" and extra.is_dir()


def test_no_global_or_source_git_hook_executes(h: Harness, monkeypatch, tmp_path):
    marker = tmp_path / "executed"
    hook = tmp_path / "hook"
    hook.write_text(f"#!/bin/sh\necho bad > '{marker.as_posix()}'\n")
    hook.chmod(0o755)
    global_config = tmp_path / "global-config"
    global_config.write_text(f"[core]\n    fsmonitor = {hook.as_posix()}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hook))
    source_hook = h.source / ".git/hooks/post-checkout"
    source_hook.parent.mkdir(exist_ok=True)
    source_hook.write_bytes(hook.read_bytes())
    source_hook.chmod(0o755)
    h.prepare()
    assert not marker.exists()


def test_real_source_change_expires_quality_gate_evidence(h: Harness, evidence_data):
    from coding_agent.core import Evidence
    from coding_agent.core.workflow import QualityGate

    h.prepare()
    revision = h.runtime.read_revision()
    evidence = Evidence.model_validate(
        evidence_data
        | {
            "task_id": h.runtime.task.id,
            "plan_version": revision.plan_version,
            "context_revision": revision.context_revision,
            "workspace_revision": revision.workspace_revision,
        }
    )
    gate = QualityGate()
    assert not gate.verification(h.runtime.task, revision, (evidence,)).issues
    (h.workspace.path / "src/new.py").write_bytes(b"untracked source")
    changed = gate.verification(h.runtime.task, h.runtime.read_revision(), (evidence,))
    assert any(issue.code == "stale" for issue in changed.issues)


@pytest.mark.skipif(os.name != "posix", reason="POSIX executable bit")
def test_executable_mode_changes_snapshot_and_is_restored(h: Harness):
    h.prepare()
    root = h.workspace.path
    before = h.workspace.snapshot().revision
    (root / "src/main.py").chmod(0o755)
    assert h.workspace.snapshot().revision != before
    saved = h.workspace.snapshot().revision
    assert h.operation("snapshot").status == "succeeded"
    (root / "src/main.py").chmod(0o644)
    result = h.operation("reset", expected_revision=before, target_revision=saved)
    assert result.status == "succeeded"
    assert (h.workspace.path / "src/main.py").stat().st_mode & 0o111


def test_snapshot_limits_fail_instead_of_omitting_inputs(h: Harness, monkeypatch):
    from coding_agent.runtime import _snapshots

    monkeypatch.setattr(_snapshots, "MAX_ENTRIES", 1)
    with pytest.raises(ValueError, match="entry limit"):
        h.workspace.snapshot()


def test_snapshot_treats_file_time_as_metadata_not_source(h: Harness):
    before = h.workspace.snapshot().revision
    path = h.source / "src/main.py"
    os.utime(path, (1_800_000_000, 1_800_000_000))
    assert h.workspace.snapshot().revision == before


def test_session_directory_cannot_alias_main_or_existing_data(h: Harness, tmp_path):
    with pytest.raises(ValueError, match="outside"):
        Workspace(h.source, h.source / "nested", h.runtime.task.scope)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "mine").write_bytes(b"preserve")
    with pytest.raises(ValueError, match="already exists"):
        Workspace(h.source, existing, h.runtime.task.scope)
    assert (existing / "mine").read_bytes() == b"preserve"


def test_cleanup_rechecks_contents_after_creating_diff(h: Harness, monkeypatch):
    h.prepare()
    old = h.workspace.snapshot().revision
    original = h.workspace._diff

    def change(*args):
        result = original(*args)
        (h.workspace.path / "src/main.py").write_bytes(b"concurrent user edit")
        return result

    monkeypatch.setattr(h.workspace, "_diff", change)
    result = h.operation("cleanup", expected_revision=old)
    assert result.status == "failed"
    assert (h.workspace.path / "src/main.py").read_bytes() == b"concurrent user edit"


def test_prepare_empty_current_tree(tmp_path, make_task):
    root = tmp_path / "source"
    root.mkdir()
    with JsonlJournal(tmp_path / "records", "empty") as journal:
        empty = Harness(root, tmp_path / "session", journal, make_task("empty"))
        empty.prepare()
        assert not empty.workspace.snapshot().files
        assert (
            empty.operation("cleanup", expected_revision=empty.workspace.snapshot().revision).status
            == "succeeded"
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS Worktree sandbox integration")
def test_managed_worktree_keeps_macos_source_boundary(h: Harness):
    from coding_agent.core.tools import Shell, ToolApproval, operation_fingerprint

    h.prepare()
    for index, (path, expected) in enumerate(
        [
            ("src/main.py", "succeeded"),
            (str(h.source / "src/main.py"), "failed"),
        ]
    ):
        request = ToolRequest(
            request_id=f"macos-{index}",
            invocation=Shell(argv=("/bin/cat", path)),
        )
        approval = ToolApproval(
            fingerprint=operation_fingerprint(
                request,
                plan_fingerprint=h.spec.fingerprint,
                task_id=h.runtime.task.id,
                revision_json=h.runtime.read_revision().model_dump_json(),
            ),
            source="test",
            timestamp=datetime.now(UTC),
        )
        result = asyncio.run(h.runtime.execute(request, approval=approval))
        assert result.status == expected, result


def test_unicode_and_space_paths_survive_git_worktree(tmp_path, make_task):
    source = tmp_path / "source"
    source.mkdir()
    name = " leading \u6587\u4ef6.txt"
    (source / name).write_bytes(b"exact content\r\n")
    with JsonlJournal(tmp_path / "records", "paths") as journal:
        h = Harness(source, tmp_path / "session", journal, make_task("paths"))
        h.prepare()
        assert (h.workspace.path / name).read_bytes() == b"exact content\r\n"
        assert h.workspace.snapshot().revision == h.spec.revision.workspace_revision


def test_serial_tasks_share_snapshot_but_keep_distinct_write_scopes(tmp_path, make_task):
    root = tmp_path / "source"
    (root / "src").mkdir(parents=True)
    (root / "src/main.py").write_bytes(b"baseline")
    first = make_task("first")
    second = TaskSpec.model_validate(
        make_task("second", "first").model_dump()
        | {
            "scope": {"allowed": ["src/second.py"], "forbidden": list(first.scope.forbidden)},
        }
    )
    workspace = Workspace(root, tmp_path / "session", first.scope)
    spec = RunSpec(
        session_id="serial",
        graph=TaskGraph(tasks=(first, second)),
        revision=workspace.revision_reader(plan_version=1, context_revision="rules")(),
    )
    approval = PlanApproval(
        session_id="serial",
        plan_fingerprint=spec.fingerprint,
        source="test",
        timestamp=datetime.now(UTC),
    )
    with JsonlJournal(tmp_path / "records", "serial") as journal:
        runtime = ToolRuntime(
            spec, first.id, root=root, journal=journal, workspace=workspace, approval=approval
        )
        prepared = asyncio.run(
            runtime.workspace_operation("prepare", WorkspaceOperation(operation="prepare"))
        )
        assert prepared.status == "succeeded", prepared.reason
        next_runtime = ToolRuntime(
            spec,
            second.id,
            root=workspace.path,
            journal=journal,
            workspace=workspace,
            approval=approval,
        )
        for name, expected in (("src/main.py", "denied"), ("src/second.py", "succeeded")):
            result = asyncio.run(
                next_runtime.execute(
                    ToolRequest(
                        request_id="write-" + expected,
                        invocation=Patch(
                            path=name,
                            expected_sha256=None,
                            content="second task",
                        ),
                    )
                )
            )
            assert result.status == expected
        assert runtime.read_revision() == next_runtime.read_revision()
        assert (root / "src/main.py").read_bytes() == b"baseline"


def test_other_bound_runtime_follows_reset_without_editing_recovery_tree(h: Harness):
    h.prepare()
    original = h.workspace.path
    other = ToolRuntime(
        h.spec,
        h.runtime.task.id,
        root=original,
        journal=h.journal,
        workspace=h.workspace,
        approval=h.runtime.approval,
    )
    target = h.workspace.snapshot().revision
    (original / "src/main.py").write_bytes(b"manual edit")
    reset = h.operation(
        "reset", expected_revision=h.workspace.snapshot().revision, target_revision=target
    )
    assert reset.status == "succeeded", reset.reason
    result = asyncio.run(
        other.execute(
            ToolRequest(
                request_id="other-runtime",
                invocation=Read(path="src/main.py"),
            )
        )
    )
    assert result.output == "user dirty\n"
    assert other.root == h.workspace.path
    assert (original / "src/main.py").read_bytes() == b"manual edit"


def test_diff_separates_lines_without_final_newline(h: Harness):
    h.prepare()
    (h.workspace.path / "src/untracked.py").write_bytes(b"without newline")
    result = h.tool(Git(operation="diff"))
    assert result.status == "succeeded"
    assert "+without newline\n\\ No newline at end of file\n" in result.output
