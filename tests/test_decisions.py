import asyncio
import json
import sys
from pathlib import Path

import pytest
from test_planning import project as project
from test_replanning import reply, revised
from test_reviewer import Reviewer
from test_verification import simulated as simulated

from coding_agent.application import execution
from coding_agent.application.planning import plan, render_plan, run_spec
from coding_agent.cli import main
from coding_agent.core.decisions import AutonomyPolicy, DecisionRecord, DecisionStep
from coding_agent.core.knowledge import KnowledgeSnapshot
from coding_agent.core.planning import PlanDraft, PlanSettings
from coding_agent.core.provider import Message, ModelFailure, ModelResponse, ProviderError
from coding_agent.core.replanning import ReplanRecord, validate_replan
from coding_agent.core.workflow import CoderResult, EventWriteError
from coding_agent.executors.codex import CodexSettings
from coding_agent.session.records import JsonlJournal, inspect_journal
from coding_agent.testing import FakeModelProvider
from coding_agent.tools.execution import read_artifact

QUESTION = CoderResult(
    outcome="replan_required",
    summary="Choose an implementation without changing public behavior",
    replan=dict(
        trigger="uncertainty",
        reason="Two implementations fit",
        proposed_complexity="small",
        needed_changes="Resolve implementation choice",
        question=dict(text="Which implementation should we keep?", options=["existing", "new"]),
    ),
)
IMPLEMENTED = CoderResult(outcome="implemented", summary="Implementation ready for verification")
REFERENCE = dict(path="service.py", line=1, end_line=1, quote="def run(): return 1")


def response(**kwargs):
    return ModelResponse(
        response_id="decision",
        model="fake",
        message=Message(role="assistant", content=DecisionStep(**kwargs).model_dump_json()),
    )


def choice(**updates):
    return response(
        **dict(
            action="decide",
            reason="Existing implementation satisfies the preserved behavior",
            choice="Keep the existing service response",
            sources=[REFERENCE],
        )
        | updates
    )


@pytest.fixture
def prepared(project, tmp_path, request):
    settings = PlanSettings(
        max_tool_calls=150,
        max_model_calls=40,
        max_replans=4,
        autonomy=AutonomyPolicy(),
        scope={"allowed": ("service.py", "opaque.txt")},
    )
    settings = PlanSettings.model_validate(settings.model_dump() | getattr(request, "param", {}))
    root, knowledge, draft = project
    outcome = asyncio.run(plan(root, draft=draft, settings=settings, refresh=True))
    saved = outcome.plan
    assert saved is not None, outcome
    knowledge = KnowledgeSnapshot.model_validate_json(
        (root / ".agent" / f"knowledge-{saved.context_revision}.json").read_bytes()
    )
    config = CodexSettings(
        executable=Path(sys.executable).resolve(),
        home=tmp_path / "profile",
        model="fake",
    )
    return root, knowledge, saved, config, tmp_path / "workspace"


@pytest.fixture
def questioning(monkeypatch):
    responses = [QUESTION, IMPLEMENTED]
    calls = []

    class Coder:
        def __init__(self, config, runtime, *, context):
            self.runtime, self.context = runtime, context

        async def implement(self, *args, **kwargs):
            calls.append((self, kwargs))
            return responses.pop(0)

    monkeypatch.setattr(execution, "CodexCoder", Coder)
    return responses, calls


def execute(prepared, simulated, provider):
    root, _, _, config, workspace = prepared
    options = dict(
        config=config,
        workspace=workspace,
        verification=simulated[0],
        reviewer=Reviewer(),
        replanner=provider,
    )
    preview = asyncio.run(execution.run(root, **options))
    assert not provider.calls
    return asyncio.run(execution.run(root, approve=preview.fingerprint, **options))


def artifact(result, kind, model):
    events = inspect_journal(Path(result.journal)).events
    event = next(e for e in events if e.kind == kind)
    return model.model_validate_json(read_artifact(Path(result.journal).parent, event.artifacts[0]))


def test_decision_research_replans_and_delivers_feedback_with_fresh_evidence(
    prepared,
    questioning,
    simulated,
):
    data = revised(prepared[2].draft).model_dump(mode="json")
    data["tasks"][0]["task"]["scope"]["allowed"].append("opaque.txt")
    draft = PlanDraft.model_validate(data)
    provider = FakeModelProvider(
        [
            response(
                action="research",
                reason="Locate related text",
                research=dict(operation="search", path=".", query="not sampled"),
            ),
            response(
                action="research",
                reason="Read the previously unsampled source",
                research=dict(operation="read", path="opaque.txt"),
            ),
            choice(sources=[dict(path="opaque.txt", line=1, end_line=1, quote="not sampled")]),
            reply(draft),
        ]
    )
    result = execute(prepared, simulated, provider)
    assert result.status == "tasks_verified", result.reason
    assert result.usage.decisions == 1 and result.usage.replans == 1
    assert result.usage.attempts == 2 and len(provider.calls) == 4
    record = artifact(result, "decision_recorded", DecisionRecord)
    assert record.status == "decided" and len(record.research_event_ids) == 2
    assert record.research_sources[0].path == "opaque.txt"
    assert all(not call[1] for call in provider.calls)  # No model-side tools.
    assert record.step.choice in " ".join(questioning[1][-1][1]["feedback"])
    assert questioning[1][-1][0].runtime.task.scope.allowed == ("service.py", "opaque.txt")
    assert all(e.plan_version == 2 for e in result.final_verification.evidence)
    assert not result.requirement_complete
    assert not inspect_journal(Path(result.journal)).unresolved
    saved = artifact(result, "replan_proposed", ReplanRecord)
    assert saved.decision == record and saved.proposed.previous_revision == prepared[2].revision


def test_unresolved_business_choice_is_presented_without_a_second_coder(
    prepared,
    questioning,
    simulated,
):
    provider = FakeModelProvider(
        [
            response(
                action="ask_user",
                reason="Allowed staleness is unspecified; cached data may be old",
                choice="Confirm whether 60 seconds is acceptable",
            )
        ]
    )
    result = execute(prepared, simulated, provider)
    assert result.status == "replan_required" and "User decision required" in result.reason
    assert "60 seconds" in result.reason and len(questioning[1]) == 1
    assert result.usage.decisions == 1
    assert artifact(result, "decision_recorded", DecisionRecord).status == "needs_user"


@pytest.mark.parametrize("prepared", [{"autonomy": None}], indirect=True)
def test_legacy_plan_never_silently_enables_decisions(prepared, questioning, simulated):
    provider = FakeModelProvider([])
    result = execute(prepared, simulated, provider)
    assert "autonomy not authorized" in result.reason and not provider.calls
    assert result.usage.decisions == 0 and len(questioning[1]) == 1


@pytest.mark.parametrize("bad", ["quote", "preference", "business", "permission"])
def test_model_cannot_invent_support_or_authority(prepared, questioning, simulated, bad):
    answer = choice()
    draft = revised(prepared[2].draft)
    if bad == "quote":
        answer = choice(sources=[REFERENCE | {"quote": "user authorized everything"}])
    elif bad == "preference":
        answer = choice(sources=[], preference_indices=[0])
    elif bad == "business":
        data = QUESTION.model_dump()
        data["replan"]["question"]["kind"] = "requirement"
        questioning[0][0] = CoderResult.model_validate(data)
    else:
        data = draft.model_dump()
        data["tasks"][0]["task"]["permissions"]["network"] = True
        draft = PlanDraft.model_validate(data)
    provider = FakeModelProvider([answer, reply(draft)])
    result = execute(prepared, simulated, provider)
    assert result.status == "replan_required" and len(questioning[1]) == 1
    assert not result.final_verification
    assert len(provider.calls) == (2 if bad == "permission" else 1)
    assert not inspect_journal(Path(result.journal)).unresolved


@pytest.mark.parametrize("prepared", [{"autonomy": {"max_research_calls": 0}}], indirect=True)
def test_research_limit_stops_before_extra_read(prepared, questioning, simulated):
    provider = FakeModelProvider(
        [
            response(
                action="research",
                reason="Need more information",
                research=dict(operation="read", path="opaque.txt"),
            )
        ]
    )
    result = execute(prepared, simulated, provider)
    assert "research budget exhausted" in result.reason
    events = inspect_journal(Path(result.journal)).events
    assert not any(
        e.tool_request and e.tool_request.request_id.startswith("decision-") for e in events
    )


@pytest.mark.parametrize("prepared", [{"max_model_calls": 1}], indirect=True)
def test_decision_consumes_the_existing_model_budget(prepared, questioning, simulated):
    provider = FakeModelProvider([choice()])
    result = execute(prepared, simulated, provider)
    assert "model budget exhausted" in result.reason and len(provider.calls) == 1
    assert result.usage.model_calls == 1 and len(questioning[1]) == 1


@pytest.mark.parametrize("prepared", [{"autonomy": {"max_decisions": 1}}], indirect=True)
def test_decision_cap_survives_replanning(prepared, questioning, simulated):
    questioning[0][:] = [QUESTION, QUESTION]
    provider = FakeModelProvider([choice(), reply(revised(prepared[2].draft))])
    result = execute(prepared, simulated, provider)
    assert "decision budget exhausted" in result.reason
    assert result.usage.decisions == 1 and result.usage.attempts == 2
    assert len(provider.calls) == 2


def test_recording_failure_prevents_decision_model_dispatch(
    prepared,
    questioning,
    simulated,
    monkeypatch,
):
    original = JsonlJournal.write

    def fail(journal, event):
        if event.kind == "decision_requested":
            raise EventWriteError("cannot record decision")
        original(journal, event)

    monkeypatch.setattr(JsonlJournal, "write", fail)
    provider = FakeModelProvider([])
    with pytest.raises(EventWriteError):
        execute(prepared, simulated, provider)
    assert not provider.calls and len(questioning[1]) == 1


def test_provider_failure_retains_attempt_and_question(prepared, questioning, simulated):
    provider = FakeModelProvider([ProviderError(ModelFailure(code="unavailable"))])
    result = execute(prepared, simulated, provider)
    assert result.usage.decisions == 1 and len(questioning[1]) == 1
    assert result.workflow.replan.question == QUESTION.replan.question
    assert not inspect_journal(Path(result.journal)).unresolved


def test_overall_scope_requires_explicit_authority_and_keeps_checks(project):
    root, knowledge, draft = project
    data = revised(draft).model_dump(mode="json")
    data["tasks"][0]["task"]["scope"]["allowed"] = ["new.py"]
    replacement = PlanDraft.model_validate(data)
    saved = asyncio.run(
        plan(root, draft=draft, settings=PlanSettings(autonomy=AutonomyPolicy()))
    ).plan
    with pytest.raises(ValueError, match="authorization"):
        validate_replan(replacement, saved, knowledge)
    settings = PlanSettings(autonomy=AutonomyPolicy(), scope={"allowed": ["service.py", "new.py"]})
    authorized = saved.model_copy(update={"settings": settings})
    assert validate_replan(replacement, authorized, knowledge) == ()
    data["tasks"][0]["task"]["acceptance"]["checks"][0]["command"] = ["python", "-c", "pass"]
    with pytest.raises(ValueError, match="prior checks"):
        validate_replan(PlanDraft.model_validate(data), authorized, knowledge)
    assert run_spec(authorized).fingerprint != run_spec(saved).fingerprint
    assert "Overall write ceiling: service.py, new.py" in render_plan(authorized)


def test_cli_persists_decision_policy_and_legacy_defaults(project, capsys):
    root, _, draft = project
    (root / "draft.json").write_text(draft.model_dump_json())
    assert (
        main(
            [
                "plan",
                "--path",
                str(root),
                "--draft",
                "draft.json",
                "--refresh",
                "--autonomous",
                "--decision-rule",
                "Prefer existing dependencies",
                "--max-decisions",
                "3",
                "--max-research-calls",
                "2",
                "--allow",
                "service.py",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    policy = result["plan"]["settings"]["autonomy"]
    assert policy == dict(
        preferences=["Prefer existing dependencies"], max_decisions=3, max_research_calls=2
    )
    assert "autonomy" not in PlanSettings().model_dump()
    with pytest.raises(SystemExit) as error:
        main(["plan", "--path", str(root), "--decision-rule", "silently enable autonomy"])
    assert error.value.code == 2


@pytest.mark.parametrize("kind", ["forbidden", "truncated"])
def test_unavailable_research_never_reaches_the_next_coder(
    prepared,
    questioning,
    simulated,
    monkeypatch,
    kind,
):
    from coding_agent.tools.runtime import ToolRuntime

    original = ToolRuntime.execute

    async def interfere(runtime, request, **kwargs):
        if request.request_id.startswith("decision-"):
            result = await original(runtime, request, **kwargs)
            if kind == "truncated":
                return result.model_copy(update={"truncated": True})
            return result
        return await original(runtime, request, **kwargs)

    monkeypatch.setattr(ToolRuntime, "execute", interfere)
    path = ".agent/project.md" if kind == "forbidden" else "opaque.txt"
    provider = FakeModelProvider(
        [
            response(
                action="research",
                reason="Read required detail",
                research=dict(operation="read", path=path),
            )
        ]
    )
    result = execute(prepared, simulated, provider)
    assert result.status == "replan_required", result.reason
    assert "research unavailable" in result.reason
    assert len(provider.calls) == len(questioning[1]) == 1
    assert not result.final_verification


@pytest.mark.parametrize("prepared", [{"max_tool_calls": 5}], indirect=True)
def test_research_uses_the_saved_session_tool_budget(prepared, questioning, simulated):
    provider = FakeModelProvider(
        [
            response(
                action="research",
                reason="Need a source after existing context reads",
                research=dict(operation="read", path="opaque.txt"),
            )
        ]
    )
    result = execute(prepared, simulated, provider)
    assert "session tool budget exhausted" in result.reason
    events = inspect_journal(Path(result.journal)).events
    request = next(
        e for e in events if e.tool_request and e.tool_request.request_id.startswith("decision-")
    )
    outcome = next(
        e.tool_result
        for e in events
        if e.tool_result and e.tool_result.request_event_id == request.event_id
    )
    assert outcome.status == "denied" and not outcome.executed
    assert len(provider.calls) == len(questioning[1]) == 1


@pytest.mark.parametrize("kind", ["workspace", "provider", "cancel"])
def test_changed_or_interrupted_decision_never_dispatches_a_new_coder(
    prepared,
    questioning,
    simulated,
    kind,
):
    class Changed(FakeModelProvider):
        async def generate(self, messages, **kwargs):
            reply = await super().generate(messages, **kwargs)
            if kind == "workspace":
                (questioning[1][0][0].runtime.root / "service.py").write_text("external edit\n")
            elif kind == "provider":
                self.settings = self.settings.model_copy(update={"model": "unapproved-model"})
            else:
                raise asyncio.CancelledError
            return reply

    provider = Changed([choice()])
    if kind == "cancel":
        with pytest.raises(asyncio.CancelledError):
            execute(prepared, simulated, provider)
        journal = prepared[0] / ".agent" / ("run-" + prepared[2].plan_id) / "events.jsonl"
        records = inspect_journal(journal)
        assert any(
            e.model_result and e.model_result.status == "interrupted" for e in records.events
        )
        assert not records.unresolved
    else:
        result = execute(prepared, simulated, provider)
        assert "changed during dispatch" in result.reason
        assert result.status == "replan_required"
    assert len(provider.calls) == len(questioning[1]) == 1


def test_decision_record_failure_prevents_replanning_and_coder(
    prepared,
    questioning,
    simulated,
    monkeypatch,
):
    original = JsonlJournal.write

    def fail(journal, event):
        if event.kind == "decision_recorded":
            raise EventWriteError("cannot record the decision")
        original(journal, event)

    monkeypatch.setattr(JsonlJournal, "write", fail)
    provider = FakeModelProvider([choice()])
    with pytest.raises(EventWriteError):
        execute(prepared, simulated, provider)
    assert len(provider.calls) == len(questioning[1]) == 1


@pytest.mark.parametrize(
    "prepared",
    [{"autonomy": {"preferences": ["Keep service.run returning 1"]}}],
    indirect=True,
)
def test_explicit_business_preference_and_decisions_survive_multiple_versions(
    prepared,
    questioning,
    simulated,
):
    data = QUESTION.model_dump()
    data["replan"]["question"]["kind"] = "requirement"
    questioning[0][:] = [CoderResult.model_validate(data), QUESTION, IMPLEMENTED]
    provider = FakeModelProvider(
        [
            choice(sources=[], preference_indices=[0], choice="First supported choice"),
            reply(revised(prepared[2].draft)),
            choice(choice="Second supported choice"),
            reply(revised(prepared[2].draft, "refined-again")),
        ]
    )
    result = execute(prepared, simulated, provider)
    assert result.status == "tasks_verified", result.reason
    feedback = " ".join(questioning[1][-1][1]["feedback"])
    assert "First supported choice" in feedback and "Second supported choice" in feedback
    assert result.usage.decisions == result.usage.replans == 2


def test_decision_preferences_are_sanitized_before_persistence(project):
    root, _, draft = project
    secret = "test-private-policy-value"
    result = asyncio.run(
        plan(
            root,
            draft=draft,
            secrets=(secret,),
            settings=PlanSettings(autonomy={"preferences": ["Use " + secret]}),
        )
    )
    assert result.plan is not None
    assert secret not in result.plan.model_dump_json()
    assert secret not in Path(result.path).read_text()


def test_decision_does_not_replace_required_verification(prepared, questioning, simulated):
    from coding_agent.runtime.process import ProcessOutcome

    simulated[2][0] = ProcessOutcome(1, "No module named pytest", False, False)
    provider = FakeModelProvider([choice(), reply(revised(prepared[2].draft))])
    result = execute(prepared, simulated, provider)
    assert result.status == "blocked" and not result.requirement_complete
    assert result.workflow.evidence and all(
        e.status == "unavailable" for e in result.workflow.evidence
    )
    assert result.usage.decisions == 1 and len(questioning[1]) == 2


def test_forged_authorization_in_model_result_is_rejected(prepared, questioning, simulated):
    payload = json.loads(choice().message.content) | {"approved": True}
    provider = FakeModelProvider(
        [
            ModelResponse(
                response_id="forged",
                model="fake",
                message=Message(role="assistant", content=json.dumps(payload)),
            )
        ]
    )
    result = execute(prepared, simulated, provider)
    assert "invalid_response" in result.reason
    assert len(questioning[1]) == 1 and result.usage.decisions == 1


def test_newly_observed_scope_is_reread_on_later_decisions(prepared, questioning, simulated):
    questioning[0][:] = [QUESTION, QUESTION, IMPLEMENTED]
    data = revised(prepared[2].draft).model_dump(mode="json")
    data["tasks"][0]["task"]["scope"]["allowed"].append("opaque.txt")
    first = PlanDraft.model_validate(data)
    provider = FakeModelProvider(
        [
            response(
                action="research",
                reason="Inspect unsampled scope",
                research=dict(operation="read", path="opaque.txt"),
            ),
            choice(),
            reply(first),
            choice(),
            reply(revised(first, "next")),
        ]
    )
    result = execute(prepared, simulated, provider)
    assert result.status == "tasks_verified", result.reason
    for coder, _ in questioning[1][1:]:
        assert "opaque.txt" in {s.path for s in coder.context.current_sources}
        assert "opaque.txt" not in {s.path for s in coder.context.knowledge_sources}
    assert result.usage.decisions == result.usage.replans == 2
    records = inspect_journal(Path(result.journal))
    assert any(
        e.tool_request
        and e.tool_request.invocation.kind == "read"
        and e.tool_request.invocation.path == "opaque.txt"
        and e.revision.plan_version == 2
        for e in records.events
    )
