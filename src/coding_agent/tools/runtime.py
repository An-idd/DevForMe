"""One task's tools, bound to an approved plan and the shared session journal."""

import asyncio
import stat
from datetime import UTC, datetime
from pathlib import Path

from ..core.tool_policy import PolicyEngine, relative_parts
from ..core.tools import (
    ArtifactRef,
    Decision,
    Git,
    Patch,
    Read,
    Search,
    Shell,
    ToolApproval,
    ToolRequest,
    ToolResult,
    operation_fingerprint,
)
from ..core.workflow import (
    EventWriteError,
    PlanApproval,
    Revision,
    RevisionReader,
    RunSpec,
    WorkflowEvent,
)
from ..runtime.filesystem import FileBoundaryError, SafeFiles
from ..runtime.process import MacReadOnlyProcess
from ..session.records import JsonlJournal


class ToolRuntime:
    def __init__(
        self,
        spec: RunSpec,
        task_id: str,
        *,
        root: Path,
        journal: JsonlJournal,
        approval: PlanApproval | None,
        read_revision: RevisionReader,
        process_backend: MacReadOnlyProcess | None = None,
        git_executable: Path = Path("/usr/bin/git"),
        timeout: float = 30,
        max_output_bytes: int = 65_536,
        max_file_bytes: int = 1_048_576,
    ) -> None:
        self.spec = RunSpec.model_validate(spec)
        self.task = next(task for task in self.spec.graph.tasks if task.id == task_id)
        self.files = SafeFiles(root, self.task.scope, max_bytes=max_file_bytes)
        self.root = self.files.root
        if journal.directory.is_relative_to(self.root):
            relative = journal.directory.relative_to(self.root).parts
            if not relative or relative[0] != ".agent":
                raise ValueError("controller records inside a repository must be under .agent")
        if (
            timeout <= 0
            or timeout > 300
            or not 1 <= max_output_bytes <= 1_048_576
            or not 1 <= max_file_bytes <= 1_048_576
        ):
            raise ValueError("invalid process/output bounds")
        self.journal = journal
        self.approval = PlanApproval.model_validate(approval) if approval is not None else None
        self.read_revision = read_revision
        self.backend = process_backend or MacReadOnlyProcess()
        self.git_executable = git_executable.resolve(strict=True)
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.policy = PolicyEngine()
        self.journal.register_plan(self.spec)

    async def execute(
        self, request: ToolRequest, *, approval: ToolApproval | None = None
    ) -> ToolResult:
        request = ToolRequest.model_validate(request)
        if len(request.model_dump_json().encode()) > 2 * 1024 * 1024:
            raise ValueError("request exceeds size limit")
        self.journal.begin_operation(request.request_id)
        try:
            return await self._execute(request, approval)
        except EventWriteError:
            self.journal.invalidate()
            raise
        finally:
            self.journal.end_operation()

    async def _execute(self, request: ToolRequest, approval: ToolApproval | None) -> ToolResult:
        revision = Revision.model_validate(self.read_revision())
        fingerprint = operation_fingerprint(
            request,
            plan_fingerprint=self.spec.fingerprint,
            task_id=self.task.id,
            revision_json=revision.model_dump_json(),
        )
        approved = (
            approval is not None
            and ToolApproval.model_validate(approval).fingerprint == fingerprint
        )
        invocation = request.invocation
        process_denial = (
            self.backend.denial(self.task, git=isinstance(invocation, Git))
            if isinstance(invocation, (Shell, Git))
            else None
        )
        plan_authorized = self.approval is not None and self.approval.covers(self.spec)
        plan_authorized &= (
            revision.plan_version == self.spec.revision.plan_version
            and revision.context_revision == self.spec.revision.context_revision
        )
        decision = self.policy.evaluate(
            self.task,
            request,
            plan_authorized=plan_authorized,
            process_denial=process_denial,
            operation_approved=approved,
        )
        sequence = self.journal.next_sequence
        request_event_id = f"{self.spec.session_id}:{sequence}"
        started = datetime.now(UTC)
        self.journal.write(
            WorkflowEvent(
                event_id=request_event_id,
                sequence=sequence,
                timestamp=started,
                session_id=self.spec.session_id,
                task_id=self.task.id,
                revision=revision,
                kind="tool_requested",
                reason=f"{decision.decision.value}: {decision.reason}",
                source=approval.source if approved and approval else None,
                tool_request=request,
            )
        )
        status = "denied" if decision.decision is Decision.DENY else "needs_approval"
        reason = decision.reason
        executed = False
        output, truncated, exit_code = "", False, None
        before, after = None, None
        artifacts: tuple[ArtifactRef, ...] = ()
        cancelled = False
        if decision.decision is Decision.ALLOW:
            try:
                # The durable request is already flushed. Check again before new work.
                self.journal.check_writable()
                if isinstance(invocation, Read):
                    output = self.files.read(invocation.path)
                    executed = True
                elif isinstance(invocation, Search):
                    output, truncated = self.files.search(invocation.path, invocation.query)
                    executed = True
                elif isinstance(invocation, Patch):
                    executed = True  # Failure may occur after replacement; preserve uncertainty.
                    before, after, diff = self.files.patch(
                        invocation.path, invocation.expected_sha256, invocation.content
                    )
                    artifacts = (self.journal.artifact("diff", diff),)
                    output = f"patched {invocation.path}"
                else:
                    cwd = invocation.cwd if isinstance(invocation, Shell) else "."
                    with self.files.directory(cwd):
                        # Directory descriptor traversal rejects symlink paths before launch.
                        workdir = self.root.joinpath(*relative_parts(cwd, root_allowed=True))
                    if isinstance(invocation, Git):
                        info = (self.root / ".git").lstat()
                        if not stat.S_ISDIR(info.st_mode):
                            raise FileBoundaryError(
                                "external Git directories/worktrees require P04"
                            )
                        argv = self._git_argv(invocation)
                    else:
                        argv = invocation.argv
                    executed = True
                    outcome = await self.backend.run(
                        argv,
                        cwd=workdir,
                        root=self.root,
                        task=self.task,
                        protected=self.journal.directory,
                        timeout=self.timeout,
                        max_output_bytes=self.max_output_bytes,
                        git=isinstance(invocation, Git),
                    )
                    output, truncated, exit_code = (
                        outcome.output,
                        outcome.truncated,
                        outcome.exit_code,
                    )
                    status = (
                        "timed_out"
                        if outcome.timed_out
                        else "succeeded"
                        if exit_code == 0
                        else "failed"
                    )
                    reason = (
                        "process timeout" if outcome.timed_out else f"process exited {exit_code}"
                    )
                if not isinstance(invocation, (Shell, Git)):
                    status, reason = "succeeded", "operation completed"
            except EventWriteError:
                raise
            except asyncio.CancelledError:
                status, reason, cancelled = (
                    "interrupted",
                    "operation interrupted; inspect actual state",
                    True,
                )
            except (OSError, ValueError) as error:
                status = "failed"
                reason = f"{type(error).__name__}: operation failed; inspect actual state"
        output = self.journal.sanitizer.text(output)
        encoded = output.encode()
        if len(encoded) > self.max_output_bytes:
            output = encoded[: self.max_output_bytes].decode("utf-8", errors="ignore")
            truncated = True
        if output:
            artifacts += (self.journal.artifact("output", output),)
        result = ToolResult.model_validate(
            dict(
                request_id=request.request_id,
                request_event_id=request_event_id,
                decision=decision.decision,
                status=status,
                executed=executed,
                reason=reason,
                exit_code=exit_code,
                output=output,
                truncated=truncated,
                artifacts=artifacts,
                before_sha256=before,
                after_sha256=after,
                started_at=started,
                finished_at=datetime.now(UTC),
            )
        )
        sequence = self.journal.next_sequence
        self.journal.write(
            WorkflowEvent(
                event_id=f"{self.spec.session_id}:{sequence}",
                sequence=sequence,
                timestamp=result.finished_at,
                session_id=self.spec.session_id,
                task_id=self.task.id,
                revision=revision,
                kind="tool_finished",
                reason=result.reason,
                artifacts=artifacts,
                tool_result=result,
            )
        )
        if cancelled:
            raise asyncio.CancelledError
        return result

    def _git_argv(self, invocation: Git) -> tuple[str, ...]:
        args = (
            str(self.git_executable),
            "--no-pager",
            f"--git-dir={self.root / '.git'}",
            f"--work-tree={self.root}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.bare=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.attributesFile=/dev/null",
            "-c",
            "core.excludesFile=/dev/null",
            "-c",
            f"safe.directory={self.root}",
        )
        if invocation.operation == "status":
            return (*args, "status", "--porcelain=v1", "--untracked-files=normal")
        return (*args, "diff", "--no-ext-diff", "--no-textconv", "--", ".", ":(exclude).agent")
