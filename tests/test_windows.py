"""Real Windows filesystem and LPAC integration on temporary inputs."""

import asyncio
import os
import shutil
import socket
import sys
from pathlib import Path

import pytest
from test_tools import tool_harness as tool_harness

from coding_agent.core.tools import Git, Patch, Read, Search, Shell
from coding_agent.runtime.windows_process import WindowsReadOnlyProcess
from coding_agent.session.records import Sanitizer, inspect_journal

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real Windows integration")


@pytest.fixture(scope="session")
def python_runtime(tmp_path_factory):
    directory = tmp_path_factory.mktemp("windows-python")
    base = Path(sys.base_prefix)
    shutil.copyfile(base / "python.exe", directory / "python.exe")
    for path in base.glob("*.dll"):
        shutil.copyfile(path, directory / path.name)
    for name in ("Lib", "DLLs"):
        if (base / name).is_dir():
            shutil.copytree(
                base / name,
                directory / name,
                ignore=shutil.ignore_patterns("site-packages", "__pycache__"),
            )
    return directory


@pytest.fixture
def windows_harness(tool_harness, python_runtime):
    h = tool_harness
    candidate = Path(shutil.which("git")).parent.parent / "mingw64/bin/git.exe"
    assert candidate.is_file(), "Git for Windows native executable required"
    h.runtime.git_executable = candidate
    h.backend = WindowsReadOnlyProcess(runtime_roots=(python_runtime,))
    h.runtime.backend = h.backend
    h.runtime.timeout = 150
    h.python = str(python_runtime / "python.exe")
    return h


def test_windows_shell_approval_and_real_read(windows_harness):
    h = windows_harness
    request = Shell(argv=(h.python, "-c", "print(open('src/main.py').read(), end='')"))
    assert h.call(request).status == "needs_approval"
    result = h.call(request, approve=True)
    assert result.status == "succeeded", (result.reason, result.exit_code, result.output)
    assert "print('hello')" in result.output
    assert not inspect_journal(h.journal.directory / "events.jsonl").unresolved


@pytest.mark.parametrize(
    "action",
    [
        "write_copy",
        "write_original",
        "read_original",
        "read_control",
        "read_forbidden",
        "read_external",
        "spawn",
        "environment",
    ],
)
def test_windows_process_boundaries(windows_harness, tmp_path, monkeypatch, action):
    h = windows_harness
    external = tmp_path / "private.txt"
    external.write_text("outside-secret")
    monkeypatch.setenv("P03_PRIVATE_CREDENTIAL", "must-not-inherit")
    code = {
        "write_copy": "open('src/main.py', 'w').write('bad')",
        "write_original": f"open({str(h.root / 'src/main.py')!r}, 'w').write('bad')",
        "read_original": f"print(open({str(h.root / 'src/main.py')!r}).read())",
        "read_control": f"print(open({str(h.journal.directory / 'events.jsonl')!r}).read())",
        "read_forbidden": "print(open('secrets/private').read())",
        "read_external": f"print(open({str(external)!r}).read())",
        "spawn": (
            "import subprocess,sys; subprocess.run([sys.executable,'-c','print(123)'],check=True)"
        ),
        "environment": (
            "import os; assert 'P03_PRIVATE_CREDENTIAL' not in os.environ; print('clean')"
        ),
    }[action]
    result = h.call(Shell(argv=(h.python, "-c", code)), approve=True)
    if action == "environment":
        assert result.status == "succeeded" and "clean" in result.output
    else:
        assert result.status == "failed" and result.exit_code != 0, result
    assert "outside-secret" not in result.output and "must-not-inherit" not in result.output
    assert (h.root / "src/main.py").read_text() == "print('hello')\n"
    assert external.read_text() == "outside-secret"


@pytest.mark.parametrize("destination", ["loopback", "external"])
def test_windows_network_denied(windows_harness, destination):
    h = windows_harness
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        # Reserved TEST-NET address checks outbound denial independently of Windows'
        # separate AppContainer loopback restriction; no server response is required.
        address = server.getsockname() if destination == "loopback" else ("203.0.113.1", 9)
        code = f"import socket; socket.create_connection({address!r}, timeout=1)"
        result = h.call(Shell(argv=(h.python, "-c", code)), approve=True)
    assert result.status == "failed" and result.exit_code != 0, result
    assert "10013" in result.output, result.output


def test_windows_git_diff_filters_index_and_redacts(windows_harness):
    import subprocess

    h = windows_harness
    secret = "-----BEGIN PRIVATE KEY-----\nSYNTHETIC-KEY-BODY\n-----END PRIVATE KEY-----\n"
    h.journal.sanitizer = Sanitizer((secret,))
    (h.root / "src/main.py").write_text(secret, newline="\n")
    subprocess.run([str(h.runtime.git_executable), "init", "-q", str(h.root)], check=True)
    subprocess.run(
        [str(h.runtime.git_executable), "-C", str(h.root), "add", "src/main.py", "secrets/private"],
        check=True,
    )
    (h.root / "secrets/private").unlink()
    (h.root / "src/main.py").write_text("public-change\n")
    result = h.call(Git(operation="diff"))
    assert result.status == "succeeded", (result.reason, result.exit_code, result.output)
    assert "+public-change" in result.output and "[REDACTED]" in result.output
    assert "confidential" not in result.output
    assert all(line not in result.output for line in secret.splitlines())


def test_windows_real_timeout_and_output_limit(windows_harness):
    h = windows_harness
    # Includes preparation in the deadline. The separate output test uses the default budget.
    h.runtime.timeout = 15
    result = h.call(
        Shell(argv=(h.python, "-c", "import time; print('started',flush=True); time.sleep(60)")),
        approve=True,
    )
    assert result.status == "timed_out", result
    assert "started" in result.output
    h.runtime.timeout = 150
    result = h.call(Shell(argv=(h.python, "-c", "print('x'*100000)")), approve=True)
    assert result.status == "succeeded" and result.truncated, result
    assert len(result.output.encode()) <= h.runtime.max_output_bytes


@pytest.mark.parametrize(
    "path",
    [
        "src/NUL",
        "src/name.",
        "src/name ",
        "src/file:stream",
        "src/CON.txt",
        ".agent./events.jsonl",
    ],
)
def test_windows_ambiguous_paths_cannot_mutate(tool_harness, path):
    h = tool_harness
    result = h.call(Patch(path=path, expected_sha256=None, content="bad"))
    assert result.status in {"denied", "failed"}
    assert not inspect_journal(h.journal.directory / "events.jsonl").unresolved


def test_windows_hardlinks_and_junctions_rejected(tool_harness, tmp_path):
    import subprocess

    h = tool_harness
    external = tmp_path / "external"
    external.mkdir()
    (external / "private.txt").write_text("outside-secret")
    os.link(external / "private.txt", h.root / "src/hard")
    junction = h.root / "src/junction"
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(external)],
        check=True,
        capture_output=True,
    )
    try:
        for path in ("src/hard", "src/junction/private.txt"):
            assert h.call(Read(path=path)).status == "failed"
            result = h.call(Patch(path=path, expected_sha256=None, content="bad"))
            assert result.status == "failed"
        search = h.call(Search(query="outside-secret"))
        assert "outside-secret" not in search.output and search.truncated
        assert (external / "private.txt").read_text() == "outside-secret"
    finally:
        # Remove just the verified test junction; never recurse into its target.
        assert junction.parent == h.root / "src" and junction.is_junction()
        os.rmdir(junction)


def test_windows_cancellation_reaps_process_and_cleans_profile(windows_harness, monkeypatch):
    import ctypes
    from ctypes import wintypes as w

    from coding_agent.runtime import _lowbox

    h = windows_harness
    profiles = []
    processes = []
    original_init = _lowbox.Lowbox.__init__
    original_create = _lowbox.create_process
    original_resume = _lowbox.resume
    open_process = _lowbox.bind(_lowbox.kernel, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)

    def initialize(box):
        original_init(box)
        profiles.append(box.directory)

    def create(*args):
        ok = original_create(*args)
        if ok:
            process = ctypes.cast(args[-1], ctypes.POINTER(_lowbox.ProcessInfo)).contents
            handle = open_process(0x100000, False, process.pid)
            assert handle
            processes.append(handle)
        return ok

    async def scenario():
        started = asyncio.Event()

        def resume(handle):
            result = original_resume(handle)
            started.set()
            return result

        monkeypatch.setattr(_lowbox, "resume", resume)
        request = h.request(Shell(argv=(h.python, "-c", "import time; time.sleep(60)")))
        running = asyncio.create_task(h.runtime.execute(request, approval=h.approval_for(request)))
        await asyncio.wait_for(started.wait(), timeout=30)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    monkeypatch.setattr(_lowbox.Lowbox, "__init__", initialize)
    monkeypatch.setattr(_lowbox, "create_process", create)
    try:
        asyncio.run(scenario())
        assert processes and all(_lowbox.wait(handle, 0) == 0 for handle in processes)
        assert profiles and all(not path.exists() for path in profiles)
        view = inspect_journal(h.journal.directory / "events.jsonl")
        assert not view.unresolved
        assert '"status":"interrupted"' in (h.journal.directory / "events.jsonl").read_text()
    finally:
        for handle in processes:
            _lowbox.close(handle)


def test_windows_does_not_inherit_extra_handles(windows_harness, tmp_path):
    import msvcrt

    h = windows_harness
    private = tmp_path / "private-handle.txt"
    private.write_text("handle-secret")
    with private.open("rb") as stream:
        handle = msvcrt.get_osfhandle(stream.fileno())
        os.set_handle_inheritable(handle, True)
        code = (
            "import os,msvcrt; "
            f"fd=msvcrt.open_osfhandle({handle},os.O_RDONLY); print(os.read(fd,100))"
        )
        try:
            result = h.call(Shell(argv=(h.python, "-c", code)), approve=True)
        finally:
            os.set_handle_inheritable(handle, False)
    assert result.status == "failed" and result.exit_code != 0, result
    assert "handle-secret" not in result.output


def test_windows_preparation_deadline_prevents_launch(windows_harness, monkeypatch):
    from coding_agent.runtime import _lowbox

    h = windows_harness
    h.runtime.timeout = 0.000001

    def unexpected(*args):
        pytest.fail("process launched after preparation deadline")

    monkeypatch.setattr(_lowbox, "create_process", unexpected)
    result = h.call(Shell(argv=(h.python, "-c", "print('unexpected')")), approve=True)
    assert result.status == "timed_out" and not result.output


def test_windows_patch_preserves_dacl_and_crlf(tool_harness):
    import ctypes
    import hashlib
    from ctypes import wintypes as w

    from coding_agent.runtime import _lowbox, _winfiles

    h = tool_harness
    target = h.root / "src/main.py"
    target.write_bytes(b"before\r\n")
    descriptor = ctypes.c_void_p()
    _lowbox.checked(
        _lowbox.convert_sd(
            "D:P(A;;FA;;;OW)(A;;FA;;;SY)",
            1,
            ctypes.byref(descriptor),
            None,
        )
    )
    try:
        _lowbox.checked(_winfiles._set_security(str(target), 4 | 0x80000000, descriptor))
    finally:
        _lowbox.local_free(descriptor)

    def dacl():
        size = w.DWORD()
        _winfiles._get_security(str(target), 4, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        _lowbox.checked(
            _winfiles._get_security(
                str(target),
                4,
                buffer,
                size,
                ctypes.byref(size),
            )
        )
        return buffer.raw

    before = dacl()
    assert h.call(Read(path="src/main.py")).output == "before\r\n"
    result = h.call(
        Patch(
            path="src/main.py",
            expected_sha256=hashlib.sha256(b"before\r\n").hexdigest(),
            content="after\r\n",
        )
    )
    assert result.status == "succeeded", result
    assert target.read_bytes() == b"after\r\n" and dacl() == before


def test_windows_short_path_alias_cannot_bypass_scope(tool_harness):
    import ctypes
    from ctypes import wintypes as w

    from coding_agent.runtime import _winfiles

    h = tool_harness
    target = h.root / "secrets/private file long name.txt"
    target.write_text("alias-secret")
    short_path = _winfiles.bind(
        _winfiles.kernel,
        "GetShortPathNameW",
        w.DWORD,
        w.LPCWSTR,
        w.LPWSTR,
        w.DWORD,
    )
    buffer = ctypes.create_unicode_buffer(32768)
    assert short_path(str(target), buffer, len(buffer))
    alias = Path(buffer.value).name
    if alias == target.name:
        pytest.skip("8.3 aliases are disabled on this test volume")
    # Direct backend rejects an alias even when its spelling is not in forbidden.
    from coding_agent.core import ScopePolicy
    from coding_agent.runtime.filesystem import SafeFiles

    files = SafeFiles(h.root, ScopePolicy(allowed=("**",), forbidden=()))
    with pytest.raises(ValueError, match="alias"):
        files.read("secrets/" + alias)


def test_windows_git_status_and_clean_diff(windows_harness):
    import subprocess

    h = windows_harness

    def git(*args):
        return subprocess.run(
            [str(h.runtime.git_executable), "-C", str(h.root), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", "src/main.py", "secrets/private")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    clean = h.call(Git(operation="diff"))
    assert clean.status == "succeeded" and clean.output == "", clean
    (h.root / "src/main.py").write_text("changed\n", newline="\n")
    (h.root / "new.txt").write_text("untracked\n")
    result = h.call(Git(operation="status"))
    assert result.status == "succeeded", result
    assert " M src/main.py" in result.output and "?? new.txt" in result.output
    assert "secrets/private" not in result.output and ".agent" not in result.output
    assert git("status", "--porcelain", "--", "src", "new.txt").strip() == result.output.strip()


@pytest.mark.parametrize(
    "unsupported",
    [
        "attributes",
        "config_filter",
        "config_include",
        "alternates",
        "index_flags",
        "submodule",
        "symlink",
        "conflict",
    ],
)
def test_windows_git_unsupported_inputs_fail_closed(windows_harness, unsupported):
    import subprocess

    h = windows_harness
    subprocess.run([str(h.runtime.git_executable), "init", "-q", str(h.root)], check=True)
    if unsupported == "attributes":
        (h.root / ".gitattributes").write_text("*.py filter=lfs\n")
    elif unsupported == "config_filter":
        with (h.root / ".git/config").open("a") as stream:
            stream.write('\n[filter "bad"]\nclean = echo unexpected\n')
    elif unsupported == "config_include":
        with (h.root / ".git/config").open("a") as stream:
            stream.write("\n[include]\npath = /outside/secret-config\n")
    elif unsupported == "alternates":
        (h.root / ".git/objects/info/alternates").write_text(str(h.root.parent))
    else:
        git = [str(h.runtime.git_executable), "-C", str(h.root)]
        subprocess.run([*git, "add", "src/main.py"], check=True)
        sha = subprocess.run(
            [*git, "rev-parse", ":src/main.py"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if unsupported == "index_flags":
            subprocess.run([*git, "update-index", "--assume-unchanged", "src/main.py"], check=True)
        elif unsupported in {"submodule", "symlink"}:
            mode = "160000" if unsupported == "submodule" else "120000"
            subprocess.run(
                [*git, "update-index", "--cacheinfo", f"{mode},{sha},src/main.py"],
                check=True,
            )
        else:
            subprocess.run([*git, "update-index", "--force-remove", "src/main.py"], check=True)
            subprocess.run(
                [*git, "update-index", "--index-info"],
                check=True,
                input="".join(
                    f"100644 {sha} {stage}\tsrc/main.py\n" for stage in (1, 2, 3)
                ).encode(),
            )
            unmerged = subprocess.run(
                [*git, "ls-files", "--unmerged"],
                check=True,
                capture_output=True,
            ).stdout
            assert len(unmerged.splitlines()) == 3
    result = h.call(Git(operation="status"))
    assert result.status == "failed" and result.exit_code != 0, result


def test_windows_default_backend_needs_no_system_git(tool_harness, monkeypatch):
    from coding_agent.tools import ToolRuntime

    h = tool_harness
    monkeypatch.setattr(shutil, "which", lambda name: None)
    runtime = ToolRuntime(
        h.spec,
        h.task.id,
        root=h.root,
        journal=h.journal,
        approval=h.approval,
        read_revision=h.runtime.read_revision,
    )
    assert isinstance(runtime.backend, WindowsReadOnlyProcess)
    assert runtime.git_executable == Path(sys.executable).resolve()
    result = asyncio.run(runtime.execute(h.request(Read(path="src/main.py"))))
    assert result.status == "succeeded" and "hello" in result.output


def test_windows_git_binary_crlf_and_executable_index(windows_harness):
    import subprocess

    h = windows_harness
    git = [str(h.runtime.git_executable), "-C", str(h.root)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "config", "core.autocrlf", "false"], check=True)
    binary = h.root / "src/data.bin"
    binary.write_bytes(b"\0before")
    subprocess.run([*git, "add", "src"], check=True)
    subprocess.run([*git, "update-index", "--chmod=+x", "src/main.py"], check=True)
    subprocess.run([*git, "config", "core.autocrlf", "true"], check=True)
    (h.root / "src/main.py").write_bytes(b"print('hello')\r\n")
    binary.write_bytes(b"\0after")
    result = h.call(Git(operation="diff"))
    assert result.status == "succeeded", result
    assert "Binary files a/src/data.bin and b/src/data.bin differ" in result.output
    assert "main.py" not in result.output and "\0after" not in result.output
    native = subprocess.run(
        [*git, "diff", "--no-ext-diff"], check=True, capture_output=True, text=True
    )
    assert result.output == native.stdout


def test_windows_runtime_root_cannot_contain_controller_records(windows_harness, monkeypatch):
    from coding_agent.runtime import _lowbox

    h = windows_harness
    tool = h.journal.directory / "tool"
    tool.mkdir()
    executable = tool / "tool.exe"
    executable.write_bytes(b"not executed")
    h.runtime.backend = WindowsReadOnlyProcess(runtime_roots=(tool,))

    def unexpected(*args):
        pytest.fail("sandbox created with a runtime root inside controller records")

    monkeypatch.setattr(_lowbox.Lowbox, "__init__", unexpected)
    result = h.call(Shell(argv=(str(executable),)), approve=True)
    assert result.status == "failed"


def test_windows_git_reader_does_not_report_read_error_as_deletion(
    windows_harness,
    tmp_path,
    monkeypatch,
):
    import json
    import subprocess

    from coding_agent.runtime._windows_git import main

    h = windows_harness
    git = [str(h.runtime.git_executable), "-C", str(h.root)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "src/main.py"], check=True)
    request = tmp_path / "git-request.json"
    request.write_text(
        json.dumps(
            {
                "root": str(h.root),
                "operation": "diff",
                "paths": ["src/main.py"],
                "forbidden": h.task.scope.forbidden,
            }
        )
    )
    original = Path.read_bytes

    def fail_read(path):
        if path == h.root / "src/main.py":
            raise PermissionError("injected worktree IO failure")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    monkeypatch.setattr(sys, "argv", ["git-helper", str(request)])
    with pytest.raises(PermissionError, match="injected"):
        main()


def test_windows_job_assignment_failure_never_resumes_child(windows_harness, monkeypatch):
    import ctypes

    from coding_agent.runtime import _lowbox

    h = windows_harness

    def deny_assignment(*args):
        ctypes.set_last_error(5)
        return False

    def unexpected(*args):
        pytest.fail("child resumed without its restrictive Job")

    monkeypatch.setattr(_lowbox, "assign_job", deny_assignment)
    monkeypatch.setattr(_lowbox, "resume", unexpected)
    result = h.call(Shell(argv=(h.python, "-c", "print('executed')")), approve=True)
    assert result.status == "failed" and not result.output
    assert not inspect_journal(h.journal.directory / "events.jsonl").unresolved
