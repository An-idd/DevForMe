import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from coding_agent.core import Evidence, EvidenceStatus, EvidenceType, TaskGraph, TaskSpec, TaskState
from coding_agent.core.workflow import (
    CoderResult,
    EventWriteError,
    PlanApproval,
    ReviewResult,
    Revision,
    RunSpec,
    VerificationResult,
    WorkflowEngine,
    WorkflowEvent,
    WorkflowMode,
    WorkflowResult,
)
from coding_agent.testing import FakeCoder, FakeEventWriter, FakeReviewer, FakeVerifier

STAMP = datetime(2026, 9, 15, tzinfo=UTC)
REVISION = Revision(
    plan_version=1, context_revision="project-001", workspace_revision="snapshot-001"
)
IMPLEMENTED = CoderResult(outcome="implemented", summary="explicit fake implementation")


def checks(
    task: TaskSpec,
    *,
    number: int = 1,
    revision: Revision = REVISION,
    status: EvidenceStatus = EvidenceStatus.PASSED,
) -> VerificationResult:
    """Explicit fixture records; these are never real verification results."""
    required = {check.id: check for check in task.acceptance.checks}
    return VerificationResult(
        evidence=tuple(
            Evidence(
                id=f"fake:{task.id}:{number}:{criterion.id}:{check_id}",
                task_id=task.id,
                plan_version=revision.plan_version,
                context_revision=revision.context_revision,
                workspace_revision=revision.workspace_revision,
                criterion_id=criterion.id,
                criterion=criterion.description,
                check_id=check_id,
                evidence_type=required[check_id].evidence_type,
                command=required[check_id].command,
                result=f"fixture result: {status.value}",
                status=status,
                source=f"fake-check:{task.id}:{number}:{check_id}",
                timestamp=STAMP,
            )
            for criterion in task.acceptance.criteria
            for check_id in criterion.required_check_ids
            if required[check_id].evidence_type is not EvidenceType.REVIEW
        )
    )


def reviewed(task: TaskSpec, *, revision: Revision = REVISION, **changes: object) -> ReviewResult:
    return ReviewResult.model_validate(
        {
            "task_id": task.id,
            "revision": revision,
            "status": "passed",
            "summary": "explicit fake review",
            "source": f"fake-review:{task.id}",
            "timestamp": STAMP,
        }
        | changes
    )


class Harness:
    def __init__(self, spec: RunSpec) -> None:
        self.spec = spec
        self.current = spec.revision
        self.coder = FakeCoder([IMPLEMENTED for _ in spec.graph.tasks])
        self.verifier = FakeVerifier([checks(task) for task in spec.graph.tasks])
        self.reviewer = FakeReviewer([reviewed(task) for task in spec.graph.tasks])
        self.writer = FakeEventWriter()

    @property
    def approval(self) -> PlanApproval:
        return PlanApproval(
            session_id=self.spec.session_id,
            plan_fingerprint=self.spec.fingerprint,
            source="explicit-test-authorization",
            timestamp=STAMP,
        )

    def create(self) -> WorkflowEngine:
        self.engine = WorkflowEngine(
            self.spec,
            coder=self.coder,
            verifier=self.verifier,
            reviewer=self.reviewer,
            read_revision=lambda: self.current,
            writer=self.writer,
        )
        return self.engine

    def run(self) -> WorkflowResult:
        return asyncio.run(self.create().run(self.approval))


@pytest.fixture
def harness(make_task: Callable[..., TaskSpec]) -> Harness:
    return Harness(
        RunSpec(
            session_id="session-001", graph=TaskGraph(tasks=(make_task("t"),)), revision=REVISION
        )
    )


def test_success_events_prove_verified_follows_checks(harness: Harness) -> None:
    result = harness.run()
    assert result.outcome == "tasks_verified"
    assert result.tasks[0].state is TaskState.VERIFIED
    assert result.tasks[0].attempts == 1
    assert {e.evidence_type for e in result.evidence} == {EvidenceType.TEST, EvidenceType.REVIEW}
    assert "delivery not run" in result.reason
    events = harness.writer.events
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len({event.event_id for event in events}) == len(events)
    assert all(
        event.session_id == harness.spec.session_id and event.revision == REVISION
        for event in events
    )
    kinds = [event.kind for event in events]
    assert kinds.index("plan_accepted") < kinds.index("coder_started")
    assert kinds.index("coder_finished") < kinds.index("verification_started")
    assert kinds.index("verification_finished") < kinds.index("review_started")
    verified = next(e.sequence for e in events if e.state is TaskState.VERIFIED)
    assert next(e.sequence for e in events if e.kind == "review_finished") < verified
    assert events[-1].kind == "session_finished"
    assert {e.id for e in result.evidence} == {
        eid for event in events for eid in event.evidence_ids
    }
    assert WorkflowResult.model_validate_json(result.model_dump_json()) == result
    assert WorkflowEvent.model_validate_json(events[0].model_dump_json()) == events[0]


def test_graph_executes_in_dependency_order_with_one_approval(
    make_task: Callable[..., TaskSpec],
) -> None:
    graph = TaskGraph(
        tasks=(make_task("d", "b", "c"), make_task("b", "a"), make_task("a"), make_task("c", "a"))
    )
    h = Harness(RunSpec(session_id="diamond", graph=graph, revision=REVISION))
    order = [graph.tasks[2], graph.tasks[1], graph.tasks[3], graph.tasks[0]]
    h.verifier = FakeVerifier([checks(t) for t in order])
    h.reviewer = FakeReviewer([reviewed(t) for t in order])
    result = h.run()
    assert result.outcome == "tasks_verified"
    assert [call[0] for call in h.coder.calls] == ["a", "b", "c", "d"]
    assert sum(e.kind == "plan_accepted" for e in h.writer.events) == 1
    active = set()
    for event in h.writer.events:
        if event.state is TaskState.RUNNING:
            active.add(event.task_id)
            assert len(active) == 1
        elif event.state is TaskState.VERIFIED:
            active.remove(event.task_id)
    assert not active


@pytest.mark.parametrize("outcome", ["blocked", "replan_required", "failed", "cancelled"])
def test_coder_stop_never_unlocks_downstream(
    make_task: Callable[..., TaskSpec], outcome: str
) -> None:
    graph = TaskGraph(tasks=(make_task("parent"), make_task("child", "parent")))
    h = Harness(RunSpec(session_id="stop", graph=graph, revision=REVISION))
    h.coder = FakeCoder(
        [CoderResult.model_validate({"outcome": outcome, "summary": "explicit stop"})]
    )
    result = h.run()
    assert result.outcome == outcome
    assert [call[0] for call in h.coder.calls] == ["parent"]
    assert not h.verifier.calls and not h.reviewer.calls
    assert result.tasks[1].state is (
        TaskState.CANCELLED if outcome == "cancelled" else TaskState.BLOCKED
    )


def test_verification_failure_repairs_and_retains_failed_evidence(harness: Harness) -> None:
    task = harness.spec.graph.tasks[0]
    harness.coder = FakeCoder([IMPLEMENTED, IMPLEMENTED])
    harness.verifier = FakeVerifier(
        [checks(task, status=EvidenceStatus.FAILED), checks(task, number=2)]
    )
    result = harness.run()
    assert result.outcome == "tasks_verified"
    assert [call[2] for call in harness.coder.calls] == ["implement", "debug"]
    assert harness.coder.calls[1][3]
    assert len(harness.reviewer.calls) == 1
    assert [e.status for e in result.evidence if e.evidence_type is EvidenceType.TEST] == [
        EvidenceStatus.FAILED,
        EvidenceStatus.PASSED,
    ]


def test_review_fix_must_reverify_and_rereview(harness: Harness) -> None:
    task = harness.spec.graph.tasks[0]
    harness.coder = FakeCoder([IMPLEMENTED, IMPLEMENTED])
    harness.verifier = FakeVerifier([checks(task), checks(task, number=2)])
    harness.reviewer = FakeReviewer([reviewed(task, major=["missing behavior"]), reviewed(task)])
    result = harness.run()
    assert result.outcome == "tasks_verified"
    assert [call[2] for call in harness.coder.calls] == ["implement", "review_fix"]
    assert "missing behavior" in harness.coder.calls[1][3]
    assert len(harness.verifier.calls) == len(harness.reviewer.calls) == 2
    assert result.tasks[0].review_fixes == 1
    states = [e.state for e in harness.writer.events if e.kind == "task_state_changed"]
    fixing = states.index(TaskState.FIXING)
    assert states[fixing + 1] is TaskState.VERIFYING
    assert len(result.reviews) == 2
    assert [e.status for e in result.evidence if e.evidence_type is EvidenceType.REVIEW] == [
        EvidenceStatus.FAILED,
        EvidenceStatus.PASSED,
    ]


def test_failures_exhaust_attempt_budget_without_infinite_retries(harness: Harness) -> None:
    task = harness.spec.graph.tasks[0]
    harness.coder = FakeCoder([IMPLEMENTED] * 4)
    harness.verifier = FakeVerifier(
        [checks(task, number=n, status=EvidenceStatus.FAILED) for n in range(1, 5)]
    )
    result = harness.run()
    assert result.outcome == "replan_required"
    assert result.tasks[0].attempts == 3
    assert "attempt budget exhausted" in result.reason
    assert len(harness.coder.calls) == len(harness.verifier.calls) == 3
    assert not harness.reviewer.calls
    with pytest.raises(RuntimeError, match="already started"):
        asyncio.run(harness.engine.run(harness.approval))
    assert len(harness.coder.calls) == 3


@pytest.mark.parametrize("limit", [0, 1])
def test_review_fixes_have_separate_limit(harness: Harness, limit: int) -> None:
    task = harness.spec.graph.tasks[0]
    harness.spec = RunSpec.model_validate(harness.spec.model_dump() | {"max_review_fixes": limit})
    harness.coder = FakeCoder([IMPLEMENTED] * 3)
    harness.verifier = FakeVerifier([checks(task, number=n) for n in range(1, 4)])
    harness.reviewer = FakeReviewer([reviewed(task, status="failed")] * 3)
    result = harness.run()
    assert result.outcome == "replan_required"
    assert result.tasks[0].review_fixes == limit
    assert result.tasks[0].attempts == limit + 1


def test_total_attempt_budget_carries_across_tasks(make_task: Callable[..., TaskSpec]) -> None:
    graph = TaskGraph(tasks=(make_task("a"), make_task("b", "a")))
    h = Harness(RunSpec(session_id="budget", graph=graph, revision=REVISION, max_total_attempts=1))
    result = h.run()
    assert result.outcome == "replan_required"
    assert result.tasks[0].state is TaskState.VERIFIED
    assert result.tasks[1].attempts == 0
    assert len(h.coder.calls) == 1


@pytest.mark.parametrize(
    "status", [EvidenceStatus.SKIPPED, EvidenceStatus.UNAVAILABLE, EvidenceStatus.INCONCLUSIVE]
)
def test_unavailable_verification_does_not_trigger_code_repair(
    harness: Harness, status: EvidenceStatus
) -> None:
    harness.verifier = FakeVerifier([checks(harness.spec.graph.tasks[0], status=status)])
    assert harness.run().outcome == "blocked"
    assert len(harness.coder.calls) == 1
    assert not harness.reviewer.calls


def test_missing_checks_cannot_become_success(harness: Harness) -> None:
    harness.verifier = FakeVerifier([VerificationResult(evidence=())])
    assert harness.run().outcome == "blocked"
    assert not harness.reviewer.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("plan_version", 2),
        ("context_revision", "project-002"),
        ("workspace_revision", "snapshot-002"),
    ],
)
def test_stale_verification_requires_replanning(
    harness: Harness, field: str, value: object
) -> None:
    stale = Revision.model_validate(REVISION.model_dump() | {field: value})
    harness.verifier = FakeVerifier([checks(harness.spec.graph.tasks[0], revision=stale)])
    assert harness.run().outcome == "replan_required"
    assert not harness.reviewer.calls


@pytest.mark.parametrize("worker", ["verifier", "reviewer"])
def test_source_change_during_checks_prevents_verified(harness: Harness, worker: str) -> None:
    def mutate(task: TaskSpec, revision: Revision) -> None:
        harness.current = Revision.model_validate(
            revision.model_dump() | {"workspace_revision": "changed"}
        )

    task = harness.spec.graph.tasks[0]
    if worker == "verifier":
        harness.verifier = FakeVerifier([checks(task)], on_call=mutate)
    else:
        harness.reviewer = FakeReviewer([reviewed(task)], on_call=mutate)
    result = harness.run()
    assert result.outcome == "replan_required"
    assert all(e.state is not TaskState.VERIFIED for e in harness.writer.events)


def test_workspace_revision_comes_from_runtime_after_coding(harness: Harness) -> None:
    changed = Revision.model_validate(
        REVISION.model_dump() | {"workspace_revision": "after-coding"}
    )

    def mutate(task: TaskSpec, revision: Revision) -> None:
        harness.current = changed

    task = harness.spec.graph.tasks[0]
    harness.coder = FakeCoder([IMPLEMENTED], on_call=mutate)
    harness.verifier = FakeVerifier([checks(task, revision=changed)])
    harness.reviewer = FakeReviewer([reviewed(task, revision=changed)])
    result = harness.run()
    assert result.outcome == "tasks_verified" and result.revision == changed
    assert all(e.workspace_revision == "after-coding" for e in result.evidence)


def test_changed_knowledge_after_coding_stops_before_verification(harness: Harness) -> None:
    def mutate(task: TaskSpec, revision: Revision) -> None:
        harness.current = Revision.model_validate(
            revision.model_dump() | {"context_revision": "new-rules"}
        )

    harness.coder = FakeCoder([IMPLEMENTED], on_call=mutate)
    assert harness.run().outcome == "replan_required"
    assert not harness.verifier.calls


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "strict"},
        {"max_total_attempts": 31},
        {"max_review_fixes": 3},
        {"worker_timeout_seconds": 1},
        {"session_id": "other"},
    ],
)
def test_authorization_cannot_be_reused_for_changed_plan(
    harness: Harness, change: dict[str, object]
) -> None:
    approval = harness.approval
    harness.spec = RunSpec.model_validate(harness.spec.model_dump() | change)
    result = asyncio.run(harness.create().run(approval))
    assert result.outcome == "blocked"
    assert not harness.coder.calls


def test_approval_binds_acceptance_scope_permissions_and_revisions(harness: Harness) -> None:
    approval = harness.approval
    for change in [
        {"scope": {"allowed": ["**"]}},
        {"permissions": {"network": True}},
        {"goal": "different goal"},
        {"max_attempts": 10},
    ]:
        data = harness.spec.model_dump()
        data["graph"]["tasks"] = (data["graph"]["tasks"][0] | change,)
        assert not approval.covers(RunSpec.model_validate(data))
    data = harness.spec.model_dump()
    data["revision"]["plan_version"] = 2
    assert not approval.covers(RunSpec.model_validate(data))
    assert approval.covers(RunSpec.model_validate_json(harness.spec.model_dump_json()))


def test_missing_approval_and_changed_starting_snapshot_run_no_workers(harness: Harness) -> None:
    assert asyncio.run(harness.create().run()).outcome == "blocked"
    assert not harness.coder.calls
    harness.writer = FakeEventWriter()
    harness.current = Revision.model_validate(
        REVISION.model_dump() | {"workspace_revision": "manual-change"}
    )
    assert harness.run().outcome == "replan_required"
    assert not harness.coder.calls


@pytest.mark.parametrize(
    "requested,risk,review_expected",
    [
        (WorkflowMode.FAST, "low", False),
        (WorkflowMode.FAST, "medium", True),
        (WorkflowMode.FAST, "high", True),
        (WorkflowMode.STANDARD, "low", True),
        (WorkflowMode.STRICT, "low", True),
    ],
)
def test_fast_omits_only_optional_review(
    harness: Harness, requested: WorkflowMode, risk: str, review_expected: bool
) -> None:
    data = harness.spec.model_dump()
    task = data["graph"]["tasks"][0]
    task["risk"] = {"level": risk}
    task["acceptance"]["criteria"][0]["required_check_ids"] = ("unit",)
    task["acceptance"]["checks"] = (task["acceptance"]["checks"][0],)
    data["mode"] = requested
    harness.spec = RunSpec.model_validate(data)
    result = harness.run()
    assert result.outcome == "tasks_verified"
    assert bool(harness.reviewer.calls) is review_expected
    assert any(e.state is TaskState.REVIEWING for e in harness.writer.events)
    assert (
        any(e.kind == "review_not_required" for e in harness.writer.events) is not review_expected
    )


@pytest.mark.parametrize("sequence", range(1, 18))
def test_writer_failure_stops_new_dispatch_and_cannot_report_success(
    harness: Harness, sequence: int
) -> None:
    harness.writer = FakeEventWriter(fail_at=sequence)
    with pytest.raises(EventWriteError):
        harness.run()
    assert harness.engine.result is None
    assert len(harness.writer.events) == sequence - 1
    for calls, started_kind in [
        (harness.coder.calls, "coder_started"),
        (harness.verifier.calls, "verification_started"),
        (harness.reviewer.calls, "review_started"),
    ]:
        assert bool(calls) is any(e.kind == started_kind for e in harness.writer.events)
    if sequence <= 16:
        assert harness.engine.states["t"] is not TaskState.VERIFIED


@pytest.mark.parametrize("worker", ["coder", "verifier", "reviewer"])
def test_worker_errors_block_without_leaking_exception_text(harness: Harness, worker: str) -> None:
    error = RuntimeError("secret=should-not-be-logged")
    if worker == "coder":
        harness.coder = FakeCoder([error])
    elif worker == "verifier":
        harness.verifier = FakeVerifier([error])
    else:
        harness.reviewer = FakeReviewer([error])
    result = harness.run()
    assert result.outcome == "blocked"
    assert "should-not-be-logged" not in result.model_dump_json()
    assert all("should-not-be-logged" not in e.model_dump_json() for e in harness.writer.events)


@pytest.mark.parametrize("worker", ["coder", "verifier", "reviewer"])
def test_external_cancellation_preserves_inspectable_state(harness: Harness, worker: str) -> None:
    if worker == "coder":
        harness.coder = FakeCoder([asyncio.CancelledError()])
    elif worker == "verifier":
        harness.verifier = FakeVerifier([asyncio.CancelledError()])
    else:
        harness.reviewer = FakeReviewer([asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        harness.run()
    assert harness.engine.result is not None
    assert harness.engine.result.outcome == "cancelled"
    assert harness.engine.states["t"] is TaskState.CANCELLED
    assert harness.writer.events[-1].kind == "session_interrupted"


def test_timeout_is_bounded_and_does_not_retry_uncertain_worker(harness: Harness) -> None:
    class WaitingVerifier:
        async def verify(self, task: TaskSpec, revision: Revision) -> VerificationResult:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    harness.spec = RunSpec.model_validate(
        harness.spec.model_dump() | {"worker_timeout_seconds": 0.01}
    )
    engine = WorkflowEngine(
        harness.spec,
        coder=harness.coder,
        verifier=WaitingVerifier(),
        reviewer=harness.reviewer,
        read_revision=lambda: REVISION,
        writer=harness.writer,
    )
    result = asyncio.run(engine.run(harness.approval))
    assert result.outcome == "blocked" and "TimeoutError" in result.reason
    assert len(harness.coder.calls) == 1


def test_engine_rejects_concurrent_or_repeated_runs(harness: Harness) -> None:
    async def scenario() -> None:
        engine = harness.create()
        one = asyncio.create_task(engine.run(harness.approval))
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="already started"):
            await engine.run(harness.approval)
        assert (await one).outcome == "tasks_verified"

    asyncio.run(scenario())
    assert len(harness.coder.calls) == 1


def test_workers_cannot_write_state_or_claim_verified(harness: Harness) -> None:
    engine = harness.create()
    with pytest.raises(TypeError):
        engine.states["t"] = TaskState.VERIFIED  # type: ignore[index]
    for result in [
        {"outcome": "VERIFIED", "summary": "done"},
        {"outcome": "implemented", "summary": "done", "state": "VERIFIED"},
        {"outcome": "implemented", "summary": "done", "evidence": []},
    ]:
        with pytest.raises(ValidationError):
            CoderResult.model_validate(result)
    assert not hasattr(engine, "transition")
    assert engine.states["t"] is TaskState.PENDING


def test_duplicate_evidence_id_across_attempts_is_not_reused(harness: Harness) -> None:
    task = harness.spec.graph.tasks[0]
    harness.coder = FakeCoder([IMPLEMENTED, IMPLEMENTED])
    harness.verifier = FakeVerifier([checks(task, status=EvidenceStatus.FAILED), checks(task)])
    result = harness.run()
    assert result.outcome == "blocked"
    assert len(result.evidence) == 1 and result.evidence[0].status is EvidenceStatus.FAILED


def test_empty_graph_dispatches_no_workers(harness: Harness) -> None:
    harness.spec = RunSpec.model_validate(harness.spec.model_dump() | {"graph": {"tasks": []}})
    result = harness.run()
    assert result.outcome == "tasks_verified" and not result.tasks
    assert not harness.coder.calls and not harness.verifier.calls and not harness.reviewer.calls


def test_review_unavailable_blocks_without_repairs(harness: Harness) -> None:
    task = harness.spec.graph.tasks[0]
    harness.reviewer = FakeReviewer([reviewed(task, status="unavailable")])
    assert harness.run().outcome == "blocked"
    assert len(harness.coder.calls) == 1


def test_review_fix_failure_returns_to_debug_and_shares_attempt_budget(harness: Harness) -> None:
    task = harness.spec.graph.tasks[0]
    harness.coder = FakeCoder([IMPLEMENTED] * 3)
    harness.verifier = FakeVerifier(
        [
            checks(task),
            checks(task, number=2, status=EvidenceStatus.FAILED),
            checks(task, number=3),
        ]
    )
    harness.reviewer = FakeReviewer([reviewed(task, major=["missing behavior"]), reviewed(task)])
    result = harness.run()
    assert result.outcome == "tasks_verified"
    assert [call[2] for call in harness.coder.calls] == ["implement", "review_fix", "debug"]
    assert result.tasks[0].attempts == 3 and result.tasks[0].review_fixes == 1


def test_state_snapshots_and_recorded_events_cannot_be_mutated(harness: Harness) -> None:
    engine = harness.create()
    before = engine.states
    result = asyncio.run(engine.run(harness.approval))
    assert before["t"] is TaskState.PENDING
    assert engine.states["t"] is TaskState.VERIFIED
    with pytest.raises(ValidationError, match="frozen"):
        result.tasks[0].state = TaskState.PENDING  # type: ignore[misc]
    with pytest.raises(ValidationError, match="frozen"):
        harness.writer.events[0].reason = "rewritten"  # type: ignore[misc]


def test_revision_reader_failure_stops_before_workers(harness: Harness) -> None:
    def broken_reader() -> Revision:
        raise OSError("snapshot unavailable")

    engine = WorkflowEngine(
        harness.spec,
        coder=harness.coder,
        verifier=harness.verifier,
        reviewer=harness.reviewer,
        read_revision=broken_reader,
        writer=harness.writer,
    )
    result = asyncio.run(engine.run(harness.approval))
    assert result.outcome == "blocked" and not harness.coder.calls


def test_fake_script_exhaustion_is_explicit_failure(harness: Harness) -> None:
    harness.coder = FakeCoder([])
    result = harness.run()
    assert result.outcome == "blocked"
    assert not harness.verifier.calls


def test_invalid_worker_result_is_revalidated(harness: Harness) -> None:
    # Deliberately bypass construction to model a defective/untrusted adapter.
    harness.coder = FakeCoder([CoderResult.model_construct(outcome="VERIFIED", summary="claim")])
    assert harness.run().outcome == "blocked"
    assert not harness.verifier.calls


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": None},
        {"state": None},
        {"previous_state": None},
        {"previous_state": "PENDING", "state": "VERIFIED"},
        {"sequence": 0},
        {"timestamp": "2026-09-15T00:00:00"},
    ],
)
def test_invalid_lifecycle_records_are_rejected(
    harness: Harness, change: dict[str, object]
) -> None:
    harness.run()
    event = next(e for e in harness.writer.events if e.kind == "task_state_changed")
    with pytest.raises(ValidationError):
        WorkflowEvent.model_validate(event.model_dump() | change)


def test_missing_approval_provenance_is_not_invented(harness: Harness) -> None:
    for field in ("source", "timestamp", "plan_fingerprint", "session_id"):
        data = harness.approval.model_dump()
        data.pop(field)
        with pytest.raises(ValidationError):
            PlanApproval.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_total_attempts", 0),
        ("max_review_fixes", -1),
        ("max_total_attempts", True),
        ("worker_timeout_seconds", 0),
        ("worker_timeout_seconds", float("inf")),
    ],
)
def test_invalid_limits_rejected(harness: Harness, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        RunSpec.model_validate(harness.spec.model_dump() | {field: value})
