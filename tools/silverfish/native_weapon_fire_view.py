"""Relay short-lived cosmetic cues from host-authoritative weapon actions."""

import asyncio
import json
import math
import re
import time

from .native_guest_drop_record import valid_id, valid_position


LIMIT = 36
_MAILBOX = "native-weapon-shots"
_MAX_MAILBOX = 16384
_MAX_AGE = .25
_STOCK_CLASS = re.compile(r"/Game/Classes/Weapons/(?:[\w-]+/)*([\w-]+)\.\1_C", re.ASCII)
_STOCK_TURRET = re.compile(r"/Game/Classes/NPCs/(?:[\w-]+/)*(Turret_[\w-]+)\.\1_C", re.ASCII)
_STOCK_CONSUMABLE = re.compile(r"/Game/Classes/Items/(?:[\w-]+/)*([\w-]+)\.\1_C", re.ASCII)
_STOCK_CYCLE = re.compile(r"/Game/Classes/Weapons/Wep_Assets/Wep_Anim/([\w-]+)\.\1", re.ASCII)
_STOCK_ANIMATION = re.compile(r"/Game/(?:[\w-]+/)+([\w-]+)\.\1", re.ASCII)
_STOCK_IMPACT = re.compile(r"/Game/Assets/Sounds/(?:[\w-]+/)*([\w-]+)\.\1", re.ASCII)
_STOCK_DECAL = re.compile(r"/Game/Assets/Materials/Decals/(D_BHole_\w+)\.\1", re.ASCII)
_WALL_COMPONENT = re.compile(r"/Game/[\w./-]+:PersistentLevel\.[\w./-]+", re.ASCII)
MELEE_CLIPS = {"slash": "Standing_Melee_Attack_Horizontal", "backhand": "Standing_Melee_Attack_Backhand",
               "overhead": "Standing_Melee_Attack_Downward"}


def stock_weapon_class(path):
    return type(path) is str and len(path) <= 160 and _STOCK_CLASS.fullmatch(path)


def stock_shot_class(path, *, recoil=False):
    return stock_weapon_class(path) or (not recoil and type(path) is str and len(path) <= 160 and _STOCK_TURRET.fullmatch(path))


def stock_cycle_clip(path):
    return type(path) is str and len(path) <= 192 and _STOCK_CYCLE.fullmatch(path)


def valid_cycle(cue):
    return (type(cue) is list and len(cue) == 5 and type(cue[0]) is int and 0 < cue[0] < 10**15
            and cue[1] == "cycle" and type(cue[2]) is int and 0 <= cue[2] <= 2147483647
            and stock_weapon_class(cue[3]) and stock_cycle_clip(cue[4]))


def valid_shot(cue):
    if type(cue) is not list or len(cue) not in (11, 12, 19):
        return False
    serial, path = cue[:2]
    pose = cue[2:11]
    return (type(serial) is int and 0 < serial < 10**15
            and stock_shot_class(path, recoil=len(cue) > 11)
            and valid_position(pose[:3])
            and all(type(angle) in (int, float) and -180 <= angle <= 180 for angle in pose[3:6])
            and all(type(value) in (int, float) and low <= value <= high
                    for value, (low, high) in zip(pose[6:], ((0, 10), (0, 10), (-26, 24)), strict=True))
            and (len(cue) == 11 or type(cue[11]) is int and 0 <= cue[11] <= 2147483647)
            and (len(cue) != 19 or valid_recoil(cue[11:])))


def valid_recoil(recoil):
    player, *target, recovery = recoil
    return (type(player) is int and 0 <= player <= 2147483647
            and all(type(value) in (int, float) and abs(value) <= limit
                    for value, limit in zip(target, (30, 30, 30, 45, 45, 45), strict=True))
            and type(recovery) in (int, float) and .02 <= recovery <= 1.5)


def valid_melee(cue):
    return (type(cue) is list and len(cue) == 6 and type(cue[0]) is int and 0 < cue[0] < 10**15
            and cue[1] == "melee" and type(cue[2]) is int and 0 <= cue[2] <= 2147483647
            and type(cue[3]) is str and cue[3] in MELEE_CLIPS
            and type(cue[4]) in (int, float) and .25 <= cue[4] <= 1.5
            and type(cue[5]) in (int, float) and 0 <= cue[5] <= .75)


def valid_npc_melee(cue):
    return (type(cue) is list and len(cue) == 7 and type(cue[0]) is int and 0 < cue[0] < 10**15
            and cue[1] == "npc-melee" and type(cue[2]) is int and 0 < cue[2] <= 65535
            and type(cue[3]) is str and len(cue[3]) <= 192 and _STOCK_ANIMATION.fullmatch(cue[3])
            and type(cue[4]) in (int, float) and .1 <= cue[4] <= 8
            and all(type(value) in (int, float) and 0 <= value <= 1 for value in cue[5:]))


def valid_impact(cue):
    return (type(cue) is list and len(cue) == 6 and type(cue[0]) is int and 0 < cue[0] < 10**15
            and cue[1] in ("npc-impact", "melee-impact") and type(cue[2]) is str and len(cue[2]) <= 192
            and _STOCK_IMPACT.fullmatch(cue[2]) and valid_position(cue[3:6]))


def valid_bullet_decal(cue):
    return (type(cue) is list and len(cue) == 13 and type(cue[0]) is int and 0 < cue[0] < 10**15
            and cue[1] == "bullet-decal" and type(cue[2]) is str and len(cue[2]) <= 128
            and _STOCK_DECAL.fullmatch(cue[2]) and type(cue[3]) is str and len(cue[3]) <= 320
            and _WALL_COMPONENT.fullmatch(cue[3]) and valid_position(cue[4:7])
            and all(type(angle) in (int, float) and -180 <= angle <= 180 for angle in cue[7:10])
            and type(cue[10]) in (int, float) and .02 <= abs(cue[10]) <= 128
            and all(type(size) in (int, float) and .02 <= size <= 128 for size in cue[11:]))


def valid_use(cue):
    return (type(cue) is list and len(cue) in (6, 7) and type(cue[0]) is int and 0 < cue[0] < 10**15
            and cue[1] == "consume" and type(cue[2]) is str and len(cue[2]) <= 160
            and _STOCK_CONSUMABLE.fullmatch(cue[2]) and valid_position(cue[3:6])
            and (len(cue) == 6 or type(cue[6]) is int and 0 <= cue[6] <= 2147483647))


def valid_dry_fire(cue):
    return (type(cue) is list and len(cue) == 5 and type(cue[0]) is int and 0 < cue[0] < 10**15
            and cue[1] == "dry" and valid_position(cue[2:]))


def valid_bolt_flight(cue):
    return (type(cue) is list and len(cue) == 11 and type(cue[0]) is int and 0 < cue[0] < 10**15
            and cue[1] == "bolt-flight" and valid_position(cue[2:5]) and valid_position(cue[5:8])
            and math.dist(cue[2:5], cue[5:8]) <= 600
            and all(type(value) in (int, float) and -180 <= value <= 180 for value in cue[8:]))


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
        if len(fields) == 5 and fields[1] == "dry":
            try:
                cue = [int(fields[0]), "dry", *map(float, fields[2:])]
            except ValueError:
                continue
            if valid_dry_fire(cue):
                cues.append(cue)
            continue
        if len(fields) == 5 and fields[1] == "cycle" and re.fullmatch(r"[0-9]{1,10}", fields[2]):
            cue = [int(fields[0]), "cycle", int(fields[2]), fields[3], fields[4]]
            if valid_cycle(cue):
                cues.append(cue)
            continue
        if ((len(fields) == 6 and fields[1] == "melee" or len(fields) == 7 and fields[1] == "npc-melee")
                and re.fullmatch(r"[0-9]{1,10}", fields[2])):
            try:
                cue = [int(fields[0]), fields[1], int(fields[2]), fields[3], *map(float, fields[4:])]
            except ValueError:
                continue
            if valid_melee(cue) or valid_npc_melee(cue):
                cues.append(cue)
            continue
        if len(fields) in (6, 7) and fields[1] in ("consume", "npc-impact", "melee-impact"):
            try:
                cue = [int(fields[0]), fields[1], fields[2], *map(float, fields[3:6])]
                if len(fields) == 7:
                    cue.append(int(fields[6]))
            except ValueError:
                continue
            if valid_use(cue) or valid_impact(cue):
                cues.append(cue)
            continue
        if len(fields) == 13 and fields[1] == "bullet-decal":
            try:
                cue = [int(fields[0]), *fields[1:4], *map(float, fields[4:])]
            except ValueError:
                continue
            if valid_bullet_decal(cue):
                cues.append(cue)
            continue
        if len(fields) not in (11, 12, 19):
            continue
        try:
            cue = [int(fields[0]), fields[1], *map(float, fields[2:11])]
            if len(fields) > 11:
                cue.extend([int(fields[11]), *map(float, fields[12:])])
        except ValueError:
            continue
        if valid_shot(cue) or valid_bolt_flight(cue):
            cues.append(cue)
    return area, cues


async def relay(host, bridge, transport, *, footsteps=None):
    path = bridge.directory / _MAILBOX
    mailbox = getattr(bridge, "local_bridge", None)
    memory = getattr(mailbox, "local_weapon_cues_available", False)
    revision, last_sent = None, 0
    while not host.stopping:
        await asyncio.sleep(.025)
        if footsteps is not None:
            footsteps.flush()
        try:
            if memory:
                frame = mailbox.take_local_weapon_cues()
                if not frame:
                    continue
                data = frame.decode("ascii")
            else:
                info = path.stat()
                current = info.st_mtime_ns, info.st_size
                if (current == revision or not 0 < info.st_size <= _MAX_MAILBOX
                        or abs(time.time() - info.st_mtime) > _MAX_AGE):
                    continue
                with path.open(encoding="ascii") as capture:
                    data = capture.read(_MAX_MAILBOX + 1)
                revision = current
        except (OSError, UnicodeError):
            continue
        if memory and footsteps is not None:
            footsteps.local(data)
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

        frames = []
        for cue in fresh:
            # Quantize presentation only. Stock raycasts still use the original transform.
            if cue[1] == "cycle":
                payload = cue
            elif cue[1] in ("melee", "npc-melee"):
                payload = cue[:4] + [round(value, 3) for value in cue[4:]]
            elif cue[1] in ("consume", "npc-impact", "melee-impact"):
                payload = cue[:3] + [round(value, 2) for value in cue[3:6]] + cue[6:]
            elif cue[1] == "bullet-decal":
                payload = cue[:4] + [round(value, 2) for value in cue[4:]]
            else:
                payload = cue[:2] + [round(axis, 2) for axis in cue[2:]]
            frames.append(json.dumps([area, epoch, payload], separators=(",", ":")).encode("ascii"))
        transport.send_weapon_shots(frames, allowed)


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
                if (valid_cycle(cue) or valid_melee(cue) or valid_npc_melee(cue) or valid_impact(cue)
                        or valid_use(cue) or valid_dry_fire(cue) or valid_bolt_flight(cue)
                        or valid_bullet_decal(cue) or valid_shot(cue)) and cue[0] > self.last_played:
                    selected[cue[0]] = cue[1:]
        if not selected:
            return game
        arguments = []
        for serial, (path, *parameters) in sorted(selected.items())[-LIMIT:]:
            values = (map(str, parameters) if path in ("melee", "npc-melee", "npc-impact", "melee-impact",
                                                       "bullet-decal", "cycle", "consume")
                      else (f"{value:.2f}" for value in parameters))
            arguments.append("|".join((str(serial), path, *values)))
        try:
            result = await bridge.command("native-weapon-play-shots", game["map"], epoch, *arguments, timeout=1.5)
        except (OSError, RuntimeError, TimeoutError) as error:
            control.event("native_weapon_shots_unavailable", reason=str(error))
            return game
        self.last_played = max(selected)
        for receipt in result.get("ack", {}).get("message", "").split(","):
            dry = re.fullmatch(r"([0-9]{1,15}):dry", receipt)
            if dry and (source := selected.get(int(dry[1]))) and source[0] == "dry":
                control.event("native_weapon_dry_fire_played", serial=int(dry[1]))
                continue
            use = re.fullmatch(r"([0-9]{1,15}):consume", receipt)
            if use and (source := selected.get(int(use[1]))) and source[0] == "consume":
                control.event("native_avatar_use_sound_played", serial=int(use[1]), item_class=source[1])
                continue
            visual = re.fullmatch(r"([0-9]{1,15}):consume_visual", receipt)
            if visual and (source := selected.get(int(visual[1]))) and source[0] == "consume" and len(source) == 6:
                control.event("native_avatar_use_visual_played", serial=int(visual[1]), item_class=source[1],
                              player_id=source[5])
                continue
            cycle = re.fullmatch(r"([0-9]{1,15}):cycle", receipt)
            if cycle and (source := selected.get(int(cycle[1]))) and source[0] == "cycle":
                control.event("native_avatar_cycle_played", serial=int(cycle[1]), player_id=source[1],
                              weapon=source[2], clip=source[3])
                continue
            hit = re.fullmatch(r"([0-9]{1,15}):([A-Za-z_]+)", receipt)
            if hit and hit[2] == "bolt_flight" and (source := selected.get(int(hit[1]))) and source[0] == "bolt-flight":
                control.event("native_short_bolt_flight_played", serial=int(hit[1]), origin=source[1:4], finish=source[4:7])
                continue
            if hit and hit[2] == "npc_melee" and (source := selected.get(int(hit[1]))) and source[0] == "npc-melee":
                control.event("native_npc_melee_received", serial=int(hit[1]), actor_id=source[1], clip=source[2])
                continue
            if (hit and hit[2] in ("npc_impact", "melee_impact") and (source := selected.get(int(hit[1])))
                    and source[0].replace("-", "_") == hit[2]):
                control.event(f"native_{hit[2]}_played", serial=int(hit[1]), sound=source[1], position=source[2:])
                continue
            if hit and hit[2] == "bullet_decal" and (source := selected.get(int(hit[1]))) and source[0] == "bullet-decal":
                control.event("native_bullet_decal_played", serial=int(hit[1]), material=source[1], surface=source[2],
                              position=source[3:6])
                continue
            if hit and (source := selected.get(int(hit[1]))) and source[0] == "melee" and MELEE_CLIPS[source[2]] == hit[2]:
                control.event("native_avatar_melee_played", serial=int(hit[1]), player_id=source[1], clip=hit[2])
                continue
            match = re.fullmatch(r"([0-9]{1,15}):([01]):([01]):([01]):([01])", receipt)
            if match and (serial := int(match[1])) in selected and stock_shot_class(selected[serial][0]):
                control.event("native_weapon_shot_played", serial=serial, weapon=selected[serial][0],
                              muzzle_flash=match[2] == "1", audio_component=match[3] == "1", stock_sound=match[4] == "1",
                              recoil=match[5] == "1")
        return result
