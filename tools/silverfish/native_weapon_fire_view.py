"""Relay short-lived cosmetic cues from the host's stock firearm shots."""

import asyncio
import json
import re
import time

from .native_guest_drop_record import valid_id, valid_position


LIMIT = 36
_MAILBOX = "native-weapon-shots"
_MAX_MAILBOX = 16384
_MAX_AGE = .25
_STOCK_CLASS = re.compile(r"/Game/Classes/Weapons/(?:[\w-]+/)*([\w-]+)\.\1_C", re.ASCII)


def valid_shot(cue):
    if type(cue) is not list or len(cue) != 11:
        return False
    serial, path, *pose = cue
    return (type(serial) is int and 0 < serial < 10**15
            and type(path) is str and len(path) <= 160 and _STOCK_CLASS.fullmatch(path)
            and valid_position(pose[:3])
            and all(type(angle) in (int, float) and -180 <= angle <= 180 for angle in pose[3:6])
            and all(type(value) in (int, float) and low <= value <= high
                    for value, (low, high) in zip(pose[6:], ((0, 10), (0, 10), (-26, 24)), strict=True)))


def _captured(data):
    lines = data.splitlines()
    if not data.endswith("\n") or not 1 < len(lines) <= LIMIT + 1:
        return None, []
    area, *rows = lines
    cues = []
    for row in rows:
        fields = row.split("|")
        if len(fields) != 11 or not re.fullmatch(r"[1-9][0-9]{0,14}", fields[0]):
            continue
        serial, path, *pose = fields
        try:
            cue = [int(serial), path, *map(float, pose)]
        except ValueError:
            continue
        if valid_shot(cue):
            cues.append(cue)
    return area, cues


async def relay(host, bridge, transport):
    path = bridge.directory / _MAILBOX
    revision, last_sent = None, 0
    while not host.stopping:
        await asyncio.sleep(.025)
        try:
            info = path.stat()
            current = info.st_mtime_ns, info.st_size
            if (current == revision or not 0 < info.st_size <= _MAX_MAILBOX
                    or abs(time.time() - info.st_mtime) > _MAX_AGE):
                continue
            with path.open(encoding="ascii") as capture:
                data = capture.read(_MAX_MAILBOX + 1)
        except (OSError, UnicodeError):
            continue
        revision = current
        area, cues = _captured(data)
        fresh = [cue for cue in cues if cue[0] > last_sent]
        if not fresh:
            continue
        last_sent = max(cue[0] for cue in fresh)
        epoch, game = host.zone_epoch, host.last_game
        if (host.transitioning or not valid_id(epoch) or game.get("state") != "hosting" or area != game.get("map")):
            continue
        seats = {character for character, owner in host.native_recipients().items() if owner["connected"]}
        if not seats:
            continue

        def allowed(guest_id):
            member = host.authenticated_guest(guest_id)
            return bool(member and member[1] in seats)

        for cue in fresh:
            # Quantize presentation only. Stock raycasts still use the original transform.
            packet = json.dumps([area, epoch, cue[:2] + [round(axis, 2) for axis in cue[2:]]],
                                separators=(",", ":")).encode("ascii")
            transport.send_weapon_shot(packet, allowed)


class Playback:
    def __init__(self):
        self.scope = None
        self.last_played = 0

    async def sync(self, bridge, control, game, packets, epoch):
        if not valid_id(epoch) or not packets:
            return game
        if epoch != self.scope:
            self.scope, self.last_played = epoch, 0

        selected = {}
        scope = [game.get("map"), epoch]
        for packet in packets:
            try:
                decoded = json.loads(packet)
            except (ValueError, UnicodeError, RecursionError):
                continue
            if type(decoded) is list and len(decoded) == 3 and decoded[:2] == scope:
                cue = decoded[2]
                if valid_shot(cue) and cue[0] > self.last_played:
                    selected[cue[0]] = cue[1:]
        if not selected:
            return game
        arguments = ["|".join((str(serial), path, *(f"{value:.2f}" for value in pose)))
                     for serial, (path, *pose) in sorted(selected.items())]
        try:
            result = await bridge.command("native-weapon-play-shots", game["map"], epoch, *arguments, timeout=1.5)
        except (OSError, RuntimeError, TimeoutError) as error:
            control.event("native_weapon_shots_unavailable", reason=str(error))
            return game
        self.last_played = max(selected)
        for receipt in result.get("ack", {}).get("message", "").split(","):
            match = re.fullmatch(r"([0-9]{1,15}):([01]):([01]):([01])", receipt)
            if match and (serial := int(match[1])) in selected:
                control.event("native_weapon_shot_played", serial=serial, weapon=selected[serial][0],
                              muzzle_flash=match[2] == "1", audio_component=match[3] == "1", stock_sound=match[4] == "1")
        return result
