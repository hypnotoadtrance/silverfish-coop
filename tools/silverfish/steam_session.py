"""Connect Steam lobbies to the installed game's existing Direct IP flow."""

import asyncio
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
import shlex
import struct
import time
import uuid
import zlib

from . import runtime, steam_browser, steam_network_report
from .campaign import valid_steam_account
from .embedded_engine import _exact_existing
from .steam_transport import SteamBuildMismatch


APP_ID = 2941710
STEAM_DLL = runtime.SHIPPING.parent / "ue4ss/Mods/SilverfishCoopEmbeddedRuntime/steam_api64.dll"
STEAM_DLL_SHA256 = "e082bf5c9f881c822b1540a76b74f9d15e18019a73ebd206a559595badcb7f65"
STATUS = "steam-coop-status.json"
HOST_OFFER = "steam-coop-host.tsv"
HOST_INVITE = "steam-coop-invite-request.tsv"
AVAILABLE = "steam-coop-available"
JOIN_OFFER = "steam-coop-join.tsv"
JOIN_CANCEL = "steam-coop-join-cancel.tsv"
JOIN_OPENED = "steam-coop-join-opened.tsv"


def _status(bridge: Path, state: str, **details):
    with suppress(OSError):
        runtime.write_json(bridge / STATUS, dict(schema=1, app_id=APP_ID, state=state, **details))


def _read(bridge: Path, name: str, *, take=False) -> bytes:
    from .embedded_startup import _read_file

    path = bridge / name
    with suppress(OSError, ValueError):
        data = _read_file(path, 128)
        if take:
            path.unlink()
        return data
    if take:
        _remove(bridge, name)
    return b""


def _remove(bridge: Path, *names: str):
    for name in names:
        with suppress(OSError):
            (bridge / name).unlink(missing_ok=True)


def _avatar_hex(pixels: bytes) -> str:
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

    scanlines = b"".join(b"\0" + pixels[i:i + 128] for i in range(0, 4096, 128))
    compressed = zlib.compress(scanlines, level=1)
    # Incompressible profile art still has to fit the title's 8,500-character
    # hex field. The stored DEFLATE block bounds the 32 px PNG below that limit.
    if len(compressed) > len(scanlines) + 11:
        compressed = zlib.compress(scanlines, level=0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", 32, 32, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")).hex()


class SteamSession:
    """Own at most one Steam lobby for this resident game's lifetime."""

    def __init__(self, transport, title_bridge: Path, pump: asyncio.Task, *, work: Path | None = None):
        self.transport, self.title_bridge, self.pump = transport, title_bridge, pump
        self.work = work
        self._lock = asyncio.Lock()
        self._role = None
        self._route = None
        self._nonce = None
        self._expires = 0

    def fingerprint_for(self, address: str, port: int) -> str | None:
        route = self._route
        if route is not None and (route.host, route.port) == (address, port):
            return route.fingerprint
        return None

    async def profile_avatars(self, accounts) -> dict[str, str]:
        pending = {account for account in accounts if valid_steam_account(account)}
        avatars = {}
        deadline = time.monotonic() + .6
        while pending and not self.pump.done():
            for account in tuple(pending):
                try:
                    pixels = self.transport.api.small_avatar_rgba(int(account))
                except (OSError, RuntimeError, ValueError):
                    pixels = b""
                if pixels is not None:
                    pending.remove(account)
                    if len(pixels) == 4096:
                        avatars[account] = _avatar_hex(pixels)
            if not pending or time.monotonic() >= deadline:
                break
            await asyncio.sleep(.1)
        return avatars

    def _leave(self):
        self._route = self._nonce = None
        self._expires = 0
        _remove(self.title_bridge, JOIN_OFFER, JOIN_CANCEL, JOIN_OPENED)
        with suppress(OSError, RuntimeError):
            self.transport.leave()

    async def receive_invites(self):
        while not self.pump.done():
            if self._nonce is not None and not self._role:
                opened = _read(self.title_bridge, JOIN_OPENED, take=True)
                cancel = _read(self.title_bridge, JOIN_CANCEL, take=True)
                if opened == f"1\t{self._nonce}\topened\n".encode("ascii"):
                    self._expires = 0
                    _remove(self.title_bridge, JOIN_OFFER)
                if (cancel == f"1\t{self._nonce}\tcancel\n".encode("ascii")
                        or self._expires and self._expires <= time.time()
                        or self.transport.lobby != self._route.lobby):
                    self._leave()
                    _status(self.title_bridge, "ready")
            try:
                lobby = await asyncio.wait_for(self.transport.pending_invites.get(), .2)
            except TimeoutError:
                continue
            if self._role or self._nonce:
                continue
            await self.open_lobby(lobby)

    async def open_lobby(self, lobby: int) -> bool:
        async with self._lock:
            if self._role or self._nonce or self.pump.done():
                return False
            self._leave()
            _status(self.title_bridge, "joining_lobby")
            try:
                route = await self.transport.join(lobby)
                if self._role or self.pump.done():
                    self._leave()
                    return False
                self._route, self._nonce = route, uuid.uuid4().hex
                self._expires = int(time.time()) + 60
                runtime.write_text(self.title_bridge / JOIN_OFFER,
                                   f"1\t{self._nonce}\t{route.port}\t{self._expires}\n", newline="\n")
                _status(self.title_bridge, "invite_ready")
                return True
            except SteamBuildMismatch as error:
                self._leave()
                nonce, expires = uuid.uuid4().hex, int(time.time()) + 60
                with suppress(OSError):
                    runtime.write_text(self.title_bridge / JOIN_OFFER,
                                       f"2\t{nonce}\t{self.transport.version}\t{error.host_version or '-'}\t{expires}\n",
                                       newline="\n")
                _status(self.title_bridge, "incompatible_build", mod_version=self.transport.version,
                        host_version=error.host_version)
                return False
            except (OSError, ValueError, RuntimeError):
                self._leave()
                _status(self.title_bridge, "invite_unavailable")
                return False

    @asynccontextmanager
    async def playing(self, address: str = "", port: int = 0, *, role: str, network_run: Path | None = None):
        self._role = role
        try:
            async with self._lock:
                route = self._route
                guest_route = role == "guest" and route is not None and (route.host, route.port) == (address, port)
                if not guest_route:
                    self._leave()
                if role == "guest" and not guest_route:
                    network_run = None
                _remove(self.title_bridge, JOIN_OFFER, JOIN_CANCEL, JOIN_OPENED)
            async with steam_network_report.capture(self.work, network_run, self.transport, role):
                yield
        finally:
            self._leave()
            self._role = None
            _status(self.title_bridge, "ready" if not self.pump.done() else "unavailable")

    @asynccontextmanager
    async def host_panel(self, bridge: Path, bind: str, port: int, certificate: str, control, *, access: str,
                         pause_token):
        address = ip_address(bind)
        if address != ip_address("127.0.0.1") or self.pump.done():
            _status(bridge, "unavailable", reason="loopback_required" if not address.is_loopback else "steam_offline")
            yield
            return
        fingerprint = sha256(certificate.encode("utf-8")).hexdigest()
        panel = asyncio.create_task(self._host_panel(bridge, str(address), port, fingerprint, control, access, pause_token))
        try:
            yield
        finally:
            panel.cancel()
            with suppress(asyncio.CancelledError):
                await panel
            _remove(bridge, HOST_OFFER, HOST_INVITE)

    async def _host_panel(self, bridge, address, port, fingerprint, control, access, pause_token):
        async def open_lobby():
            try:
                await self.transport.host(port, address=address, fingerprint=fingerprint, access=access)
            except (OSError, ValueError, RuntimeError):
                _status(bridge, "lobby_unavailable", access=access)
                return "retry"
            _status(bridge, "lobby_open", access=access)
            with suppress(OSError):
                control.event("steam_lobby_open", access=access)
            return "ready"

        offer_path = bridge / HOST_OFFER
        last_token = None
        _status(bridge, "opening_lobby", access=access)
        offer_state = await open_lobby()
        while not self.pump.done():
            token = pause_token()
            if token != last_token:
                last_token = token
                if token is None:
                    _remove(bridge, HOST_OFFER, HOST_INVITE)
                else:
                    with suppress(OSError):
                        runtime.write_text(offer_path, f"1\t{token}\t{offer_state}\n", newline="\n")
            request = _read(bridge, HOST_INVITE, take=True)
            if token is not None and request == f"1\t{token}\tinvite\n".encode("ascii"):
                try:
                    runtime.write_text(offer_path, f"1\t{token}\topening\n", newline="\n")
                    offer_state = await open_lobby() if self.transport.lobby is None else "ready"
                    if offer_state == "ready":
                        overlay = self.transport.show_invites()
                        offer_state = "ready" if overlay else "friends"
                        _status(bridge, "lobby_open", access=access, overlay=overlay)
                except (OSError, ValueError, RuntimeError):
                    offer_state = "overlay"
                    _status(bridge, "invite_unavailable", access=access)
                    with suppress(OSError):
                        control.event("steam_invite_unavailable")
                finally:
                    with suppress(OSError):
                        runtime.write_text(offer_path, f"1\t{token}\t{offer_state}\n", newline="\n")
            await asyncio.sleep(.15)
        _remove(bridge, HOST_OFFER, HOST_INVITE)
        _status(bridge, "unavailable", reason="steam_offline")


@asynccontextmanager
async def installed_steam(work: Path, bridge: Path, argv, *, enabled: bool):
    """Keep Steam failures optional so Direct IP can start in ordinary installations."""
    _remove(bridge, AVAILABLE)
    if not enabled:
        yield None
        return
    from .steam_transport import SteamTransport, parse_launch_lobby
    from .steamworks import Steamworks

    path = runtime.content_root(work) / STEAM_DLL
    try:
        if _exact_existing(path, directory=False) != path or path.stat().st_size > 2**20:
            raise ValueError("Unexpected Steamworks redistributable")
        if sha256(path.read_bytes()).hexdigest() != STEAM_DLL_SHA256:
            raise ValueError("Unverified Steamworks redistributable")
        build = runtime.staged_build(work)
        metadata = runtime.read_json(runtime.stage_manifest(runtime.content_root(work)))
        version = runtime.release_version(metadata) or f"dev-{build[:7]}"
        api = Steamworks.open(path)
    except (OSError, ValueError, RuntimeError):
        _status(bridge, "unavailable", reason="steam_initialization")
        yield None
        return

    transport = SteamTransport(api, build=build, version=version)
    pump = asyncio.create_task(transport.run())
    session = SteamSession(transport, bridge, pump, work=work)
    try:
        cold_lobby = parse_launch_lobby(argv)
        with suppress(OSError, ValueError, RuntimeError):
            steam_lobby = parse_launch_lobby(shlex.split(api.launch_command_line(), posix=False))
            if steam_lobby is not None:
                cold_lobby = steam_lobby if cold_lobby in (None, steam_lobby) else None
        if cold_lobby is not None:
            transport.pending_invites.put_nowait(cold_lobby)
        _status(bridge, "ready")
        with suppress(OSError):
            runtime.write_text(bridge / AVAILABLE, "1\n", newline="\n")
        inbox = asyncio.create_task(session.receive_invites())
        browser = asyncio.create_task(steam_browser.serve(bridge, transport, session.open_lobby, pump))
        try:
            yield session
        finally:
            browser.cancel()
            inbox.cancel()
            with suppress(asyncio.CancelledError):
                await inbox
            with suppress(asyncio.CancelledError):
                await browser
    finally:
        _remove(bridge, AVAILABLE)
        session._leave()
        pump.cancel()
        with suppress(asyncio.CancelledError, OSError, RuntimeError, ValueError):
            await pump
        transport.close()
