"""Lease the exact staged Unreal process that owns the embedded Python worker."""

import asyncio
from collections.abc import Mapping
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import threading

from . import native_title_entry, runtime
from .control import NAME
from .title_handoff import _isolated_stock_title


STARTUP_KEY = native_title_entry.STARTUP_KEY
BRIDGE_ENV = "SILVERFISH_COOP_BRIDGE"
OPT_IN_ENV = "SILVERFISH_COOP_EMBEDDED_ENGINE"
_ROLE_ROOTS = {"host": "campaigns", "client": "clients"}
_UUID = re.compile(r"[a-f0-9]{32}")
_FEATURE = re.compile(r"SILVERFISH_COOP_[A-Z0-9_]+")
_STARTUP_OPTIONS = ("userdir", "multihome", "port")
_STARTUP_OPTION = re.compile(r"(?<!\w)(?:userdir|multihome|port)\s*=", re.IGNORECASE)
_MAX_TIMEOUT = 30


@dataclass(frozen=True)
class _Startup:
    pid: int
    image: Path
    argv: tuple[str, ...]
    environment: Mapping[str, str | None]


def _current_startup(names: tuple[str, ...]) -> _Startup:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    module_name = kernel.GetModuleFileNameW
    module_name.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    module_name.restype = wintypes.DWORD
    image = ctypes.create_unicode_buffer(32768)
    length = module_name(None, image, len(image))
    if not 0 < length < len(image):
        raise OSError("Cannot read the current Windows executable")

    command_line = kernel.GetCommandLineW
    command_line.argtypes = []
    command_line.restype = wintypes.LPWSTR
    split_argv = ctypes.WinDLL("shell32", use_last_error=True).CommandLineToArgvW
    split_argv.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    split_argv.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = kernel.LocalFree
    free.argtypes = [wintypes.HLOCAL]
    free.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    arguments = split_argv(command_line(), ctypes.byref(count))
    if not arguments:
        raise OSError("Cannot read the current Windows startup arguments")
    try:
        argv = tuple(arguments[index] for index in range(count.value))
    finally:
        free(ctypes.cast(arguments, wintypes.HLOCAL))

    read_env = kernel.GetEnvironmentVariableW
    read_env.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    read_env.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    environment = {}
    for name in names:
        ctypes.set_last_error(0)
        length = read_env(name, buffer, len(buffer))
        if length >= len(buffer) or (length == 0 and ctypes.get_last_error() not in (0, 203)):
            raise OSError("Cannot read the staged engine environment")
        environment[name] = None if not length and ctypes.get_last_error() == 203 else buffer.value[:length]
    return _Startup(os.getpid(), Path(image.value), argv, environment)


def _options(argv: tuple[str, ...]) -> dict[str, str]:
    options = {}
    for argument in argv[1:]:
        name, equals, value = argument.partition("=")
        option = name.casefold().lstrip("-/+")
        if option in _STARTUP_OPTIONS:
            if (name.casefold() != "-" + option or not equals or not value or option in options
                    or _STARTUP_OPTION.search(value)):
                raise ValueError("Ambiguous staged Unreal startup option")
            options[option] = value
        elif _STARTUP_OPTION.search(argument):
            raise ValueError("Ambiguous staged Unreal startup option")
    return options


def _exact_existing(path: Path, *, directory: bool = True) -> Path:
    """Reject a stage component or private mailbox that resolves elsewhere."""
    if path.resolve(strict=True) != path or not (path.is_dir() if directory else path.is_file()):
        raise ValueError("The staged engine path is not an exact local path")
    return path


def _assert_no_reparse(status: os.stat_result) -> None:
    if (getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or stat.S_ISLNK(status.st_mode)):
        raise ValueError("The isolated Unreal save tree contains a reparse point")


def assert_private_saved_tree(profile: Path) -> None:
    """Reject existing redirections without entering them or creating a profile."""
    profile = profile.absolute()
    if profile.resolve() != profile:
        raise ValueError("The isolated Unreal game profile is not an exact local directory")
    try:
        profile_status = profile.lstat()
    except FileNotFoundError:
        return
    _assert_no_reparse(profile_status)
    if not stat.S_ISDIR(profile_status.st_mode):
        raise ValueError("The isolated Unreal game profile is not an exact local directory")
    saved = profile / "Saved"
    try:
        status = saved.lstat()
    except FileNotFoundError:
        return
    _assert_no_reparse(status)
    if not stat.S_ISDIR(status.st_mode):
        raise ValueError("The isolated Unreal Saved path is not a directory")
    pending = [saved]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                status = entry.stat(follow_symlinks=False)
                _assert_no_reparse(status)
                if stat.S_ISDIR(status.st_mode):
                    pending.append(entry.path)


def _qualify(startup: _Startup, work: Path, directory: Path, role: str,
             required_env: Mapping[str, str | None]) -> tuple[Path, Path]:
    executable = _exact_existing(work / "stage" / runtime.SHIPPING, directory=False)
    if (startup.pid != os.getpid() or not startup.argv
            or startup.image.resolve(strict=True) != executable
            or Path(startup.argv[0]).resolve(strict=True) != executable):
        raise ValueError("The embedded worker does not own the exact staged executable")
    if startup.environment.get(OPT_IN_ENV) != "1" or any(
        startup.environment.get(key) != value for key, value in required_env.items()
    ):
        raise ValueError("The embedded worker lacks its staged role environment")
    metadata = runtime.read_json(_exact_existing(work / "stage" / "silverfish-coop-stage.json", directory=False))
    fingerprint = metadata.get("game_build")
    if metadata.get("schema") != 1 or not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ValueError("The embedded worker's game stage is unrecognized")

    if not NAME.fullmatch(directory.name):
        raise ValueError("Invalid staged engine profile name")
    role_path = _exact_existing(work / _ROLE_ROOTS[role] / directory.name)
    if directory.resolve(strict=True) != role_path:
        raise ValueError("The profile does not belong to the staged engine role")
    profile = _exact_existing(role_path / "game-user")
    bridge = Path(startup.environment.get(BRIDGE_ENV) or "")
    if not _UUID.fullmatch(bridge.name) or bridge != role_path / "run" / "game-bridge" / bridge.name:
        raise ValueError("The embedded worker lacks its exact role bridge")
    _exact_existing(bridge)

    options = _options(startup.argv)
    requested_directory = Path(options.get("userdir", ""))
    if not requested_directory.is_absolute() or requested_directory.resolve(strict=True) != profile:
        raise ValueError("The engine was not started with its isolated user directory")
    if "port" in options or options.get("multihome") != ("127.0.0.1" if role == "client" else None):
        raise ValueError("The engine startup network options do not match its staged role")
    assert_private_saved_tree(profile)
    return profile / "Saved", bridge


class EmbeddedEngine:
    """A single-use process view and bridge owned by the embedded service.

    poll and returncode describe the lease. A released view has a return code
    even though the current game process remains alive at its stock title.
    """

    _lock = threading.Lock()
    _used = False

    def __init__(self, pid: int, mailbox: Path, saved_directory: Path, internal_port: int | None):
        self.pid = pid
        self.internal_port = internal_port
        self.returncode = None
        self.bridge = runtime.Bridge(mailbox, self)
        self._saved_directory = saved_directory
        self._sequence = 0
        self._state = "acquiring"

    def poll(self) -> int | None:
        return self.returncode

    @classmethod
    async def acquire(cls, work: Path, directory: Path, *, role: str, internal_port: int | None = None,
                      required_env: Mapping[str, str | None] | None = None, timeout: float = 12) -> "EmbeddedEngine":
        """Check immutable process startup and two advancing qualified stock-title frames."""
        if (role not in _ROLE_ROOTS
                or role == "host" and (type(internal_port) is not int or not 1 <= internal_port <= 65535)
                or role == "client" and internal_port is not None):
            raise ValueError("Invalid staged engine role or private co-op port")
        if not 0 < timeout <= _MAX_TIMEOUT:
            raise ValueError("Expected a bounded engine startup timeout")
        conditions = dict(required_env or {})
        if any(not isinstance(key, str) or not _FEATURE.fullmatch(key) or key in {BRIDGE_ENV, OPT_IN_ENV}
               or value is not None and not isinstance(value, str) for key, value in conditions.items()):
            raise ValueError("Invalid staged engine environment condition")

        startup = _current_startup((OPT_IN_ENV, BRIDGE_ENV, *conditions))
        saved_directory, mailbox = _qualify(startup, work.resolve(strict=True), directory, role, conditions)
        with cls._lock:
            if cls._used:
                raise RuntimeError("The staged engine lease was already used in this process")
            cls._used = True
        engine = cls(startup.pid, mailbox, saved_directory, internal_port)
        first = None

        def advancing(snapshot):
            nonlocal first
            frame = _isolated_stock_title(snapshot, saved_directory)
            if not frame:
                first = None
                return False
            if first is None or first[1] != frame[1]:
                first = frame
                return False
            return frame[0] > first[0]

        status = await engine.bridge.wait_for(advancing, timeout=timeout)
        engine._sequence = status["sequence"]
        engine._state = "active"
        return engine

    async def release(self, *, timeout: float = 12) -> dict:
        """Request stock leave on the game thread and verify a later isolated title."""
        if self._state != "active":
            raise RuntimeError("The staged engine lease is not active")
        if not 0 < timeout <= _MAX_TIMEOUT:
            raise ValueError("Expected a bounded engine leave timeout")
        self._state = "releasing"
        try:
            async with asyncio.timeout(timeout):
                assert_private_saved_tree(self._saved_directory.parent)
                status = await self.bridge.command("leave", timeout=timeout)
                acknowledged = status.get("sequence")
                if type(acknowledged) is not int or acknowledged <= self._sequence:
                    raise RuntimeError("The staged engine did not acknowledge stock leave on a new frame")
                ready = await self.bridge.wait_for(
                    lambda snapshot: _isolated_stock_title(snapshot, self._saved_directory)
                    and snapshot["sequence"] > acknowledged,
                    timeout=timeout,
                )
                assert_private_saved_tree(self._saved_directory.parent)
        except BaseException:
            self.returncode = 1
            raise
        else:
            self.returncode = 0
            return ready
        finally:
            self._state = "released"
