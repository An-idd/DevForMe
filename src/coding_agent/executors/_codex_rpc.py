"""Bounded stdio transport for a trusted Codex binary; this is not a sandbox."""

import asyncio
import json
import os
import signal
import sys
from collections import deque
from pathlib import Path
from typing import Any

MAX_LINE = 2 * 1024 * 1024
MAX_STREAM = 16 * MAX_LINE


class CodexRPC:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._job: int | None = None
        self._bytes = 0
        self._stderr_bytes = 0
        self._sequence = 0
        self._notifications: deque[dict[str, Any]] = deque()
        self._messages = 0

    async def start(self, executable: Path, home: Path, cwd: Path) -> None:
        # No inherited Codex profiles, API endpoints, plugins, proxies or API keys.
        env = {
            k: os.environ[k] for k in ("SystemRoot", "WINDIR", "LANG", "LC_ALL") if k in os.environ
        }
        env.update(
            CODEX_HOME=str(home),
            HOME=str(home),
            USERPROFILE=str(home),
            TEMP=str(cwd),
            TMP=str(cwd),
            TMPDIR=str(cwd),
        )
        options: dict[str, Any] = (
            {"creationflags": 0x08000000}
            if sys.platform == "win32"
            else {"start_new_session": True}
        )
        launch = asyncio.create_task(
            asyncio.create_subprocess_exec(
                str(executable),
                "app-server",
                "--listen",
                "stdio://",
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_LINE,
                **options,
            )
        )
        try:
            self.process = await asyncio.shield(launch)
        except asyncio.CancelledError:
            # Retain the process handle even if cancellation races with creation.
            self.process = await launch
            raise
        if sys.platform == "win32":
            self._attach_job(self.process.pid)
        self._stderr = asyncio.create_task(self._drain_stderr())

    def _attach_job(self, pid: int) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows job requested on another platform")
        # Reuse the existing Win32 bindings. The job bounds lifetime/children,
        # not filesystem or network access; only Tool Runtime supplies a sandbox.
        import ctypes
        from ctypes import wintypes as w

        from ..runtime import _lowbox as win
        from ..runtime._winfiles import bind, kernel

        job = win.create_job(None, None)
        win.checked(job)
        self._job = int(job)
        limits = win.JobLimits()
        limits.flags = 0x2000 | 0x8  # kill on close; no child processes
        limits.active_processes = 1
        win.checked(win.set_job(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        open_process = bind(kernel, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
        handle = open_process(0x100 | 0x1, False, pid)
        win.checked(handle)
        try:
            win.checked(win.assign_job(job, handle))
        finally:
            win.close(handle)

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while data := await self.process.stderr.read(8192):
            # Never persist vendor diagnostics, which can contain prompts or secrets.
            self._stderr_bytes += len(data)
            if self._stderr_bytes > MAX_STREAM:
                self.process.kill()
                return

    async def send(self, value: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        data = (json.dumps(value, ensure_ascii=False) + "\n").encode()
        if len(data) > MAX_LINE:
            raise ValueError("Codex RPC output exceeds limit")
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def receive(self) -> dict[str, Any]:
        if self._notifications:
            return self._notifications.popleft()
        return await self._read()

    async def _read(self) -> dict[str, Any]:
        assert self.process is not None and self.process.stdout is not None
        line = await self.process.stdout.readline()
        self._bytes += len(line)
        self._messages += 1
        if self._messages > 10000:
            raise ValueError("Codex event budget exhausted")
        if not line.endswith(b"\n") or len(line) > MAX_LINE or self._bytes > MAX_STREAM:
            raise ValueError("Codex RPC closed or exceeded limit")
        if self._stderr_bytes > MAX_STREAM:
            raise ValueError("Codex diagnostic stream exceeded limit")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("invalid Codex envelope")
        return value

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        request_id = f"vca-{self._sequence}"
        await self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = await self._read()
            if method == "turn/start" and "method" in message:
                # App Server may emit events (even a tool call) before its ACK.
                # Hold these until the caller has validated the actual turn ID.
                self._notifications.append(message)
                continue
            if "id" in message:
                if message.get("id") != request_id or "error" in message or "method" in message:
                    raise ValueError("unexpected Codex response or server request")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise ValueError("invalid Codex result")
                return result
            if message.get("method") not in {
                "warning",
                "account/updated",
                "account/login/completed",
                "account/rateLimits/updated",
                "thread/started",
                "remoteControl/status/changed",
            }:
                raise ValueError("unexpected notification during Codex handshake")

    async def close(self) -> None:
        if sys.platform == "win32" and self._job is not None:
            from ..runtime import _lowbox as win

            win.checked(win.close(self._job))
            self._job = None
        if self.process is not None:
            if self.process.returncode is None:
                try:
                    if sys.platform == "win32":
                        self.process.kill()
                    else:
                        os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(self.process.wait(), timeout=5)
        if self._stderr is not None:
            await asyncio.wait_for(self._stderr, timeout=5)
