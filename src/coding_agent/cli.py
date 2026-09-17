"""Thin CLI: argument parsing and output around application flows."""

import argparse
import asyncio
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from .application.evidence import EvidenceLedger, inspect_evidence
from .application.execution import RunResult
from .application.execution import run as execute_plan
from .application.initialization import InitializationResult, initialize
from .application.planning import PlanInspection, PlanningResult, inspect_plan, plan, render_graph
from .core.models import ScopePolicy
from .core.planning import PlanSettings
from .core.provider import GenerationSettings, ModelFailure, ProviderError
from .core.verification import VerificationSettings
from .core.workflow import EventWriteError
from .executors.codex import CodexSettings
from .providers.config import AssistantConfig, load_assistant_config
from .providers.openai import OpenAIProvider
from .providers.zhipu import ZhipuProvider
from .session.records import Sanitizer
from .tools.execution import RunHistory, inspect_execution


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create or inspect source-backed project knowledge")
    init.add_argument("path", nargs="?", type=Path, default=Path("."))
    init.add_argument(
        "--refresh", action="store_true", help="Refresh changed sources; preserve user guidance"
    )
    init.add_argument(
        "--rule", action="append", default=[], help="Add an explicit user rule (repeatable)"
    )
    init.add_argument(
        "--focus",
        action="append",
        default=[],
        help="Inspect a relative file/directory (repeatable)",
    )
    init.add_argument(
        "--forbid", action="append", default=[], help="Exclude a relative path pattern (repeatable)"
    )
    init.add_argument("--model", help="Opt in to one bounded OpenAI-assisted exploration call")
    init.add_argument("--json", action="store_true", help="Emit the application result as JSON")
    planning = commands.add_parser(
        "plan", help="Create a validated plan proposal without execution"
    )
    planning.add_argument("request", nargs="?")
    planning.add_argument("--path", type=Path, default=Path("."))
    planning.add_argument("--requirement", help="Repository-relative RequirementContract JSON")
    inputs = planning.add_mutually_exclusive_group()
    inputs.add_argument(
        "--draft", help="Repository-relative PlanDraft JSON for offline compilation"
    )
    inputs.add_argument(
        "--import", dest="import_path", help="Saved PlanVersion JSON; freshness required"
    )
    planning.add_argument("--reason", help="Required explanation when revising an existing plan")
    planning.add_argument("--new", action="store_true", help="Start a separate plan history")
    planning.add_argument("--refresh", action="store_true", help="Refresh project knowledge")
    planning.add_argument("--focus", action="append", default=[])
    planning.add_argument("--forbid", action="append", default=[])
    planning.add_argument(
        "--allow", action="append", default=[], help="Optional write scope ceiling"
    )
    planning.add_argument("--mode", choices=("fast", "standard", "strict"), default="standard")
    planning.add_argument("--max-attempts", type=int, default=30)
    planning.add_argument("--model", help="Opt in to one recorded Planner call")
    planning.add_argument("--json", action="store_true")
    for name in ("status", "graph"):
        inspect = commands.add_parser(
            name, help="Read the current saved plan without initialization"
        )
        inspect.add_argument("--path", type=Path, default=Path("."))
        inspect.add_argument("--json", action="store_true")
        if name == "status":
            inspect.add_argument("--session", help="Inspect a recorded execution")
    execution = commands.add_parser("run", help="Execute an approved plan in a retained workspace")
    execution.add_argument("--path", type=Path, default=Path("."))
    execution.add_argument(
        "--workspace", type=Path, required=True, help="New directory outside the project"
    )
    execution.add_argument("--codex", type=Path, help="Native Codex executable")
    execution.add_argument(
        "--codex-home", type=Path, default=os.environ.get("CODING_AGENT_CODEX_HOME")
    )
    execution.add_argument("--model", default=os.environ.get("CODING_AGENT_CODEX_MODEL"))
    execution.add_argument(
        "--verification-runtime",
        action="append",
        type=Path,
        default=[],
        help="Trusted standalone runtime directory outside the project (repeatable)",
    )
    execution.add_argument(
        "--review-model", help="Opt in to independent API review; separate from Coder model"
    )
    execution.add_argument(
        "--review-env-file", type=Path, help="Reviewer CODING_AGENT_* configuration"
    )
    execution.add_argument("--approve", help="Exact fingerprint printed by run preview")
    execution.add_argument("--json", action="store_true")
    for name in ("diff", "history", "evidence"):
        history = commands.add_parser(
            name, help="Inspect persisted execution records without running tools"
        )
        history.add_argument("--path", type=Path, default=Path("."))
        history.add_argument("--session", help="run-<plan-id>; defaults to current plan")
        history.add_argument("--json", action="store_true")
    for assisted in (init, planning):
        assisted.add_argument(
            "--env-file", type=Path, help="Opt in using CODING_AGENT_* from this UTF-8 dotenv file"
        )
    args = parser.parse_args(argv)
    config: AssistantConfig | None = None
    secrets = tuple(
        filter(None, (os.environ.get("OPENAI_API_KEY"), os.environ.get("CODING_AGENT_API_KEY")))
    )

    def model_provider(
        token_limit: int, *, model: str | None = None
    ) -> OpenAIProvider | ZhipuProvider:
        settings = GenerationSettings(
            model=config.model if config else model or args.model,
            max_output_tokens=token_limit,
            timeout_seconds=60,
        )
        if config is None:
            return OpenAIProvider(settings)
        if config.provider == "zhipu":
            return ZhipuProvider(settings, api_key=config.api_key, api_url=config.api_url)
        if config.api_url != "https://api.openai.com/v1/responses":
            raise ProviderError(ModelFailure(code="configuration"))
        return OpenAIProvider(settings, api_key=config.api_key)

    async def run() -> (
        InitializationResult
        | PlanningResult
        | PlanInspection
        | RunResult
        | RunHistory
        | EvidenceLedger
    ):
        if args.command == "evidence":
            return inspect_evidence(args.path, args.session)
        if args.command in {"diff", "history"}:
            return inspect_execution(args.path, args.session)
        if args.command == "status":
            try:
                return inspect_execution(args.path, args.session)
            except FileNotFoundError:
                if args.session is not None:
                    raise
        if args.command == "run":
            executable = args.codex or shutil.which("codex")
            if executable is None or args.codex_home is None or not args.model:
                raise ValueError(
                    "run requires native Codex, --codex-home and --model "
                    "(or CODING_AGENT_CODEX_HOME/MODEL)"
                )
            options = dict(
                root=args.path,
                workspace=args.workspace,
                verification=VerificationSettings(
                    runtime_roots=tuple(p.resolve(strict=True) for p in args.verification_runtime),
                ),
                approve=args.approve,
                config=CodexSettings(
                    executable=Path(executable).resolve(strict=True),
                    home=Path(args.codex_home).resolve(),
                    model=args.model,
                ),
                secrets=secrets,
            )
            if args.review_model or config:
                async with model_provider(8192, model=args.review_model) as provider:
                    return await execute_plan(reviewer=provider, **options)
            return await execute_plan(**options)
        if args.command in {"status", "graph"}:
            return inspect_plan(args.path)
        if args.command == "plan":
            options = dict(
                draft_path=args.draft,
                requirement_path=args.requirement,
                import_path=args.import_path,
                settings=PlanSettings(
                    scope=ScopePolicy(allowed=tuple(args.allow), forbidden=tuple(args.forbid)),
                    mode=args.mode,
                    max_total_attempts=args.max_attempts,
                ),
                focus=tuple(args.focus),
                refresh=args.refresh,
                reason=args.reason,
                new_plan=args.new,
                secrets=secrets,
            )
            if args.model or config:
                async with model_provider(16384) as provider:
                    return await plan(args.path, args.request, provider=provider, **options)
            return await plan(args.path, args.request, **options)
        scope = ScopePolicy(forbidden=tuple(args.forbid))
        if args.model or config:
            async with model_provider(8192) as provider:
                return await initialize(
                    args.path,
                    refresh=args.refresh,
                    rules=tuple(args.rule),
                    focus=tuple(args.focus),
                    scope=scope,
                    provider=provider,
                    secrets=secrets,
                )
        return await initialize(
            args.path,
            refresh=args.refresh,
            rules=tuple(args.rule),
            focus=tuple(args.focus),
            scope=scope,
            secrets=secrets,
        )

    try:
        if args.command == "run" and args.review_env_file is not None:
            config = load_assistant_config(args.review_env_file, model=args.review_model)
            secrets = (*secrets, config.api_key.get_secret_value())
        if getattr(args, "env_file", None) is not None:
            config = load_assistant_config(args.env_file, model=args.model)
            secrets = (*secrets, config.api_key.get_secret_value())
        result = asyncio.run(run())
    except (OSError, ValueError, ProviderError, EventWriteError) as error:
        # Provider errors contain a fixed category; arbitrary SDK bodies are never exposed.
        safe_error = Sanitizer(secrets).text(str(error))
        parser.exit(2, f"agent {args.command}: {safe_error}\n")
    except KeyboardInterrupt:
        parser.exit(
            130, f"agent {args.command}: interrupted; inspect the journal before retrying\n"
        )
    if args.json:
        print(result.model_dump_json())
    elif isinstance(result, EvidenceLedger):
        for view in result.checks:
            report = view.record
            for evidence in report.evidence:
                print(
                    f"{report.phase} {evidence.task_id} {evidence.criterion_id}/"
                    f"{evidence.check_id}: {evidence.status} "
                    f"({'current' if view.current else 'historical or unavailable snapshot'})\n"
                    f"  source={evidence.source} snapshot={evidence.workspace_revision}"
                )
        if not result.checks:
            print("No recorded verification evidence")
        print("Requirement complete: false")
    elif isinstance(result, RunResult):
        print(f"{result.status}: {result.reason}")
        if result.spec is not None:
            print(
                f"Session limits: {result.spec.max_tool_calls} tool requests, "
                f"{result.spec.max_model_calls} model segments, "
                f"{result.spec.max_total_attempts} Coder attempts"
            )
        if result.worktree:
            print("Worktree: " + result.worktree)
        if result.journal:
            print("Journal: " + result.journal)
        print("Requirement complete: false")
    elif isinstance(result, RunHistory):
        if args.command == "diff":
            print("Recorded execution diff; later manual edits are not included.")
            print(
                result.diff
                if result.diff_recorded
                else "No complete diff snapshot recorded; inspect history and retained workspace."
            )
        elif args.command == "history":
            for event in result.records.events:
                print(f"{event.sequence} {event.kind} {event.task_id or '-'}: {event.reason}")
        else:
            print(f"Session {result.session_id}: {result.status}")
            for task_id, state in result.states.items():
                print(f"{task_id}: {state}")
        if result.records.unresolved or result.records.incomplete_tail:
            print("Incomplete records; inspect actual state before recovery.")
        print("Requirement complete: false")
    elif isinstance(result, PlanningResult):
        print(result.summary or "\n".join(result.blockers))
        if result.path:
            print("Saved: " + result.path)
        if result.journal:
            print("Journal: " + result.journal)
    elif isinstance(result, PlanInspection):
        print(
            render_graph(result.plan)
            if args.command == "graph"
            else (
                f"Plan {result.plan.plan_id} v{result.plan.version}: {result.status}\n"
                f"Authorization: {result.authorization}; execution: not tracked\n"
                f"Pending milestones: {', '.join(result.plan.pending_milestones) or 'none'}"
            )
        )
        for blocker in result.blockers:
            print("Blocked: " + blocker)
    else:
        print(
            f"{result.status}: {result.guide}\n"
            f"revision: {result.revision}\njournal: {result.journal}"
        )
        if result.changed_sources:
            print("Changed: " + ", ".join(result.changed_sources))
        for question in result.questions:
            label = (
                "Answered: "
                if question.answer is not None
                else "Required: "
                if question.required
                else "Open: "
            )
            print(label + question.text)
            if question.answer is not None:
                print("Answer: " + question.answer)
        if result.status == "stale":
            print("Sources changed. Run agent init --refresh before planning or execution.")
        if result.status == "blocked":
            print(
                "Answer required questions in project instructions or --rule, then "
                "reassess with --refresh --model MODEL. Offline refresh preserves open questions."
            )
    if isinstance(result, RunResult):
        return (
            130 if result.status == "cancelled" else 0 if result.status == "tasks_verified" else 2
        )
    return 2 if result.status in {"stale", "blocked", "incomplete"} else 0
