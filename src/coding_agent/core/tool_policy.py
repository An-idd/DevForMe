"""Pure capability policy. Paths are resolved again by the concrete filesystem tool."""

from fnmatch import fnmatchcase
from functools import lru_cache
from unicodedata import normalize

from .models import ScopePolicy, TaskSpec
from .tools import Decision, Git, Patch, PolicyDecision, Shell, ToolRequest

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


def path_permitted(path: str, scope: ScopePolicy, *, write: bool = False) -> bool:
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


class PolicyEngine:
    def evaluate(
        self,
        task: TaskSpec,
        request: ToolRequest,
        *,
        plan_authorized: bool,
        process_denial: str | None,
        operation_approved: bool = False,
    ) -> PolicyDecision:
        invocation = request.invocation
        if not plan_authorized:
            return PolicyDecision(
                decision=Decision.DENY, reason="matching plan authorization required"
            )
        if isinstance(invocation, (Shell, Git)):
            if isinstance(invocation, Shell):
                if task.permissions.shell != "restricted":
                    return PolicyDecision(decision=Decision.DENY, reason="shell permission denied")
                if not path_permitted(invocation.cwd, task.scope):
                    return PolicyDecision(decision=Decision.DENY, reason="working directory denied")
            if process_denial:
                return PolicyDecision(decision=Decision.DENY, reason=process_denial)
            if isinstance(invocation, Shell):
                declared = invocation.cwd == "." and any(
                    check.command == invocation.argv for check in task.acceptance.checks
                )
                if not declared and not operation_approved:
                    return PolicyDecision(
                        decision=Decision.ASK, reason="approve this concrete command"
                    )
        elif not path_permitted(invocation.path, task.scope, write=isinstance(invocation, Patch)):
            return PolicyDecision(decision=Decision.DENY, reason="path is outside permitted scope")
        return PolicyDecision(decision=Decision.ALLOW, reason="operation fits authorized policy")
