"""Dependency-free path rules shared by policy and the isolated Git reader."""

from fnmatch import fnmatchcase
from functools import lru_cache
from typing import Protocol
from unicodedata import normalize


class PathScope(Protocol):
    @property
    def forbidden(self) -> tuple[str, ...]: ...

    @property
    def allowed(self) -> tuple[str, ...]: ...


CONTROL_NAMES = frozenset({".agent", ".git", ".agents", ".codex"})


def relative_parts(path: str, *, root_allowed: bool = False) -> tuple[str, ...]:
    if path == "." and root_allowed:
        return ()
    parts = tuple(path.split("/"))
    if (
        len(path) > 4096
        or len(parts) > 128
        or "\\" in path
        or ":" in path
        or any(ord(char) < 32 for char in path)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("use a normalized repository-relative path")
    if any(part.casefold() in CONTROL_NAMES for part in parts):
        raise ValueError("controller and Git metadata are not accessible through file tools")
    return parts


def glob_matches(path: str, pattern: str) -> bool:
    parts = tuple(normalize("NFC", path).split("/"))
    patterns = tuple(normalize("NFC", pattern).split("/"))

    @lru_cache
    def match(i: int, j: int) -> bool:
        if j == len(patterns):
            return i == len(parts)
        if patterns[j] == "**":
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatchcase(parts[i], patterns[j]) and match(i + 1, j + 1)

    return match(0, 0)


def path_permitted(path: str, scope: PathScope, *, write: bool = False) -> bool:
    try:
        parts = relative_parts(path, root_allowed=not write)
    except ValueError:
        return False
    prefixes = ("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    if any(
        glob_matches(prefix.casefold(), pattern.casefold())
        for prefix in prefixes
        for pattern in scope.forbidden
    ):
        return False
    return not write or any(glob_matches(path, pattern) for pattern in scope.allowed)
