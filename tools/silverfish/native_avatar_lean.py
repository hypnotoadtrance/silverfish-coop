"""Relay wall-clamped cosmetic lean without sending reliable gameplay actions."""

import asyncio
import os
import struct
import time

from . import native_player_avatar
from .party import REQUEST_ID

_UPLOAD = struct.Struct("!16sIh")
_HEADER = struct.Struct("!16sI")
_ENTRY = struct.Struct("!Ih")
_INTERVAL = .05
_REFRESH = .18
_MAX_AGE = .38
_MAX_ANGLE = 4000
_MAX_PLAYERS = 16


def enabled(bridge):
    return (os.getenv("SILVERFISH_COOP_AVATAR_LEAN") == "1" and bridge.local_bridge is not None
            and bridge.local_bridge.avatar_pose_available)


def _epoch(service):
    epoch = service.zone_epoch
    return bytes.fromhex(epoch) if isinstance(epoch, str) and REQUEST_ID.fullmatch(epoch) else None


def _pawn(game):
    return game.get("pawns", {}).get("local", {}).get("name")


def _local_sample(mailbox, pawn):
    raw = mailbox.take_avatar_pose()
    if not raw or not pawn:
        return None
    identity, tab, value = raw.partition(b"\t")
    digits = value.removeprefix(b"-")
    if tab and identity == pawn.encode("ascii", "replace") and 1 <= len(digits) <= 4 and digits.isdigit():
        angle = int(value)
        if abs(angle) <= _MAX_ANGLE:
            return angle
    return None


def _entries(packet):
    body = memoryview(packet)[_HEADER.size:]
    if not _ENTRY.size <= len(body) <= _MAX_PLAYERS * _ENTRY.size or len(body) % _ENTRY.size:
        return None
    result = {}
    for player_id, angle in _ENTRY.iter_unpack(body):
        if player_id > 2147483647 or abs(angle) > _MAX_ANGLE or player_id in result:
            return None
        result[player_id] = angle
    return result


def _project(mailbox, pawn, entries):
    if pawn and entries:
        rows = "".join(f"{player_id}\t{angle}\n" for player_id, angle in entries.items())
        mailbox.publish_avatar_poses(f"{pawn}\n{rows}".encode("ascii", "replace"))


def _standing(host, character):
    return not host.party.get("wipe") and host.party.get("players", {}).get(character, {}).get("downed") is False


async def relay(host, bridge, transport):
    """Collect one current pose per admitted pawn, then fan out its assigned player ID."""
    mailbox = bridge.local_bridge
    latest = {}
    local_scope, local_angle, local_seen = None, 0, 0.
    epoch, previous, sent = None, {}, 0.
    serial = 0

    def receive(guest_id, payload):
        if len(payload) != _UPLOAD.size or epoch is None or host.transitioning:
            return
        visit, number, angle = _UPLOAD.unpack(payload)
        if visit != epoch or visit != _epoch(host) or number == 0 or abs(angle) > _MAX_ANGLE:
            return
        identity = host.authenticated_guest(guest_id)
        if identity is None or host.guest_leave_pending(guest_id):
            return
        character = identity[1]
        member = host.native_recipients().get(character)
        if not member or not member["connected"] or not _standing(host, character):
            return
        previous_sample = latest.get(character)
        if previous_sample and previous_sample[0] == member["pawn"] and number <= previous_sample[1]:
            return
        latest[character] = member["pawn"], number, angle, time.monotonic()

    transport.set_avatar_pose_handler(receive)
    try:
        while not host.stopping:
            game, now = host.last_game, time.monotonic()
            pawn = _pawn(game)
            scope = _epoch(host) if (game.get("state") == "hosting" and pawn and not host.transitioning
                                      and now - host.last_seen < 1.5) else None
            angle = _local_sample(mailbox, pawn)
            if scope != epoch:
                # The guest can still be in this visit while the host's game view is stale.
                epoch, sent = scope, 0.
                latest.clear()
                previous.clear()
                angle = None
            if (epoch, pawn) != local_scope:
                local_scope, local_angle, local_seen = (epoch, pawn), 0, 0.
            if angle is not None:
                local_angle, local_seen = angle, now
            if epoch is not None:
                members = {character: member for character, member in host.native_recipients().items()
                           if member["connected"] and _standing(host, character)}
                guests = {member["pawn"]: latest[character][2] for character, member in members.items()
                          if character in latest and latest[character][0] == member["pawn"]
                          and now - latest[character][3] <= _MAX_AGE}
                current = {}
                lead_id = None
                for raw_id, outfit in game.get(native_player_avatar.KEY, {}).items():
                    player_id = int(raw_id)
                    owner = outfit["pawn"]
                    if owner == pawn:
                        lead_id = player_id
                        if local_angle and now - local_seen <= _MAX_AGE and _standing(host, "host"):
                            current[player_id] = local_angle
                    elif guests.get(owner):
                        current[player_id] = guests[owner]
                if current != previous or current and now - sent >= _REFRESH:
                    batch = current | {player_id: 0 for player_id in previous.keys() - current.keys()}
                    if 0 < len(batch) <= _MAX_PLAYERS:
                        _project(mailbox, pawn, {key: value for key, value in batch.items() if key != lead_id})
                        serial += 1
                        packet = _HEADER.pack(epoch, serial) + b"".join(_ENTRY.pack(*entry) for entry in batch.items())
                        admitted = {character for character, member in host.native_recipients().items() if member["connected"]}
                        transport.send_avatar_pose(packet, lambda guest_id: (identity := host.authenticated_guest(guest_id))
                                                   is not None and identity[1] in admitted)
                    previous, sent = current, now
                latest = {character: sample for character, sample in latest.items() if character in members}
            await asyncio.sleep(_INTERVAL)
    finally:
        transport.set_avatar_pose_handler(None)


async def follow(client):
    """Send the local owner's sampled roll and apply only the current host visit."""
    mailbox, transport = client.bridge.local_bridge, client.transport
    scope, seen_epoch = None, None
    sent, received, last_angle = 0, 0, 0
    while not client.stopping:
        game = client.last_game
        pawn = _pawn(game)
        epoch = _epoch(client) if (transport.game_access and game.get("state") == "connected"
                                   and game.get("map") == client.expected_zone and not client.needs_travel
                                   and not client.server_transitioning) else None
        angle, packet = _local_sample(mailbox, pawn), transport.take_avatar_pose()
        if (epoch, pawn) != scope:
            scope, last_angle = (epoch, pawn), 0
            angle = None
        if epoch is not None and pawn:
            if epoch != seen_epoch:
                seen_epoch, received = epoch, 0
            if angle is not None and (angle or last_angle):
                sent += 1
                if transport.send_avatar_pose(_UPLOAD.pack(epoch, sent, angle)):
                    last_angle = angle
            if packet is not None and len(packet) > _HEADER.size:
                visit, number = _HEADER.unpack_from(packet)
                if visit == epoch and number > received and (entries := _entries(packet)) is not None:
                    _project(mailbox, pawn, entries)
                    received = number
        await asyncio.sleep(_INTERVAL)
