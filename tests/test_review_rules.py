import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from test_execution import planned as planned
from test_execution import run
from test_execution import scripted as scripted
from test_planning import project as project
from test_tools import tool_harness as tool_harness

from coding_agent.context.reviewer import attach_review_guidance
from coding_agent.core.review import ReviewGuidance, ReviewRulesOperation
from coding_agent.core.workflow import CoderResult, EventWriteError
from coding_agent.runtime import review_rules
from coding_agent.session.records import inspect_journal


def request(paths=("src/main.py",), digest=review_rules.BUNDLE_SHA256):
    return ReviewRulesOperation(paths=paths, bundle_sha256=digest)


def call(h, operation):
    return asyncio.run(h.runtime.execute(h.request(operation)))


def test_pinned_rules_match_upstream_selection():
    oracle = json.loads(
        (Path(__file__).parent / "fixtures/ocr_rule_selection.json").read_text(encoding="utf-8")
    )
    guidance = review_rules.load_guidance(request(tuple(oracle["rule_text_sha256"])))
    assert guidance.bundle_sha256 == oracle["bundle_sha256"]
    assert guidance.source_commit == oracle["source_commit"]
    assert {
        path: hashlib.sha256(group.text.encode()).hexdigest()
        for group in guidance.groups
        for path in group.paths
    } == oracle["rule_text_sha256"]
    assert guidance.authority == "supplemental"


def test_rule_read_is_recorded_and_counts_budget(tool_harness, monkeypatch):
    h = tool_harness
    h.runtime.max_output_bytes = 65536
    original = review_rules._bundle_bytes

    def read():
        view = inspect_journal(h.journal.directory / "events.jsonl")
        assert view.events[-1].kind == "tool_requested"
        assert view.events[-1].tool_request.invocation.kind == "review_rules"
        return original()

    monkeypatch.setattr(review_rules, "_bundle_bytes", read)
    result = call(h, request(("src/main.py", "README.md")))
    assert result.status == "succeeded" and not result.truncated
    guidance = ReviewGuidance.model_validate_json(result.output)
    assert {p for g in guidance.groups for p in g.paths} == {"src/main.py", "README.md"}
    assert h.journal.agent_tool_calls == 1
    assert result.process_argv is None
    view = inspect_journal(h.journal.directory / "events.jsonl")
    assert not view.unresolved
    assert view.events[-1].revision == h.runtime.read_revision()
    assert view.events[-1].artifacts


@pytest.mark.parametrize("case", ["unapproved", "forbidden", "budget", "journal"])
def test_denial_prevents_asset_read(tool_harness, monkeypatch, case):
    h = tool_harness
    operation = request()
    if case == "unapproved":
        h.runtime.approval = None
    elif case == "forbidden":
        operation = request(("secrets/private",))
    elif case == "budget":
        h.journal._agent_tool_calls = h.spec.max_tool_calls
    else:
        h.journal.invalidate()

    def forbidden_read():
        raise AssertionError("denied request must not read assets")

    monkeypatch.setattr(review_rules, "_bundle_bytes", forbidden_read)
    if case == "journal":
        with pytest.raises(EventWriteError):
            call(h, operation)
    else:
        result = call(h, operation)
        assert result.status == "denied" and not result.executed


@pytest.mark.parametrize("case", ["wrong_revision", "corrupt", "missing"])
def test_asset_failure_is_not_success(tool_harness, monkeypatch, case):
    h = tool_harness
    operation = request()
    if case == "wrong_revision":
        operation = request(digest="0" * 64)
    elif case == "corrupt":
        monkeypatch.setattr(review_rules, "_bundle_bytes", lambda: b"{}")
    else:

        def missing():
            raise FileNotFoundError("asset removed")

        monkeypatch.setattr(review_rules, "_bundle_bytes", missing)
    result = call(h, operation)
    assert result.status == "failed" and result.output == ""
    assert not inspect_journal(h.journal.directory / "events.jsonl").unresolved


@pytest.mark.parametrize("path", ["../secret", ".Git/config", "C:/secret", "a\\b", "."])
def test_invalid_paths_rejected(path):
    with pytest.raises(ValueError):
        request((path,))


@pytest.mark.parametrize("case", ["valid", "stale", "wrong_bundle", "truncated", "changed_during"])
def test_context_preserves_project_rules_and_fails_closed(planned, scripted, case, monkeypatch):
    async def inspect(coder):
        runtime, context = coder.runtime, coder.context
        original_entries = context.entries
        digest = review_rules.BUNDLE_SHA256
        if case == "stale":
            context = context.model_copy(update={"task_id": "other"})
        elif case == "wrong_bundle":
            digest = "0" * 64
        elif case == "truncated":
            runtime.max_output_bytes = 100
        elif case == "changed_during":
            original = runtime.execute

            async def changing(operation):
                result = await original(operation)
                (runtime.root / "service.py").write_text("changed during read\n")
                return result

            monkeypatch.setattr(runtime, "execute", changing)
        if case != "valid":
            with pytest.raises(ValueError):
                await attach_review_guidance(
                    runtime, context, ("service.py",), bundle_sha256=digest
                )
        else:
            attached = await attach_review_guidance(runtime, context, ("service.py",))
            assert attached.project.entries == original_entries
            assert any(e.entry.authority == "rule" for e in original_entries)
            assert attached.guidance.authority == "supplemental"
            assert attached.request_event_id
            assert len(attached.revision) == 64
            assert attached.guidance.groups[0].paths == ("service.py",)
            assert "override" in attached.precedence
        return CoderResult(outcome="blocked", summary="context probe only")

    scripted[0][0] = inspect
    result = run(planned, approve=run(planned).fingerprint)
    assert result.status == "blocked" and not result.requirement_complete
