"""Vendor-independent model drafts, correlation and recorded call metadata.

These values never grant tool permission, change task state, or certify evidence.
"""

import json
from hashlib import sha256
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, Field, SerializeAsAny, StrictBool, model_validator

from .models import DomainModel, Identifier, NonEmptyStr
from .tools import Git, Patch, Read, Search, Shell, ToolRequest, ToolResult

ToolName = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MAX_MESSAGE_BYTES = 2 * 1024 * 1024


class GenerationSettings(DomainModel):
    model: NonEmptyStr
    # None records that an external engine has no enforceable per-call token cap.
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=65536)] | None = 2048
    timeout_seconds: Annotated[float, Field(gt=0, le=300, allow_inf_nan=False)] = 60.0


class TokenUsage(DomainModel):
    input_tokens: Count
    output_tokens: Count
    total_tokens: Count
    cached_input_tokens: Count | None = None
    cache_write_tokens: Count | None = None
    reasoning_tokens: Count | None = None

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("inconsistent token totals")
        if self.cached_input_tokens is not None and self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached tokens exceed input")
        if self.cache_write_tokens is not None and self.cache_write_tokens > self.input_tokens:
            raise ValueError("cache writes exceed input")
        if self.reasoning_tokens is not None and self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning tokens exceed output")
        return self


class ToolCall(DomainModel):
    call_id: Annotated[str, Field(min_length=1, max_length=256)]
    name: ToolName
    arguments_json: Annotated[str, Field(min_length=2, max_length=MAX_MESSAGE_BYTES)]


class Message(DomainModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    # Opaque adapter replay data, never instructions or an application state record.
    continuation: str | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_role(self) -> Self:
        if (self.role == "tool") != (self.tool_call_id is not None):
            raise ValueError("tool results require their call ID")
        if self.role != "assistant" and (self.tool_calls or self.continuation is not None):
            raise ValueError("only assistant messages carry calls/continuation")
        if not self.content and not self.tool_calls:
            raise ValueError("message has no text or calls")
        if len({call.call_id for call in self.tool_calls}) != len(self.tool_calls):
            raise ValueError("duplicate tool call IDs")
        return self


def validate_schema(schema: type[BaseModel]) -> None:
    if schema.model_config.get("extra") != "forbid":
        raise ValueError("model output and tool schemas must reject unknown fields")
    if schema.model_json_schema().get("type") != "object":
        raise ValueError("an object schema is required")


class ToolSchema(DomainModel):
    name: ToolName
    description: NonEmptyStr
    arguments_type: type[BaseModel]

    @model_validator(mode="after")
    def validate_arguments(self) -> Self:
        validate_schema(self.arguments_type)
        return self


class ModelResponse(DomainModel):
    response_id: NonEmptyStr
    model: NonEmptyStr
    message: Message
    structured: SerializeAsAny[BaseModel] | None = None
    usage: TokenUsage | None = None

    @model_validator(mode="after")
    def validate_response(self) -> Self:
        if self.message.role != "assistant":
            raise ValueError("model responses must be assistant messages")
        if self.structured is not None and self.message.tool_calls:
            raise ValueError("tool requests are not final structured output")
        return self


ErrorCode = Literal[
    "configuration",
    "authentication",
    "permission",
    "invalid_request",
    "rate_limit",
    "unavailable",
    "transport",
    "timeout",
    "invalid_response",
    "refused",
    "incomplete",
]


class ModelFailure(DomainModel):
    code: ErrorCode
    retryable: StrictBool = False
    status_code: Annotated[int, Field(strict=True, ge=100, le=599)] | None = None
    retry_after_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    response_id: str | None = None
    model: str | None = None
    usage: TokenUsage | None = None


class ProviderError(RuntimeError):
    def __init__(self, failure: ModelFailure) -> None:
        self.failure = failure
        # Never expose an SDK exception body, headers, prompt or credentials.
        super().__init__(f"model provider: {failure.code}")


class ModelProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def settings(self) -> GenerationSettings: ...

    async def generate(
        self,
        messages: tuple[Message, ...],
        *,
        tools: tuple[ToolSchema, ...] = (),
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse: ...


def validate_input(
    messages: tuple[Message, ...],
    tools: tuple[ToolSchema, ...],
    response_schema: type[BaseModel] | None,
) -> None:
    if not messages or len(messages) > 1000 or len(tools) > 32:
        raise ValueError("invalid message/tool count")
    if len({tool.name for tool in tools}) != len(tools):
        raise ValueError("duplicate tool names")
    for tool in tools:
        ToolSchema.model_validate(tool)
    if response_schema is not None:
        validate_schema(response_schema)
    pending: set[str] = set()
    seen: set[str] = set()
    size = 0
    for message in messages:
        Message.model_validate(message)
        size += len(message.model_dump_json().encode())
        if size > MAX_MESSAGE_BYTES:
            raise ValueError("model context exceeds size limit")
        if message.role == "tool":
            if message.tool_call_id is None or message.tool_call_id not in pending:
                raise ValueError("uncorrelated or repeated tool output")
            pending.remove(message.tool_call_id)
        else:
            if pending:
                raise ValueError("missing tool outputs before the next message")
            for call in message.tool_calls:
                if call.call_id in seen:
                    raise ValueError("reused tool call ID")
                seen.add(call.call_id)
                pending.add(call.call_id)
    if pending:
        raise ValueError("all requested tools need an output before generating again")


def validate_output(
    response: ModelResponse,
    tools: tuple[ToolSchema, ...],
    response_schema: type[BaseModel] | None,
) -> ModelResponse:
    response = ModelResponse.model_validate(response)
    if len(response.message.model_dump_json().encode()) > MAX_MESSAGE_BYTES:
        raise ValueError("model response exceeds size limit")
    definitions = {tool.name: tool for tool in tools}
    for call in response.message.tool_calls:
        if call.name not in definitions:
            raise ValueError("model requested an undeclared tool")
        definitions[call.name].arguments_type.model_validate_json(call.arguments_json, strict=True)
    if not response.message.tool_calls and response_schema is not None:
        parsed = response_schema.model_validate_json(response.message.content, strict=True)
        response = ModelResponse.model_validate(response.model_dump() | {"structured": parsed})
    elif response.structured is not None:
        raise ValueError("unexpected structured response")
    return response


def runtime_tools() -> tuple[ToolSchema, ...]:
    """Explicit Agent capability list: no workspace lifecycle or authority writers."""
    return tuple(
        ToolSchema(name=name, description=description, arguments_type=model)
        for name, description, model in (
            ("read", "Read a permitted UTF-8 file", Read),
            ("search", "Search permitted UTF-8 files", Search),
            ("patch", "Replace a permitted file after checking its SHA-256", Patch),
            ("shell", "Request a sandboxed command; runtime approval rules apply", Shell),
            ("git", "Inspect permitted Git status or diff", Git),
        )
    )


def tool_request(call: ToolCall, request_id: str) -> ToolRequest:
    definition = next((tool for tool in runtime_tools() if tool.name == call.name), None)
    if definition is None:
        raise ValueError("not an Agent runtime tool")
    invocation = definition.arguments_type.model_validate_json(call.arguments_json, strict=True)
    return ToolRequest.model_validate({"request_id": request_id, "invocation": invocation})


def tool_output(call: ToolCall, request: ToolRequest, result: ToolResult) -> Message:
    if tool_request(call, request.request_id) != request or result.request_id != request.request_id:
        raise ValueError("tool result belongs to another invocation")
    return Message(role="tool", tool_call_id=call.call_id, content=result.model_dump_json())


class ModelCallRequest(DomainModel):
    request_id: Identifier
    provider: NonEmptyStr
    settings: GenerationSettings
    input_sha256: Digest
    tools: tuple[ToolName, ...]
    output_schema: str | None

    @classmethod
    def describe(
        cls,
        request_id: str,
        provider: ModelProvider,
        messages: tuple[Message, ...],
        tools: tuple[ToolSchema, ...],
        schema: type[BaseModel] | None,
    ) -> Self:
        content = json.dumps(
            {
                "messages": [message.model_dump(mode="json") for message in messages],
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.arguments_type.model_json_schema(),
                    }
                    for tool in tools
                ],
                "schema": schema.model_json_schema() if schema is not None else None,
            },
            sort_keys=True,
        )
        return cls(
            request_id=request_id,
            provider=provider.name,
            settings=provider.settings,
            input_sha256=sha256(content.encode()).hexdigest(),
            tools=tuple(t.name for t in tools),
            output_schema=schema.__name__ if schema is not None else None,
        )


class ModelCallResult(DomainModel):
    request_id: Identifier
    request_event_id: Identifier
    status: Literal["completed", "failed", "interrupted"]
    response_id: str | None = None
    model: str | None = None
    usage: TokenUsage | None = None
    failure: ModelFailure | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if (self.status == "failed") != (self.failure is not None):
            raise ValueError("only failed model calls require a failure record")
        if self.status == "completed" and (not self.response_id or not self.model):
            raise ValueError("completed model calls require actual response identity")
        if self.failure is not None and (self.response_id, self.model, self.usage) != (
            self.failure.response_id,
            self.failure.model,
            self.failure.usage,
        ):
            raise ValueError("failure metadata must match its recorded result")
        return self
