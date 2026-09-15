"""POSIX descriptor-relative text operations, rejecting links and special files."""

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from difflib import unified_diff
from pathlib import Path
from uuid import uuid4

from ..core.models import ScopePolicy
from ..core.tool_policy import path_permitted, relative_parts


class FileBoundaryError(ValueError):
    pass


class SafeFiles:
    def __init__(self, root: Path, scope: ScopePolicy, *, max_bytes: int = 1_048_576) -> None:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise FileBoundaryError("descriptor-relative no-follow backend is unavailable")
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise FileBoundaryError("repository root must be a directory")
        self.scope = scope
        self.max_bytes = max_bytes
        self._identity = (self.root.stat().st_dev, self.root.stat().st_ino)

    @contextmanager
    def directory(self, path: str = ".") -> Iterator[int]:
        if not path_permitted(path, self.scope):
            raise FileBoundaryError("directory denied by policy")
        parts = relative_parts(path, root_allowed=True)
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != self._identity:
                raise FileBoundaryError("repository root changed")
            for part in parts:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def _parent(self, path: str, *, write: bool = False) -> Iterator[tuple[int, str]]:
        if not path_permitted(path, self.scope, write=write):
            raise FileBoundaryError("path denied by policy")
        parts = relative_parts(path)
        with self.directory("/".join(parts[:-1]) or ".") as fd:
            yield fd, parts[-1]

    def _read_at(self, parent: int, name: str) -> bytes:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise FileBoundaryError(
                    "only regular files without hard-link aliases are supported"
                )
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(self.max_bytes + 1)
            if len(data) > self.max_bytes:
                raise FileBoundaryError("file exceeds configured size limit")
            return data
        finally:
            os.close(fd)

    def read(self, path: str) -> str:
        with self._parent(path) as (fd, name):
            return self._read_at(fd, name).decode("utf-8")

    def search(self, path: str, query: str, *, max_entries: int = 10_000) -> tuple[str, bool]:
        matches: list[str] = []
        output_size = 0
        visited = 0
        truncated = False

        def walk(directory: str) -> None:
            nonlocal visited, output_size, truncated
            with self.directory(directory) as fd:
                # Bound the inventory before sorting it; do not materialize unbounded directories.
                with os.scandir(fd) as entries:
                    names = []
                    for entry in entries:
                        visited += 1
                        if visited > max_entries:
                            truncated = True
                            return
                        names.append(entry.name)
                for name in sorted(names):
                    child = name if directory == "." else f"{directory}/{name}"
                    if not path_permitted(child, self.scope):
                        continue
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        walk(child)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        try:
                            text = self._read_at(fd, name).decode("utf-8")
                        except (UnicodeDecodeError, FileBoundaryError):
                            truncated = True
                            continue
                        for line_no, line in enumerate(text.splitlines(), 1):
                            if query in line:
                                match = f"{child}:{line_no}:{line}\n"
                                output_size += len(match.encode())
                                if output_size > self.max_bytes:
                                    truncated = True
                                    return
                                matches.append(match)
                    else:
                        truncated = True
                    if truncated and (visited > max_entries or output_size > self.max_bytes):
                        return

        walk(path)
        return "".join(matches), truncated

    def patch(
        self, path: str, expected_sha256: str | None, content: str
    ) -> tuple[str | None, str, str]:
        data = content.encode("utf-8")
        if len(data) > self.max_bytes:
            raise FileBoundaryError("patch exceeds configured size limit")
        with self._parent(path, write=True) as (parent, name):
            try:
                old = self._read_at(parent, name)
                mode = stat.S_IMODE(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode)
            except FileNotFoundError:
                old = None
                mode = 0o644
            before = hashlib.sha256(old).hexdigest() if old is not None else None
            if before != expected_sha256:
                raise FileBoundaryError("content changed or creation target already exists")
            old_text = old.decode("utf-8") if old is not None else ""
            temporary = f".agent-patch-{uuid4().hex}"
            temp_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode & 0o777,
                dir_fd=parent,
            )
            try:
                with os.fdopen(temp_fd, "wb") as stream:
                    os.fchmod(stream.fileno(), mode & 0o777)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Recheck the target before replacing; never follow a swapped link.
                try:
                    current = self._read_at(parent, name)
                except FileNotFoundError:
                    current = None
                if current != old:
                    raise FileBoundaryError("target changed while preparing patch")
                if old is None:
                    # link+unlink installs create-only without clobbering a racing new file.
                    os.link(
                        temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False
                    )
                    os.unlink(temporary, dir_fd=parent)
                else:
                    os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
            after = hashlib.sha256(data).hexdigest()
            diff = "".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in unified_diff(
                    old_text.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                )
            )
            return before, after, diff
