"""Windows no-follow handles. Imported only on Windows."""

import ctypes
import os
import sys
from ctypes import wintypes as w
from pathlib import Path
from typing import Any

if sys.platform != "win32":
    raise ImportError("Windows file backend")

import msvcrt

kernel = ctypes.WinDLL("kernel32", use_last_error=True)


def bind(library: Any, name: str, result: Any, *arguments: Any) -> Any:
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


_create = bind(
    kernel,
    "CreateFileW",
    w.HANDLE,
    w.LPCWSTR,
    w.DWORD,
    w.DWORD,
    w.LPVOID,
    w.DWORD,
    w.DWORD,
    w.HANDLE,
)
_close = bind(kernel, "CloseHandle", w.BOOL, w.HANDLE)
_final = bind(kernel, "GetFinalPathNameByHandleW", w.DWORD, w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD)
_move = bind(kernel, "MoveFileExW", w.BOOL, w.LPCWSTR, w.LPCWSTR, w.DWORD)
_info = bind(kernel, "GetFileInformationByHandle", w.BOOL, w.HANDLE, w.LPVOID)
_volume = bind(
    kernel,
    "GetVolumeInformationByHandleW",
    w.BOOL,
    w.HANDLE,
    w.LPWSTR,
    w.DWORD,
    w.LPVOID,
    w.LPVOID,
    w.LPVOID,
    w.LPWSTR,
    w.DWORD,
)
_drive_type = bind(kernel, "GetDriveTypeW", w.UINT, w.LPCWSTR)
advapi = ctypes.WinDLL("advapi32", use_last_error=True)
_get_security = bind(
    advapi, "GetFileSecurityW", w.BOOL, w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID
)
_set_security = bind(advapi, "SetFileSecurityW", w.BOOL, w.LPCWSTR, w.DWORD, w.LPVOID)
_sd_control = bind(advapi, "GetSecurityDescriptorControl", w.BOOL, w.LPVOID, w.LPVOID, w.LPVOID)
INVALID_HANDLE = ctypes.c_void_p(-1).value


class FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", w.DWORD),
        ("created", w.FILETIME),
        ("accessed", w.FILETIME),
        ("written", w.FILETIME),
        ("volume", w.DWORD),
        ("size_high", w.DWORD),
        ("size_low", w.DWORD),
        ("links", w.DWORD),
        ("index_high", w.DWORD),
        ("index_low", w.DWORD),
    ]


def final_path(handle: int) -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    length = _final(handle, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        raise ValueError("network filesystems are unsupported")
    return Path(value.removeprefix("\\\\?\\"))


def fd_path(fd: int) -> Path:
    return final_path(msvcrt.get_osfhandle(fd))


def open_file(
    path: Path, flags: int, *, directory: bool = False, allow_hardlinks: bool = False
) -> int:
    path = path.absolute()
    if str(path).startswith("\\\\"):
        raise ValueError("network filesystems are unsupported")
    # Trailing dots/spaces, device names and 8.3 aliases must not bypass scope.
    for part in path.parts[1:]:
        stem = part.split(".")[0].upper()
        if (
            part.endswith((" ", "."))
            or ":" in part
            or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or stem
            in {f"{prefix}{n}" for prefix in ("COM", "LPT") for n in "123456789\u00b9\u00b2\u00b3"}
        ):
            raise ValueError("ambiguous Windows path")
    access = 0x80000000  # GENERIC_READ
    if flags & (os.O_WRONLY | os.O_RDWR):
        access |= 0x40000000
    creation = 1 if flags & os.O_EXCL else 3  # CREATE_NEW / OPEN_EXISTING
    attributes = 0x00200000 | (0x02000000 if directory else 0)  # OPEN_REPARSE_POINT
    if flags & (os.O_WRONLY | os.O_RDWR):
        attributes |= 0x80000000  # WRITE_THROUGH
    # Pin directories against rename/removal while their paths are in use.
    handle = _create(str(path), access, 3 if directory else 7, None, creation, attributes, None)
    if handle == INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        filesystem = ctypes.create_unicode_buffer(32)
        if not _volume(handle, None, 0, None, None, None, filesystem, len(filesystem)):
            raise ctypes.WinError(ctypes.get_last_error())
        if filesystem.value != "NTFS" or _drive_type(path.anchor) != 3:
            raise ValueError("Windows backend requires a local fixed NTFS volume")
        info = FileInfo()
        if not _info(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise ValueError("reparse points and non-regular inputs are unsupported")
        if not directory and info.links != 1 and not allow_hardlinks:
            raise ValueError("hardlinked files are unsupported")
        if str(final_path(handle)).casefold() != str(path).casefold():
            raise ValueError("Windows path alias is unsupported")
        fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        _close(handle)
        raise
    return fd


def install(parent: int, source: str, target: str, *, replace: bool) -> None:
    directory = fd_path(parent)
    if replace:
        # MoveFileEx does not retain the old DACL. Copy it before the atomic
        # installation so a restrictive target ACL is never widened.
        length = w.DWORD()
        _get_security(str(directory / target), 4, None, 0, ctypes.byref(length))
        if not length.value:
            raise ctypes.WinError(ctypes.get_last_error())
        descriptor = ctypes.create_string_buffer(length.value)
        if not _get_security(str(directory / target), 4, descriptor, length, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        control, revision = w.WORD(), w.DWORD()
        if not _sd_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        protection = 0x80000000 if control.value & 0x1000 else 0x20000000
        if not _set_security(str(directory / source), 4 | protection, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    if not _move(str(directory / source), str(directory / target), 8 | int(replace)):
        raise ctypes.WinError(ctypes.get_last_error())
