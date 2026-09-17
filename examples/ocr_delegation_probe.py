"""Offline Windows OCR capability probe; exit 1 means capability unavailable.

Run with the project venv. Only a synthetic repository enters the existing sandbox.
The output directory must be new; it retains the fixture and diagnostic JSONL.
This is a developer diagnostic, not product ReviewResult or acceptance evidence.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from coding_agent.core.models import TaskSpec
from coding_agent.runtime.windows_process import WindowsReadOnlyProcess


async def probe(ocr: Path, git_runtime: Path, output: Path) -> bool:
    from dulwich import porcelain

    output.mkdir()  # Refuse existing directories instead of overwriting user files.
    repo = output / "repo"
    records = output / "records"
    tools = output / "tools"
    for directory in (repo, records, tools):
        directory.mkdir()
    shutil.copyfile(ocr, tools / "ocr.exe")
    executable = tools / "ocr.exe"
    task = TaskSpec.model_validate(
        {
            "id": "ocr-boundary-probe",
            "title": "Probe offline OCR delegation",
            "goal": "Measure existing sandbox compatibility",
            "scope": {"allowed": ["**"], "forbidden": []},
            "requirements": ["Use a synthetic fixture without model credentials"],
            "acceptance": {
                "criteria": [
                    {
                        "id": "delegation",
                        "description": "Offline delegation runs in the existing sandbox",
                        "required_check_ids": ["probe"],
                    }
                ],
                "checks": [
                    {
                        "id": "probe",
                        "description": "Developer capability probe",
                        "evidence_type": "test",
                        "command": ["ocr", "delegate", "preview", "--format", "json"],
                    }
                ],
            },
            "risk": {"level": "high", "security": "high"},
            "permissions": {"network": False, "shell": "restricted", "database": "deny"},
        }
    )
    backend = WindowsReadOnlyProcess(runtime_roots=(tools, git_runtime), verification=True)
    with (records / "probe.jsonl").open("x", encoding="utf-8") as log:

        def record(kind: str, **data: object) -> None:
            log.write(json.dumps({"kind": kind, **data}, ensure_ascii=True) + "\n")
            log.flush()
            os.fsync(log.fileno())

        record(
            "fixture_requested",
            binary_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            backend="WindowsReadOnlyProcess(verification=True)",
            os_version=str(sys.getwindowsversion()),
        )
        (repo / "sample.py").write_text("value = 1\n", encoding="utf-8")
        (repo / "README.md").write_text("# Before\n", encoding="utf-8")
        porcelain.init(str(repo)).close()
        porcelain.add(str(repo), paths=["sample.py", "README.md"])
        porcelain.commit(
            str(repo),
            message=b"synthetic baseline",
            author=b"Probe <probe@example.invalid>",
            committer=b"Probe <probe@example.invalid>",
        )
        (repo / "sample.py").write_text("value = 2\n", encoding="utf-8")
        (repo / "new.py").write_text("enabled = True\n", encoding="utf-8")
        (repo / "README.md").write_text("# After\n", encoding="utf-8")

        def snapshot() -> dict[str, str]:
            return {
                path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in repo.rglob("*")
                if path.is_file()
            }

        before = snapshot()
        record("fixture_ready", files=before)
        compatible = True
        for args in (
            ("version",),
            ("delegate", "preview", "--format", "json"),
            ("delegate", "rule", "--format", "json", "sample.py", "new.py"),
        ):
            record("process_requested", args=args)
            try:
                result = await backend.run(
                    (str(executable), *args),
                    cwd=repo,
                    root=repo,
                    task=task,
                    protected=records,
                    timeout=45,
                    max_output_bytes=65536,
                )
            except (OSError, ValueError, TimeoutError) as error:
                record("process_unavailable", args=args, error=str(error))
                compatible = False
                print(f"{' '.join(args)}: unavailable ({error})", flush=True)
                continue
            valid = result.exit_code == 0 and not result.timed_out and not result.truncated
            if valid and args[0] == "delegate":
                try:
                    payload = json.loads(result.output)
                    valid = isinstance(payload, dict) and payload.get("schema_version") == "1"
                    if valid and args[1] == "preview":
                        valid = {f["path"] for f in payload["reviewable_files"]} == {
                            "sample.py",
                            "new.py",
                        } and {f["path"] for f in payload["excluded_files"]} == {"README.md"}
                    elif valid:
                        valid = {f for g in payload["groups"] for f in g["files"]} == {
                            "sample.py",
                            "new.py",
                        } and all(g["rule"].strip() for g in payload["groups"])
                except (ValueError, KeyError, TypeError, AttributeError):
                    valid = False
            record("process_result", args=args, valid=valid, **asdict(result))
            compatible &= valid
            print(f"{' '.join(args)}: exit={result.exit_code}, valid={valid}", flush=True)
        unchanged = before == snapshot()
        compatible &= unchanged
        record("probe_finished", compatible=compatible, fixture_unchanged=unchanged)
        return compatible


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ocr", type=Path, required=True, help="trusted standalone Windows OCR exe"
    )
    parser.add_argument(
        "--git-runtime", type=Path, required=True, help="Git for Windows mingw64/bin"
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="new diagnostic directory")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("this probe measures the Windows backend only")
    ocr = args.ocr.resolve(strict=True)
    git_runtime = args.git_runtime.resolve(strict=True)
    if not ocr.is_file() or not (git_runtime / "git.exe").is_file():
        parser.error("OCR executable and Git runtime must exist")
    output = args.output_dir.resolve()
    if output.is_relative_to(git_runtime) or git_runtime.is_relative_to(output):
        parser.error("output and trusted Git runtime must not overlap")
    return 0 if asyncio.run(probe(ocr, git_runtime, output)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
