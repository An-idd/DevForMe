from collections.abc import Callable

import pytest
from pydantic import ValidationError

from coding_agent.core import TaskSpec
from coding_agent.core.tool_policy import PolicyEngine, glob_matches, path_permitted
from coding_agent.core.tools import Decision, Git, Patch, Shell, ToolRequest, ToolResult


@pytest.mark.parametrize(
    "path,pattern,expected",
    [
        ("src/main.py", "src/*.py", True),
        ("src/pkg/main.py", "src/*.py", False),
        ("src/main.py", "src/**/*.py", True),
        ("src/pkg/main.py", "src/**/*.py", True),
        ("src", "src/**", True),
        ("src", "src/*", False),
        ("other/main.py", "src/**", False),
        ("tests/test_a.py", "tests/test_?.py", True),
        ("cafe\u0301/private", "caf\u00e9/**", True),
    ],
)
def test_globs_respect_directory_boundaries(path: str, pattern: str, expected: bool) -> None:
    assert glob_matches(path, pattern) is expected


def test_forbidden_parent_and_metadata_override_write_grants(
    make_task: Callable[..., TaskSpec],
) -> None:
    task = make_task("t")
    data = task.model_dump()
    data["scope"] = {"allowed": ["**"], "forbidden": ["private", "src/generated/**"]}
    scope = TaskSpec.model_validate(data).scope
    for path in (
        "private/file",
        "PRIVATE/file",
        "src/generated/foo",
        "src/.git/config",
        "src/.CODEX/log",
    ):
        assert not path_permitted(path, scope, write=True)
        assert not path_permitted(path, scope)
    assert path_permitted("src/main.py", scope, write=True)


def test_declared_command_is_exact_and_approval_never_overrides_denial(
    make_task: Callable[..., TaskSpec],
) -> None:
    task = make_task("t")
    policy = PolicyEngine()

    def decision(invocation, **changes):
        return policy.evaluate(
            task,
            ToolRequest(request_id="r", invocation=invocation),
            **({"plan_authorized": True, "process_denial": None} | changes),
        ).decision

    declared = task.acceptance.checks[0].command
    assert decision(Shell(argv=declared)) is Decision.ALLOW
    assert decision(Shell(argv=(*declared, "--extra"))) is Decision.ASK
    assert decision(Shell(argv=declared, cwd="src")) is Decision.ASK
    assert (
        decision(Shell(argv=declared), process_denial="unavailable", operation_approved=True)
        is Decision.DENY
    )
    assert (
        decision(Git(operation="status"), plan_authorized=False, operation_approved=True)
        is Decision.DENY
    )
    assert (
        decision(
            Patch(path="secrets/key", expected_sha256=None, content=""), operation_approved=True
        )
        is Decision.DENY
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"decision": "deny", "status": "succeeded"},
        {"decision": "ask", "status": "failed"},
        {"decision": "allow", "status": "denied"},
        {"executed": False},
        {"exit_code": 1},
        {"finished_at": "2026-09-14T00:00:00Z"},
    ],
)
def test_tool_results_cannot_claim_inconsistent_success(changes: dict) -> None:
    with pytest.raises(ValidationError):
        ToolResult.model_validate(
            {
                "request_id": "r",
                "request_event_id": "s:1",
                "decision": "allow",
                "status": "succeeded",
                "executed": True,
                "reason": "completed",
                "started_at": "2026-09-15T00:00:00Z",
                "finished_at": "2026-09-15T00:00:01Z",
            }
            | changes
        )
