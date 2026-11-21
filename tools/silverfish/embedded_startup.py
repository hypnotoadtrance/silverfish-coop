"""One typed launch request for a packaged embedded co-op game process."""

import asyncio
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass, replace
import hashlib
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
import uuid

from . import runtime
from .control import NAME
from .embedded_engine import (
    BRIDGE_ENV, OPT_IN_ENV, EmbeddedEngine, _assert_no_reparse, _current_startup, _exact_existing, _qualify,
)


STARTUP_ENV = "SILVERFISH_COOP_EMBEDDED_STARTUP"
RESIDENT_ENV = "SILVERFISH_COOP_RESIDENT_TITLE"
RESIDENT_FILE = "resident-host.json"
RESIDENT_JOIN_FILE = "resident-join.json"
_RESIDENT_FEATURES = ("SILVERFISH_COOP_NATIVE_EVENTS", "SILVERFISH_COOP_NATIVE_GUEST_SAVE", "SILVERFISH_COOP_NATIVE_ZONE")
REQUEST_FILE = "startup-request.json"
CLAIM_FILE = "startup-claimed.json"
ACCEPTED_FILE = "startup-accepted.json"
INVITE_FILE = "startup-invite.json"
REQUEST_LIMIT = 4096
INVITE_LIMIT = 32_768
_ROLES = {"host": "campaigns", "client": "clients"}
_NONCE = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])", re.IGNORECASE)


@dataclass(frozen=True)
class HostSelection:
    bind: str
    public_port: int
    private_port: int
    start: str


@dataclass(frozen=True)
class JoinSelection:
    invite_sha256: str
    character_id: str


@dataclass(frozen=True)
class StartupRequest:
    role: str
    title_generation: str
    title_request_id: str
    bridge_id: str
    pid: int
    game_build: str
    profile: str
    selection: HostSelection | JoinSelection


def _nonce(value: object) -> bool:
    return isinstance(value, str) and value != "0" * 32 and _NONCE.fullmatch(value) is not None


def _hash(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _decode(payload: bytes) -> dict:
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("Repeated embedded startup field")
            result[name] = value
        return result

    if not 0 < len(payload) <= REQUEST_LIMIT:
        raise ValueError("Invalid embedded startup record size")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid embedded startup record") from error
    if type(value) is not dict:
        raise ValueError("Invalid embedded startup record")
    return value


def _host(value: dict) -> HostSelection:
    if set(value) != {"bind", "public_port", "private_port", "start"}:
        raise ValueError("Invalid embedded Host selection")
    bind = value["bind"]
    if not isinstance(bind, str) or len(bind) > 45 or "%" in bind or str(ip_address(bind)) != bind:
        raise ValueError("Expected a canonical numeric Host bind address")
    ports = value["public_port"], value["private_port"]
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ports) or ports[0] == ports[1]:
        raise ValueError("Embedded Host requires distinct public and private UDP ports")
    if value["start"] not in ("resume", "wanderer"):
        raise ValueError("Unsupported embedded Host start")
    return HostSelection(bind, *ports, value["start"])


def _join(value: dict) -> JoinSelection:
    if type(value) is not dict or set(value) != {"invite_sha256", "character_id"} or not _hash(value["invite_sha256"]):
        raise ValueError("Invalid embedded Join selection")
    character = value["character_id"]
    if not isinstance(character, str) or len(character) != 36 or str(uuid.UUID(character)) != character:
        raise ValueError("Embedded Join requires one exact selected character")
    return JoinSelection(value["invite_sha256"], character)


def parse_request(payload: bytes) -> StartupRequest:
    """Decode the complete bounded record without touching the filesystem."""
    fields = _decode(payload)
    expected = {"schema", "role", "title_generation", "title_request_id", "bridge_id",
                "pid", "game_build", "profile", "selection"}
    if (set(fields) != expected or type(fields["schema"]) is not int or fields["schema"] != 1
            or fields["role"] not in ("host", "client") or type(fields["selection"]) is not dict
            or any(not _nonce(fields[name]) for name in ("title_generation", "title_request_id", "bridge_id"))
            or type(fields["pid"]) is not int or not 1 <= fields["pid"] < 2**32
            or not _hash(fields["game_build"])):
        raise ValueError("Invalid embedded startup identity")
    profile = fields["profile"]
    if not isinstance(profile, str) or not NAME.fullmatch(profile) or _DEVICE.fullmatch(profile):
        raise ValueError("Invalid embedded co-op profile name")
    selection = _host(fields["selection"]) if fields["role"] == "host" else _join(fields["selection"])
    return StartupRequest(fields["role"], fields["title_generation"], fields["title_request_id"], fields["bridge_id"],
                          fields["pid"], fields["game_build"], profile, selection)


def _record(fields: dict) -> bytes:
    return (json.dumps(fields, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")


def _receipt(request: StartupRequest) -> dict:
    return dict(schema=1, state="accepted", role=request.role, title_generation=request.title_generation,
                title_request_id=request.title_request_id, bridge_id=request.bridge_id, pid=request.pid,
                game_build=request.game_build, profile=request.profile)


def _private_file(status: os.stat_result) -> None:
    _assert_no_reparse(status)
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise ValueError("Embedded startup data must be an ordinary private file")


def _read_file(path: Path, limit: int) -> bytes:
    _private_file(path.lstat())
    with path.open("rb") as stream:
        _private_file(os.fstat(stream.fileno()))
        data = stream.read(limit + 1)
    if not 0 < len(data) <= limit:
        raise ValueError("Embedded startup data exceeds its bound")
    return data


def _directory(path: Path) -> Path:
    _assert_no_reparse(path.lstat())
    return _exact_existing(path)


def _bridge(bridge: Path, role: str, profile: str, nonce: str) -> tuple[Path, Path]:
    if not bridge.is_absolute() or len(bridge.parents) < 5:
        raise ValueError("The embedded startup bridge must be local and role-owned")
    directory = bridge.parents[2]
    work = bridge.parents[4]
    if directory.name != profile or bridge != work / _ROLES[role] / profile / "run" / "game-bridge" / nonce:
        raise ValueError("The embedded startup bridge does not belong to that role and profile")
    for path in (work, directory.parent, directory, directory / "run", bridge.parent, bridge):
        _directory(path)
    return work, directory


def _publish_once(path: Path, contents: bytes) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".startup-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
        # On Windows rename is atomic within this directory and refuses an existing target.
        temporary.rename(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish(bridge: Path, request: StartupRequest, *, invitation: Path | None = None) -> None:
    """Publish after the supervisor has assigned the exact game PID to its job."""
    payload = _record({"schema": 1, **asdict(request)})
    if parse_request(payload) != request:
        raise ValueError("Invalid embedded game startup request")
    work, _ = _bridge(bridge, request.role, request.profile, request.bridge_id)
    for name in (REQUEST_FILE, CLAIM_FILE, ACCEPTED_FILE):
        try:
            (bridge / name).lstat()
        except FileNotFoundError:
            continue
        raise FileExistsError("This embedded startup bridge already has a request")
    if isinstance(request.selection, JoinSelection):
        if (invitation is None or not invitation.is_absolute() or not invitation.is_relative_to(work)
                or not invitation.name.endswith(".invite.json")):
            raise ValueError("Import the invitation into the private co-op workspace")
        _exact_existing(invitation, directory=False)
        payload_invite = _read_file(invitation, INVITE_LIMIT)
        if hashlib.sha256(payload_invite).hexdigest() != request.selection.invite_sha256:
            raise ValueError("The selected invitation changed")
        _publish_once(bridge / INVITE_FILE, payload_invite)
    elif invitation is not None:
        raise ValueError("Host startup cannot carry an invitation")
    _publish_once(bridge / REQUEST_FILE, payload)


def accepted(bridge: Path, request: StartupRequest) -> bool:
    """Match the worker receipt to the supervisor-owned role/PID and title claim."""
    try:
        _bridge(bridge, request.role, request.profile, request.bridge_id)
        expected = _record(_receipt(request))
        return _read_file(bridge / ACCEPTED_FILE, REQUEST_LIMIT) == expected
    except (OSError, ValueError):
        return False


@contextmanager
def _pinned_invite(path: Path, digest: str):
    """Deny Windows writes and removal while the service reads this exact copy."""
    import msvcrt

    _private_file(path.lstat())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                          wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    open_file.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    generic_read, share_read, open_existing, open_reparse_point = 0x80000000, 1, 3, 0x00200000
    handle = open_file(str(path), generic_read, share_read, None, open_existing, open_reparse_point, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        file = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    with os.fdopen(file, "rb") as stream:
        _private_file(os.fstat(stream.fileno()))
        data = stream.read(INVITE_LIMIT + 1)
        if not 0 < len(data) <= INVITE_LIMIT or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("The private invitation snapshot does not match")
        yield path


def _host_start(directory: Path, selection: HostSelection) -> dict:
    from .service import menu_host_start

    save = directory / "save"
    try:
        _directory(save)
    except FileNotFoundError:
        pass
    for name in ("campaign.json", "campaign.previous.json", "host-identity.json", ".campaign.lock"):
        path = save / name
        try:
            status = path.lstat()
        except FileNotFoundError:
            continue
        _private_file(status)
    mode, _ = menu_host_start(directory)
    if mode != selection.start:
        raise ValueError("The selected Host campaign changed before startup")
    return {"start_preset": mode} if mode == "wanderer" else {}


async def _run(*, timeout: float = 15) -> None:
    """Consume the one request in the exact packaged game's private bridge."""
    from . import service

    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise ValueError("Expected a bounded embedded startup timeout")
    startup = _current_startup((OPT_IN_ENV, BRIDGE_ENV, STARTUP_ENV, RESIDENT_ENV, "SILVERFISH_COOP_HEADLESS",
                                *_RESIDENT_FEATURES))
    nonce = startup.environment.get(STARTUP_ENV)
    if startup.environment.get(OPT_IN_ENV) != "1" or not _nonce(nonce):
        raise ValueError("The packaged worker is not opted into embedded startup")
    render_mode = startup.environment.get("SILVERFISH_COOP_HEADLESS")
    headless = render_mode == "1"
    arguments = {argument.casefold() for argument in startup.argv[1:]}
    render_flags = {"-nullrhi", "-renderoffscreen"}
    if render_mode not in (None, "1") or arguments & render_flags != (render_flags if headless else set()):
        raise ValueError("The packaged worker did not begin with its expected render mode")
    bridge = Path(startup.environment.get(BRIDGE_ENV) or "")
    if not bridge.is_absolute() or bridge.name != nonce or len(bridge.parents) < 5:
        raise ValueError("The packaged worker lacks its exact boot bridge")
    for role, root in _ROLES.items():
        if bridge.parents[3].name == root:
            break
    else:
        raise ValueError("The packaged worker's bridge role is unknown")
    directory = bridge.parents[2]
    work, _ = _bridge(bridge, role, directory.name, nonce)
    expected_image = _exact_existing(work / "stage" / runtime.SHIPPING, directory=False)
    if startup.image != expected_image or startup.pid != os.getpid():
        raise ValueError("The packaged worker does not own the exact staged executable")
    if startup.environment.get(RESIDENT_ENV) == "1":
        if not runtime.qualified_authored_routes(work):
            raise ValueError("The resident worker requires its qualified native stage")
        required = {STARTUP_ENV: nonce, RESIDENT_ENV: "1"}
        if role == "host":
            required.update(dict.fromkeys(_RESIDENT_FEATURES, "1"))
        saved, _ = _qualify(startup, work, directory, role, required)
        resident = _resident_host if role == "host" else _resident_join
        await resident(work, directory, bridge, saved, headless)
        return

    path, claimed = bridge / REQUEST_FILE, bridge / CLAIM_FILE
    async with asyncio.timeout(timeout):
        while True:
            try:
                claimed.lstat()
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError("This embedded startup request has already been claimed")
            try:
                _private_file(path.lstat())
            except FileNotFoundError:
                await asyncio.sleep(.05)
            else:
                path.rename(claimed)
                break
    request = parse_request(_read_file(claimed, REQUEST_LIMIT))
    if (request.pid, request.role, request.bridge_id, request.profile) != (startup.pid, role, nonce, directory.name):
        raise ValueError("The startup request does not identify this role and process")
    if not runtime.qualified_authored_routes(work) or request.game_build != runtime.staged_build(work):
        raise ValueError("The startup request does not match the qualified staged build")
    selection = request.selection
    _qualify(startup, work, directory, role, {STARTUP_ENV: nonce})

    def accept():
        _publish_once(bridge / ACCEPTED_FILE, _record(_receipt(request)))

    if isinstance(selection, HostSelection):
        options = _host_start(directory, selection)
        accept()
        await service.host_game(
            work, directory, selection.bind, selection.public_port, headless, embedded_port=selection.private_port,
            max_guests=service.MENU_HOST_GUEST_LIMIT, **service.MENU_HOST_OPTIONS, **options,
        )
    else:
        with _pinned_invite(bridge / INVITE_FILE, selection.invite_sha256) as invitation:
            invite = service.load_invite(invitation, request.game_build)
            if invite.get("native_zones") is not True:
                raise ValueError("The invitation does not offer embedded native co-op")
            accept()
            await service.join_game(work, directory, invitation, headless, selection.character_id, embedded_engine=True)


async def _resident_host(work: Path, campaign: Path, mailbox: Path, saved: Path, headless: bool) -> None:
    from . import service
    from .title_session import SessionPlan

    settings = _decode(_read_file(mailbox / RESIDENT_FILE, REQUEST_LIMIT))
    if set(settings) != {"schema", "bind", "port"} or type(settings["schema"]) is not int or settings["schema"] != 1:
        raise ValueError("Invalid resident Host configuration")

    def endpoint(bind, port):
        if type(bind) is not str or "%" in bind or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Host requires a valid UDP port and a numeric local address")
        return str(ip_address(bind)), port

    default_bind, default_port = endpoint(settings["bind"], settings["port"])

    def status():
        return runtime.read_json(campaign / "run" / "status.json")

    def host_plan(bind, port):
        mode, area = service.menu_host_start(campaign)

        async def start():
            private = runtime.spare_udp_port()
            if private == port:
                private = runtime.spare_udp_port()
            if private == port:
                raise RuntimeError("A distinct private Unreal port was not available")
            engine = EmbeddedEngine(os.getpid(), mailbox, saved, private)
            await engine.observe_title()
            await service.host_game(
                work, campaign, bind, port, headless, resident_engine=engine,
                max_guests=service.MENU_HOST_GUEST_LIMIT, **service.MENU_HOST_OPTIONS,
                **({"start_preset": mode} if mode == "wanderer" else {}),
            )

        return SessionPlan(start, status,
                           lambda frame: frame.get("endpoint") == {"address": bind, "port": port}
                           and frame["game"]["map"] == area)

    def resolve(claim):
        if claim.host_settings is None:
            return host_plan(default_bind, default_port)
        name, address, port_text = claim.host_settings
        if name and name != campaign.name:
            raise ValueError("The resident title has a different campaign profile")
        bind, port = endpoint(address or default_bind, int(port_text) if port_text else default_port)
        return host_plan(bind, port)

    plan = replace(host_plan(default_bind, default_port), resolve_claim=resolve)
    await _resident_title(mailbox, saved, {"host": plan})


async def _resident_join(work: Path, directory: Path, mailbox: Path, saved: Path, headless: bool) -> None:
    from . import service
    from .title_session import SessionPlan

    settings = _decode(_read_file(mailbox / RESIDENT_JOIN_FILE, REQUEST_LIMIT))
    if set(settings) != {"schema", "selection"} or type(settings["schema"]) is not int or settings["schema"] != 1:
        raise ValueError("Invalid resident Join configuration")
    selection = _join(settings["selection"])
    path = mailbox / INVITE_FILE
    with _pinned_invite(path, selection.invite_sha256) as invitation:
        invite = service.load_invite(invitation, runtime.staged_build(work))
        if invite.get("native_zones") is not True:
            raise ValueError("The invitation does not offer embedded native co-op")
        options = service.guest_engine_options(work, invite)
    required = service._embedded_features(work, headless, None, options, resident=True)
    if _current_startup(tuple(required)).environment != required:
        raise ValueError("The resident guest lacks the invitation's staged game features")

    async def start():
        with _pinned_invite(path, selection.invite_sha256) as invitation:
            engine = EmbeddedEngine(os.getpid(), mailbox, saved, None)
            await engine.observe_title()
            await service.join_game(work, directory, invitation, headless, selection.character_id,
                                    resident_engine=engine)

    def status():
        return runtime.read_json(directory / "run" / "status.json")

    plan = SessionPlan(start, status, lambda frame: frame.get("character_id") == selection.character_id
                       and frame.get("gameplay_fresh") is True)
    await _resident_title(mailbox, saved, {"join": plan})


async def _resident_title(mailbox, saved, plans):
    from .title_session import TitleSession

    title = EmbeddedEngine(os.getpid(), mailbox, saved, None)
    await TitleSession(title.bridge, plans, resident=True, title_saved_directory=saved,
                       on_title_ready=lambda: None).run()


def run() -> None:
    """Native worker entry point. It has no gameplay or process arguments."""
    asyncio.run(_run())
