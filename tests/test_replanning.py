import asyncio
from pathlib import Path

import pytest
from test_execution import patch
from test_execution import planned as planned
from test_execution import scripted as scripted
from test_planning import project as project
from test_planning import run as plan_project
from test_reviewer import Reviewer
from test_verification import simulated as simulated
from test_workflow import IMPLEMENTED, REVISION, Harness, checks

from coding_agent.application import execution
from coding_agent.application.evidence import inspect_evidence
from coding_agent.core.models import EvidenceStatus
from coding_agent.core.planning import PlanDraft, PlanSettings
from coding_agent.core.provider import Message, ModelResponse
from coding_agent.core.replanning import ReplanRecord, validate_replan
from coding_agent.core.workflow import CoderResult, RunSpec, WorkflowEngine
from coding_agent.runtime.process import ProcessOutcome
from coding_agent.session.records import JsonlJournal, inspect_journal
from coding_agent.testing import FakeCoder, FakeModelProvider, FakeVerifier
from coding_agent.tools.execution import read_artifact


def revised(draft, name="refined"):
    data = draft.model_dump(mode="json")
    data["tasks"][0]["task"]["id"] = name
    data["milestones"][0]["task_ids"] = [name]
    data["assessment"]["complexity"] = "medium"
    data["assessment"]["execution_strategy"] = "task_graph"
    data["assessment"]["reasons"] = ["Observed verification difficulty requires refinement"]
    return PlanDraft.model_validate(data)


def reply(draft):
    return ModelResponse(
        response_id="plan",
        model="fake",
        message=Message(role="assistant", content=draft.model_dump_json()),
    )


def run(planned, simulated, planner, reviewer=None, approve=None):
    root, _, _, config, workspace = planned
    return asyncio.run(
        execution.run(
            root,
            config=config,
            workspace=workspace,
            verification=simulated[0],
            replanner=planner,
            reviewer=reviewer,
            approve=approve,
        )
    )


@pytest.mark.parametrize(
    "planned", [PlanSettings(max_tool_calls=150, max_model_calls=40)], indirect=True
)
def test_live_replan_retains_work_history_and_requires_fresh_evidence(planned, scripted, simulated):
    planner = FakeModelProvider([reply(revised(planned[2].draft))])
    reviewer = Reviewer()

    async def implement(coder):
        if len(scripted[1]) == 1:
            assert (await patch(coder.runtime)).status == "succeeded"
        # The first plan exhausts its three attempts on a definite failure.
        simulated[2][0] = ProcessOutcome(
            1 if coder.runtime.spec.revision.plan_version == 1 else 0,
            "1 failed" if coder.runtime.spec.revision.plan_version == 1 else "1 passed",
            False,
            False,
        )
        return IMPLEMENTED

    scripted[0][0] = implement
    preview = run(planned, simulated, planner, reviewer)
    assert not planner.calls and not reviewer.calls
    outcome = run(planned, simulated, planner, reviewer, preview.fingerprint)
    assert outcome.status == "tasks_verified", outcome.reason
    assert outcome.usage.attempts == 4 and outcome.usage.replans == 1
    assert len(planner.calls) == 1 and len(reviewer.calls) == 3
    assert not outcome.requirement_complete
    assert (planned[0] / "service.py").read_text() == "def run(): return 1\n"
    assert (Path(outcome.worktree) / "service.py").read_text() == "def run(): return 2\n"
    events = inspect_journal(Path(outcome.journal)).events
    registered = [e for e in events if e.kind == "plan_registered"]
    assert len(registered) == 2 and registered[1].source == registered[0].reason
    proposed = next(e for e in events if e.kind == "replan_proposed")
    record = ReplanRecord.model_validate_json(
        read_artifact(Path(outcome.journal).parent, proposed.artifacts[0])
    )
    assert record.previous_revision == planned[2].revision
    assert record.retired_task_ids == ("service",)
    assert record.previous_assessment.complexity == "small"
    assert record.proposed.draft.assessment.complexity == "medium"
    assert record.usage.attempts == 3 and record.usage.replans == 1
    ledger = inspect_evidence(planned[0])
    assert ledger.current_revision.plan_version == 2
    assert all(not c.current for c in ledger.checks if c.record.before.plan_version == 1)
    assert all(c.current for c in ledger.checks if c.record.before.plan_version == 2)
    assert sum(c.record.before.plan_version == 1 for c in ledger.checks) == 3
    assert not inspect_journal(Path(outcome.journal)).unresolved


@pytest.mark.parametrize(
    "limit,model_limit,expected_calls,expected_attempts",
    [(0, 31, 0, 1), (1, 31, 1, 2), (4, 1, 1, 2)],
)
def test_replan_cap_does_not_reset_with_task_ids(
    project, tmp_path, monkeypatch, simulated, limit, model_limit, expected_calls, expected_attempts
):
    import sys

    from coding_agent.application.planning import plan
    from coding_agent.executors.codex import CodexSettings

    root, _, draft = project
    saved = asyncio.run(
        plan(
            root,
            draft=draft,
            settings=PlanSettings(
                max_replans=limit, max_tool_calls=100, max_model_calls=model_limit
            ),
        )
    ).plan
    config = CodexSettings(
        executable=Path(sys.executable).resolve(), home=tmp_path / "profile", model="fake"
    )
    prepared = root, project[1], saved, config, tmp_path / "workspace"
    planner = FakeModelProvider([reply(revised(draft))])

    class Coder:
        def __init__(self, *args, **kwargs):
            pass

        async def implement(self, *args, **kwargs):
            return CoderResult(
                outcome="replan_required",
                summary="Refine task",
                replan=dict(
                    trigger="verification",
                    reason="More context needed",
                    proposed_complexity="medium",
                    needed_changes="Refine current task",
                ),
            )

    monkeypatch.setattr(execution, "CodexCoder", Coder)
    outcome = run(
        prepared, simulated, planner, approve=run(prepared, simulated, planner).fingerprint
    )
    assert outcome.status == "replan_required" and "budget exhausted" in outcome.reason
    assert len(planner.calls) == expected_calls
    assert outcome.usage.attempts == expected_attempts
    assert outcome.usage.replans == expected_calls


@pytest.mark.parametrize(
    "mutation", ["scope", "permissions", "acceptance", "rules", "risk", "attempts"]
)
def test_replan_cannot_weaken_prior_authority(project, mutation):
    saved = plan_project(project).plan
    data = revised(saved.draft).model_dump(mode="json")
    task = data["tasks"][0]["task"]
    if mutation == "scope":
        task["scope"]["allowed"] = ["other.py"]
    elif mutation == "permissions":
        task["permissions"]["network"] = True
    elif mutation == "acceptance":
        task["acceptance"]["checks"][0]["command"] = ["python", "-c", "pass"]
    elif mutation == "rules":
        data["tasks"][0]["rule_ids"] = []
    elif mutation == "risk":
        task["risk"] = {"level": "low"}
    else:
        task["max_attempts"] += 1
    with pytest.raises(ValueError):
        validate_replan(PlanDraft.model_validate(data), saved, project[1])


@pytest.mark.parametrize(
    "status,category",
    [
        (EvidenceStatus.UNAVAILABLE, "environment"),
        (EvidenceStatus.INCONCLUSIVE, "unknown"),
        (EvidenceStatus.FAILED, "pre_existing"),
    ],
)
def test_failure_diagnosis_blocks_unsupported_repairs(make_task, status, category):
    task = make_task("t")
    harness = Harness(RunSpec(session_id="s", graph={"tasks": (task,)}, revision=REVISION))
    failed = checks(task, status=status)
    harness.verifier = FakeVerifier([failed])
    engine = WorkflowEngine(
        harness.spec,
        coder=harness.coder,
        verifier=harness.verifier,
        reviewer=harness.reviewer,
        read_revision=lambda: REVISION,
        writer=harness.writer,
        baseline_evidence=failed.evidence if category == "pre_existing" else (),
    )
    result = asyncio.run(engine.run(harness.approval))
    assert result.outcome == "blocked" and len(harness.coder.calls) == 1
    assert result.diagnoses[0].category == category
    assert not result.diagnoses[0].repair_allowed


def test_new_task_does_not_reset_session_attempts(make_task):
    first = make_task("first")
    harness = Harness(
        RunSpec(session_id="s", graph={"tasks": (first,)}, revision=REVISION, max_total_attempts=1)
    )
    harness.run()
    second = make_task("replacement")
    next_spec = RunSpec.model_validate(harness.spec.model_dump() | {"graph": {"tasks": (second,)}})
    coder = FakeCoder([IMPLEMENTED])
    engine = WorkflowEngine(
        next_spec,
        coder=coder,
        verifier=harness.verifier,
        reviewer=harness.reviewer,
        read_revision=lambda: REVISION,
        writer=harness.writer,
        history=harness.writer.events,
    )
    harness.spec = next_spec
    result = asyncio.run(engine.run(harness.approval))
    assert result.outcome == "replan_required" and not coder.calls


def test_journal_revision_requires_predecessor_and_fixed_budgets(tmp_path, make_task):
    spec = RunSpec(session_id="s", graph={"tasks": (make_task("t"),)}, revision=REVISION)
    new = RunSpec.model_validate(
        spec.model_dump() | {"revision": REVISION.model_dump() | {"plan_version": 2}}
    )
    with JsonlJournal(tmp_path / "journal", "s") as journal:
        journal.register_plan(spec)
        with pytest.raises(ValueError, match="silently replace"):
            journal.register_plan(new)
        changed = RunSpec.model_validate(new.model_dump() | {"max_tool_calls": 100})
        with pytest.raises(ValueError, match="budgets"):
            journal.register_plan(changed, previous=spec)
        journal.register_plan(new, previous=spec)
        journal.register_plan(new)
        assert len(inspect_journal(journal.directory / "events.jsonl").events) == 2


@pytest.mark.parametrize("failure", ["expanded_scope", "journal_failure"])
@pytest.mark.parametrize("planned", [PlanSettings(max_tool_calls=100)], indirect=True)
def test_rejected_or_unrecorded_replan_never_dispatches_next_coder(
    planned, scripted, simulated, monkeypatch, failure
):
    from coding_agent.core.workflow import EventWriteError

    draft = revised(planned[2].draft)
    if failure == "expanded_scope":
        data = draft.model_dump(mode="json")
        data["tasks"][0]["task"]["scope"]["allowed"] = ["other.py"]
        draft = PlanDraft.model_validate(data)
    else:
        original = JsonlJournal.write

        def fail(journal, event):
            if event.kind == "replan_proposed":
                journal.invalidate()
                raise EventWriteError("simulated persistence failure")
            original(journal, event)

        monkeypatch.setattr(JsonlJournal, "write", fail)
    planner = FakeModelProvider([reply(draft)])

    async def request(coder):
        assert (await patch(coder.runtime)).status == "succeeded"
        return CoderResult(
            outcome="replan_required",
            summary="Refine task",
            replan=dict(
                trigger="coupling",
                reason="New coupling",
                proposed_complexity="medium",
                needed_changes="Split task",
            ),
        )

    scripted[0][0] = request
    preview = run(planned, simulated, planner)
    if failure == "journal_failure":
        with pytest.raises(EventWriteError):
            run(planned, simulated, planner, approve=preview.fingerprint)
    else:
        outcome = run(planned, simulated, planner, approve=preview.fingerprint)
        assert outcome.status == "replan_required" and "authorization" in outcome.reason
        events = inspect_journal(Path(outcome.journal)).events
        assert any(e.kind == "replan_rejected" for e in events)
        assert len([e for e in events if e.kind == "plan_registered"]) == 1
    assert len(scripted[1]) == 1
    worktrees = list(planned[-1].glob("*/service.py"))
    assert len(worktrees) == 1 and worktrees[0].read_text() == "def run(): return 2\n"
    assert (planned[0] / "service.py").read_text() == "def run(): return 1\n"


def test_split_preserves_pending_scope_and_invariants(project):
    from test_planning import staged

    root, knowledge, original = project
    original = staged(original)
    saved = plan_project((root, knowledge, original)).plan
    data = saved.draft.model_dump(mode="json")
    data["tasks"][0]["task"]["id"] = "first-half"
    data["milestones"][0]["task_ids"] = ["first-half"]
    data["assessment"]["complexity"] = "large"
    data["assessment"]["execution_strategy"] = "staged"
    second = dict(data["tasks"][0])
    second["task"] = dict(second["task"], id="second-half", depends_on=["first-half"])
    data["tasks"].append(second)
    data["milestones"][0]["task_ids"].append("second-half")
    proposed = PlanDraft.model_validate(data)
    assert validate_replan(proposed, saved, knowledge) == ()
    data["baseline_check_ids"] = []
    data["refactor"] = False
    with pytest.raises(ValueError, match="invariants"):
        validate_replan(PlanDraft.model_validate(data), saved, knowledge)


def test_invalid_review_budget_cannot_be_replenished_by_replanning(make_task):
    from test_workflow import reviewed

    from coding_agent.testing import FakeReviewer

    task = make_task("t")
    harness = Harness(
        RunSpec(session_id="s", graph={"tasks": (task,)}, revision=REVISION, max_review_fixes=1)
    )
    harness.coder = FakeCoder([IMPLEMENTED, IMPLEMENTED])
    harness.verifier = FakeVerifier([checks(task, number=1), checks(task, number=2)])
    harness.reviewer = FakeReviewer([reviewed(task, major=("Repair required",)), reviewed(task)])
    first = harness.run()
    assert first.tasks[0].review_fixes == 1
    second_task = make_task("replacement")
    spec = RunSpec.model_validate(harness.spec.model_dump() | {"graph": {"tasks": (second_task,)}})
    harness.spec = spec
    coder = FakeCoder([IMPLEMENTED])
    engine = WorkflowEngine(
        spec,
        coder=coder,
        verifier=FakeVerifier([checks(second_task)]),
        reviewer=FakeReviewer([reviewed(second_task, major=("Repair required",))]),
        read_revision=lambda: REVISION,
        writer=harness.writer,
        history=harness.writer.events,
    )
    result = asyncio.run(engine.run(harness.approval))
    assert result.outcome == "replan_required" and result.reason == "review repair budget exhausted"
    assert len(coder.calls) == 1


def test_replacement_cannot_drop_task_local_acceptance(project):
    root, knowledge, draft = project
    data = draft.model_dump(mode="json")
    local = dict(data["tasks"][0]["task"]["acceptance"]["criteria"][0])
    local["id"] = "local-contract"
    data["tasks"][0]["task"]["acceptance"]["criteria"].append(local)
    saved = plan_project((root, knowledge, PlanDraft.model_validate(data))).plan
    replacement = revised(saved.draft).model_dump(mode="json")
    replacement["tasks"][0]["task"]["acceptance"]["criteria"].pop()
    with pytest.raises(ValueError, match="prior checks and criteria"):
        validate_replan(PlanDraft.model_validate(replacement), saved, knowledge)


def test_replanned_review_repair_counts_before_dispatch(make_task):
    task = make_task("t")
    harness = Harness(
        RunSpec(session_id="s", graph={"tasks": (task,)}, revision=REVISION, max_review_fixes=0)
    )
    engine = WorkflowEngine(
        harness.spec,
        coder=harness.coder,
        verifier=harness.verifier,
        reviewer=harness.reviewer,
        read_revision=lambda: REVISION,
        writer=harness.writer,
        initial_purpose="review_fix",
    )
    result = asyncio.run(engine.run(harness.approval))
    assert result.outcome == "replan_required" and result.reason == "review repair budget exhausted"
    assert not harness.coder.calls
