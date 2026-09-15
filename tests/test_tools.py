import asyncio
import hashlib
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from coding_agent.core import TaskGraph, TaskSpec
from coding_agent.core.tools import (
    Git,
    Patch,
    Read,
    Search,
    Shell,
    ToolApproval,
    ToolRequest,
    operation_fingerprint,
)
from coding_agent.core.workflow import EventWriteError, PlanApproval, Revision, RunSpec
from coding_agent.runtime.process import MacReadOnlyProcess
from coding_agent.session.records import JsonlJournal, Sanitizer, inspect_journal
from coding_agent.tools import ToolRuntime

REVISION = Revision(plan_version=1, context_revision="rules-1", workspace_revision="snapshot-1")
STAMP = datetime(2026, 9, 15, tzinfo=UTC)
pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem integration")


class ToolHarness:
    def __init__(self, root: Path, journal: JsonlJournal, task: TaskSpec) -> None:
        self.root, self.journal, self.task = root, journal, task
        self.spec = RunSpec(session_id="tools", graph=TaskGraph(tasks=(task,)), revision=REVISION)
        self.approval = PlanApproval(
            session_id="tools",
            plan_fingerprint=self.spec.fingerprint,
            source="test-authorization",
            timestamp=STAMP,
        )
        roots = tuple(
            p
            for p in (Path(sys.base_prefix), Path(sys.prefix), Path("/opt/homebrew"))
            if p.exists()
        )
        self.backend = MacReadOnlyProcess(runtime_roots=roots)
        self.runtime = ToolRuntime(
            self.spec,
            task.id,
            root=root,
            journal=journal,
            approval=self.approval,
            read_revision=lambda: REVISION,
            process_backend=self.backend,
            git_executable=Path(shutil.which("git") or "/usr/bin/git"),
            timeout=0.5,
            max_output_bytes=2048,
        )
        self.counter = 0

    def request(self, invocation: Read | Search | Patch | Shell | Git) -> ToolRequest:
        self.counter += 1
        return ToolRequest(request_id=f"r{self.counter}", invocation=invocation)

    def approval_for(self, request: ToolRequest) -> ToolApproval:
        return ToolApproval(
            fingerprint=operation_fingerprint(
                request,
                plan_fingerprint=self.spec.fingerprint,
                task_id=self.task.id,
                revision_json=REVISION.model_dump_json(),
            ),
            source="test-command-approval",
            timestamp=STAMP,
        )

    def call(self, invocation: Read | Search | Patch | Shell | Git, *, approve: bool = False):
        request = self.request(invocation)
        return asyncio.run(
            self.runtime.execute(request, approval=self.approval_for(request) if approve else None)
        )


@pytest.fixture
def tool_harness(tmp_path: Path, make_task: Callable[..., TaskSpec]) -> Iterator[ToolHarness]:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "secrets").mkdir()
    (root / "src/main.py").write_text("print('hello')\n")
    (root / "secrets/private").write_text("confidential\n")
    (root / ".agent").mkdir()
    (root / ".agent/state.json").write_text("authoritative\n")
    task = TaskSpec.model_validate(
        make_task("t").model_dump()
        | {
            "permissions": {"network": False, "shell": "restricted", "database": "deny"},
        }
    )
    with JsonlJournal(tmp_path / "records", "tools") as journal:
        yield ToolHarness(root, journal, task)


def test_read_search_patch_and_correlated_records(tool_harness: ToolHarness) -> None:
    h = tool_harness
    read = h.call(Read(path="src/main.py"))
    assert read.status == "succeeded" and read.output == "print('hello')\n"
    assert "src/main.py:1:" in h.call(Search(query="hello")).output
    old_hash = hashlib.sha256(read.output.encode()).hexdigest()
    changed = h.call(
        Patch(path="src/main.py", expected_sha256=old_hash, content="print('changed')\n")
    )
    assert changed.status == "succeeded"
    assert changed.before_sha256 == old_hash
    assert changed.after_sha256 == hashlib.sha256((h.root / "src/main.py").read_bytes()).hexdigest()
    assert "-print('hello')" in (h.journal.directory / changed.artifacts[0].path).read_text()
    created = h.call(Patch(path="src/new.py", expected_sha256=None, content="new\n"))
    assert created.status == "succeeded" and created.before_sha256 is None
    conflict = h.call(Patch(path="src/new.py", expected_sha256=None, content="clobber\n"))
    assert conflict.status == "failed" and (h.root / "src/new.py").read_text() == "new\n"
    records = inspect_journal(h.journal.directory / "events.jsonl")
    assert not records.unresolved and not records.incomplete_tail
    for event in records.events:
        for artifact in event.artifacts:
            data = (h.journal.directory / artifact.path).read_bytes()
            assert hashlib.sha256(data).hexdigest() == artifact.sha256
    assert records.events[0].kind == "plan_registered"


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/tmp/escape",
        "src/../../escape",
        "src//main.py",
        "src/./main.py",
        "C:/escape",
        "src\\main.py",
        ".agent/state.json",
        ".git/config",
        "secrets/private",
        "src/../secrets/private",
        ".AGENT/state.json",
        "SECRETS/private",
    ],
)
def test_scope_denial_has_no_file_side_effect(tool_harness: ToolHarness, path: str) -> None:
    h = tool_harness
    original = (h.root / "src/main.py").read_bytes()
    result = h.call(Patch(path=path, expected_sha256=None, content="malicious"))
    assert result.status == "denied" and not result.executed
    assert (h.root / "src/main.py").read_bytes() == original


def test_symlink_hardlink_and_fifo_are_never_followed(
    tool_harness: ToolHarness, tmp_path: Path
) -> None:
    h = tool_harness
    external = tmp_path / "outside"
    external.write_text("outside data")
    (h.root / "src/link").symlink_to(external)
    (h.root / "src/directory").symlink_to(tmp_path, target_is_directory=True)
    os.link(external, h.root / "src/hard")
    os.mkfifo(h.root / "src/pipe")
    for path in ("src/link", "src/directory/outside", "src/hard", "src/pipe"):
        assert h.call(Read(path=path)).status == "failed"
        assert h.call(Patch(path=path, expected_sha256=None, content="bad")).status == "failed"
    assert external.read_text() == "outside data"
    search = h.call(Search(query="outside"))
    assert "outside data" not in search.output and search.truncated


def test_wrong_hash_preserves_manual_edits(tool_harness: ToolHarness) -> None:
    h = tool_harness
    original_hash = hashlib.sha256((h.root / "src/main.py").read_bytes()).hexdigest()
    (h.root / "src/main.py").write_text("user edit")
    assert (
        h.call(
            Patch(path="src/main.py", expected_sha256=original_hash, content="agent edit")
        ).status
        == "failed"
    )
    assert (h.root / "src/main.py").read_text() == "user edit"


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS backend integration")
def test_shell_asks_then_reuses_concrete_approval(tool_harness: ToolHarness) -> None:
    h = tool_harness
    invocation = Shell(argv=(sys.executable, "-c", "print('p03 process')"))
    pending = h.call(invocation)
    assert pending.status == "needs_approval" and not pending.executed
    result = h.call(invocation, approve=True)
    assert result.status == "succeeded", result.output
    assert result.exit_code == 0 and result.output == "p03 process\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS backend integration")
@pytest.mark.parametrize(
    "action",
    [
        "write_repo",
        "write_control",
        "read_control",
        "read_forbidden",
        "read_external",
        "network",
        "sysctl",
        "fork",
        "link",
    ],
)
def test_kernel_denies_ungranted_actions(
    tool_harness: ToolHarness, tmp_path: Path, action: str
) -> None:
    h = tool_harness
    external = tmp_path / "outside.txt"
    external.write_text("outside")
    code = {
        "write_repo": f"open({str(h.root / 'src/main.py')!r}, 'w').write('bad')",
        "write_control": f"open({str(h.journal.directory / 'events.jsonl')!r}, 'w').write('bad')",
        "read_control": f"print(open({str(h.journal.directory / 'events.jsonl')!r}).read())",
        "read_forbidden": f"print(open({str(h.root / 'secrets/private')!r}).read())",
        "read_external": f"print(open({str(external)!r}).read())",
        "network": "import socket; socket.create_connection(('127.0.0.1', 9), timeout=0.1)",
        "sysctl": (
            "import ctypes, os; libc = ctypes.CDLL(None, use_errno=True); "
            "size = ctypes.c_size_t(); "
            "rc = libc.sysctlbyname(b'kern.osrelease', None, ctypes.byref(size), None, 0); "
            "assert rc == 0, os.strerror(ctypes.get_errno())"
        ),
        "fork": "import os; os.fork()",
        "link": f"import os; os.link({str(h.journal.directory / 'events.jsonl')!r}, 'src/alias')",
    }[action]
    result = h.call(Shell(argv=(sys.executable, "-c", code)), approve=True)
    assert result.status == "failed" and result.exit_code != 0, result.output
    assert "Operation not permitted" in result.output or "Permission denied" in result.output, (
        result.output
    )
    assert (h.root / "src/main.py").read_text() == "print('hello')\n"
    assert external.read_text() == "outside"
    assert not inspect_journal(h.journal.directory / "events.jsonl").unresolved


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS backend integration")
def test_process_timeout_and_bounded_output(tool_harness: ToolHarness) -> None:
    h = tool_harness
    result = h.call(
        Shell(
            argv=(sys.executable, "-c", "import time; print('started', flush=True); time.sleep(60)")
        ),
        approve=True,
    )
    assert result.status == "timed_out" and result.exit_code is not None
    assert "started" in result.output
    output = h.call(
        Shell(argv=(sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)")),
        approve=True,
    )
    assert output.status == "succeeded" and output.truncated
    assert len(output.output.encode()) <= 2048


def test_journal_failure_before_patch_blocks_mutation(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = tool_harness
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(EventWriteError):
        h.call(Patch(path="src/new.py", expected_sha256=None, content="new"))
    assert not (h.root / "src/new.py").exists()
    with pytest.raises(EventWriteError):
        h.call(Read(path="src/main.py"))


def test_artifact_failure_after_patch_retains_unresolved_request(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = tool_harness

    def broken_artifact(*args: object, **kwargs: object):
        raise EventWriteError("artifact storage failed")

    monkeypatch.setattr(h.journal, "artifact", broken_artifact)
    with pytest.raises(EventWriteError):
        h.call(Patch(path="src/new.py", expected_sha256=None, content="completed change"))
    assert (h.root / "src/new.py").read_text() == "completed change"
    records = inspect_journal(h.journal.directory / "events.jsonl")
    assert len(records.unresolved) == 1
    with pytest.raises(EventWriteError):
        h.call(Patch(path="src/next.py", expected_sha256=None, content="must not run"))
    assert not (h.root / "src/next.py").exists()


def test_request_ids_cannot_replay_side_effects(tool_harness: ToolHarness) -> None:
    h = tool_harness
    request = h.request(Patch(path="src/one.py", expected_sha256=None, content="one"))
    assert asyncio.run(h.runtime.execute(request)).status == "succeeded"
    with pytest.raises(ValueError, match="repeated"):
        asyncio.run(h.runtime.execute(request))


def test_request_and_output_are_sanitized(tool_harness: ToolHarness) -> None:
    h = tool_harness
    h.journal.sanitizer = Sanitizer(("sample-secret-value",))
    result = h.call(
        Patch(
            path="src/text",
            expected_sha256=None,
            content="token: sample-secret-value\npassword=abc\n",
        )
    )
    assert result.status == "succeeded"
    for path in h.journal.directory.iterdir():
        content = path.read_text()
        assert "sample-secret-value" not in content and "password=abc" not in content
    assert "sample-secret-value" in (h.root / "src/text").read_text()
    assert "[REDACTED]" in h.call(Read(path="src/text")).output


def test_incomplete_tail_is_reported_without_repair(tool_harness: ToolHarness) -> None:
    h = tool_harness
    h.call(Read(path="src/main.py"))
    h.journal.close()
    path = h.journal.directory / "events.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"partial":')
    before = path.read_bytes()
    inspection = inspect_journal(path)
    assert inspection.incomplete_tail and len(inspection.events) == 3
    assert path.read_bytes() == before
    with pytest.raises(FileExistsError):
        JsonlJournal(h.journal.directory, "tools")


def test_records_cannot_live_in_an_agent_writable_directory(
    tool_harness: ToolHarness, tmp_path: Path
) -> None:
    h = tool_harness
    with JsonlJournal(h.root / "src/records", "tools") as journal:
        with pytest.raises(ValueError, match="under .agent"):
            ToolRuntime(
                h.spec,
                h.task.id,
                root=h.root,
                journal=journal,
                approval=h.approval,
                read_revision=lambda: REVISION,
            )


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS backend integration")
def test_readonly_git_ignores_external_diff_and_hooks(tool_harness: ToolHarness) -> None:
    import subprocess

    h = tool_harness
    subprocess.run(["git", "init", "-q", str(h.root)], check=True)
    subprocess.run(["git", "-C", str(h.root), "add", "src/main.py"], check=True)
    (h.root / "src/main.py").write_text("changed\n")
    subprocess.run(
        ["git", "-C", str(h.root), "config", "diff.external", "touch escaped"], check=True
    )
    diff = h.call(Git(operation="diff"))
    assert diff.status == "succeeded", diff.output
    assert "changed" in diff.output and not (h.root / "escaped").exists()
    status = h.call(Git(operation="status"))
    assert status.status == "succeeded", status.output


@pytest.mark.parametrize(
    "permissions",
    [
        {"network": True, "shell": "restricted", "database": "deny"},
        {"network": False, "shell": "restricted", "database": "test_only"},
        {"network": False, "shell": "deny", "database": "deny"},
    ],
)
def test_unsupported_permissions_never_launch(
    tool_harness: ToolHarness, tmp_path: Path, permissions: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = tool_harness
    task = TaskSpec.model_validate(h.task.model_dump() | {"permissions": permissions})
    with JsonlJournal(tmp_path / "other-records", "tools") as journal:
        other = ToolHarness(h.root, journal, task)
        launch = AsyncMock(side_effect=AssertionError("must not launch"))
        monkeypatch.setattr(other.backend, "run", launch)
        result = other.call(Shell(argv=("/usr/bin/true",)), approve=True)
        assert result.status == "denied" and not result.executed
        launch.assert_not_called()


def test_missing_backend_denies_even_with_approval(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = tool_harness
    monkeypatch.setattr("coding_agent.runtime.process.sys.platform", "unsupported")
    result = h.call(Shell(argv=("/usr/bin/true",)), approve=True)
    assert result.status == "denied" and "unavailable" in result.reason
    assert not result.executed


def test_revision_and_command_changes_do_not_reuse_authorization(tool_harness: ToolHarness) -> None:
    h = tool_harness
    request = h.request(Shell(argv=("/usr/bin/true",)))
    approval = h.approval_for(request)
    changed = h.request(Shell(argv=("/usr/bin/false",)))
    result = asyncio.run(h.runtime.execute(changed, approval=approval))
    assert result.status in {"needs_approval", "denied"} and not result.executed
    h.runtime.read_revision = lambda: Revision(
        plan_version=2, context_revision="rules-1", workspace_revision="snapshot-1"
    )
    result = h.call(Patch(path="src/no.py", expected_sha256=None, content="bad"))
    assert result.status == "denied" and not (h.root / "src/no.py").exists()
    h.runtime.read_revision = lambda: REVISION
    h.runtime.approval = None
    assert h.call(Read(path="src/main.py")).status == "denied"


def test_unexpected_failure_keeps_next_operation_blocked(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = tool_harness

    def crash(*args: object):
        raise RuntimeError("unexpected adapter failure")

    monkeypatch.setattr(h.runtime.files, "patch", crash)
    with pytest.raises(RuntimeError):
        h.call(Patch(path="src/no.py", expected_sha256=None, content="no"))
    with pytest.raises(EventWriteError, match="unresolved"):
        h.call(Read(path="src/main.py"))
    assert len(inspect_journal(h.journal.directory / "events.jsonl").unresolved) == 1


def test_result_write_failure_does_not_replay_patch(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = tool_harness
    original = h.journal.write

    def fail_result(event):
        if event.kind == "tool_finished":
            raise EventWriteError("result persistence failed")
        original(event)

    monkeypatch.setattr(h.journal, "write", fail_result)
    with pytest.raises(EventWriteError):
        h.call(Patch(path="src/done.py", expected_sha256=None, content="done"))
    assert (h.root / "src/done.py").read_text() == "done"
    with pytest.raises(EventWriteError):
        h.call(Patch(path="src/next.py", expected_sha256=None, content="never"))
    assert len(inspect_journal(h.journal.directory / "events.jsonl").unresolved) == 1


def test_short_writes_are_completed_and_artifacts_remain_utf8(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = tool_harness
    original = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: original(fd, data[:17]))
    assert h.call(Read(path="src/main.py")).status == "succeeded"
    assert not inspect_journal(h.journal.directory / "events.jsonl").unresolved
    ref = h.journal.artifact("output", "汉字" * 20, limit=7)
    text = (h.journal.directory / ref.path).read_text(encoding="utf-8")
    assert ref.truncated and text == "汉字\n[TRUNCATED]\n"


@pytest.mark.parametrize("damage", ["blank", "sequence", "wrong_task", "duplicate_result"])
def test_journal_rejects_corrupt_complete_records(
    tool_harness: ToolHarness, tmp_path: Path, damage: str
) -> None:
    import json

    h = tool_harness
    h.call(Read(path="src/main.py"))
    lines = (h.journal.directory / "events.jsonl").read_text().splitlines()
    if damage == "blank":
        lines.insert(1, "")
    elif damage == "duplicate_result":
        record = json.loads(lines[-1])
        record.update(sequence=4, event_id="tools:4")
        lines.append(json.dumps(record))
    else:
        record = json.loads(lines[-1])
        record["sequence" if damage == "sequence" else "task_id"] = (
            9 if damage == "sequence" else "other"
        )
        lines[-1] = json.dumps(record)
    path = tmp_path / "damaged.jsonl"
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises((EventWriteError, ValidationError)):
        inspect_journal(path)


def test_journal_replacement_is_detected(tool_harness: ToolHarness) -> None:
    h = tool_harness
    path = h.journal.directory / "events.jsonl"
    path.rename(path.with_suffix(".preserved"))
    path.write_text("")
    with pytest.raises(EventWriteError):
        h.call(Patch(path="src/never.py", expected_sha256=None, content="no"))
    assert not (h.root / "src/never.py").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS backend integration")
def test_process_reads_cannot_bypass_scope_with_aliases(
    tool_harness: ToolHarness, tmp_path: Path
) -> None:
    h = tool_harness
    target = h.root / "secrets/private"
    (h.root / "src/link").symlink_to(target)
    for path in (h.root / "src/link", h.root / "SECRETS/private", h.root / ".AGENT/state.json"):
        result = h.call(
            Shell(argv=(sys.executable, "-c", f"print(open({str(path)!r}).read())")), approve=True
        )
        assert result.status == "failed" and result.exit_code != 0, result.output
        assert "confidential" not in result.output and "authoritative" not in result.output
    os.link(target, h.root / "src/hard")
    result = h.call(
        Shell(argv=(sys.executable, "-c", "print(open('src/hard').read())")), approve=True
    )
    assert result.status == "failed" and "confidential" not in result.output


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS backend integration")
def test_no_environment_or_open_descriptor_leaks(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = tool_harness
    monkeypatch.setenv("P03_TEST_CREDENTIAL", "do-not-inherit")
    fd = os.open(h.journal.directory / "events.jsonl", os.O_RDONLY)
    try:
        os.set_inheritable(fd, True)
        code = f"import os; assert 'P03_TEST_CREDENTIAL' not in os.environ; os.read({fd}, 1)"
        result = h.call(Shell(argv=(sys.executable, "-c", code)), approve=True)
        assert result.status == "failed" and "Bad file descriptor" in result.output
        assert "do-not-inherit" not in result.output
    finally:
        os.close(fd)


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS backend integration")
def test_unix_socket_is_denied(tool_harness: ToolHarness) -> None:
    import socket
    from tempfile import TemporaryDirectory

    h = tool_harness
    with (
        TemporaryDirectory(dir="/tmp", prefix="p03-") as directory,
        socket.socket(socket.AF_UNIX) as server,
    ):
        path = Path(directory) / "service.sock"
        server.bind(str(path))
        server.listen(1)
        code = f"import socket; socket.socket(socket.AF_UNIX).connect({str(path)!r})"
        result = h.call(Shell(argv=(sys.executable, "-c", code)), approve=True)
        assert result.status == "failed" and "Operation not permitted" in result.output


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS backend integration")
def test_cancelled_process_is_reaped_and_recorded(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = tool_harness
    h.runtime.timeout = 10
    launched = []
    original = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await original(*args, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    request = h.request(Shell(argv=(sys.executable, "-c", "import time; time.sleep(60)")))

    async def cancel():
        operation = asyncio.create_task(
            h.runtime.execute(request, approval=h.approval_for(request))
        )
        await asyncio.sleep(0.1)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation

    asyncio.run(cancel())
    assert len(launched) == 1 and launched[0].returncode == -9
    with pytest.raises(ProcessLookupError):
        os.kill(launched[0].pid, 0)
    records = inspect_journal(h.journal.directory / "events.jsonl")
    assert not records.unresolved
    assert records.events[-1].tool_result.status == "interrupted"
    assert h.call(Read(path="src/main.py")).status == "succeeded"


@pytest.mark.parametrize("fail_result", [False, True])
def test_workflow_and_tools_share_one_durable_sequence(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch, fail_result: bool
) -> None:
    from coding_agent.core.workflow import CoderResult, VerificationResult, WorkflowEngine
    from coding_agent.testing import FakeReviewer, FakeVerifier

    h = tool_harness
    if fail_result:
        original = h.journal.write

        def fail(event):
            if event.kind == "tool_finished":
                raise EventWriteError("injected lost result")
            original(event)

        monkeypatch.setattr(h.journal, "write", fail)

    class ToolCoder:
        async def implement(self, *args, **kwargs):
            request = h.request(
                Patch(path="src/integration.py", expected_sha256=None, content="actual file\n")
            )
            result = await h.runtime.execute(request)
            assert result.status == "succeeded"
            return CoderResult(
                outcome="implemented", summary="tool completed; verification required"
            )

    verifier = FakeVerifier([VerificationResult(evidence=())])
    engine = WorkflowEngine(
        h.spec,
        coder=ToolCoder(),
        verifier=verifier,
        reviewer=FakeReviewer([]),
        read_revision=lambda: REVISION,
        writer=h.journal,
    )
    if fail_result:
        with pytest.raises(EventWriteError):
            asyncio.run(engine.run(h.approval))
        assert engine.result is None and not verifier.calls
    else:
        result = asyncio.run(engine.run(h.approval))
        assert result.outcome == "blocked"  # A real patch is not verification evidence.
        assert len(verifier.calls) == 1
    assert (h.root / "src/integration.py").read_text() == "actual file\n"
    records = inspect_journal(h.journal.directory / "events.jsonl")
    assert bool(records.unresolved) is fail_result
    assert [e.sequence for e in records.events] == list(range(1, len(records.events) + 1))
    kinds = [e.kind for e in records.events]
    assert kinds.index("coder_started") < kinds.index("tool_requested")
    if not fail_result:
        assert kinds.index("tool_finished") < kinds.index("coder_finished")
        assert kinds[-1] == "session_finished"


def test_patch_preserves_mode_and_records_missing_newline(tool_harness: ToolHarness) -> None:
    import stat

    h = tool_harness
    path = h.root / "src/main.py"
    path.chmod(0o751)
    result = h.call(
        Patch(
            path="src/main.py",
            expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            content="without newline",
        )
    )
    assert result.status == "succeeded" and stat.S_IMODE(path.stat().st_mode) == 0o751
    assert path.read_text() == "without newline"
    diff = (h.journal.directory / result.artifacts[0].path).read_text()
    assert "+without newline\n\\ No newline at end of file\n" in diff


def test_size_limits_do_not_turn_partial_content_into_success(tool_harness: ToolHarness) -> None:
    h = tool_harness
    h.runtime.files.max_bytes = 8
    assert h.call(Read(path="src/main.py")).status == "failed"
    result = h.call(Patch(path="src/large", expected_sha256=None, content="x" * 9))
    assert result.status == "failed" and not (h.root / "src/large").exists()
    assert h.call(Search(query="hello")).truncated


def test_lifecycle_cannot_advance_past_an_unresolved_request(tool_harness: ToolHarness) -> None:
    from coding_agent.core.workflow import WorkflowEvent

    h = tool_harness
    request = h.request(Read(path="src/main.py"))
    h.journal.write(
        WorkflowEvent(
            event_id="tools:2",
            sequence=2,
            timestamp=STAMP,
            session_id="tools",
            task_id=h.task.id,
            revision=REVISION,
            kind="tool_requested",
            reason="pending request",
            tool_request=request,
        )
    )
    with pytest.raises(EventWriteError):
        h.journal.write(
            WorkflowEvent(
                event_id="tools:3",
                sequence=3,
                timestamp=STAMP,
                session_id="tools",
                task_id=None,
                revision=REVISION,
                kind="session_finished",
                reason="must not advance",
            )
        )
    assert len(inspect_journal(h.journal.directory / "events.jsonl").events) == 2
