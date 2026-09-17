"""Recorded Reviewer dispatch and conversion of model judgments to gate inputs."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from ..agents.reviewer import DIMENSIONS, ReviewDraft, review_context
from ..context.coder import build_context
from ..context.reviewer import ReviewGuidanceContext, attach_review_guidance
from ..core.knowledge import KnowledgeSnapshot, SourceFile
from ..core.models import DomainModel, Evidence, EvidenceStatus, TaskSpec
from ..core.paths import path_permitted
from ..core.planning import PlanVersion
from ..core.provider import ModelProvider, ProviderError
from ..core.tools import Git, Read, ToolRequest
from ..core.workflow import QualityGate, ReviewResult, Revision
from ..core.workspace import WorkspaceStatus
from ..providers.runtime import ModelRuntime
from ..runtime.review_rules import BUNDLE_SHA256
from .execution import ExecutionRuntime
from .runtime import ToolRuntime


def reviewer_identity(provider: ModelProvider | None) -> str:
    return json.dumps(
        None
        if provider is None
        else {
            "provider": provider.name,
            "generation": provider.settings.model_dump(mode="json"),
            "endpoint_sha256": getattr(provider, "endpoint_sha256", None),
            "rules_sha256": BUNDLE_SHA256,
        },
        sort_keys=True,
    )


class ReviewerContext(DomainModel):
    project: ReviewGuidanceContext
    task: TaskSpec
    evidence: tuple[Evidence, ...]
    baseline_evidence: tuple[Evidence, ...]
    refactor: bool
    invariant_criterion_ids: tuple[str, ...]
    diff: str
    required_paths: tuple[str, ...]
    applicable_rule_ids: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    current_sources: tuple[SourceFile, ...]


class ReviewRecord(DomainModel):
    phase: Literal["task", "final"]
    context_sha256: str | None
    draft: ReviewDraft | None
    result: ReviewResult


class ReviewRunner:
    def __init__(
        self,
        owner: ExecutionRuntime,
        plan: PlanVersion,
        knowledge: KnowledgeSnapshot,
        provider: ModelProvider,
        runtime: Callable[[str], ToolRuntime],
    ) -> None:
        self.owner, self.plan, self.knowledge = owner, plan, knowledge
        self.provider, self.runtime = provider, runtime
        self.identity = reviewer_identity(provider)
        self.baseline_evidence: tuple[Evidence, ...] = ()

    async def review(
        self, task: TaskSpec, revision: Revision, evidence: tuple[Evidence, ...]
    ) -> ReviewResult:
        return await self.check(task, revision, evidence, phase="task")

    async def check(
        self,
        task: TaskSpec,
        revision: Revision,
        evidence: tuple[Evidence, ...],
        *,
        phase: Literal["task", "final"],
    ) -> ReviewResult:
        runtime = self.runtime(task.id)
        context = draft = None
        status, summary = EvidenceStatus.UNAVAILABLE, "Reviewer prerequisites unavailable"
        try:
            if runtime.read_revision() != revision:
                raise ValueError("review snapshot changed before dispatch")
            if not QualityGate().verification(task, revision, evidence).passed:
                raise ValueError("review requires complete current verification evidence")
            if reviewer_identity(self.provider) != self.identity:
                raise ValueError("review configuration changed")
            if self.owner.journal.model_calls >= runtime.spec.max_model_calls:
                raise ValueError("session model budget exhausted")
            context = await self._context(runtime, task, evidence)
            if runtime.read_revision() != revision:
                raise ValueError("review context changed")
            self.owner.record("review_context_built", context, revision, task_id=task.id)

            def current() -> Revision:
                actual = runtime.read_revision()
                if actual != revision or reviewer_identity(self.provider) != self.identity:
                    raise ValueError("review inputs changed")
                if self.owner.journal.model_calls >= runtime.spec.max_model_calls:
                    raise ValueError("session model budget exhausted")
                return actual

            model = ModelRuntime(
                self.provider, journal=self.owner.journal, task_id=task.id, read_revision=current
            )
            draft = await review_context(context, model)
            if (
                runtime.read_revision() != revision
                or reviewer_identity(self.provider) != self.identity
            ):
                raise ValueError("review inputs changed during model call")
            complete = (
                len(draft.reviewed_paths) == len(context.required_paths)
                and set(draft.reviewed_paths) == set(context.required_paths)
                and len(draft.assessed_rule_ids) == len(context.applicable_rule_ids)
                and set(draft.assessed_rule_ids) == set(context.applicable_rule_ids)
                and len(draft.checked_dimensions) == len(DIMENSIONS)
                and set(draft.checked_dimensions) == set(DIMENSIONS)
            )
            status = (
                EvidenceStatus.INCONCLUSIVE
                if not complete or draft.conclusion == "inconclusive"
                else EvidenceStatus.FAILED
                if draft.conclusion == "changes_requested" or draft.blocking or draft.major
                else EvidenceStatus.PASSED
            )
            summary = draft.summary if complete else "Reviewer coverage is incomplete"
        except ProviderError as error:
            status, summary = (
                EvidenceStatus.UNAVAILABLE,
                "Reviewer model failed: " + error.failure.code,
            )
        except (OSError, ValueError):
            status, summary = (
                EvidenceStatus.INCONCLUSIVE,
                "Review context/configuration unavailable or changed",
            )
        # Journal failures and cancellation intentionally propagate; never fabricate a result.
        sequence = self.owner.journal.next_sequence
        result = ReviewResult(
            task_id=task.id,
            revision=revision,
            status=status,
            summary=summary,
            blocking=draft.blocking if draft is not None else (),
            major=draft.major if draft is not None else (),
            minor=draft.minor if draft is not None else (),
            source=f"{self.owner.session_id}:{sequence}",
            timestamp=datetime.now(UTC),
        )
        self.owner.record(
            "review_recorded",
            ReviewRecord(
                phase=phase,
                context_sha256=sha256(context.model_dump_json().encode()).hexdigest()
                if context is not None
                else None,
                draft=draft,
                result=result,
            ),
            revision,
            task_id=task.id,
            source=result.source,
        )
        return result

    async def _context(
        self, runtime: ToolRuntime, task: TaskSpec, evidence: tuple[Evidence, ...]
    ) -> ReviewerContext:
        project = await build_context(runtime, self.plan, self.knowledge, review=True)
        if any(s.truncated for s in (*project.current_sources, *project.knowledge_sources)):
            raise ValueError("review project context is incomplete")

        async def read(operation: Git | Read) -> str:
            result = await runtime.execute(
                ToolRequest(
                    request_id=f"review-input-{runtime.journal.next_sequence}", invocation=operation
                )
            )
            if result.status != "succeeded" or result.truncated:
                raise ValueError("review input unavailable or truncated")
            return result.output

        status = WorkspaceStatus.model_validate_json(await read(Git(operation="status")))
        if status.current_revision != project.revision.workspace_revision:
            raise ValueError("review status changed")
        diff = await read(Git(operation="diff"))
        assert runtime.workspace is not None
        snapshot = runtime.workspace.snapshot()
        actual = {f.path: f for f in snapshot.files}
        selected = set(status.added + status.modified + status.deleted)
        selected.update(p for p in actual if path_permitted(p, task.scope, write=True))
        selected.update(s.path for s in project.current_sources)
        paths = tuple(sorted(selected))
        if not paths or len(paths) > 64:
            raise ValueError("review input scope requires refinement")
        current = {s.path: s for s in project.current_sources}
        for path in paths:
            if path not in actual:
                if path not in status.deleted:
                    raise ValueError("review source disappeared")
                continue
            if path not in current:
                current[path] = SourceFile(
                    path=path,
                    sha256=actual[path].sha256,
                    role="code",
                    text=await read(Read(path=path)),
                )
        guidance = await attach_review_guidance(runtime, project, paths)
        context = ReviewerContext(
            project=guidance,
            task=task,
            evidence=evidence,
            baseline_evidence=self.baseline_evidence,
            refactor=self.plan.draft.refactor,
            invariant_criterion_ids=self.plan.draft.invariant_criterion_ids,
            diff=diff,
            required_paths=paths,
            applicable_rule_ids=tuple(e.id for e in project.entries if e.entry.authority == "rule"),
            deleted_paths=tuple(sorted(status.deleted)),
            current_sources=tuple(current[p] for p in sorted(current)),
        )
        if len(context.model_dump_json().encode()) > 512 * 1024:
            raise ValueError("review input exceeds 512 KiB; refine scope")
        return context
