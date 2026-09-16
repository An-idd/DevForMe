import asyncio
import json
import shutil
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from coding_agent.core import TaskGraph, TaskSpec
from coding_agent.core.workflow import PlanApproval, Revision, RunSpec
from coding_agent.executors import codex
from coding_agent.executors.codex import CODEX_VERSION, CodexCoder, CodexSettings
from coding_agent.session.records import JsonlJournal, inspect_journal
from coding_agent.tools import ToolRuntime

REVISION = Revision(plan_version=1, context_revision="rules-1", workspace_revision="snapshot-1")


@pytest.fixture
def environment(tmp_path, make_task):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "secrets").mkdir()
    (root / "secrets/private.txt").write_text("private user data")
    task = TaskSpec.model_validate(
        make_task("t").model_dump()
        | {
            "permissions": {"network": False, "shell": "restricted", "database": "deny"},
        }
    )
    spec = RunSpec(session_id="session", graph=TaskGraph(tasks=(task,)), revision=REVISION)
    approval = PlanApproval(
        session_id="session",
        plan_fingerprint=spec.fingerprint,
        source="test-controller",
        timestamp=datetime.now(UTC),
    )
    with JsonlJournal(tmp_path / "records", "session") as journal:
        runtime = ToolRuntime(
            spec, "t", root=root, journal=journal, approval=approval, read_revision=lambda: REVISION
        )
        yield runtime


def settings(runtime, **changes):
    return CodexSettings(
        executable=Path(sys.executable).resolve(),
        home=runtime.root.parent / "profile",
        model="gpt-5.4",
        **changes,
    )


def implement(coder):
    return coder.implement(
        coder.runtime.task, REVISION, attempt=1, purpose="implement", feedback=()
    )


def final_item(text='{"outcome":"implemented","summary":"offline draft"}'):
    return {
        "id": "msg-1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def function_item(name, arguments, call_id="call-1"):
    return {
        "id": "fc-" + call_id,
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments),
    }


@pytest.fixture
def codex_server(monkeypatch):
    executable = shutil.which("codex")
    if executable is None:
        pytest.skip("native Codex CLI not installed; offline subprocess conformance unavailable")
    batches = []
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            requests.append(body)
            item = batches.pop(0) if batches else final_item()
            if callable(item):
                item()
                return
            events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {"type": "response.output_item.added", "output_index": 0, "item": item},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp-1",
                        "status": "completed",
                        "output": [item],
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                            "input_tokens_details": {"cached_tokens": 0},
                        },
                    },
                },
            ]
            data = "".join(
                "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Change only the trusted provider endpoint in this test, retaining all capability settings.
    offline = (
        'model_provider = "offline"\n'
        + codex._CONFIG
        + f'\n[model_providers.offline]\nname = "offline"\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        "request_max_retries = 0\nstream_max_retries = 0\n"
    )
    monkeypatch.setattr(codex, "_CONFIG", offline)
    try:
        yield Path(executable).resolve(), batches, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_native_codex_round_trip_uses_recorded_tool_runtime(environment, codex_server):
    executable, batches, requests = codex_server
    batches.extend(
        [
            function_item(
                "verified_patch",
                {
                    "kind": "patch",
                    "path": "src/hello.py",
                    "expected_sha256": None,
                    "content": "print('hello')\n",
                },
            ),
            final_item(),
        ]
    )
    coder = CodexCoder(
        settings(environment).model_copy(update={"executable": executable}), environment
    )
    result = asyncio.run(implement(coder))
    assert result.outcome == "implemented", result.summary
    assert (environment.root / "src/hello.py").read_text() == "print('hello')\n"
    assert len(requests) == 2
    offered = {t["name"] for t in requests[0]["tools"]}
    assert {
        "verified_read",
        "verified_patch",
        "verified_search",
        "verified_shell",
        "verified_git",
    } <= offered
    assert not offered & {"exec_command", "shell", "apply_patch", "web_search", "computer"}
    inspected = inspect_journal(environment.journal.directory / "events.jsonl")
    assert not inspected.unresolved
    assert [e.kind for e in inspected.events] == [
        "plan_registered",
        "model_requested",
        "model_finished",
        "tool_requested",
        "tool_finished",
        "model_requested",
        "model_finished",
    ]
    assert inspected.events[-1].model_result.usage.total_tokens == 30
    assert inspected.events[1].model_request.settings.max_output_tokens is None
    assert all(e.state is None and not e.evidence_ids for e in inspected.events)
    assert not (coder.settings.home / ".verified-runtime.lock").exists()


class FakeRPC:
    def __init__(self, events, journal, *, changed_thread=None):
        self.events = list(events)
        self.journal = journal
        self.changed_thread = changed_thread or {}
        self.started = False
        self.closed = False
        self.sent = []
        self.waiting = asyncio.Event()

    async def start(self, executable, home, cwd):
        self.started = True
        self.home, self.cwd = home, cwd
        assert (
            inspect_journal(self.journal.directory / "events.jsonl").events[-1].kind
            == "model_requested"
        )

    async def call(self, method, params):
        self.sent.append((method, params))
        if method == "initialize":
            return {"codexHome": str(self.home)}
        if method == "thread/start":
            return {
                "thread": {
                    "id": "thread",
                    "cliVersion": CODEX_VERSION,
                    "environments": [],
                    "ephemeral": True,
                },
                "model": "gpt-5.4",
                "cwd": str(self.cwd),
                "instructionSources": [],
                "runtimeWorkspaceRoots": [],
                "approvalPolicy": "never",
                "sandbox": {"type": "readOnly", "networkAccess": False},
            } | self.changed_thread
        if method == "turn/start":
            return {"turn": {"id": "turn", "status": "inProgress"}}
        raise AssertionError(method)

    async def send(self, value):
        self.sent.append(value)
        if "result" in value:
            events = inspect_journal(self.journal.directory / "events.jsonl").events
            assert events[-1].kind == "model_requested"
            assert events[-2].kind == "tool_finished"

    async def receive(self):
        if self.events:
            return self.events.pop(0)
        self.waiting.set()
        await asyncio.Event().wait()

    async def close(self):
        self.closed = True


def dynamic(path="src/created.py", *, call_id="call-1", **overrides):
    return {
        "id": 0,
        "method": "item/tool/call",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "namespace": None,
            "callId": call_id,
            "tool": "verified_patch",
            "arguments": {
                "path": path,
                "expected_sha256": None,
                "content": "runtime change",
                "kind": "patch",
            },
        }
        | overrides,
    }


def finished(text='{"outcome":"implemented","summary":"draft"}'):
    return [
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "item": {"type": "agentMessage", "id": "message", "text": text},
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread",
                "turn": {"id": "turn", "status": "completed", "error": None},
            },
        },
    ]


def fake_coder(monkeypatch, runtime, events, *, changed_thread=None, **options):
    rpc = FakeRPC(events, runtime.journal, changed_thread=changed_thread)
    monkeypatch.setattr(codex, "CodexRPC", lambda: rpc)
    return CodexCoder(settings(runtime, **options), runtime), rpc


def test_bridge_stops_between_model_requests_and_runtime_side_effects(environment, monkeypatch):
    coder, rpc = fake_coder(monkeypatch, environment, [dynamic(), *finished()])
    assert asyncio.run(implement(coder)).outcome == "implemented"
    assert (environment.root / "src/created.py").read_text() == "runtime change"
    assert rpc.closed
    assert not rpc.cwd.exists()


@pytest.mark.parametrize(
    "changed",
    [
        {
            "thread": {
                "id": "thread",
                "cliVersion": "unknown",
                "environments": [],
                "ephemeral": True,
            }
        },
        {
            "thread": {
                "id": "thread",
                "cliVersion": CODEX_VERSION,
                "environments": [{}],
                "ephemeral": True,
            }
        },
        {"instructionSources": [{"path": "external instructions"}]},
        {"runtimeWorkspaceRoots": ["outside"]},
        {"approvalPolicy": "on-request"},
        {"sandbox": {"type": "dangerFullAccess"}},
        {"model": "different-model"},
    ],
)
def test_capability_mismatch_stops_before_turn(environment, monkeypatch, changed):
    coder, rpc = fake_coder(monkeypatch, environment, [], changed_thread=changed)
    assert asyncio.run(implement(coder)).outcome == "failed"
    assert not any(isinstance(m, tuple) and m[0] == "turn/start" for m in rpc.sent)
    assert rpc.closed


@pytest.mark.parametrize(
    "event",
    [
        dynamic(tool="apply_patch"),
        dynamic(tool="verified_workspace"),
        dynamic(arguments={"path": "src/created.py"}),
        dynamic(
            arguments={
                "kind": "patch",
                "path": "src/created.py",
                "content": "bad",
                "expected_sha256": None,
                "approval": True,
            }
        ),
        dynamic(threadId="foreign"),
        dynamic(turnId="foreign"),
        dynamic(namespace="other"),
        {"id": 7, "method": "item/commandExecution/requestApproval", "params": {}},
        {"method": "item/started", "params": {"item": {"type": "fileChange"}}},
        {"method": "unexpected", "params": {}},
    ],
)
def test_invalid_requests_cannot_dispatch_tools(environment, monkeypatch, event):
    coder, rpc = fake_coder(monkeypatch, environment, [event])
    assert asyncio.run(implement(coder)).outcome == "failed"
    assert rpc.closed and not (environment.root / "src/created.py").exists()
    events = inspect_journal(environment.journal.directory / "events.jsonl").events
    assert not any(e.tool_request is not None for e in events)


@pytest.mark.parametrize("path", ["secrets/private.txt", "../outside.txt", ".agent/state.json"])
def test_runtime_denial_stops_codex_without_granting_approval(environment, monkeypatch, path):
    coder, rpc = fake_coder(monkeypatch, environment, [dynamic(path), *finished()])
    assert asyncio.run(implement(coder)).outcome == "blocked"
    assert (environment.root / "secrets/private.txt").read_text() == "private user data"
    assert not any(isinstance(m, dict) and "result" in m for m in rpc.sent)
    assert rpc.closed


def test_duplicate_call_id_never_repeats_effect(environment, monkeypatch):
    coder, rpc = fake_coder(monkeypatch, environment, [dynamic(), dynamic("src/other.py")])
    assert asyncio.run(implement(coder)).outcome == "failed"
    assert (environment.root / "src/created.py").exists()
    assert not (environment.root / "src/other.py").exists()


def test_tool_budget_stops_before_next_operation(environment, monkeypatch):
    coder, rpc = fake_coder(
        monkeypatch,
        environment,
        [dynamic(), dynamic("src/other.py", call_id="call-2")],
        max_tool_calls=1,
    )
    assert asyncio.run(implement(coder)).outcome == "blocked"
    assert not (environment.root / "src/other.py").exists()
    assert rpc.closed


@pytest.mark.parametrize(
    "text",
    [
        "not JSON",
        '{"outcome":"VERIFIED","summary":"lie"}',
        '{"outcome":"implemented","summary":"draft","state":"VERIFIED"}',
    ],
)
def test_final_output_is_only_a_validated_coder_draft(environment, monkeypatch, text):
    coder, rpc = fake_coder(monkeypatch, environment, finished(text))
    assert asyncio.run(implement(coder)).outcome == "failed"
    assert rpc.closed
    assert all(
        e.state is None
        for e in inspect_journal(environment.journal.directory / "events.jsonl").events
    )


@pytest.mark.parametrize("fail_write", [1, 2, 3, 4, 5])
def test_journal_failure_stops_dispatch_and_preserves_actual_changes(
    environment, monkeypatch, fail_write
):
    from coding_agent.core.workflow import EventWriteError
    from coding_agent.session import records

    coder, rpc = fake_coder(monkeypatch, environment, [dynamic(), *finished()])
    original = records.os.write
    calls = 0

    def fail(fd, data):
        nonlocal calls
        calls += 1
        if calls == fail_write:
            raise OSError("journal unavailable")
        return original(fd, data)

    monkeypatch.setattr(records.os, "write", fail)
    with pytest.raises(EventWriteError):
        asyncio.run(implement(coder))
    assert rpc.started == (fail_write > 1)
    assert rpc.closed
    assert (environment.root / "src/created.py").exists() == (fail_write >= 4)
    assert not any(isinstance(m, dict) and "result" in m for m in rpc.sent)
    inspected = inspect_journal(environment.journal.directory / "events.jsonl")
    assert bool(inspected.unresolved) == (fail_write in {2, 4})


def test_cancellation_reaps_executor_and_records_interruption(environment, monkeypatch):
    coder, rpc = fake_coder(monkeypatch, environment, [])

    async def run():
        pending = asyncio.create_task(implement(coder))
        await asyncio.wait_for(rpc.waiting.wait(), timeout=2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(run())
    assert rpc.closed and not rpc.cwd.exists()
    inspected = inspect_journal(environment.journal.directory / "events.jsonl")
    assert not inspected.unresolved and inspected.events[-1].model_result.status == "interrupted"


def test_total_timeout_is_bounded(environment, monkeypatch):
    coder, rpc = fake_coder(monkeypatch, environment, [], timeout_seconds=0.05)
    result = asyncio.run(implement(coder))
    assert result.outcome == "failed" and "timeout" in result.summary
    assert rpc.closed


def test_unapproved_or_stale_task_does_not_start_codex(environment, monkeypatch):
    coder, rpc = fake_coder(monkeypatch, environment, finished())
    environment.approval = None
    assert asyncio.run(implement(coder)).outcome == "blocked"
    assert not rpc.started


def test_user_profile_configuration_is_not_overwritten(environment, monkeypatch):
    coder, rpc = fake_coder(monkeypatch, environment, [])
    coder.settings.home.mkdir()
    config_path = coder.settings.home / "config.toml"
    config_path.write_text("[features]\nhooks = true\n")
    assert asyncio.run(implement(coder)).outcome == "failed"
    assert not rpc.started and config_path.read_text() == "[features]\nhooks = true\n"


def test_concurrent_profile_owner_is_not_removed(environment, monkeypatch):
    coder, rpc = fake_coder(monkeypatch, environment, [])
    coder.settings.home.mkdir()
    lock = coder.settings.home / ".verified-runtime.lock"
    lock.write_text("another owner")
    assert asyncio.run(implement(coder)).outcome == "failed"
    assert not rpc.started and lock.read_text() == "another owner"


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("exec_command", {"cmd": "echo native-bypass"}),
        ("apply_patch", {"patch": "*** Begin Patch\n*** Add File: bypass\n+unsafe\n*** End Patch"}),
        ("read_file", {"path": "secrets/private.txt"}),
    ],
)
def test_native_codex_rejects_unadvertised_native_tools(environment, codex_server, tool, arguments):
    executable, batches, requests = codex_server
    batches.extend([function_item(tool, arguments), final_item()])
    coder = CodexCoder(
        settings(environment).model_copy(update={"executable": executable}), environment
    )
    result = asyncio.run(implement(coder))
    assert result.outcome == "implemented", result.summary
    assert len(requests) == 2 and "unsupported call" in json.dumps(requests[1]["input"])
    assert "private user data" not in json.dumps(requests)
    assert not (environment.root / "bypass").exists()
    assert not any(
        e.tool_request
        for e in inspect_journal(environment.journal.directory / "events.jsonl").events
    )


@pytest.mark.parametrize("namespace", [None, "skills"])
def test_native_skill_cannot_read_arbitrary_project_paths(environment, codex_server, namespace):
    executable, batches, requests = codex_server
    item = function_item(
        "skills.read" if namespace is None else "read",
        {
            "package": str(environment.root / "secrets"),
            "resource": str(environment.root / "secrets/private.txt"),
        },
    )
    if namespace is not None:
        item["namespace"] = namespace
    batches.extend([item, final_item()])
    coder = CodexCoder(
        settings(environment).model_copy(update={"executable": executable}), environment
    )
    result = asyncio.run(implement(coder))
    assert result.outcome in {"implemented", "failed"}
    assert requests and "private user data" not in json.dumps(requests)
    if len(requests) == 2:
        outputs = [
            item["output"]
            for item in requests[1]["input"]
            if item["type"] == "function_call_output"
        ]
        assert outputs == [
            "unsupported call: skills.read"
            if namespace is None
            else "skill package is not available"
        ]


def test_native_codex_does_not_discover_target_project_configuration(environment, codex_server):
    executable, batches, requests = codex_server
    (environment.root / ".codex").mkdir()
    (environment.root / ".codex/config.toml").write_text("[features]\nhooks = true\n")
    (environment.root / "AGENTS.md").write_text("untrusted-project-discovery-marker")
    coder = CodexCoder(
        settings(environment).model_copy(update={"executable": executable}), environment
    )
    assert asyncio.run(implement(coder)).outcome == "implemented"
    assert "untrusted-project-discovery-marker" not in json.dumps(requests)


def test_openai_transport_still_requires_an_output_token_bound():
    from pydantic import SecretStr

    from coding_agent.core.provider import GenerationSettings, ProviderError
    from coding_agent.providers.openai import OpenAIProvider

    with pytest.raises(ProviderError, match="configuration"):
        OpenAIProvider(
            GenerationSettings(model="explicit", max_output_tokens=None), api_key=SecretStr("test")
        )


@pytest.mark.parametrize(
    "data",
    [b"not-json\n", b"[]\n", b'{"id":1}', b"", b"x" * (2 * 1024 * 1024 + 1) + b"\n"],
    ids=["invalid-json", "array", "truncated", "eof", "oversized"],
)
def test_stdio_rejects_malformed_truncated_or_oversized_messages(data):
    from types import SimpleNamespace

    from coding_agent.executors._codex_rpc import MAX_LINE, CodexRPC

    async def run():
        reader = asyncio.StreamReader(limit=MAX_LINE)
        reader.feed_data(data)
        reader.feed_eof()
        rpc = CodexRPC()
        rpc.process = SimpleNamespace(stdout=reader)
        with pytest.raises(ValueError):
            await rpc.receive()

    asyncio.run(run())


def test_rpc_rejects_a_foreign_response_id():
    from types import SimpleNamespace

    from coding_agent.executors._codex_rpc import CodexRPC

    class Input:
        def write(self, data):
            pass

        async def drain(self):
            pass

    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"id":"foreign", "result":{}}\n')
        rpc = CodexRPC()
        rpc.process = SimpleNamespace(stdout=reader, stdin=Input())
        with pytest.raises(ValueError, match="unexpected Codex"):
            await rpc.call("initialize", {})

    asyncio.run(run())


def test_cancellation_during_spawn_retains_process_for_cleanup(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from coding_agent.executors._codex_rpc import CodexRPC

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        process = SimpleNamespace(pid=123)

        async def create(*args, **kwargs):
            entered.set()
            await release.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        rpc = CodexRPC()
        launch = asyncio.create_task(rpc.start(Path(sys.executable), tmp_path, tmp_path))
        await entered.wait()
        launch.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await launch
        assert rpc.process is process

    asyncio.run(run())


def test_native_cancel_while_model_waits_reaps_process(environment, codex_server, monkeypatch):
    from coding_agent.executors._codex_rpc import CodexRPC

    executable, batches, requests = codex_server
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        release.wait(timeout=10)

    batches.append(hold)
    rpc = CodexRPC()
    monkeypatch.setattr(codex, "CodexRPC", lambda: rpc)
    coder = CodexCoder(
        settings(environment).model_copy(update={"executable": executable}), environment
    )

    async def run():
        pending = asyncio.create_task(implement(coder))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
        finally:
            pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    try:
        asyncio.run(run())
    finally:
        release.set()
    assert rpc.process.returncode is not None and rpc._job is None
    assert not (coder.settings.home / ".verified-runtime.lock").exists()
    inspected = inspect_journal(environment.journal.directory / "events.jsonl")
    assert not inspected.unresolved and inspected.events[-1].model_result.status == "interrupted"


def test_turn_notifications_before_ack_wait_for_identity_validation():
    from types import SimpleNamespace

    from coding_agent.executors._codex_rpc import CodexRPC

    class Input:
        def write(self, data):
            pass

        async def drain(self):
            pass

    async def run():
        events = [
            {"method": "thread/status/changed", "params": {"threadId": "thread"}},
            dynamic(),
            {"id": "vca-1", "result": {"turn": {"id": "turn"}}},
        ]
        reader = asyncio.StreamReader()
        reader.feed_data("".join(json.dumps(e) + "\n" for e in events).encode())
        rpc = CodexRPC()
        rpc.process = SimpleNamespace(stdout=reader, stdin=Input())
        result = await rpc.call("turn/start", {"threadId": "thread"})
        assert result == {"turn": {"id": "turn"}}
        assert await rpc.receive() == events[0]
        assert await rpc.receive() == events[1]

    asyncio.run(run())


@pytest.mark.parametrize("field", ["executable", "home"])
def test_executor_control_paths_cannot_be_in_agent_workspace(environment, field):
    path = environment.root / "src/controlled.exe"
    if field == "executable":
        path.write_text("untrusted project executable")
    config = settings(environment).model_copy(update={field: path})
    with pytest.raises(ValueError, match="outside project"):
        CodexCoder(config, environment)
