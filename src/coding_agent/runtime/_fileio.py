"""Small platform boundary shared by file tools and the durable journal."""

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

if sys.platform == "win32":
    from . import _winfiles

    NOFOLLOW = 0
    NONBLOCK = 0
else:
    NOFOLLOW = os.O_NOFOLLOW
    NONBLOCK = os.O_NONBLOCK


def open_directory(path: Path) -> int:
    if sys.platform == "win32":
        return _winfiles.open_file(path, os.O_RDONLY, directory=True)
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def directory_at(parent: int, name: str) -> int:
    if sys.platform == "win32":
        return open_directory(_winfiles.fd_path(parent) / name)
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def open_at(
    parent: int, name: str, flags: int, mode: int = 0o600, *, allow_hardlinks: bool = False
) -> int:
    if sys.platform == "win32":
        return _winfiles.open_file(
            _winfiles.fd_path(parent) / name, flags, allow_hardlinks=allow_hardlinks
        )
    return os.open(name, flags, mode, dir_fd=parent)


def open_read(path: Path) -> int:
    if sys.platform == "win32":
        return _winfiles.open_file(path, os.O_RDONLY)
    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW)


def stat_at(parent: int, name: str) -> os.stat_result:
    if sys.platform == "win32":
        return (_winfiles.fd_path(parent) / name).lstat()
    return os.stat(name, dir_fd=parent, follow_symlinks=False)


@contextmanager
def entries(parent: int) -> Iterator[Iterator[os.DirEntry[str]]]:
    if sys.platform == "win32":
        with os.scandir(_winfiles.fd_path(parent)) as listing:
            yield listing
    else:
        with os.scandir(parent) as listing:
            yield listing


def sync_directory(parent: int) -> None:
    if sys.platform != "win32":
        os.fsync(parent)
    # Windows flushes files through os.fsync/WRITE_THROUGH and namespace installs
    # through MoveFileExW(WRITE_THROUGH); it has no POSIX directory fsync.


def preserve_mode(fd: int, mode: int) -> None:
    if sys.platform != "win32":
        os.fchmod(fd, mode)
    # Windows preserves the target DACL during install; POSIX mode bits do not apply.


def install(parent: int, source: str, target: str, *, replace: bool) -> None:
    if sys.platform == "win32":
        _winfiles.install(parent, source, target, replace=replace)
    elif replace:
        os.replace(source, target, src_dir_fd=parent, dst_dir_fd=parent)
    else:
        os.link(source, target, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.unlink(source, dir_fd=parent)


def unlink(parent: int, name: str) -> None:
    if sys.platform == "win32":
        os.unlink(_winfiles.fd_path(parent) / name)
    else:
        os.unlink(name, dir_fd=parent)
