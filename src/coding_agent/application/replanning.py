"""A bounded, recorded Planner call during a live execution; never resumes old writers."""

import asyncio
import json

from ..agents.planner import propose_plan
from ..context.coder import build_context
from ..core.knowledge import KnowledgeSnapshot
from ..core.planning import PlanVersion
from ..core.provider import ModelProvider
from ..core.replanning import ReplanRecord, SessionUsage, validate_replan
from ..core.tools import Git, ToolRequest
from ..core.workflow import WorkflowResult
from ..providers.runtime import ModelRuntime
from ..session.records import inspect_journal
from ..tools.execution import ExecutionRuntime
from ..tools.runtime import ToolRuntime


async def replan(
    owner: ExecutionRuntime,
    previous: PlanVersion,
    knowledge: KnowledgeSnapshot,
    runtime: ToolRuntime,
    provider: ModelProvider,
    result: WorkflowResult,
) -> PlanVersion:
    view = inspect_journal(owner.journal.directory / "events.jsonl")
    if view.unresolved or view.incomplete_tail:
        raise ValueError("replanning requires complete operation outcomes")
    usage = SessionUsage.from_events(view.events)
    if (
        usage.replans >= previous.settings.max_replans
        or usage.attempts >= previous.settings.max_total_attempts
        or usage.tool_calls >= previous.settings.max_tool_calls
        or usage.model_calls >= previous.settings.max_model_calls
    ):
        raise ValueError("session budget exhausted; retain work and reconcile before further work")
    if result.reason == "review repair budget exhausted" or (
        usage.review_fixes >= previous.settings.max_review_fixes
        and any(d.category == "review" for d in result.diagnoses)
    ):
        raise ValueError("session review repair budget exhausted")
    if result.replan is None and (
        not result.diagnoses or any(not d.repair_allowed for d in result.diagnoses)
    ):
        raise ValueError("uncertain failure requires reconciliation, not automatic replanning")
    revision = runtime.read_revision()
    if revision != result.revision:
        raise ValueError("workspace changed after workflow stopped")
    owner.record("replan_requested", result, revision)
    context = await build_context(runtime, previous, knowledge)
    diff = await runtime.execute(
        ToolRequest(
            request_id=f"replan-diff-{owner.journal.next_sequence}",
            invocation=Git(operation="diff"),
        )
    )
    if diff.status != "succeeded" or diff.truncated:
        raise ValueError("replanning requires a complete recorded diff")
    model = ModelRuntime(provider, journal=owner.journal, read_revision=runtime.read_revision)
    if owner.journal.model_calls >= previous.settings.max_model_calls:
        raise ValueError("session model budget exhausted before replanning")
    draft = await asyncio.wait_for(
        propose_plan(
            previous.draft.requirement,
            knowledge,
            previous.settings,
            model,
            previous=previous,
            feedback=json.dumps(
                {
                    "workflow": result.model_dump(mode="json"),
                    "context": context.model_dump(mode="json"),
                    "diff": diff.output,
                    "usage": usage.model_dump(mode="json"),
                    "instruction": "Refine tasks within prior authorization; do not reset budgets",
                }
            ),
        ),
        timeout=previous.settings.worker_timeout_seconds,
    )
    if runtime.read_revision() != revision:
        raise ValueError("workspace changed during replanning; reconciliation required")
    assert runtime.workspace is not None
    try:
        blockers = validate_replan(draft, previous, knowledge)
    except ValueError as error:
        blockers = (str(error),)
    proposed = PlanVersion(
        plan_id=previous.plan_id,
        version=previous.version + 1,
        previous_revision=previous.revision,
        change_reason=result.reason,
        original_request=previous.original_request,
        context_revision=previous.context_revision,
        workspace=runtime.workspace.snapshot(),
        settings=previous.settings,
        draft=draft,
        blockers=blockers,
    )
    record = ReplanRecord(
        previous_revision=previous.revision,
        previous_assessment=previous.draft.assessment,
        proposed=proposed,
        retired_task_ids=tuple(
            t.task.id
            for t in previous.draft.tasks
            if t.task.id not in {n.task.id for n in draft.tasks}
        ),
        usage=SessionUsage.from_events(
            inspect_journal(owner.journal.directory / "events.jsonl").events
        ),
    )
    owner.record("replan_proposed", record, revision)
    if blockers:
        raise ValueError("replan blocked: " + "; ".join(blockers))
    return proposed
