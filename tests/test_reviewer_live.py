"""Opt-in real Reviewer and Windows verifier smoke; synthetic Coder, at most four API calls."""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from test_execution import planned as planned
from test_execution import scripted as scripted
from test_planning import project as project
from test_windows import python_runtime as python_runtime

from coding_agent.application import execution
from coding_agent.application.evidence import inspect_evidence
from coding_agent.application.initialization import initialize
from coding_agent.application.planning import plan
from coding_agent.core.planning import PlanDraft, PlanSettings
from coding_agent.core.provider import GenerationSettings
from coding_agent.core.tools import Patch, ToolRequest
from coding_agent.core.verification import VerificationSettings
from coding_agent.core.workflow import CoderResult
from coding_agent.providers.config import load_assistant_config
from coding_agent.providers.openai import OpenAIProvider
from coding_agent.providers.zhipu import ZhipuProvider
from coding_agent.session.records import inspect_journal
from coding_agent.tools.execution import read_artifact

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(sys.platform != "win32", reason="real Windows verification runtime"),
    pytest.mark.skipif(
        os.environ.get("CODING_AGENT_REVIEW_LIVE") != "1",
        reason="requires explicit CODING_AGENT_REVIEW_LIVE=1",
    ),
]


@pytest.mark.parametrize("case", ["preserve", "regression"])
def test_live_reviewer_with_real_verification(planned, scripted, python_runtime, case):
    config = load_assistant_config(Path(os.environ.get("CODING_AGENT_REVIEW_ENV_FILE", ".env")))
    settings = GenerationSettings(model=config.model, max_output_tokens=8192, timeout_seconds=60)
    root, _, saved, coder, workspace = planned
    # Deliberately weak regression test demonstrates that passing tests cannot override review.
    assertion = (
        "self.assertEqual(service.run(), 1)"
        if case == "preserve"
        else "self.assertIn(service.run(), (1, 2))"
    )
    (root / "test_service.py").write_text(
        "import unittest\nimport service\n\n"
        "class ServiceTest(unittest.TestCase):\n"
        f"    def test_response(self):\n        {assertion}\n",
        encoding="utf-8",
    )
    asyncio.run(initialize(root, refresh=True))
    raw = saved.draft.model_dump(mode="json")
    raw["tasks"][0]["task"]["scope"]["allowed"].append("test_service.py")
    for acceptance in (raw["acceptance"], raw["tasks"][0]["task"]["acceptance"]):
        for check in acceptance["checks"]:
            if check["evidence_type"] == "test":
                check["command"] = ["python", "-m", "unittest", "discover", "-v"]
    asyncio.run(
        plan(
            root,
            draft=PlanDraft.model_validate(raw),
            settings=PlanSettings(max_review_fixes=0),
            new_plan=True,
        )
    )
    content = (
        "# Service entry point.\ndef run(): return 1\n"
        if case == "preserve"
        else "def run(): return 2\n"
    )

    async def implement(adapter):
        result = await adapter.runtime.execute(
            ToolRequest(
                request_id="live-fixture-patch",
                invocation=Patch(
                    path="service.py",
                    expected_sha256=hashlib.sha256(b"def run(): return 1\n").hexdigest(),
                    content=content,
                ),
            )
        )
        assert result.status == "succeeded"
        return CoderResult(outcome="implemented", summary="Synthetic fixture change applied")

    scripted[0][0] = implement

    async def scenario():
        provider = (
            ZhipuProvider(settings, api_key=config.api_key, api_url=config.api_url)
            if config.provider == "zhipu"
            else OpenAIProvider(settings, api_key=config.api_key)
        )
        async with provider:
            options = dict(
                config=coder,
                workspace=workspace,
                verification=VerificationSettings(runtime_roots=(python_runtime,)),
                reviewer=provider,
                secrets=(config.api_key.get_secret_value(),),
            )
            preview = await execution.run(root, **options)
            return await execution.run(root, approve=preview.fingerprint, **options)

    outcome = asyncio.run(scenario())
    assert outcome.journal
    journal_path = Path(outcome.journal)
    view = inspect_journal(journal_path)
    reviews = [
        json.loads(read_artifact(journal_path.parent, event.artifacts[0]))
        for event in view.events
        if event.kind == "review_recorded"
    ]
    calls = [e.model_result for e in view.events if e.model_result]
    report = {
        "case": case,
        "provider": config.provider,
        "model": config.model,
        "status": outcome.status,
        "reason": outcome.reason,
        "journal": str(journal_path),
        "reviews": reviews,
        "model_calls": [call.model_dump(mode="json") for call in calls],
    }
    report_path = root.parent / f"review-live-{case}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case": case, "status": outcome.status, "report": str(report_path)}))
    assert not view.unresolved
    assert not outcome.requirement_complete
    assert (root / "service.py").read_bytes() == b"def run(): return 1\n"
    assert outcome.worktree
    assert (Path(outcome.worktree) / "service.py").read_text(encoding="utf-8") == content
    assert calls and all(call.status == "completed" for call in calls), report_path
    checks = inspect_evidence(root).checks
    assert checks and all(check.record.result.exit_code == 0 for check in checks)
    assert all(e.passed for check in checks for e in check.record.evidence)
    if case == "preserve":
        assert outcome.status == "tasks_verified", report_path
        assert len(calls) == 3 and len(outcome.final_reviews) == 2
        assert all(review["result"]["status"] == "passed" for review in reviews)
    else:
        assert outcome.status != "tasks_verified", report_path
        assert len(calls) == 1
        result = reviews[0]["result"]
        assert result["status"] == "failed", report_path
        assert result["blocking"] or result["major"], report_path
        assert "service.py" in json.dumps(result), report_path
