import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest
from test_codex import codex_server as codex_server
from test_codex import final_item, function_item
from test_planning import project as project

from coding_agent.application import execution
from coding_agent.application.planning import plan
from coding_agent.cli import main
from coding_agent.core.planning import PlanDraft
from coding_agent.core.tools import Patch, ToolRequest
from coding_agent.core.workflow import CoderResult, EventWriteError, RunSpec
from coding_agent.executors.codex import CodexSettings
from coding_agent.session.records import JsonlJournal
from coding_agent.tools.execution import inspect_execution, read_artifact


@pytest.fixture
def planned(project, tmp_path):
    root, knowledge, draft = project
    saved = asyncio.run(plan(root, draft=draft)).plan
    config = CodexSettings(
        executable=Path(sys.executable).resolve(),
        home=tmp_path / "dedicated-profile",
        model="offline",
    )
    return root, knowledge, saved, config, tmp_path / "workspace"


def run(planned, **kwargs):
    root, _, _, config, workspace = planned
    return asyncio.run(execution.run(root, config=config, workspace=workspace, **kwargs))


async def patch(runtime):
    return await runtime.execute(
        ToolRequest(
            request_id="write-service",
            invocation=Patch(
                path="service.py",
                expected_sha256=hashlib.sha256(b"def run(): return 1\n").hexdigest(),
                content="def run(): return 2\n",
            ),
        )
    )


@pytest.fixture
def scripted(monkeypatch):
    calls = []

    async def change(coder):
        assert (await patch(coder.runtime)).status == "succeeded"
        return CoderResult(outcome="implemented", summary="Tests passed; imaginary.py modified")

    actions = [change]

    class Coder:
        def __init__(self, config, runtime, *, context):
            self.runtime, self.context = runtime, context
            calls.append(self)

        async def implement(self, *args, **kwargs):
            return await actions[0](self)

    monkeypatch.setattr(execution, "CodexCoder", Coder)
    return actions, calls


def test_preview_has_no_execution_side_effects_and_binds_configuration(planned):
    root, _, _, config, workspace = planned
    before = set((root / ".agent").rglob("*"))
    preview = run(planned)
    assert preview.status == "approval_required" and preview.fingerprint
    assert preview.spec.max_tool_calls == 30 and preview.spec.max_model_calls == 31
    assert not workspace.exists() and set((root / ".agent").rglob("*")) == before
    other = CodexSettings.model_validate(config.model_dump() | {"model": "other-model"})
    changed = asyncio.run(
        execution.run(
            root,
            config=other,
            workspace=workspace,
            approve=preview.fingerprint,
        )
    )
    assert changed.status == "approval_required" and changed.fingerprint != preview.fingerprint
    changed_path = asyncio.run(
        execution.run(root, config=config, workspace=workspace.with_name("other"))
    )
    assert changed_path.fingerprint != preview.fingerprint
    assert not workspace.exists()


def test_real_patch_stays_in_worktree_and_missing_evidence_blocks_completion(planned, scripted):
    root, knowledge, saved, _, _ = planned
    result = run(planned, approve=run(planned).fingerprint)
    assert result.status == "blocked" and not result.requirement_complete
    assert result.workflow is not None, result
    assert result.workflow.evidence == ()
    assert result.workflow.tasks[0].attempts == 1
    assert result.workflow.tasks[0].state == "BLOCKED"
    assert (root / "service.py").read_text() == "def run(): return 1\n"
    assert (Path(result.worktree) / "service.py").read_text() == "def run(): return 2\n"
    context = scripted[1][0].context
    assert context.plan_revision == saved.revision
    assert context.revision.context_revision == knowledge.revision
    assert {e.id for e in context.entries} >= set(saved.draft.tasks[0].rule_ids)
    assert (
        next(s for s in context.current_sources if s.path == "service.py").text
        == "def run(): return 1\n"
    )
    history = inspect_execution(root)
    assert history.states == {"service": "BLOCKED"} and history.diff_recorded
    assert "+def run(): return 2" in history.diff
    kinds = [e.kind for e in history.records.events]
    assert kinds.index("execution_authorized") < kinds.index("tool_requested")
    record = next(e for e in history.records.events if e.kind == "implementation_recorded")
    actual = json.loads(read_artifact(Path(result.journal).parent, record.artifacts[0]))
    assert actual["modified_files"] == ["service.py"] and actual["executed_commands"] == []
    assert actual["tool_request_ids"] == ["write-service"]
    assert not (root / ".agent/init.lock").exists()


def test_same_plan_cannot_restart_in_a_different_workspace(planned, scripted):
    first = run(planned, approve=run(planned).fingerprint)
    root, knowledge, saved, config, directory = planned
    second = root, knowledge, saved, config, directory.with_name("second-workspace")
    preview = run(second)
    with pytest.raises(FileExistsError):
        run(second, approve=preview.fingerprint)
    assert not second[-1].exists()
    assert Path(first.worktree).exists() and len(scripted[1]) == 1
    assert not (root / ".agent/init.lock").exists()


@pytest.mark.parametrize("changed", ["service.py", "opaque.txt", ".agent/project.md"])
def test_stale_input_refuses_execution_before_worktree_creation(planned, scripted, changed):
    preview = run(planned)
    target = planned[0] / changed
    target.write_text(target.read_text() + "\nexternal edit\n")
    result = run(planned, approve=preview.fingerprint)
    assert result.status == "stale"
    assert not planned[-1].exists() and not scripted[1]


def test_source_changes_during_coding_stop_new_actions(planned, scripted):
    async def external_edit(coder):
        (planned[0] / "opaque.txt").write_text("user changed the source")
        await patch(coder.runtime)
        raise AssertionError("must not reach")

    scripted[0][0] = external_edit
    with pytest.raises(ValueError, match="source workspace changed"):
        run(planned, approve=run(planned).fingerprint)
    history = inspect_execution(planned[0])
    assert history.states["service"] == "BLOCKED"
    assert not any(
        e.tool_request and isinstance(e.tool_request.invocation, Patch)
        for e in history.records.events
    )
    assert (planned[0] / "opaque.txt").read_text() == "user changed the source"


def test_replan_is_structured_and_never_retries_in_the_old_plan(planned, scripted):
    async def replan(coder):
        return CoderResult(
            outcome="replan_required",
            summary="New shared caller discovered",
            replan={
                "trigger": "coupling",
                "reason": "Caller shares mutable service state",
                "proposed_complexity": "large",
                "needed_changes": "Explore the caller and stage migration",
            },
        )

    scripted[0][0] = replan
    result = run(planned, approve=run(planned).fingerprint)
    assert result.status == "replan_required" and len(scripted[1]) == 1
    recorded = next(e for e in inspect_execution(planned[0]).records.events if e.coder_result)
    assert recorded.coder_result.replan.proposed_complexity == "large"
    assert result.workflow.tasks[0].attempts == 1


def test_unstructured_replan_cannot_start_another_attempt(planned, scripted):
    async def replan(coder):
        return CoderResult(outcome="replan_required", summary="Widen everything")

    scripted[0][0] = replan
    result = run(planned, approve=run(planned).fingerprint)
    assert result.status == "blocked" and len(scripted[1]) == 1


@pytest.mark.parametrize("refactor", [True, False])
def test_refactor_requires_baseline_before_any_modification(planned, refactor):
    root, _, saved, _, workspace = planned
    changed = PlanDraft.model_validate(
        saved.draft.model_dump()
        | {
            "refactor": refactor,
            "invariant_criterion_ids": ["behavior"],
            "baseline_check_ids": ["unit"],
        }
    )
    asyncio.run(plan(root, draft=changed, reason="Declare refactoring invariants", new_plan=True))
    result = run(planned)
    assert result.status == "blocked" and "baseline" in result.reason
    assert not workspace.exists()


def test_cancel_retains_real_patch_and_records_interruption(planned, scripted):
    async def scenario():
        edited = asyncio.Event()

        async def wait_after_patch(coder):
            await patch(coder.runtime)
            edited.set()
            await asyncio.Event().wait()

        scripted[0][0] = wait_after_patch
        root, _, _, config, workspace = planned
        preview = await execution.run(root, config=config, workspace=workspace)
        task = asyncio.create_task(
            execution.run(root, config=config, workspace=workspace, approve=preview.fingerprint)
        )
        await asyncio.wait_for(edited.wait(), timeout=20)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    view = inspect_execution(planned[0])
    assert view.states["service"] == "CANCELLED" and not view.records.unresolved
    assert "+def run(): return 2" in view.diff


def test_result_write_failure_retains_lock_and_stops_followup_tools(planned, scripted, monkeypatch):
    original = JsonlJournal.write

    def fail_patch_result(journal, event):
        if event.tool_result and event.tool_result.after_sha256 is not None:
            raise EventWriteError("injected result write failure")
        original(journal, event)

    monkeypatch.setattr(JsonlJournal, "write", fail_patch_result)
    with pytest.raises(EventWriteError):
        run(planned, approve=run(planned).fingerprint)
    history = inspect_execution(planned[0])
    assert history.status == "incomplete" and len(history.records.unresolved) == 1
    assert history.records.events[-1].tool_request.invocation.kind == "patch"
    assert (planned[0] / ".agent/init.lock").exists()
    assert len(list(planned[-1].glob("*/service.py"))) == 1


def test_session_budget_includes_context_reads(planned, scripted, monkeypatch):
    original = execution.execution_spec

    def limited(*args):
        return RunSpec.model_validate(original(*args).model_dump() | {"max_tool_calls": 1})

    monkeypatch.setattr(execution, "execution_spec", limited)
    result = run(planned, approve=run(planned).fingerprint)
    assert result.status == "blocked" and not scripted[1]
    tools = [e.tool_result for e in inspect_execution(planned[0]).records.events if e.tool_result]
    assert any(r.status == "denied" and "budget" in r.reason for r in tools)


def test_native_codex_receives_context_and_actual_patch_is_not_verified(planned, codex_server):
    executable, batches, requests = codex_server
    root, knowledge, saved, config, directory = planned
    config = CodexSettings.model_validate(config.model_dump() | {"executable": executable})
    native = root, knowledge, saved, config, directory
    batches.extend(
        [
            function_item(
                "verified_patch",
                {
                    "path": "service.py",
                    "expected_sha256": hashlib.sha256(b"def run(): return 1\n").hexdigest(),
                    "content": "def run(): return 2\n",
                },
            ),
            final_item(),
        ]
    )
    result = run(native, approve=run(native).fingerprint)
    assert result.status == "blocked"
    assert (Path(result.worktree) / "service.py").read_text() == "def run(): return 2\n"
    assert saved.revision in json.dumps(requests[0]) and "knowledge_sources" in json.dumps(
        requests[0]
    )
    assert saved.draft.tasks[0].rule_ids[0] in json.dumps(requests[0])
    view = inspect_execution(root)
    assert len([e for e in view.records.events if e.model_request]) == 2


def test_session_model_budget_stops_continuation_without_discarding_patch(
    planned, codex_server, monkeypatch
):
    original = execution.execution_spec
    monkeypatch.setattr(
        execution,
        "execution_spec",
        lambda *a: RunSpec.model_validate(
            original(*a).model_dump() | {"max_model_calls": 1},
        ),
    )
    executable, batches, requests = codex_server
    root, knowledge, saved, config, directory = planned
    native = (
        root,
        knowledge,
        saved,
        CodexSettings.model_validate(config.model_dump() | {"executable": executable}),
        directory,
    )
    batches.append(
        function_item(
            "verified_patch",
            {
                "path": "service.py",
                "expected_sha256": hashlib.sha256(b"def run(): return 1\n").hexdigest(),
                "content": "def run(): return 2\n",
            },
        )
    )
    result = run(native, approve=run(native).fingerprint)
    assert result.status == "blocked" and "model budget" in result.reason
    assert len(requests) == 1 and "+def run(): return 2" in inspect_execution(root).diff


def test_cli_preview_run_status_diff_and_history(planned, scripted, capsys):
    root, _, _, config, workspace = planned
    args = [
        "run",
        "--path",
        str(root),
        "--workspace",
        str(workspace),
        "--codex",
        str(config.executable),
        "--codex-home",
        str(config.home),
        "--model",
        config.model,
        "--json",
    ]
    assert main(args) == 2
    preview = json.loads(capsys.readouterr().out)
    assert main([*args, "--approve", preview["fingerprint"]]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    before = {p: p.read_bytes() for p in (root / ".agent").rglob("*") if p.is_file()}
    assert main(["status", "--path", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["states"] == {"service": "BLOCKED"}
    assert main(["diff", "--path", str(root)]) == 0
    assert "+def run(): return 2" in capsys.readouterr().out
    assert main(["history", "--path", str(root)]) == 0
    assert "coder_finished" in capsys.readouterr().out
    assert before == {p: p.read_bytes() for p in (root / ".agent").rglob("*") if p.is_file()}


@pytest.mark.parametrize("session", ["../x", "run-x", "run-" + "a" * 32 + ":stream"])
def test_history_rejects_path_escape(planned, session):
    with pytest.raises(ValueError, match="session ID"):
        inspect_execution(planned[0], session)


def test_history_rejects_modified_diff_artifact(planned, scripted):
    result = run(planned, approve=run(planned).fingerprint)
    view = inspect_execution(planned[0])
    artifact = next(
        a
        for e in reversed(view.records.events)
        if e.tool_result
        for a in e.tool_result.artifacts
        if a.path.startswith("diff-")
    )
    (Path(result.journal).parent / artifact.path).write_text("tampered")
    with pytest.raises(ValueError, match="artifact changed"):
        inspect_execution(planned[0])


def test_first_run_initializes_but_requires_a_saved_plan(tmp_path):
    root = tmp_path / "fresh"
    root.mkdir()
    (root / "service.py").write_text("def run(): return 1\n")
    config = CodexSettings(
        executable=Path(sys.executable).resolve(), home=tmp_path / "home", model="offline"
    )
    result = asyncio.run(execution.run(root, config=config, workspace=tmp_path / "workspace"))
    assert result.status == "blocked" and (root / ".agent/project.json").exists()
    assert not (tmp_path / "workspace").exists()


def test_crlf_snapshot_is_lossless_and_lf_hash_cannot_overwrite_it(planned, scripted):
    root, knowledge, saved, config, workspace = planned
    original = b"def run(): return 1\r\n"
    (root / "service.py").write_bytes(original)
    saved = asyncio.run(plan(root, draft=saved.draft, refresh=True, new_plan=True)).plan
    crlf_plan = root, knowledge, saved, config, workspace

    async def reject_wrong_hash(coder):
        result = await patch(coder.runtime)
        assert result.status == "failed"
        assert result.after_sha256 is None
        return CoderResult(outcome="blocked", summary="Wrong input hash refused")

    scripted[0][0] = reject_wrong_hash
    result = run(crlf_plan, approve=run(crlf_plan).fingerprint)
    assert result.status == "blocked"
    assert (root / "service.py").read_bytes() == original
    assert (Path(result.worktree) / "service.py").read_bytes() == original
    assert inspect_execution(root).diff == ""


def test_changed_worktree_rules_stop_subsequent_tools(planned, scripted):
    root, knowledge, saved, config, workspace = planned
    data = saved.draft.model_dump(mode="json")
    data["tasks"][0]["task"]["scope"]["allowed"].append("AGENTS.md")
    saved = asyncio.run(plan(root, draft=PlanDraft.model_validate(data), new_plan=True)).plan
    rules_plan = root, knowledge, saved, config, workspace
    original_hash = next(f.sha256 for f in saved.workspace.files if f.path == "AGENTS.md")

    async def edit_rules(coder):
        result = await coder.runtime.execute(
            ToolRequest(
                request_id="change-rules",
                invocation=Patch(
                    path="AGENTS.md",
                    expected_sha256=original_hash,
                    content="New rule for a future plan.\n",
                ),
            )
        )
        assert result.status == "succeeded"
        await patch(coder.runtime)
        raise AssertionError("must reconcile new rules")

    scripted[0][0] = edit_rules
    with pytest.raises(ValueError, match="rules changed inside worktree"):
        run(rules_plan, approve=run(rules_plan).fingerprint)
    view = inspect_execution(root)
    patches = [
        e.tool_request.invocation.path
        for e in view.records.events
        if e.tool_request and isinstance(e.tool_request.invocation, Patch)
    ]
    assert patches == ["AGENTS.md"]
    assert view.states["service"] == "BLOCKED"


@pytest.mark.parametrize("target", ["profile", "executable", "workspace"])
def test_execution_paths_cannot_overlap_source(planned, target):
    root, knowledge, saved, config, directory = planned
    if target == "workspace":
        directory = root / "nested-workspace"
    else:
        field = "home" if target == "profile" else "executable"
        config = CodexSettings.model_validate(config.model_dump() | {field: root / "private"})
    with pytest.raises(ValueError, match="outside"):
        run((root, knowledge, saved, config, directory))
    assert not list((root / ".agent").glob("run-*"))


def test_model_budget_survives_new_coder_instances(planned, codex_server, tmp_path):
    from datetime import UTC, datetime

    from coding_agent.core.workflow import PlanApproval
    from coding_agent.executors.codex import CodexCoder
    from coding_agent.tools.runtime import ToolRuntime

    executable, _, requests = codex_server
    root, _, saved, config, directory = planned
    config = CodexSettings.model_validate(config.model_dump() | {"executable": executable})
    spec = RunSpec.model_validate(
        execution.execution_spec(saved, config, directory).model_dump()
        | {
            "max_model_calls": 1,
        }
    )
    approval = PlanApproval(
        session_id=spec.session_id,
        plan_fingerprint=spec.fingerprint,
        source="test-controller",
        timestamp=datetime.now(UTC),
    )
    with JsonlJournal(tmp_path / "budget-records", spec.session_id) as journal:
        task = spec.graph.tasks[0]
        runtime = ToolRuntime(
            spec,
            task.id,
            root=root,
            journal=journal,
            approval=approval,
            read_revision=lambda: spec.revision,
        )

        async def attempt(number):
            return await CodexCoder(config, runtime).implement(
                task,
                spec.revision,
                attempt=number,
                purpose="implement",
                feedback=(),
            )

        assert asyncio.run(attempt(1)).outcome == "implemented"
        assert asyncio.run(attempt(2)).outcome == "blocked"
        assert journal.model_calls == 1 and journal.agent_tool_calls == 0
    assert len(requests) == 1


def test_context_excerpt_limit_counts_utf8_bytes_without_dropping_rules(planned, scripted):
    root, knowledge, saved, config, workspace = planned
    content = ("def run(): return 1\n#" + "\u754c" * 12000 + "\n").encode("utf-8")
    (root / "service.py").write_bytes(content)
    saved = asyncio.run(plan(root, draft=saved.draft, refresh=True, new_plan=True)).plan
    large_source = root, knowledge, saved, config, workspace

    async def stop(coder):
        return CoderResult(outcome="blocked", summary="Inspect bounded context")

    scripted[0][0] = stop
    result = run(large_source, approve=run(large_source).fingerprint)
    assert result.status == "blocked"
    context = scripted[1][0].context
    source = next(s for s in context.current_sources if s.path == "service.py")
    assert source.text == content[:32768].decode("utf-8", errors="ignore")
    assert len(source.text.encode("utf-8")) <= 32768 and source.truncated
    assert source.sha256 == hashlib.sha256(content).hexdigest()
    assert {e.id for e in context.entries} >= set(saved.draft.tasks[0].rule_ids)
