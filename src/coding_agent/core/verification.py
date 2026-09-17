"""Controller configuration and persisted verification facts."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from .models import AcceptanceCheck, DomainModel, Evidence, TaskSpec
from .tools import ToolResult
from .workflow.contracts import Revision


class VerificationSettings(DomainModel):
    runtime_roots: tuple[Path, ...] = ()
    timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 30
    max_output_bytes: Annotated[int, Field(strict=True, ge=1024, le=1048576)] = 65536


class CheckExecution(DomainModel):
    phase: Literal["baseline", "task", "final"]
    task: TaskSpec
    check: AcceptanceCheck
    before: Revision
    after: Revision
    cwd: str
    executable_command: tuple[str, ...] | None
    settings: VerificationSettings
    version: ToolResult | None
    result: ToolResult | None
    evidence: tuple[Evidence, ...]
