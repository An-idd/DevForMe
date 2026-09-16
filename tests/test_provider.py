import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from openai import DefaultAsyncHttpxClient
from pydantic import SecretStr, ValidationError

from coding_agent.core.models import DomainModel, RequirementContract
from coding_agent.core.provider import (
    GenerationSettings,
    Message,
    ProviderError,
    TokenUsage,
    ToolCall,
    runtime_tools,
    tool_request,
    validate_input,
)
from coding_agent.core.workflow import ReviewResult
from coding_agent.providers.openai import OpenAIProvider

SETTINGS = GenerationSettings(model="explicit-test-model", max_output_tokens=128)
MESSAGES = (Message(role="user", content="synthetic task"),)
USAGE = {
    "input_tokens": 11,
    "output_tokens": 7,
    "total_tokens": 18,
    "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 0},
    "output_tokens_details": {"reasoning_tokens": 2},
}


class Answer(DomainModel):
    answer: str


def output_text(content: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": content, "annotations": []}],
    }


def function_call(
    name="read", arguments='{"kind":"read","path":"src/main.py"}', call_id="call_1"
) -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": "fc_1",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


def wire(*items: dict[str, Any], **updates: Any) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "actual-test-model",
        "status": "completed",
        "error": None,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": list(items),
        "usage": USAGE,
    } | updates


def provider(handler: Callable, settings=SETTINGS) -> OpenAIProvider:
    return OpenAIProvider(
        settings,
        api_key=SecretStr("test-key-never-real"),
        http_client=DefaultAsyncHttpxClient(
            transport=httpx2.MockTransport(handler), follow_redirects=False
        ),
    )


def generate(payload, *, schema=None, tools=(), messages=MESSAGES):
    async def run():
        async with provider(lambda request: httpx2.Response(200, json=payload)) as model:
            return await model.generate(messages, tools=tools, response_schema=schema)

    return asyncio.run(run())


def test_sdk_wire_config_and_structured_output(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://must-not-receive.invalid")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=wire(output_text('{"answer":"hello"}')))

    async def run():
        async with provider(handler) as model:
            result = await model.generate(MESSAGES, response_schema=Answer)
            assert result.structured == Answer(answer="hello")
            assert result.model == "actual-test-model"
            assert result.usage == TokenUsage(
                input_tokens=11,
                output_tokens=7,
                total_tokens=18,
                cached_input_tokens=3,
                cache_write_tokens=0,
                reasoning_tokens=2,
            )

    asyncio.run(run())
    assert len(requests) == 1 and str(requests[0].url) == "https://api.openai.com/v1/responses"
    sent = json.loads(requests[0].content)
    assert sent["store"] is False and sent["parallel_tool_calls"] is False
    assert sent["include"] == ["reasoning.encrypted_content"]
    assert sent["max_output_tokens"] == 128 and sent["model"] == SETTINGS.model
    assert sent["truncation"] == "disabled"
    format_ = sent["text"]["format"]
    assert format_["strict"] is True
    assert format_["schema"]["additionalProperties"] is False
    assert format_["schema"]["required"] == ["answer"]


@pytest.mark.parametrize(
    "schema,data",
    [
        (
            RequirementContract,
            {
                "id": "req-1",
                "title": "Title",
                "goal": "Goal",
                "functional_requirements": ["behavior"],
                "risk": {"level": "low"},
            },
        ),
        (
            ReviewResult,
            {
                "task_id": "t",
                "revision": {
                    "plan_version": 1,
                    "context_revision": "ctx",
                    "workspace_revision": "snap",
                },
                "status": "passed",
                "summary": "draft only",
                "source": "model-draft",
                "timestamp": "2026-09-16T00:00:00Z",
            },
        ),
    ],
)
def test_existing_domain_structured_outputs(schema, data):
    result = generate(wire(output_text(json.dumps(data))), schema=schema)
    assert isinstance(result.structured, schema)


def test_tool_round_trip_replays_reasoning_and_matches_call_id():
    requests = []
    reasoning = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "opaque-encrypted-data",
    }

    def handler(request):
        requests.append(json.loads(request.content))
        items = (reasoning, function_call()) if len(requests) == 1 else (output_text("done"),)
        return httpx2.Response(200, json=wire(*items))

    async def run():
        async with provider(handler) as model:
            first = await model.generate(MESSAGES, tools=runtime_tools())
            assert first.structured is None
            request = tool_request(first.message.tool_calls[0], "local-1")
            assert request.request_id == "local-1" and request.invocation.kind == "read"
            second = await model.generate(
                (
                    *MESSAGES,
                    first.message,
                    Message(role="tool", tool_call_id="call_1", content='{"status":"denied"}'),
                ),
                tools=runtime_tools(),
            )
            assert second.message.content == "done"

    asyncio.run(run())
    sent = requests[1]["input"]
    assert sent[1] == reasoning and sent[2]["call_id"] == sent[3]["call_id"] == "call_1"
    assert sent[3]["type"] == "function_call_output"
    definitions = requests[0]["tools"]
    assert {t["name"] for t in definitions} == {"read", "search", "patch", "shell", "git"}
    assert all(
        t["strict"] and t["parameters"]["additionalProperties"] is False for t in definitions
    )


@pytest.mark.parametrize(
    "payload,schema,tools,code",
    [
        (wire(output_text("not json")), Answer, (), "invalid_response"),
        (wire(output_text('{"answer":"ok","state":"VERIFIED"}')), Answer, (), "invalid_response"),
        (wire(output_text('{"answer":3}')), Answer, (), "invalid_response"),
        (wire(function_call(arguments="{}")), None, runtime_tools(), "invalid_response"),
        (wire(function_call(arguments='{"path":12}')), None, runtime_tools(), "invalid_response"),
        (wire(function_call(name="workspace")), None, runtime_tools(), "invalid_response"),
        (wire(function_call(), function_call()), None, runtime_tools(), "invalid_response"),
        (wire(function_call()), None, (), "invalid_response"),
        (wire(output_text("partial"), status="incomplete"), None, (), "incomplete"),
        (wire(output_text("error"), status="failed"), None, (), "unavailable"),
        (wire(), None, (), "invalid_response"),
        (
            wire(
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "refusal", "refusal": "cannot comply"}],
                }
            ),
            None,
            (),
            "refused",
        ),
        (
            wire({"type": "reasoning", "id": "rs_1", "summary": []}, output_text("hi")),
            None,
            (),
            "invalid_response",
        ),
    ],
)
def test_reject_invalid_responses_and_preserve_actual_usage(payload, schema, tools, code):
    with pytest.raises(ProviderError) as failure:
        generate(payload, schema=schema, tools=tools)
    assert failure.value.failure.code == code
    assert failure.value.failure.response_id == "resp_1"
    assert failure.value.failure.usage.total_tokens == 18


def test_unavailable_usage_is_unknown_and_malformed_usage_is_rejected():
    assert generate(wire(output_text("hi"), usage=None)).usage is None
    with pytest.raises(ProviderError, match="invalid_response"):
        generate(wire(output_text("hi"), usage=USAGE | {"total_tokens": 100}))


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (400, "invalid_request", False),
        (401, "authentication", False),
        (403, "permission", False),
        (408, "timeout", True),
        (429, "rate_limit", True),
        (500, "unavailable", True),
        (503, "unavailable", True),
    ],
)
def test_http_errors_have_no_implicit_retry_or_secret_body(status, code, retryable):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx2.Response(
            status, headers={"retry-after": "2"}, json={"error": {"message": "secret-server-body"}}
        )

    async def run():
        async with provider(handler) as model:
            with pytest.raises(ProviderError) as failure:
                await model.generate(MESSAGES)
            assert failure.value.failure.code == code
            assert failure.value.failure.retryable == retryable
            assert failure.value.failure.status_code == status
            assert failure.value.failure.retry_after_seconds == 2
            assert "secret-server-body" not in str(failure.value)
            assert "test-key-never-real" not in str(failure.value)

    asyncio.run(run())
    assert len(attempts) == 1


@pytest.mark.parametrize(
    "error,code",
    [
        (httpx2.ConnectError("secret"), "transport"),
        (httpx2.ReadTimeout("secret"), "timeout"),
    ],
)
def test_transport_failures(error, code):
    def handler(request):
        raise error

    async def run():
        async with provider(handler) as model:
            with pytest.raises(ProviderError, match=code):
                await model.generate(MESSAGES)

    asyncio.run(run())


def test_deadline_cancels_inflight_transport():
    cancelled = []

    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def run():
        async with provider(
            handler, GenerationSettings(model="test", timeout_seconds=0.5)
        ) as model:
            with pytest.raises(ProviderError, match="timeout"):
                await model.generate(MESSAGES)

    asyncio.run(run())
    assert cancelled == [True]


def test_explicit_cancellation_propagates():
    async def run():
        entered = asyncio.Event()
        stopped = asyncio.Event()

        async def handler(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async with provider(handler) as model:
            call = asyncio.create_task(model.generate(MESSAGES))
            await entered.wait()
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            assert stopped.is_set()

    asyncio.run(run())


def test_missing_credentials_fail_before_network(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="configuration"):
        OpenAIProvider(SETTINGS)


CALL = ToolCall(call_id="call_1", name="read", arguments_json='{"path":"src/main.py"}')
ASSISTANT = Message(role="assistant", tool_calls=(CALL,))
TOOL = Message(role="tool", tool_call_id="call_1", content="result")


@pytest.mark.parametrize(
    "messages",
    [
        (),
        (TOOL,),
        (*MESSAGES, ASSISTANT),
        (*MESSAGES, ASSISTANT, TOOL, TOOL),
        (*MESSAGES, ASSISTANT, Message(role="user", content="continue"), TOOL),
        (*MESSAGES, ASSISTANT, TOOL, ASSISTANT, TOOL),
    ],
)
def test_input_tool_correlation(messages):
    with pytest.raises(ValueError):
        validate_input(messages, runtime_tools(), None)


def test_bad_continuation_is_rejected_before_dispatch():
    async def run():
        def handler(request):
            pytest.fail("invalid continuation was sent")

        async with provider(handler) as model:
            malformed = Message(
                role="assistant",
                content="visible",
                continuation=json.dumps([output_text("different")]),
            )
            with pytest.raises(ProviderError, match="invalid_request"):
                await model.generate((*MESSAGES, malformed))

    asyncio.run(run())


def test_no_workspace_lifecycle_tool_and_no_model_state_payload():
    with pytest.raises(ValueError):
        tool_request(ToolCall(call_id="1", name="workspace", arguments_json="{}"), "local-1")
    with pytest.raises(ValidationError):
        Message.model_validate({"role": "assistant", "content": "done", "state": "VERIFIED"})


@pytest.mark.parametrize("body", [b"not-json", b"{}", b'{"output":"wrong"}'])
def test_malformed_wire_response_is_classified(body):
    async def run():
        async with provider(
            lambda r: httpx2.Response(
                200, content=body, headers={"content-type": "application/json"}
            )
        ) as model:
            with pytest.raises(ProviderError, match="invalid_response"):
                await model.generate(MESSAGES)

    asyncio.run(run())


def test_reused_call_id_from_an_earlier_turn_is_rejected():
    with pytest.raises(ProviderError, match="invalid_response"):
        generate(
            wire(function_call()), messages=(*MESSAGES, ASSISTANT, TOOL), tools=runtime_tools()
        )


def test_unknown_sdk_output_type_and_excessive_context_fail_closed():
    with pytest.raises(ProviderError, match="invalid_response"):
        generate(wire({"type": "new_hosted_tool", "command": "execute"}))
    with pytest.raises(ProviderError, match="invalid_request"):
        generate(wire(output_text("hi")), messages=(Message(role="user", content="x" * 2097153),))


@pytest.mark.parametrize(
    "fields",
    [
        {"input_tokens": -1, "output_tokens": 1, "total_tokens": 0},
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 3},
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "cached_input_tokens": 2},
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "reasoning_tokens": 2},
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "cache_write_tokens": 2},
    ],
)
def test_usage_invariants(fields):
    with pytest.raises(ValidationError):
        TokenUsage.model_validate(fields)
