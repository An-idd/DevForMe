"""Thin CLI: argument parsing and output around application flows."""

import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

from .application.initialization import InitializationResult, initialize
from .core.models import ScopePolicy
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
    args = parser.parse_args(argv)

    async def run() -> InitializationResult:
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
        parser.exit(2, f"agent init: {safe_error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "agent init: interrupted; inspect the journal before retrying\n")
    if args.json:
        print(result.model_dump_json())
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
