"""Project worn and held stock equipment by Unreal's replicated player ID."""

import hashlib
import json
import re
import time

from .native_stock_door import actor_map
from .native_weapon_fire_view import stock_weapon_class
from .party import REQUEST_ID


KEY = "native_avatar_outfits"
RECEIPT = "native_avatar_outfits_receipt"
HOST_AIM_RECEIPT = "native_avatar_host_aim_receipt"
LOCAL_AIM = "native_avatar_local_aim"
TAG = "native-avatar-outfit"
_ARMOR = re.compile(r"/Game/Classes/Armor_Items/([A-Za-z0-9_]+)\.\1_C")
_MESH = re.compile(r"/Game/(?:Assets|Classes)/[A-Za-z0-9_/-]+/([A-Za-z0-9_-]+)\.\1")
_COSMETIC = re.compile(r"(/Game/Classes/[A-Za-z0-9_/]+/)([A-Za-z0-9_]+)\.\2")
_ID = re.compile(r"0|[1-9][0-9]{0,9}")
_NUMBER = re.compile(r"-?[0-9]{1,3}\.[0-9]{2}")
_COLOR = re.compile(r"[0-9]\.[0-9]{5}(?:,[0-9]\.[0-9]{5}){3}")
_POSE_BOUNDS = (250, 250, 250, 180, 180, 180, 4, 4, 4)
_NPC = "/Game/Classes/NPCs/NPC_Assets/"
_HELMET_MESHES = "/Game/Classes/Player/PlayerAssets/HelmetMeshes/"
_APPEARANCE = ("face", "plumage", "body", "tail_static", "tail_skeletal", "scale_pattern", "skin_color",
               "plumage_color", "eye_color", "eye_style")
_OUTFIT = ("body", "head", "weapon", "appearance", "aim", "firearm")
_AIM = ("-", "pistol", "rifle")


def valid_path(path):
    return isinstance(path, str) and (path == "-" or len(path) <= 160 and _ARMOR.fullmatch(path))


def valid_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) and int(value) <= 2147483647


def weapon_meshes(model):
    if model == "-":
        return []
    if not isinstance(model, str) or not 1 <= len(model) <= 4096:
        return None
    parts = model.split("|")
    if len(parts) > 12:
        return None
    meshes = []
    has_rig = needs_rig = False
    for part in parts:
        columns = part.split("@")
        if len(columns) not in (11, 12) or columns[0] not in ("s", "k", "r", "h"):
            return None
        kind, path, *pose = columns[:11]
        rig = kind in ("r", "h")
        socket = columns[11] if len(columns) == 12 else None
        if rig and has_rig or socket is not None and (rig or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", socket)):
            return None
        has_rig = has_rig or rig
        needs_rig = needs_rig or socket is not None
        if (len(path) > 192 or not _MESH.fullmatch(path) or not all(_NUMBER.fullmatch(value) for value in pose)
                or any(abs(float(value)) > bound for value, bound in zip(pose, _POSE_BOUNDS, strict=True))
                or any(abs(float(value)) < .01 for value in pose[-3:])):
            return None
        if kind != "h":
            meshes.append(path)
    if needs_rig and not has_rig:
        return None
    return meshes


def character_appearance(model):
    if model == "-":
        return {}
    if not isinstance(model, str) or len(model) > 1280:
        return None
    fields = model.split("|")
    if len(fields) != len(_APPEARANCE):
        return None
    result = {}
    for index, value in enumerate(fields[:6]):
        if value == "-":
            continue
        match = _COSMETIC.fullmatch(value)
        if not match or len(value) > 192:
            return None
        folder = match[1]
        if index < 2:
            allowed = folder == _HELMET_MESHES
        elif index < 5:
            allowed = folder.startswith(_NPC) or folder == _HELMET_MESHES
        else:
            allowed = folder == _NPC + "NPC_Materials/NPC_Textures/"
        if not allowed:
            return None
        result[_APPEARANCE[index]] = value
    for index, value in enumerate(fields[6:9], 6):
        if value == "-":
            continue
        if not _COLOR.fullmatch(value):
            return None
        color = [float(channel) for channel in value.split(",")]
        if any(channel > 8 for channel in color):
            return None
        result[_APPEARANCE[index]] = color
    if fields[9] != "-":
        if not re.fullmatch(r"[0-9]{1,2}", fields[9]) or int(fields[9]) > 20:
            return None
        result["eye_style"] = int(fields[9])
    return result


def parse(fields, phase, area):
    if len(fields) not in (5, 6, 7, 8, 9, 10) or phase != "hosting":
        raise ValueError("Invalid authoritative outfit row")
    _, player_id, pawn, body, head = fields[:5]
    weapon = fields[5] if len(fields) >= 6 else "-"
    appearance = fields[6] if len(fields) >= 7 else "-"
    aim = fields[7] if len(fields) >= 8 else "-"
    gun = fields[8] if len(fields) >= 9 else aim
    firearm = fields[9] if len(fields) == 10 else "-"
    if (not valid_id(player_id) or not pawn.startswith("Player_FP_C ") or actor_map(pawn) != area
            or not all(map(valid_path, (body, head))) or weapon_meshes(weapon) is None
            or character_appearance(appearance) is None or aim not in ("-", gun)
            or gun not in _AIM or gun != "-" and weapon == "-"
            or firearm != "-" and (weapon == "-" or not stock_weapon_class(firearm))):
        raise ValueError("Invalid authoritative outfit identity")
    return player_id, {"pawn": pawn, "body": body, "head": head, "weapon": weapon, "appearance": appearance,
                       "aim": aim, "gun": gun, "firearm": firearm}


def guest_aim(host, character, member, pressed):
    pawn = member["pawn"]
    standing = host.party["players"].get(character, {}).get("downed") is False
    if pressed and standing and not host.party.get("wipe"):
        host.guest_avatar_aims[pawn] = host.zone_epoch, time.monotonic() + 1.5
    else:
        host.guest_avatar_aims.pop(pawn, None)


def frame(host):
    game = host.last_game
    area, epoch = game.get("map"), host.zone_epoch
    if (game.get("state") != "hosting" or not area or KEY not in game
            or not isinstance(epoch, str) or not REQUEST_ID.fullmatch(epoch)):
        return None
    allowed = {owner["pawn"]: character for character, owner in host.native_recipients().items() if owner["connected"]}
    lead = game.get("pawns", {}).get("local", {}).get("name")
    now = time.monotonic()
    host.guest_avatar_aims = {pawn: value for pawn, value in host.guest_avatar_aims.items()
                              if pawn in allowed and value[0] == epoch and value[1] > now}
    outfits = {}
    for player_id, outfit in game[KEY].items():
        pawn = outfit["pawn"]
        if pawn in allowed or pawn == lead:
            row = {key: outfit.get(key, "-") for key in _OUTFIT}
            if pawn in allowed:
                standing = host.party["players"].get(allowed[pawn], {}).get("downed") is False
                aiming = pawn in host.guest_avatar_aims and standing and not host.party.get("wipe")
                row["aim"] = outfit.get("gun", "-") if aiming else "-"
            outfits[player_id] = row
    return {"map": area, "epoch": epoch, "outfits": outfits}


async def project_host(host, bridge):
    game = host.last_game
    if not host.guest_avatar_aims and not game.get(HOST_AIM_RECEIPT):
        return game
    incoming = frame(host)
    if incoming is None:
        return game
    lead = game.get("pawns", {}).get("local", {}).get("name")
    aims = {player_id: row["aim"] for player_id, row in incoming["outfits"].items()
            if row["aim"] != "-" and game[KEY][player_id]["pawn"] != lead}
    receipt = hashlib.sha256(json.dumps([incoming["map"], incoming["epoch"], aims], sort_keys=True).encode()).hexdigest()[:32]
    if game.get(HOST_AIM_RECEIPT) == receipt:
        return game
    arguments = [value for pair in sorted(aims.items()) for value in pair]
    return await bridge.command("native-avatar-host-aims", incoming["map"], receipt, *arguments, timeout=2)


async def project(bridge, game, incoming, epoch):
    area = game.get("map")
    if (not isinstance(incoming, dict) or not area or (incoming.get("map"), incoming.get("epoch")) != (area, epoch)
            or game.get("state") != "connected" or not isinstance(epoch, str) or not REQUEST_ID.fullmatch(epoch)):
        return game
    outfits = incoming.get("outfits")
    if (not isinstance(outfits, dict) or len(outfits) > 5 or any(
            not valid_id(key) or not isinstance(row, dict) or set(row) != set(_OUTFIT)
            or not all(map(valid_path, (row["body"], row["head"]))) or weapon_meshes(row["weapon"]) is None
            or character_appearance(row["appearance"]) is None or row["aim"] not in _AIM
            or row["aim"] != "-" and row["weapon"] == "-"
            or row["firearm"] != "-" and (row["weapon"] == "-" or not stock_weapon_class(row["firearm"]))
            for key, row in outfits.items())):
        return game
    receipt = hashlib.sha256(json.dumps([area, epoch, outfits], sort_keys=True).encode()).hexdigest()[:32]
    if game.get(RECEIPT) == receipt:
        return game
    arguments = [value for player_id, row in sorted(outfits.items()) for value in (player_id, *(row[key] for key in _OUTFIT))]
    return await bridge.command("native-avatar-outfits", area, receipt, *arguments, timeout=4)
