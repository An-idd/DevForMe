"""Attach recorded supplemental rules to a fresh, versioned project context."""

from hashlib import sha256

from ..core.models import DomainModel
from ..core.review import ReviewGuidance, ReviewRulesOperation
from ..core.tools import ToolRequest
from ..runtime.review_rules import BUNDLE_SHA256
from ..tools.runtime import ToolRuntime
from .coder import TaskContextPack


class ReviewGuidanceContext(DomainModel):
    project: TaskContextPack
    guidance: ReviewGuidance
    request_event_id: str
    precedence: str = "Explicit project/user rules override supplemental external guidance."

    @property
    def revision(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


async def attach_review_guidance(
    runtime: ToolRuntime,
    context: TaskContextPack,
    paths: tuple[str, ...],
    *,
    bundle_sha256: str = BUNDLE_SHA256,
) -> ReviewGuidanceContext:
    """Caller supplies a fresh project context; no Coder conversation is accepted."""
    if (
        context.task_id != runtime.task.id
        or context.plan_revision != runtime.spec.plan_revision
        or context.revision != runtime.read_revision()
    ):
        raise ValueError("review context is stale or belongs to another task")
    result = await runtime.execute(
        ToolRequest(
            request_id=f"review-rules-{runtime.journal.next_sequence}",
            invocation=ReviewRulesOperation(paths=paths, bundle_sha256=bundle_sha256),
        )
    )
    if result.status != "succeeded" or result.truncated:
        raise ValueError("required review guidance unavailable; inspect tool records")
    guidance = ReviewGuidance.model_validate_json(result.output)
    selected = tuple(path for group in guidance.groups for path in group.paths)
    if (
        guidance.bundle_sha256 != bundle_sha256
        or len(selected) != len(paths)
        or set(selected) != set(paths)
        or runtime.read_revision() != context.revision
    ):
        raise ValueError("review guidance coverage or revision changed")
    return ReviewGuidanceContext(
        project=context, guidance=guidance, request_event_id=result.request_event_id
    )
