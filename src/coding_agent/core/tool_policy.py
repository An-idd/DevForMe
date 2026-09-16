"""Pure capability policy. Paths are resolved again by the concrete filesystem tool."""

from .models import TaskSpec
from .paths import CONTROL_NAMES as CONTROL_NAMES
from .paths import glob_matches as glob_matches
from .paths import path_permitted as path_permitted
from .paths import relative_parts as relative_parts
from .tools import Decision, Git, Patch, PolicyDecision, Shell, ToolRequest


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
