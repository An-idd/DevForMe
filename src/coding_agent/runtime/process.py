"""macOS read-only, offline, single-process backend. No unsandboxed fallback."""

import asyncio
import json
import os
import signal
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..core.models import TaskSpec
from ..core.tool_policy import CONTROL_NAMES, glob_matches


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int
    output: str
    truncated: bool
    timed_out: bool


class ProcessBackend(Protocol):
    def denial(self, task: TaskSpec, *, git: bool = False) -> str | None: ...

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        root: Path,
        task: TaskSpec,
        protected: Path,
        timeout: float,
        max_output_bytes: int,
        git: bool = False,
    ) -> ProcessOutcome: ...


def default_process_backend(*, runtime_roots: tuple[Path, ...] = ()) -> ProcessBackend:
    if sys.platform == "win32":
        from .windows_process import WindowsReadOnlyProcess

        return WindowsReadOnlyProcess(runtime_roots=runtime_roots)
    return MacReadOnlyProcess(runtime_roots=runtime_roots)


class MacReadOnlyProcess:
    def __init__(self, *, runtime_roots: tuple[Path, ...] = ()) -> None:
        self.runtime_roots = tuple(path.resolve(strict=True) for path in runtime_roots)

    def denial(self, task: TaskSpec, *, git: bool = False) -> str | None:
        if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
            return "verified macOS process backend unavailable; no unsandboxed fallback"
        if not git and task.permissions.network:
            return "network-enabled execution is unsupported by the offline backend"
        if not git and task.permissions.database != "deny":
            return "test database provisioning/isolation is unsupported"
        for pattern in task.scope.forbidden:
            prefix = pattern[:-3] if pattern.endswith("/**") else pattern
            if any(char in prefix for char in "*?["):
                return "process backend supports literal or directory/** exclusions only"
        return None

    def profile(self, root: Path, task: TaskSpec, protected: Path, *, git: bool = False) -> str:
        def quoted(path: Path) -> str:
            return json.dumps(str(path), ensure_ascii=False)

        allowed = (
            Path("/System/Library"),
            Path("/usr/lib"),
            Path("/usr/bin"),
            Path("/bin"),
            Path("/usr/share/locale"),
            Path("/usr/share/zoneinfo"),
            Path("/System/Cryptexes/OS"),
            Path("/System/Volumes/Preboot/Cryptexes/OS"),
            root,
            *self.runtime_roots,
        )
        rules = [
            "(version 1)",
            "(deny default)",
            "(allow process-exec)",
            "(allow file-read-metadata)",
            '(allow file-read-data (literal "/"))',
            '(allow file-write-data (literal "/dev/null"))',
            "(allow file-read* file-map-executable "
            + " ".join(f"(subpath {quoted(p)})" for p in allowed)
            + ")",
            '(allow file-read* (literal "/dev/null") '
            '(literal "/dev/urandom") (literal "/dev/random"))',
            f"(deny file-read* file-map-executable (subpath {quoted(protected)}))",
            "(deny file-read* file-map-executable "
            '(regex #"/[.]([aA][gG][eE][nN][tT][sS]?|[cC][oO][dD][eE][xX])(/|$)"))',
        ]
        if not git:
            rules.append('(deny file-read* file-map-executable (regex #"/[.][gG][iI][tT](/|$)"))')
        for pattern in task.scope.forbidden:
            prefix = pattern[:-3] if pattern.endswith("/**") else pattern
            rules.append(f"(deny file-read* file-map-executable (subpath {quoted(root / prefix)}))")
        # No persistent writes, network (including Unix sockets), Mach IPC or process-fork.
        return "\n".join(rules)

    def validate_inputs(self, root: Path, task: TaskSpec, *, git: bool) -> None:
        """Reject pre-existing hardlink aliases; the kernel separately resolves symlinks.

        Workspace ownership is exclusive to the controller during execution. Trusted
        runtime roots must not be writable by repository code or contain controller data.
        """
        pending = [root]
        count = 0
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    count += 1
                    if count > 20_000:
                        raise ValueError("workspace exceeds process input inspection limit")
                    if entry.name.casefold() in CONTROL_NAMES and not (
                        git and entry.name == ".git"
                    ):
                        continue
                    path = Path(entry.path)
                    relative = path.relative_to(root).as_posix()
                    if any(
                        glob_matches(relative.casefold(), p.casefold())
                        for p in task.scope.forbidden
                    ):
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                        raise ValueError("hardlinked process input could alias protected data")

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        root: Path,
        task: TaskSpec,
        protected: Path,
        timeout: float,
        max_output_bytes: int,
        git: bool = False,
    ) -> ProcessOutcome:
        if reason := self.denial(task, git=git):
            raise ValueError(reason)
        if not argv or not Path(argv[0]).is_absolute() or any("\x00" in arg for arg in argv):
            raise ValueError(
                "process executable must be an absolute path; NUL arguments are invalid"
            )
        self.validate_inputs(root, task, git=git)
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "TMPDIR": "/nonexistent",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "",
        }
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/sandbox-exec",
            "-p",
            self.profile(root, task, protected, git=git),
            *argv,
            cwd=cwd,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
        assert process.stdout is not None
        output = bytearray()
        truncated = False

        async def drain() -> None:
            nonlocal truncated
            assert process.stdout is not None
            while chunk := await process.stdout.read(16384):
                remaining = max_output_bytes - len(output)
                output.extend(chunk[:remaining])
                truncated |= len(chunk) > remaining

        reader = asyncio.create_task(drain())
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            self._kill(process.pid)
            await process.wait()
        except BaseException:
            self._kill(process.pid)
            await process.wait()
            await reader
            raise
        await reader
        assert process.returncode is not None
        return ProcessOutcome(
            process.returncode, output.decode("utf-8", errors="replace"), truncated, timed_out
        )

    @staticmethod
    def _kill(pid: int) -> None:
        if sys.platform == "win32":
            raise ValueError("POSIX process groups unavailable")
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
