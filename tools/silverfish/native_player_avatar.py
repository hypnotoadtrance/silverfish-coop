"""Project worn and held stock equipment by Unreal's replicated player ID."""

import hashlib
import json
import re

from .native_stock_door import actor_map
from .party import REQUEST_ID


KEY = "native_avatar_outfits"
RECEIPT = "native_avatar_outfits_receipt"
TAG = "native-avatar-outfit"
_ARMOR = re.compile(r"/Game/Classes/Armor_Items/([A-Za-z0-9_]+)\.\1_C")
_MESH = re.compile(r"/Game/(?:Assets|Classes)/[A-Za-z0-9_/]+/([A-Za-z0-9_]+)\.\1")
_COSMETIC = re.compile(r"(/Game/Classes/[A-Za-z0-9_/]+/)([A-Za-z0-9_]+)\.\2")
_ID = re.compile(r"0|[1-9][0-9]{0,9}")
_NUMBER = re.compile(r"-?[0-9]{1,3}\.[0-9]{2}")
_COLOR = re.compile(r"[0-9]\.[0-9]{5}(?:,[0-9]\.[0-9]{5}){3}")
_POSE_BOUNDS = (250, 250, 250, 180, 180, 180, 4, 4, 4)
_NPC = "/Game/Classes/NPCs/NPC_Assets/"
_HELMET_MESHES = "/Game/Classes/Player/PlayerAssets/HelmetMeshes/"
_APPEARANCE = ("face", "plumage", "body", "tail_static", "tail_skeletal", "scale_pattern", "skin_color",
               "plumage_color", "eye_color", "eye_style")
_OUTFIT = ("body", "head", "weapon", "appearance")


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
    for part in parts:
        columns = part.split("@")
        if len(columns) != 11 or columns[0] not in ("s", "k"):
            return None
        path, pose = columns[1], columns[2:]
        if (len(path) > 192 or not _MESH.fullmatch(path) or not all(_NUMBER.fullmatch(value) for value in pose)
                or any(abs(float(value)) > bound for value, bound in zip(pose, _POSE_BOUNDS, strict=True))
                or any(abs(float(value)) < .01 for value in pose[-3:])):
            return None
        meshes.append(path)
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
    if len(fields) not in (5, 6, 7) or phase != "hosting":
        raise ValueError("Invalid authoritative outfit row")
    _, player_id, pawn, body, head = fields[:5]
    weapon = fields[5] if len(fields) >= 6 else "-"
    appearance = fields[6] if len(fields) == 7 else "-"
    if (not valid_id(player_id) or not pawn.startswith("Player_FP_C ") or actor_map(pawn) != area
            or not all(map(valid_path, (body, head))) or weapon_meshes(weapon) is None
            or character_appearance(appearance) is None):
        raise ValueError("Invalid authoritative outfit identity")
    return player_id, {"pawn": pawn, "body": body, "head": head, "weapon": weapon, "appearance": appearance}


def frame(host):
    game = host.last_game
    area, epoch = game.get("map"), host.zone_epoch
    if (game.get("state") != "hosting" or not area or KEY not in game
            or not isinstance(epoch, str) or not REQUEST_ID.fullmatch(epoch)):
        return None
    allowed = {owner["pawn"] for owner in host.native_recipients().values() if owner["connected"]}
    allowed.add(game.get("pawns", {}).get("local", {}).get("name"))
    return {"map": area, "epoch": epoch, "outfits": {
        player_id: {key: outfit.get(key, "-") for key in _OUTFIT}
        for player_id, outfit in game[KEY].items() if outfit["pawn"] in allowed
    }}


async def project(bridge, game, incoming, epoch):
    area = game.get("map")
    if (not isinstance(incoming, dict) or not area or (incoming.get("map"), incoming.get("epoch")) != (area, epoch)
            or game.get("state") != "connected" or not isinstance(epoch, str) or not REQUEST_ID.fullmatch(epoch)):
        return game
    outfits = incoming.get("outfits")
    if (not isinstance(outfits, dict) or len(outfits) > 5 or any(
            not valid_id(key) or not isinstance(row, dict) or set(row) != set(_OUTFIT)
            or not all(map(valid_path, (row["body"], row["head"]))) or weapon_meshes(row["weapon"]) is None
            or character_appearance(row["appearance"]) is None
            for key, row in outfits.items())):
        return game
    receipt = hashlib.sha256(json.dumps([area, epoch, outfits], sort_keys=True).encode()).hexdigest()[:32]
    if game.get(RECEIPT) == receipt:
        return game
    arguments = [value for player_id, row in sorted(outfits.items()) for value in (player_id, *(row[key] for key in _OUTFIT))]
    return await bridge.command("native-avatar-outfits", area, receipt, *arguments, timeout=4)
