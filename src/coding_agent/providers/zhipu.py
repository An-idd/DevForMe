"""Zhipu Chat Completions for bounded, read-only Explorer and Planner calls."""

import asyncio
import json
from typing import Any, Self, cast
from urllib.parse import urlsplit

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    DefaultAsyncHttpxClient,
)
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, SecretStr

from ..core.provider import (
    MAX_MESSAGE_BYTES,
    GenerationSettings,
    Message,
    ModelFailure,
    ModelResponse,
    ProviderError,
    TokenUsage,
    ToolSchema,
    validate_input,
    validate_output,
)
from .openai import _failure


class ZhipuProvider:
    """No tool loop, retries, redirect following, or raw reasoning persistence."""

    name = "zhipu-chat-completions"

    def __init__(
        self,
        settings: GenerationSettings,
        *,
        api_key: SecretStr,
        api_url: str,
        http_client: DefaultAsyncHttpxClient | None = None,
    ) -> None:
        self.settings = GenerationSettings.model_validate(settings)
        try:
            url = urlsplit(api_url)
            if (
                self.settings.max_output_tokens is None
                or not api_key.get_secret_value().strip()
                or url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
                or not url.path.endswith("/chat/completions")
                or any(character.isspace() for character in api_url)
            ):
                raise ValueError("invalid endpoint/configuration")
            self._client = AsyncOpenAI(
                api_key=api_key.get_secret_value(),
                base_url=api_url.removesuffix("/chat/completions"),
                max_retries=0,
                timeout=self.settings.timeout_seconds,
                _strict_response_validation=True,
                http_client=http_client or DefaultAsyncHttpxClient(follow_redirects=False),
            )
        except ValueError:
            raise ProviderError(ModelFailure(code="configuration")) from None

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
            if tools or any(
                message.role == "tool" or message.tool_calls or message.continuation is not None
                for message in messages
            ):
                raise ValueError("this adapter supports read-only text calls")
            inputs: list[dict[str, Any]] = [
                {"role": message.role, "content": message.content} for message in messages
            ]
            if response_schema is not None:
                inputs.insert(
                    0,
                    {
                        "role": "system",
                        "content": "Return only a JSON object matching this JSON Schema: "
                        + json.dumps(response_schema.model_json_schema(), ensure_ascii=False),
                    },
                )
            if len(json.dumps(inputs).encode()) > MAX_MESSAGE_BYTES:
                raise ValueError("context with schema exceeds limit")
        except (ValueError, TypeError):
            raise ProviderError(ModelFailure(code="invalid_request")) from None
        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                response = await self._client.chat.completions.create(
                    model=self.settings.model,
                    messages=cast(list[ChatCompletionMessageParam], inputs),
                    max_tokens=self.settings.max_output_tokens,
                    stream=False,
                    response_format=(
                        {"type": "json_object"} if response_schema else {"type": "text"}
                    ),
                    extra_body={"thinking": {"type": "disabled"}},
                )
        except (TimeoutError, APITimeoutError):
            raise ProviderError(ModelFailure(code="timeout", retryable=True)) from None
        except APIStatusError as error:
            raise ProviderError(_failure(error)) from None
        except APIConnectionError:
            raise ProviderError(ModelFailure(code="transport", retryable=True)) from None
        except (APIResponseValidationError, ValueError, TypeError):
            raise ProviderError(ModelFailure(code="invalid_response")) from None

        usage = None
        try:
            if response.usage is not None:
                usage = TokenUsage(
                    input_tokens=response.usage.prompt_tokens,
                    output_tokens=response.usage.completion_tokens,
                    total_tokens=response.usage.total_tokens,
                    cached_input_tokens=(
                        response.usage.prompt_tokens_details.cached_tokens
                        if response.usage.prompt_tokens_details
                        else None
                    ),
                    reasoning_tokens=(
                        response.usage.completion_tokens_details.reasoning_tokens
                        if response.usage.completion_tokens_details
                        else None
                    ),
                )
            if len(response.choices) != 1:
                raise ValueError("expected exactly one choice")
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise ProviderError(ModelFailure(code="incomplete"))
            if choice.finish_reason == "content_filter" or choice.message.refusal:
                raise ProviderError(ModelFailure(code="refused"))
            if (
                choice.finish_reason != "stop"
                or choice.message.role != "assistant"
                or choice.message.tool_calls
                or choice.message.function_call
            ):
                raise ValueError("unsupported completion")
            return validate_output(
                ModelResponse(
                    response_id=response.id,
                    model=response.model,
                    message=Message(role="assistant", content=choice.message.content or ""),
                    usage=usage,
                ),
                tools,
                response_schema,
            )
        except (ValueError, TypeError, AttributeError, ProviderError) as error:
            code = error.failure.code if isinstance(error, ProviderError) else "invalid_response"
            raise ProviderError(
                ModelFailure(code=code, response_id=response.id, model=response.model, usage=usage)
            ) from None
