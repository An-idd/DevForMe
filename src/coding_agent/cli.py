"""Thin CLI: argument parsing and output around application flows."""

import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

from .application.initialization import InitializationResult, initialize
from .application.planning import PlanInspection, PlanningResult, inspect_plan, plan, render_graph
from .core.models import ScopePolicy
from .core.planning import PlanSettings
from .core.provider import GenerationSettings, ProviderError
from .core.workflow import EventWriteError
from .providers.openai import OpenAIProvider
from .session.records import Sanitizer


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
    args = parser.parse_args(argv)

    async def run() -> InitializationResult | PlanningResult | PlanInspection:
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
                secrets=tuple(filter(None, (os.environ.get("OPENAI_API_KEY"),))),
            )
            if args.model:
                settings = GenerationSettings(
                    model=args.model, max_output_tokens=16384, timeout_seconds=60
                )
                async with OpenAIProvider(settings) as provider:
                    return await plan(args.path, args.request, provider=provider, **options)
            return await plan(args.path, args.request, **options)
        scope = ScopePolicy(forbidden=tuple(args.forbid))
        secrets = tuple(filter(None, (os.environ.get("OPENAI_API_KEY"),)))
        if args.model:
            settings = GenerationSettings(
                model=args.model, max_output_tokens=8192, timeout_seconds=60
            )
            async with OpenAIProvider(settings) as provider:
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
        result = asyncio.run(run())
    except (OSError, ValueError, ProviderError, EventWriteError) as error:
        # Provider errors contain a fixed category; arbitrary SDK bodies are never exposed.
        safe_error = Sanitizer(tuple(filter(None, (os.environ.get("OPENAI_API_KEY"),)))).text(
            str(error)
        )
        parser.exit(2, f"agent {args.command}: {safe_error}\n")
    except KeyboardInterrupt:
        parser.exit(
            130, f"agent {args.command}: interrupted; inspect the journal before retrying\n"
        )
    if args.json:
        print(result.model_dump_json())
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
    return 2 if result.status in {"stale", "blocked"} else 0
