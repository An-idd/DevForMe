import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_execution import planned as planned
from test_execution import project as project
from test_execution import scripted as scripted
from test_tools import tool_harness as tool_harness
from test_windows import python_runtime as python_runtime
from test_windows import windows_harness as windows_harness

from coding_agent.application import execution
from coding_agent.application.evidence import inspect_evidence
from coding_agent.application.initialization import initialize
from coding_agent.application.planning import plan
from coding_agent.cli import main
from coding_agent.core.models import AcceptanceCheck
from coding_agent.core.planning import PlanDraft, PlanSettings
from coding_agent.core.tools import Decision, Shell, ToolResult
from coding_agent.core.verification import VerificationSettings
from coding_agent.core.workflow import EventWriteError
from coding_agent.runtime.process import MacReadOnlyProcess, ProcessOutcome
from coding_agent.runtime.windows_process import WindowsReadOnlyProcess
from coding_agent.session.records import JsonlJournal
from coding_agent.tools.execution import inspect_execution
from coding_agent.tools.verification import classify


def result(output, code=0, **updates):
    now = datetime.now(UTC)
    return ToolResult.model_validate(
        dict(
            request_id="check",
            request_event_id="session:1",
            decision=Decision.ALLOW,
            status="succeeded" if code == 0 else "failed",
            executed=True,
            reason="process exited",
            exit_code=code,
            output=output,
            started_at=now,
            finished_at=now,
        )
        | updates
    )


@pytest.mark.parametrize(
    "command,output,code,expected",
    [
        (("python", "-m", "pytest"), "2 passed in 0.1s", 0, "passed"),
        (("python", "-m", "pytest"), "no tests ran in 0.1s", 5, "inconclusive"),
        (("python", "-m", "pytest"), "1 passed, 1 skipped in 0.1s", 0, "skipped"),
        (("python", "-m", "pytest"), "1 passed, 1 deselected in 0.1s", 0, "skipped"),
        (("python", "-m", "pytest"), "1 xfailed in 0.1s", 0, "skipped"),
        (("python", "-m", "pytest"), "1 failed in 0.1s", 1, "failed"),
        (("python", "-m", "pytest"), "1 failed in 0.1s", 0, "failed"),
        (("python", "-m", "pytest"), "something happened", 0, "inconclusive"),
        (("python", "-m", "pytest"), "No module named pytest", 1, "unavailable"),
        (("python", "-m", "unittest"), "Ran 2 tests in 0.1s\n\nOK\n", 0, "passed"),
        (("python", "-m", "unittest"), "Ran 0 tests in 0.1s\n\nOK\n", 0, "inconclusive"),
        (("python", "-m", "unittest"), "Ran 2 tests in 0.1s\nOK (skipped=1)\n", 0, "skipped"),
        (("python", "custom.py"), "2 passed", 0, "inconclusive"),
    ],
)
def test_test_outcome_requires_positive_collection(command, output, code, expected):
    check = AcceptanceCheck(
        id="unit", description="unit tests", evidence_type="test", command=command
    )
    assert classify(check, result(output, code))[0] == expected


@pytest.mark.parametrize("kind", ["build", "lint", "static_analysis", "command"])
def test_other_checks_record_exit_and_incomplete_output(kind):
    check = AcceptanceCheck(
        id="check", description="check", evidence_type=kind, command=("tool", "check")
    )
    assert classify(check, result(""))[0] == "passed"
    assert classify(check, result("", 1))[0] == "failed"
    assert classify(check, result("Read-only file system", 1))[0] == "unavailable"
    assert classify(check, result("ok", truncated=True))[0] == "inconclusive"
    assert classify(check, result("ok", status="timed_out"))[0] == "inconclusive"


@pytest.fixture
def simulated(tmp_path, monkeypatch):
    runtime = tmp_path / "trusted-runtime"
    runtime.mkdir()
    (runtime / ("python.exe" if sys.platform == "win32" else "python")).write_bytes(b"mock")
    calls = []
    outcomes = [ProcessOutcome(0, "1 passed in 0.1s", False, False)]
    action = [None]

    class Backend:
        def denial(self, task, *, git=False):
            return None

        async def run(self, argv, **kwargs):
            calls.append(argv)
            if argv[-1] == "--version":
                return ProcessOutcome(0, "pytest 8.4.0", False, False)
            if action[0] is not None:
                await action[0](kwargs)
            return outcomes[0]

    monkeypatch.setattr(
        "coding_agent.runtime.verification.default_process_backend", lambda **kwargs: Backend()
    )
    return VerificationSettings(runtime_roots=(runtime,)), calls, outcomes, action


def run(planned, settings, **kwargs):
    root, _, _, config, workspace = planned
    return asyncio.run(
        execution.run(
            root,
            config=config,
            workspace=workspace,
            verification=settings,
            **kwargs,
        )
    )


def test_task_evidence_is_recorded_and_review_still_blocks(planned, scripted, simulated, capsys):
    settings, calls, _, _ = simulated
    preview = run(planned, settings)
    assert not calls
    outcome = run(planned, settings, approve=preview.fingerprint)
    assert outcome.status == "blocked" and not outcome.requirement_complete
    evidence = outcome.workflow.evidence
    assert evidence and all(e.passed for e in evidence if e.evidence_type != "review")
    assert any(e.evidence_type == "review" and e.status == "unavailable" for e in evidence)
    assert "review" in outcome.reason.lower()
    ledger = inspect_evidence(planned[0])
    assert len(ledger.checks) == 1 and ledger.checks[0].current
    report = ledger.checks[0].record
    assert report.version.output == "pytest 8.4.0"
    assert report.result.exit_code == 0 and report.result.artifacts
    assert report.cwd == outcome.worktree
    assert report.before == report.after == outcome.workflow.revision
    assert report.executable_command[1:] == report.check.command[1:]
    before = set((planned[0] / ".agent").rglob("*"))
    assert main(["evidence", "--path", str(planned[0]), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["checks"][0]["current"]
    assert set((planned[0] / ".agent").rglob("*")) == before
    (Path(outcome.worktree) / "service.py").write_text("changed after verification")
    assert not inspect_evidence(planned[0]).checks[0].current


def test_runtime_configuration_changes_approval(planned, simulated):
    settings, calls, _, _ = simulated
    old = run(planned, VerificationSettings())
    new = run(planned, settings, approve=old.fingerprint)
    assert new.status == "approval_required" and new.fingerprint != old.fingerprint
    assert not calls and not planned[-1].exists()


@pytest.mark.parametrize("passed", [True, False])
def test_baseline_runs_before_coder_and_preserves_failure(planned, scripted, simulated, passed):
    root, _, saved, _, _ = planned
    draft = PlanDraft.model_validate(
        saved.draft.model_dump()
        | {
            "refactor": True,
            "invariant_criterion_ids": ["behavior"],
            "baseline_check_ids": ["unit"],
        }
    )
    asyncio.run(plan(root, draft=draft, new_plan=True, reason="Require baseline"))
    settings, calls, outcomes, _ = simulated
    if not passed:
        outcomes[0] = ProcessOutcome(1, "1 failed", False, False)
    outcome = run(planned, settings, approve=run(planned, settings).fingerprint)
    assert outcome.baseline.evidence[0].passed == passed
    ledger = inspect_evidence(root)
    assert ledger.checks[0].record.phase == "baseline"
    assert ledger.checks[0].record.before.workspace_revision == saved.workspace.revision
    if passed:
        assert scripted[1] and len(ledger.checks) == 2
        assert not ledger.checks[0].current and ledger.checks[1].current
    else:
        assert outcome.status == "blocked" and "baseline" in outcome.reason
        assert not scripted[1] and len(ledger.checks) == 1
        assert (Path(outcome.worktree) / "service.py").read_text() == "def run(): return 1\n"
        assert ledger.status == "recorded"


def test_changed_during_check_cannot_pass(planned, scripted, simulated):
    settings, _, _, action = simulated

    async def change(kwargs):
        (kwargs["root"] / "service.py").write_text("externally changed during validation")

    action[0] = change
    outcome = run(planned, settings, approve=run(planned, settings).fingerprint)
    assert outcome.status == "replan_required"
    assert outcome.workflow.evidence[0].status == "inconclusive"
    assert not inspect_evidence(planned[0]).checks[0].current


def test_output_artifact_tamper_is_rejected(planned, scripted, simulated):
    settings, _, _, _ = simulated
    outcome = run(planned, settings, approve=run(planned, settings).fingerprint)
    ledger = inspect_evidence(planned[0])
    output = ledger.checks[0].record.result.artifacts[0]
    (Path(outcome.journal).parent / output.path).write_text("fake passing report")
    with pytest.raises(ValueError, match="artifact changed"):
        inspect_evidence(planned[0])


def test_recording_failure_stops_further_checks(planned, scripted, simulated, monkeypatch):
    settings, calls, _, _ = simulated
    actual = JsonlJournal.write

    def fail(writer, event):
        if event.kind == "verification_recorded":
            raise EventWriteError("synthetic persistence failure")
        actual(writer, event)

    monkeypatch.setattr(JsonlJournal, "write", fail)
    with pytest.raises(EventWriteError):
        run(planned, settings, approve=run(planned, settings).fingerprint)
    assert len(calls) == 2
    assert (planned[0] / ".agent/init.lock").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows LPAC verification")
def test_windows_verification_scratch_children_keep_security(windows_harness, tmp_path):
    h = windows_harness
    h.runtime.backend = WindowsReadOnlyProcess(
        runtime_roots=h.backend.runtime_roots,
        verification=True,
    )
    h.runtime.timeout = 30
    external = tmp_path / "external-secret"
    external.write_text("must-remain-private")
    child = (
        "import os,socket,tempfile,pathlib;"
        "p=pathlib.Path(tempfile.gettempdir())/'child.txt';"
        "p.write_text('scratch');assert p.read_text()=='scratch';p.unlink();"
        "d=pathlib.Path(tempfile.gettempdir())/'nested';d.mkdir();"
        "q=d/'temp';q.write_text('temporary');q.unlink();d.rmdir();"
        "assert not p.exists() and not d.exists();"
        "assert 'CODING_AGENT_API_KEY' not in os.environ;"
    )
    child += (
        "\nfor name in "
        + repr(
            [
                str(external),
                str(h.root / "src/main.py"),
                str(h.journal.directory / "events.jsonl"),
                "secrets/private",
            ]
        )
        + ":\n try: open(name).read()\n except OSError: pass\n else: raise AssertionError(name)\n"
    )
    child += (
        "try: socket.create_connection(('203.0.113.1',9),timeout=1)\n"
        "except OSError as e: assert e.winerror==10013\n"
        "else: raise AssertionError('network permitted')\n"
        "print('child protected')"
    )
    code = (
        "import subprocess,sys;"
        f"subprocess.run([sys.executable,'-c',{child!r}],check=True);"
        "\ntry: open('src/main.py','w').write('bad')\n"
        "except OSError: pass\nelse: raise AssertionError('source write permitted')\n"
        "print('verified boundaries')"
    )
    outcome = h.call(Shell(argv=(h.python, "-c", code)), approve=True)
    assert outcome.status == "succeeded", outcome
    assert "child protected" in outcome.output and "verified boundaries" in outcome.output
    assert (h.root / "src/main.py").read_text() == "print('hello')\n"
    assert external.read_text() == "must-remain-private"


def test_mac_verification_profile_limits_scratch_writes(tmp_path, make_task):
    task = make_task("t")
    backend = MacReadOnlyProcess(verification=True)
    profile = backend.profile(
        tmp_path / "source", task, tmp_path / "records", scratch=tmp_path / "scratch"
    )
    assert "(allow file-read* file-write*" in profile
    assert "(allow process-fork)" not in profile
    assert "(allow network" not in profile


@pytest.mark.parametrize("final_pass", [True, False])
def test_final_snapshot_conservatively_reruns_required_checks(
    planned,
    scripted,
    simulated,
    final_pass,
):
    root, _, saved, _, _ = planned
    raw = saved.draft.model_dump(mode="json")
    raw["requirement"]["risk"] = {"level": "low"}
    acceptance = raw["acceptance"]
    acceptance["checks"] = [c for c in acceptance["checks"] if c["evidence_type"] != "review"]
    allowed = {c["id"] for c in acceptance["checks"]}
    for criterion in acceptance["criteria"]:
        criterion["required_check_ids"] = [
            i for i in criterion["required_check_ids"] if i in allowed
        ]
    for item in raw["tasks"]:
        item["task"]["risk"] = {"level": "low"}
        item["task"]["acceptance"] = acceptance
    asyncio.run(
        plan(
            root,
            draft=PlanDraft.model_validate(raw),
            new_plan=True,
            settings=PlanSettings(mode="fast"),
        )
    )
    settings, calls, outcomes, action = simulated
    count = 0

    async def count_check(kwargs):
        nonlocal count
        count += 1
        if count > 1 and not final_pass:
            outcomes[0] = ProcessOutcome(1, "1 failed", False, False)

    action[0] = count_check
    outcome = run(planned, settings, approve=run(planned, settings).fingerprint)
    assert outcome.status == ("tasks_verified" if final_pass else "blocked")
    assert not outcome.requirement_complete
    assert len(outcome.final_verification.evidence) == 2
    assert all(e.passed == final_pass for e in outcome.final_verification.evidence)
    assert count == 3 and len(calls) == 6
    ledger = inspect_evidence(root)
    assert [v.record.phase for v in ledger.checks] == ["task", "final", "final"]
    assert all(v.current for v in ledger.checks)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows LPAC application verification")
@pytest.mark.parametrize("case", ["pass", "skip", "empty"])
def test_real_windows_unittest_evidence(planned, scripted, python_runtime, case):
    root, _, saved, _, _ = planned
    if case != "empty":
        source = (
            "import unittest, service\n"
            "class ServiceTest(unittest.TestCase):\n"
            + ("    @unittest.skip('required behavior unavailable')\n" if case == "skip" else "")
            + "    def test_behavior(self): self.assertEqual(service.run(), 2)\n"
        )
        (root / "test_service.py").write_text(source, encoding="utf-8")
    asyncio.run(initialize(root, refresh=True))
    raw = saved.draft.model_dump(mode="json")
    for acceptance in (raw["acceptance"], *(t["task"]["acceptance"] for t in raw["tasks"])):
        for check in acceptance["checks"]:
            if check["evidence_type"] == "test":
                check["command"] = ["python", "-m", "unittest", "discover", "-v"]
    asyncio.run(plan(root, draft=PlanDraft.model_validate(raw), new_plan=True))
    settings = VerificationSettings(runtime_roots=(python_runtime,))
    outcome = run(planned, settings, approve=run(planned, settings).fingerprint)
    assert outcome.status == "blocked" and not outcome.requirement_complete
    report = inspect_evidence(root).checks[0]
    assert report.current
    assert report.record.version.exit_code == 0
    actual = report.record.result
    assert actual.exit_code == (5 if case == "empty" else 0)
    assert actual.process_argv[1:] == ("-m", "unittest", "discover", "-v")
    assert Path(actual.process_cwd).name == "repo"
    assert Path(actual.process_argv[0]).parent.parent == Path(actual.process_cwd).parent
    assert not Path(actual.process_cwd).exists()
    assert (
        report.record.evidence[0].status
        == {
            "pass": "passed",
            "skip": "skipped",
            "empty": "inconclusive",
        }[case]
    )
    assert (root / "service.py").read_bytes() == b"def run(): return 1\n"


def test_unittest_empty_nonzero_exit_is_not_a_code_failure():
    check = AcceptanceCheck(
        id="unit",
        description="unit tests",
        evidence_type="test",
        command=("python", "-m", "unittest"),
    )
    assert classify(check, result("Ran 0 tests in 0.0s\n\nNO TESTS RAN\n", 5))[0] == "inconclusive"


def test_evidence_payload_cannot_invent_execution(planned, scripted, simulated, monkeypatch):
    import coding_agent.application.evidence as evidence_module

    settings, _, _, _ = simulated
    run(planned, settings, approve=run(planned, settings).fingerprint)
    original = evidence_module.read_artifact

    def altered(directory, artifact):
        text = original(directory, artifact)
        if not artifact.path.startswith("execution-"):
            return text
        raw = json.loads(text)
        if isinstance(raw, dict) and "phase" in raw:
            raw["result"]["output"] = "invented successful test report"
            return json.dumps(raw)
        return text

    monkeypatch.setattr(evidence_module, "read_artifact", altered)
    with pytest.raises(ValueError, match="matching execution source"):
        inspect_evidence(planned[0])


def test_verification_cancellation_preserves_patch_and_interrupted_tool(
    planned, scripted, simulated
):
    settings, _, _, action = simulated

    async def scenario():
        entered = asyncio.Event()

        async def waiting(kwargs):
            entered.set()
            await asyncio.Event().wait()

        action[0] = waiting
        root, _, _, config, workspace = planned
        preview = await execution.run(
            root, config=config, workspace=workspace, verification=settings
        )
        operation = asyncio.create_task(
            execution.run(
                root,
                config=config,
                workspace=workspace,
                verification=settings,
                approve=preview.fingerprint,
            )
        )
        await asyncio.wait_for(entered.wait(), 30)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        history = inspect_execution(root)
        assert any(
            e.tool_result and e.tool_result.status == "interrupted" for e in history.records.events
        )
        assert not history.records.unresolved
        assert "+def run(): return 2" in history.diff

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows descendant lifetime")
def test_windows_parent_exit_does_not_hide_live_verification_child(windows_harness):
    h = windows_harness
    h.runtime.backend = WindowsReadOnlyProcess(
        runtime_roots=h.backend.runtime_roots, verification=True
    )
    h.runtime.timeout = 15
    code = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        "print('parent-exited',flush=True)"
    )
    outcome = h.call(Shell(argv=(h.python, "-c", code)), approve=True)
    assert "parent-exited" in outcome.output
    assert outcome.status == "timed_out", outcome


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows private-directory capability")
@pytest.mark.xfail(
    strict=True,
    reason="P09 pending: Python 3.12.4+ protected mkdir ACL excludes LPAC identity",
)
def test_windows_verification_python_private_directory(windows_harness):
    h = windows_harness
    h.runtime.backend = WindowsReadOnlyProcess(
        runtime_roots=h.backend.runtime_roots, verification=True
    )
    h.runtime.timeout = 15
    code = (
        "import tempfile,pathlib;print('private-directory-start',flush=True)\n"
        "with tempfile.TemporaryDirectory() as d:\n"
        " pathlib.Path(d,'temp').write_text('temporary')\n"
        "assert not pathlib.Path(d).exists()\n"
        "print('private-directory-removed')"
    )
    outcome = h.call(Shell(argv=(h.python, "-c", code)), approve=True)
    # Preserve the missing capability's success criterion; never count its timeout as a pass.
    assert "private-directory-start" in outcome.output, outcome
    assert outcome.status == "succeeded", outcome
    assert "private-directory-removed" in outcome.output
