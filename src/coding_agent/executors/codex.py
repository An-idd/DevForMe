"""Codex owns its coding loop; this bridge only records and dispatches runtime tools.

Each generate() stops at a dynamic-tool request. ModelRuntime flushes its result,
ToolRuntime records/executes the operation, and the next recorded generate() sends
its reply. The existing single-writer journal needs no nested operation support.
"""

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ..core.models import DomainModel, NonEmptyStr, TaskSpec
from ..core.provider import (
    GenerationSettings,
    Message,
    ModelFailure,
    ModelResponse,
    ProviderError,
    TokenUsage,
    ToolCall,
    ToolSchema,
    runtime_tools,
    tool_output,
    tool_request,
    validate_input,
    validate_output,
)
from ..core.tools import ToolResult
from ..core.workflow import CoderResult, Revision
from ..providers.runtime import ModelRuntime
from ..tools import ToolRuntime
from ._codex_rpc import CodexRPC

# Experimental environment suppression has been checked against this exact build.
CODEX_VERSION = "0.154.0-alpha.6.2"
_CONFIG = """web_search = "disabled"
cli_auth_credentials_store = "file"
check_for_update_on_startup = false
[features]
shell_tool = false
unified_exec = false
hooks = false
plugins = false
apps = false
multi_agent = false
browser_use = false
computer_use = false
code_mode = false
code_mode_host = false
code_mode_prewarm = false
image_generation = false
view_image = false
shell_snapshot = false
skill_mcp_dependency_install = false
skip_host_skill_discovery = true
skill_search = false
unbounded_connection_retries = false
workspace_dependencies = false
tool_suggest = false
goals = false
sleep_tool = false
"""


class CodexSettings(DomainModel):
    executable: Path
    home: Path
    model: NonEmptyStr
    timeout_seconds: Annotated[float, Field(gt=0, le=300, allow_inf_nan=False)] = 60.0
    max_tool_calls: Annotated[int, Field(strict=True, ge=1, le=100)] = 20


class _CodexBridge:
    name = f"codex-app-server/{CODEX_VERSION}"

    def __init__(self, config: CodexSettings) -> None:
        self.config = config
        self.settings = GenerationSettings(
            model=config.model,
            max_output_tokens=None,
            timeout_seconds=config.timeout_seconds,
        )
        self.rpc = CodexRPC()
        self._directory: TemporaryDirectory[str] | None = None
        self._lock: int | None = None
        self._history: tuple[Message, ...] = ()
        self._pending: int | str | None = None
        self._seen: set[str] = set()
        self._thread = ""
        self._turn = ""
        self._model = ""
        self._usage: TokenUsage | None = None
        self._closed = False

    async def _start(self, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> None:
        home = self.config.home
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if home.resolve() != home:
            raise ProviderError(ModelFailure(code="configuration"))
        self._lock = os.open(
            home / ".verified-runtime.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        config_path = home / "config.toml"
        if config_path.exists():
            if config_path.is_symlink() or config_path.stat().st_size > 8192:
                raise ProviderError(ModelFailure(code="configuration"))
            if config_path.read_text(encoding="utf-8") != _CONFIG:
                raise ProviderError(ModelFailure(code="configuration"))
        else:
            with config_path.open("x", encoding="utf-8") as stream:
                stream.write(_CONFIG)
        self._directory = TemporaryDirectory(prefix="verified-codex-")
        cwd = Path(self._directory.name)
        await self.rpc.start(self.config.executable, home, cwd)
        initialized = await self.rpc.call(
            "initialize",
            {
                "clientInfo": {"name": "verified-coding-agent", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        if Path(initialized["codexHome"]) != home:
            raise ProviderError(ModelFailure(code="configuration"))
        await self.rpc.send({"method": "initialized", "params": {}})
        started = await self.rpc.call(
            "thread/start",
            {
                "model": self.config.model,
                "cwd": str(cwd),
                "environments": [],
                "ephemeral": True,
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "baseInstructions": (
                    "Implement the supplied task using only verified_* dynamic tools. "
                    "All paths are relative to the project served by those tools. "
                    "Respect the supplied scope and requirements. Return only the requested "
                    "CoderResult JSON. implemented means a draft, never verified success. "
                    "Request replanning when scope or requirements must change."
                ),
                "dynamicTools": [
                    {
                        "type": "function",
                        "name": "verified_" + tool.name,
                        "description": tool.description,
                        "inputSchema": tool.arguments_type.model_json_schema(),
                    }
                    for tool in tools
                ],
            },
        )
        thread = started["thread"]
        if (
            thread["cliVersion"] != CODEX_VERSION
            or thread["environments"] != []
            or thread["ephemeral"] is not True
            or started["instructionSources"] != []
            or started["runtimeWorkspaceRoots"] != []
            or started["approvalPolicy"] != "never"
            or started["sandbox"] != {"type": "readOnly", "networkAccess": False}
            or Path(started["cwd"]) != cwd
            or started["model"] != self.config.model
        ):
            raise ProviderError(ModelFailure(code="configuration"))
        self._thread, self._model = thread["id"], started["model"]
        turn = await self.rpc.call(
            "turn/start",
            {
                "threadId": self._thread,
                "environments": [],
                "input": [
                    {
                        "type": "text",
                        "text": json.dumps([m.model_dump(mode="json") for m in messages]),
                    }
                ],
                "outputSchema": CoderResult.model_json_schema(),
            },
        )
        self._turn = turn["turn"]["id"]
        if not self._thread or not self._turn or turn["turn"]["status"] != "inProgress":
            raise ValueError("invalid Codex turn identity")

    async def generate(
        self,
        messages: tuple[Message, ...],
        *,
        tools: tuple[ToolSchema, ...] = (),
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        try:
            if self._closed or tools != runtime_tools() or response_schema is not CoderResult:
                raise ValueError("unsupported Codex bridge contract")
            validate_input(messages, tools, response_schema)
            if not self._history:
                await self._start(messages, tools)
            else:
                if (
                    len(messages) != len(self._history) + 1
                    or messages[:-1] != self._history
                    or messages[-1].role != "tool"
                    or self._pending is None
                ):
                    raise ValueError("Codex continuation does not match the pending call")
                await self.rpc.send(
                    {
                        "id": self._pending,
                        "result": {
                            "contentItems": [{"type": "inputText", "text": messages[-1].content}],
                            "success": ToolResult.model_validate_json(messages[-1].content).status
                            == "succeeded",
                        },
                    }
                )
                self._pending = None
            response = await self._receive()
            response = validate_output(response, tools, response_schema)
            self._history = (*messages, response.message)
            if not response.message.tool_calls:
                await self.close()
            return response
        except ProviderError:
            await self.close()
            raise
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise ProviderError(
                ModelFailure(
                    code="invalid_response",
                    model=self._model or None,
                    usage=self._usage,
                )
            ) from None

    async def _receive(self) -> ModelResponse:
        final_text = ""
        while True:
            event = await self.rpc.receive()
            method = event.get("method")
            params = event.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("invalid Codex notification")
            if params.get("threadId", self._thread) != self._thread:
                raise ValueError("foreign Codex thread")
            if params.get("turnId", self._turn) != self._turn:
                raise ValueError("foreign Codex turn")
            if "id" in event:
                if method != "item/tool/call" or type(event["id"]) not in (int, str):
                    raise ValueError("unsupported Codex server request")
                if (
                    params["threadId"] != self._thread
                    or params["turnId"] != self._turn
                    or params["namespace"] is not None
                    or not params["tool"].startswith("verified_")
                ):
                    raise ValueError("untrusted Codex tool routing")
                call = ToolCall(
                    call_id=params["callId"],
                    name=params["tool"][9:],
                    arguments_json=json.dumps(params["arguments"]),
                )
                if call.call_id in self._seen:
                    raise ValueError("duplicate Codex call")
                self._seen.add(call.call_id)
                self._pending = event["id"]
                return ModelResponse(
                    response_id=f"{self._turn}:{len(self._seen)}",
                    model=self._model,
                    message=Message(role="assistant", tool_calls=(call,)),
                )
            if method == "thread/tokenUsage/updated":
                total = params["tokenUsage"]["total"]
                self._usage = TokenUsage(
                    input_tokens=total["inputTokens"],
                    output_tokens=total["outputTokens"],
                    total_tokens=total["totalTokens"],
                    cached_input_tokens=total["cachedInputTokens"],
                    cache_write_tokens=total["cacheWriteInputTokens"],
                    reasoning_tokens=total["reasoningOutputTokens"],
                )
            elif method in {"item/started", "item/completed"}:
                item = params["item"]
                if item["type"] not in {
                    "userMessage",
                    "agentMessage",
                    "reasoning",
                    "dynamicToolCall",
                }:
                    raise ValueError("Codex attempted an unbridged native tool")
                if method == "item/completed" and item["type"] == "agentMessage":
                    if item.get("phase") in (None, "final_answer"):
                        final_text = item["text"]
            elif method == "turn/completed":
                turn = params["turn"]
                if turn["id"] != self._turn or turn["status"] != "completed" or turn.get("error"):
                    raise ValueError("Codex turn did not complete")
                return ModelResponse(
                    response_id=self._turn,
                    model=self._model,
                    usage=self._usage,
                    message=Message(role="assistant", content=final_text),
                )
            elif method not in {
                "thread/started",
                "turn/started",
                "thread/status/changed",
                "warning",
                "account/rateLimits/updated",
                "account/updated",
                "remoteControl/status/changed",
                "item/agentMessage/delta",
                "item/reasoning/textDelta",
                "item/reasoning/summaryTextDelta",
                "item/reasoning/summaryPartAdded",
                "turn/diff/updated",
                "turn/plan/updated",
            }:
                raise ValueError("unsupported Codex notification")

    async def close(self) -> None:
        if self._closed:
            return
        await self.rpc.close()
        if self._directory is not None:
            self._directory.cleanup()
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None
            (self.config.home / ".verified-runtime.lock").unlink()
        self._closed = True


class CodexCoder:
    """One approved task, one fresh Codex turn per Coder attempt; no state authority."""

    def __init__(self, settings: CodexSettings, runtime: ToolRuntime) -> None:
        self.settings = CodexSettings.model_validate(settings)
        self.runtime = runtime
        for path in (self.settings.executable, self.settings.home):
            if not path.is_absolute() or path.resolve() != path:
                raise ValueError("Codex paths must be absolute and canonical")
            if path.is_relative_to(runtime.root) or path.is_relative_to(runtime.journal.directory):
                raise ValueError("Codex executable/profile must be outside project and journal")
        if not settings.executable.is_file():
            raise ValueError("Codex native executable is unavailable")
        if os.name == "nt" and settings.executable.suffix.lower() != ".exe":
            raise ValueError("Windows requires the native Codex executable, not a shell shim")
        self._busy = False

    async def implement(
        self,
        task: TaskSpec,
        revision: Revision,
        *,
        attempt: int,
        purpose: Literal["implement", "debug", "review_fix"],
        feedback: tuple[str, ...],
    ) -> CoderResult:
        if self._busy:
            raise ValueError("CodexCoder is serial")
        if (
            task != self.runtime.task
            or revision != self.runtime.read_revision()
            or revision.plan_version != self.runtime.spec.revision.plan_version
            or revision.context_revision != self.runtime.spec.revision.context_revision
            or self.runtime.approval is None
            or not self.runtime.approval.covers(self.runtime.spec)
        ):
            return CoderResult(
                outcome="blocked",
                summary="Codex requires the matching approved task and live revision",
            )
        if type(attempt) is not int or not 1 <= attempt <= task.max_attempts:
            raise ValueError("invalid Coder attempt")
        self._busy = True
        bridge = _CodexBridge(self.settings)
        model = ModelRuntime(
            bridge,
            journal=self.runtime.journal,
            read_revision=self.runtime.read_revision,
            task_id=task.id,
        )
        messages: tuple[Message, ...] = (
            Message(
                role="user",
                content=json.dumps(
                    {
                        "task": task.model_dump(mode="json"),
                        "revision": revision.model_dump(mode="json"),
                        "attempt": attempt,
                        "purpose": purpose,
                        "feedback": feedback,
                        "max_tool_calls": self.settings.max_tool_calls,
                    }
                ),
            ),
        )
        try:
            async with asyncio.timeout(
                min(self.settings.timeout_seconds, self.runtime.spec.worker_timeout_seconds)
            ):
                for index in range(self.settings.max_tool_calls + 1):
                    response = await model.generate(
                        messages, tools=runtime_tools(), response_schema=CoderResult
                    )
                    if not response.message.tool_calls:
                        assert isinstance(response.structured, CoderResult)
                        return response.structured
                    if index == self.settings.max_tool_calls:
                        return CoderResult(outcome="blocked", summary="Codex tool budget exhausted")
                    call = response.message.tool_calls[0]
                    request = tool_request(call, f"codex-{uuid4().hex}")
                    result = await self.runtime.execute(request)
                    if result.status in {"denied", "needs_approval"}:
                        return CoderResult(
                            outcome="blocked", summary=f"Tool Runtime: {result.status}"
                        )
                    messages = (*messages, response.message, tool_output(call, request, result))
        except (ProviderError, TimeoutError) as error:
            reason = error.failure.code if isinstance(error, ProviderError) else "timeout"
            return CoderResult(outcome="failed", summary=f"Codex adapter: {reason}")
        finally:
            try:
                await bridge.close()
            except BaseException:
                self.runtime.journal.invalidate()
                raise
            finally:
                self._busy = False
        raise AssertionError("unreachable Codex loop")
