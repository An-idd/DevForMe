import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from coding_agent.application import initialize
from coding_agent.cli import main
from coding_agent.context.explorer import reference
from coding_agent.core.knowledge import (
    InitializationOperation,
    InitializationRevision,
    KnowledgeEntry,
    KnowledgeSnapshot,
    OpenQuestion,
    RepoSummary,
    SourceFile,
    SourceReference,
    ValidationCommand,
)
from coding_agent.core.models import ScopePolicy
from coding_agent.core.provider import Message, ModelResponse, ToolCall
from coding_agent.core.tools import Decision, ToolRequest
from coding_agent.core.workflow import EventWriteError, WorkflowEvent
from coding_agent.session.records import JsonlJournal, inspect_journal
from coding_agent.testing import FakeModelProvider
from coding_agent.tools.initialization import InitializationRuntime, digest


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text(
        "# Development\n\nKeep storage access behind the Store interface.\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname="sample"\ndependencies=["fastapi>=1"]\n'
        '[tool.pytest.ini_options]\ntestpaths=["tests"]\n[tool.ruff]\nline-length=100\n',
        encoding="utf-8",
    )
    (root / "service.py").write_text(
        "import store\n\ndef get_order():\n    return store.load()\n", encoding="utf-8"
    )
    (root / "store.py").write_text("def load():\n    return 'order'\n", encoding="utf-8")
    return root


def init(repo, **kwargs):
    return asyncio.run(initialize(repo, **kwargs))


def snapshot(result):
    path = Path(result.guide).parent / f"knowledge-{result.revision}.json"
    return KnowledgeSnapshot.model_validate_json(path.read_bytes())


def response(summary):
    return ModelResponse(
        response_id="explore-1",
        model="fake-explorer",
        message=Message(role="assistant", content=summary.model_dump_json()),
    )


def test_init_is_sourced_read_only_and_precedes_publication(repo, monkeypatch):
    before = {p.name: p.read_bytes() for p in repo.iterdir()}
    actual = InitializationRuntime._replace
    seen = []

    def checked(runtime, name, data, expected):
        view = inspect_journal(runtime.journal.directory / "events.jsonl")
        request = view.events[-1]
        assert request.kind == "tool_requested"
        assert request.tool_request.invocation.operation == "publish"
        assert view.unresolved == (request.event_id,)
        seen.append(name)
        return actual(runtime, name, data, expected)

    monkeypatch.setattr(InitializationRuntime, "_replace", checked)
    result = init(repo)
    assert result.status == "initialized"
    assert seen == [f"knowledge-{result.revision}.json", "project.md", "project.json"]
    saved = snapshot(result)
    assert {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()} == before
    assert all(c.status == "discovered" for c in saved.summary.commands)
    assert {c.purpose for c in saved.summary.commands} == {"test", "lint"}
    guide = Path(result.guide).read_text()
    assert "store.load()" in guide and "Language indicator: Python" in guide
    assert "Framework dependency declaration" in guide and "fact | coupling" in guide
    assert any(e.authority == "rule" and "Store interface" in e.text for e in saved.summary.entries)
    view = inspect_journal(Path(result.journal))
    assert [e.kind for e in view.events] == [
        "tool_requested",
        "tool_finished",
        "tool_requested",
        "tool_finished",
    ]
    assert not view.unresolved
    assert all(isinstance(e.revision, InitializationRevision) for e in view.events)
    assert all(e.task_id is None and e.state is None and not e.evidence_ids for e in view.events)


def test_repeated_init_and_unchanged_refresh_reuse_identical_files(repo):
    first = init(repo)
    paths = [Path(first.guide), Path(first.guide).with_suffix(".json")]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    for refresh in (False, True):
        result = init(repo, refresh=refresh)
        assert result.status == "reused" and result.revision == first.revision
        assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before
        assert len(inspect_journal(Path(result.journal)).events) == 2


def test_source_changes_require_refresh_and_retain_old_revision_and_rule_ids(repo):
    first = init(repo)
    old = snapshot(first)
    before = Path(first.guide).read_bytes()
    (repo / "service.py").write_text("import store\n\ndef load_order():\n    return store.load()\n")
    stale = init(repo)
    assert stale.status == "stale" and "service.py" in stale.changed_sources
    assert Path(first.guide).read_bytes() == before
    fresh = init(repo, refresh=True)
    assert fresh.status == "refreshed" and fresh.revision != first.revision
    new = snapshot(fresh)
    assert new.previous_revision == first.revision and snapshot(first) == old
    assert {e.id for e in old.summary.entries if e.authority == "rule"} == {
        e.id for e in new.summary.entries if e.authority == "rule"
    }
    assert "load_order" in Path(fresh.guide).read_text()


def test_existing_guide_and_later_user_additions_are_preserved(repo):
    folder = repo / ".agent"
    folder.mkdir()
    path = folder / "project.md"
    original = "User architecture notes.\r\n\r\nUse InventoryService; direct writes are legacy.\r\n"
    path.write_bytes(original.encode())
    first = init(repo)
    assert path.read_bytes().startswith(original.encode())
    with path.open("ab") as stream:
        stream.write(b"\nExternal APIs must stay compatible.\n")
    assert init(repo).status == "stale"
    fresh = init(repo, refresh=True, rules=("Do not add dependencies.",))
    raw = path.read_bytes()
    assert raw.startswith(original.encode()) and raw.endswith(
        b"\nExternal APIs must stay compatible.\n"
    )
    saved = snapshot(fresh)
    rules = [e for e in saved.summary.entries if e.authority == "rule"]
    assert any("InventoryService" in e.text for e in rules)
    assert any("External APIs" in e.text for e in rules)
    assert any("Do not add dependencies" in e.text for e in rules)
    assert init(repo).revision == fresh.revision
    assert snapshot(first).previous_revision is None


def test_manual_generated_edit_is_not_overwritten(repo):
    result = init(repo)
    guide = Path(result.guide)
    changed = guide.read_text().replace("Generated repository guide", "My changed guide")
    guide.write_text(changed)
    with pytest.raises(ValueError, match="generated guide"):
        init(repo, refresh=True)
    assert guide.read_text() == changed


def test_inventory_additions_and_removed_sources_are_detected(repo):
    first = init(repo)
    (repo / "README.md").write_text("# A new API module")
    assert init(repo).status == "stale"
    (repo / "store.py").unlink()
    refreshed = init(repo, refresh=True)
    assert refreshed.status == "refreshed" and "store.py" in refreshed.changed_sources
    assert "README.md" in snapshot(refreshed).repository.paths
    assert all(s.path != "store.py" for s in snapshot(refreshed).repository.sources)
    assert snapshot(first).repository.sources


def test_scope_and_focus_are_explicit_and_forbidden_inputs_are_never_read(repo):
    (repo / "private").mkdir()
    (repo / "private" / "config.py").write_text("TOP_SECRET_DATA")
    (repo / ".env").write_text("API_KEY=PRIVATE_ENV")
    result = init(repo, scope=ScopePolicy(forbidden=("private/**",)), focus=("service.py",))
    data = (repo / ".agent" / f"knowledge-{result.revision}.json").read_text()
    assert "TOP_SECRET_DATA" not in data and "PRIVATE_ENV" not in data
    saved = snapshot(result)
    assert saved.repository.focus == ("service.py",)
    assert not any(p.startswith("private/") or p == ".env" for p in saved.repository.paths)
    with pytest.raises(ValueError, match="outside readable scope"):
        init(repo, focus=("private/config.py",), scope=ScopePolicy(forbidden=("private/**",)))


@pytest.mark.parametrize("focus", ["../outside", "/absolute", ".agent/project.md", "missing.py"])
def test_invalid_focus_fails_without_creating_guide(repo, focus):
    with pytest.raises(ValueError):
        init(repo, focus=(focus,))
    assert not (repo / ".agent/project.md").exists()


def test_hardlinked_repository_input_is_rejected(repo, tmp_path):
    external = tmp_path / "private.py"
    external.write_text("unreadable-external")
    os.link(external, repo / "link.py")
    with pytest.raises(ValueError, match="unaliased"):
        init(repo)
    assert external.read_text() == "unreadable-external"
    assert not (repo / ".agent/project.json").exists()


def test_linked_control_directory_cannot_write_outside_repo(repo, tmp_path):
    external = tmp_path / "outside"
    external.mkdir()
    link = repo / ".agent"
    if sys.platform == "win32":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(external)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(external, target_is_directory=True)
    try:
        with pytest.raises((ValueError, OSError)):
            init(repo)
        assert not list(external.iterdir())
    finally:
        assert link.parent == repo
        if sys.platform == "win32":
            assert link.is_junction()
            os.rmdir(link)
        else:
            link.unlink()


def test_concurrent_initializers_are_rejected(repo):
    with InitializationRuntime(repo, ScopePolicy()) as runtime:
        with pytest.raises(ValueError, match="lock exists"):
            init(repo)
        assert (repo / ".agent/init.lock").read_text() == runtime.session_id
    assert not (repo / ".agent/init.lock").exists()


@pytest.mark.parametrize("event_kind", ["tool_requested", "tool_finished"])
def test_publication_journal_failure_stops_writes_or_preserves_unresolved(
    repo, monkeypatch, event_kind
):
    original = JsonlJournal.write

    def fail(journal, event):
        # First operation is inspection, second is publication.
        if event.sequence in (3, 4) and event.kind == event_kind:
            raise EventWriteError("test disk failure")
        return original(journal, event)

    monkeypatch.setattr(JsonlJournal, "write", fail)
    with pytest.raises(EventWriteError):
        init(repo)
    journal = next((repo / ".agent").glob("init-*/events.jsonl"))
    view = inspect_journal(journal)
    if event_kind == "tool_requested":
        assert not (repo / ".agent/project.md").exists()
        assert not view.unresolved
    else:
        assert (repo / ".agent/project.json").exists()
        assert view.unresolved == (view.events[-1].event_id,)
        assert (repo / ".agent/init.lock").exists()
        with pytest.raises(ValueError, match="lock exists"):
            init(repo, refresh=True)


def test_interrupted_two_file_publish_is_detected_and_keeps_previous_snapshot(repo, monkeypatch):
    first = init(repo)
    index = (repo / ".agent/project.json").read_bytes()
    original = InitializationRuntime._replace

    def fail(runtime, name, data, expected):
        if name == "project.json":
            raise OSError("metadata write unavailable")
        return original(runtime, name, data, expected)

    monkeypatch.setattr(InitializationRuntime, "_replace", fail)
    (repo / "store.py").write_text("def load_updated():\n    return 'updated'\n")
    with pytest.raises(OSError, match="metadata write"):
        init(repo, refresh=True)
    assert (repo / ".agent/project.json").read_bytes() == index
    assert snapshot(first).previous_revision is None
    monkeypatch.setattr(InitializationRuntime, "_replace", original)
    with pytest.raises(ValueError, match="generated guide"):
        init(repo, refresh=True)


def test_model_calls_are_recorded_and_only_sourced_additions_are_saved(repo):
    source = SourceFile(
        path="service.py",
        sha256=digest((repo / "service.py").read_bytes()),
        role="code",
        text=(repo / "service.py").read_text(),
    )
    entry = KnowledgeEntry(
        authority="fact",
        area="coupling",
        text="The service delegates loading to store.",
        sources=(reference(source, 4),),
    )
    provider = FakeModelProvider([response(RepoSummary(entries=(entry,)))])
    result = init(repo, provider=provider)
    saved = snapshot(result)
    assert saved.mode == "model" and entry in saved.summary.entries
    assert all(not call[1] for call in provider.calls)
    view = inspect_journal(Path(result.journal))
    assert [e.kind for e in view.events] == [
        "tool_requested",
        "tool_finished",
        "model_requested",
        "model_finished",
        "tool_requested",
        "tool_finished",
    ]
    assert all(isinstance(e.revision, InitializationRevision) for e in view.events)
    assert not view.unresolved and len(provider.calls) == 1
    reused = init(repo, provider=FakeModelProvider([]))
    assert reused.status == "reused" and reused.revision == result.revision


@pytest.mark.parametrize("bad", ["unknown_source", "wrong_quote", "invented_rule", "tool"])
def test_untrusted_model_output_cannot_publish_or_request_tools(repo, bad):
    ref = SourceReference(path="service.py", line=1, end_line=1, quote="import store")
    if bad == "unknown_source":
        ref = ref.model_copy(update={"path": "never-read.py"})
    elif bad == "wrong_quote":
        ref = ref.model_copy(update={"quote": "import other"})
    entry = KnowledgeEntry(
        authority="rule" if bad == "invented_rule" else "fact",
        area="development",
        text="Use direct database access.",
        sources=(ref,),
    )
    reply = response(RepoSummary(entries=(entry,)))
    if bad == "tool":
        reply = ModelResponse(
            response_id="bad",
            model="fake",
            message=Message(
                role="assistant",
                tool_calls=(ToolCall(call_id="x", name="shell", arguments_json="{}"),),
            ),
        )
    with pytest.raises((ValueError, RuntimeError)):
        init(repo, provider=FakeModelProvider([reply]))
    assert not (repo / ".agent/project.json").exists()
    assert not (repo / ".agent/project.md").exists()


def test_required_model_question_blocks_with_actionable_guide(repo):
    question = OpenQuestion(
        text="Confirm whether inventory writes must go through Store.", required=True
    )
    result = init(repo, provider=FakeModelProvider([response(RepoSummary(questions=(question,)))]))
    assert result.status == "blocked" and result.questions[-1] == question
    assert "REQUIRED" in Path(result.guide).read_text()
    assert init(repo).status == "blocked"


def test_sources_changed_during_model_work_are_not_published(repo):
    class Changing(FakeModelProvider):
        async def generate(self, *args, **kwargs):
            (repo / "service.py").write_text("user changes during exploration")
            return await super().generate(*args, **kwargs)

    with pytest.raises(ValueError, match="sources changed"):
        init(repo, provider=Changing([response(RepoSummary())]))
    assert (repo / "service.py").read_text() == "user changes during exploration"
    assert not (repo / ".agent/project.json").exists()


def test_user_edit_during_model_work_is_not_overwritten(repo):
    initial = init(repo)
    guide = Path(initial.guide)
    (repo / "store.py").write_text("def load(): return 2")

    class Changing(FakeModelProvider):
        async def generate(self, *args, **kwargs):
            with guide.open("a") as stream:
                stream.write("\nUser appended a rule while exploration was running.\n")
            return await super().generate(*args, **kwargs)

    with pytest.raises(ValueError, match="guidance changed"):
        init(repo, refresh=True, provider=Changing([response(RepoSummary())]))
    assert guide.read_text().endswith("User appended a rule while exploration was running.\n")


def test_unaffected_model_entries_are_reused_on_refresh(repo):
    source = SourceFile(
        path="service.py",
        sha256=digest((repo / "service.py").read_bytes()),
        role="code",
        text=(repo / "service.py").read_text(),
    )
    entry = KnowledgeEntry(
        authority="assumption",
        area="modules",
        text="The service may be the public entry point.",
        sources=(reference(source, 3),),
    )
    first = init(repo, provider=FakeModelProvider([response(RepoSummary(entries=(entry,)))]))
    (repo / "store.py").write_text("def load(): return 'new'")
    new = init(repo, refresh=True, provider=FakeModelProvider([response(RepoSummary())]))
    assert entry in snapshot(new).summary.entries and new.revision != first.revision
    (repo / "service.py").write_text("def renamed(): pass")
    latest = init(repo, refresh=True, provider=FakeModelProvider([response(RepoSummary())]))
    assert entry not in snapshot(latest).summary.entries


def test_known_secrets_are_excluded_from_saved_sources_and_model_context(repo):
    secret = "sensitive-example-credential"
    (repo / "README.md").write_text(f"Example value: {secret}")
    provider = FakeModelProvider([response(RepoSummary())])
    result = init(repo, provider=provider, secrets=(secret,))
    for path in (repo / ".agent").rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
    assert secret not in repr(provider.calls)
    assert snapshot(result).repository.sources


def test_cli_init_refresh_and_noninteractive_staleness(repo, capsys):
    assert main(["init", str(repo), "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "initialized"
    (repo / "store.py").write_text("def load(): return 9")
    assert main(["init", str(repo)]) == 2
    assert "--refresh" in capsys.readouterr().out
    assert (
        main(["init", str(repo), "--refresh", "--rule", "Keep API compatibility.", "--json"]) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["revision"] != first["revision"]
    assert "Keep API compatibility." in Path(result["guide"]).read_text()


def test_cli_missing_model_credentials_does_not_initialize(repo, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        main(["init", str(repo), "--model", "configured-model"])
    assert error.value.code == 2 and "configuration" in capsys.readouterr().err
    assert not (repo / ".agent").exists()


def test_knowledge_metadata_cannot_be_used_as_a_workflow_revision(repo):
    result = init(repo)
    event = inspect_journal(Path(result.journal)).events[0]
    with pytest.raises(ValueError, match="workflow outcomes"):
        WorkflowEvent.model_validate(
            {
                **event.model_dump(),
                "kind": "session_finished",
                "tool_request": None,
            }
        )


def test_initialization_capability_is_not_an_agent_tool(repo, make_task):
    from coding_agent.core.provider import runtime_tools
    from coding_agent.core.tool_policy import PolicyEngine

    assert "initialization" not in {tool.name for tool in runtime_tools()}
    decision = PolicyEngine().evaluate(
        make_task("t"),
        ToolRequest(
            request_id="r",
            invocation=InitializationOperation(
                operation="publish",
                input_sha256="0" * 64,
            ),
        ),
        plan_authorized=True,
        process_denial=None,
        workspace_controller=True,
        operation_approved=True,
    )
    assert decision.decision is Decision.DENY


def test_discovered_commands_cannot_report_passed():
    with pytest.raises(ValueError):
        ValidationCommand.model_validate(
            dict(
                purpose="test",
                argv=["python", "-m", "pytest"],
                status="passed",
                sources=[dict(path="README.md", line=1, end_line=1, quote="python -m pytest")],
            )
        )


def test_real_sdk_serializes_explorer_schema_without_execution_tools(repo):
    import httpx2
    from test_provider import output_text, provider, wire

    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload.get("tools", []) == []
        schema = payload["text"]["format"]
        assert schema["name"] == "RepoSummary" and schema["strict"] is True
        return httpx2.Response(200, json=wire(output_text(RepoSummary().model_dump_json())))

    async def run():
        async with provider(handler) as model:
            return await initialize(repo, provider=model)

    result = asyncio.run(run())
    assert result.status == "initialized" and snapshot(result).mode == "model"
    assert len(requests) == 1


def test_nested_instructions_keep_source_scope_and_all_user_rules(repo):
    nested = repo / "subsystem"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("Only this subsystem may use direct storage.")
    (nested / "service.py").write_text("def local(): pass")
    result = init(repo, focus=("subsystem/service.py",))
    guide = Path(result.guide).read_text()
    assert "Instruction scope: subsystem" in guide
    assert "Instruction scope: ." in guide
    assert "Keep storage access" in guide and "Only this subsystem" in guide


def test_model_cannot_turn_legacy_code_into_a_project_rule(repo):
    entry = KnowledgeEntry(
        authority="rule",
        area="development",
        text="import store",
        sources=(SourceReference(path="service.py", line=1, end_line=1, quote="import store"),),
    )
    with pytest.raises(ValueError, match="explicit rules"):
        init(repo, provider=FakeModelProvider([response(RepoSummary(entries=(entry,)))]))
    assert not (repo / ".agent/project.md").exists()


@pytest.mark.parametrize("limit", ["MAX_ENTRIES", "MAX_SOURCES"])
def test_inspection_budget_is_enforced_without_publishing(repo, monkeypatch, limit):
    from coding_agent.tools import initialization

    monkeypatch.setattr(initialization, limit, 1)
    with pytest.raises(ValueError, match="exceeds"):
        init(repo, focus=("service.py",))
    assert not (repo / ".agent/project.json").exists()


def test_generated_source_excerpts_are_bounded_and_do_not_hide_truncation(repo):
    (repo / "store.py").write_text("def load():\n    pass\n" + "# comment\n" * 5000)
    result = init(repo)
    source = next(s for s in snapshot(result).repository.sources if s.path == "store.py")
    assert source.truncated and len(source.text.encode()) <= 32768
    assert source.sha256 == digest((repo / "store.py").read_bytes())
    assert "Source excerpt truncated: store.py" in snapshot(result).summary.gaps


def test_oversized_instruction_does_not_replace_existing_rules(repo):
    first = init(repo)
    guide = Path(first.guide).read_bytes()
    (repo / "AGENTS.md").write_text("Keep all rules.\n\n" * 4000)
    with pytest.raises(ValueError, match="instruction"):
        init(repo, refresh=True)
    assert Path(first.guide).read_bytes() == guide


def test_foreign_controller_metadata_and_snapshot_tampering_fail_closed(repo):
    first = init(repo)
    payload = repo / ".agent" / f"knowledge-{first.revision}.json"
    payload.write_bytes(payload.read_bytes() + b" ")
    with pytest.raises(ValueError, match="snapshot changed"):
        init(repo, refresh=True)
    assert (repo / ".agent/project.json").exists()


def test_user_guidance_metadata_is_checked_but_manual_rule_edits_are_refreshable(repo):
    first = init(repo, rules=("Keep public behavior.",))
    metadata = Path(first.guide).with_suffix(".json")
    original = metadata.read_bytes()
    changed = json.loads(original)
    changed["user_sha256"] = "a" * 64
    metadata.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="metadata changed"):
        init(repo)
    metadata.write_bytes(original)
    guide = Path(first.guide)
    guide.write_text(
        guide.read_text().replace("Keep public behavior.", "Preserve public behavior.", 1)
    )
    assert init(repo).status == "stale"
    assert init(repo, refresh=True).status == "refreshed"


def test_refresh_does_not_execute_declared_or_model_commands(repo, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("discovery attempted to execute a command")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    (repo / "README.md").write_text("python -m pytest --bad-arg\n")
    result = init(repo)
    assert any("--bad-arg" in c.argv for c in snapshot(result).summary.commands)


def test_cancelled_model_leaves_no_guide_and_a_correlated_interruption(repo):
    class Cancelled(FakeModelProvider):
        async def generate(self, *args, **kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        init(repo, provider=Cancelled([]))
    journal = next((repo / ".agent").glob("init-*/events.jsonl"))
    view = inspect_journal(journal)
    assert not view.unresolved and view.events[-1].model_result.status == "interrupted"
    assert not (repo / ".agent/project.json").exists()


def test_empty_repository_records_unknowns_instead_of_claiming_validation(tmp_path):
    result = init(tmp_path)
    saved = snapshot(result)
    assert saved.repository.paths == () and saved.summary.commands == ()
    assert saved.summary.questions
    assert "have not been executed" in Path(result.guide).read_text()


def test_cli_module_entry_point_in_path_with_spaces(repo):
    completed = subprocess.run(
        [sys.executable, "-m", "coding_agent", "init", str(repo), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "initialized"


def test_nested_manifest_command_uses_its_actual_directory(repo):
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"scripts":{"test":"vitest run"}}')
    result = init(repo)
    command = next(c for c in snapshot(result).summary.commands if c.argv[0] == "npm")
    assert command.cwd == "frontend" and command.sources[0].path == "frontend/package.json"


def test_json_credentials_in_source_are_redacted_before_model_and_persistence(repo):
    (repo / "package.json").write_text('{"api_key":"unconfigured secret with spaces"}')
    provider = FakeModelProvider([response(RepoSummary())])
    result = init(repo, provider=provider)
    for path in (repo / ".agent").rglob("*"):
        if path.is_file():
            assert b"unconfigured secret with spaces" not in path.read_bytes()
    assert "unconfigured secret with spaces" not in repr(provider.calls)
    source = next(s for s in snapshot(result).repository.sources if s.path == "package.json")
    assert json.loads(source.text)["api_key"] == "[REDACTED]"


def test_rule_substrings_are_not_mistaken_for_existing_rules(repo):
    first = init(repo, rules=("Preserve public behavior and compatibility.",))
    result = init(repo, rules=("Preserve public behavior",))
    assert result.revision != first.revision
    assert "\n\nPreserve public behavior\n\n" in Path(result.guide).read_text()


def test_refresh_retains_model_knowledge_and_required_questions_until_user_reassessment(repo):
    (repo / "README.md").write_text("uv run pytest\n")
    command = ValidationCommand(
        purpose="test",
        argv=("uv", "run", "pytest"),
        sources=(SourceReference(path="README.md", line=1, end_line=1, quote="uv run pytest"),),
    )
    entry = KnowledgeEntry(
        authority="assumption",
        area="modules",
        text="The service may be the entry point.",
        sources=(SourceReference(path="service.py", line=1, end_line=1, quote="import store"),),
    )
    question = OpenQuestion(text="Confirm the public entry point.", required=True)
    init(
        repo,
        provider=FakeModelProvider(
            [response(RepoSummary(entries=(entry,), commands=(command,), questions=(question,)))]
        ),
    )
    (repo / "store.py").write_text("def load(): return 2")
    offline = init(repo, refresh=True)
    assert offline.status == "blocked"
    assert entry in snapshot(offline).summary.entries and question in offline.questions
    assert command in snapshot(offline).summary.commands
    model = FakeModelProvider([response(RepoSummary())])
    changed = init(repo, refresh=True, provider=model)
    assert changed.status == "blocked" and entry in snapshot(changed).summary.entries
    assert "prior_questions" in model.calls[0][0][-1].content
    still_blocked = init(repo, rules=("The service is the public entry point.",))
    assert still_blocked.status == "blocked"
    source = next(s for s in snapshot(still_blocked).repository.sources if s.role == "user")
    line = next(
        i
        for i, text in enumerate(source.text.splitlines(), 1)
        if text == "The service is the public entry point."
    )
    answered = OpenQuestion(
        text=question.text,
        required=True,
        answer="The service is the public entry point.",
        sources=(reference(source, line),),
    )
    resolved = init(
        repo,
        refresh=True,
        provider=FakeModelProvider([response(RepoSummary(questions=(answered,)))]),
    )
    assert resolved.status == "refreshed"
    assert answered in resolved.questions and not answered.blocking
    guide = Path(resolved.guide)
    guide.write_text(
        guide.read_text().replace(
            "The service is the public entry point.", "Use a different entry point.", 1
        )
    )
    invalidated = init(repo, refresh=True)
    assert invalidated.status == "blocked"
    assert any(q.text == question.text and q.answer is None for q in invalidated.questions)


def test_model_cannot_answer_a_required_question_from_code(repo):
    question = OpenQuestion(
        text="Confirm whether import store is required.",
        required=True,
        answer="import store",
        sources=(SourceReference(path="service.py", line=1, end_line=1, quote="import store"),),
    )
    with pytest.raises(ValueError, match="answers must quote"):
        init(repo, provider=FakeModelProvider([response(RepoSummary(questions=(question,)))]))
    assert not (repo / ".agent/project.json").exists()


def test_model_cannot_silently_downgrade_a_required_question(repo):
    question = OpenQuestion(text="Confirm the public entry point.", required=True)
    init(repo, provider=FakeModelProvider([response(RepoSummary(questions=(question,)))]))
    result = init(
        repo,
        rules=("Keep APIs stable.",),
        provider=FakeModelProvider(
            [response(RepoSummary(questions=(question.model_copy(update={"required": False}),)))]
        ),
    )
    assert result.status == "blocked" and result.questions[-1].required
