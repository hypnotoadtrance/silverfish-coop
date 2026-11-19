"""Private QUIC link for the Direct IP prototype's loopback-only Unreal sockets."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import logging
import math
import ssl
import struct
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic import events
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

__all__ = [
    "ClientLink", "HostLink", "LinkError", "ServerIdentity",
    "checkpoint_action_timeout", "load_server_identity", "make_server_identity", "start_client", "start_host",
]

_ALPN = "silverfish-coop-direct/1"
_TLS_NAME = "silverfish-coop.invalid"
_ADMISSION_STREAM = 0
_MAX_ADMISSION = 4096
_MAX_ACTION_BODY = 32 * 1024
_MAX_ACTION_FRAME = _MAX_ACTION_BODY + 64
_MAX_ACTIONS = 8
_ACTION_TIMEOUT = 8.0
_CHECKPOINT_ACTION_TIMEOUTS = {
    "native-authored-pickup-claim": 20.0,
    "native-finite-shop-list": 15.0,
    "native-finite-shop-buy": 15.0,
    "native-finite-shop-sale-quote": 15.0,
    "native-finite-shop-sell": 15.0,
    "native-guest-bandage": 15.0,
    "native-quest-interact": 15.0,
    "native-quest-list": 15.0,
    "native-turret-photo-capture": 20.0,
    "native-turret-photo-shutter": 20.0,
}
_QUEST_ACTIONS = {"native-quest-start", "native-quest-objective", "native-quest-turnin"}
_CHECKPOINT_QUESTS = {"Photo_DB_Turrets", "Clt_ComputerBoard", "Clt_CoupReport_01", "DepotDocs", "Jmr_FloodPlains"}
_MAX_CERTIFICATE = 8192
_ADMISSION_TIMEOUT = 8.0
_IDLE_TIMEOUT = 12.0
_KEEPALIVE_INTERVAL = 3.0
_MAX_GAME_PACKET = 65507
_FRAGMENT_SIZE = 1024
_FRAGMENT_HEADER = struct.Struct("!IHH")
_FRAGMENT_TTL = 1.0
_MAX_ASSEMBLIES = 32
_MAX_QUEUED_FRAMES = 256
_MAX_CACHED_GUESTS = 16
_MAX_CATALOG_LINKS = 16
_MAX_HOST_TASKS = 32
_APP_ERROR = 0x100
_LOG = logging.getLogger(__name__)

Address = tuple[str, int]
Admission = dict[str, Any]
Admit = Callable[[Admission], Admission | Awaitable[Admission]]
OnDisconnect = Callable[[str], None | Awaitable[None]]
Action = dict[str, Any]
OnRequest = Callable[[str, Action], Action | Awaitable[Action]]

_ACTION_ERRORS = {
    "disabled": "application requests are disabled",
    "invalid": "invalid application request",
    "timed_out": "host application request timed out",
    "failed": "host application request failed",
}


def checkpoint_action_timeout(payload: Action) -> float | None:
    """Return the bounded request budget for a stock action that can take a checkpoint."""
    if payload.keys() != {"action", "operation_id", "target"} or not isinstance(payload["action"], str):
        return None
    action, target = payload["action"], payload["target"]
    if action == "native-quest-turnin" and target == "Clt_Wire":
        return 22.0
    if action in _QUEST_ACTIONS and isinstance(target, str) and target in _CHECKPOINT_QUESTS:
        return 20.0
    return _CHECKPOINT_ACTION_TIMEOUTS.get(action)


class LinkError(ConnectionError):
    """A link could not be opened or sent an invalid response."""


@dataclass(frozen=True)
class ServerIdentity:
    certificate: x509.Certificate
    private_key: ec.EllipticCurvePrivateKey = field(repr=False)

    @property
    def certificate_pem(self) -> str:
        return self.certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def make_server_identity() -> ServerIdentity:
    """Generate a host identity. Share its public certificate through a private invite."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, _TLS_NAME)])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(_TLS_NAME)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return ServerIdentity(certificate, key)


def _load_pinned_certificate(pem: str | bytes) -> tuple[bytes, x509.Certificate]:
    try:
        if not isinstance(pem, (str, bytes)) or not 0 < len(pem) <= _MAX_CERTIFICATE:
            raise ValueError
        encoded = pem.encode("ascii") if isinstance(pem, str) else pem
        certificates = x509.load_pem_x509_certificates(encoded)
        if len(certificates) != 1:
            raise ValueError
        certificate = certificates[0]
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        if constraints.value.ca or _TLS_NAME not in names.value.get_values_for_type(x509.DNSName):
            raise ValueError
        certificate.verify_directly_issued_by(certificate)
    except (ValueError, InvalidSignature, UnsupportedAlgorithm, x509.ExtensionNotFound):
        raise LinkError("invalid pinned server certificate") from None
    return encoded, certificate


def load_server_identity(certificate_pem: str | bytes, private_key_pem: str | bytes) -> ServerIdentity:
    """Load a previously generated identity from private, host-controlled storage."""
    _, certificate = _load_pinned_certificate(certificate_pem)
    try:
        encoded = private_key_pem.encode("ascii") if isinstance(private_key_pem, str) else private_key_pem
        key = serialization.load_pem_private_key(encoded, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError
        expected = certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        actual = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if expected != actual:
            raise ValueError
    except (TypeError, ValueError, UnsupportedAlgorithm):
        raise LinkError("invalid host private key") from None
    return ServerIdentity(certificate, key)


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _address(transport: asyncio.BaseTransport) -> Address:
    host, port, *_ = transport.get_extra_info("sockname")
    return host, port


def _configuration(is_client: bool) -> QuicConfiguration:
    return QuicConfiguration(
        is_client=is_client,
        alpn_protocols=[_ALPN],
        idle_timeout=_IDLE_TIMEOUT,
        max_data=_MAX_ADMISSION + _MAX_ACTION_FRAME * _MAX_ACTIONS,
        max_stream_data=_MAX_ACTION_FRAME,
        max_datagram_size=1200,
        max_datagram_frame_size=_FRAGMENT_SIZE + _FRAGMENT_HEADER.size + 16,
    )


def _encode(message: Admission) -> bytes:
    try:
        payload = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise LinkError("invalid admission message") from None
    if len(payload) > _MAX_ADMISSION:
        raise LinkError("admission message too large")
    return payload


def _decode(payload: bytes) -> Admission:
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise LinkError("invalid admission message") from None
    if not isinstance(message, dict):
        raise LinkError("invalid admission message")
    return message


def _validate_request(request: Admission) -> None:
    if not isinstance(request, dict):
        raise LinkError("invalid admission request")
    limits = {"guest_id": 96, "credential": 512, "character_id": 96, "game_build": 128}
    for name, maximum in limits.items():
        value = request.get(name)
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise LinkError(f"invalid {name}")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise LinkError(f"invalid {name}")
    protocol = request.get("protocol_version")
    if type(protocol) is not int or not 1 <= protocol <= 2**31 - 1:
        raise LinkError("invalid protocol_version")
    if "catalog" in request and type(request["catalog"]) is not bool:
        raise LinkError("invalid catalog flag")


def _validate_response(response: Admission) -> None:
    if not isinstance(response, dict) or type(response.get("accepted")) is not bool:
        raise LinkError("invalid admission response")
    if "game_access" in response and type(response["game_access"]) is not bool:
        raise LinkError("invalid game_access flag")


def _rejection(reason: str) -> Admission:
    return {"accepted": False, "reason": reason}


def _encode_action(message: Action, limit: int = _MAX_ACTION_BODY) -> bytes:
    if not isinstance(message, dict):
        raise LinkError("application message must be an object")
    try:
        data = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise LinkError("invalid application message") from None
    if len(data) > limit:
        raise LinkError("application message too large")
    return data


def _finite_number(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON object key")
    return result


def _decode_action(data: bytes) -> Action:
    try:
        message = json.loads(data.decode("utf-8"), parse_float=_finite_number,
                             parse_constant=_finite_number, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError):
        raise LinkError("invalid application message") from None
    if not isinstance(message, dict):
        raise LinkError("application message must be an object")
    return message


def _application_stream(stream_id: int) -> bool:
    return stream_id > _ADMISSION_STREAM and stream_id % 4 == 0


@dataclass
class _IncomingAction:
    body: bytearray = field(default_factory=bytearray)
    deadline: asyncio.TimerHandle | None = None
    abandoned: bool = False


@dataclass
class _OutgoingAction:
    response: asyncio.Future[Action]
    body: bytearray = field(default_factory=bytearray)


@dataclass
class _PartialPacket:
    size: int
    created_at: float
    chunks: dict[int, bytes] = field(default_factory=dict)


class _Reassembler:
    def __init__(self) -> None:
        self._pending: dict[int, _PartialPacket] = {}

    def receive(self, frame: bytes, now: float) -> bytes | None:
        if len(frame) <= _FRAGMENT_HEADER.size:
            return None
        packet_id, size, index = _FRAGMENT_HEADER.unpack_from(frame)
        chunk = frame[_FRAGMENT_HEADER.size:]
        start = index * _FRAGMENT_SIZE
        if not 0 < size <= _MAX_GAME_PACKET or start >= size:
            return None
        if len(chunk) != min(_FRAGMENT_SIZE, size - start):
            return None
        if size <= _FRAGMENT_SIZE:
            return chunk

        for key, packet in list(self._pending.items()):
            if now - packet.created_at > _FRAGMENT_TTL:
                del self._pending[key]

        partial = self._pending.get(packet_id)
        if partial is None:
            if len(self._pending) >= _MAX_ASSEMBLIES:
                return None
            partial = _PartialPacket(size, now)
            self._pending[packet_id] = partial
        if partial.size != size:
            return None
        partial.chunks.setdefault(index, chunk)
        expected = (size + _FRAGMENT_SIZE - 1) // _FRAGMENT_SIZE
        if len(partial.chunks) != expected:
            return None
        del self._pending[packet_id]
        return b"".join(partial.chunks[index] for index in range(expected))


class _LocalUdp(asyncio.DatagramProtocol):
    def __init__(self, receive: Callable[[bytes, Address], None]) -> None:
        self.receive = receive

    def datagram_received(self, data: bytes, address: Address) -> None:
        self.receive(data, address)

    def error_received(self, exc: Exception) -> None:
        # Unreal can briefly close its local socket during travel.
        pass


class _LinkProtocol(QuicConnectionProtocol):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.terminated = False
        self.authorized = False
        self.game_access = False
        self._admission_buffer = bytearray()
        self._admission_finished = False
        self._reassembler = _Reassembler()
        self._next_packet_id = 0

    def send_admission(self, payload: bytes) -> None:
        self._quic.send_stream_data(_ADMISSION_STREAM, payload, end_stream=True)
        self.transmit()

    def forward_game_packet(self, data: bytes) -> None:
        if not self.authorized or not self.game_access or self.terminated or not 0 < len(data) <= _MAX_GAME_PACKET:
            return
        count = (len(data) + _FRAGMENT_SIZE - 1) // _FRAGMENT_SIZE
        # aioquic 1.3.0 queues DATAGRAM writes until congestion control permits them.
        if len(self._quic._datagrams_pending) + count > _MAX_QUEUED_FRAMES:
            return
        packet_id = self._next_packet_id
        self._next_packet_id = (packet_id + 1) % 2**32
        for index in range(count):
            start = index * _FRAGMENT_SIZE
            frame = _FRAGMENT_HEADER.pack(packet_id, len(data), index)
            self._quic.send_datagram_frame(frame + data[start:start + _FRAGMENT_SIZE])
        self.transmit()

    def _admission_payload(self, event: events.StreamDataReceived) -> bytes | None:
        if event.stream_id != _ADMISSION_STREAM or self._admission_finished:
            self.close(error_code=_APP_ERROR, reason_phrase="unexpected admission stream")
            return None
        if len(self._admission_buffer) + len(event.data) > _MAX_ADMISSION:
            self.close(error_code=_APP_ERROR, reason_phrase="admission message too large")
            return None
        self._admission_buffer.extend(event.data)
        if event.end_stream:
            self._admission_finished = True
            return bytes(self._admission_buffer)
        return None

    def _game_payload(self, event: events.DatagramFrameReceived) -> bytes | None:
        if self.authorized and self.game_access and not self.terminated:
            return self._reassembler.receive(event.data, self._loop.time())
        return None


class _HostProtocol(_LinkProtocol):
    def __init__(self, host: HostLink, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.host = host
        self.guest_id: str | None = None
        self._actions: dict[int, _IncomingAction] = {}

    def quic_event_received(self, event: events.QuicEvent) -> None:
        if isinstance(event, events.StreamDataReceived):
            if event.stream_id == _ADMISSION_STREAM:
                payload = self._admission_payload(event)
                if payload is not None:
                    if len(self.host._tasks) < _MAX_HOST_TASKS:
                        self.host._spawn(self._respond, payload)
                    else:
                        self.close(error_code=_APP_ERROR, reason_phrase="host busy")
            else:
                self._read_action(event)
        elif isinstance(event, (events.StreamReset, events.StopSendingReceived)):
            action = self._actions.get(event.stream_id)
            if action is not None:
                action.abandoned = True
                # Cancel partial reads. Submitted callbacks can finish, so disconnection
                # and timeout are never guarantees about whether the action took effect.
                if action.deadline is not None and isinstance(event, events.StreamReset):
                    action.deadline.cancel()
                    action.deadline = None
                    self._actions.pop(event.stream_id)
        elif isinstance(event, events.DatagramFrameReceived):
            packet = self._game_payload(event)
            if packet is not None:
                self.host._to_game(self, packet)
        elif isinstance(event, events.ConnectionTerminated):
            self.terminated = True
            self.authorized = False
            for action in self._actions.values():
                if action.deadline is not None:
                    action.deadline.cancel()
            self._actions.clear()
            if self.guest_id is not None:
                self.host._spawn(self.host._release, self)

    def _read_action(self, event: events.StreamDataReceived) -> None:
        stream_id = event.stream_id
        if not self.authorized or not _application_stream(stream_id):
            self.close(error_code=_APP_ERROR, reason_phrase="unexpected application stream")
            return

        action = self._actions.get(stream_id)
        if action is None:
            if len(self._actions) >= _MAX_ACTIONS or len(self.host._tasks) >= _MAX_HOST_TASKS:
                self.close(error_code=_APP_ERROR, reason_phrase="application request limit exceeded")
                return
            action = self._actions[stream_id] = _IncomingAction()
            action.deadline = self._loop.call_later(_ACTION_TIMEOUT, self._action_read_timeout)
        if action.deadline is None:
            return
        if len(action.body) + len(event.data) > _MAX_ACTION_BODY:
            self.close(error_code=_APP_ERROR, reason_phrase="application request too large")
            return
        action.body.extend(event.data)
        if event.end_stream:
            action.deadline.cancel()
            action.deadline = None
            if len(self.host._tasks) >= _MAX_HOST_TASKS:
                self.close(error_code=_APP_ERROR, reason_phrase="host busy")
                return
            self.host._spawn(self._respond_to_action, stream_id, action)

    def _action_read_timeout(self) -> None:
        self.close(error_code=_APP_ERROR, reason_phrase="application stream timed out")

    async def _respond_to_action(self, stream_id: int, action: _IncomingAction) -> None:
        try:
            if not self.authorized or self.terminated or action.abandoned:
                return
            response = await self.host._run_action(self, bytes(action.body))
            if self.authorized and not self.terminated and not action.abandoned:
                self._quic.send_stream_data(stream_id, response, end_stream=True)
                self.transmit()
        finally:
            self._actions.pop(stream_id, None)

    async def _respond(self, payload: bytes) -> None:
        try:
            request = _decode(payload)
            _validate_request(request)
        except LinkError:
            self.send_admission(_encode(_rejection("invalid_request")))
            return
        response = await self.host._admit(self, request)
        if not self.terminated:
            self.send_admission(response)


class HostLink:
    """Authenticated guest links with separate Unreal-facing UDP endpoints."""

    def __init__(
        self,
        game_address: Address,
        admit: Admit,
        on_disconnect: OnDisconnect | None,
        on_request: OnRequest | None = None,
        *,
        max_game_guests: int = 1,
    ) -> None:
        if type(max_game_guests) is not int or not 1 <= max_game_guests <= _MAX_CACHED_GUESTS:
            raise ValueError(f"Allow between 1 and {_MAX_CACHED_GUESTS} concurrent game guests")
        self._game_address = game_address
        self._max_game_guests = max_game_guests
        self._admit_callback = admit
        self._disconnect_callback = on_disconnect
        self._request_callback = on_request
        self._guest_sockets: dict[str, asyncio.DatagramTransport] = {}
        self._protocols: dict[str, _HostProtocol] = {}
        self._native_admission: frozenset[str] | None = None
        self._game_route_holds = 0
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def address(self) -> Address:
        # aioquic's public serve() result exposes the datagram transport here.
        return _address(self._server._transport)

    def guest_endpoint(self, guest_id: str) -> Address | None:
        transport = self._guest_sockets.get(guest_id)
        return _address(transport) if transport is not None else None

    def connected(self, guest_id: str) -> bool:
        """Return whether the guest currently has an admitted game link."""
        protocol = self._protocols.get(guest_id)
        return bool(protocol and protocol.authorized and protocol.game_access)

    def restrict_game_routes(self, allowed: set[str] | None) -> None:
        """Gate Unreal packets independently of authenticated control traffic."""
        self._native_admission = None if allowed is None else frozenset(allowed)

    @contextmanager
    def hold_game_routes(self) -> Iterator[None]:
        """Pause gameplay UDP without altering the current admissions or control streams."""
        self._game_route_holds += 1
        try:
            yield
        finally:
            self._game_route_holds -= 1

    def _native_route_permitted(self, guest_id: str) -> bool:
        return self._game_route_holds == 0 and (self._native_admission is None or guest_id in self._native_admission)

    async def release_guest(self, guest_id: str) -> bool:
        """Retire a disconnected guest's cached UDP endpoint after its grace period."""
        async with self._lock:
            protocol = self._protocols.get(guest_id)
            if protocol and protocol.game_access:
                return False
            transport = self._guest_sockets.pop(guest_id, None)
            if transport is None:
                return False
            transport.close()
            return True

    async def abort_guest_route(self, guest_id: str) -> None:
        """Close the admitted peer and its route after failed native enrollment."""
        async with self._lock:
            protocol = self._protocols.get(guest_id)
            if protocol and protocol.game_access:
                protocol.close(error_code=_APP_ERROR, reason_phrase="native enrollment failed")
                await self._release_locked(protocol)
            route = self._guest_sockets.pop(guest_id, None)
            if route is not None:
                route.close()

    async def _create_game_route(self, guest_id: str) -> asyncio.DatagramTransport:
        transport = None

        def receive(data: bytes, _: Address) -> None:
            if transport is not None and self._guest_sockets.get(guest_id) is transport:
                self._from_game(guest_id, data)

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: _LocalUdp(receive),
            local_addr=(self._game_address[0], 0),
            remote_addr=self._game_address,
        )
        return transport

    async def refresh_guest_game_route(self, guest_id: str) -> None:
        """Replace one authorized Unreal relay socket without interrupting QUIC."""
        async with self._lock:
            if self._closed or not self.connected(guest_id) or not self._native_route_permitted(guest_id):
                raise LinkError("the guest's Unreal relay route is unavailable")
            prior = self._guest_sockets.get(guest_id)
            if prior is None:
                raise LinkError("the guest has no Unreal relay route")
            route = await self._create_game_route(guest_id)
            if self._closed or not self.connected(guest_id) or not self._native_route_permitted(guest_id):
                route.close()
                raise LinkError("the guest's Unreal relay route changed")
            self._guest_sockets[guest_id] = route
            prior.close()

    async def refresh_game_routes(self) -> None:
        """Rebuild Unreal relay routes after hard travel without closing the QUIC link.

        Call once the new Unreal listener is ready. Reconnects alone continue to
        reuse existing routes. A reserved guest gets a fresh route on readmission.
        """
        async with self._lock:
            if self._closed:
                raise LinkError("host link is closed")
            refreshed: dict[str, asyncio.DatagramTransport] = {}
            try:
                for guest_id in self._protocols:
                    if self.connected(guest_id):
                        refreshed[guest_id] = await self._create_game_route(guest_id)
                if self._closed:
                    raise LinkError("host link is closed")
                for guest_id in tuple(refreshed):
                    if not self.connected(guest_id):
                        refreshed.pop(guest_id).close()
            except BaseException as error:
                for route in refreshed.values():
                    route.close()
                if isinstance(error, OSError) and not isinstance(error, LinkError):
                    raise LinkError("could not refresh the Unreal relay route") from None
                raise

            old_routes, self._guest_sockets = self._guest_sockets, refreshed
            for route in old_routes.values():
                route.close()

    def _spawn(self, callback: Callable[..., Coroutine[Any, Any, None]], *args: Any) -> None:
        # aioquic can deliver termination events after the server has closed.
        # HostLink.close releases the active guests and drains its tasks.
        if self._closed:
            return
        task = asyncio.create_task(callback(*args))
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _LOG.warning("Host link task failed")

    async def _admit(self, protocol: _HostProtocol, request: Admission) -> bytes:
        guest_id = request["guest_id"]
        catalog = request.get("catalog") is True
        async with self._lock:
            if self._closed or protocol.terminated:
                return _encode(_rejection("host_unavailable"))
            if guest_id in self._protocols:
                return _encode(_rejection("guest_already_connected"))
            games = sum(current.game_access for current in self._protocols.values())
            catalogs = len(self._protocols) - games
            if catalog and catalogs >= _MAX_CATALOG_LINKS:
                return _encode(_rejection("catalog_capacity"))
            if not catalog and games >= self._max_game_guests:
                reason = "guest_already_connected" if self._max_game_guests == 1 else "party_full"
                return _encode(_rejection(reason))

            transport = None
            try:
                async with asyncio.timeout(_ADMISSION_TIMEOUT):
                    response = self._admit_callback(request)
                    if inspect.isawaitable(response):
                        response = await response
                if isinstance(response, dict) and response.get("accepted") is True:
                    # Take ownership of a game reservation before validating the
                    # result or opening a relay. Failures below release it.
                    protocol.guest_id = guest_id
                    protocol.game_access = response.get("game_access") is not False
                    self._protocols[guest_id] = protocol
                _validate_response(response)
                payload = _encode(response)
                if not response["accepted"]:
                    return payload
                if catalog and protocol.game_access:
                    raise LinkError("catalog admission cannot grant Unreal access")
                if not protocol.game_access and catalogs >= _MAX_CATALOG_LINKS:
                    return _encode(_rejection("catalog_capacity"))
                if protocol.terminated or self._closed:
                    return _encode(_rejection("host_unavailable"))

                if protocol.game_access and guest_id not in self._guest_sockets:
                    if len(self._guest_sockets) >= _MAX_CACHED_GUESTS:
                        return _encode(_rejection("guest_endpoint_capacity"))
                    transport = await self._create_game_route(guest_id)
                if protocol.terminated or self._closed:
                    return _encode(_rejection("host_unavailable"))
                if transport is not None:
                    self._guest_sockets[guest_id] = transport
                protocol.authorized = True
                return payload
            except Exception:
                _LOG.warning("Host link admission failed")
                return _encode(_rejection("admission_failed"))
            finally:
                if not protocol.authorized and self._protocols.get(guest_id) is protocol:
                    if transport is not None:
                        transport.close()
                    await self._release_locked(protocol)

    async def _run_action(self, protocol: _HostProtocol, body: bytes) -> bytes:
        guest_id = protocol.guest_id
        if self._closed or not protocol.authorized or guest_id is None or self._protocols.get(guest_id) is not protocol:
            return _encode_action({"error": "failed"})
        if self._request_callback is None:
            return _encode_action({"error": "disabled"})
        try:
            payload = _decode_action(body)
        except LinkError:
            return _encode_action({"error": "invalid"})
        try:
            async with asyncio.timeout(checkpoint_action_timeout(payload) or _ACTION_TIMEOUT):
                result = self._request_callback(guest_id, payload)
                if inspect.isawaitable(result):
                    result = await result
                if not isinstance(result, dict):
                    raise LinkError("invalid application callback response")
                _encode_action(result)
                return _encode_action({"result": result}, limit=_MAX_ACTION_FRAME)
        except TimeoutError:
            return _encode_action({"error": "timed_out"})
        except Exception:
            _LOG.warning("Host application request failed")
            return _encode_action({"error": "failed"})

    async def _release(self, protocol: _HostProtocol) -> None:
        async with self._lock:
            await self._release_locked(protocol)

    async def _release_locked(self, protocol: _HostProtocol) -> None:
        guest_id = protocol.guest_id
        if guest_id is None or self._protocols.get(guest_id) is not protocol:
            return
        del self._protocols[guest_id]
        protocol.authorized = False
        was_game_link = protocol.game_access
        protocol.game_access = False
        if not was_game_link or self._disconnect_callback is None:
            return
        try:
            async with asyncio.timeout(_ADMISSION_TIMEOUT):
                result = self._disconnect_callback(guest_id)
                if inspect.isawaitable(result):
                    await result
        except Exception:
            _LOG.warning("Host link disconnect callback failed")

    def _to_game(self, protocol: _HostProtocol, data: bytes) -> None:
        guest_id = protocol.guest_id
        if guest_id is None or self._protocols.get(guest_id) is not protocol:
            return
        if protocol.authorized and protocol.game_access and self._native_route_permitted(guest_id):
            transport = self._guest_sockets.get(guest_id)
            if transport is not None:
                transport.sendto(data)

    def _from_game(self, guest_id: str, data: bytes) -> None:
        protocol = self._protocols.get(guest_id)
        if protocol is not None and self._native_route_permitted(guest_id):
            protocol.forward_game_packet(data)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            for protocol in tuple(self._protocols.values()):
                await self._release_locked(protocol)
            self._server.close()
            for transport in self._guest_sockets.values():
                transport.close()
            self._guest_sockets.clear()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        # Give the QUIC close send a turn before finishing the UDP transport.
        # Windows may otherwise leave a socket with an in-flight write open.
        await asyncio.sleep(0)
        self._server._transport.abort()
        await asyncio.sleep(0)


async def start_host(
    bind_host: str,
    bind_port: int,
    game_host: str,
    game_port: int,
    identity: ServerIdentity,
    admit: Admit,
    on_disconnect: OnDisconnect | None = None,
    *,
    on_request: OnRequest | None = None,
    max_game_guests: int = 1,
) -> HostLink:
    """Open a private game relay and optionally serve admitted application requests."""
    if not _is_loopback(game_host):
        raise ValueError("Unreal relay endpoint must be a loopback address")
    link = HostLink((game_host, game_port), admit, on_disconnect, on_request, max_game_guests=max_game_guests)
    configuration = _configuration(is_client=False)
    configuration.certificate = identity.certificate
    configuration.private_key = identity.private_key
    link._server = await serve(
        bind_host, bind_port, configuration=configuration,
        create_protocol=lambda *args, **kwargs: _HostProtocol(link, *args, **kwargs),
        retry=True,
    )
    return link


class _ClientProtocol(_LinkProtocol):
    def __init__(self, client: ClientLink, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.client = client
        self.handshake = asyncio.Event()
        self.reply = asyncio.Event()
        self.response: Admission | None = None
        self._actions: dict[int, _OutgoingAction] = {}

    def quic_event_received(self, event: events.QuicEvent) -> None:
        if isinstance(event, events.HandshakeCompleted):
            self.handshake.set()
        elif isinstance(event, events.StreamDataReceived):
            if event.stream_id == _ADMISSION_STREAM:
                payload = self._admission_payload(event)
                if payload is not None:
                    try:
                        self.response = _decode(payload)
                        _validate_response(self.response)
                        self.authorized = self.response["accepted"]
                        self.game_access = self.authorized and self.response.get("game_access", True)
                    except LinkError:
                        self.response = None
                    self.reply.set()
            else:
                self._read_action_response(event)
        elif isinstance(event, (events.StreamReset, events.StopSendingReceived)):
            action = self._actions.get(event.stream_id)
            if action is not None and not action.response.done():
                action.response.set_exception(LinkError("application request stream closed"))
        elif isinstance(event, events.DatagramFrameReceived):
            packet = self._game_payload(event)
            if packet is not None:
                self.client._to_game(packet)
        elif isinstance(event, events.ConnectionTerminated):
            self.terminated = True
            self.authorized = False
            self.handshake.set()
            self.reply.set()
            for action in self._actions.values():
                if not action.response.done():
                    action.response.set_exception(LinkError("link disconnected during application request"))

    async def request(self, body: bytes, timeout: float) -> Action:
        if len(self._actions) >= _MAX_ACTIONS:
            raise LinkError("too many application requests pending")
        stream_id = self._quic.get_next_available_stream_id()
        action = self._actions[stream_id] = _OutgoingAction(self._loop.create_future())
        try:
            self._quic.send_stream_data(stream_id, body, end_stream=True)
            self.transmit()
            async with asyncio.timeout(timeout):
                return await action.response
        except TimeoutError:
            raise LinkError("application request timed out") from None
        finally:
            self._actions.pop(stream_id, None)
            if not action.response.done():
                action.response.cancel()

    def _read_action_response(self, event: events.StreamDataReceived) -> None:
        stream_id = event.stream_id
        if (not self.authorized or not _application_stream(stream_id)
                or stream_id >= self._quic.get_next_available_stream_id()):
            self.close(error_code=_APP_ERROR, reason_phrase="unexpected application response")
            return
        action = self._actions.get(stream_id)
        if action is None or action.response.done():
            return
        if len(action.body) + len(event.data) > _MAX_ACTION_FRAME:
            action.response.set_exception(LinkError("application response too large"))
            self.close(error_code=_APP_ERROR, reason_phrase="application response too large")
            return
        action.body.extend(event.data)
        if not event.end_stream:
            return
        try:
            envelope = _decode_action(bytes(action.body))
            result = envelope.get("result")
            if envelope.keys() == {"result"} and isinstance(result, dict):
                action.response.set_result(result)
                return
            error = envelope.get("error")
            if envelope.keys() == {"error"} and isinstance(error, str) and error in _ACTION_ERRORS:
                raise LinkError(_ACTION_ERRORS[error])
            raise LinkError("invalid application response")
        except LinkError as error:
            action.response.set_exception(error)

    async def keepalive(self) -> None:
        while self.authorized and not self.terminated:
            await asyncio.sleep(_KEEPALIVE_INTERVAL)
            if self.authorized and not self.terminated:
                # No response future is needed. QUIC's idle timeout handles loss.
                self._quic.send_ping(0)
                self.transmit()


@asynccontextmanager
async def _open_client_protocol(client: ClientLink, address: Address, configuration: QuicConfiguration):
    connection = QuicConnection(configuration=configuration)
    # Use a connected UDP socket so the OS selects the source address for this host.
    # A loopback destination then keeps the QUIC client socket bound to loopback too.
    transport, protocol = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: _ClientProtocol(client, connection), remote_addr=address,
    )
    try:
        protocol.connect(transport.get_extra_info("peername"))
        yield protocol
    finally:
        try:
            protocol.close()
            await protocol.wait_closed()
        finally:
            transport.close()


class ClientLink:
    """Runs a local UDP tunnel and preserves its address across QUIC reconnects."""

    def __init__(self, *, catalog_only: bool = False) -> None:
        self._lock = asyncio.Lock()
        self._closed = False
        self._catalog_only = catalog_only
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _ClientProtocol | None = None
        self._context = None
        self._keepalive: asyncio.Task[None] | None = None
        self._game_endpoint: Address | None = None
        self.game_local_datagrams = 0
        self.game_packets_sent = 0
        self.game_packets_received = 0

    @property
    def address(self) -> Address:
        if self._transport is None:
            raise LinkError("client link has no local Unreal endpoint")
        return _address(self._transport)

    @property
    def game_access(self) -> bool:
        protocol = self._protocol
        return bool(protocol and self.connected and protocol.game_access)

    @property
    def connected(self) -> bool:
        protocol = self._protocol
        return bool(protocol and protocol.authorized and not protocol.terminated)

    async def connect(
        self,
        host: str,
        port: int,
        certificate_pem: str,
        request: Admission,
        timeout: float = 10.0,
    ) -> Admission:
        _validate_request(request)
        if self._catalog_only and request.get("catalog") is not True:
            raise LinkError("this client requires catalog admission")
        payload = _encode(request)
        pinned, _ = _load_pinned_certificate(certificate_pem)

        async with self._lock:
            if self._closed or self.connected:
                raise LinkError("client link is closed or already connected")
            await self._disconnect_locked()
            configuration = _configuration(is_client=True)
            configuration.server_name = _TLS_NAME
            configuration.verify_mode = ssl.CERT_REQUIRED
            configuration.cadata = pinned
            context = _open_client_protocol(self, (host, port), configuration)
            entered = False
            keep_context = False
            try:
                async with asyncio.timeout(timeout):
                    protocol = await context.__aenter__()
                    entered = True
                    await protocol.handshake.wait()
                    if protocol.terminated:
                        raise LinkError("link handshake failed")
                    protocol.send_admission(payload)
                    await protocol.reply.wait()
                    if protocol.response is None or protocol.terminated:
                        raise LinkError("link admission failed")
                    if protocol.response["accepted"]:
                        if self._catalog_only and protocol.game_access:
                            raise LinkError("host did not grant catalog-only admission")
                        self._protocol = protocol
                        self._context = context
                        self._keepalive = asyncio.create_task(protocol.keepalive())
                        keep_context = True
                    return protocol.response
            except TimeoutError:
                raise LinkError("link handshake or admission timed out") from None
            except LinkError:
                raise
            except OSError:
                raise LinkError("could not open link") from None
            finally:
                if entered and not keep_context:
                    await context.__aexit__(None, None, None)

    async def request(self, payload: Action, *, timeout: float = 5.0) -> Action:
        """Send one reliable request as this link's admitted guest.

        A timeout or disconnect can occur after the application applied the
        request. Include an application transaction identifier when retrying.
        """
        if (type(timeout) not in (int, float) or not 0 < timeout <= 3600
                or not math.isfinite(timeout)):
            raise ValueError("application timeout must be positive and at most one hour")
        protocol = self._protocol
        if protocol is None or not protocol.authorized or protocol.terminated:
            raise LinkError("link is not admitted")
        return await protocol.request(_encode_action(payload), timeout)

    def _from_game(self, data: bytes, address: Address) -> None:
        self.game_local_datagrams += 1
        if not _is_loopback(address[0]) or not 0 < len(data) <= _MAX_GAME_PACKET:
            return
        protocol = self._protocol
        if protocol is not None and self.game_access:
            if self._game_endpoint is None:
                self._game_endpoint = address
            if self._game_endpoint == address:
                protocol.forward_game_packet(data)
                self.game_packets_sent += 1

    def reset_game_endpoint(self) -> None:
        """Accept a new local Unreal socket on the next deliberate game join."""
        self._game_endpoint = None
        self.game_local_datagrams = 0
        self.game_packets_sent = self.game_packets_received = 0

    async def _open_game_socket(self, local_host: str, local_port: int) -> asyncio.DatagramTransport:
        transport = None

        def receive(data: bytes, address: Address) -> None:
            if self._transport is transport:
                self._from_game(data, address)

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: _LocalUdp(receive), local_addr=(local_host, local_port),
        )
        return transport

    async def renew_game_endpoint(self) -> None:
        """Isolate a deliberate Unreal rejoin from late packets on the old port."""
        async with self._lock:
            if self._closed or self._transport is None:
                raise LinkError("client link has no local Unreal endpoint")
            prior = self._transport
            self._transport = await self._open_game_socket(self.address[0], 0)
            self.reset_game_endpoint()
            prior.close()

    def _to_game(self, data: bytes) -> None:
        if self._transport is not None and self._game_endpoint is not None and not self._closed:
            self._transport.sendto(data, self._game_endpoint)
            self.game_packets_received += 1

    async def _disconnect_locked(self) -> None:
        if self._keepalive is not None:
            self._keepalive.cancel()
            await asyncio.gather(self._keepalive, return_exceptions=True)
            self._keepalive = None
        self._protocol = None
        if self._context is not None:
            context, self._context = self._context, None
            await context.__aexit__(None, None, None)

    async def disconnect(self) -> None:
        async with self._lock:
            await self._disconnect_locked()

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            try:
                await self._disconnect_locked()
            finally:
                if self._transport is not None:
                    self._transport.close()


async def start_client(
    local_host: str = "127.0.0.1", local_port: int = 0, *, catalog_only: bool = False,
) -> ClientLink:
    """Open a client, optionally without an Unreal loopback relay socket."""
    client = ClientLink(catalog_only=catalog_only)
    if not catalog_only:
        if not _is_loopback(local_host):
            raise ValueError("client Unreal endpoint must be a loopback address")
        client._transport = await client._open_game_socket(local_host, local_port)
    return client
