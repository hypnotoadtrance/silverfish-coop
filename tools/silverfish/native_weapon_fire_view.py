"""Relay short-lived cosmetic cues from host-authoritative weapon actions."""

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
MELEE_CLIPS = {"slash": "Standing_Melee_Attack_Horizontal", "backhand": "Standing_Melee_Attack_Backhand",
               "overhead": "Standing_Melee_Attack_Downward"}


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


def valid_melee(cue):
    return (type(cue) is list and len(cue) == 6 and type(cue[0]) is int and 0 < cue[0] < 10**15
            and cue[1] == "melee" and type(cue[2]) is int and 0 <= cue[2] <= 2147483647
            and type(cue[3]) is str and cue[3] in MELEE_CLIPS
            and type(cue[4]) in (int, float) and .25 <= cue[4] <= 1.5
            and type(cue[5]) in (int, float) and 0 <= cue[5] <= .75)


def _captured(data):
    lines = data.splitlines()
    if not data.endswith("\n") or not 1 < len(lines) <= LIMIT + 1:
        return None, []
    area, *rows = lines
    cues = []
    for row in rows:
        fields = row.split("|")
        if not re.fullmatch(r"[1-9][0-9]{0,14}", fields[0]):
            continue
        if len(fields) == 6 and fields[1] == "melee" and re.fullmatch(r"[0-9]{1,10}", fields[2]):
            try:
                cue = [int(fields[0]), "melee", int(fields[2]), fields[3], *map(float, fields[4:])]
            except ValueError:
                continue
            if valid_melee(cue):
                cues.append(cue)
            continue
        if len(fields) != 11:
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
            payload = (cue[:4] + [round(value, 3) for value in cue[4:]] if cue[1] == "melee"
                       else cue[:2] + [round(axis, 2) for axis in cue[2:]])
            packet = json.dumps([area, epoch, payload], separators=(",", ":")).encode("ascii")
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
                if (valid_melee(cue) or valid_shot(cue)) and cue[0] > self.last_played:
                    selected[cue[0]] = cue[1:]
        if not selected:
            return game
        arguments = []
        for serial, (path, *parameters) in sorted(selected.items()):
            values = map(str, parameters) if path == "melee" else (f"{value:.2f}" for value in parameters)
            arguments.append("|".join((str(serial), path, *values)))
        try:
            result = await bridge.command("native-weapon-play-shots", game["map"], epoch, *arguments, timeout=1.5)
        except (OSError, RuntimeError, TimeoutError) as error:
            control.event("native_weapon_shots_unavailable", reason=str(error))
            return game
        self.last_played = max(selected)
        for receipt in result.get("ack", {}).get("message", "").split(","):
            hit = re.fullmatch(r"([0-9]{1,15}):([A-Za-z_]+)", receipt)
            if hit and (source := selected.get(int(hit[1]))) and source[0] == "melee" and MELEE_CLIPS[source[2]] == hit[2]:
                control.event("native_avatar_melee_played", serial=int(hit[1]), player_id=source[1], clip=hit[2])
                continue
            match = re.fullmatch(r"([0-9]{1,15}):([01]):([01]):([01])", receipt)
            if match and (serial := int(match[1])) in selected and selected[serial][0] != "melee":
                control.event("native_weapon_shot_played", serial=serial, weapon=selected[serial][0],
                              muzzle_flash=match[2] == "1", audio_component=match[3] == "1", stock_sound=match[4] == "1")
        return result
