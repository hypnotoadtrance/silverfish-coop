"""Connect Steam lobbies to the installed game's existing Direct IP flow."""

import asyncio
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
import re
import shlex
import time
import uuid

from . import runtime
from .embedded_engine import _exact_existing


APP_ID = 2941710
STEAM_DLL = runtime.SHIPPING.parent / "ue4ss/Mods/SilverfishCoopEmbeddedRuntime/steam_api64.dll"
STEAM_DLL_SHA256 = "e082bf5c9f881c822b1540a76b74f9d15e18019a73ebd206a559595badcb7f65"
STATUS = "steam-coop-status.json"
HOST_OFFER = "steam-coop-host.tsv"
HOST_INVITE = "steam-coop-invite-request.tsv"
JOIN_OFFER = "steam-coop-join.tsv"
JOIN_CANCEL = "steam-coop-join-cancel.tsv"
JOIN_OPENED = "steam-coop-join-opened.tsv"
_PAUSE_OFFER = re.compile(rb"1\t([0-9a-f]{32})\thost\t([1-9][0-9]{0,14})\n")


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


class SteamSession:
    """Own at most one Steam lobby for this resident game's lifetime."""

    def __init__(self, transport, title_bridge: Path, pump: asyncio.Task):
        self.transport, self.title_bridge, self.pump = transport, title_bridge, pump
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

    def _leave(self):
        self._route = self._nonce = None
        self._expires = 0
        _remove(self.title_bridge, JOIN_OFFER, JOIN_CANCEL, JOIN_OPENED)
        with suppress(OSError, RuntimeError):
            self.transport.leave()

    async def receive_invites(self):
        from .native_session_exit import SESSION

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
            if self._role or self._nonce or _PAUSE_OFFER.fullmatch(_read(self.title_bridge, SESSION)):
                continue
            async with self._lock:
                if self._role:
                    continue
                self._leave()
                _status(self.title_bridge, "joining_lobby")
                try:
                    route = await self.transport.join(lobby)
                    if self._role or self.pump.done():
                        self._leave()
                        continue
                    self._route, self._nonce = route, uuid.uuid4().hex
                    self._expires = int(time.time()) + 60
                    runtime.write_text(self.title_bridge / JOIN_OFFER,
                                       f"1\t{self._nonce}\t{route.port}\t{self._expires}\n", newline="\n")
                    _status(self.title_bridge, "invite_ready")
                except (OSError, ValueError, RuntimeError):
                    self._leave()
                    _status(self.title_bridge, "invite_unavailable")

    @asynccontextmanager
    async def playing(self, address: str = "", port: int = 0, *, role: str):
        self._role = role
        try:
            async with self._lock:
                route = self._route
                if role != "guest" or route is None or (route.host, route.port) != (address, port):
                    self._leave()
                _remove(self.title_bridge, JOIN_OFFER, JOIN_CANCEL, JOIN_OPENED)
            yield
        finally:
            self._leave()
            self._role = None
            _status(self.title_bridge, "ready" if not self.pump.done() else "unavailable")

    @asynccontextmanager
    async def host_panel(self, bridge: Path, bind: str, port: int, certificate: str, control):
        address = ip_address(bind)
        if address.version != 4 or self.pump.done():
            _status(bridge, "unavailable", reason="ipv4_host_required" if address.version != 4 else "steam_offline")
            yield
            return
        fingerprint = sha256(certificate.encode("utf-8")).hexdigest()
        endpoint = "127.0.0.1" if address.is_unspecified else str(address)
        panel = asyncio.create_task(self._host_panel(bridge, endpoint, port, fingerprint, control))
        try:
            yield
        finally:
            panel.cancel()
            with suppress(asyncio.CancelledError):
                await panel
            _remove(bridge, HOST_OFFER, HOST_INVITE)

    async def _host_panel(self, bridge, address, port, fingerprint, control):
        from .native_session_exit import SESSION

        offer_path = bridge / HOST_OFFER
        last_token = None
        offer_state = "ready"
        _status(bridge, "ready")
        while not self.pump.done():
            match = _PAUSE_OFFER.fullmatch(_read(bridge, SESSION))
            token = match[1].decode("ascii") if match and int(match[2]) > time.time() else None
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
                    if self.transport.lobby is None:
                        await self.transport.host(port, address=address, fingerprint=fingerprint)
                    overlay = self.transport.show_invites()
                except (OSError, ValueError, RuntimeError):
                    offer_state = "ready"
                    _status(bridge, "invite_unavailable")
                    with suppress(OSError):
                        control.event("steam_invite_unavailable")
                else:
                    offer_state = "ready" if overlay else "friends"
                    _status(bridge, "lobby_open", overlay=overlay)
                    with suppress(OSError):
                        control.event("steam_lobby_open")
                finally:
                    with suppress(OSError):
                        runtime.write_text(offer_path, f"1\t{token}\t{offer_state}\n", newline="\n")
            await asyncio.sleep(.15)
        _remove(bridge, HOST_OFFER, HOST_INVITE)
        _status(bridge, "unavailable", reason="steam_offline")


@asynccontextmanager
async def installed_steam(work: Path, bridge: Path, argv, *, enabled: bool):
    """Keep Steam failures optional so Direct IP can start in ordinary installations."""
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
        api = Steamworks.open(path)
    except (OSError, ValueError, RuntimeError):
        _status(bridge, "unavailable", reason="steam_initialization")
        yield None
        return

    transport = SteamTransport(api)
    pump = asyncio.create_task(transport.run())
    session = SteamSession(transport, bridge, pump)
    try:
        cold_lobby = parse_launch_lobby(argv)
        with suppress(OSError, ValueError, RuntimeError):
            steam_lobby = parse_launch_lobby(shlex.split(api.launch_command_line(), posix=False))
            if steam_lobby is not None:
                cold_lobby = steam_lobby if cold_lobby in (None, steam_lobby) else None
        if cold_lobby is not None:
            transport.pending_invites.put_nowait(cold_lobby)
        _status(bridge, "ready")
        inbox = asyncio.create_task(session.receive_invites())
        try:
            yield session
        finally:
            inbox.cancel()
            with suppress(asyncio.CancelledError):
                await inbox
    finally:
        session._leave()
        pump.cancel()
        with suppress(asyncio.CancelledError, OSError, RuntimeError, ValueError):
            await pump
        transport.close()
