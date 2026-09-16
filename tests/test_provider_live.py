"""Opt-in: at most 3 API calls, 1024 output tokens/call, 30 seconds/call, no retries."""

import asyncio
import os
from datetime import UTC, datetime

import pytest

from coding_agent.core import TaskGraph, TaskSpec
from coding_agent.core.models import DomainModel
from coding_agent.core.provider import (
    GenerationSettings,
    Message,
    runtime_tools,
    tool_output,
    tool_request,
)
from coding_agent.core.workflow import PlanApproval, Revision, RunSpec
from coding_agent.providers.openai import OpenAIProvider
from coding_agent.providers.runtime import ModelRuntime
from coding_agent.session.records import JsonlJournal, inspect_journal
from coding_agent.tools import ToolRuntime


class SmokeAnswer(DomainModel):
    answer: str


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("CODING_AGENT_LIVE_SMOKE") != "1",
    reason="real API smoke requires explicit CODING_AGENT_LIVE_SMOKE=1",
)
def test_openai_live_structured_and_tool_round_trip(tmp_path, make_task):
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("OPENAI_MODEL"):
        pytest.fail("explicit live smoke requires OPENAI_API_KEY and OPENAI_MODEL")
    settings = GenerationSettings(
        model=os.environ["OPENAI_MODEL"], max_output_tokens=1024, timeout_seconds=30
    )
    root = tmp_path / "synthetic-repo"
    (root / "src").mkdir(parents=True)
    (root / "src/smoke.txt").write_text("live fixture")
    revision = Revision(
        plan_version=1,
        context_revision="synthetic-smoke-context",
        workspace_revision="synthetic-smoke-fixture",
    )
    task = TaskSpec.model_validate(
        make_task("smoke").model_dump()
        | {"permissions": {"network": False, "shell": "deny", "database": "deny"}}
    )
    spec = RunSpec(session_id="live-smoke", graph=TaskGraph(tasks=(task,)), revision=revision)
    approval = PlanApproval(
        session_id=spec.session_id,
        plan_fingerprint=spec.fingerprint,
        source="explicit-live-smoke",
        timestamp=datetime.now(UTC),
    )
    with JsonlJournal(tmp_path / "records", spec.session_id) as journal:
        tools = ToolRuntime(
            spec,
            task.id,
            root=root,
            journal=journal,
            approval=approval,
            read_revision=lambda: revision,
        )

        async def run():
            async with OpenAIProvider(settings) as provider:
                model = ModelRuntime(
                    provider, journal=journal, read_revision=lambda: revision, task_id=task.id
                )
                structured = await model.generate(
                    (
                        Message(
                            role="user",
                            content="Return the answer field with exactly the string ok.",
                        ),
                    ),
                    response_schema=SmokeAnswer,
                )
                assert structured.structured == SmokeAnswer(answer="ok")
                messages = (
                    Message(
                        role="user",
                        content="Use the read function exactly once to "
                        "read src/smoke.txt. Do not guess its content or answer before reading it.",
                    ),
                )
                definitions = tuple(t for t in runtime_tools() if t.name == "read")
                first = await model.generate(messages, tools=definitions)
                assert len(first.message.tool_calls) == 1
                call = first.message.tool_calls[0]
                request = tool_request(call, "smoke-read")
                result = await tools.execute(request)
                assert result.status == "succeeded" and result.output == "live fixture"
                last = await model.generate(
                    (
                        *messages,
                        first.message,
                        tool_output(call, request, result),
                        Message(
                            role="user",
                            content="Return the exact file content in the answer field.",
                        ),
                    ),
                    response_schema=SmokeAnswer,
                )
                assert last.structured == SmokeAnswer(answer="live fixture")

        asyncio.run(run())
    inspection = inspect_journal(tmp_path / "records/events.jsonl")
    calls = [e.model_result for e in inspection.events if e.model_result is not None]
    assert len(calls) == 3 and not inspection.unresolved
    assert all(c.status == "completed" for c in calls)
    print(
        {
            "settings": settings.model_dump(),
            "calls": [c.model_dump() for c in calls],
            "records": str(tmp_path / "records"),
        }
    )
