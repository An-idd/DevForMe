import asyncio
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from coding_agent.core import TaskGraph, TaskSpec
from coding_agent.core.provider import (
    GenerationSettings,
    Message,
    ModelCallResult,
    ModelFailure,
    ModelResponse,
    ProviderError,
    TokenUsage,
    ToolCall,
    runtime_tools,
    tool_output,
    tool_request,
)
from coding_agent.core.workflow import (
    EventWriteError,
    PlanApproval,
    Revision,
    RunSpec,
    WorkflowEvent,
)
from coding_agent.providers.runtime import ModelRuntime
from coding_agent.session import records
from coding_agent.session.records import JsonlJournal, Sanitizer, inspect_journal
from coding_agent.testing import FakeModelProvider
from coding_agent.tools import ToolRuntime

REVISION = Revision(plan_version=1, context_revision="rules-1", workspace_revision="snapshot-1")
MESSAGES = (Message(role="user", content="private prompt must not be persisted"),)
USAGE = TokenUsage(input_tokens=10, output_tokens=4, total_tokens=14)


def response(content="model draft", **message):
    return ModelResponse(
        response_id="resp-1",
        model="actual-fake-model",
        usage=USAGE,
        message=Message(role="assistant", content=content, **message),
    )


def runtime(journal, fake):
    return ModelRuntime(fake, journal=journal, read_revision=lambda: REVISION, task_id="t")


def test_request_precedes_provider_and_draft_is_sanitized_not_state(tmp_path):
    class CheckingFake(FakeModelProvider):
        async def generate(self, *args, **kwargs):
            inspection = inspect_journal(tmp_path / "events.jsonl")
            assert [e.kind for e in inspection.events] == ["model_requested"]
            assert inspection.unresolved == ("session:1",)
            return await super().generate(*args, **kwargs)

    fake = CheckingFake(
        [response("draft containing confidential", continuation="private reasoning")]
    )
    with JsonlJournal(tmp_path, "session", sanitizer=Sanitizer(("confidential",))) as journal:
        result = asyncio.run(runtime(journal, fake).generate(MESSAGES))
        assert result.message.content == "draft containing confidential"
    inspection = inspect_journal(tmp_path / "events.jsonl")
    assert not inspection.unresolved and len(inspection.events) == 2
    requested, finished = inspection.events
    assert requested.revision == REVISION and requested.model_request.settings.model == "fake"
    assert len(requested.model_request.input_sha256) == 64
    assert finished.model_result.status == "completed" and finished.model_result.usage == USAGE
    assert all(e.state is None and not e.evidence_ids for e in inspection.events)
    artifact = (tmp_path / finished.artifacts[0].path).read_text()
    assert "[REDACTED]" in artifact and "confidential" not in artifact
    assert "private reasoning" not in artifact
    assert MESSAGES[0].content not in (tmp_path / "events.jsonl").read_text()


@pytest.mark.parametrize("fail_write", [1, 2])
def test_journal_failure_stops_dispatch_and_preserves_unresolved(tmp_path, monkeypatch, fail_write):
    calls = 0
    actual_write = records.os.write

    def fail(fd, data):
        nonlocal calls
        calls += 1
        if calls == fail_write:
            raise OSError("disk unavailable")
        return actual_write(fd, data)

    fake = FakeModelProvider([response()])
    with JsonlJournal(tmp_path, "session") as journal:
        monkeypatch.setattr(records.os, "write", fail)
        model = runtime(journal, fake)
        with pytest.raises(EventWriteError):
            asyncio.run(model.generate(MESSAGES))
        assert len(fake.calls) == fail_write - 1
        with pytest.raises(EventWriteError):
            asyncio.run(model.generate(MESSAGES))
        with pytest.raises(EventWriteError):
            journal.begin_operation("next-tool")
    inspection = inspect_journal(tmp_path / "events.jsonl")
    assert inspection.unresolved == (() if fail_write == 1 else ("session:1",))


@pytest.mark.parametrize(
    "draft",
    [
        response("", tool_calls=(ToolCall(call_id="c", name="workspace", arguments_json="{}"),)),
        response("", tool_calls=(ToolCall(call_id="c", name="patch", arguments_json="{}"),)),
        ProviderError(ModelFailure(code="rate_limit", retryable=True, status_code=429)),
        RuntimeError("sensitive implementation details"),
        None,
        {"state": "VERIFIED"},
    ],
)
def test_invalid_fake_and_failures_are_recorded_without_execution(tmp_path, draft):
    fake = FakeModelProvider([draft])
    with JsonlJournal(tmp_path, "session") as journal:
        with pytest.raises(ProviderError):
            asyncio.run(runtime(journal, fake).generate(MESSAGES, tools=runtime_tools()))
    inspection = inspect_journal(tmp_path / "events.jsonl")
    assert not inspection.unresolved and len(fake.calls) == 1
    result = inspection.events[-1].model_result
    assert result.status == "failed"
    assert "sensitive implementation details" not in (tmp_path / "events.jsonl").read_text()
    assert all(e.state is None for e in inspection.events)


def test_cancelled_model_is_recorded_and_remote_usage_remains_unknown(tmp_path):
    with JsonlJournal(tmp_path, "session") as journal:
        fake = FakeModelProvider([asyncio.CancelledError()])
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(runtime(journal, fake).generate(MESSAGES))
    result = inspect_journal(tmp_path / "events.jsonl").events[-1].model_result
    assert result.status == "interrupted" and result.usage is None


def test_timeout_applies_to_any_provider_without_retry(tmp_path):
    class HangingFake(FakeModelProvider):
        async def generate(self, *args, **kwargs):
            await asyncio.Event().wait()

    with JsonlJournal(tmp_path, "session") as journal:
        fake = HangingFake([], settings=GenerationSettings(model="fake", timeout_seconds=0.01))
        with pytest.raises(ProviderError, match="timeout"):
            asyncio.run(runtime(journal, fake).generate(MESSAGES))
    result = inspect_journal(tmp_path / "events.jsonl").events[-1].model_result
    assert result.status == "failed" and result.failure.code == "timeout"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/created.py", "succeeded"),
        ("secrets/private", "denied"),
        (".agent/state.json", "denied"),
    ],
)
def test_model_tool_requests_use_actual_permissions_and_shared_sequence(
    tmp_path, make_task, path, expected
):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "secrets").mkdir()
    (root / ".agent").mkdir()
    secret = root / "secrets/private"
    secret.write_text("user data")
    state = root / ".agent/state.json"
    state.write_text("controller state")
    task = TaskSpec.model_validate(
        make_task("t").model_dump()
        | {"permissions": {"network": False, "shell": "restricted", "database": "deny"}}
    )
    spec = RunSpec(session_id="session", graph=TaskGraph(tasks=(task,)), revision=REVISION)
    approval = PlanApproval(
        session_id="session",
        plan_fingerprint=spec.fingerprint,
        source="test",
        timestamp=datetime.now(UTC),
    )
    call = ToolCall(
        call_id="vendor-call-1",
        name="patch",
        arguments_json=json.dumps(
            {"kind": "patch", "path": path, "content": "proposed change", "expected_sha256": None}
        ),
    )
    fake = FakeModelProvider([response("", tool_calls=(call,)), response("proposed completion")])
    with JsonlJournal(tmp_path / "records", "session") as journal:
        tools = ToolRuntime(
            spec, "t", root=root, journal=journal, approval=approval, read_revision=lambda: REVISION
        )
        model = runtime(journal, fake)

        async def run():
            first = await model.generate(MESSAGES, tools=runtime_tools())
            assert not (root / "src/created.py").exists()
            request = tool_request(first.message.tool_calls[0], "local-tool-1")
            result = await tools.execute(request)
            assert result.status == expected
            output = tool_output(call, request, result)
            assert output.tool_call_id == "vendor-call-1"
            wrong = tool_request(call, "other-local-id")
            with pytest.raises(ValueError):
                tool_output(call, wrong, result)
            await model.generate((*MESSAGES, first.message, output), tools=runtime_tools())

        asyncio.run(run())
    inspection = inspect_journal(tmp_path / "records/events.jsonl")
    assert [e.kind for e in inspection.events] == [
        "plan_registered",
        "model_requested",
        "model_finished",
        "tool_requested",
        "tool_finished",
        "model_requested",
        "model_finished",
    ]
    assert not inspection.unresolved and all(e.state is None for e in inspection.events)
    assert secret.read_text() == "user data" and state.read_text() == "controller state"
    if expected == "succeeded":
        assert (root / path).read_text() == "proposed change"


def test_model_result_cannot_claim_state_or_finish_an_unrelated_request(tmp_path):
    fake = FakeModelProvider([response()])
    with JsonlJournal(tmp_path, "session") as journal:
        asyncio.run(runtime(journal, fake).generate(MESSAGES))
    events = inspect_journal(tmp_path / "events.jsonl").events
    with pytest.raises(ValidationError):
        WorkflowEvent.model_validate(events[1].model_dump() | {"state": "VERIFIED"})
    forged = events[1].model_dump()
    forged["model_result"]["request_id"] = "different"
    # Offline inspection also rejects corrupted correlation.
    (tmp_path / "events.jsonl").write_text(
        events[0].model_dump_json()
        + "\n"
        + WorkflowEvent.model_validate(forged).model_dump_json()
        + "\n"
    )
    with pytest.raises(EventWriteError):
        inspect_journal(tmp_path / "events.jsonl")


def test_fake_requires_explicit_script_and_result_cannot_fabricate_success():
    with pytest.raises(RuntimeError, match="exhausted"):
        asyncio.run(FakeModelProvider([]).generate(MESSAGES))
    with pytest.raises(ValidationError):
        ModelCallResult(request_id="r", request_event_id="s:1", status="completed")


def test_failure_metadata_cannot_contradict_recorded_usage():
    with pytest.raises(ValidationError, match="failure metadata"):
        ModelCallResult(
            request_id="r",
            request_event_id="s:1",
            status="failed",
            usage=USAGE,
            failure=ModelFailure(code="timeout"),
        )
