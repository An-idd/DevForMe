"""Execute acceptance commands through ToolRuntime and persist version-bound evidence."""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..core.models import (
    AcceptanceCheck,
    Evidence,
    EvidenceStatus,
    EvidenceType,
    TaskSpec,
)
from ..core.tools import Shell, ToolApproval, ToolRequest, ToolResult, operation_fingerprint
from ..core.verification import CheckExecution, VerificationSettings
from ..core.workflow import EventWriteError, Revision, VerificationResult
from ..runtime.verification import VerificationProcess
from .execution import ExecutionRuntime
from .runtime import ToolRuntime

Phase = Literal["baseline", "task", "final"]


def classify(check: AcceptanceCheck, result: ToolResult) -> tuple[EvidenceStatus, str]:
    if result.status in {"denied", "needs_approval"}:
        return EvidenceStatus.UNAVAILABLE, result.reason
    if result.status in {"timed_out", "interrupted"} or result.truncated:
        return EvidenceStatus.INCONCLUSIVE, "Check interrupted or output incomplete"
    if result.exit_code is None:
        return EvidenceStatus.UNAVAILABLE, result.reason
    if result.exit_code == 0 and result.status != "succeeded":
        return EvidenceStatus.INCONCLUSIVE, "Process did not record success"
    if re.search(
        r"No module named|command not found|PermissionError|Access is denied|"
        r"Permission denied|Operation not permitted|Read-only file system",
        result.output,
        re.I,
    ):
        return EvidenceStatus.UNAVAILABLE, "Required runtime dependency or capability unavailable"
    if check.evidence_type is EvidenceType.TEST:
        command = check.command or ()
        runner = Path(command[0]).stem.lower() if command else ""
        pytest = runner == "pytest" or ("-m", "pytest") == command[1:3]
        unittest = ("-m", "unittest") == command[1:3]
        if pytest and (result.exit_code == 5 or re.search(r"\bno tests ran\b", result.output)):
            return EvidenceStatus.INCONCLUSIVE, "No tests collected"
        collected = re.findall(r"^Ran (\d+) tests? in .+$", result.output, re.M)
        if unittest and collected and int(collected[-1]) == 0:
            return EvidenceStatus.INCONCLUSIVE, "No tests collected"
        if result.exit_code != 0:
            return EvidenceStatus.FAILED, f"Check exited {result.exit_code}"
        if pytest:
            summaries = re.findall(r"^=+ (.+?) =+\s*$", result.output, re.M)
            summary = summaries[-1] if summaries else result.output.strip().splitlines()[-1:]
            text = summary if isinstance(summary, str) else " ".join(summary)
            counts = dict(
                (name, int(number))
                for number, name in re.findall(
                    r"\b(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed|deselected)\b",
                    text,
                )
            )
            if any(counts.get(name, 0) for name in ("failed", "error", "errors", "xpassed")):
                return EvidenceStatus.FAILED, "Test report contains failures"
            if any(counts.get(name, 0) for name in ("skipped", "xfailed", "deselected")):
                return EvidenceStatus.SKIPPED, "Test report contains unexecuted required coverage"
            if counts.get("passed", 0) > 0:
                return EvidenceStatus.PASSED, text
        elif unittest:
            if re.search(r"skipped=\d+|expected failures=\d+", result.output):
                return EvidenceStatus.SKIPPED, "Test report contains skipped coverage"
            if collected and int(collected[-1]) > 0 and re.search(r"^OK\s*$", result.output, re.M):
                return EvidenceStatus.PASSED, f"Ran {collected[-1]} tests"
        return EvidenceStatus.INCONCLUSIVE, "No supported positive test collection report"
    if result.exit_code != 0:
        return EvidenceStatus.FAILED, f"Check exited {result.exit_code}"
    return EvidenceStatus.PASSED, "Declared check exited 0"


def version_command(command: tuple[str, ...]) -> tuple[str, ...]:
    if command[1:3] in {("-m", "pytest"), ("-m", "ruff"), ("-m", "mypy"), ("-m", "build")}:
        return (*command[:3], "--version")
    return (command[0], "--version")


class VerificationRunner:
    def __init__(
        self,
        owner: ExecutionRuntime,
        settings: VerificationSettings,
        runtime: Callable[[str], ToolRuntime],
    ) -> None:
        self.owner, self.settings, self.runtime = owner, settings, runtime

    async def verify(self, task: TaskSpec, revision: Revision) -> VerificationResult:
        return await self.check(task, revision, phase="task")

    async def check(
        self,
        task: TaskSpec,
        revision: Revision,
        *,
        phase: Phase,
        check_ids: tuple[str, ...] | None = None,
    ) -> VerificationResult:
        runtime = self.runtime(task.id)
        backend = VerificationProcess(self.settings)
        runtime.backend = backend
        runtime.timeout = self.settings.timeout_seconds
        runtime.max_output_bytes = self.settings.max_output_bytes
        evidence: list[Evidence] = []
        for check in task.acceptance.checks:
            if check.evidence_type is EvidenceType.REVIEW or (
                check_ids is not None and check.id not in check_ids
            ):
                continue
            if runtime.read_revision() != revision:
                raise ValueError("verification inputs changed before dispatch")
            version = result = None
            executable = None
            status, reason = EvidenceStatus.UNAVAILABLE, "Check has no executable command"
            if check.command is not None:
                # Resolution is read-only. A missing executable still gets a recorded request.
                try:
                    executable = backend.resolve(check.command)
                except ValueError:
                    pass
                probe = ToolRequest(
                    request_id=f"version-{self.owner.journal.next_sequence}",
                    invocation=Shell(argv=version_command(check.command)),
                )
                version = await runtime.execute(
                    probe,
                    approval=ToolApproval(
                        fingerprint=operation_fingerprint(
                            probe,
                            plan_fingerprint=runtime.spec.fingerprint,
                            task_id=task.id,
                            revision_json=revision.model_dump_json(),
                        ),
                        source="controller:declared-check-version-probe",
                        timestamp=datetime.now(UTC),
                    ),
                )
                if runtime.read_revision() != revision:
                    raise ValueError("verification inputs changed during version probe")
                if (
                    version.status == "succeeded"
                    and not version.truncated
                    and version.output.strip()
                ):
                    result = await runtime.execute(
                        ToolRequest(
                            request_id=f"check-{self.owner.journal.next_sequence}",
                            invocation=Shell(argv=check.command),
                        )
                    )
                    status, reason = classify(check, result)
                else:
                    reason = "Tool version unavailable: " + version.reason
            after = runtime.read_revision()
            if after != revision:
                status, reason = EvidenceStatus.INCONCLUSIVE, "Source changed during verification"
            sequence = self.owner.journal.next_sequence
            source = result or version
            records = tuple(
                Evidence(
                    id=f"{runtime.spec.session_id}:check:{sequence}:{index}",
                    task_id=task.id,
                    **revision.model_dump(),
                    criterion_id=criterion.id,
                    criterion=criterion.description,
                    check_id=check.id,
                    evidence_type=check.evidence_type,
                    command=check.command,
                    result=reason,
                    status=status,
                    source=source.request_event_id
                    if source
                    else f"{runtime.spec.session_id}:{sequence}",
                    timestamp=source.finished_at if source else datetime.now(UTC),
                    artifacts=tuple(a.path for r in (version, result) if r for a in r.artifacts),
                )
                for index, criterion in enumerate(task.acceptance.criteria)
                if check.id in criterion.required_check_ids
            )
            report = CheckExecution(
                phase=phase,
                task=task,
                check=check,
                before=revision,
                after=after,
                cwd=str(runtime.root),
                executable_command=executable,
                settings=self.settings,
                version=version,
                result=result,
                evidence=records,
            )
            try:
                self.owner.record("verification_recorded", report, revision, task_id=task.id)
            except EventWriteError:
                self.owner.journal.invalidate()
                raise
            evidence.extend(records)
            if after != revision:
                break
        return VerificationResult(evidence=tuple(evidence))
