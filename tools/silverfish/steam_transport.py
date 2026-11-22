"""Carry the existing QUIC datagrams over Steam's private session transport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import ipaddress
import re
import secrets
import struct
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .steamworks import Steamworks


_LOOPBACK = "127.0.0.1"
_METADATA_KEY = "silverfishcoop"
_METADATA_VERSION = "1"
_PUBLIC_KEY = "silverfishcoop-public"
_HOST_NAME = "silverfishcoop-host"
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_HEADER = struct.Struct("!4sQQ")
_MAGIC = b"SFQ1"
_MAX_PACKET = 4096 - _HEADER.size
_MAX_MEMBERS = 5
_PUBLIC_LIMIT = 12
_MAX_PEER_FLOWS = 16
_FLOW_IDLE = 45.0
_LOBBY_TIMEOUT = 12.0
_POLL_INTERVAL = .01
_ROSTER_INTERVAL = .25
_CALLBACK_INVITE = 333
_CALLBACK_FRIEND_JOIN = 337
_CALLBACK_ENTER = 504
_CALLBACK_MATCH_LIST = 510
_CALLBACK_ROSTER = 506
_CALLBACK_CREATED = 513
_CALLBACK_SESSION_REQUEST = 1251


class SteamTransportError(RuntimeError):
    """A Steam lobby or relay failure safe to show in the game UI."""


def _steam_id(value: object) -> bool:
    return type(value) is int and 0 < value < 2**64


def parse_launch_lobby(argv: Sequence[str]) -> int | None:
    """Read the lobby Steam passed when it launched the game from an invite."""
    arguments = [index for index, value in enumerate(argv) if value == "+connect_lobby"]
    if len(arguments) != 1 or arguments[0] + 1 >= len(argv):
        return None
    value = argv[arguments[0] + 1]
    if not 1 <= len(value) <= 20 or not value.isascii() or not value.isdecimal():
        return None
    lobby = int(value)
    return lobby if _steam_id(lobby) else None


@dataclass(frozen=True, slots=True)
class SteamRoute:
    lobby: int
    peer_id: int
    host: str
    port: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class PublicLobby:
    lobby: int
    host_name: str


@dataclass(slots=True)
class _LobbyCall:
    callback: int
    lobby: int | None
    result: asyncio.Future[int]


@dataclass(slots=True)
class _GuestFlow:
    source: tuple[str, int]
    seen: float


@dataclass(slots=True)
class _HostFlow:
    transport: asyncio.DatagramTransport
    seen: float


class _Udp(asyncio.DatagramProtocol):
    def __init__(self, receive: Callable[[bytes, tuple[str, int]], None]) -> None:
        self.receive = receive

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        self.receive(data, address)

    def error_received(self, exc: Exception) -> None:
        # QUIC handles loss while the local connection changes or closes.
        pass


class SteamTransport:
    """Own one lobby and loopback tunnel on the embedded worker's event loop."""

    def __init__(self, api: Steamworks) -> None:
        self.api = api
        self.pending_invites: asyncio.Queue[int] = asyncio.Queue(maxsize=4)
        self.lobby: int | None = None
        self._host: tuple[str, int] | None = None
        self._guest: asyncio.DatagramTransport | None = None
        self._owner: int | None = None
        self._members: set[int] = set()
        self._sessions: set[int] = set()
        self._host_flows: dict[tuple[int, int], _HostFlow] = {}
        self._guest_flows: dict[int, _GuestFlow] = {}
        self._guest_sources: dict[tuple[str, int], int] = {}
        self._calls: dict[int, _LobbyCall] = {}
        self._search: tuple[int, asyncio.Future] | None = None
        self._opening = False
        self._join_presence = False
        self._closed = False
        self._running = False
        self._generation = 0
        self._roster_at = 0.0

    async def _lobby_call(self, requested: int | None, *, access: str = "friends") -> int:
        if self._closed or self._opening or self.lobby is not None:
            raise SteamTransportError("A Steam lobby is already active or opening.")
        if len(self._calls) >= 4 or any(entry.lobby == requested for entry in self._calls.values()):
            raise SteamTransportError("Steam is still finishing a previous lobby request.")
        handle = self.api.create_lobby(access=access) if requested is None else self.api.join_lobby(requested)
        if not handle or handle in self._calls:
            raise SteamTransportError("Steam could not start the lobby request.")
        future = asyncio.get_running_loop().create_future()
        self._calls[handle] = _LobbyCall(_CALLBACK_CREATED if requested is None else _CALLBACK_ENTER, requested, future)
        self._opening = True
        generation = self._generation
        try:
            lobby = await asyncio.wait_for(future, _LOBBY_TIMEOUT)
            if generation != self._generation:
                if not self._closed:
                    self.api.leave_lobby(lobby)
                raise SteamTransportError("The Steam lobby request was cancelled.")
            return lobby
        except TimeoutError as error:
            # Keep its handle until dispatch so a late success can leave its lobby.
            raise SteamTransportError("Steam did not answer the lobby request.") from error
        finally:
            self._opening = False

    async def host(self, port: int, *, address: str = _LOOPBACK, fingerprint: str, access: str = "friends") -> int:
        """Publish the chosen Steam lobby for an already bound local QUIC listener."""
        try:
            endpoint = ipaddress.IPv4Address(address)
            if endpoint.is_unspecified:
                endpoint = ipaddress.IPv4Address(_LOOPBACK)
        except (ValueError, TypeError) as error:
            raise SteamTransportError("Steam relay hosting requires an IPv4 listener.") from error
        if (
            endpoint.is_multicast
            or endpoint == ipaddress.IPv4Address("255.255.255.255")
            or type(port) is not int
            or not 0 < port < 65536
        ):
            raise SteamTransportError("Steam relay hosting requires a valid local endpoint.")
        if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
            raise SteamTransportError("Steam relay hosting requires a campaign identity.")
        if access not in ("invite", "friends", "public"):
            raise SteamTransportError("Choose a Steam lobby access mode.")
        lobby = await self._lobby_call(None, access=access)
        self.lobby = lobby
        try:
            self._owner = self.api.local_id
            if self.api.lobby_owner(lobby) != self._owner or not self.api.set_lobby_data(
                lobby, _METADATA_KEY, f"{_METADATA_VERSION}:{self._owner}:{fingerprint}",
            ):
                raise SteamTransportError("Steam could not publish the co-op lobby.")
            self._host = (str(endpoint), port)
            self._refresh_roster()
            if self.lobby != lobby or self.api.local_id not in self._members:
                raise SteamTransportError("Steam could not confirm the co-op lobby.")
            if access == "public":
                host_name = self._display_name(self.api.persona_name())
                if (not self.api.set_lobby_data(lobby, _HOST_NAME, host_name)
                        or not self.api.set_lobby_data(lobby, _PUBLIC_KEY, _METADATA_VERSION)):
                    raise SteamTransportError("Steam could not list the public co-op lobby.")
            self._join_presence = access != "invite" and self.api.set_join_presence(lobby)
            return lobby
        except BaseException:
            self.leave()
            raise

    @staticmethod
    def _display_name(value: str) -> str:
        return "".join(char for char in value if char.isprintable()).strip()[:26] or "Silverfish player"

    async def public_lobbies(self) -> tuple[PublicLobby, ...]:
        if self._closed or self._search is not None or self._opening or self.lobby is not None:
            raise SteamTransportError("The Steam lobby browser is unavailable.")
        future = asyncio.get_running_loop().create_future()
        handle = self.api.request_lobbies(_PUBLIC_KEY, _METADATA_VERSION, limit=_PUBLIC_LIMIT)
        self._search = handle, future
        try:
            async with asyncio.timeout(_LOBBY_TIMEOUT):
                return await future
        except TimeoutError as error:
            raise SteamTransportError("Steam did not return a lobby list.") from error
        finally:
            self._search = None

    def _lobby_results(self, data: bytes) -> tuple[PublicLobby, ...]:
        if len(data) != 4:
            raise SteamTransportError("Steam did not return a lobby list.")
        count, = struct.unpack("<I", data)
        results = []
        for lobby in dict.fromkeys(self.api.list_lobbies(min(count, _PUBLIC_LIMIT))):
            if not _steam_id(lobby) or self.api.lobby_data(lobby, _PUBLIC_KEY) != _METADATA_VERSION:
                continue
            owner = self.api.lobby_owner(lobby)
            if not _steam_id(owner) or owner == self.api.local_id:
                continue
            marker = self.api.lobby_data(lobby, _METADATA_KEY)
            prefix = f"{_METADATA_VERSION}:{owner}:"
            if marker.startswith(prefix) and _FINGERPRINT.fullmatch(marker[len(prefix):]):
                results.append(PublicLobby(lobby, self._display_name(self.api.lobby_data(lobby, _HOST_NAME))))
        return tuple(results)

    async def join(self, lobby: int) -> SteamRoute:
        """Join a Steam invite and bind the local endpoint for catalogue and gameplay."""
        if not _steam_id(lobby):
            raise SteamTransportError("The Steam invite has an invalid lobby.")
        lobby = await self._lobby_call(lobby)
        self.lobby = lobby
        generation = self._generation
        try:
            owner = self.api.lobby_owner(lobby)
            marker = self.api.lobby_data(lobby, _METADATA_KEY)
            version, member, fingerprint = marker.split(":", 2) if marker.count(":") == 2 else ("", "", "")
            if (
                not _steam_id(owner)
                or owner == self.api.local_id
                or version != _METADATA_VERSION
                or member != str(owner)
                or not _FINGERPRINT.fullmatch(fingerprint)
                or self.api.lobby_owner(lobby) != owner
            ):
                raise SteamTransportError("The Steam lobby is not a compatible Silverfish co-op session.")
            self._owner = owner
            self._refresh_roster()
            if not self._allowed(owner):
                raise SteamTransportError("The Steam lobby host is unavailable.")
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: _Udp(self._guest_udp), local_addr=(_LOOPBACK, 0),
            )
            if generation != self._generation:
                transport.close()
                raise SteamTransportError("The Steam lobby was closed before the relay was ready.")
            self._guest = transport
            return SteamRoute(lobby, owner, _LOOPBACK, transport.get_extra_info("sockname")[1], fingerprint)
        except BaseException:
            if generation == self._generation:
                self.leave()
            raise

    def show_invites(self) -> bool:
        if self.lobby is None or self._host is None:
            raise SteamTransportError("Start a Steam lobby before inviting friends.")
        opened = self.api.show_invites(self.lobby)
        if not opened and not self._join_presence:
            raise SteamTransportError("Steam could not open invites or publish Join Game.")
        return opened

    def leave(self) -> None:
        self._generation += 1
        lobby, self.lobby = self.lobby, None
        for call in self._calls.values():
            if not call.result.done():
                call.result.set_exception(SteamTransportError("The Steam lobby request was cancelled."))
        if self._guest is not None:
            self._guest.close()
            self._guest = None
        for flow in self._host_flows.values():
            flow.transport.close()
        self._host_flows.clear()
        self._guest_flows.clear()
        self._guest_sources.clear()
        self._members.clear()
        self._owner = None
        if self._join_presence:
            self.api.set_join_presence(None)
        self._join_presence = False
        self._host = None
        for peer in self._sessions:
            self.api.close_messages(peer)
        self._sessions.clear()
        if lobby is not None:
            self.api.leave_lobby(lobby)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self.leave()
            finally:
                self.api.close()

    def _allowed(self, peer: int | None) -> bool:
        return (
            self.lobby is not None
            and self.api.local_id in self._members
            and peer != self.api.local_id
            and peer in self._members
            and (self._host is not None or peer == self._owner)
        )

    def _refresh_roster(self) -> None:
        if self.lobby is None or self._owner is None:
            return
        owner = self.api.lobby_owner(self.lobby)
        if _steam_id(owner) and owner != self._owner:
            # Steam may transfer lobby ownership, but it cannot migrate the campaign host.
            self.leave()
            return
        members = set(self.api.lobby_members(self.lobby)) if owner == self._owner else set()
        if not members or len(members) > _MAX_MEMBERS or not all(map(_steam_id, members)):
            # A temporary Steam outage should not discard the UDP endpoints used by QUIC.
            self._members = set()
            return
        self._members = members
        for peer in self._sessions - members:
            self.api.close_messages(peer)
            self._sessions.remove(peer)
        for key, flow in list(self._host_flows.items()):
            if key[0] not in members:
                flow.transport.close()
                del self._host_flows[key]

    def _send(self, peer: int, token: int, payload: bytes) -> None:
        if self._allowed(peer) and 0 < len(payload) <= _MAX_PACKET:
            if self.api.send_datagram(peer, _HEADER.pack(_MAGIC, self.lobby, token) + payload):
                self._sessions.add(peer)

    def _remove_guest_flow(self, token: int) -> None:
        flow = self._guest_flows.pop(token)
        del self._guest_sources[flow.source]

    def _guest_udp(self, payload: bytes, source: tuple[str, int]) -> None:
        owner = self._owner
        if owner is None or not self._allowed(owner) or source[0] != _LOOPBACK or not 0 < len(payload) <= _MAX_PACKET:
            return
        token = self._guest_sources.get(source)
        now = time.monotonic()
        if token is None:
            if len(self._guest_flows) >= _MAX_PEER_FLOWS:
                self._remove_guest_flow(min(self._guest_flows, key=lambda key: self._guest_flows[key].seen))
            token = secrets.randbits(64)
            while token == 0 or token in self._guest_flows:
                token = secrets.randbits(64)
            self._guest_sources[source] = token
            self._guest_flows[token] = _GuestFlow(source, now)
        else:
            self._guest_flows[token].seen = now
        self._send(owner, token, payload)

    async def _receive(self, peer: int, packet: bytes) -> None:
        if not self._allowed(peer) or not _HEADER.size < len(packet) <= _HEADER.size + _MAX_PACKET:
            return
        magic, lobby, token = _HEADER.unpack_from(packet)
        if magic != _MAGIC or lobby != self.lobby or token == 0:
            return
        payload = packet[_HEADER.size:]
        now = time.monotonic()
        if self._host is not None:
            key = (peer, token)
            flow = self._host_flows.get(key)
            if flow is None:
                peer_flows = [existing for existing in self._host_flows if existing[0] == peer]
                if len(peer_flows) >= _MAX_PEER_FLOWS:
                    oldest = min(peer_flows, key=lambda existing: self._host_flows[existing].seen)
                    self._host_flows.pop(oldest).transport.close()
                generation = self._generation
                try:
                    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                        lambda: _Udp(lambda data, _: self._host_udp(generation, key, data)), remote_addr=self._host,
                    )
                except OSError:
                    return
                if generation != self._generation:
                    transport.close()
                    return
                flow = self._host_flows[key] = _HostFlow(transport, now)
            flow.seen = now
            flow.transport.sendto(payload)
        elif self._guest is not None and (flow := self._guest_flows.get(token)) is not None:
            flow.seen = now
            self._guest.sendto(payload, flow.source)

    def _host_udp(self, generation: int, key: tuple[int, int], payload: bytes) -> None:
        if generation == self._generation and (flow := self._host_flows.get(key)) is not None:
            flow.seen = time.monotonic()
            self._send(*key, payload)

    def _expire_flows(self, now: float) -> None:
        for key, flow in list(self._host_flows.items()):
            if now - flow.seen > _FLOW_IDLE:
                flow.transport.close()
                del self._host_flows[key]
        for token, flow in list(self._guest_flows.items()):
            if now - flow.seen > _FLOW_IDLE:
                self._remove_guest_flow(token)

    def _invite(self, lobby: int) -> None:
        if _steam_id(lobby) and lobby != self.lobby:
            if self.pending_invites.full():
                self.pending_invites.get_nowait()
            self.pending_invites.put_nowait(lobby)

    def _callback(self, callback: int, data: bytes, handle: int) -> None:
        if callback == _CALLBACK_MATCH_LIST and self._search is not None and self._search[0] == handle:
            _, future = self._search
            if not future.done():
                try:
                    future.set_result(self._lobby_results(data))
                except (OSError, ValueError, RuntimeError) as error:
                    future.set_exception(error)
            return
        if handle and (call := self._calls.get(handle)) is not None:
            if call.callback != callback:
                return
            del self._calls[handle]
            if callback == _CALLBACK_CREATED and len(data) == 16:
                status, lobby = struct.unpack("<i4xQ", data)
            elif callback == _CALLBACK_ENTER and len(data) == 24:
                lobby, _, _, status = struct.unpack("<QIB3xI4x", data)
            else:
                status, lobby = 0, 0
            accepted = status == 1 and _steam_id(lobby) and (call.lobby is None or call.lobby == lobby)
            if call.result.done():
                if accepted and lobby != self.lobby:
                    self.api.leave_lobby(lobby)
            elif accepted:
                call.result.set_result(lobby)
            else:
                call.result.set_exception(SteamTransportError(f"Steam refused the lobby request ({status})."))
            return
        if callback == _CALLBACK_INVITE and len(data) == 16:
            lobby, _friend = struct.unpack("<QQ", data)
            self._invite(lobby)
        elif callback == _CALLBACK_FRIEND_JOIN and len(data) == 264:
            connect, separator, _ = data[8:].partition(b"\x00")
            if separator and (match := re.fullmatch(rb"\+connect_lobby ([0-9]{1,20})", connect)):
                self._invite(int(match[1]))
        elif callback == _CALLBACK_ROSTER and self.lobby is not None:
            self._refresh_roster()
        elif callback == _CALLBACK_SESSION_REQUEST:
            self._refresh_roster()
            peer = self.api.callback_peer(data)
            if peer is not None and self._allowed(peer) and self.api.accept_messages(peer):
                self._sessions.add(peer)

    async def run(self) -> None:
        if self._running:
            raise SteamTransportError("The Steam relay is already running.")
        self._running = True
        try:
            while not self._closed:
                now = time.monotonic()
                if now >= self._roster_at:
                    self._roster_at = now + _ROSTER_INTERVAL
                    self._refresh_roster()
                    self._expire_flows(now)
                for callback, data, handle in self.api.poll_callbacks():
                    self._callback(callback, data, handle)
                for peer, packet in self.api.receive_datagrams():
                    await self._receive(peer, packet)
                await asyncio.sleep(_POLL_INTERVAL)
        finally:
            self._running = False
            self.close()
