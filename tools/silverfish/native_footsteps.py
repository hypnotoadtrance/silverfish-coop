"""Relay short stock player sounds over the transient co-op cue lane."""

import asyncio
from collections import deque
import struct
import time

from .native_player_avatar import KEY as OUTFITS, valid_id as valid_player_id
from .party import REQUEST_ID


_CUES = frozenset({
    "Brush", "Carpet", "ChainLink", "Default", "Dirt", "Glass", "Goop", "Grass", "Metal",
    "MetalLadder", "Sand", "Stone", "Water_Deep", "Water_Shallow", "Wood", "Trash", "CameraShutter",
})
_UPLOAD = struct.Struct("!16sIHH")
_DOWNLOAD = struct.Struct("!16sIIHH")
_INTERVAL = .025
_MIN_INTERVAL = .08
_MAX_AGE = .20
_MAX_BATCH = 16


def enabled(bridge):
    return getattr(getattr(bridge, "local_bridge", None), "footsteps_available", False)


def _scope(owner, game):
    epoch = owner.zone_epoch
    area, pawn = game.get("map"), game.get("pawns", {}).get("local", {}).get("name")
    if area and pawn and isinstance(epoch, str) and REQUEST_ID.fullmatch(epoch):
        return bytes.fromhex(epoch), area, pawn


def _valid(serial, cue, volume, pitch):
    return 0 < serial < 2**32 and cue in _CUES and 0 < volume <= 4000 and 0 < pitch <= 1000


def _local(data, area, pawn):
    lines = data.splitlines()
    if not lines or lines[0] != area:
        return
    for row in lines[1:]:
        fields = row.split("|", 5)
        if len(fields) != 6 or fields[0] != "step" or fields[5] != pawn:
            continue
        _, serial, cue, volume, pitch, _ = fields
        ints = (serial, volume, pitch)
        if not all(field and field.isascii() and field.isdecimal() and len(field) <= 10 for field in ints):
            continue
        step = (int(serial), cue, int(volume), int(pitch))
        if _valid(*step):
            yield step


def _decode(packet, header):
    if not header.size < len(packet) <= header.size + 13:
        return None
    try:
        cue = packet[header.size:].decode("ascii")
    except UnicodeError:
        return None
    return header.unpack_from(packet), cue


def _player_id(game, pawn):
    ids = [int(key) for key, row in game.get(OUTFITS, {}).items() if row.get("pawn") == pawn and valid_player_id(key)]
    return ids[0] if len(ids) == 1 else None


def _project(mailbox, scope, steps):
    if not steps:
        return
    _, area, pawn = scope
    body = "".join(f"{player}|{cue}|{volume}|{pitch}\n" for player, cue, volume, pitch in steps)
    mailbox.publish_footsteps(f"{area}\n{pawn}\n{body}".encode("ascii", "replace"))


class HostFootsteps:
    def __init__(self, host, bridge, transport):
        self.host, self.mailbox, self.transport = host, bridge.local_bridge, transport
        self.scope = None
        self.recent = {}
        self.pending = deque(maxlen=_MAX_BATCH)

    def _current(self):
        game, now = self.host.last_game, time.monotonic()
        same_visit = not self.host.transitioning or self.host.stock_photo_in_progress
        scope = _scope(self.host, game) if (game.get("state") == "hosting" and same_visit
                                            and not self.host.stopping and now - self.host.last_seen < 1.5) else None
        if scope != self.scope:
            self.scope = scope
            self.recent.clear()
            self.pending.clear()
        return scope

    def _peer(self, guest):
        auth = self.host.authenticated_guest(guest)
        if auth is None or self.host.guest_leave_pending(guest):
            return None
        member = self.host.native_recipients().get(auth[1])
        if member and member["connected"]:
            return member["pawn"]

    def _send(self, visit, player, step, *, excluded=None):
        serial, cue, volume, pitch = step
        data = _DOWNLOAD.pack(visit, player, serial, volume, pitch) + cue.encode("ascii")
        self.transport.send_footstep(data, lambda guest: guest != excluded and self._peer(guest) is not None)

    def local(self, data):
        scope = self._current()
        if scope is None:
            return
        visit, area, pawn = scope
        player = _player_id(self.host.last_game, pawn)
        if player is not None:
            for step in _local(data, area, pawn):
                self._send(visit, player, step)

    def receive(self, guest, packet):
        scope, received = self._current(), _decode(packet, _UPLOAD)
        if scope is None or received is None:
            return
        (visit, serial, volume, pitch), cue = received
        step = serial, cue, volume, pitch
        if visit != scope[0] or not _valid(*step):
            return
        pawn = self._peer(guest)
        player = _player_id(self.host.last_game, pawn) if pawn else None
        if player is None or player == _player_id(self.host.last_game, scope[2]):
            return
        now = time.monotonic()
        shutter = cue == "CameraShutter"
        key = guest, shutter
        if key not in self.recent and len(self.recent) >= 2 * _MAX_BATCH:
            self.recent = {key: record for key, record in self.recent.items() if now - record[2] < 1.5}
            if len(self.recent) >= 2 * _MAX_BATCH:
                return
        previous = self.recent.get(key)
        if previous and previous[0] == pawn and now - previous[2] < 1.5:
            if serial <= previous[1] or now - previous[2] < _MIN_INTERVAL:
                return
        self.recent[key] = pawn, serial, now
        self.pending.append((now, player, cue, volume, pitch))
        self._send(visit, player, step, excluded=guest)

    def flush(self):
        scope = self._current()
        if scope is None or not self.pending:
            return
        now = time.monotonic()
        _project(self.mailbox, scope, [entry[1:] for entry in self.pending if now - entry[0] <= _MAX_AGE])
        self.pending.clear()


async def follow(client):
    mailbox, transport = client.bridge.local_bridge, client.transport
    previous, latest = None, {}
    while not client.stopping:
        game = client.last_game
        scope = _scope(client, game) if (transport.game_access and game.get("state") == "connected"
                                         and game.get("map") == client.expected_zone and not client.needs_travel) else None
        frame, packets = mailbox.take_local_weapon_cues(), transport.take_footsteps()
        if scope != previous:
            previous = scope
            latest.clear()
            frame, packets = None, ()
        if scope is not None:
            visit, area, pawn = scope
            if frame:
                try:
                    local = frame.decode("ascii")
                except UnicodeError:
                    local = ""
                for serial, cue, volume, pitch in _local(local, area, pawn):
                    if client.server_transitioning and cue != "CameraShutter":
                        continue
                    transport.send_footstep(_UPLOAD.pack(visit, serial, volume, pitch) + cue.encode("ascii"))
            steps, now = [], time.monotonic()
            local_player = _player_id(game, pawn)
            for packet in packets:
                decoded = _decode(packet, _DOWNLOAD)
                if decoded is None:
                    continue
                (epoch, player, serial, volume, pitch), cue = decoded
                if (epoch != visit or player > 2147483647 or player == local_player
                        or (client.server_transitioning and cue != "CameraShutter")
                        or not _valid(serial, cue, volume, pitch)):
                    continue
                key = player, cue == "CameraShutter"
                sequence, last = latest.get(key, (0, 0.))
                if serial <= sequence and now - last < 1.5:
                    continue
                if key not in latest and len(latest) >= 2 * _MAX_BATCH:
                    latest = {key: record for key, record in latest.items() if now - record[1] < 1.5}
                    if len(latest) >= 2 * _MAX_BATCH:
                        continue
                latest[key] = serial, now
                steps.append((player, cue, volume, pitch))
            _project(mailbox, scope, steps)
        await asyncio.sleep(_INTERVAL)
