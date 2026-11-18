"""Private QUIC link for the Direct IP prototype's loopback-only Unreal sockets."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import logging
import ssl
import struct
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
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
    "load_server_identity", "make_server_identity", "start_client", "start_host",
]

_ALPN = "silverfish-coop-direct/1"
_TLS_NAME = "silverfish-coop.invalid"
_ADMISSION_STREAM = 0
_MAX_ADMISSION = 4096
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
_MAX_ADMISSION_TASKS = 32
_APP_ERROR = 0x100
_LOG = logging.getLogger(__name__)

Address = tuple[str, int]
Admission = dict[str, Any]
Admit = Callable[[Admission], Admission | Awaitable[Admission]]
OnDisconnect = Callable[[str], None | Awaitable[None]]


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
        max_data=_MAX_ADMISSION + 1,
        max_stream_data=_MAX_ADMISSION + 1,
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


def _validate_response(response: Admission) -> None:
    if not isinstance(response, dict) or type(response.get("accepted")) is not bool:
        raise LinkError("invalid admission response")


def _rejection(reason: str) -> Admission:
    return {"accepted": False, "reason": reason}


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
        self._admission_buffer = bytearray()
        self._admission_finished = False
        self._reassembler = _Reassembler()
        self._next_packet_id = 0

    def send_admission(self, payload: bytes) -> None:
        self._quic.send_stream_data(_ADMISSION_STREAM, payload, end_stream=True)
        self.transmit()

    def forward_game_packet(self, data: bytes) -> None:
        if not self.authorized or self.terminated or not 0 < len(data) <= _MAX_GAME_PACKET:
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
        if self.authorized and not self.terminated:
            return self._reassembler.receive(event.data, self._loop.time())
        return None


class _HostProtocol(_LinkProtocol):
    def __init__(self, host: HostLink, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.host = host
        self.guest_id: str | None = None

    def quic_event_received(self, event: events.QuicEvent) -> None:
        if isinstance(event, events.StreamDataReceived):
            payload = self._admission_payload(event)
            if payload is not None:
                if len(self.host._tasks) < _MAX_ADMISSION_TASKS:
                    self.host._spawn(self._respond(payload))
                else:
                    self.close(error_code=_APP_ERROR, reason_phrase="host busy")
        elif isinstance(event, events.DatagramFrameReceived):
            packet = self._game_payload(event)
            if packet is not None:
                self.host._to_game(self, packet)
        elif isinstance(event, events.ConnectionTerminated):
            self.terminated = True
            self.authorized = False
            if self.guest_id is not None:
                self.host._spawn(self.host._release(self))

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
    """One admitted guest at a time, with a stable Unreal UDP endpoint per guest."""

    def __init__(
        self,
        game_address: Address,
        admit: Admit,
        on_disconnect: OnDisconnect | None,
    ) -> None:
        self._game_address = game_address
        self._admit_callback = admit
        self._disconnect_callback = on_disconnect
        self._guest_sockets: dict[str, asyncio.DatagramTransport] = {}
        self._current: _HostProtocol | None = None
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
        protocol = self._current
        return bool(protocol and protocol.guest_id == guest_id and protocol.authorized)

    async def release_guest(self, guest_id: str) -> bool:
        """Retire a disconnected guest's cached UDP endpoint after its grace period."""
        async with self._lock:
            if self._current is not None and self._current.guest_id == guest_id:
                return False
            transport = self._guest_sockets.pop(guest_id, None)
            if transport is None:
                return False
            transport.close()
            return True

    def _spawn(self, request: Awaitable[None]) -> None:
        task = asyncio.create_task(request)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _LOG.warning("Host link task failed")

    async def _admit(self, protocol: _HostProtocol, request: Admission) -> bytes:
        guest_id = request["guest_id"]
        async with self._lock:
            if self._closed or protocol.terminated:
                return _encode(_rejection("host_unavailable"))
            if self._current is not None:
                return _encode(_rejection("guest_already_connected"))

            transport = self._guest_sockets.get(guest_id)
            new_transport = transport is None
            keep_transport = False
            try:
                if new_transport:
                    if len(self._guest_sockets) >= _MAX_CACHED_GUESTS:
                        return _encode(_rejection("guest_endpoint_capacity"))
                    receive = lambda data, _: self._from_game(guest_id, data)
                    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                        lambda: _LocalUdp(receive),
                        local_addr=(self._game_address[0], 0),
                        remote_addr=self._game_address,
                    )

                async with asyncio.timeout(_ADMISSION_TIMEOUT):
                    response = self._admit_callback(request)
                    if inspect.isawaitable(response):
                        response = await response
                _validate_response(response)
                if response["accepted"]:
                    protocol.guest_id = guest_id
                    self._current = protocol
                    try:
                        payload = _encode(response)
                    except LinkError:
                        await self._release_locked(protocol)
                        raise
                    if protocol.terminated or self._closed:
                        await self._release_locked(protocol)
                        return _encode(_rejection("host_unavailable"))
                    self._guest_sockets[guest_id] = transport
                    keep_transport = True
                    protocol.authorized = True
                    return payload
                return _encode(response)
            except Exception:
                _LOG.warning("Host link admission failed")
                return _encode(_rejection("admission_failed"))
            finally:
                if new_transport and transport is not None and not keep_transport:
                    transport.close()

    async def _release(self, protocol: _HostProtocol) -> None:
        async with self._lock:
            await self._release_locked(protocol)

    async def _release_locked(self, protocol: _HostProtocol) -> None:
        if self._current is not protocol:
            return
        self._current = None
        protocol.authorized = False
        if self._disconnect_callback is None:
            return
        try:
            async with asyncio.timeout(_ADMISSION_TIMEOUT):
                result = self._disconnect_callback(protocol.guest_id)
                if inspect.isawaitable(result):
                    await result
        except Exception:
            _LOG.warning("Host link disconnect callback failed")

    def _to_game(self, protocol: _HostProtocol, data: bytes) -> None:
        if self._current is protocol and protocol.authorized:
            transport = self._guest_sockets.get(protocol.guest_id)
            if transport is not None:
                transport.sendto(data)

    def _from_game(self, guest_id: str, data: bytes) -> None:
        protocol = self._current
        if protocol is not None and protocol.guest_id == guest_id:
            protocol.forward_game_packet(data)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            if self._current is not None:
                await self._release_locked(self._current)
            self._server.close()
            for transport in self._guest_sockets.values():
                transport.close()
            self._guest_sockets.clear()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


async def start_host(
    bind_host: str,
    bind_port: int,
    game_host: str,
    game_port: int,
    identity: ServerIdentity,
    admit: Admit,
    on_disconnect: OnDisconnect | None = None,
) -> HostLink:
    if not _is_loopback(game_host):
        raise ValueError("Unreal relay endpoint must be a loopback address")
    link = HostLink((game_host, game_port), admit, on_disconnect)
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

    def quic_event_received(self, event: events.QuicEvent) -> None:
        if isinstance(event, events.HandshakeCompleted):
            self.handshake.set()
        elif isinstance(event, events.StreamDataReceived):
            payload = self._admission_payload(event)
            if payload is not None:
                try:
                    self.response = _decode(payload)
                    _validate_response(self.response)
                    self.authorized = self.response["accepted"]
                except LinkError:
                    self.response = None
                self.reply.set()
        elif isinstance(event, events.DatagramFrameReceived):
            packet = self._game_payload(event)
            if packet is not None:
                self.client._to_game(packet)
        elif isinstance(event, events.ConnectionTerminated):
            self.terminated = True
            self.authorized = False
            self.handshake.set()
            self.reply.set()

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

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._closed = False
        self._protocol: _ClientProtocol | None = None
        self._context = None
        self._keepalive: asyncio.Task[None] | None = None
        self._game_endpoint: Address | None = None

    @property
    def address(self) -> Address:
        return _address(self._transport)

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

    def _from_game(self, data: bytes, address: Address) -> None:
        if not _is_loopback(address[0]) or not 0 < len(data) <= _MAX_GAME_PACKET:
            return
        protocol = self._protocol
        if protocol is not None and protocol.authorized:
            if self._game_endpoint is None:
                self._game_endpoint = address
            if self._game_endpoint == address:
                protocol.forward_game_packet(data)

    def reset_game_endpoint(self) -> None:
        """Accept a new local Unreal socket on the next deliberate game join."""
        self._game_endpoint = None

    def _to_game(self, data: bytes) -> None:
        if self._game_endpoint is not None and not self._closed:
            self._transport.sendto(data, self._game_endpoint)

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
                self._transport.close()


async def start_client(local_host: str = "127.0.0.1", local_port: int = 0) -> ClientLink:
    if not _is_loopback(local_host):
        raise ValueError("client Unreal endpoint must be a loopback address")
    client = ClientLink()
    client._transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: _LocalUdp(client._from_game), local_addr=(local_host, local_port),
    )
    return client
