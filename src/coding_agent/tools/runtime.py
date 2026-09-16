"""One task's tools, bound to an approved plan and the shared session journal."""

import asyncio
import os
import shutil
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from ..core.knowledge import InitializationOperation
from ..core.tool_policy import PolicyEngine, path_permitted, relative_parts
from ..core.tools import (
    ArtifactRef,
    Decision,
    Git,
    Patch,
    PolicyDecision,
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
from ..core.workspace import WorkspaceOperation
from ..runtime.filesystem import FileBoundaryError, SafeFiles
from ..runtime.process import ProcessBackend, ProcessOutcome, default_process_backend
from ..runtime.workspace import Workspace
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
        read_revision: RevisionReader | None = None,
        workspace: Workspace | None = None,
        process_backend: ProcessBackend | None = None,
        git_executable: Path | None = None,
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
        self.workspace = workspace
        if workspace is not None:
            # Snapshot reads use forbidden; writes ALWAYS use the approved task's
            # scope in PolicyEngine and SafeFiles. Serial tasks may allow different
            # writes while sharing exactly the same readable snapshot boundary.
            if (
                workspace.scope.forbidden != self.task.scope.forbidden
                or self.root != workspace.path
            ):
                raise ValueError("workspace root/scope does not match the task")
            self.read_revision = workspace.revision_reader(
                plan_version=self.spec.revision.plan_version,
                context_revision=self.spec.revision.context_revision,
            )
        elif read_revision is not None:
            self.read_revision = read_revision
        else:
            raise ValueError("a live revision reader is required")
        # Windows dispatches Git reads to the isolated Dulwich helper. The host
        # executable is a marker, never an unrestricted Git fallback.
        git_path = git_executable or Path(
            sys.executable if sys.platform == "win32" else shutil.which("git") or "/usr/bin/git"
        )
        self.git_executable = git_path.resolve(strict=True)
        self.backend = process_backend or default_process_backend(
            runtime_roots=() if sys.platform == "win32" else (self.git_executable.parent,)
        )
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.policy = PolicyEngine()
        self.journal.register_plan(self.spec)

    def _sync_workspace(self) -> None:
        if (
            self.workspace is not None
            and self.workspace.prepared
            and self.root != self.workspace.path
        ):
            self.files = SafeFiles(
                self.workspace.path,
                self.task.scope,
                max_bytes=self.files.max_bytes,
            )
            self.root = self.files.root

    async def workspace_operation(
        self, request_id: str, operation: WorkspaceOperation
    ) -> ToolResult:
        """Controller entry point; Agent execute() cannot acquire this capability."""
        if self.workspace is None:
            raise ValueError("no managed workspace configured")
        request = ToolRequest(request_id=request_id, invocation=operation)
        return await self._execute_recorded(request, approval=None, workspace_controller=True)

    async def execute(
        self, request: ToolRequest, *, approval: ToolApproval | None = None
    ) -> ToolResult:
        return await self._execute_recorded(request, approval=approval)

    async def _execute_recorded(
        self,
        request: ToolRequest,
        *,
        approval: ToolApproval | None,
        workspace_controller: bool = False,
    ) -> ToolResult:
        request = ToolRequest.model_validate(request)
        if len(request.model_dump_json().encode()) > 2 * 1024 * 1024:
            raise ValueError("request exceeds size limit")
        self.journal.begin_operation(request.request_id)
        try:
            return await self._execute(request, approval, workspace_controller=workspace_controller)
        except EventWriteError:
            self.journal.invalidate()
            raise
        finally:
            self.journal.end_operation()

    async def _execute(
        self,
        request: ToolRequest,
        approval: ToolApproval | None,
        *,
        workspace_controller: bool = False,
    ) -> ToolResult:
        revision = Revision.model_validate(self.read_revision())
        self._sync_workspace()
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
            and not (isinstance(invocation, Git) and self.workspace is not None)
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
            workspace_controller=workspace_controller,
        )
        if (
            self.workspace is not None
            and not self.workspace.prepared
            and not (
                workspace_controller
                and isinstance(invocation, WorkspaceOperation)
                and invocation.operation == "prepare"
            )
        ):
            decision = PolicyDecision(decision=Decision.DENY, reason="prepare the workspace first")
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
                if isinstance(invocation, WorkspaceOperation):
                    if self.workspace is None:
                        raise ValueError("no managed workspace configured")
                    executed = True
                    output, artifacts = self.workspace.perform(
                        invocation,
                        self.journal,
                        approved_revision=self.spec.revision.workspace_revision,
                        timeout=self.timeout,
                    )
                    self._sync_workspace()
                elif isinstance(invocation, InitializationOperation):
                    raise ValueError("initialization is controller-only")
                elif isinstance(invocation, Git) and self.workspace is not None:
                    executed = True
                    output = (
                        self.workspace.status().model_dump_json()
                        if invocation.operation == "status"
                        else self.workspace.diff()
                    )
                    status, reason = "succeeded", "managed worktree inspected"
                elif isinstance(invocation, Read):
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
                                "only controller-managed worktrees are supported"
                            )
                        executed = True
                        outcome = await self._run_git(invocation)
                    else:
                        executed = True
                        outcome = await self.backend.run(
                            invocation.argv,
                            cwd=workdir,
                            root=self.root,
                            task=self.task,
                            protected=self.journal.directory,
                            timeout=self.timeout,
                            max_output_bytes=self.max_output_bytes,
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
            except TimeoutError:
                status, reason = "timed_out", "process timeout"
            except (OSError, ValueError) as error:
                status = "failed"
                reason = (
                    str(error)
                    if isinstance(invocation, WorkspaceOperation)
                    else f"{type(error).__name__}: operation failed; inspect actual state"
                )
        if isinstance(invocation, WorkspaceOperation) and self.workspace is not None and executed:
            artifacts = tuple(self.workspace.operation_artifacts)
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

    async def _run_git(self, invocation: Git) -> ProcessOutcome:
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
            f"core.hooksPath={os.devnull}",
            "-c",
            f"core.attributesFile={os.devnull}",
            "-c",
            f"core.excludesFile={os.devnull}",
            "-c",
            f"safe.directory={self.root}",
        )
        deadline = monotonic() + self.timeout

        async def run(*command: str) -> ProcessOutcome:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Git operation deadline reached")
            return await self.backend.run(
                (*args, *command),
                cwd=self.root,
                root=self.root,
                task=self.task,
                protected=self.journal.directory,
                timeout=remaining,
                max_output_bytes=self.max_output_bytes,
                git=True,
            )

        if invocation.operation == "status":
            return await run("status", "--porcelain=v1", "--untracked-files=normal")
        # Worktree read denials cannot protect deleted files still in the index.
        # Inspect names only, then use the same policy as native reads, including
        # case/Unicode equivalence and control directories at any depth.
        inventory = await run("ls-files", "--cached", "-z")
        if inventory.exit_code != 0 or inventory.timed_out:
            return ProcessOutcome(
                inventory.exit_code,
                "Git index inventory failed; diff not executed",
                inventory.truncated,
                inventory.timed_out,
            )
        if (
            inventory.truncated
            or (inventory.output and not inventory.output.endswith("\0"))
            or "\ufffd" in inventory.output
        ):
            raise FileBoundaryError("Git index inventory incomplete; diff not executed")
        paths = tuple(
            f":(top,literal){path}"
            for path in dict.fromkeys(inventory.output.split("\0")[:-1])
            if path_permitted(path, self.task.scope)
        )
        if not paths:
            return ProcessOutcome(0, "", False, False)
        self.journal.check_writable()
        return await run(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--no-color",
            "--submodule=short",
            "--",
            *paths,
        )
