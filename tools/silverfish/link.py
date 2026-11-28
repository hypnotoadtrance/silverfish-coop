"""Private QUIC link for the Direct IP prototype's loopback-only Unreal sockets."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import logging
import math
import secrets
import ssl
import struct
import zlib
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from time import perf_counter_ns
from typing import Any

from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic import events
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection
from aioquic.quic.logger import QuicLogger, QuicLoggerTrace
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .native_guest_consumable import RESULT as GUEST_CONSUMABLE_ACTIONS
from .gameplay_snapshot import DELTA as GAMEPLAY_DELTA
from .gameplay_snapshot import FIELD_CHANGES as GAMEPLAY_FIELD_CHANGES
from .gameplay_snapshot import ROW_CHANGES as GAMEPLAY_ROW_CHANGES
from .gameplay_snapshot import WORLD_SECTION as GAMEPLAY_WORLD
from .udp_ownership import verify_loopback_socket

__all__ = [
    "ClientLink", "HostLink", "LinkError", "ServerIdentity",
    "checkpoint_action_timeout", "load_server_identity", "make_server_identity", "start_client", "start_host",
]

_ALPN = "silverfish-coop-direct/1"
_TLS_NAME = "silverfish-coop.invalid"
_ADMISSION_STREAM = 0
_END_SESSION_STREAM = 1
_END_SESSION_NOTICE_LIMIT = 128
_END_SESSION_ACK = b"ack"
_END_SESSION_TIMEOUT = 2.0
GUEST_LEAVE = "native-session-leave"
GUEST_LEAVE_CONFIRM = "native-session-leave-confirm"
_MAX_ADMISSION = 4096
_MAX_ACTION_BODY = 32 * 1024
_MAX_ACTION_FRAME = _MAX_ACTION_BODY + 64
_MAX_SNAPSHOT_BODY = 256 * 1024
_MAX_SNAPSHOT_FRAME = 96 * 1024
_COMPRESS_SNAPSHOT_MINIMUM = 1024
_COMPRESS_DELTA_MINIMUM = 256
_COMPRESSED_SNAPSHOT = b"SFZ\x02"
# Both seats ship this dictionary with the frame version. Keep it small so
# seeding the compressor costs little for ordinary one- or two-row updates.
_SNAPSHOT_DICTIONARY = (
    b'/Game/Classes/Maps/Maps_OverWorld/ /Game/Classes/Maps/Maps_Dungeons/'
    b'/Game/Classes/Items/ /Game/Classes/Weapons/ .Observitory_P:PersistentLevel.'
    b'.CrossRoads_P:PersistentLevel. .RailYard_P:PersistentLevel. '
    b'"native_authored_quest_placements": {"items":{'
    b'"native_stock_enemy_loot":{ "native_loot_rolls":{ "native_loot_roll_revision":'
    b'"native_world_view":{ "native_stock_doors":{ "native_stock_switches":{ '
    b'"native_guest_health":{ "native_reputation":{ "native_explosive_canisters":{ '
    b'"native_guest_route":{ "native_player_outfits":{ "native_quests":{ '
    b'"class_path":"/Game/Classes/ "transform":{"position":[ "rotation":[ "scale":[ '
    b'"actor":" "location":[ "velocity":[ "pawn":"Player_FP_C /Game/Classes/Maps/'
    b'"phase": "health": "maximum": "downed":false "character_id":" "observed_at":'
    b'"mode":"sample","map":" "doors":{ "switches":{ "powered":false "locked":false '
    b'"sequence": "close": "open": "lights":{ "angles":[ "owner":" '
    b'"host_world":{ "native_scene":{ "instance_seed": "native_rules":{ '
    b'"gameplay_world_updates":true,"gameplay_placement_updates":true,"gameplay_field_updates":true,'
    b'"gameplay_row_updates":true, "gameplay_revision":" "gameplay_base":" '
    b'"gameplay_delta":{"changed":{ "fields":{ "rows":{ "removed":[]'
    b'"zone_epoch":" "viewer_role":"guest","zone":"/Game/Classes/Maps/Maps_OverWorld/'
    b'"travel_in_progress":false, "loading_zone":" "map":"/Game/Classes/Maps/'
    b'"epoch":" {"result":{"ok":true,"gameplay":{'
)
_MAX_ACTIONS = 8
_ACTION_TIMEOUT = 8.0
_CHECKPOINT_ACTION_TIMEOUTS = {
    "native-npc-barter-exchange": 22.0,
    GUEST_LEAVE: 40.0,
    "native-dialogue-choose": 20.0,
    "native-mosby-tape-convert": 20.0,
    "native-drill-test-results-convert": 20.0,
    "native-drill-instructions-convert": 20.0,
    "native-mindcore-upload": 35.0,
    "native-pump-tape-convert": 20.0,
    "native-authored-pickup-claim": 20.0,
    "native-loose-pickup-claim": 24.0,
    "native-mushroom-claim": 24.0,
    "native-missing-contact-collect": 28.0,
    "native-finite-shop-list": 15.0,
    "native-finite-shop-buy": 15.0,
    "native-finite-shop-sale-quote": 15.0,
    "native-finite-shop-sell": 15.0,
    **dict.fromkeys(GUEST_CONSUMABLE_ACTIONS, 15.0),
    "native-guest-armor-equip": 20.0,
    "native-guest-helmet-equip": 20.0,
    "native-guest-melee-swing": 20.0,
    "native-guest-door-blowtorch": 20.0,
    "native-guest-door-crowbar": 20.0,
    "native-inventory-split": 20.0,
    "native-inventory-merge": 20.0,
    "native-inventory-take-ammo": 20.0,
    "native-inventory-sort": 20.0,
    "native-guest-stack-drop": 36.0,
    "native-stash-move": 36.0,
    "native-guest-stack-reclaim": 24.0,
    "native-stock-enemy-loot-claim": 24.0,
    "native-subway-valve-activate": 24.0,
    "native-quest-interact": 15.0,
    "native-quest-list": 15.0,
    "native-turret-photo-capture": 20.0,
    "native-turret-photo-shutter": 20.0,
}
_QUEST_ACTIONS = {"native-quest-start", "native-quest-turnin"}
_CHECKPOINT_QUESTS = {"Photo_DB_Turrets", "Clt_ComputerBoard", "Clt_CoupReport_01", "Clt_Brandy", "DepotDocs", "Jmr_FloodPlains", "Jmr_Lake", "Jmr_CrossRoads", "Asn_Lenny_01", "Asn_Spratt", "Clt_Molasis", "Clct_SavloKey", "Sub_Valve", "Clt_StolenBag", "DeadDrop_CrossRoads", "DeadDrop_Metro", "Clct_Radio_Scar", "Note_Farmhouse"}
_MAX_CERTIFICATE = 8192
_ADMISSION_TIMEOUT = 8.0
_IDLE_TIMEOUT = 12.0
_CATALOG_KEEPALIVE_INTERVAL = 3.0
_MAX_KEEPALIVE_PROBES = 8
_MAX_GAME_PACKET = 65507
_FRAGMENT_SIZE = 1024
LOOT_MOTION_MAX_PACKET = _FRAGMENT_SIZE
_FRAGMENT_HEADER = struct.Struct("!IHH")
_COMPRESSED_FRAGMENT = 1 << 15
_COMPRESS_GAME_MINIMUM = 256
STATE_INTERVAL = 1 / 20
# Unreal fragments always declare a nonzero size. The reserved headers carry
# small application datagrams on the same authenticated connection.
_WEAPON_SHOT_HEADER = _FRAGMENT_HEADER.pack(0, 0, 1)
_LOOT_MOTION_HEADER = _FRAGMENT_HEADER.pack(0, 0, 2)
_PROP_POSE_HEADER = _FRAGMENT_HEADER.pack(0, 0, 3)
_AVATAR_POSE_HEADER = _FRAGMENT_HEADER.pack(0, 0, 4)
_FOOTSTEP_HEADER = _FRAGMENT_HEADER.pack(0, 0, 5)
_GAMEPLAY_CHANGE_HEADER = _FRAGMENT_HEADER.pack(0, 0, 6)
_GAMEPLAY_CHANGE_MAX_AGE = .25
_FOOTSTEP_MAX_AGE = .20
_FOOTSTEP_MAX_SIZE = 48
_WEAPON_SHOT_MAX_AGE = .25
_AVATAR_POSE_MAX_AGE = .25
_FRAGMENT_TTL = 1.0
_MAX_ASSEMBLIES = 32
_MAX_QUEUED_FRAMES = 96
_GAME_QUEUE_AGE = 0.120
_MAX_CACHED_GUESTS = 16
_MAX_CATALOG_LINKS = 16
_MAX_HOST_TASKS = 32
_APP_ERROR = 0x100
_SNAPSHOT_CANCELLED = 0x101
_LOG = logging.getLogger(__name__)

Address = tuple[str, int]
Admission = dict[str, Any]
Admit = Callable[[Admission], Admission | Awaitable[Admission]]
Enroll = Callable[[Admission, tuple[str, int]], Awaitable[Admission]]
OnDisconnect = Callable[[str], None | Awaitable[None]]
Action = dict[str, Any]
OnRequest = Callable[[str, Action], Action | Awaitable[Action]]
OnPropPose = Callable[[str, bytes], None]
OnAvatarPose = Callable[[str, bytes], None]

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
    if action == "native-quest-turnin" and target in ("Clt_Tape_StoneMaze_01", "Clt_DrillHead", "Sub_Valve"):
        return 28.0
    if action in _QUEST_ACTIONS and isinstance(target, str) and target in _CHECKPOINT_QUESTS:
        return 20.0
    return _CHECKPOINT_ACTION_TIMEOUTS.get(action)


def _valid_session_end(session: object, revision: object) -> bool:
    return (isinstance(session, str) and len(session) == 32 and all(ch in "0123456789abcdef" for ch in session)
            and type(revision) is int and 0 < revision < 2**53)


async def network_ticks(interval: float = STATE_INTERVAL) -> AsyncIterator[None]:
    """Schedule worker updates from monotonic deadlines and skip missed slots."""
    clock = asyncio.get_running_loop().time
    deadline = clock() + interval
    while True:
        while (remaining := deadline - clock()) > 0:
            await asyncio.sleep(remaining)
        now = clock()
        deadline += interval
        if deadline <= now:
            deadline = now + interval
        yield
        now = clock()
        if deadline <= now:
            deadline += (int((now - deadline) / interval) + 1) * interval


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


class _PacketMixTrace(QuicLoggerTrace):
    def __init__(self, capture: _PacketMixLogger, is_client: bool) -> None:
        super().__init__(is_client=is_client, odcid=b"")
        self.capture = capture
        self.received = None

    def _count(self, direction, frames, length):
        if not self.capture.active or type(length) is not int or length <= 0:
            return
        kinds = {frame["frame_type"] for frame in frames if "frame_type" in frame}
        if "datagram" in kinds:
            kind = "mixed" if "stream" in kinds else "datagram"
        elif "stream" in kinds:
            kind = "stream"
        else:
            kind = "ack_only" if kinds - {"padding"} == {"ack"} else "control_only"
        counts = self.capture.counts
        for key, amount in ((f"{direction}_{kind}_packets", 1), (f"{direction}_{kind}_bytes", length)):
            counts[key] = counts.get(key, 0) + amount

    def log_event(self, *, category: str, event: str, data: dict) -> None:
        if category != "transport" or event not in {"packet_sent", "packet_received"} or not self.capture.active:
            return
        frames = data.get("frames", ())
        length = data.get("raw", {}).get("length")
        if event == "packet_sent":
            self._count("sent", frames, length)
        else:
            # aioquic populates this list after announcing a received packet.
            self.finish_receive()
            self.received = frames, length

    def finish_receive(self) -> None:
        if self.received is not None:
            frames, length = self.received
            self.received = None
            self._count("received", frames, length)


class _PacketMixLogger(QuicLogger):
    def __init__(self) -> None:
        super().__init__()
        self.active = True
        self.counts: dict[str, int] = {}

    def start_trace(self, is_client: bool, odcid: bytes) -> QuicLoggerTrace:
        return _PacketMixTrace(self, is_client)

    def end_trace(self, trace: QuicLoggerTrace) -> None:
        # Neither completed nor active connection traces are retained here.
        pass


_packet_mix_logger: ContextVar[_PacketMixLogger | None] = ContextVar("packet_mix_logger", default=None)


@contextmanager
def measure_quic_packet_mix() -> Iterator[Callable[[], dict[str, int]]]:
    """Collect only aggregate QUIC packet counts and bytes in this diagnostic session."""
    logger = _PacketMixLogger()
    token = _packet_mix_logger.set(logger)
    try:
        yield logger.counts.copy
    finally:
        logger.active = False
        _packet_mix_logger.reset(token)


def _configuration(is_client: bool) -> QuicConfiguration:
    # A rebuilding guest can receive one large world baseline alongside its
    # ordinary action replies. The host still advertises the smaller request window.
    frame = _MAX_SNAPSHOT_FRAME if is_client else _MAX_ACTION_FRAME
    packet_mix = _packet_mix_logger.get()
    return QuicConfiguration(
        is_client=is_client,
        alpn_protocols=[_ALPN],
        idle_timeout=_IDLE_TIMEOUT,
        max_data=_MAX_ADMISSION + _MAX_ACTION_FRAME * (_MAX_ACTIONS - 1) + frame,
        max_stream_data=frame,
        max_datagram_size=1200,
        max_datagram_frame_size=_FRAGMENT_SIZE + _FRAGMENT_HEADER.size + 16,
        quic_logger=packet_mix if packet_mix is not None and packet_mix.active else None,
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
    limits = {"guest_id": 96, "credential": 512, "game_build": 128}
    if request.get("catalog") is not True or "character_id" in request:
        limits["character_id"] = 96
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
    if "runtime_id" in request:
        runtime_id = request["runtime_id"]
        if (request.get("catalog") is True or not isinstance(runtime_id, str) or len(runtime_id) != 32
                or runtime_id == "0" * 32 or any(character not in "0123456789abcdef" for character in runtime_id)):
            raise LinkError("invalid runtime_id")


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


@dataclass(slots=True)
class _TransportStatistics:
    """Cumulative local counters, with no peer identifiers or payload contents."""

    schema_version: int = 1
    quic_datagrams_sent: int = 0
    quic_payload_bytes_sent: int = 0
    quic_datagrams_received: int = 0
    quic_payload_bytes_received: int = 0
    quic_timer_callbacks: int = 0
    quic_timer_late_max_ns: int = 0
    keepalive_probes_queued: int = 0
    keepalive_probes_acked: int = 0
    keepalive_ticks_congested: int = 0
    game_packets_queued: int = 0
    game_payload_bytes_queued: int = 0
    game_wire_payload_bytes_queued: int = 0
    game_wire_payload_bytes_received: int = 0
    game_packets_compressed: int = 0
    game_fragments_queued: int = 0
    game_fragments_received: int = 0
    game_fragments_rejected: int = 0
    game_packets_reassembled: int = 0
    game_packets_delivered: int = 0
    game_payload_bytes_delivered: int = 0
    game_packets_not_delivered: int = 0
    relay_queue_max_frames: int = 0
    relay_queue_evicted_frames: int = 0
    relay_queue_expired_frames: int = 0
    relay_queue_departed_frames: int = 0
    relay_queue_wait_total_ns: int = 0
    relay_queue_wait_max_ns: int = 0
    relay_queue_wait_over_20ms_frames: int = 0
    relay_queue_wait_over_50ms_frames: int = 0
    interaction_priority_passes: int = 0
    weapon_cues_rejected_full_queue: int = 0
    prop_pose_datagrams_queued: int = 0
    prop_pose_datagrams_replaced: int = 0
    prop_pose_datagrams_received: int = 0
    avatar_pose_datagrams_queued: int = 0
    avatar_pose_datagrams_replaced: int = 0
    avatar_pose_datagrams_received: int = 0
    loot_motion_datagrams_queued: int = 0
    loot_motion_datagrams_received: int = 0
    loot_motion_bytes_queued: int = 0
    loot_motion_bytes_received: int = 0
    reassembly_expired_packets: int = 0
    reassembly_max_pending_packets: int = 0
    snapshot_responses_encoded: int = 0
    snapshot_responses_oversized: int = 0
    snapshot_responses_compressed: int = 0
    snapshot_delta_responses: int = 0
    snapshot_row_delta_responses: int = 0
    snapshot_world_delta_responses: int = 0
    snapshot_field_delta_responses: int = 0
    snapshot_rows_changed: int = 0
    snapshot_plaintext_bytes: int = 0
    snapshot_encoded_bytes: int = 0
    snapshot_encode_total_ns: int = 0
    snapshot_encode_max_ns: int = 0
    snapshot_responses_decoded: int = 0
    snapshot_received_bytes: int = 0
    snapshot_decode_total_ns: int = 0
    snapshot_decode_max_ns: int = 0


def _encode_response(result: Action, *, snapshot: bool, snapshot_limit: int = _MAX_SNAPSHOT_FRAME,
                     stats: _TransportStatistics | None = None) -> bytes:
    started = perf_counter_ns() if snapshot and stats is not None else None
    body = _encode_action(result, limit=_MAX_SNAPSHOT_BODY if snapshot else _MAX_ACTION_BODY)
    frame = b"".join((b'{"result":', body, b"}"))
    plaintext_size = len(frame)
    delta = result.get(GAMEPLAY_DELTA) if snapshot else None
    rows = delta.get(GAMEPLAY_ROW_CHANGES) if isinstance(delta, dict) else None
    fields = delta.get(GAMEPLAY_FIELD_CHANGES) if isinstance(delta, dict) else None
    # Small deltas repeat world keys, paths and revision IDs across changed sections.
    # Keep the existing codec and use compression only when the result is smaller.
    minimum = _COMPRESS_DELTA_MINIMUM if isinstance(delta, dict) else _COMPRESS_SNAPSHOT_MINIMUM
    if snapshot and (rows or plaintext_size >= minimum):
        compressor = zlib.compressobj(level=1, zdict=_SNAPSHOT_DICTIONARY)
        compressed = _COMPRESSED_SNAPSHOT + compressor.compress(frame) + compressor.flush()
        if len(compressed) < plaintext_size:
            frame = compressed
    maximum = min(snapshot_limit, _MAX_SNAPSHOT_FRAME) if snapshot else _MAX_ACTION_FRAME
    if len(frame) > maximum:
        if snapshot and stats is not None:
            stats.snapshot_responses_oversized += 1
        raise LinkError("application response too large")
    if started is not None:
        assert stats is not None
        elapsed = perf_counter_ns() - started
        stats.snapshot_responses_encoded += 1
        stats.snapshot_delta_responses += GAMEPLAY_DELTA in result
        if rows:
            stats.snapshot_row_delta_responses += 1
            stats.snapshot_world_delta_responses += GAMEPLAY_WORLD in rows
            stats.snapshot_rows_changed += sum(len(patch["changed"]) + len(patch["removed"]) for patch in rows.values())
        stats.snapshot_field_delta_responses += bool(fields)
        stats.snapshot_responses_compressed += len(frame) < plaintext_size
        stats.snapshot_plaintext_bytes += plaintext_size
        stats.snapshot_encoded_bytes += len(frame)
        stats.snapshot_encode_total_ns += elapsed
        stats.snapshot_encode_max_ns = max(stats.snapshot_encode_max_ns, elapsed)
    return frame


def _decode_response(frame: bytes) -> Action:
    if frame.startswith(_COMPRESSED_SNAPSHOT):
        decoder = zlib.decompressobj(zdict=_SNAPSHOT_DICTIONARY)
        maximum = _MAX_SNAPSHOT_BODY + 16
        try:
            frame = decoder.decompress(frame[len(_COMPRESSED_SNAPSHOT):], maximum + 1)
        except zlib.error:
            raise LinkError("invalid compressed snapshot") from None
        if len(frame) > maximum or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise LinkError("invalid compressed snapshot")
    return _decode_action(frame)


def _application_stream(stream_id: int) -> bool:
    return stream_id > _ADMISSION_STREAM and stream_id % 4 == 0


@dataclass
class _IncomingAction:
    body: bytearray = field(default_factory=bytearray)
    deadline: asyncio.TimerHandle | None = None
    abandoned: bool = False
    snapshot: bool = False


@dataclass
class _OutgoingAction:
    response: asyncio.Future[Action]
    snapshot: bool = False
    body: bytearray = field(default_factory=bytearray)


@dataclass
class _PartialPacket:
    size: int
    created_at: float
    compressed: bool
    chunks: dict[int, bytes] = field(default_factory=dict)


class _Reassembler:
    def __init__(self, stats: _TransportStatistics) -> None:
        self._pending: dict[int, _PartialPacket] = {}
        self._stats = stats

    def receive(self, frame: bytes, now: float) -> bytes | None:
        stats = self._stats
        stats.game_fragments_received += 1
        if len(frame) <= _FRAGMENT_HEADER.size:
            stats.game_fragments_rejected += 1
            return None
        packet_id, size, index = _FRAGMENT_HEADER.unpack_from(frame)
        compressed = bool(index & _COMPRESSED_FRAGMENT)
        index &= ~_COMPRESSED_FRAGMENT
        chunk = frame[_FRAGMENT_HEADER.size:]
        start = index * _FRAGMENT_SIZE
        if (not 0 < size <= _MAX_GAME_PACKET or start >= size
                or len(chunk) != min(_FRAGMENT_SIZE, size - start)):
            stats.game_fragments_rejected += 1
            return None
        stats.game_wire_payload_bytes_received += len(chunk)

        # Dict insertion order is creation order. Stop at the first live assembly.
        while self._pending:
            oldest = next(iter(self._pending))
            if now - self._pending[oldest].created_at <= _FRAGMENT_TTL:
                break
            del self._pending[oldest]
            stats.reassembly_expired_packets += 1

        if size <= _FRAGMENT_SIZE:
            return self._complete(chunk, compressed)

        partial = self._pending.get(packet_id)
        if partial is None:
            if len(self._pending) >= _MAX_ASSEMBLIES:
                stats.game_fragments_rejected += 1
                return None
            partial = _PartialPacket(size, now, compressed)
            self._pending[packet_id] = partial
            stats.reassembly_max_pending_packets = max(stats.reassembly_max_pending_packets, len(self._pending))
        if partial.size != size or partial.compressed != compressed:
            stats.game_fragments_rejected += 1
            return None
        partial.chunks.setdefault(index, chunk)
        expected = (size + _FRAGMENT_SIZE - 1) // _FRAGMENT_SIZE
        if len(partial.chunks) != expected:
            return None
        del self._pending[packet_id]
        return self._complete(b"".join(partial.chunks[index] for index in range(expected)), compressed)

    def _complete(self, data: bytes, compressed: bool) -> bytes | None:
        if compressed:
            decoder = zlib.decompressobj()
            try:
                data = decoder.decompress(data, _MAX_GAME_PACKET + 1)
            except zlib.error:
                data = b""
            if not 0 < len(data) <= _MAX_GAME_PACKET or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                self._stats.game_fragments_rejected += 1
                return None
        self._stats.game_packets_reassembled += 1
        return data


class _LocalUdp(asyncio.DatagramProtocol):
    def __init__(self, receive: Callable[[bytes, Address], None]) -> None:
        self.receive = receive

    def datagram_received(self, data: bytes, address: Address) -> None:
        self.receive(data, address)

    def error_received(self, exc: Exception) -> None:
        # Unreal can briefly close its local socket during travel.
        pass


class _MeasuredDatagramTransport:
    """Count bytes submitted by aioquic without replacing its transmit/timer logic."""

    def __init__(self, transport: asyncio.BaseTransport, stats: _TransportStatistics) -> None:
        self._transport = transport
        self._stats = stats

    def sendto(self, data: bytes, address: Address | None = None) -> None:
        self._transport.sendto(data, address)
        self._stats.quic_datagrams_sent += 1
        self._stats.quic_payload_bytes_sent += len(data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._transport, name)


class _LinkProtocol(QuicConnectionProtocol):
    def __init__(self, stats: _TransportStatistics, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stats = stats
        self.terminated = False
        self.authorized = False
        self.game_access = False
        self._admission_buffer = bytearray()
        self._admission_finished = False
        self._reassembler = _Reassembler(stats)
        self._next_packet_id = 0
        self._game_enqueued: deque[float] = deque()
        self._next_game_expiry: float | None = None
        self._interaction_streams: dict[int, int] = {}
        trace = self._quic._quic_logger
        self._packet_mix_trace = trace if isinstance(trace, _PacketMixTrace) else None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        super().connection_made(_MeasuredDatagramTransport(transport, self._stats))

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self._stats.quic_datagrams_received += 1
        self._stats.quic_payload_bytes_received += len(data)
        try:
            super().datagram_received(data, addr)
        finally:
            if self._packet_mix_trace is not None:
                self._packet_mix_trace.finish_receive()

    def _handle_timer(self) -> None:
        stats = self._stats
        stats.quic_timer_callbacks += 1
        if self._timer_at is not None:
            late = int((self._loop.time() - self._timer_at) * 1_000_000_000)
            stats.quic_timer_late_max_ns = max(stats.quic_timer_late_max_ns, late)
        super()._handle_timer()

    def send_admission(self, payload: bytes) -> None:
        self._quic.send_stream_data(_ADMISSION_STREAM, payload, end_stream=True)
        self.transmit()

    def _send_action_stream(self, stream_id: int, payload: bytes, *, priority: bool = True) -> None:
        self._quic.send_stream_data(stream_id, payload, end_stream=True)
        if priority and self.game_access and len(payload) <= _FRAGMENT_SIZE:
            self._interaction_streams[stream_id] = len(payload)
        self.transmit()

    def _ready_interactions(self) -> set[int]:
        ready = set()
        remaining = self._quic._remote_max_data - self._quic._remote_max_data_used
        for stream_id, size in tuple(self._interaction_streams.items()):
            stream = self._quic._streams.get(stream_id)
            if stream is None or stream.sender.is_finished:
                del self._interaction_streams[stream_id]
                continue
            sender = stream.sender
            if stream.is_blocked or sender.buffer_is_empty or sender.reset_pending:
                continue
            limit = min(stream.max_stream_data_remote, sender.highest_offset + remaining, size)
            if sender.next_offset < limit or sender.next_offset == size and sender._pending_eof:
                ready.add(stream_id)
        return ready

    def forward_game_packet(self, data: bytes) -> None:
        if not self.authorized or not self.game_access or self.terminated or not 0 < len(data) <= _MAX_GAME_PACKET:
            return
        original_size = len(data)
        flag = 0
        if original_size >= _COMPRESS_GAME_MINIMUM:
            compressed = zlib.compress(data, level=1)
            if len(compressed) <= original_size - max(24, original_size // 8):
                data, flag = compressed, _COMPRESSED_FRAGMENT
        now = self._loop.time()
        self._expire_game_queue(now)
        count = (len(data) + _FRAGMENT_SIZE - 1) // _FRAGMENT_SIZE
        pending = self._quic._datagrams_pending
        # Evict the oldest queued packet as a group when the path is congested.
        # Unreal implements its own reliability above this datagram transport.
        stats = self._stats
        before = len(pending)
        while len(pending) + count > _MAX_QUEUED_FRAMES:
            evicted = pending.popleft()[:6]
            while pending and pending[0][:6] == evicted:
                pending.popleft()
        dropped = before - len(pending)
        for _ in range(dropped):
            self._game_enqueued.popleft()
        stats.relay_queue_evicted_frames += dropped
        if not pending:
            self._next_game_expiry = now + _GAME_QUEUE_AGE
        packet_id = self._next_packet_id
        self._next_packet_id = (packet_id + 1) % 2**32
        for index in range(count):
            start = index * _FRAGMENT_SIZE
            frame = _FRAGMENT_HEADER.pack(packet_id, len(data), index | flag)
            self._quic.send_datagram_frame(frame + data[start:start + _FRAGMENT_SIZE])
        self._game_enqueued.extend([now] * count)
        stats.game_packets_queued += 1
        stats.game_payload_bytes_queued += original_size
        stats.game_wire_payload_bytes_queued += len(data)
        stats.game_packets_compressed += bool(flag)
        stats.game_fragments_queued += count
        stats.relay_queue_max_frames = max(stats.relay_queue_max_frames, len(pending))
        # Let packets from the same event-loop turn share a QUIC datagram.
        # Control streams and heartbeats can still transmit immediately.
        self._transmit_soon()

    def _forward_presentation(self, header: bytes, data: bytes) -> bool:
        if not self._queue_presentation(header, data):
            return False
        self.transmit()
        return True

    def _queue_presentation(self, header: bytes, data: bytes) -> bool:
        if not self.authorized or not self.game_access or self.terminated or not 0 < len(data) <= _FRAGMENT_SIZE:
            return False
        now = self._loop.time()
        self._expire_game_queue(now)
        pending = self._quic._datagrams_pending
        frame = header + data
        if header in (_PROP_POSE_HEADER, _AVATAR_POSE_HEADER, _GAMEPLAY_CHANGE_HEADER):
            for index, queued in enumerate(pending):
                if queued.startswith(header):
                    # Preserve the channel's queue position while discarding the older pose.
                    pending[index] = frame
                    self._game_enqueued[index] = now
                    if header == _PROP_POSE_HEADER:
                        self._stats.prop_pose_datagrams_replaced += 1
                    elif header == _AVATAR_POSE_HEADER:
                        self._stats.avatar_pose_datagrams_replaced += 1
                    return True
        if len(pending) >= _MAX_QUEUED_FRAMES:
            if header == _WEAPON_SHOT_HEADER:
                self._stats.weapon_cues_rejected_full_queue += 1
            return False
        if not pending:
            self._next_game_expiry = now + _GAME_QUEUE_AGE
        self._quic.send_datagram_frame(frame)
        self._game_enqueued.append(now)
        if header == _PROP_POSE_HEADER:
            self._stats.prop_pose_datagrams_queued += 1
        elif header == _AVATAR_POSE_HEADER:
            self._stats.avatar_pose_datagrams_queued += 1
        elif header == _LOOT_MOTION_HEADER:
            self._stats.loot_motion_datagrams_queued += 1
            self._stats.loot_motion_bytes_queued += len(frame)
        self._stats.relay_queue_max_frames = max(self._stats.relay_queue_max_frames, len(pending))
        return True

    def _expire_game_queue(self, now: float) -> None:
        if self._next_game_expiry is None or now < self._next_game_expiry:
            return
        pending = self._quic._datagrams_pending
        # All fragments of a packet share a deadline. A replacement pose keeps
        # its queue position but gets a fresh deadline.
        fresh = [(frame, queued_at) for frame, queued_at in zip(pending, self._game_enqueued)
                 if now < queued_at + _GAME_QUEUE_AGE]
        self._stats.relay_queue_expired_frames += len(pending) - len(fresh)
        pending.clear()
        pending.extend(frame for frame, _ in fresh)
        self._game_enqueued = deque(queued_at for _, queued_at in fresh)
        self._next_game_expiry = min(self._game_enqueued) + _GAME_QUEUE_AGE if fresh else None

    def transmit(self) -> None:
        now = self._loop.time()
        self._expire_game_queue(now)
        pending = self._quic._datagrams_pending
        if self._interaction_streams and (ready := self._ready_interactions()):
            # aioquic 1.3 writes DATAGRAM frames before STREAM frames. Give a
            # small interaction a pass ahead of datagrams and bulk streams.
            self._quic._streams_queue.sort(key=lambda stream: stream.stream_id not in ready)
            if pending:
                self._stats.interaction_priority_passes += 1
                self._quic._datagrams_pending = deque()
                try:
                    super().transmit()
                finally:
                    self._quic._datagrams_pending = pending
                # If congestion held back the stream, the next ACK or QUIC
                # timer retries it. When it was sent, resume the datagram queue.
                if not self._ready_interactions():
                    self._transmit_soon()
                return
        try:
            super().transmit()
        finally:
            # aioquic consumes only the front of the pending datagram queue.
            pending = self._quic._datagrams_pending
            departed = len(self._game_enqueued) - len(pending)
            if departed:
                stats = self._stats
                waited = 0.
                oldest = now
                for _ in range(departed):
                    queued_at = self._game_enqueued.popleft()
                    age = now - queued_at
                    waited += age
                    oldest = min(oldest, queued_at)
                    stats.relay_queue_wait_over_20ms_frames += age > .02
                    stats.relay_queue_wait_over_50ms_frames += age > STATE_INTERVAL
                stats.relay_queue_departed_frames += departed
                stats.relay_queue_wait_total_ns += int(waited * 1_000_000_000)
                stats.relay_queue_wait_max_ns = max(stats.relay_queue_wait_max_ns, int((now - oldest) * 1_000_000_000))
            if not pending:
                self._next_game_expiry = None

    def _admission_payload(self, event: events.StreamDataReceived) -> bytes | None:
        # aioquic can report a retransmitted FIN as an empty end event.
        if event.stream_id == _ADMISSION_STREAM and self._admission_finished and event.end_stream and not event.data:
            return None
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
        super().__init__(host._stats, *args, **kwargs)
        self.host = host
        self.guest_id: str | None = None
        self.peer = ("", 0)
        self._actions: dict[int, _IncomingAction] = {}
        self._end_ack: asyncio.Event | None = None
        self._end_ack_bytes = bytearray()
        self._deferred_loot_motion: list[tuple[bytes, _GameRoute, int, float, Callable[[str], bool]]] = []

    def defer_loot_motion(self, frames: Sequence[bytes], allowed: Callable[[str], bool]) -> None:
        guest_id = self.guest_id
        route = self.host._guest_routes.get(guest_id) if guest_id is not None else None
        if route is None:
            return
        pending = self._deferred_loot_motion
        before = len(pending)
        generation = self.host._game_route_generation
        expires = self._loop.time() + _GAME_QUEUE_AGE
        for frame in frames[:_MAX_QUEUED_FRAMES - before]:
            if 0 < len(frame) <= _FRAGMENT_SIZE:
                pending.append((frame, route, generation, expires, allowed))
        if len(pending) > before:
            self._transmit_soon()

    def transmit(self) -> None:
        if self._deferred_loot_motion:
            pending, self._deferred_loot_motion = self._deferred_loot_motion, []
            guest_id = self.guest_id
            if (guest_id is not None and not self.host._closed and self.host._protocols.get(guest_id) is self
                    and self.host._native_route_permitted(guest_id)):
                route = self.host._guest_routes.get(guest_id)
                now = self._loop.time()
                for frame, original, generation, expires, allowed in pending:
                    if (now < expires and original is route and generation == self.host._game_route_generation
                            and allowed(guest_id)):
                        self._queue_presentation(_LOOT_MOTION_HEADER, frame)
        super().transmit()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.peer = (addr[0], addr[1])
        super().datagram_received(data, addr)

    def send_session_end(self, body: bytes) -> asyncio.Event:
        self._end_ack = asyncio.Event()
        self._send_action_stream(_END_SESSION_STREAM, body)
        return self._end_ack

    def quic_event_received(self, event: events.QuicEvent) -> None:
        if isinstance(event, events.StreamDataReceived):
            if event.stream_id == _ADMISSION_STREAM:
                payload = self._admission_payload(event)
                if payload is not None:
                    if len(self.host._tasks) < _MAX_HOST_TASKS:
                        self.host._spawn(self._respond, payload, self.peer)
                    else:
                        self.close(error_code=_APP_ERROR, reason_phrase="host busy")
            elif event.stream_id == _END_SESSION_STREAM and self._end_ack is not None:
                if len(self._end_ack_bytes) + len(event.data) > len(_END_SESSION_ACK):
                    self._end_ack = None
                else:
                    self._end_ack_bytes.extend(event.data)
                    if event.end_stream and self._end_ack_bytes == _END_SESSION_ACK:
                        self._end_ack.set()
            else:
                self._read_action(event)
        elif isinstance(event, (events.StreamReset, events.StopSendingReceived)):
            action = self._actions.get(event.stream_id)
            if action is not None:
                action.abandoned = True
                # A partial request can be discarded once either side closes the stream.
                # A submitted callback can still finish without returning a response.
                if action.deadline is not None:
                    action.deadline.cancel()
                    action.deadline = None
                    self._actions.pop(event.stream_id)
        elif isinstance(event, events.DatagramFrameReceived):
            if event.data.startswith(_PROP_POSE_HEADER):
                self.host._receive_prop_pose(self, event.data[_FRAGMENT_HEADER.size:])
                return
            if event.data.startswith(_AVATAR_POSE_HEADER):
                self.host._receive_avatar_pose(self, event.data[_FRAGMENT_HEADER.size:])
                return
            if event.data.startswith(_FOOTSTEP_HEADER):
                self.host._receive_footstep(self, event.data[_FRAGMENT_HEADER.size:])
                return
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
        stream = self._quic._streams.get(stream_id)
        if stream is not None and stream.sender._reset_error_code is not None:
            # STOP_SENDING can reach the host before the request on the same stream.
            return

        action = self._actions.get(stream_id)
        if action is None:
            # A FIN can finish a buffered request, but cannot start a request with no bytes.
            if event.end_stream and not event.data:
                return
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
            # This is the peer's initial limit for its own bidirectional
            # streams. Older guests advertise the original 32 KiB window.
            response = await self.host._run_action(self, action, self._quic._remote_max_stream_data_bidi_local)
            if response is not None and self.authorized and not self.terminated and not action.abandoned:
                self._send_action_stream(stream_id, response, priority=not action.snapshot)
        finally:
            self._actions.pop(stream_id, None)

    async def _respond(self, payload: bytes, peer: tuple[str, int]) -> None:
        try:
            request = _decode(payload)
            if "direct" in request:
                if self.terminated or self.host._closed:
                    return
                response = (_rejection("direct_join_disabled") if self.host._enroll is None else
                            await self.host._enroll(request, peer))
                if not self.terminated:
                    self.send_admission(_encode(response))
                return
            _validate_request(request)
        except LinkError:
            self.send_admission(_encode(_rejection("invalid_request")))
            return
        response = await self.host._admit(self, request)
        if not self.terminated:
            self.send_admission(response)


@dataclass(frozen=True)
class _GameRoute:
    transport: asyncio.DatagramTransport
    runtime_id: str | None


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
        enroll: Enroll | None = None,
    ) -> None:
        if type(max_game_guests) is not int or not 1 <= max_game_guests <= _MAX_CACHED_GUESTS:
            raise ValueError(f"Allow between 1 and {_MAX_CACHED_GUESTS} concurrent game guests")
        self._game_address = game_address
        self._max_game_guests = max_game_guests
        self._admit_callback = admit
        self._disconnect_callback = on_disconnect
        self._request_callback = on_request
        self._prop_pose_handler: OnPropPose | None = None
        self._avatar_pose_handler: OnAvatarPose | None = None
        self._footstep_handler: Callable[[str, bytes], None] | None = None
        self._enroll = enroll
        self._guest_routes: dict[str, _GameRoute] = {}
        self._protocols: dict[str, _HostProtocol] = {}
        self._native_admission: frozenset[str] | None = None
        self._game_route_holds = 0
        self._game_route_generation = 0
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._stats = _TransportStatistics()

    def statistics(self) -> dict[str, int]:
        """Report cumulative counters across this host link's QUIC connections.

        Outer byte counts are UDP payloads assigned to those connections. They
        exclude IP/UDP headers and server Retry or version negotiation.
        """
        return asdict(self._stats)

    @property
    def address(self) -> Address:
        # aioquic's public serve() result exposes the datagram transport here.
        return _address(self._server._transport)

    def guest_endpoint(self, guest_id: str) -> Address | None:
        route = self._guest_routes.get(guest_id)
        return _address(route.transport) if route else None

    def guest_runtime_id(self, guest_id: str) -> str | None:
        """Identify the game process associated with a connected or cached relay."""
        route = self._guest_routes.get(guest_id)
        return route.runtime_id if route else None

    def connected(self, guest_id: str) -> bool:
        """Return whether the guest currently has an admitted game link."""
        protocol = self._protocols.get(guest_id)
        return bool(protocol and protocol.authorized and protocol.game_access)

    def restrict_game_routes(self, allowed: set[str] | None) -> None:
        """Gate Unreal packets independently of authenticated control traffic."""
        current = None if allowed is None else frozenset(allowed)
        if current != self._native_admission:
            self._native_admission = current
            self._game_route_generation += 1

    @contextmanager
    def hold_game_routes(self) -> Iterator[None]:
        """Pause gameplay UDP without altering the current admissions or control streams."""
        self._game_route_holds += 1
        self._game_route_generation += 1
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
            route = self._guest_routes.pop(guest_id, None)
            if route is None:
                return False
            route.transport.close()
            return True

    async def abort_guest_route(self, guest_id: str) -> None:
        """Close the admitted peer and its route after failed native enrollment."""
        async with self._lock:
            protocol = self._protocols.get(guest_id)
            if protocol and protocol.game_access:
                protocol.close(error_code=_APP_ERROR, reason_phrase="native enrollment failed")
                await self._release_locked(protocol)
            route = self._guest_routes.pop(guest_id, None)
            if route is not None:
                route.transport.close()

    async def _create_game_route(self, guest_id: str) -> asyncio.DatagramTransport:
        transport = None

        def receive(data: bytes, _: Address) -> None:
            current = self._guest_routes.get(guest_id)
            if current is not None and current.transport is transport:
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
            prior = self._guest_routes.get(guest_id)
            if prior is None:
                raise LinkError("the guest has no Unreal relay route")
            route = _GameRoute(await self._create_game_route(guest_id), prior.runtime_id)
            if self._closed or not self.connected(guest_id) or not self._native_route_permitted(guest_id):
                route.transport.close()
                raise LinkError("the guest's Unreal relay route changed")
            self._guest_routes[guest_id] = route
            prior.transport.close()

    async def refresh_game_routes(self) -> None:
        """Rebuild Unreal relay routes after hard travel without closing the QUIC link.

        Call once the new Unreal listener is ready. Reconnects alone continue to
        reuse existing routes. A reserved guest gets a fresh route on readmission.
        """
        async with self._lock:
            if self._closed:
                raise LinkError("host link is closed")
            refreshed: dict[str, _GameRoute] = {}
            try:
                for guest_id in self._protocols:
                    if self.connected(guest_id):
                        prior = self._guest_routes[guest_id]
                        refreshed[guest_id] = _GameRoute(await self._create_game_route(guest_id), prior.runtime_id)
                if self._closed:
                    raise LinkError("host link is closed")
                for guest_id in tuple(refreshed):
                    if not self.connected(guest_id):
                        refreshed.pop(guest_id).transport.close()
            except BaseException as error:
                for route in refreshed.values():
                    route.transport.close()
                if isinstance(error, OSError) and not isinstance(error, LinkError):
                    raise LinkError("could not refresh the Unreal relay route") from None
                raise

            old_routes, self._guest_routes = self._guest_routes, refreshed
            for route in old_routes.values():
                route.transport.close()

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
        runtime_id = request.get("runtime_id")
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

            route = prior = None
            rotate = False
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
                # The admission callback authorizes replacement only after its native roster check.
                rotate = response.get("rotate_game_route", False)
                if type(rotate) is not bool or rotate and (not protocol.game_access or runtime_id is None):
                    raise LinkError("invalid game relay rotation")
                if "rotate_game_route" in response:
                    response = {key: value for key, value in response.items() if key != "rotate_game_route"}
                payload = _encode(response)
                if not response["accepted"]:
                    return payload
                if catalog and protocol.game_access:
                    raise LinkError("catalog admission cannot grant Unreal access")
                if not protocol.game_access and catalogs >= _MAX_CATALOG_LINKS:
                    return _encode(_rejection("catalog_capacity"))
                if protocol.terminated or self._closed:
                    return _encode(_rejection("host_unavailable"))

                prior = self._guest_routes.get(guest_id)
                if protocol.game_access and prior and prior.runtime_id != runtime_id and not rotate:
                    return _encode(_rejection("native_runtime_retiring"))
                if protocol.game_access and (prior is None or rotate):
                    if prior is None and len(self._guest_routes) >= _MAX_CACHED_GUESTS:
                        return _encode(_rejection("guest_endpoint_capacity"))
                    route = _GameRoute(await self._create_game_route(guest_id), runtime_id)
                if protocol.terminated or self._closed:
                    return _encode(_rejection("host_unavailable"))
                if route is not None:
                    self._guest_routes[guest_id] = route
                    if prior is not None:
                        prior.transport.close()
                protocol.authorized = True
                return payload
            except Exception:
                _LOG.warning("Host link admission failed")
                return _encode(_rejection("admission_failed"))
            finally:
                if not protocol.authorized and self._protocols.get(guest_id) is protocol:
                    if route is not None:
                        route.transport.close()
                    # The callback already verified that the old native seat can be replaced.
                    if rotate is True and prior is not None and self._guest_routes.get(guest_id) is prior:
                        del self._guest_routes[guest_id]
                        prior.transport.close()
                    await self._release_locked(protocol)

    async def _run_action(self, protocol: _HostProtocol, action: _IncomingAction, snapshot_limit: int) -> bytes | None:
        guest_id = protocol.guest_id
        if self._closed or not protocol.authorized or guest_id is None or self._protocols.get(guest_id) is not protocol:
            return _encode_action({"error": "failed"})
        if self._request_callback is None:
            return _encode_action({"error": "disabled"})
        try:
            payload = _decode_action(bytes(action.body))
        except LinkError:
            return _encode_action({"error": "invalid"})
        action.snapshot = payload.get("action") == "snapshot"
        try:
            async with asyncio.timeout(checkpoint_action_timeout(payload) or _ACTION_TIMEOUT):
                result = self._request_callback(guest_id, payload)
                if inspect.isawaitable(result):
                    result = await result
                if action.abandoned or not protocol.authorized or protocol.terminated:
                    return None
                if not isinstance(result, dict):
                    raise LinkError("invalid application callback response")
                return _encode_response(result, snapshot=action.snapshot, snapshot_limit=snapshot_limit, stats=self._stats)
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

    async def announce_session_end(self, session_id: str, revision: int) -> int:
        """Send the committed terminal outcome on the exact admitted peers' reliable streams."""
        if not _valid_session_end(session_id, revision):
            raise ValueError("Invalid orderly session end")
        body = _encode_action({"session_id": session_id, "revision": revision})
        recipients = []
        for protocol in tuple(self._protocols.values()):
            if protocol.authorized and protocol.game_access and not protocol.terminated:
                try:
                    recipients.append(protocol.send_session_end(body))
                except (OSError, RuntimeError):
                    _LOG.warning("An admitted guest could not receive the committed session end")
        if recipients:
            try:
                async with asyncio.timeout(_END_SESSION_TIMEOUT):
                    await asyncio.gather(*(ack.wait() for ack in recipients))
            except TimeoutError:
                pass
        return sum(ack.is_set() for ack in recipients)

    def _to_game(self, protocol: _HostProtocol, data: bytes) -> None:
        guest_id = protocol.guest_id
        if guest_id is None or self._protocols.get(guest_id) is not protocol:
            self._stats.game_packets_not_delivered += 1
            return
        if protocol.authorized and protocol.game_access and self._native_route_permitted(guest_id):
            route = self._guest_routes.get(guest_id)
            if route is not None:
                route.transport.sendto(data)
                self._stats.game_packets_delivered += 1
                self._stats.game_payload_bytes_delivered += len(data)
                return
        self._stats.game_packets_not_delivered += 1

    def _from_game(self, guest_id: str, data: bytes) -> None:
        protocol = self._protocols.get(guest_id)
        if protocol is not None and self._native_route_permitted(guest_id):
            protocol.forward_game_packet(data)

    def set_prop_pose_handler(self, handler: OnPropPose | None) -> None:
        """Set the synchronous recipient of transient, admitted guest prop poses."""
        self._prop_pose_handler = handler

    def _pose_source(self, protocol: _HostProtocol, data: bytes) -> str | None:
        guest_id = protocol.guest_id
        if (not self._closed and guest_id is not None and protocol.authorized and protocol.game_access
                and not protocol.terminated and self._protocols.get(guest_id) is protocol
                and guest_id in self._guest_routes and self._native_route_permitted(guest_id)
                and 0 < len(data) <= _FRAGMENT_SIZE):
            return guest_id
        return None

    def _receive_prop_pose(self, protocol: _HostProtocol, data: bytes) -> None:
        handler = self._prop_pose_handler
        if handler is not None and (guest_id := self._pose_source(protocol, data)) is not None:
            self._stats.prop_pose_datagrams_received += 1
            handler(guest_id, data)

    def set_avatar_pose_handler(self, handler: OnAvatarPose | None) -> None:
        """Set the synchronous recipient of transient, admitted guest avatar poses."""
        self._avatar_pose_handler = handler

    def _receive_avatar_pose(self, protocol: _HostProtocol, data: bytes) -> None:
        handler = self._avatar_pose_handler
        if handler is not None and (guest_id := self._pose_source(protocol, data)) is not None:
            self._stats.avatar_pose_datagrams_received += 1
            handler(guest_id, data)

    def send_avatar_pose(self, data: bytes, allowed: Callable[[str], bool]) -> None:
        """Offer one replaceable pose frame to each eligible guest game link."""
        if isinstance(data, bytes) and 0 < len(data) <= _FRAGMENT_SIZE:
            self._send_presentation(_AVATAR_POSE_HEADER, (data,),
                                    lambda guest_id: guest_id in self._guest_routes and allowed(guest_id))

    def set_footstep_handler(self, handler: Callable[[str, bytes], None] | None) -> None:
        self._footstep_handler = handler

    def _receive_footstep(self, protocol: _HostProtocol, data: bytes) -> None:
        handler = self._footstep_handler
        if handler is not None and len(data) <= _FOOTSTEP_MAX_SIZE and (guest_id := self._pose_source(protocol, data)) is not None:
            handler(guest_id, data)

    def send_footstep(self, data: bytes, allowed: Callable[[str], bool]) -> None:
        if isinstance(data, bytes) and 0 < len(data) <= _FOOTSTEP_MAX_SIZE:
            self._send_presentation(_FOOTSTEP_HEADER, (data,),
                                    lambda guest_id: guest_id in self._guest_routes and allowed(guest_id))

    def send_weapon_shots(self, cues: Sequence[bytes], allowed: Callable[[str], bool]) -> None:
        self._send_presentation(_WEAPON_SHOT_HEADER, cues, allowed)

    def send_loot_motion(self, frames: Sequence[bytes], allowed: Callable[[str], bool]) -> None:
        for guest_id, protocol in self._protocols.items():
            if self.connected(guest_id) and self._native_route_permitted(guest_id) and allowed(guest_id):
                protocol.defer_loot_motion(frames, allowed)

    def send_gameplay_change(self, epoch: str, *, allowed: Callable[[str], bool] | None = None) -> None:
        """Hint that the current visit has newer authoritative snapshot state."""
        self._send_presentation(_GAMEPLAY_CHANGE_HEADER, (bytes.fromhex(epoch),),
                                allowed if allowed is not None else lambda _: True)

    def _send_presentation(self, header: bytes, frames: Sequence[bytes], allowed: Callable[[str], bool]) -> None:
        for guest_id, protocol in self._protocols.items():
            if self.connected(guest_id) and self._native_route_permitted(guest_id) and allowed(guest_id):
                queued = False
                for data in frames:
                    if protocol._queue_presentation(header, data):
                        queued = True
                if queued:
                    # Keep each cue as its own QUIC frame and let QUIC pack the burst.
                    protocol.transmit()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            for protocol in tuple(self._protocols.values()):
                await self._release_locked(protocol)
            self._server.close()
            for route in self._guest_routes.values():
                route.transport.close()
            self._guest_routes.clear()
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
    enroll: Enroll | None = None,
) -> HostLink:
    """Open a private game relay and optionally serve admitted application requests."""
    if not _is_loopback(game_host):
        raise ValueError("Unreal relay endpoint must be a loopback address")
    link = HostLink((game_host, game_port), admit, on_disconnect, on_request, max_game_guests=max_game_guests,
                    enroll=enroll)
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
        super().__init__(client._stats, *args, **kwargs)
        self.client = client
        self.handshake = asyncio.Event()
        self.reply = asyncio.Event()
        self.response: Admission | None = None
        self._actions: dict[int, _OutgoingAction] = {}
        self._session_end_bytes = bytearray()
        self.session_end: tuple[str, int] | None = None
        self.avatar_pose: tuple[float, bytes] | None = None
        self.gameplay_change: tuple[float, str] | None = None
        self.keepalive_rtt_us = -1
        self._keepalive_serial = 0
        self._keepalive_latest_ack = 0
        self._keepalive_probes: dict[int, int] = {}

    def quic_event_received(self, event: events.QuicEvent) -> None:
        if isinstance(event, events.HandshakeCompleted):
            self.handshake.set()
        elif isinstance(event, events.PingAcknowledged):
            sent_ns = self._keepalive_probes.pop(event.uid, None)
            if sent_ns is not None:
                self._stats.keepalive_probes_acked += 1
                if event.uid > self._keepalive_latest_ack:
                    self._keepalive_latest_ack = event.uid
                    self.keepalive_rtt_us = (perf_counter_ns() - sent_ns) // 1000
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
            elif event.stream_id == _END_SESSION_STREAM:
                self._read_session_end(event)
            else:
                self._read_action_response(event)
        elif isinstance(event, (events.StreamReset, events.StopSendingReceived)):
            action = self._actions.get(event.stream_id)
            if action is not None and not action.response.done():
                action.response.set_exception(LinkError("application request stream closed"))
        elif isinstance(event, events.DatagramFrameReceived):
            header = event.data[:_FRAGMENT_HEADER.size]
            if header == _AVATAR_POSE_HEADER:
                payload = event.data[_FRAGMENT_HEADER.size:]
                if (self.client._protocol is self and self.authorized and self.game_access and not self.terminated
                        and self.session_end is None and 0 < len(payload) <= _FRAGMENT_SIZE):
                    self.avatar_pose = (self._loop.time(), payload)
                    self._stats.avatar_pose_datagrams_received += 1
                return
            if header == _FOOTSTEP_HEADER:
                payload = event.data[_FRAGMENT_HEADER.size:]
                if (self.client._protocol is self and self.authorized and self.game_access and not self.terminated
                        and self.session_end is None and 0 < len(payload) <= _FOOTSTEP_MAX_SIZE):
                    self.client._footsteps.append((self._loop.time(), payload))
                return
            if header == _GAMEPLAY_CHANGE_HEADER:
                payload = event.data[_FRAGMENT_HEADER.size:]
                if (self.client._protocol is self and self.authorized and self.game_access and not self.terminated
                        and self.session_end is None and len(payload) == 16):
                    self.gameplay_change = (self._loop.time(), payload.hex())
                    if handler := self.client._gameplay_change_handler:
                        handler()
                return
            if header in (_WEAPON_SHOT_HEADER, _LOOT_MOTION_HEADER):
                if self.authorized and self.game_access and not self.terminated:
                    payload = event.data[_FRAGMENT_HEADER.size:]
                    if header == _WEAPON_SHOT_HEADER:
                        self.client._receive_weapon_shot(payload, self._loop.time())
                    else:
                        self.client._receive_loot_motion(payload, self._loop.time())
                return
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

    def _read_session_end(self, event: events.StreamDataReceived) -> None:
        if (not self.authorized or not self.game_access or self.session_end is not None
                or len(self._session_end_bytes) + len(event.data) > _END_SESSION_NOTICE_LIMIT):
            self.close(error_code=_APP_ERROR, reason_phrase="invalid session end notice")
            return
        self._session_end_bytes.extend(event.data)
        if not event.end_stream:
            return
        try:
            outcome = _decode_action(bytes(self._session_end_bytes))
            session, revision = outcome.get("session_id"), outcome.get("revision")
            if (outcome.keys() != {"session_id", "revision"} or not _valid_session_end(session, revision)
                    or self.response is None or session != self.response.get("session_id")):
                raise LinkError("invalid session end notice")
        except LinkError:
            self.close(error_code=_APP_ERROR, reason_phrase="invalid session end notice")
            return
        self.session_end = (session, revision)
        self._send_action_stream(_END_SESSION_STREAM, _END_SESSION_ACK)

    async def request(self, body: bytes, timeout: float, *, snapshot: bool = False) -> Action:
        if len(self._actions) >= _MAX_ACTIONS:
            raise LinkError("too many application requests pending")
        stream_id = self._quic.get_next_available_stream_id()
        action = self._actions[stream_id] = _OutgoingAction(self._loop.create_future(), snapshot=snapshot)
        try:
            self._send_action_stream(stream_id, body, priority=not snapshot)
            async with asyncio.timeout(timeout):
                return await action.response
        except (TimeoutError, asyncio.CancelledError) as error:
            if snapshot and self.authorized and not self.terminated:
                # Stop the obsolete reply without promising rollback of a request
                # already delivered to the host.
                with suppress(OSError, ValueError):
                    self._quic.stop_stream(stream_id, _SNAPSHOT_CANCELLED)
                    self.transmit()
            if isinstance(error, TimeoutError):
                raise LinkError("application request timed out") from None
            raise
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
        limit = _MAX_SNAPSHOT_FRAME if action.snapshot else _MAX_ACTION_FRAME
        if len(action.body) + len(event.data) > limit:
            action.response.set_exception(LinkError("application response too large"))
            self.close(error_code=_APP_ERROR, reason_phrase="application response too large")
            return
        action.body.extend(event.data)
        if not event.end_stream:
            return
        try:
            started = perf_counter_ns() if action.snapshot else None
            envelope = _decode_response(bytes(action.body))
            result = envelope.get("result")
            if envelope.keys() == {"result"} and isinstance(result, dict):
                if started is not None:
                    elapsed = perf_counter_ns() - started
                    self._stats.snapshot_responses_decoded += 1
                    self._stats.snapshot_received_bytes += len(action.body)
                    self._stats.snapshot_decode_total_ns += elapsed
                    self._stats.snapshot_decode_max_ns = max(self._stats.snapshot_decode_max_ns, elapsed)
                action.response.set_result(result)
                return
            error = envelope.get("error")
            if envelope.keys() == {"error"} and isinstance(error, str) and error in _ACTION_ERRORS:
                raise LinkError(_ACTION_ERRORS[error])
            raise LinkError("invalid application response")
        except LinkError as error:
            action.response.set_exception(error)

    async def keepalive(self) -> None:
        interval = STATE_INTERVAL if self.game_access else _CATALOG_KEEPALIVE_INTERVAL
        async for _ in network_ticks(interval):
            if not self.authorized or self.terminated:
                return
            # QUIC retries lost PINGs. Bound outstanding probes on a slow or blocked path.
            if len(self._keepalive_probes) >= _MAX_KEEPALIVE_PROBES:
                self._stats.keepalive_ticks_congested += 1
                continue
            self._keepalive_serial += 1
            uid = self._keepalive_serial
            self._keepalive_probes[uid] = perf_counter_ns()
            self._stats.keepalive_probes_queued += 1
            self._quic.send_ping(uid)
            self.transmit()


@asynccontextmanager
async def _open_client_protocol(client: ClientLink, address: Address, configuration: QuicConfiguration):
    connection = QuicConnection(configuration=configuration)
    # Windows reports an implicit bind as wildcard even for a connected loopback peer.
    local = (address[0], 0) if _is_loopback(address[0]) else None
    transport, protocol = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: _ClientProtocol(client, connection), local_addr=local, remote_addr=address,
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


async def direct_request(host: str, port: int, request: Admission, *, certificate: str | None) -> Admission:
    """Make one discovery or enrollment request without opening a game relay."""
    if certificate is None and request != {"direct": "discover"}:
        raise ValueError("Only public host identity discovery can run without a certificate pin")
    configuration = _configuration(is_client=True)
    configuration.server_name = _TLS_NAME
    configuration.verify_mode = ssl.CERT_NONE if certificate is None else ssl.CERT_REQUIRED
    if certificate is not None:
        configuration.cadata, _ = _load_pinned_certificate(certificate)
    client = ClientLink(catalog_only=True)
    try:
        async with asyncio.timeout(7):
            async with _open_client_protocol(client, (host, port), configuration) as protocol:
                await protocol.handshake.wait()
                if protocol.terminated:
                    raise LinkError("The host identity could not be verified")
                protocol.send_admission(_encode(request))
                await protocol.reply.wait()
                if protocol.response is None or protocol.terminated:
                    raise LinkError("The host did not answer the Direct IP request")
                if protocol.response.get("game_access") is not False and protocol.response["accepted"]:
                    raise LinkError("Direct IP enrollment unexpectedly opened game access")
                return protocol.response
    except (OSError, TimeoutError) as error:
        if isinstance(error, LinkError):
            raise
        raise LinkError("The Direct IP address or port could not be reached") from None


class ClientLink:
    """Runs a local UDP tunnel and preserves its address across QUIC reconnects."""

    def __init__(self, *, catalog_only: bool = False, game_pid: int | None = None) -> None:
        self._lock = asyncio.Lock()
        self._closed = False
        self._catalog_only = catalog_only
        self._game_pid = game_pid
        self._runtime_id = None if catalog_only else secrets.token_hex(16)
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _ClientProtocol | None = None
        self._context = None
        self._keepalive: asyncio.Task[None] | None = None
        self._game_endpoint: Address | None = None
        self._weapon_shots: deque[tuple[float, bytes]] = deque(maxlen=36)
        self._weapon_shots_received = asyncio.Event()
        self._footsteps: deque[tuple[float, bytes]] = deque(maxlen=16)
        self._loot_motion: deque[tuple[float, bytes]] = deque(maxlen=32)
        self._gameplay_change_handler: Callable[[], None] | None = None
        self.game_local_datagrams = 0
        self.game_packets_sent = 0
        self.game_packets_received = 0
        self._stats = _TransportStatistics()

    def statistics(self) -> dict[str, int]:
        """Report cumulative counters and a transport RTT for the current link."""
        return {**asdict(self._stats), "keepalive_rtt_us": self._protocol.keepalive_rtt_us if self.connected else -1}

    @property
    def runtime_id(self) -> str | None:
        """Identify this game process across reconnects of its QUIC link."""
        return self._runtime_id

    @property
    def unreal_local_port(self) -> int | None:
        if self._game_endpoint is None or self._game_pid is None:
            return None
        return self._game_endpoint[1]

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

    @property
    def session_end(self) -> tuple[str, int] | None:
        return self._protocol.session_end if self._protocol else None

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
        return await protocol.request(_encode_action(payload), timeout, snapshot=payload.get("action") == "snapshot")

    def _from_game(self, data: bytes, address: Address) -> None:
        self.game_local_datagrams += 1
        if not _is_loopback(address[0]) or not 0 < len(data) <= _MAX_GAME_PACKET:
            return
        protocol = self._protocol
        if protocol is not None and self.game_access:
            if self._game_endpoint is None:
                if self._game_pid is not None:
                    try:
                        verify_loopback_socket(self._game_pid, address[1])
                    except (OSError, RuntimeError):
                        return
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
            self._stats.game_packets_delivered += 1
            self._stats.game_payload_bytes_delivered += len(data)
        else:
            self._stats.game_packets_not_delivered += 1

    def _receive_weapon_shot(self, data: bytes, now: float) -> None:
        if 0 < len(data) <= _FRAGMENT_SIZE:
            self._weapon_shots.append((now, data))
            self._weapon_shots_received.set()

    async def wait_weapon_shots(self) -> None:
        await self._weapon_shots_received.wait()

    def weapon_shots_ready(self) -> bool:
        return bool(self._weapon_shots)

    def take_weapon_shots(self) -> list[bytes]:
        now = asyncio.get_running_loop().time()
        result = [data for received, data in self._weapon_shots if now - received <= _WEAPON_SHOT_MAX_AGE]
        self._weapon_shots.clear()
        self._weapon_shots_received.clear()
        return result

    def _send_pose(self, header: bytes, data: bytes) -> bool:
        protocol = self._protocol
        return bool(protocol is not None and not self._closed and protocol.session_end is None
                    and isinstance(data, bytes) and protocol._forward_presentation(header, data))

    def send_footstep(self, data: bytes) -> bool:
        return isinstance(data, bytes) and 0 < len(data) <= _FOOTSTEP_MAX_SIZE and self._send_pose(_FOOTSTEP_HEADER, data)

    def take_footsteps(self) -> list[bytes]:
        now = asyncio.get_running_loop().time()
        pending = [data for received, data in self._footsteps if now - received <= _FOOTSTEP_MAX_AGE]
        self._footsteps.clear()
        return pending

    def send_prop_pose(self, data: bytes) -> bool:
        """Queue one transient prop pose on the current authenticated game link."""
        return self._send_pose(_PROP_POSE_HEADER, data)

    def send_avatar_pose(self, data: bytes) -> bool:
        """Queue one transient avatar pose on the current authenticated game link."""
        return self._send_pose(_AVATAR_POSE_HEADER, data)

    def take_avatar_pose(self) -> bytes | None:
        """Consume the most recent arrival on the current link if it is still fresh."""
        protocol = self._protocol
        if not self.game_access or protocol is None or self._closed or protocol.session_end is not None:
            return None
        pose, protocol.avatar_pose = protocol.avatar_pose, None
        if pose is not None and 0 <= protocol._loop.time() - pose[0] <= _AVATAR_POSE_MAX_AGE:
            return pose[1]
        return None

    def _receive_loot_motion(self, data: bytes, now: float) -> None:
        if 0 < len(data) <= LOOT_MOTION_MAX_PACKET:
            self._stats.loot_motion_datagrams_received += 1
            self._stats.loot_motion_bytes_received += len(_LOOT_MOTION_HEADER) + len(data)
            self._loot_motion.append((now, data))

    def gameplay_changed(self, epoch: str) -> bool:
        protocol = self._protocol
        if not self.game_access or protocol is None or protocol.session_end is not None or not protocol.gameplay_change:
            return False
        received, changed_epoch = protocol.gameplay_change
        return changed_epoch == epoch and protocol._loop.time() - received <= _GAMEPLAY_CHANGE_MAX_AGE

    def set_gameplay_change_handler(self, handler: Callable[[], None] | None) -> None:
        self._gameplay_change_handler = handler

    def consume_gameplay_change(self) -> None:
        if self._protocol is not None:
            self._protocol.gameplay_change = None

    def loot_motion_ready(self) -> bool:
        return bool(self._loot_motion)

    def take_loot_motion(self) -> list[tuple[float, bytes]]:
        frames = list(self._loot_motion)
        self._loot_motion.clear()
        return frames

    async def _disconnect_locked(self) -> None:
        self._weapon_shots.clear()
        self._weapon_shots_received.clear()
        self._footsteps.clear()
        self._loot_motion.clear()
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
    local_host: str = "127.0.0.1", local_port: int = 0, *, catalog_only: bool = False, game_pid: int | None = None,
) -> ClientLink:
    """Open a client, optionally without an Unreal loopback relay socket."""
    client = ClientLink(catalog_only=catalog_only, game_pid=game_pid)
    if not catalog_only:
        if not _is_loopback(local_host):
            raise ValueError("client Unreal endpoint must be a loopback address")
        client._transport = await client._open_game_socket(local_host, local_port)
    return client
