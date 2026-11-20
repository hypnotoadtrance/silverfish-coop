"""Windowless bridge from the stock title to the isolated co-op title."""

import asyncio
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import uuid

from tools.silverfish import runtime
from tools.silverfish.__main__ import staged_title_menu
from tools.silverfish.control import NAME


_REQUEST = re.compile(rb"1\topen-coop\t([0-9a-f]{32})\t([0-9a-f]{32})\n")
_REQUEST_BYTES = len(b"1\topen-coop\t\t\n") + 64
_CONFIG_BYTES = 8192


@dataclass(frozen=True)
class Receipt:
    generation: str
    request_id: str


def parse_receipt(row: bytes) -> Receipt:
    match = _REQUEST.fullmatch(row)
    if match is None or b"0" * 32 in match.groups():
        raise ValueError("Invalid co-op title launch request")
    return Receipt(*(value.decode("ascii") for value in match.groups()))


def _inherited_pipe(standard_handle: int) -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    handle = kernel.GetStdHandle(standard_handle)
    if not handle or kernel.GetFileType(handle) != 3:  # FILE_TYPE_PIPE
        raise ValueError("The game title must supply a private inherited pipe")
    return handle


def read_inherited_receipt() -> Receipt:
    import msvcrt

    fd = msvcrt.open_osfhandle(_inherited_pipe(-10), os.O_RDONLY | os.O_BINARY)  # STD_INPUT_HANDLE
    with os.fdopen(fd, "rb") as stream:
        return parse_receipt(stream.read(_REQUEST_BYTES + 1))


def write_inherited_ready(receipt: Receipt) -> None:
    import msvcrt

    row = f"1\tcoop-title-ready\t{receipt.generation}\t{receipt.request_id}\n".encode("ascii")
    fd = msvcrt.open_osfhandle(_inherited_pipe(-11), os.O_WRONLY | os.O_BINARY)  # STD_OUTPUT_HANDLE
    with os.fdopen(fd, "wb", buffering=0) as stream:
        if stream.write(row) != len(row):
            raise RuntimeError("The windowless title readiness receipt was incomplete")


def _qualified_path(value: object, *, directory: bool) -> Path:
    if not isinstance(value, str) or not value or not value.isprintable():
        raise ValueError("Invalid co-op coordinator path")
    path = Path(value)
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or not (path.is_dir() if directory else path.is_file())):
        raise ValueError("Expected an exact local co-op coordinator path")
    return path


def _profile(value: object) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError("Invalid co-op campaign or character profile name")
    return value


def load_plan(config_path: Path) -> SimpleNamespace:
    with _qualified_path(str(config_path), directory=False).open("rb") as stream:
        payload = stream.read(_CONFIG_BYTES + 1)
    if len(payload) > _CONFIG_BYTES:
        raise ValueError("The co-op coordinator settings are too large")
    config = json.loads(payload.decode("utf-8"))
    if (type(config) is not dict or set(config) != {"schema", "work", "host", "join"}
            or type(config["schema"]) is not int or config["schema"] != 1):
        raise ValueError("Unsupported co-op coordinator settings")
    work = _qualified_path(config["work"], directory=True)
    host, join = config["host"], config["join"]
    plan = SimpleNamespace(work=work, title_profile="bootstrap", campaign=None, bind="127.0.0.1", port=7777,
                           invite=None, profile="guest", character=None)
    if host is not None:
        if (type(host) is not dict or set(host) != {"campaign", "bind", "port"}
                or type(host["port"]) is not int or not 1 <= host["port"] <= 65535
                or not isinstance(host["bind"], str)):
            raise ValueError("Invalid configured Host action")
        plan.campaign = _profile(host["campaign"])
        plan.bind, plan.port = str(ipaddress.ip_address(host["bind"])), host["port"]
    if join is not None:
        if type(join) is not dict or set(join) != {"profile", "invite", "character"}:
            raise ValueError("Invalid configured Join action")
        plan.profile = _profile(join["profile"])
        plan.invite = _qualified_path(join["invite"], directory=False)
        if not plan.invite.is_relative_to(work) or not plan.invite.name.endswith(".invite.json"):
            raise ValueError("Import the invitation into the private co-op workspace")
        character = join["character"]
        if character is not None and (not isinstance(character, str) or str(uuid.UUID(character)) != character):
            raise ValueError("Invalid configured guest character ID")
        plan.character = character
    if host is None and join is None:
        raise ValueError("Enable a Host or Join action for the co-op title")
    return plan


async def open_coop(receipt: Receipt, config: Path, *, report_ready=write_inherited_ready) -> None:
    plan = load_plan(config)
    receipts = plan.work / "bootstrap-receipts"
    receipts.mkdir(exist_ok=True)
    _qualified_path(str(receipts), directory=True)
    lock = receipts / f"{receipt.generation}.lock"
    status = lock.with_suffix(".json")
    with lock.open("xb"):
        progress = dict(schema=1, generation=receipt.generation, request_id=receipt.request_id)
        runtime.write_json(status, dict(progress, state="opening"))

        def ready():
            runtime.write_json(status, dict(progress, state="title_ready"))
            report_ready(receipt)

        try:
            headless = os.environ.get("SILVERFISH_COOP_BOOTSTRAP_HEADLESS") == "1"
            await staged_title_menu(plan, on_title_ready=ready, require_embedded=True, headless=headless)
        except (OSError, ValueError, RuntimeError) as error:
            runtime.write_json(status, dict(progress, state="failed", error_type=type(error).__name__))
            raise
        else:
            runtime.write_json(status, dict(progress, state="stopped"))


def main() -> int:
    try:
        receipt = read_inherited_receipt()
    except (OSError, ValueError):
        return 2
    try:
        _inherited_pipe(-11)
        local = Path(os.environ["LOCALAPPDATA"]).resolve(strict=True)
        asyncio.run(open_coop(receipt, local / "SilverfishCoop" / "coordinator.json"))
    except (KeyError, OSError, ValueError, RuntimeError):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
