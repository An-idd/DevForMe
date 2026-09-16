"""Bounded, no-follow source capture and lossless controller-owned snapshots."""

import hashlib
import os
import stat
from pathlib import Path
from unicodedata import normalize
from uuid import uuid4

from ..core.models import ScopePolicy
from ..core.paths import CONTROL_NAMES, glob_matches, path_permitted, relative_parts
from ..core.workspace import SnapshotFile, WorkspaceSnapshot
from . import _fileio as io
from .filesystem import SafeFiles

MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 20_000
MAX_FILE = 1_048_576
MAX_MANIFEST = 8 * 1024 * 1024
DEFAULT_EXCLUDED = (
    ".venv/**",
    "**/__pycache__/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
)


def excluded_path(path: str, patterns: tuple[str, ...]) -> bool:
    return any(
        glob_matches(path.casefold(), pattern.casefold())
        or (pattern.endswith("/**") and glob_matches(path.casefold(), pattern[:-3].casefold()))
        for pattern in patterns
    )


def capture(
    root: Path,
    scope: ScopePolicy,
    excluded: tuple[str, ...],
) -> tuple[WorkspaceSnapshot, dict[str, bytes]]:
    files = SafeFiles(root, scope, max_bytes=MAX_FILE)
    payloads: dict[str, bytes] = {}
    items: list[SnapshotFile] = []
    seen: set[str] = set()
    count = total = 0

    def walk(directory: str) -> None:
        nonlocal count, total
        with files.directory(directory) as fd:
            with io.entries(fd) as listing:
                names = []
                for entry in listing:
                    count += 1
                    if count > MAX_ENTRIES:
                        raise ValueError("snapshot entry limit exceeded")
                    names.append(entry.name)
            for name in sorted(names):
                path = name if directory == "." else f"{directory}/{name}"
                if name.casefold() in CONTROL_NAMES:
                    continue
                relative_parts(path)
                if not path_permitted(path, scope) or excluded_path(path, excluded):
                    continue
                key = normalize("NFC", path).casefold()
                if key in seen:
                    raise ValueError("snapshot contains ambiguous case/Unicode paths")
                seen.add(key)
                info = io.stat_at(fd, name)
                if getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("snapshot reparse point is unsupported")
                if stat.S_ISDIR(info.st_mode):
                    walk(path)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    data = files._read_at(fd, name)
                    after = io.stat_at(fd, name)
                    if (
                        info.st_ino,
                        info.st_dev,
                        info.st_size,
                        info.st_mtime_ns,
                        info.st_ctime_ns,
                        info.st_mode,
                    ) != (
                        after.st_ino,
                        after.st_dev,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                        after.st_mode,
                    ):
                        raise ValueError("source changed during snapshot capture")
                    total += len(data)
                    if total > MAX_BYTES:
                        raise ValueError("snapshot byte limit exceeded")
                    digest = hashlib.sha256(data).hexdigest()
                    payloads[digest] = data
                    items.append(
                        SnapshotFile(
                            path=path,
                            sha256=digest,
                            size_bytes=len(data),
                            mode=0o100755 if os.name != "nt" and info.st_mode & 0o111 else 0o100644,
                        )
                    )
                else:
                    raise ValueError("snapshot requires regular files without link aliases")

    walk(".")
    snapshot = WorkspaceSnapshot(
        files=tuple(sorted(items, key=lambda item: item.path)),
        excluded=excluded,
        forbidden=scope.forbidden,
    )
    if len(snapshot.model_dump_json().encode()) > MAX_MANIFEST:
        raise ValueError("snapshot manifest limit exceeded")
    return snapshot, payloads


def read_bytes(path: Path, *, limit: int = MAX_FILE) -> bytes:
    parent = io.open_directory(path.parent)
    try:
        fd = io.open_at(parent, path.name, os.O_RDONLY | io.NOFOLLOW | io.NONBLOCK)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("controller input must be a regular unaliased file")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("controller input exceeds size limit")
        return data


def save_bytes(directory: Path, name: str, data: bytes) -> None:
    """Create-only durable install; existing content must match exactly."""
    parent = io.open_directory(directory)
    temporary = f"pending-{uuid4().hex}"
    try:
        try:
            if read_bytes(directory / name, limit=max(MAX_FILE, len(data))) != data:
                raise ValueError("immutable controller content changed")
            return
        except FileNotFoundError:
            pass
        fd = io.open_at(parent, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            io.install(parent, temporary, name, replace=False)
            io.sync_directory(parent)
        finally:
            try:
                io.unlink(parent, temporary)
            except FileNotFoundError:
                pass
    finally:
        os.close(parent)


def install_file(root: Path, item: SnapshotFile, data: bytes) -> None:
    """Populate a new worktree; existing paths must never be overwritten."""
    parts = relative_parts(item.path)
    folder = root
    for part in parts[:-1]:
        folder = folder / part
        folder.mkdir(exist_ok=True)
        fd = io.open_directory(folder)
        os.close(fd)
    parent = io.open_directory(folder)
    temporary = f"restore-{uuid4().hex}"
    try:
        fd = io.open_at(parent, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            io.preserve_mode(stream.fileno(), item.mode & 0o777)
            stream.flush()
            os.fsync(stream.fileno())
        io.install(parent, temporary, parts[-1], replace=False)
        io.sync_directory(parent)
    finally:
        try:
            io.unlink(parent, temporary)
        except FileNotFoundError:
            pass
        os.close(parent)
