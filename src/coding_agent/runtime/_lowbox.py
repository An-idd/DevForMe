"""Windows LPAC launch and job lifetime. No unrestricted launch path."""

import asyncio
import ctypes
import subprocess
import sys
from ctypes import wintypes as w
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

if sys.platform != "win32":
    raise ImportError("Windows process backend")

from ._winfiles import bind, kernel
from .process import ProcessOutcome

userenv = ctypes.WinDLL("userenv", use_last_error=True)
advapi = ctypes.WinDLL("advapi32", use_last_error=True)
P = ctypes.c_void_p
SIZE = ctypes.c_size_t


class StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        ("x", w.DWORD),
        ("y", w.DWORD),
        ("cx", w.DWORD),
        ("cy", w.DWORD),
        ("xc", w.DWORD),
        ("yc", w.DWORD),
        ("fill", w.DWORD),
        ("flags", w.DWORD),
        ("show", w.WORD),
        ("reserved_size", w.WORD),
        ("reserved_data", P),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", P)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class SecurityCapabilities(ctypes.Structure):
    _fields_ = [("sid", P), ("capabilities", P), ("count", w.DWORD), ("reserved", w.DWORD)]


class SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", w.DWORD), ("descriptor", P), ("inherit", w.BOOL)]


class JobLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", w.DWORD),
        ("min_working_set", SIZE),
        ("max_working_set", SIZE),
        ("active_processes", w.DWORD),
        ("affinity", SIZE),
        ("priority", w.DWORD),
        ("scheduling", w.DWORD),
        ("io", ctypes.c_uint64 * 6),
        ("process_memory", SIZE),
        ("job_memory", SIZE),
        ("peak_process_memory", SIZE),
        ("peak_job_memory", SIZE),
    ]


create_profile = bind(
    userenv,
    "CreateAppContainerProfile",
    ctypes.c_long,
    w.LPCWSTR,
    w.LPCWSTR,
    w.LPCWSTR,
    P,
    w.DWORD,
    P,
)
delete_profile = bind(userenv, "DeleteAppContainerProfile", ctypes.c_long, w.LPCWSTR)
profile_path = bind(userenv, "GetAppContainerFolderPath", ctypes.c_long, w.LPCWSTR, P)
free_task = bind(ctypes.WinDLL("ole32"), "CoTaskMemFree", None, P)
derive_capability = bind(
    ctypes.WinDLL("kernelbase", use_last_error=True),
    "DeriveCapabilitySidsFromName",
    w.BOOL,
    w.LPCWSTR,
    P,
    P,
    P,
    P,
)


class SidAttributes(ctypes.Structure):
    _fields_ = [("sid", P), ("flags", w.DWORD)]


free_sid = bind(advapi, "FreeSid", P, P)
sid_string = bind(advapi, "ConvertSidToStringSidW", w.BOOL, P, P)
local_free = bind(kernel, "LocalFree", P, P)
convert_sd = bind(
    advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL, w.LPCWSTR, w.DWORD, P, P
)
set_security = bind(advapi, "SetFileSecurityW", w.BOOL, w.LPCWSTR, w.DWORD, P)
init_attributes = bind(kernel, "InitializeProcThreadAttributeList", w.BOOL, P, w.DWORD, w.DWORD, P)
update_attribute = bind(
    kernel, "UpdateProcThreadAttribute", w.BOOL, P, w.DWORD, SIZE, P, SIZE, P, P
)
delete_attributes = bind(kernel, "DeleteProcThreadAttributeList", None, P)
create_process = bind(
    kernel, "CreateProcessW", w.BOOL, w.LPCWSTR, w.LPWSTR, P, P, w.BOOL, w.DWORD, P, w.LPCWSTR, P, P
)


class JobAccounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_int64),
        ("kernel_time", ctypes.c_int64),
        ("period_user_time", ctypes.c_int64),
        ("period_kernel_time", ctypes.c_int64),
        ("page_faults", w.DWORD),
        ("total_processes", w.DWORD),
        ("active_processes", w.DWORD),
        ("terminated_processes", w.DWORD),
    ]


query_job = bind(kernel, "QueryInformationJobObject", w.BOOL, w.HANDLE, ctypes.c_int, P, w.DWORD, P)


def active_processes(job: int) -> int:
    accounting = JobAccounting()
    checked(query_job(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
    return int(accounting.active_processes)


create_job = bind(kernel, "CreateJobObjectW", w.HANDLE, P, w.LPCWSTR)
set_job = bind(kernel, "SetInformationJobObject", w.BOOL, w.HANDLE, ctypes.c_int, P, w.DWORD)
assign_job = bind(kernel, "AssignProcessToJobObject", w.BOOL, w.HANDLE, w.HANDLE)
terminate_job = bind(kernel, "TerminateJobObject", w.BOOL, w.HANDLE, w.UINT)
terminate_process = bind(kernel, "TerminateProcess", w.BOOL, w.HANDLE, w.UINT)
resume = bind(kernel, "ResumeThread", w.DWORD, w.HANDLE)
wait = bind(kernel, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
exit_code = bind(kernel, "GetExitCodeProcess", w.BOOL, w.HANDLE, P)
close = bind(kernel, "CloseHandle", w.BOOL, w.HANDLE)
create_pipe = bind(kernel, "CreatePipe", w.BOOL, P, P, P, w.DWORD)
set_handle = bind(kernel, "SetHandleInformation", w.BOOL, w.HANDLE, w.DWORD, w.DWORD)
peek_pipe = bind(kernel, "PeekNamedPipe", w.BOOL, w.HANDLE, P, w.DWORD, P, P, P)
read_file = bind(kernel, "ReadFile", w.BOOL, w.HANDLE, P, w.DWORD, P, P)


def checked(ok: object) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


class Lowbox:
    """A per-launch LPAC identity, deleted with its private profile on close."""

    def __init__(self) -> None:
        self.sid = P()
        self.name = "verified-coding-agent-" + uuid4().hex
        self._allocations: list[object] = []
        result = create_profile(self.name, self.name, self.name, None, 0, ctypes.byref(self.sid))
        if result < 0:
            raise OSError(f"AppContainer profile creation failed: {result}")
        value = w.LPWSTR()
        try:
            checked(sid_string(self.sid, ctypes.byref(value)))
            self.identity = value.value
            folder = w.LPWSTR()
            result = profile_path(self.identity, ctypes.byref(folder))
            if result < 0:
                raise OSError("AppContainer profile path unavailable")
            try:
                assert folder.value is not None
                self.directory = Path(folder.value)
            finally:
                free_task(folder)
            sids = []
            for name in ("lpacAppExperience", "registryRead"):
                groups, capabilities = ctypes.POINTER(P)(), ctypes.POINTER(P)()
                group_count, capability_count = w.DWORD(), w.DWORD()
                checked(
                    derive_capability(
                        name,
                        ctypes.byref(groups),
                        ctypes.byref(group_count),
                        ctypes.byref(capabilities),
                        ctypes.byref(capability_count),
                    )
                )
                self._allocations.extend(
                    [groups[i] for i in range(group_count.value)]
                    + [capabilities[i] for i in range(capability_count.value)]
                    + [groups, capabilities]
                )
                if capability_count.value != 1:
                    raise OSError("unexpected capability count")
                sids.append(SidAttributes(capabilities[0], 4))
            self.capabilities = (SidAttributes * len(sids))(*sids)
        except BaseException:
            self.close()
            raise
        finally:
            if value:
                local_free(value)

    def grant_read(self, directory: Path, *, writable: bool = False) -> None:
        # Only controller-owned execution copies get ACLs. Original inputs are untouched.
        descriptor = P()
        checked(
            convert_sd(
                f"D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)"
                f"(A;OICI;{'GRGWGXSD' if writable else 'GRGX'};;;{self.identity})",
                1,
                ctypes.byref(descriptor),
                None,
            )
        )
        try:
            checked(set_security(str(directory), 4 | 0x80000000, descriptor))
        finally:
            local_free(descriptor)

    def close(self) -> None:
        for allocation in self._allocations:
            local_free(allocation)
        self._allocations.clear()
        if self.sid:
            free_sid(self.sid)
            self.sid = P()
            result = delete_profile(self.name)
            if result < 0:
                raise OSError(f"AppContainer profile cleanup failed: {result}")

    async def run(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        environment: dict[str, str],
        *,
        timeout: float,
        max_output_bytes: int,
        max_processes: int = 1,
    ) -> ProcessOutcome:
        if not 1 <= max_processes <= 16:
            raise ValueError("invalid process limit")
        handles: list[int | None] = []
        attributes = None
        process = ProcessInfo()
        job = None
        assigned = False
        output = bytearray()
        truncated = False
        timed_out = False
        try:
            sa = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, True)
            read_end, write_end, input_read, input_write = (w.HANDLE() for _ in range(4))
            checked(
                create_pipe(ctypes.byref(read_end), ctypes.byref(write_end), ctypes.byref(sa), 0)
            )
            handles.extend((read_end.value, write_end.value))
            checked(set_handle(read_end, 1, 0))
            checked(
                create_pipe(
                    ctypes.byref(input_read), ctypes.byref(input_write), ctypes.byref(sa), 0
                )
            )
            handles.extend((input_read.value, input_write.value))
            checked(set_handle(input_write, 1, 0))
            close(input_write)
            handles.remove(input_write.value)

            size = SIZE()
            init_attributes(None, 4, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            checked(init_attributes(attributes, 4, 0, ctypes.byref(size)))
            capabilities = SecurityCapabilities(
                self.sid, ctypes.cast(self.capabilities, P), len(self.capabilities), 0
            )
            lpac = w.DWORD(1)
            mitigation = ctypes.c_uint64(1 << 28)  # Win32k system calls disabled.
            inherited = (w.HANDLE * 2)(input_read.value, write_end.value)
            for name, value in (
                (0x20009, capabilities),
                (0x2000F, lpac),
                (0x20007, mitigation),
                (0x20002, inherited),
            ):
                checked(
                    update_attribute(
                        attributes,
                        0,
                        name,
                        ctypes.byref(value),
                        ctypes.sizeof(value),
                        None,
                        None,
                    )
                )

            job = create_job(None, None)
            checked(job)
            limits = JobLimits()
            # No breakaway; kill descendants on close, bounded memory, no error dialog.
            limits.flags = 0x2000 | 0x8 | 0x100 | 0x200 | 0x400
            limits.active_processes = max_processes
            limits.process_memory = 512 * 1024 * 1024
            limits.job_memory = 1024 * 1024 * 1024
            checked(set_job(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            ui_limits = w.DWORD(0xFF)
            checked(set_job(job, 4, ctypes.byref(ui_limits), ctypes.sizeof(ui_limits)))
            startup = StartupInfoEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.startup.flags = 0x100
            startup.startup.stdin = input_read
            startup.startup.stdout = startup.startup.stderr = write_end
            startup.attributes = ctypes.cast(attributes, P)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            env = ctypes.create_unicode_buffer(
                "\0".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\0\0"
            )
            checked(
                create_process(
                    argv[0],
                    command,
                    None,
                    None,
                    True,
                    0x80000 | 0x8000000 | 0x4 | 0x400,
                    env,
                    str(cwd),
                    ctypes.byref(startup),
                    ctypes.byref(process),
                )
            )
            checked(assign_job(job, process.process))
            assigned = True
            if resume(process.thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            close(write_end)
            handles.remove(write_end.value)
            close(input_read)
            handles.remove(input_read.value)
            deadline = monotonic() + timeout
            while True:
                available = w.DWORD()
                if peek_pipe(read_end, None, 0, None, ctypes.byref(available), None):
                    while available.value:
                        buffer = ctypes.create_string_buffer(min(available.value, 16384))
                        received = w.DWORD()
                        checked(
                            read_file(read_end, buffer, len(buffer), ctypes.byref(received), None)
                        )
                        chunk = buffer.raw[: received.value]
                        remaining = max_output_bytes - len(output)
                        output.extend(chunk[:remaining])
                        truncated |= len(chunk) > remaining
                        available.value -= received.value
                elif ctypes.get_last_error() != 109:  # ERROR_BROKEN_PIPE
                    raise ctypes.WinError(ctypes.get_last_error())
                state = wait(process.process, 0)
                if state not in (0, 258):
                    raise ctypes.WinError(ctypes.get_last_error())
                if state == 0 and active_processes(job) == 0 and not available.value:
                    # Every descendant exited; all writes precede the final drain.
                    tail = w.DWORD()
                    if (
                        not peek_pipe(read_end, None, 0, None, ctypes.byref(tail), None)
                        or not tail.value
                    ):
                        break
                if not timed_out and monotonic() >= deadline:
                    timed_out = True
                    checked(terminate_job(job, 124))
                if timed_out and monotonic() > deadline + 5:
                    raise OSError("sandbox job did not stop after timeout")
                await asyncio.sleep(0.01)
            code = w.DWORD()
            checked(exit_code(process.process, ctypes.byref(code)))
            return ProcessOutcome(
                int(code.value),
                output.decode("utf-8", errors="replace"),
                truncated,
                timed_out,
                argv,
                str(cwd),
            )
        finally:
            reaped = True
            if process.process:
                if assigned:
                    terminate_job(job, 125)
                else:
                    terminate_process(process.process, 125)
                reaped = wait(process.process, 5000) == 0
                close(process.process)
                close(process.thread)
            if job:
                try:
                    deadline = monotonic() + 5
                    while active_processes(job) and monotonic() < deadline:
                        sleep(0.01)
                    reaped &= active_processes(job) == 0
                except OSError:
                    reaped = False
                close(job)
            if attributes is not None:
                delete_attributes(attributes)
            for handle in handles:
                close(handle)
            if not reaped:
                raise OSError("sandbox process did not exit after termination")
