"""One typed launch request for a packaged embedded co-op game process."""

import asyncio
from contextlib import AsyncExitStack, contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
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
from .control import NAME, status as control_status
from .embedded_engine import (
    BRIDGE_ENV, INSTALLED_ENV, OPT_IN_ENV, UNIFIED_ENV, EmbeddedEngine,
    _assert_no_reparse, _current_startup, _exact_existing, _qualify,
)


STARTUP_ENV = "SILVERFISH_COOP_EMBEDDED_STARTUP"
RESIDENT_ENV = "SILVERFISH_COOP_RESIDENT_TITLE"
DIRECT_JOIN_ENV = "SILVERFISH_COOP_TITLE_DIRECT_JOIN"
RESIDENT_FILE = "resident-host.json"
RESIDENT_JOIN_FILE = "resident-join.json"
UNIFIED_FILE = "resident-title.json"
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
    root = "titles" if role == "title" else _ROLES[role]
    if directory.name != profile or bridge != work / root / profile / "run" / "game-bridge" / nonce:
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
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise ValueError("Expected a bounded embedded startup timeout")
    startup = _current_startup((OPT_IN_ENV, BRIDGE_ENV, STARTUP_ENV, RESIDENT_ENV, INSTALLED_ENV, UNIFIED_ENV, DIRECT_JOIN_ENV,
                                "SILVERFISH_COOP_HEADLESS", *_RESIDENT_FEATURES))
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
    unified = startup.environment.get(UNIFIED_ENV) == "1"
    roles = {"title": "titles"} if unified else _ROLES
    for role, root in roles.items():
        if bridge.parents[3].name == root:
            break
    else:
        raise ValueError("The packaged worker's bridge role is unknown")
    directory = bridge.parents[2]
    work, _ = _bridge(bridge, role, directory.name, nonce)
    if startup.environment.get(INSTALLED_ENV) == "1":
        if not unified or startup.environment.get(RESIDENT_ENV) != "1":
            raise ValueError("Installed startup requires the unified resident title")
        game = startup.image.parents[len(runtime.SHIPPING.parts) - 1]
        if (game.is_relative_to(work) or work.is_relative_to(game)
                or startup.image != game / runtime.SHIPPING):
            raise ValueError("The installed executable and co-op workspace are not distinct")
        with runtime.installed_content(work, game):
            await _run_qualified(startup, work, directory, bridge, role, nonce, headless, timeout)
        return
    await _run_qualified(startup, work, directory, bridge, role, nonce, headless, timeout)


async def _run_qualified(startup, work, directory, bridge, role, nonce, headless, timeout):
    from . import service

    expected_image = _exact_existing(runtime.content_root(work) / runtime.SHIPPING, directory=False)
    if startup.image != expected_image or startup.pid != os.getpid():
        raise ValueError("The packaged worker does not own the exact staged executable")
    if startup.environment.get(RESIDENT_ENV) == "1":
        if not runtime.qualified_authored_routes(work):
            raise ValueError("The resident worker requires its qualified native stage")
        required = {STARTUP_ENV: nonce, RESIDENT_ENV: "1"}
        if role in {"host", "title"}:
            required.update(dict.fromkeys(_RESIDENT_FEATURES, "1"))
        if role == "title":
            required.update({UNIFIED_ENV: "1", DIRECT_JOIN_ENV: "1"})
        if startup.environment.get(INSTALLED_ENV) == "1":
            required[INSTALLED_ENV] = "1"
            runtime.verify_installed_lua(work)
        saved, _ = _qualify(startup, work, directory, role, required)
        resident = {"title": _resident_unified, "host": _resident_host, "client": _resident_join}[role]
        await resident(work, directory, bridge, saved, headless)
        return

    if role == "title":
        raise ValueError("The unified title requires the resident worker")
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
    from .title_handoff import HostDefaults

    settings = _decode(_read_file(mailbox / RESIDENT_FILE, REQUEST_LIMIT))
    if set(settings) != {"schema", "bind", "port"} or type(settings["schema"]) is not int or settings["schema"] != 1:
        raise ValueError("Invalid resident Host configuration")
    defaults = HostDefaults(campaign.name, settings["bind"], settings["port"])
    plan = _resident_host_plan(work, campaign, mailbox, saved, headless, defaults.bind, defaults.port)
    await _resident_title(mailbox, saved, campaign, {"host": plan}, host_campaign=campaign, host_defaults=defaults)


def _reserve_campaign(work: Path, campaign_root: Path) -> Path:
    if campaign_root != work / "campaigns":
        raise ValueError("The co-op campaign directory is redirected")
    _exact_existing(work)
    campaign_root.mkdir(exist_ok=True)
    _assert_no_reparse(campaign_root.lstat())
    _exact_existing(campaign_root)
    for _ in range(8):
        directory = campaign_root / f"campaign-{uuid.uuid4().hex}"
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        return directory
    raise RuntimeError("Could not reserve a new co-op campaign directory")


def _resident_host_plan(work, campaign, mailbox, saved, headless, default_bind, default_port, *,
                        automatic_campaigns=False, host_options=None, cloud_backups=None, steam=None):
    from . import service
    from .control import select_run
    from .title_host_campaigns import saved_campaign
    from .title_session import SessionPlan

    def endpoint(bind, port):
        if type(bind) is not str or "%" in bind or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Host requires a valid UDP port and a numeric local address")
        return str(ip_address(bind)), port

    default_bind, default_port = endpoint(default_bind, default_port)
    if host_options is None:
        host_options = service.MENU_HOST_OPTIONS

    def host_plan(selected, bind, port, password="", *, creator_requested=False, resume_area=None, steam_access=None):
        if steam_access is not None:
            if steam is None or steam.pump.done():
                raise ValueError("Steam networking is unavailable")
            bind, port = "127.0.0.1", 0
        mode, area = (("resume", resume_area) if resume_area is not None
                      else service.menu_host_start(selected, profile=saved.parent))
        if creator_requested and mode == "resume":
            raise ValueError("The requested campaign already has a save")
        use_creator = mode != "resume" and (creator_requested or os.environ.get(INSTALLED_ENV) == "1" and not headless)
        engine = None

        async def start():
            nonlocal engine
            private = runtime.spare_udp_port()
            if private == port:
                private = runtime.spare_udp_port()
            if private == port:
                raise RuntimeError("A distinct private Unreal port was not available")
            engine = EmbeddedEngine(os.getpid(), mailbox, saved, private, session_directory=selected)
            await engine.observe_title()
            async with AsyncExitStack() as scope:
                if steam is not None:
                    await scope.enter_async_context(steam.playing(role="host"))
                session = service.host_game(
                    work, selected, bind, port, headless, resident_engine=engine,
                    direct_password=password, stock_creator=use_creator, resume_existing=resume_area is not None,
                    max_guests=service.MENU_HOST_GUEST_LIMIT, **host_options,
                    **({"start_preset": mode} if mode == "wanderer" and not use_creator else {}),
                    **({"steam_session": steam, "steam_access": steam_access} if steam_access is not None else {}),
                )
                if cloud_backups is None:
                    await session
                else:
                    await cloud_backups.mirror_host(selected, session)

        def status():
            return control_status(selected / "run")

        def ready(frame):
            listener = frame.get("endpoint")
            if not isinstance(listener, dict) or listener.get("address") != bind:
                return False
            actual_port = listener.get("port")
            return (type(actual_port) is int and (actual_port == port if port else 0 < actual_port <= 65535)
                    and (use_creator or frame["game"]["map"] == area))

        return SessionPlan(start, status, ready, read_game=lambda: engine.bridge.snapshot() if engine else None)

    def resolve(claim):
        if claim.host_settings is None:
            return host_plan(campaign, default_bind, default_port)
        name, address, port_text = claim.host_settings
        bind, port = endpoint(address or default_bind, int(port_text) if port_text else default_port)
        if claim.stock_resume:
            selected, area = saved_campaign(campaign, saved, name)
            return host_plan(selected, bind, port, claim.direct_password or "", resume_area=area,
                             steam_access=claim.steam_access)
        selected = campaign
        if not name and claim.stock_creator and automatic_campaigns:
            selected = _reserve_campaign(work, campaign.parent)
        elif name and name != campaign.name:
            if saved.parent == campaign / "game-user":
                raise ValueError("The resident title has a different campaign profile")
            selected = select_run(work, "campaigns", name)
            if selected != campaign.parent / name:
                raise ValueError("The campaign directory is redirected")
        return host_plan(selected, bind, port, claim.direct_password or "", creator_requested=claim.stock_creator,
                         steam_access=claim.steam_access)

    async def no_selection():
        raise ValueError("Choose a host campaign first")

    return SessionPlan(no_selection, lambda: None, lambda _: False, resolve_claim=resolve)


async def _resident_join(work: Path, directory: Path, mailbox: Path, saved: Path, headless: bool) -> None:
    from . import service
    from .title_session import SessionPlan

    settings = _decode(_read_file(mailbox / RESIDENT_JOIN_FILE, REQUEST_LIMIT))
    direct_enabled = _current_startup((DIRECT_JOIN_ENV,)).environment.get(DIRECT_JOIN_ENV) == "1"
    if direct_enabled:
        if set(settings) != {"schema", "direct"} or type(settings["schema"]) is not int \
                or settings["schema"] != 1 or settings["direct"] is not True:
            raise ValueError("Invalid resident Direct Join configuration")
        await _resident_direct_join(work, directory, mailbox, saved, headless)
        return
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
    engine = None

    async def start():
        nonlocal engine
        with _pinned_invite(path, selection.invite_sha256) as invitation:
            engine = EmbeddedEngine(os.getpid(), mailbox, saved, None, session_directory=directory)
            await engine.observe_title()
            await service.join_game(work, directory, invitation, headless, selection.character_id,
                                    resident_engine=engine)

    def status():
        return control_status(directory / "run")

    plan = SessionPlan(start, status, lambda frame: frame.get("character_id") == selection.character_id
                       and frame.get("gameplay_fresh") is True,
                       read_game=lambda: engine.bridge.snapshot() if engine else None)
    await _resident_title(mailbox, saved, directory, {"join": plan})


async def _resident_direct_join(work: Path, directory: Path, mailbox: Path, saved: Path, headless: bool) -> None:
    plan, settings = _resident_direct_join_plan(work, directory, mailbox, saved, headless)
    try:
        await _resident_title(mailbox, saved, directory, {"join": plan}, join_settings=settings)
    finally:
        await settings.close()


def _resident_direct_join_plan(work, directory, mailbox, saved, headless, *, available=None, steam=None):
    from . import direct_ip, service
    from .title_join_settings import TitleJoinSettings
    from .title_session import SessionPlan

    def qualify(invite):
        if invite.get("native_zones") is not True:
            raise ValueError("The host does not offer native co-op")
        options = service.guest_engine_options(work, invite)
        if available is not None:
            if any(enabled and not available.get(feature) for feature, enabled in options.items()):
                raise ValueError("The title is missing features required by the host")
            return
        required = service._embedded_features(work, headless, None, options, resident=True)
        if _current_startup(tuple(required)).environment != required:
            raise ValueError("The resident guest lacks the host's staged game features")

    def status():
        return control_status(directory / "run")

    settings = TitleJoinSettings(work, mailbox, direct_profile=directory, qualify_connection=qualify,
                                 timeout=18, saved=saved,
                                 endpoint_fingerprint=steam.fingerprint_for if steam is not None else None)

    def resolve(claim):
        if claim.join_settings is None:
            raise ValueError("Enter a host address and choose a character first")
        draft, profile, character = claim.join_settings
        if profile and profile != directory.name:
            raise ValueError("The resident title has a different guest profile")
        connection = settings.resolve(claim.generation, draft, character)
        if not isinstance(connection, direct_ip.Connection):
            raise ValueError("Expected an authenticated Direct IP connection")
        engine = None

        async def start():
            nonlocal engine
            engine = EmbeddedEngine(os.getpid(), mailbox, saved, None, session_directory=directory)
            await engine.observe_title()
            async with AsyncExitStack() as scope:
                if steam is not None:
                    endpoint = connection.session()
                    await scope.enter_async_context(steam.playing(endpoint["host"], endpoint["port"], role="guest"))
                await service.join_game(work, directory, connection, headless, character, resident_engine=engine)

        return SessionPlan(start, status, lambda frame: frame.get("character_id") == character
                           and frame.get("gameplay_fresh") is True,
                           read_game=lambda: engine.bridge.snapshot() if engine else None)

    async def no_selection():
        raise ValueError("A Direct IP character selection is required")

    return SessionPlan(no_selection, status, lambda _: False, resolve_claim=resolve), settings


async def _resident_unified(work: Path, seat: Path, mailbox: Path, saved: Path, headless: bool) -> None:
    from . import service
    from .control import select_run
    from .title_handoff import HostDefaults

    settings = _decode(_read_file(mailbox / UNIFIED_FILE, REQUEST_LIMIT))
    if set(settings) != {"schema", "campaign", "bind", "port"} or type(settings["schema"]) is not int or settings["schema"] != 1:
        raise ValueError("Invalid unified title configuration")
    name = settings["campaign"]
    if not isinstance(name, str):
        raise ValueError("Expected a co-op campaign name")
    campaign = select_run(work, "campaigns", name)
    guest = select_run(work, "clients", seat.name)
    if campaign != work / "campaigns" / name or guest != work / "clients" / seat.name:
        raise ValueError("The resident role directory is redirected")
    host_options = service.unified_host_options(work)
    options = service.unified_title_options(work, host_options=host_options)
    required = service._embedded_features(work, headless, None, options, resident=True, unified=True)
    startup = _current_startup(tuple(required))
    if startup.environment != required:
        raise ValueError("The unified title lacks the standard native co-op capabilities")

    from .steam_cloud import CloudBackups
    from .steam_session import installed_steam

    defaults = HostDefaults(name, settings["bind"], settings["port"])
    installed = os.environ.get(INSTALLED_ENV) == "1"
    cloud = CloudBackups(campaign.parent, saved.parent) if installed else None
    async with installed_steam(work, mailbox, startup.argv, enabled=installed and not headless) as steam:
        host = _resident_host_plan(work, campaign, mailbox, saved, headless, defaults.bind, defaults.port,
                                   automatic_campaigns=True, host_options=host_options, cloud_backups=cloud, steam=steam)
        join, join_settings = _resident_direct_join_plan(work, guest, mailbox, saved, headless, available=options, steam=steam)
        try:
            await _resident_title(mailbox, saved, seat, {"host": host, "join": join}, join_settings=join_settings,
                                  host_campaign=campaign, host_defaults=defaults, cloud_backups=cloud)
        finally:
            await join_settings.close()


async def _resident_title(mailbox, saved, directory, plans, *, join_settings=None, host_campaign=None,
                          host_defaults=None, cloud_backups=None):
    from .title_host_campaigns import campaign_entries
    from .title_session import TitleSession

    title = EmbeddedEngine(os.getpid(), mailbox, saved, None, session_directory=directory)
    def scan_campaigns():
        if cloud_backups is not None:
            cloud_backups.restore()
        return campaign_entries(host_campaign, saved)

    host_campaign_scan = scan_campaigns if host_campaign is not None else None
    await TitleSession(title.bridge, plans, resident=True, title_saved_directory=saved,
                       on_title_ready=lambda: None, join_settings=join_settings,
                       host_campaign_scan=host_campaign_scan, host_defaults=host_defaults).run()


def run() -> None:
    """Native worker entry point. It has no gameplay or process arguments."""
    asyncio.run(_run())
