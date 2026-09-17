"""Executable resolution inside explicitly trusted, controller-configured runtimes."""

import os
from pathlib import Path

from ..core.models import TaskSpec
from ..core.verification import VerificationSettings
from .process import ProcessOutcome, default_process_backend


class VerificationProcess:
    def __init__(self, settings: VerificationSettings) -> None:
        self.settings = settings
        self.backend = default_process_backend(
            runtime_roots=settings.runtime_roots, verification=True
        )

    def denial(self, task: TaskSpec, *, git: bool = False) -> str | None:
        if not self.settings.runtime_roots:
            return "verification runtime is not configured"
        return self.backend.denial(task, git=git)

    def resolve(self, argv: tuple[str, ...]) -> tuple[str, ...]:
        name = Path(argv[0])
        roots = self.settings.runtime_roots
        candidates: tuple[Path, ...]
        if name.is_absolute():
            candidates = (name,)
        elif name.name == argv[0] and argv[0] not in {".", ".."}:
            names = (argv[0], argv[0] + ".exe") if os.name == "nt" else (argv[0],)
            candidates = tuple(
                root / folder / executable
                for root in roots
                for folder in ("", "bin", "Scripts")
                for executable in names
            )
        else:
            raise ValueError("verification executable must be a runtime name or absolute path")
        for candidate in candidates:
            if candidate.is_file():
                executable = candidate.resolve(strict=True)
                if any(executable.is_relative_to(root) for root in roots):
                    return (str(executable), *argv[1:])
        raise ValueError("verification executable unavailable in configured runtime")

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        root: Path,
        task: TaskSpec,
        protected: Path,
        timeout: float,
        max_output_bytes: int,
        git: bool = False,
    ) -> ProcessOutcome:
        return await self.backend.run(
            self.resolve(argv),
            cwd=cwd,
            root=root,
            task=task,
            protected=protected,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            git=git,
        )
