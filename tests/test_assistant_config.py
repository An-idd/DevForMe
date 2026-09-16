import asyncio
import json
import os

import httpx2
import pytest
from openai import DefaultAsyncHttpxClient
from pydantic import SecretStr

import coding_agent.cli as cli
from coding_agent.core.models import DomainModel
from coding_agent.core.provider import GenerationSettings, Message, ProviderError, runtime_tools
from coding_agent.providers.config import load_assistant_config
from coding_agent.providers.zhipu import ZhipuProvider

URL = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
KEY = "synthetic-private-key"
SETTINGS = GenerationSettings(model="glm-5.3", max_output_tokens=128)
MESSAGES = (Message(role="user", content="Return an answer"),)


class Answer(DomainModel):
    answer: str


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    for name in ("PROVIDER", "MODEL", "API_URL", "API_KEY"):
        monkeypatch.delenv("CODING_AGENT_" + name, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        f"CODING_AGENT_PROVIDER=zhipu\nCODING_AGENT_API_URL={URL}\n"
        f"CODING_AGENT_MODEL=glm-5.3\nCODING_AGENT_API_KEY='{KEY}'\n",
        encoding="utf-8-sig",
    )
    return path


def test_config_precedence_no_export_and_secret_repr(env_file, monkeypatch):
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write("CODING_AGENT_CODEX_MODEL=separate-model\n")
    before = dict(os.environ)
    config = load_assistant_config(env_file)
    assert config.model == "glm-5.3" and config.api_key.get_secret_value() == KEY
    assert KEY not in repr(config) and KEY not in config.model_dump_json()
    assert dict(os.environ) == before
    monkeypatch.setenv("CODING_AGENT_MODEL", "environment-model")
    assert load_assistant_config(env_file).model == "environment-model"
    assert load_assistant_config(env_file, model="cli-model").model == "cli-model"
    monkeypatch.setenv("CODING_AGENT_API_KEY", "")
    with pytest.raises(ProviderError, match="configuration"):
        load_assistant_config(env_file)


@pytest.mark.parametrize(
    "extra",
    [
        "CODING_AGENT_PROVIDER=unknown",
        "CODING_AGENT_TYPO=value",
        "CODING_AGENT_API_KEY='unterminated",
        "CODING_AGENT_MODEL",
        pytest.param("X" * 65537, id="oversized"),
    ],
)
def test_invalid_configuration_is_safe(env_file, extra):
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write(extra)
    with pytest.raises(ProviderError) as error:
        load_assistant_config(env_file)
    assert str(error.value) == "model provider: configuration"
    assert KEY not in str(error.value)


def wire(content='{"answer":"ok"}', finish="stop", **updates):
    return {
        "id": "chat-1",
        "object": "chat.completion",
        "created": 1,
        "model": "actual-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": content, "reasoning_content": KEY},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    } | updates


def provider(handler, settings=SETTINGS):
    return ZhipuProvider(
        settings,
        api_key=SecretStr(KEY),
        api_url=URL,
        http_client=DefaultAsyncHttpxClient(
            transport=httpx2.MockTransport(handler),
            follow_redirects=False,
        ),
    )


def test_chat_wire_json_schema_usage_and_no_reasoning():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=wire())

    async def run():
        async with provider(handler) as model:
            result = await model.generate(MESSAGES, response_schema=Answer)
            assert result.structured == Answer(answer="ok")
            assert result.model == "actual-model" and result.usage.total_tokens == 15
            assert result.message.continuation is None
            assert KEY not in result.model_dump_json()

    asyncio.run(run())
    assert len(requests) == 1 and str(requests[0].url) == URL
    assert requests[0].headers["authorization"] == "Bearer " + KEY
    body = json.loads(requests[0].content)
    assert body["model"] == "glm-5.3" and body["max_tokens"] == 128
    assert body["response_format"] == {"type": "json_object"} and body["stream"] is False
    assert body["thinking"] == {"type": "disabled"}
    assert '"additionalProperties": false' in body["messages"][0]["content"]
    assert KEY not in requests[0].content.decode()
    assert "tools" not in body


@pytest.mark.parametrize(
    "payload,code",
    [
        (wire(finish="length"), "incomplete"),
        (wire(finish="content_filter"), "refused"),
        (wire(finish="tool_calls"), "invalid_response"),
        (wire(content='{"answer":7}'), "invalid_response"),
        (wire(content='{"answer":"ok","extra":true}'), "invalid_response"),
        (wire(content="not JSON"), "invalid_response"),
        (wire(choices=[]), "invalid_response"),
        (
            wire(usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 1}),
            "invalid_response",
        ),
        ({}, "invalid_response"),
    ],
)
def test_chat_rejects_unusable_output(payload, code):
    async def run():
        async with provider(lambda request: httpx2.Response(200, json=payload)) as model:
            with pytest.raises(ProviderError) as error:
                await model.generate(MESSAGES, response_schema=Answer)
            assert error.value.failure.code == code
            assert KEY not in str(error.value)

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication"),
        (429, "rate_limit"),
        (500, "unavailable"),
        (307, "invalid_request"),
    ],
)
def test_http_failures_no_retry_redirect_or_error_body(status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(
            status, json={"error": KEY}, headers={"location": "https://do-not-follow.invalid"}
        )

    async def run():
        async with provider(handler) as model:
            with pytest.raises(ProviderError) as error:
                await model.generate(MESSAGES)
            assert str(error.value) == "model provider: " + code

    asyncio.run(run())
    assert len(requests) == 1


def test_no_tools_and_timeout():
    requests = []

    async def handler(request):
        requests.append(request)
        await asyncio.sleep(0.2)
        return httpx2.Response(200, json=wire())

    async def run():
        settings = GenerationSettings(model="test", max_output_tokens=10, timeout_seconds=0.01)
        async with provider(handler, settings) as model:
            with pytest.raises(ProviderError, match="invalid_request"):
                await model.generate(MESSAGES, tools=runtime_tools())
            assert requests == []
            with pytest.raises(ProviderError, match="timeout"):
                await model.generate(MESSAGES)

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/chat/completions",
        "https://user:pass@example.com/chat/completions",
        URL + "?key=private",
        URL + "#fragment",
        URL + "/bad",
        "https://[bad",
    ],
)
def test_invalid_endpoint_fails_before_client(url):
    with pytest.raises(ProviderError, match="configuration"):
        ZhipuProvider(SETTINGS, api_key=SecretStr(KEY), api_url=url)


@pytest.mark.parametrize("command,token_limit", [("init", 8192), ("plan", 16384)])
def test_cli_selects_provider_and_redacts_file_key(
    env_file, monkeypatch, capsys, command, token_limit
):
    seen = []

    async def application(*args, provider, secrets, **kwargs):
        assert isinstance(provider, ZhipuProvider)
        assert provider.settings.model == "override"
        assert provider.settings.max_output_tokens == token_limit
        assert KEY in secrets
        seen.append(provider)
        raise ValueError("synthetic failure containing " + KEY)

    monkeypatch.setattr(cli, "initialize" if command == "init" else "plan", application)
    args = [command, "--env-file", str(env_file), "--model", "override"]
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2 and seen
    assert KEY not in capsys.readouterr().err
    assert os.environ.get("CODING_AGENT_API_KEY") is None


def test_cli_offline_ignores_dotenv_and_blank_key_blocks_before_init(
    env_file,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(env_file.parent)
    env_file.write_text("CODING_AGENT_API_KEY=\n", encoding="utf-8")
    assert cli.main(["init", ".", "--json"]) == 0
    capsys.readouterr()
    root = env_file.parent / "untouched"
    root.mkdir()
    with pytest.raises(SystemExit) as error:
        cli.main(["init", str(root), "--env-file", str(env_file)])
    assert error.value.code == 2 and "configuration" in capsys.readouterr().err
    assert not (root / ".agent").exists()


def test_cli_real_initialization_records_provider_failure_without_secrets(
    env_file,
    monkeypatch,
    capsys,
):
    root = env_file.parent / "project"
    root.mkdir()
    (root / "main.py").write_text("def run(): return 1\n", encoding="utf-8")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=wire(content='{"unexpected":"' + KEY + '"}'))

    class Configured(ZhipuProvider):
        def __init__(self, settings, *, api_key, api_url):
            assert api_key.get_secret_value() == KEY and api_url == URL
            super().__init__(
                settings,
                api_key=api_key,
                api_url=api_url,
                http_client=DefaultAsyncHttpxClient(
                    transport=httpx2.MockTransport(handler),
                    follow_redirects=False,
                ),
            )

    monkeypatch.setattr(cli, "ZhipuProvider", Configured)
    with pytest.raises(SystemExit) as error:
        cli.main(["init", str(root), "--env-file", str(env_file)])
    assert error.value.code == 2
    assert KEY not in capsys.readouterr().err
    assert len(requests) == 1
    assert KEY not in requests[0].content.decode()
    logs = list((root / ".agent").glob("init-*/events.jsonl"))
    assert len(logs) == 1
    records = logs[0].read_text(encoding="utf-8")
    assert '"model_requested"' in records and '"model_finished"' in records
    assert '"invalid_response"' in records and KEY not in records


def test_cli_openai_dotenv_selects_existing_transport(env_file, monkeypatch, capsys):
    from coding_agent.providers.openai import OpenAIProvider

    env_file.write_text(
        "CODING_AGENT_PROVIDER=openai\n"
        "CODING_AGENT_API_URL=https://api.openai.com/v1/responses\n"
        f"CODING_AGENT_MODEL=explicit-model\nCODING_AGENT_API_KEY={KEY}\n",
        encoding="utf-8",
    )
    seen = []

    async def application(*args, provider, secrets, **kwargs):
        assert isinstance(provider, OpenAIProvider)
        assert provider.settings.model == "explicit-model" and KEY in secrets
        seen.append(provider)
        raise ValueError("synthetic failure containing " + KEY)

    monkeypatch.setattr(cli, "initialize", application)
    with pytest.raises(SystemExit) as error:
        cli.main(["init", "--env-file", str(env_file)])
    assert error.value.code == 2 and seen
    assert KEY not in capsys.readouterr().err
