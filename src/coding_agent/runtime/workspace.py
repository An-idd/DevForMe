"""Session-owned Git worktrees with content snapshots and non-destructive reset.

Lifecycle calls are dispatched by ToolRuntime after durable intent. Only fixed Git
plumbing runs here, against a controller-created repository with isolated config;
repository programs still require the process sandbox.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from difflib import unified_diff
from pathlib import Path
from time import monotonic
from uuid import uuid4

from ..core.models import ScopePolicy
from ..core.paths import CONTROL_NAMES, relative_parts
from ..core.tools import ArtifactRef
from ..core.workflow import Revision
from ..core.workspace import WorkspaceOperation, WorkspaceSnapshot, WorkspaceStatus
from ..session.records import JsonlJournal
from . import _fileio as io
from ._snapshots import (
    DEFAULT_EXCLUDED,
    MAX_BYTES,
    capture,
    install_file,
    read_bytes,
    save_bytes,
)


class Workspace:
    def __init__(
        self,
        source: Path,
        directory: Path,
        scope: ScopePolicy,
        *,
        excluded: tuple[str, ...] = DEFAULT_EXCLUDED,
        git_executable: Path | None = None,
    ) -> None:
        self.source = source.resolve(strict=True)
        self.directory = directory.absolute()
        # The existing parent is canonical; never adopt an unknown directory.
        if self.directory.parent.resolve(strict=True) != self.directory.parent:
            raise ValueError("workspace parent must be canonical")
        if self.source.is_relative_to(self.directory) or self.directory.is_relative_to(self.source):
            raise ValueError("session workspace must be outside the source tree")
        if any(part.casefold() in CONTROL_NAMES for part in self.directory.parts):
            raise ValueError("worktree location must not be under a protected control directory")
        self.scope = ScopePolicy.model_validate(scope)
        for pattern in excluded:
            relative_parts(pattern.removesuffix("/**"))
        self.excluded = tuple(sorted(set(excluded)))
        executable = git_executable or Path(shutil.which("git") or "/usr/bin/git")
        self.git = executable.resolve(strict=True)
        if any(self.git.is_relative_to(p) for p in (self.source, self.directory)):
            raise ValueError("Git executable must be controller-owned")
        self.repository = self.directory / "repository.git"
        self.path = self.source
        self.baseline: WorkspaceSnapshot | None = None
        self._metadata: dict[str, str] = {}
        self._closed = False
        self._failed = False
        self._check: Callable[[], None] = lambda: None
        self._deadline = 0.0
        self.operation_artifacts: list[ArtifactRef] = []
        self._journal: JsonlJournal | None = None
        self._identities: dict[Path, tuple[int, int]] = {}
        self._config = json.dumps(
            {
                "version": 1,
                "source": str(self.source),
                "directory": str(self.directory),
                "scope": self.scope.model_dump(mode="json"),
                "excluded": self.excluded,
            },
            sort_keys=True,
        ).encode()
        if self.directory.exists():
            raise ValueError("workspace directory already exists; retain it for inspection")

    @property
    def prepared(self) -> bool:
        return self.baseline is not None and not self._closed and not self._failed

    def snapshot(self) -> WorkspaceSnapshot:
        if self._closed or self._failed:
            raise ValueError("workspace unavailable; inspect retained files before recovery")
        if self.baseline is not None:
            self._validate()
        return capture(self.path, self.scope, self.excluded)[0]

    def revision_reader(
        self, *, plan_version: int, context_revision: str
    ) -> Callable[[], Revision]:
        def read() -> Revision:
            return Revision(
                plan_version=plan_version,
                context_revision=context_revision,
                workspace_revision=self.snapshot().revision,
            )

        return read

    def status(self) -> WorkspaceStatus:
        if self.baseline is None:
            raise ValueError("prepare workspace first")
        return self._status(self.snapshot())

    def _status(self, current: WorkspaceSnapshot) -> WorkspaceStatus:
        assert self.baseline is not None
        before = {item.path: item for item in self.baseline.files}
        after = {item.path: item for item in current.files}
        return WorkspaceStatus(
            baseline_revision=self.baseline.revision,
            current_revision=current.revision,
            added=tuple(sorted(after.keys() - before.keys())),
            deleted=tuple(sorted(before.keys() - after.keys())),
            modified=tuple(
                sorted(path for path in before.keys() & after.keys() if before[path] != after[path])
            ),
        )

    def _save(self, snapshot: WorkspaceSnapshot, payloads: dict[str, bytes]) -> None:
        self._check()
        for digest, data in payloads.items():
            self._check()
            save_bytes(self.directory / "blobs", digest, data)
        self._check()
        save_bytes(
            self.directory / "snapshots",
            snapshot.revision + ".json",
            snapshot.model_dump_json().encode(),
        )

    def _load(self, revision: str) -> tuple[WorkspaceSnapshot, dict[str, bytes]]:
        snapshot = WorkspaceSnapshot.model_validate_json(
            read_bytes(
                self.directory / "snapshots" / (revision + ".json"),
                limit=8 * 1024 * 1024,
            )
        )
        if (
            snapshot.revision != revision
            or snapshot.excluded != self.excluded
            or snapshot.forbidden != self.scope.forbidden
        ):
            raise ValueError("saved snapshot identity/policy mismatch")
        payloads = {}
        total = 0
        for item in snapshot.files:
            data = read_bytes(self.directory / "blobs" / item.sha256)
            if len(data) != item.size_bytes or hashlib.sha256(data).hexdigest() != item.sha256:
                raise ValueError("saved snapshot blob changed")
            total += len(data)
            if total > MAX_BYTES:
                raise ValueError("saved snapshot exceeds byte limit")
            payloads[item.sha256] = data
        return snapshot, payloads

    def diff(self) -> str:
        current, payloads = capture(self.path, self.scope, self.excluded)
        self._validate()
        return self._diff(current, payloads)

    def _diff(self, current: WorkspaceSnapshot, payloads: dict[str, bytes]) -> str:
        assert self.baseline is not None
        baseline, old_data = self._load(self.baseline.revision)
        before = {item.path: item for item in baseline.files}
        after = {item.path: item for item in current.files}
        chunks: list[str] = []
        size = 0
        for path in sorted(before.keys() | after.keys()):
            old, new = before.get(path), after.get(path)
            if old == new:
                continue
            left = old_data[old.sha256] if old else b""
            right = payloads[new.sha256] if new else b""
            header = f"diff --workspace {json.dumps(path)}\n"
            header += f"old mode {old.mode:o}\n" if old else "new file\n"
            header += f"new mode {new.mode:o}\n" if new else "deleted file\n"
            try:
                if b"\0" in left or b"\0" in right:
                    raise UnicodeError
                lines = unified_diff(
                    left.decode("utf-8").splitlines(keepends=True),
                    right.decode("utf-8").splitlines(keepends=True),
                    fromfile="a/" + path if old else "/dev/null",
                    tofile="b/" + path if new else "/dev/null",
                )
                body = "".join(
                    line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                    for line in lines
                )
            except UnicodeError:
                body = f"Binary content: {old.sha256 if old else '-'} -> "
                body += f"{new.sha256 if new else '-'}\n"
            chunk = header + body + "\n"
            size += len(chunk.encode())
            if size > MAX_BYTES * 3:
                raise ValueError("diff exceeds inspection limit")
            chunks.append(chunk)
        return "".join(chunks)

    def _run(self, *args: str, data: bytes | None = None, worktree: Path | None = None) -> bytes:
        self._check()
        remaining = self._deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("workspace operation timeout")
        env = {
            "PATH": str(self.git.parent),
            "HOME": str(self.directory / "home"),
            "USERPROFILE": str(self.directory / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Workspace",
            "GIT_AUTHOR_EMAIL": "workspace@localhost",
            "GIT_COMMITTER_NAME": "Workspace",
            "GIT_COMMITTER_EMAIL": "workspace@localhost",
            "LC_ALL": "C",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        if os.name == "nt":
            env["SystemRoot"] = os.environ["SystemRoot"]
        gitdir = self.repository if worktree is None else worktree / ".git"
        command = (
            str(self.git),
            "--no-pager",
            f"--git-dir={gitdir}",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.autocrlf=false",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            "-c",
            f"safe.directory={self.repository}",
            "-c",
            f"safe.directory={worktree or self.repository}",
            *args,
        )
        assert self._journal is not None
        self.operation_artifacts.append(
            self._journal.artifact(
                "gitrequest",
                json.dumps(
                    {
                        "argv": command,
                        "cwd": str(self.directory),
                        "timeout": remaining,
                    }
                ),
            )
        )
        self._check()
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            result = subprocess.run(
                command,
                input=data,
                capture_output=True,
                cwd=self.directory,
                env=env,
                timeout=remaining,
                check=False,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as error:
            self.operation_artifacts.append(
                self._journal.artifact(
                    "gitresult",
                    json.dumps({"operation": args[0], "timed_out": True}),
                )
            )
            raise TimeoutError("workspace Git timeout; inspect retained state") from error
        self.operation_artifacts.append(
            self._journal.artifact(
                "gitresult",
                json.dumps(
                    {
                        "operation": args[0],
                        "exit_code": result.returncode,
                        "stdout": result.stdout[:65536].decode("utf-8", errors="replace"),
                        "stderr": result.stderr[:65536].decode("utf-8", errors="replace"),
                        "truncated": len(result.stdout) > 65536 or len(result.stderr) > 65536,
                    }
                ),
            )
        )
        if result.returncode:
            raise ValueError(f"controller Git {args[0]} exited {result.returncode}")
        return result.stdout

    def _validate(self) -> None:
        if self.baseline is None or self._closed or self._failed:
            raise ValueError("workspace is not active")
        for name in ("objects/info/alternates", "info/grafts", "refs/replace", "config.worktree"):
            if os.path.lexists(self.repository / name):
                raise ValueError("unowned Git metadata extension is unsupported")
        for folder, identity in self._identities.items():
            fd = io.open_directory(folder)
            try:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != identity:
                    raise ValueError("workspace directory identity changed")
            finally:
                os.close(fd)
        for path, digest in self._metadata.items():
            if hashlib.sha256(read_bytes(Path(path), limit=8 * 1024 * 1024)).hexdigest() != digest:
                raise ValueError("owned Git metadata changed; refusing workspace operation")
        if self.path.parent != self.directory or not self.path.name.startswith("worktree-"):
            raise ValueError("worktree ownership mismatch")

    def _remember_metadata(self) -> None:
        gitdir = self.repository / "worktrees" / self.path.name
        paths = (
            self.directory / "owner.json",
            self.repository / "config",
            self.path / ".git",
            gitdir / "commondir",
            gitdir / "gitdir",
            gitdir / "HEAD",
            gitdir / "index",
            self.repository / "HEAD",
            self.repository / "refs/heads/baseline",
        )
        self._identities = {}
        for folder in {path.parent for path in paths} | {
            self.directory,
            self.repository / "worktrees",
            self.directory / "blobs",
            self.directory / "snapshots",
        }:
            fd = io.open_directory(folder)
            try:
                info = os.fstat(fd)
                self._identities[folder] = (info.st_dev, info.st_ino)
            finally:
                os.close(fd)
        self._metadata = {
            str(path): hashlib.sha256(read_bytes(path, limit=8 * 1024 * 1024)).hexdigest()
            for path in paths
        }

    def _create_tree(self, snapshot: WorkspaceSnapshot, payloads: dict[str, bytes]) -> Path:
        target = self.directory / ("worktree-" + uuid4().hex)
        self._run(
            "worktree", "add", "--detach", "--no-checkout", str(target), "refs/heads/baseline"
        )
        self._run("read-tree", "refs/heads/baseline", worktree=target)
        for item in snapshot.files:
            self._check()
            install_file(target, item, payloads[item.sha256])
        if capture(target, self.scope, self.excluded)[0] != snapshot:
            raise ValueError("prepared worktree does not match snapshot")
        return target

    def _prepare(self, expected: str) -> WorkspaceSnapshot:
        if self.baseline is not None or self.directory.exists():
            raise ValueError("workspace already prepared or partial preparation retained")
        snapshot, payloads = capture(self.source, self.scope, self.excluded)
        if snapshot.revision != expected:
            raise ValueError("source changed after plan authorization")
        self._check()
        self.directory.mkdir(mode=0o700)
        for name in ("home", "blobs", "snapshots"):
            (self.directory / name).mkdir(mode=0o700)
        save_bytes(self.directory, "owner.json", self._config)
        self._save(snapshot, payloads)
        self._run(
            "init", "--bare", "--template=", "--initial-branch=baseline", str(self.repository)
        )
        stream = bytearray(
            b"commit refs/heads/baseline\n"
            b"committer Workspace <workspace@localhost> 0 +0000\ndata 8\nbaseline\n"
        )
        for item in snapshot.files:
            data = payloads[item.sha256]
            stream.extend(
                f"M {item.mode:o} inline {json.dumps(item.path, ensure_ascii=False)}\n".encode()
            )
            stream.extend(f"data {len(data)}\n".encode() + data + b"\n")
        stream.extend(b"\ndone\n")
        self._run("fast-import", "--quiet", "--done", data=bytes(stream))
        target = self._create_tree(snapshot, payloads)
        if capture(self.source, self.scope, self.excluded)[0] != snapshot:
            raise ValueError("source changed during preparation; retained candidate for inspection")
        self.path, self.baseline = target, snapshot
        self._remember_metadata()
        return snapshot

    def _cleanup(self, current: WorkspaceSnapshot) -> None:
        if current != self.baseline:
            raise ValueError("modified workspace retained; reset with a recovery copy first")
        # Git ignores empty directories and ignored files. They still belong to the
        # user: refuse removal if anything outside the exact baseline is present.
        permitted = {item.path for item in current.files} | {".git"}
        directories = {
            str(p) for item in current.files for p in Path(item.path).parents if str(p) != "."
        }
        directories = {p.replace("\\", "/") for p in directories}
        pending = [self.path]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    relative = path.relative_to(self.path).as_posix()
                    if entry.is_dir(follow_symlinks=False):
                        if relative not in directories:
                            raise ValueError("unowned directory retained during cleanup")
                        pending.append(path)
                    elif relative not in permitted or entry.is_symlink():
                        raise ValueError("unowned file retained during cleanup")
        self._validate()
        if self.snapshot() != current:
            raise ValueError("workspace changed during cleanup; preserving current files")
        self._run("worktree", "remove", str(self.path))
        self._closed = True

    def perform(
        self,
        operation: WorkspaceOperation,
        journal: JsonlJournal,
        *,
        approved_revision: str,
        timeout: float,
    ) -> tuple[str, tuple[ArtifactRef, ...]]:
        """Internal ToolRuntime dispatch, never an Agent capability."""
        self._deadline = monotonic() + timeout

        def check() -> None:
            journal.check_writable()
            if monotonic() >= self._deadline:
                raise TimeoutError("workspace operation timeout")

        self._check = check
        self._journal = journal
        self.operation_artifacts = []
        if journal.directory.is_relative_to(self.directory) or self.directory.is_relative_to(
            journal.directory
        ):
            raise ValueError("workspace and journal locations must not overlap")
        artifacts = self.operation_artifacts
        artifacts.append(journal.artifact("workspace", self._config.decode()))
        if operation.operation == "prepare":
            try:
                current = self._prepare(approved_revision)
            except BaseException:
                self._failed = True
                raise
        else:
            self._validate()
            current, payloads = capture(self.path, self.scope, self.excluded)
            if (
                operation.expected_revision is not None
                and current.revision != operation.expected_revision
            ):
                raise ValueError("workspace revision changed; destructive operation refused")
            if operation.operation in {"snapshot", "reset", "cleanup"}:
                self._save(current, payloads)
                artifacts.append(journal.artifact("snapshot", current.model_dump_json()))
            if operation.operation in {"diff", "reset", "cleanup"}:
                artifacts.append(journal.artifact("diff", self._diff(current, payloads)))
            if operation.operation == "reset":
                assert operation.target_revision is not None
                target, data = self._load(operation.target_revision)
                previous = self.path
                candidate = self._create_tree(target, data)
                if self.snapshot().revision != current.revision:
                    raise ValueError("workspace changed during reset; both worktrees retained")
                self.path = candidate
                self._remember_metadata()
                artifacts.append(
                    journal.artifact(
                        "recovery",
                        json.dumps(
                            {
                                "retained_worktree": str(previous),
                                "revision": current.revision,
                            }
                        ),
                    )
                )
                current = target
            elif operation.operation == "cleanup":
                self._cleanup(current)
        assert self.baseline is not None
        output = json.dumps(
            {
                "operation": operation.operation,
                "path": str(self.path),
                "baseline_revision": self.baseline.revision,
                "revision": current.revision,
                "status": self._status(current).model_dump(mode="json"),
                "closed": self._closed,
            }
        )
        return output, tuple(artifacts)
