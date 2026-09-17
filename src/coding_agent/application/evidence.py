"""Read-only evidence inspection with artifact/source validation and live freshness."""

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Literal

from ..core.models import DomainModel, EvidenceStatus
from ..core.tools import Shell
from ..core.verification import CheckExecution
from ..core.workflow import Revision, RunSpec
from ..core.workspace import WorkspaceOperation
from ..runtime._snapshots import capture
from ..tools.execution import inspect_execution, read_artifact
from ..tools.initialization import EXCLUDED
from ..tools.planning import PlanningRuntime
from ..tools.verification import classify, version_command


class EvidenceView(DomainModel):
    record: CheckExecution
    current: bool


class EvidenceLedger(DomainModel):
    status: Literal["recorded", "incomplete"]
    session_id: str
    journal: str
    current_revision: Revision | None
    checks: tuple[EvidenceView, ...]
    requirement_complete: Literal[False] = False


def inspect_evidence(root: Path, session_id: str | None = None) -> EvidenceLedger:
    history = inspect_execution(root, session_id)
    directory = Path(history.journal).parent
    events = history.records.events
    registered = [e for e in events if e.kind == "plan_registered"]
    if not registered:
        raise ValueError("recorded execution plan unavailable")
    specs: dict[int, RunSpec] = {}
    previous: RunSpec | None = None
    for event in registered:
        if len(event.artifacts) != 1:
            raise ValueError("recorded execution plan unavailable")
        spec = RunSpec.model_validate_json(read_artifact(directory, event.artifacts[0]))
        if sha256(spec.model_dump_json(exclude_unset=True).encode()).hexdigest() != event.reason:
            raise ValueError("execution plan fingerprint changed")
        if spec.revision != event.revision or spec.session_id != history.session_id:
            raise ValueError("execution plan identity changed")
        if previous is not None and (
            event.source != previous.fingerprint
            or spec.revision.plan_version != previous.revision.plan_version + 1
            or spec.revision.context_revision != previous.revision.context_revision
        ):
            raise ValueError("execution plan history changed")
        specs[spec.revision.plan_version] = spec
        previous = spec
    original_spec = next(iter(specs.values()))
    assert previous is not None
    spec = previous
    requests = {e.event_id: e for e in events if e.tool_request is not None}
    results = {e.tool_result.request_event_id: e for e in events if e.tool_result is not None}
    prepare_ids = {
        event_id
        for event_id, request in requests.items()
        if request.tool_request is not None
        and isinstance(request.tool_request.invocation, WorkspaceOperation)
        and request.tool_request.invocation.operation == "prepare"
    }
    current = None
    authorization = next((e for e in events if e.kind == "execution_authorized"), None)
    if authorization is not None:
        saved_authorization = json.loads(read_artifact(directory, authorization.artifacts[0]))
        with PlanningRuntime.read_only(root) as runtime:
            saved, _ = runtime.current()
            state = runtime.load()
            try:
                runtime.check_knowledge(state)
                if saved is not None and saved.revision == original_spec.plan_revision:
                    prepared = next(
                        event.tool_result
                        for event in events
                        if event.tool_result is not None
                        and event.tool_result.status == "succeeded"
                        and event.tool_result.request_event_id in prepare_ids
                    )
                    workspace = Path(json.loads(prepared.output)["path"])
                    if workspace.parent != Path(
                        saved_authorization["workspace"]
                    ) or not re.fullmatch(r"worktree-[a-f0-9]{32}", workspace.name):
                        raise ValueError("recorded worktree path is outside authorization")
                    snapshot = capture(workspace, saved.settings.scope, EXCLUDED)[0]
                    if (
                        capture(root.resolve(), saved.settings.scope, EXCLUDED)[0]
                        == saved.workspace
                    ):
                        current = Revision(
                            plan_version=spec.revision.plan_version,
                            context_revision=spec.revision.context_revision,
                            workspace_revision=snapshot.revision,
                        )
            except (OSError, ValueError, StopIteration):
                # An unavailable/changed source is never inferred current.
                pass
    checks: list[EvidenceView] = []
    for event in events:
        if event.kind != "verification_recorded":
            continue
        if len(event.artifacts) != 1 or event.artifacts[0].truncated:
            raise ValueError("verification record artifact unavailable")
        report = CheckExecution.model_validate_json(read_artifact(directory, event.artifacts[0]))
        report_spec = specs.get(report.before.plan_version)
        if report_spec is None or not any(
            e.revision == report_spec.revision and e.sequence < event.sequence for e in registered
        ):
            raise ValueError("verification plan was not registered before execution")
        tasks = {t.id: t for t in (*report_spec.graph.tasks, *report_spec.verification_tasks)}
        if (
            report.before != event.revision
            or report.task.id != event.task_id
            or tasks.get(report.task.id) != report.task
            or report.check not in report.task.acceptance.checks
        ):
            raise ValueError("verification record does not match the approved plan")
        for result, command in (
            (
                report.version,
                version_command(report.check.command) if report.check.command else None,
            ),
            (report.result, report.check.command),
        ):
            if result is None:
                continue
            request = requests.get(result.request_event_id)
            recorded = results.get(result.request_event_id)
            if (
                request is None
                or request.tool_request is None
                or recorded is None
                or recorded.tool_result != result
                or request.revision != report.before
                or request.task_id != report.task.id
                or not isinstance(request.tool_request.invocation, Shell)
                or request.tool_request.invocation.argv != command
                or recorded.sequence >= event.sequence
            ):
                raise ValueError("verification result has no matching execution source")
            for artifact in result.artifacts:
                read_artifact(directory, artifact)
        expected_status = EvidenceStatus.UNAVAILABLE
        if report.result is not None:
            if (
                report.version is None
                or report.version.status != "succeeded"
                or report.version.truncated
                or not report.version.output.strip()
            ):
                raise ValueError("check ran without a usable version probe")
            expected_status = classify(report.check, report.result)[0]
        if report.before != report.after:
            expected_status = EvidenceStatus.INCONCLUSIVE
        expected_pairs = {
            (c.id, report.check.id)
            for c in report.task.acceptance.criteria
            if report.check.id in c.required_check_ids
        }
        if {(e.criterion_id, e.check_id) for e in report.evidence} != expected_pairs:
            raise ValueError("verification coverage changed")
        if len(report.evidence) != len(expected_pairs):
            raise ValueError("duplicate verification evidence")
        source = report.result or report.version
        for evidence in report.evidence:
            report.task.acceptance.validate_evidence(evidence)
            if (
                evidence.task_id != report.task.id
                or evidence.status != expected_status
                or evidence.plan_version != report.before.plan_version
                or evidence.context_revision != report.before.context_revision
                or evidence.workspace_revision != report.before.workspace_revision
                or evidence.source != (source.request_event_id if source else event.event_id)
                or (source is not None and evidence.timestamp != source.finished_at)
            ):
                raise ValueError("evidence disagrees with execution facts")
        checks.append(EvidenceView(record=report, current=current == report.before == report.after))
    return EvidenceLedger(
        status=history.status,
        session_id=history.session_id,
        journal=history.journal,
        current_revision=current,
        checks=tuple(checks),
    )
