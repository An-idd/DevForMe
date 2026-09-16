"""Controller-owned plan history using the initialization lock and durable IO."""

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..core.knowledge import KnowledgeSnapshot
from ..core.models import ScopePolicy
from ..core.planning import PlanIndex, PlanningOperation, PlanningRevision, PlanVersion
from ..core.workspace import WorkspaceSnapshot
from ..runtime import _fileio as io
from ..runtime._snapshots import capture
from ..runtime.filesystem import SafeFiles
from ..session.records import Sanitizer
from .initialization import EXCLUDED, InitializationRuntime, KnowledgeState, digest, user_text


class PlanningRuntime(InitializationRuntime):
    """Reuse the existing controller boundary; no new Agent capabilities."""

    def __init__(
        self, root: Path, scope: ScopePolicy, *, sanitizer: Sanitizer | None = None
    ) -> None:
        super().__init__(root, scope, sanitizer=sanitizer)
        self.session_id = "plan-" + uuid4().hex
        self.revision = PlanningRevision(
            context_revision="not-yet-loaded", workspace_revision="not-yet-captured"
        )

    @classmethod
    @contextmanager
    def read_only(cls, root: Path) -> Iterator["PlanningRuntime"]:
        runtime = cls(root, ScopePolicy())
        with runtime.files.directory() as fd:
            try:
                runtime._agent_fd = io.directory_at(fd, ".agent")
            except FileNotFoundError:
                raise ValueError("no saved plan; run agent plan first") from None
        try:
            yield runtime
        finally:
            os.close(runtime._agent_fd)
            runtime._agent_fd = None

    def current(self) -> tuple[PlanVersion | None, bytes | None]:
        raw = self._optional("plan.json")
        if raw is None:
            return None, None
        index = PlanIndex.model_validate_json(raw)
        name = f"plan-{index.plan_id}-v{index.version}.json"
        payload = self._optional(name)
        if payload is None:
            raise ValueError("current plan version is missing")
        plan = PlanVersion.model_validate_json(payload)
        if plan.revision != index.revision or digest(payload) != index.revision:
            raise ValueError("immutable plan or index changed")
        if plan.plan_id != index.plan_id or plan.version != index.version:
            raise ValueError("plan identity disagrees with its index")
        return plan, raw

    def read_input(self, path: str) -> str:
        def action() -> str:
            # Explicit import of a controller plan is allowed, never arbitrary control paths.
            parts = path.split("/")
            if (
                len(parts) == 2
                and parts[0] == ".agent"
                and re.fullmatch(r"plan-[a-f0-9]{32}-v[1-9][0-9]*\.json", parts[1])
            ):
                raw = self._optional(parts[1])
                if raw is None:
                    raise ValueError("imported plan does not exist")
                return raw.decode("utf-8")
            return SafeFiles(self.root, self.scope, max_bytes=8 * 1024 * 1024).read(path)

        return self._record(PlanningOperation(operation="input", input_sha256=digest(path)), action)

    def snapshot(self) -> WorkspaceSnapshot:
        return self._record(
            PlanningOperation(
                operation="snapshot", input_sha256=digest(self.scope.model_dump_json())
            ),
            lambda: capture(self.root, self.scope, EXCLUDED)[0],
        )

    def check_knowledge(self, state: KnowledgeState) -> KnowledgeSnapshot:
        if state.snapshot is None:
            raise ValueError("initialize project knowledge before planning")
        snapshot = state.snapshot
        expected_user = next(
            (source.sha256 for source in snapshot.repository.sources if source.role == "user"),
            digest(user_text(snapshot.user_prefix, snapshot.user_suffix)),
        )
        if digest(user_text(state.prefix, state.suffix)) != expected_user:
            raise ValueError("user guidance changed; refresh project knowledge")
        if (self._optional("project.md"), self._optional("project.json")) != (
            state.guide,
            state.metadata,
        ):
            raise ValueError("project knowledge changed during planning; refresh first")
        current = self._capture(snapshot.repository.focus, snapshot.repository)
        expected = snapshot.repository.model_copy(
            update={"sources": tuple(s for s in snapshot.repository.sources if s.role != "user")}
        )
        if current != expected:
            raise ValueError("project sources changed; refresh project knowledge")
        return snapshot

    def inspect_knowledge(self, state: KnowledgeState) -> KnowledgeSnapshot:
        return self._record(
            PlanningOperation(operation="inspect", input_sha256=digest(state.metadata or b"")),
            lambda: self.check_knowledge(state),
        )

    def publish_plan(
        self,
        plan: PlanVersion,
        state: KnowledgeState,
        expected_index: bytes | None,
        summary: str,
        imported: PlanVersion | None = None,
    ) -> str:
        def action() -> str:
            knowledge = self.check_knowledge(state)
            if plan.context_revision != knowledge.revision:
                raise ValueError("plan context does not match project knowledge")
            if capture(self.root, self.scope, EXCLUDED)[0] != plan.workspace:
                raise ValueError("workspace changed during planning; regenerate the plan")
            if self._optional("plan.json") != expected_index:
                raise ValueError("current plan changed during planning")
            data = plan.model_dump_json().encode()
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("plan exceeds the 8 MiB persistence limit")
            archives = (
                ((f"imported-plan-{imported.revision}.json", imported.model_dump_json().encode()),)
                if imported is not None
                else ()
            )
            if imported is not None and plan.imported_from != imported.revision:
                raise ValueError("import provenance does not match the retained original")
            for name, content in (
                *archives,
                (plan.filename, data),
                (plan.filename.removesuffix(".json") + ".md", summary.encode()),
            ):
                self.journal.check_writable()
                existing = self._optional(name)
                if existing is None:
                    self._replace(name, content, None)
                elif existing != content:
                    raise ValueError("plan version already exists; inspect its publication journal")
            self.journal.check_writable()
            index = PlanIndex(plan_id=plan.plan_id, version=plan.version, revision=plan.revision)
            self._replace("plan.json", index.model_dump_json().encode(), expected_index)
            return plan.revision

        return self._record(
            PlanningOperation(operation="publish", input_sha256=plan.revision), action
        )
