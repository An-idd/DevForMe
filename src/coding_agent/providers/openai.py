"""One stateless OpenAI Responses transport. No tools execute here."""

import asyncio
import json
import math
import os
from typing import Any, Self, cast

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    DefaultAsyncHttpxClient,
    pydantic_function_tool,
)
from openai.types.responses import Response, ResponseInputParam, ResponseTextConfigParam, ToolParam
from pydantic import BaseModel, SecretStr

from ..core.provider import (
    MAX_MESSAGE_BYTES,
    ErrorCode,
    GenerationSettings,
    Message,
    ModelFailure,
    ModelResponse,
    ProviderError,
    TokenUsage,
    ToolCall,
    ToolSchema,
    validate_input,
    validate_output,
)


def _message(items: list[dict[str, Any]]) -> Message:
    text: list[str] = []
    calls: list[ToolCall] = []
    for item in items:
        kind = item["type"]
        if item.get("status") not in (None, "completed"):
            raise ValueError("unfinished output item")
        if kind == "message":
            if item["role"] != "assistant":
                raise ValueError("invalid output role")
            for part in item["content"]:
                if part["type"] == "refusal":
                    raise ProviderError(ModelFailure(code="refused"))
                if part["type"] != "output_text":
                    raise ValueError("unsupported output content")
                text.append(part["text"])
        elif kind == "function_call":
            calls.append(
                ToolCall(
                    call_id=item["call_id"], name=item["name"], arguments_json=item["arguments"]
                )
            )
        elif kind == "reasoning":
            # Required for replay with store=False; never persist raw reasoning.
            if not item.get("encrypted_content"):
                raise ValueError("missing encrypted reasoning continuation")
        else:
            raise ValueError("unsupported output item")
    return Message(role="assistant", content="".join(text), tool_calls=tuple(calls))


def _input(messages: tuple[Message, ...]) -> ResponseInputParam:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.continuation is not None:
            replay = json.loads(message.continuation)
            if not isinstance(replay, list):
                raise ValueError("invalid continuation")
            visible = _message(replay)
            if visible.content != message.content or visible.tool_calls != message.tool_calls:
                raise ValueError("continuation does not match its message")
            items.extend(replay)
        elif message.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
        else:
            if message.content:
                items.append({"role": message.role, "content": message.content})
            items.extend(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments_json,
                }
                for call in message.tool_calls
            )
    return cast(ResponseInputParam, items)


def _usage(response: Response) -> TokenUsage | None:
    usage = response.usage
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cached_input_tokens=usage.input_tokens_details.cached_tokens,
        reasoning_tokens=usage.output_tokens_details.reasoning_tokens,
        cache_write_tokens=usage.input_tokens_details.cache_write_tokens,
    )


def _failure(error: APIStatusError) -> ModelFailure:
    status = error.status_code
    code: ErrorCode
    if status == 401:
        code = "authentication"
    elif status == 403:
        code = "permission"
    elif status == 429:
        code = "rate_limit"
    elif status == 408:
        code = "timeout"
    elif status >= 500:
        code = "unavailable"
    else:
        code = "invalid_request"
    delay = None
    try:
        candidate = float(error.response.headers.get("retry-after", ""))
        if math.isfinite(candidate) and candidate >= 0:
            delay = candidate
    except ValueError:
        pass
    return ModelFailure(
        code=code,
        status_code=status,
        retryable=status in (408, 429) or status >= 500,
        retry_after_seconds=delay,
    )


class OpenAIProvider:
    """Controller configuration selects the model explicitly; SDK retries are disabled."""

    name = "openai-responses"

    def __init__(
        self,
        settings: GenerationSettings,
        *,
        api_key: SecretStr | None = None,
        http_client: DefaultAsyncHttpxClient | None = None,
    ) -> None:
        self.settings = GenerationSettings.model_validate(settings)
        if self.settings.max_output_tokens is None:
            raise ProviderError(ModelFailure(code="configuration"))
        key = (
            api_key.get_secret_value() if api_key is not None else os.environ.get("OPENAI_API_KEY")
        )
        if not key or not key.strip():
            raise ProviderError(ModelFailure(code="configuration"))
        self._client = AsyncOpenAI(
            api_key=key,
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=self.settings.timeout_seconds,
            _strict_response_validation=True,
            http_client=http_client or DefaultAsyncHttpxClient(follow_redirects=False),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._client.close()

    async def generate(
        self,
        messages: tuple[Message, ...],
        *,
        tools: tuple[ToolSchema, ...] = (),
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        try:
            validate_input(messages, tools, response_schema)
            inputs = _input(messages)
            functions = [
                {
                    "type": "function",
                    **pydantic_function_tool(
                        tool.arguments_type,
                        name=tool.name,
                        description=tool.description,
                    )["function"],
                }
                for tool in tools
            ]
            text: dict[str, Any] = {"format": {"type": "text"}}
            if response_schema is not None:
                function = pydantic_function_tool(response_schema)["function"]
                text = {
                    "format": {
                        "type": "json_schema",
                        "name": function["name"],
                        "strict": True,
                        "schema": function["parameters"],
                    }
                }
        except (ValueError, TypeError, KeyError, ProviderError):
            raise ProviderError(ModelFailure(code="invalid_request")) from None
        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                response = await self._client.responses.create(
                    model=self.settings.model,
                    input=inputs,
                    tools=cast(list[ToolParam], functions),
                    text=cast(ResponseTextConfigParam, text),
                    store=False,
                    stream=False,
                    include=["reasoning.encrypted_content"],
                    parallel_tool_calls=False,
                    max_output_tokens=self.settings.max_output_tokens,
                    truncation="disabled",
                )
        except (TimeoutError, APITimeoutError):
            raise ProviderError(ModelFailure(code="timeout", retryable=True)) from None
        except APIStatusError as error:
            raise ProviderError(_failure(error)) from None
        except APIResponseValidationError:
            raise ProviderError(ModelFailure(code="invalid_response")) from None
        except (ValueError, TypeError):
            raise ProviderError(ModelFailure(code="invalid_response")) from None
        except APIConnectionError:
            raise ProviderError(ModelFailure(code="transport", retryable=True)) from None

        usage = None
        try:
            usage = _usage(response)
            if response.status == "incomplete":
                raise ProviderError(ModelFailure(code="incomplete"))
            if response.status != "completed" or response.error is not None:
                raise ProviderError(ModelFailure(code="unavailable"))
            items = [item.model_dump(mode="json", exclude_none=True) for item in response.output]
            continuation = json.dumps(items)
            if len(continuation.encode()) > MAX_MESSAGE_BYTES:
                raise ValueError("response exceeds size limit")
            message = _message(items)
            seen = {call.call_id for m in messages for call in m.tool_calls}
            if any(call.call_id in seen for call in message.tool_calls):
                raise ValueError("reused tool call ID")
            message = Message.model_validate(message.model_dump() | {"continuation": continuation})
            return validate_output(
                ModelResponse(
                    response_id=response.id,
                    model=response.model,
                    message=message,
                    usage=usage,
                ),
                tools,
                response_schema,
            )
        except (ValueError, TypeError, KeyError, AttributeError, ProviderError) as error:
            code = error.failure.code if isinstance(error, ProviderError) else "invalid_response"
            raise ProviderError(
                ModelFailure(code=code, response_id=response.id, model=response.model, usage=usage)
            ) from None
