import asyncio
import json
import sys
from pathlib import Path

import pytest
from test_execution import planned as planned
from test_execution import scripted as scripted
from test_planning import project as project
from test_verification import simulated as simulated

from coding_agent.agents.reviewer import DIMENSIONS, ReviewDraft
from coding_agent.application import execution
from coding_agent.cli import main
from coding_agent.core.provider import (
    GenerationSettings,
    Message,
    ModelFailure,
    ModelResponse,
    ProviderError,
)
from coding_agent.core.workflow import EventWriteError
from coding_agent.session.records import JsonlJournal, inspect_journal
from coding_agent.tools.execution import read_artifact


class Reviewer:
    name = "test-reviewer"
    settings = GenerationSettings(model="independent-review")
    endpoint_sha256 = "a" * 64

    def __init__(self, action=None):
        self.calls = []
        self.action = action

    async def generate(self, messages, *, tools=(), response_schema=None):
        assert tools == () and response_schema is ReviewDraft
        assert len(messages) == 2
        context = json.loads(messages[1].content)
        self.calls.append(context)
        draft = dict(
            conclusion="passed",
            summary="Reviewed current inputs",
            blocking=[],
            major=[],
            minor=[],
            reviewed_paths=context["required_paths"],
            assessed_rule_ids=context["applicable_rule_ids"],
            checked_dimensions=list(DIMENSIONS),
            documentation_assessment="No documentation update required for this change.",
        )
        if self.action:
            self.action(self, context, draft)
        return ModelResponse(
            response_id=f"review-{len(self.calls)}",
            model=self.settings.model,
            message=Message(role="assistant", content=json.dumps(draft)),
        )


def run(planned, simulated, reviewer, *, approve=None):
    root, _, _, coder, workspace = planned
    return asyncio.run(
        execution.run(
            root,
            config=coder,
            workspace=workspace,
            reviewer=reviewer,
            verification=simulated[0],
            approve=approve,
        )
    )


def records(outcome):
    path = Path(outcome.journal)
    view = inspect_journal(path)
    return view, [
        json.loads(read_artifact(path.parent, e.artifacts[0]))
        for e in view.events
        if e.kind == "review_recorded"
    ]


def test_review_is_fresh_recorded_and_repeated_at_final_snapshot(planned, scripted, simulated):
    provider = Reviewer()
    preview = run(planned, simulated, provider)
    assert not provider.calls
    outcome = run(planned, simulated, provider, approve=preview.fingerprint)
    assert outcome.status == "tasks_verified", outcome.reason
    assert not outcome.requirement_complete
    assert len(provider.calls) == 3
    assert len(outcome.final_reviews) == 2
    for context in provider.calls:
        assert "+def run(): return 2" in context["diff"]
        assert context["evidence"] and all(e["status"] == "passed" for e in context["evidence"])
        assert context["project"]["guidance"]["authority"] == "supplemental"
        assert context["applicable_rule_ids"]
        assert "Tests passed; imaginary.py modified" not in json.dumps(context)
        assert "documentation_assessment" not in context
    view, reports = records(outcome)
    assert [r["phase"] for r in reports] == ["task", "final", "final"]
    assert all(r["result"]["status"] == "passed" and r["context_sha256"] for r in reports)
    kinds = [e.kind for e in view.events]
    assert kinds.index("review_context_built") < kinds.index("model_requested")
    assert kinds.index("model_finished") < kinds.index("review_recorded")
    assert not view.unresolved
    assert all(e.evidence_type == "review" or e.source for e in outcome.workflow.evidence)


@pytest.mark.parametrize(
    "case", ["coverage", "rules", "dimensions", "inconclusive", "forged", "provider"]
)
def test_invalid_or_uncertain_review_blocks(planned, scripted, simulated, case):
    def action(provider, context, draft):
        if case == "coverage":
            draft["reviewed_paths"] = []
        elif case == "rules":
            draft["assessed_rule_ids"] = []
        elif case == "dimensions":
            draft["checked_dimensions"] = ["requirements"]
        elif case == "inconclusive":
            draft["conclusion"] = "inconclusive"
        elif case == "forged":
            draft["evidence"] = [{"status": "passed"}]
        else:
            raise ProviderError(ModelFailure(code="timeout"))

    provider = Reviewer(action)
    outcome = run(
        planned, simulated, provider, approve=run(planned, simulated, provider).fingerprint
    )
    assert outcome.status == "blocked"
    assert len(provider.calls) == 1
    assert outcome.workflow.reviews[0].status in {"inconclusive", "unavailable"}
    assert not outcome.final_reviews
    assert not inspect_journal(Path(outcome.journal)).unresolved


def test_major_findings_trigger_bounded_repair_and_cannot_pass(planned, scripted, simulated):
    def action(provider, context, draft):
        draft["major"] = ["service.py: response breaks the explicit contract"]

    provider = Reviewer(action)
    outcome = run(
        planned, simulated, provider, approve=run(planned, simulated, provider).fingerprint
    )
    assert outcome.status != "tasks_verified"
    assert outcome.workflow.reviews[0].status == "failed"
    assert outcome.workflow.reviews[0].major
    assert outcome.workflow.tasks[0].attempts > 1
    assert len(provider.calls) < 5


@pytest.mark.parametrize("when", ["during_model", "final"])
def test_changed_snapshot_or_failed_final_review_blocks(planned, scripted, simulated, when):
    def action(provider, context, draft):
        if when == "during_model":
            path = scripted[1][0].runtime.root / "service.py"
            path.write_text("changed during review\n")
        elif len(provider.calls) > 1:
            draft["conclusion"] = "inconclusive"

    provider = Reviewer(action)
    outcome = run(
        planned, simulated, provider, approve=run(planned, simulated, provider).fingerprint
    )
    assert outcome.status != "tasks_verified"
    if when == "during_model":
        assert outcome.workflow.reviews[0].status != "passed"
    else:
        assert outcome.workflow.reviews[0].status == "passed"
        assert outcome.final_reviews and all(r.status != "passed" for r in outcome.final_reviews)


@pytest.mark.parametrize("kind", ["review_context_built", "model_requested", "review_recorded"])
def test_recording_failure_propagates_without_success(
    planned, scripted, simulated, monkeypatch, kind
):
    provider = Reviewer()
    fingerprint = run(planned, simulated, provider).fingerprint
    original = JsonlJournal.write

    def fail(journal, event):
        if event.kind == kind:
            journal.invalidate()
            raise EventWriteError("injected failure")
        original(journal, event)

    monkeypatch.setattr(JsonlJournal, "write", fail)
    with pytest.raises(EventWriteError):
        run(planned, simulated, provider, approve=fingerprint)
    assert len(provider.calls) == (1 if kind == "review_recorded" else 0)


def test_configuration_is_bound_to_run_approval(planned, scripted, simulated):
    provider = Reviewer()
    original = run(planned, simulated, provider).fingerprint
    provider.endpoint_sha256 = "b" * 64
    changed = run(planned, simulated, provider, approve=original)
    assert changed.status == "approval_required" and changed.fingerprint != original
    assert not provider.calls and not scripted[1]


def test_budget_is_shared_with_coder(planned, scripted, simulated):
    original = scripted[0][0]

    async def exhausted(coder):
        result = await original(coder)
        coder.runtime.journal._model_calls = coder.runtime.spec.max_model_calls
        return result

    scripted[0][0] = exhausted
    provider = Reviewer()
    outcome = run(
        planned, simulated, provider, approve=run(planned, simulated, provider).fingerprint
    )
    assert outcome.status == "blocked" and not provider.calls


def test_review_cli_keeps_coder_and_review_models_separate(tmp_path, monkeypatch, capsys):
    config = tmp_path / "review.env"
    config.write_text(
        "CODING_AGENT_PROVIDER=zhipu\n"
        "CODING_AGENT_API_URL=https://open.bigmodel.cn/api/coding/paas/v4/chat/completions\n"
        "CODING_AGENT_MODEL=review-file-model\nCODING_AGENT_API_KEY=synthetic-review-key\n"
    )
    for name in ("PROVIDER", "API_URL", "MODEL", "API_KEY"):
        monkeypatch.delenv("CODING_AGENT_" + name, raising=False)
    captured = []

    async def execute(**kwargs):
        captured.append(kwargs)
        return execution.RunResult(status="approval_required", reason="preview")

    monkeypatch.setattr("coding_agent.cli.execute_plan", execute)
    assert (
        main(
            [
                "run",
                "--path",
                str(tmp_path),
                "--workspace",
                str(tmp_path / "workspace"),
                "--codex",
                sys.executable,
                "--codex-home",
                str(tmp_path / "profile"),
                "--model",
                "coder-model",
                "--review-env-file",
                str(config),
                "--review-model",
                "review-override",
                "--json",
            ]
        )
        == 2
    )
    assert captured[0]["config"].model == "coder-model"
    assert captured[0]["reviewer"].settings.model == "review-override"
    assert "synthetic-review-key" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "case", ["truncated", "deleted", "added", "tool_budget", "provider_changed"]
)
def test_context_boundaries(planned, scripted, simulated, case, monkeypatch):
    original = scripted[0][0]

    async def change(coder):
        result = await original(coder)
        if case == "truncated":
            (coder.runtime.root / "service.py").write_text("x" * 70000)
        elif case == "deleted":
            (coder.runtime.root / "service.py").unlink()
        elif case == "added":
            (coder.runtime.root / "extra.py").write_text("new = True\n")
        elif case == "tool_budget":
            # Leave enough for Verifier's probe and check, then block context reads.
            coder.runtime.journal._agent_tool_calls = coder.runtime.spec.max_tool_calls - 2
        else:
            provider.endpoint_sha256 = "c" * 64
        return result

    scripted[0][0] = change
    provider = Reviewer()
    outcome = run(
        planned, simulated, provider, approve=run(planned, simulated, provider).fingerprint
    )
    if case in {"deleted", "added"}:
        assert provider.calls, outcome.reason
        context = provider.calls[0]
        if case == "deleted":
            assert "service.py" in context["deleted_paths"]
            assert "service.py" in context["required_paths"]
            assert all(s["path"] != "service.py" for s in context["current_sources"])
        else:
            assert "extra.py" in context["required_paths"]
            assert any(s["path"] == "extra.py" for s in context["current_sources"])
    else:
        assert outcome.status == "blocked" and not provider.calls


def test_failed_verification_never_dispatches_review(planned, scripted, simulated):
    from coding_agent.runtime.process import ProcessOutcome

    simulated[2][0] = ProcessOutcome(0, "1 passed, 1 skipped", False, False)
    provider = Reviewer()
    outcome = run(
        planned, simulated, provider, approve=run(planned, simulated, provider).fingerprint
    )
    assert outcome.status == "blocked" and not provider.calls


def test_refactor_review_receives_invariants_and_historical_baseline(planned, scripted, simulated):
    from coding_agent.application.planning import plan
    from coding_agent.core.planning import PlanDraft

    root, knowledge, saved, coder, workspace = planned
    raw = saved.draft.model_dump()
    raw.update(refactor=True, invariant_criterion_ids=("behavior",), baseline_check_ids=("unit",))
    updated = asyncio.run(
        plan(
            root,
            new_plan=True,
            draft=PlanDraft.model_validate(raw),
            reason="Require explicit refactor invariants",
        )
    ).plan
    planned = root, knowledge, updated, coder, workspace
    provider = Reviewer()
    outcome = run(
        planned, simulated, provider, approve=run(planned, simulated, provider).fingerprint
    )
    assert outcome.status == "tasks_verified", outcome.reason
    context = provider.calls[0]
    assert context["refactor"] is True
    assert context["invariant_criterion_ids"] == ["behavior"]
    assert context["baseline_evidence"]
    assert (
        context["baseline_evidence"][0]["workspace_revision"]
        != context["evidence"][0]["workspace_revision"]
    )


def test_cancellation_is_recorded_and_not_converted_to_review_success(planned, scripted, simulated):
    def cancel(provider, context, draft):
        raise asyncio.CancelledError()

    provider = Reviewer(cancel)
    fingerprint = run(planned, simulated, provider).fingerprint
    with pytest.raises(asyncio.CancelledError):
        run(planned, simulated, provider, approve=fingerprint)
    path = planned[0] / ".agent" / ("run-" + planned[2].plan_id) / "events.jsonl"
    view = inspect_journal(path)
    assert any(e.model_result and e.model_result.status == "interrupted" for e in view.events)
    assert not any(e.kind == "review_recorded" for e in view.events)
    assert not view.unresolved
