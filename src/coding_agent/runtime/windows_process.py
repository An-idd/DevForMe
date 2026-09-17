"""Windows read-only execution copies inside a per-call LPAC and Job Object."""

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from ..core.models import TaskSpec
from ..core.tool_policy import CONTROL_NAMES, path_permitted
from . import _fileio as io
from .process import ProcessOutcome


class WindowsReadOnlyProcess:
    def __init__(self, *, runtime_roots: tuple[Path, ...] = (), verification: bool = False) -> None:
        self.verification = verification
        self.runtime_roots = tuple(path.resolve(strict=True) for path in runtime_roots)

    def denial(self, task: TaskSpec, *, git: bool = False) -> str | None:
        if sys.platform != "win32":
            return "verified Windows process backend unavailable; no unsandboxed fallback"
        if sys.getwindowsversion().build < 17763:
            return "Windows 10 1809 or later is required for the LPAC backend"
        if not git and task.permissions.network:
            return "network-enabled execution is unsupported by the offline backend"
        if not git and task.permissions.database != "deny":
            return "test database provisioning/isolation is unsupported"
        return None

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
        if sys.platform != "win32":
            raise ValueError("Windows process backend unavailable")
        from ._lowbox import Lowbox

        if reason := self.denial(task, git=git):
            raise ValueError(reason)
        if not argv or not Path(argv[0]).is_absolute() or any("\0" in arg for arg in argv):
            raise ValueError("an absolute executable and NUL-free arguments are required")
        executable = Path(argv[0]).resolve(strict=True)
        if not git and not any(executable.is_relative_to(path) for path in self.runtime_roots):
            raise ValueError("executable must be under a controller-configured runtime root")
        if any(
            path.is_relative_to(root)
            or root.is_relative_to(path)
            or protected.is_relative_to(path)
            or path.is_relative_to(protected)
            for path in self.runtime_roots
        ):
            raise ValueError("trusted runtime roots must not overlap workspace or records")
        deadline = monotonic() + timeout
        box = Lowbox()
        try:
            with TemporaryDirectory(prefix="coding-agent-exec-") as temporary:
                staging = Path(temporary).resolve(strict=True)
                box.grant_read(staging)
                total_bytes = 0
                count = 0

                def copy_tree(
                    source: Path,
                    destination: Path,
                    *,
                    repository: bool,
                    exclude: frozenset[str] = frozenset(),
                ) -> None:
                    nonlocal total_bytes, count
                    pending = [(source, destination)]
                    while pending:
                        folder, target = pending.pop()
                        target.mkdir()
                        fd = io.open_directory(folder)
                        try:
                            with io.entries(fd) as listing:
                                for entry in listing:
                                    count += 1
                                    if monotonic() >= deadline:
                                        raise TimeoutError("execution preparation exceeded timeout")
                                    if count > 20_000:
                                        raise ValueError("execution copy exceeds entry limit")
                                    if entry.name in exclude:
                                        continue
                                    original = folder / entry.name
                                    relative = original.relative_to(source)
                                    if repository:
                                        metadata = git and relative.parts[0] == ".git"
                                        if not metadata and not path_permitted(
                                            relative.as_posix(), task.scope
                                        ):
                                            continue
                                        if original == protected or protected in original.parents:
                                            continue
                                    elif any(
                                        part.casefold() in CONTROL_NAMES for part in relative.parts
                                    ):
                                        continue
                                    info = original.lstat()
                                    if getattr(info, "st_file_attributes", 0) & 0x400:
                                        raise ValueError(
                                            "reparse points are unsupported process inputs"
                                        )
                                    output = target / entry.name
                                    if stat.S_ISDIR(info.st_mode):
                                        pending.append((original, output))
                                    elif stat.S_ISREG(info.st_mode):
                                        opened = io.open_at(
                                            fd,
                                            entry.name,
                                            os.O_RDONLY | io.NOFOLLOW,
                                            allow_hardlinks=not repository,
                                        )
                                        with (
                                            os.fdopen(opened, "rb") as reader,
                                            output.open("xb") as writer,
                                        ):
                                            while chunk := reader.read(65_536):
                                                total_bytes += len(chunk)
                                                if monotonic() >= deadline:
                                                    raise TimeoutError(
                                                        "execution preparation exceeded timeout"
                                                    )
                                                if total_bytes > 1024 * 1024 * 1024:
                                                    raise ValueError(
                                                        "execution copy exceeds byte limit"
                                                    )
                                                writer.write(chunk)
                                    else:
                                        raise ValueError("special process input is unsupported")
                        finally:
                            os.close(fd)

                repository = staging / "repo"
                copy_tree(root, repository, repository=True)
                tool_directories: list[str] = []
                mapped: tuple[str, ...]
                if git:
                    # Native Git's normalized DOS-path lookup is denied in AppContainer.
                    # Run the read-only library in the SAME sandbox instead of widening ACLs.
                    python = staging / "python"
                    python.mkdir()
                    base = Path(sys.base_prefix).resolve(strict=True)
                    if any(
                        base.is_relative_to(path) or path.is_relative_to(base)
                        for path in (root, protected)
                    ):
                        raise ValueError("controller Python must not overlap workspace or records")
                    for source in (base / "python.exe", *base.glob("*.dll")):
                        with os.fdopen(io.open_read(source), "rb") as reader:
                            (python / source.name).write_bytes(reader.read())
                    for name in ("Lib", "DLLs"):
                        copy_tree(
                            base / name,
                            python / name,
                            repository=False,
                            exclude=frozenset({"site-packages", "__pycache__"}),
                        )
                    packages = python / "Lib/site-packages"
                    packages.mkdir()
                    for name in ("dulwich", "urllib3"):
                        package = importlib.util.find_spec(name)
                        if package is None or package.origin is None:
                            raise ValueError("Windows Git dependency unavailable")
                        copy_tree(
                            Path(package.origin).parent,
                            packages / name,
                            repository=False,
                            exclude=frozenset({"__pycache__"}),
                        )
                    # Only the helper and dependency-free shared policy enter this runtime.
                    # Empty package initializers avoid loading unrelated product components.
                    for name in ("coding_agent", "coding_agent/core", "coding_agent/runtime"):
                        folder = packages / name
                        folder.mkdir()
                        (folder / "__init__.py").write_text("")
                    for source, module_name in (
                        (Path(__file__).with_name("_windows_git.py"), "runtime/_windows_git.py"),
                        (Path(__file__).parent.parent / "core/paths.py", "core/paths.py"),
                    ):
                        (packages / "coding_agent" / module_name).write_bytes(source.read_bytes())
                    command = next(
                        (arg for arg in argv[1:] if arg in {"status", "ls-files", "diff"}), None
                    )
                    if command is None:
                        raise ValueError("unsupported read-only Git invocation")
                    paths = []
                    if command == "diff":
                        for arg in argv[argv.index("--") + 1 :]:
                            if not arg.startswith(":(top,literal)"):
                                raise ValueError("Git diff requires literal paths")
                            paths.append(arg.removeprefix(":(top,literal)"))
                    request = staging / "git-request.json"
                    request.write_text(
                        json.dumps(
                            {
                                "root": str(repository),
                                "operation": command,
                                "paths": paths,
                                "forbidden": task.scope.forbidden,
                            }
                        ),
                        encoding="utf-8",
                    )
                    tool_directories.append(str(python))
                    mapped = (
                        str(python / "python.exe"),
                        "-I",
                        "-B",
                        "-X",
                        "utf8",
                        "-m",
                        "coding_agent.runtime._windows_git",
                        str(request),
                    )
                else:
                    mapped_executable: Path | None = None
                    for index, runtime_root in enumerate(self.runtime_roots):
                        target = staging / f"tool-{index}"
                        copy_tree(runtime_root, target, repository=False)
                        tool_directories.append(str(target))
                        if executable.is_relative_to(runtime_root):
                            mapped_executable = target / executable.relative_to(runtime_root)
                    assert mapped_executable is not None

                    def map_argument(argument: str) -> str:
                        path = Path(argument)
                        if path.is_absolute() and path.is_relative_to(root):
                            return str(repository / path.relative_to(root))
                        return argument

                    mapped = (str(mapped_executable), *(map_argument(arg) for arg in argv[1:]))
                # Windows uses these for profile/loader setup. No credentials, agent
                # environment, config paths, or arbitrary inherited variables are passed.
                environment = {
                    key: os.environ[key]
                    for key in (
                        "SystemRoot",
                        "WINDIR",
                        "SystemDrive",
                        "USERPROFILE",
                        "LOCALAPPDATA",
                        "APPDATA",
                        "TEMP",
                        "TMP",
                    )
                    if key in os.environ
                }
                environment.update(
                    {
                        "PATH": os.pathsep.join(tool_directories),
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONUTF8": "1",
                        "PYTHONIOENCODING": "utf-8",
                        "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_CONFIG_GLOBAL": os.devnull,
                        "GIT_OPTIONAL_LOCKS": "0",
                        "GIT_TERMINAL_PROMPT": "0",
                        "GIT_PAGER": "",
                        "HOME": str(staging / "no-home"),
                    }
                )
                if self.verification and not git:
                    scratch = staging / "scratch"
                    scratch.mkdir()
                    box.grant_read(scratch, writable=True)
                    environment.update(
                        {
                            name: str(scratch)
                            for name in (
                                "TEMP",
                                "TMP",
                                "TMPDIR",
                                "HOME",
                                "XDG_CACHE_HOME",
                                "RUFF_CACHE_DIR",
                                "MYPY_CACHE_DIR",
                            )
                        }
                    )
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("execution preparation exceeded timeout")
                return await box.run(
                    mapped,
                    repository / cwd.relative_to(root),
                    environment,
                    timeout=remaining,
                    max_output_bytes=max_output_bytes,
                    max_processes=16 if self.verification and not git else 1,
                )
        finally:
            box.close()
