import asyncio
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from coding_agent.application.initialization import initialize
from coding_agent.application.planning import inspect_plan, plan, render_graph, run_spec
from coding_agent.cli import main
from coding_agent.core.knowledge import KnowledgeSnapshot, OpenQuestion, SourceReference
from coding_agent.core.models import RequirementContract, TaskSpec
from coding_agent.core.planning import (
    ComplexityAssessment,
    Milestone,
    PlanDraft,
    PlannedTask,
    PlanningOperation,
    PlanningRevision,
    PlanSettings,
    PlanVersion,
    RequirementCoverage,
    applicable_rules,
    validate_draft,
)
from coding_agent.core.provider import Message, ModelResponse
from coding_agent.core.tools import Decision, ToolRequest
from coding_agent.core.workflow import EventWriteError, PlanApproval, WorkflowMode
from coding_agent.core.workflow.policy import effective_mode
from coding_agent.session.records import JsonlJournal, inspect_journal
from coding_agent.testing import FakeModelProvider
from coding_agent.tools.planning import PlanningRuntime


@pytest.fixture
def project(tmp_path, acceptance):
    root = tmp_path / "sample project"
    root.mkdir()
    (root / "AGENTS.md").write_text("Preserve public behavior.\n")
    (root / "service.py").write_bytes(b"def run(): return 1\n")
    (root / "opaque.txt").write_text("not sampled by Explorer\n")
    initialized = asyncio.run(initialize(root))
    knowledge = KnowledgeSnapshot.model_validate_json(
        (root / ".agent" / f"knowledge-{initialized.revision}.json").read_bytes()
    )
    requirement = RequirementContract(
        id="behavior-change",
        title="Update service",
        goal="Update the service safely",
        functional_requirements=("Preserve the service response",),
        risk={"level": "high", "security": "high"},
    )
    task = TaskSpec(
        id="service",
        title="Update service",
        goal=requirement.goal,
        scope={"allowed": ("service.py",)},
        requirements=requirement.functional_requirements,
        acceptance=acceptance,
        risk=requirement.risk,
        permissions={"shell": "restricted"},
    )
    draft = PlanDraft(
        requirement=requirement,
        assessment=ComplexityAssessment(
            complexity="small",
            execution_strategy="single_task",
            scope="One service entry point.",
            coupling="No known external callers.",
            uncertainty="Caller coverage remains a review judgment.",
            verification_difficulty="Run the existing behavior tests and independent review.",
            reasons=("The observed entry point is localized.",),
            sources=(
                SourceReference(path="service.py", line=1, end_line=1, quote="def run(): return 1"),
            ),
        ),
        acceptance=acceptance,
        coverage=(RequirementCoverage(requirement=1, criterion_ids=("behavior",)),),
        tasks=(
            PlannedTask(
                task=task,
                criterion_ids=("behavior",),
                rule_ids=applicable_rules(knowledge, ("service.py",)),
            ),
        ),
        milestones=(
            Milestone(
                id="current",
                title="Service change",
                status="current",
                criterion_ids=("behavior",),
                task_ids=("service",),
            ),
        ),
    )
    return root, knowledge, draft


def run(project, **kwargs):
    root, _, draft = project
    return asyncio.run(plan(root, draft=draft, **kwargs))


def reply(draft):
    return ModelResponse(
        response_id="p1",
        model="fake",
        message=Message(role="assistant", content=draft.model_dump_json()),
    )


def test_plan_is_persisted_without_running_or_authorizing_tasks(project, monkeypatch):
    root, knowledge, draft = project
    original = PlanningRuntime._replace

    def checked(runtime, name, data, expected):
        view = inspect_journal(runtime.journal.directory / "events.jsonl")
        assert view.events[-1].tool_request.invocation.operation == "publish"
        return original(runtime, name, data, expected)

    monkeypatch.setattr(PlanningRuntime, "_replace", checked)
    result = run(project, settings=PlanSettings(mode="fast"))
    saved = result.plan
    assert result.status == "proposed" and saved is not None
    assert saved.context_revision == knowledge.revision and saved.version == 1
    assert (root / "service.py").read_text() == "def run(): return 1\n"
    assert "opaque.txt" in {f.path for f in saved.workspace.files}
    assert Path(result.path).read_bytes() == saved.model_dump_json().encode()
    assert Path(result.path).with_suffix(".md").exists()
    spec = run_spec(saved)
    assert effective_mode(draft.tasks[0].task, spec.mode) is WorkflowMode.STRICT
    view = inspect_journal(Path(result.journal))
    assert not view.unresolved
    assert all(isinstance(e.revision, PlanningRevision) for e in view.events)
    assert not any(e.kind == "plan_accepted" or e.evidence_ids for e in view.events)
    inspected = inspect_plan(root)
    assert inspected.authorization == "required" and inspected.execution_status == "not_tracked"
    assert not inspected.requirement_complete


def test_model_is_read_only_and_uses_recorded_schema(project):
    root, _, draft = project
    provider = FakeModelProvider([reply(draft)])
    result = asyncio.run(plan(root, draft.requirement, provider=provider))
    assert result.status == "proposed" and len(provider.calls) == 1
    assert provider.calls[0][1] == () and provider.calls[0][2] is PlanDraft
    assert all(rule_id in provider.calls[0][0][-1].content for rule_id in draft.tasks[0].rule_ids)
    view = inspect_journal(Path(result.journal))
    assert [e.kind for e in view.events].count("model_requested") == 1
    assert [e.kind for e in view.events].count("model_finished") == 1


def test_no_model_never_invents_an_executable_plan(project):
    root, _, draft = project
    result = asyncio.run(plan(root, draft.requirement.goal))
    assert result.status == "blocked" and result.plan is None
    assert not (root / ".agent/plan.json").exists()


def test_first_plan_reuses_initialization_flow(tmp_path, project):
    _, _, draft = project
    (tmp_path / "AGENTS.md").write_text("Preserve public behavior.\n")
    (tmp_path / "service.py").write_text("def run(): return 1\n")
    result = asyncio.run(plan(tmp_path, draft=draft))
    assert result.status == "proposed" and (tmp_path / ".agent/project.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "cycle",
        "missing_dependency",
        "no_acceptance",
        "broad_scope",
        "controller_path",
        "missing_requirement",
        "missing_rule",
    ],
)
def test_invalid_plans_never_reach_publication(project, mutation):
    root, knowledge, draft = project
    data = draft.model_dump(mode="json")
    task = data["tasks"][0]["task"]
    if mutation == "cycle":
        task["depends_on"] = ["service"]
    elif mutation == "missing_dependency":
        task["depends_on"] = ["later"]
    elif mutation == "no_acceptance":
        task["acceptance"]["criteria"] = []
    elif mutation == "broad_scope":
        task["scope"]["allowed"] = ["**"]
    elif mutation == "controller_path":
        task["scope"]["allowed"] = [".agent/project.json"]
    elif mutation == "missing_requirement":
        data["coverage"] = []
    else:
        data["tasks"][0]["rule_ids"] = []
    with pytest.raises(ValueError):
        parsed = PlanDraft.model_validate(data)
        validate_draft(parsed, knowledge, PlanSettings())
        asyncio.run(plan(root, draft=parsed))
    assert not (root / ".agent/plan.json").exists()


def test_overlapping_writes_require_ordering(project):
    root, knowledge, draft = project
    second = draft.tasks[0].model_copy(
        update={"task": draft.tasks[0].task.model_copy(update={"id": "second"})}
    )
    data = draft.model_dump()
    data["assessment"]["execution_strategy"] = "task_graph"
    data["tasks"] = (draft.tasks[0], second)
    data["milestones"][0]["task_ids"] = ("service", "second")
    unordered = PlanDraft.model_validate(data)
    with pytest.raises(ValueError, match="overlapping"):
        validate_draft(unordered, knowledge, PlanSettings())
    data["tasks"] = (
        draft.tasks[0],
        second.model_copy(
            update={"task": second.task.model_copy(update={"depends_on": ("service",)})}
        ),
    )
    ordered = PlanDraft.model_validate(data)
    assert asyncio.run(plan(root, draft=ordered)).status == "proposed"


@pytest.mark.parametrize("mutation", ["risk", "network", "budget", "boundary"])
def test_complexity_never_bypasses_risk_permissions_or_budget(project, mutation):
    _, knowledge, draft = project
    settings = PlanSettings()
    if mutation == "risk":
        task = draft.tasks[0].task.model_copy(update={"risk": {"level": "low"}})
        draft = draft.model_copy(
            update={"tasks": (draft.tasks[0].model_copy(update={"task": task}),)}
        )
    elif mutation == "network":
        task = draft.tasks[0].task.model_copy(update={"permissions": {"network": True}})
        draft = draft.model_copy(
            update={"tasks": (draft.tasks[0].model_copy(update={"task": task}),)}
        )
    elif mutation == "budget":
        settings = PlanSettings(max_total_attempts=1)
    else:
        settings = PlanSettings(scope={"allowed": ("other.py",)})
    with pytest.raises(ValueError):
        validate_draft(draft, knowledge, settings)


def test_missing_task_exploration_blocks_but_preserves_proposal(project):
    root, _, draft = project
    task = draft.tasks[0].task.model_copy(update={"scope": {"allowed": ("opaque.txt",)}})
    draft = PlanDraft.model_validate(
        draft.model_dump() | {"tasks": (draft.tasks[0].model_copy(update={"task": task}),)}
    )
    result = asyncio.run(plan(root, draft=draft))
    assert result.status == "blocked" and "--focus" in result.blockers[0]
    assert Path(result.path).exists()
    with pytest.raises(ValueError, match="blocked plan"):
        run_spec(result.plan)


def test_unread_source_changes_invalidate_saved_plan_and_approval(project):
    root, _, _ = project
    result = run(project)
    saved = result.plan
    approval = PlanApproval(
        session_id=saved.plan_id,
        plan_fingerprint=run_spec(saved).fingerprint,
        source="explicit user approval",
        timestamp=datetime.now(UTC),
    )
    assert inspect_plan(root, approval=approval).authorization == "matching"
    (root / "opaque.txt").write_text("uncommitted change outside Explorer excerpts")
    inspection = inspect_plan(root, approval=approval)
    assert inspection.status == "stale" and inspection.authorization == "invalid"


def test_user_guidance_change_invalidates_plan_without_overwrite(project):
    root, _, _ = project
    result = run(project)
    guide = root / ".agent/project.md"
    with guide.open("a") as stream:
        stream.write("\nUse a different service boundary.\n")
    inspection = inspect_plan(root)
    assert inspection.status == "stale"
    assert guide.read_text().endswith("Use a different service boundary.\n")
    assert Path(result.path).exists()


def test_plan_revisions_preserve_history_ids_and_require_new_approval(project):
    root, _, draft = project
    first = run(project).plan
    approval = PlanApproval(
        session_id=first.plan_id,
        plan_fingerprint=run_spec(first).fingerprint,
        source="user",
        timestamp=datetime.now(UTC),
    )
    with pytest.raises(ValueError, match="reason"):
        run(project)
    second = run(project, reason="Explain the same scoped change more clearly").plan
    assert second.version == 2 and second.plan_id == first.plan_id
    assert second.previous_revision == first.revision
    assert second.draft.tasks[0].task.id == draft.tasks[0].task.id
    assert (root / ".agent" / first.filename).read_bytes() == first.model_dump_json().encode()
    assert inspect_plan(root, approval=approval).authorization == "invalid"


def staged(draft):
    data = draft.model_dump(mode="json")
    data["assessment"]["complexity"] = "large"
    data["assessment"]["execution_strategy"] = "staged"
    data["refactor"] = True
    data["invariant_criterion_ids"] = ("behavior",)
    data["baseline_check_ids"] = ("unit",)
    data["milestones"].append(
        dict(
            id="migration",
            title="Migrate remaining callers",
            status="pending",
            criterion_ids=("behavior",),
            task_ids=(),
            unknowns=({"text": "Identify later callers", "required": True},),
        )
    )
    return PlanDraft.model_validate(data)


def test_staged_plan_preserves_unknown_future_scope_without_scheduling_it(project):
    root, knowledge, draft = project
    draft = staged(draft)
    result = asyncio.run(plan(root, draft=draft))
    saved = result.plan
    assert result.status == "proposed"
    assert saved.pending_milestones == ("migration",)
    assert {t.id for t in run_spec(saved).graph.tasks} == {"service"}
    assert run_spec(saved).pending_milestones == ("migration",)
    assert "migration" in render_graph(saved)
    dropped = draft.model_copy(update={"milestones": draft.milestones[:1]})
    with pytest.raises(ValueError, match="pending scope"):
        validate_draft(dropped, knowledge, PlanSettings(), previous=saved)
    unknown = draft.milestones[1].model_dump() | {"task_ids": ("mystery",)}
    with pytest.raises(ValueError, match="placeholders"):
        Milestone.model_validate(unknown)


def test_refactor_without_behavior_baseline_is_rejected(project):
    _, _, draft = project
    data = staged(draft).model_dump()
    data["baseline_check_ids"] = ()
    with pytest.raises(ValueError, match="baseline"):
        PlanDraft.model_validate(data)


def test_required_unknowns_block_and_cannot_be_dropped_in_revisions(project):
    root, _, draft = project
    draft = draft.model_copy(
        update={
            "assessment": draft.assessment.model_copy(
                update={
                    "unknowns": (
                        OpenQuestion(text="Confirm the external caller contract", required=True),
                    )
                }
            )
        }
    )
    first = asyncio.run(plan(root, draft=draft))
    assert first.status == "blocked" and Path(first.path).exists()
    assert inspect_plan(root).status == "blocked"
    cleared = draft.model_copy(
        update={"assessment": draft.assessment.model_copy(update={"unknowns": ()})}
    )
    with pytest.raises(ValueError, match="unresolved"):
        asyncio.run(plan(root, draft=cleared, reason="Drop a required decision"))


def test_import_saved_plan_checks_freshness_and_keeps_origin(project):
    root, _, _ = project
    first = run(project).plan
    copied = asyncio.run(plan(root, import_path=".agent/" + first.filename, new_plan=True))
    assert copied.plan.imported_from == first.revision
    assert copied.plan.plan_id != first.plan_id and copied.status == "proposed"
    assert (
        root / ".agent" / f"imported-plan-{first.revision}.json"
    ).read_bytes() == first.model_dump_json().encode()
    (root / "opaque.txt").write_text("now stale")
    with pytest.raises(ValueError, match="stale"):
        asyncio.run(plan(root, import_path=".agent/" + first.filename, new_plan=True))


def test_changed_sources_during_model_call_prevent_plan_publication(project):
    root, _, draft = project

    class Changing(FakeModelProvider):
        async def generate(self, *args, **kwargs):
            (root / "opaque.txt").write_text("user edit during planning")
            return await super().generate(*args, **kwargs)

    with pytest.raises(ValueError, match="workspace changed"):
        asyncio.run(plan(root, draft.requirement, provider=Changing([reply(draft)])))
    assert (root / "opaque.txt").read_text() == "user edit during planning"
    assert not (root / ".agent/plan.json").exists()


@pytest.mark.parametrize("kind", ["tool_requested", "tool_finished"])
def test_journal_failure_prevents_writes_or_preserves_unknown_result(project, monkeypatch, kind):
    root, _, _ = project
    original = JsonlJournal.write

    def fail(journal, event):
        if (
            journal.session_id.startswith("plan-")
            and event.kind == kind
            and (
                event.tool_request is not None
                and event.tool_request.invocation.operation == "publish"
                or event.tool_result is not None
                and "publish completed" in event.tool_result.reason
            )
        ):
            raise EventWriteError("test storage failure")
        return original(journal, event)

    monkeypatch.setattr(JsonlJournal, "write", fail)
    with pytest.raises(EventWriteError):
        run(project)
    assert (root / ".agent/plan.json").exists() is (kind == "tool_finished")
    assert (root / ".agent/init.lock").exists() is (kind == "tool_finished")


def test_failed_pointer_install_preserves_old_plan_and_new_immutable_artifact(project, monkeypatch):
    root, _, _ = project
    first = run(project).plan
    original = PlanningRuntime._replace

    def fail(runtime, name, data, expected):
        if name == "plan.json":
            raise OSError("pointer unavailable")
        return original(runtime, name, data, expected)

    monkeypatch.setattr(PlanningRuntime, "_replace", fail)
    with pytest.raises(OSError, match="pointer"):
        run(project, reason="Try revised plan")
    assert inspect_plan(root).plan == first
    assert (root / ".agent" / f"plan-{first.plan_id}-v2.json").exists()


def test_inspection_commands_are_read_only_and_do_not_initialize(tmp_path, project, capsys):
    before = list(tmp_path.iterdir())
    with pytest.raises(SystemExit) as error:
        main(["status", "--path", str(tmp_path)])
    assert error.value.code == 2 and list(tmp_path.iterdir()) == before
    root, _, _ = project
    run(project)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert main(["status", "--path", str(root), "--json"]) == 0
    assert main(["graph", "--path", str(root)]) == 0
    capsys.readouterr()
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "budgets", [[], ["--max-tool-calls", "80", "--max-model-calls", "90", "--max-replans", "1"]]
)
def test_cli_offline_draft_and_missing_model_configuration(project, capsys, budgets):
    root, _, draft = project
    (root / "draft.json").write_text(draft.model_dump_json())
    assert (
        main(
            ["plan", "--path", str(root), "--draft", "draft.json", "--refresh", "--json", *budgets]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "proposed" and result["plan"]["version"] == 1
    saved = inspect_plan(root).plan
    assert run_spec(saved).max_tool_calls == (80 if budgets else 30)
    assert run_spec(saved).max_model_calls == (90 if budgets else 31)
    assert saved.settings.max_replans == (1 if budgets else 2)
    assert f"tool request limit: {run_spec(saved).max_tool_calls}" in result["summary"]


def test_plan_operation_is_not_an_agent_capability(project, make_task):
    from coding_agent.core.provider import runtime_tools
    from coding_agent.core.tool_policy import PolicyEngine

    assert "planning" not in {t.name for t in runtime_tools()}
    decision = PolicyEngine().evaluate(
        make_task("t"),
        ToolRequest(
            request_id="p", invocation=PlanningOperation(operation="publish", input_sha256="a" * 64)
        ),
        plan_authorized=True,
        process_denial=None,
        operation_approved=True,
        workspace_controller=True,
    )
    assert decision.decision is Decision.DENY


def test_actual_sdk_accepts_plan_schema_with_no_execution_tools(project):
    import httpx2
    from test_provider import output_text, provider, wire

    root, _, draft = project
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        assert payload["text"]["format"]["name"] == "PlanDraft"
        assert payload.get("tools", []) == []
        calls.append(payload)
        return httpx2.Response(200, json=wire(output_text(draft.model_dump_json())))

    async def execute():
        async with provider(handler) as model:
            return await plan(root, draft.requirement, provider=model)

    assert asyncio.run(execute()).status == "proposed" and len(calls) == 1


def test_compilation_carries_controller_read_restrictions_into_tasks(project):
    result = run(project, settings=PlanSettings(scope={"forbidden": ("private/**",)}), refresh=True)
    task = run_spec(result.plan).graph.tasks[0]
    assert "private/**" in task.scope.forbidden


def test_verified_batch_still_reports_pending_milestones(project):
    from test_workflow import IMPLEMENTED, checks, reviewed

    from coding_agent.core.workflow import WorkflowEngine
    from coding_agent.testing import FakeCoder, FakeEventWriter, FakeReviewer, FakeVerifier

    root, _, draft = project
    result = asyncio.run(plan(root, draft=staged(draft)))
    spec = run_spec(result.plan)
    task = spec.graph.tasks[0]
    approval = PlanApproval(
        session_id=spec.session_id,
        plan_fingerprint=spec.fingerprint,
        source="explicit fake approval",
        timestamp=datetime.now(UTC),
    )
    engine = WorkflowEngine(
        spec,
        coder=FakeCoder([IMPLEMENTED]),
        verifier=FakeVerifier([checks(task, revision=spec.revision)]),
        reviewer=FakeReviewer([reviewed(task, revision=spec.revision)]),
        read_revision=lambda: spec.revision,
        writer=FakeEventWriter(),
    )
    completed = asyncio.run(engine.run(approval))
    assert completed.outcome == "tasks_verified"
    assert completed.pending_milestones == ("migration",)


@pytest.mark.parametrize(
    "path",
    [
        ".agent/plan-../outside.json",
        ".agent/plan-a:stream.json",
        ".agent/plan-a\\outside.json",
        "../foreign.json",
    ],
)
def test_import_cannot_use_aliased_or_arbitrary_control_paths(project, path):
    root, _, _ = project
    with pytest.raises(ValueError):
        asyncio.run(plan(root, import_path=path))


def test_tampered_plan_and_index_are_rejected(project):
    root, _, _ = project
    saved = run(project).plan
    path = root / ".agent" / saved.filename
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="immutable"):
        inspect_plan(root)
    path.write_bytes(original)
    index = root / ".agent/plan.json"
    data = json.loads(index.read_bytes())
    data["plan_id"] = "../outside"
    index.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        inspect_plan(root)


def test_cancelled_planner_records_interruption_and_never_publishes(project):
    root, _, draft = project

    class Cancelled(FakeModelProvider):
        async def generate(self, *args, **kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(plan(root, draft.requirement, provider=Cancelled([])))
    journal = next((root / ".agent").glob("plan-*/events.jsonl"))
    view = inspect_journal(journal)
    assert view.events[-1].model_result.status == "interrupted" and not view.unresolved
    assert not (root / ".agent/plan.json").exists()


def test_authorization_binds_entire_plan_not_only_current_graph(project):
    saved = run(project).plan
    original_spec = run_spec(saved)
    changed = saved.model_copy(update={"change_reason": "Altered planning intent"})
    assert run_spec(changed).graph == original_spec.graph
    assert run_spec(changed).fingerprint != original_spec.fingerprint


@pytest.mark.parametrize(
    "change", [{"version": 2}, {"original_request": "A different requirement"}]
)
def test_saved_plan_requires_coherent_history_and_original_goal(project, change):
    from coding_agent.core.planning import PlanVersion

    saved = run(project).plan
    with pytest.raises(ValueError):
        PlanVersion.model_validate(saved.model_dump() | change)


def test_planning_records_cannot_emit_workflow_outcomes(project):
    from coding_agent.core.workflow import WorkflowEvent

    result = run(project)
    event = inspect_journal(Path(result.journal)).events[0]
    with pytest.raises(ValueError, match="workflow outcomes"):
        WorkflowEvent.model_validate(
            event.model_dump()
            | {
                "kind": "session_finished",
                "tool_request": None,
            }
        )


def test_import_rejects_credentials_before_archiving_original(project):
    root, _, _ = project
    saved = run(project).plan
    data = saved.model_dump(mode="json")
    data["original_request"] = "Configure password=example-private-value"
    data["draft"]["requirement"]["goal"] = data["original_request"]
    name = "plan-" + "b" * 32 + "-v1.json"
    source = root / ".agent" / name
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="contains credentials"):
        asyncio.run(plan(root, import_path=".agent/" + name, new_plan=True))
    assert not list((root / ".agent").glob("imported-plan-*.json"))
    assert "example-private-value" in source.read_text()


@pytest.mark.parametrize("field", ["max_tool_calls", "max_model_calls"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_plan_call_budgets_require_positive_integers(field, value):
    with pytest.raises(ValueError):
        PlanSettings.model_validate({field: value})


def test_legacy_plan_serialization_and_revision_are_preserved(project):
    settings = PlanSettings()
    assert sha256(settings.model_dump_json().encode()).hexdigest() == (
        "7442b324d2ab187a2e71fb4d76165d0dd15a76c34427d08556e226905920848c"
    )
    saved = run(project).plan
    data = saved.model_dump(mode="json")
    assert "max_tool_calls" not in data["settings"]
    assert "max_model_calls" not in data["settings"]
    legacy_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    loaded = PlanVersion.model_validate_json(legacy_json)
    assert loaded.model_dump_json() == legacy_json
    assert loaded.revision == sha256(legacy_json.encode()).hexdigest()
    assert run_spec(loaded).max_tool_calls == 30
    assert run_spec(loaded).max_model_calls == 31


@pytest.mark.parametrize("field", ["max_tool_calls", "max_model_calls"])
def test_call_budgets_bind_approval_and_cannot_change_in_revision(project, field):
    saved = run(project).plan
    spec = run_spec(saved)
    approval = PlanApproval(
        session_id=saved.plan_id,
        plan_fingerprint=spec.fingerprint,
        source="user",
        timestamp=datetime.now(UTC),
    )
    settings = PlanSettings.model_validate({field: 70})
    changed = PlanVersion.model_validate(saved.model_dump() | {"settings": settings})
    loaded = PlanVersion.model_validate_json(changed.model_dump_json())
    assert getattr(run_spec(loaded), field) == 70
    assert loaded.revision != saved.revision
    assert not approval.covers(run_spec(loaded))
    with pytest.raises(ValueError, match="boundaries or budgets"):
        run(project, settings=settings, reason="Increase execution budget")
    assert inspect_plan(project[0]).plan.revision == saved.revision


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_replan_limit_requires_nonnegative_integer(value):
    with pytest.raises(ValueError):
        PlanSettings(max_replans=value)
