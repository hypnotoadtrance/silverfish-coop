"""Project worn stock equipment by Unreal's replicated player ID."""

import hashlib
import json
import re

from .native_stock_door import actor_map
from .party import REQUEST_ID


KEY = "native_avatar_outfits"
RECEIPT = "native_avatar_outfits_receipt"
TAG = "native-avatar-outfit"
_ARMOR = re.compile(r"/Game/Classes/Armor_Items/([A-Za-z0-9_]+)\.\1_C")
_ID = re.compile(r"0|[1-9][0-9]{0,9}")


def valid_path(path):
    return isinstance(path, str) and (path == "-" or len(path) <= 160 and _ARMOR.fullmatch(path))


def valid_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) and int(value) <= 2147483647


def parse(fields, phase, area):
    if len(fields) != 5 or phase != "hosting":
        raise ValueError("Invalid authoritative outfit row")
    _, player_id, pawn, body, head = fields
    if (not valid_id(player_id) or not pawn.startswith("Player_FP_C ") or actor_map(pawn) != area
            or not all(map(valid_path, (body, head)))):
        raise ValueError("Invalid authoritative outfit identity")
    return player_id, {"pawn": pawn, "body": body, "head": head}


def frame(host):
    game = host.last_game
    area, epoch = game.get("map"), host.zone_epoch
    if (game.get("state") != "hosting" or not area or KEY not in game
            or not isinstance(epoch, str) or not REQUEST_ID.fullmatch(epoch)):
        return None
    allowed = {owner["pawn"] for owner in host.native_recipients().values() if owner["connected"]}
    allowed.add(game.get("pawns", {}).get("local", {}).get("name"))
    return {"map": area, "epoch": epoch, "outfits": {
        player_id: {"body": outfit["body"], "head": outfit["head"]}
        for player_id, outfit in game[KEY].items() if outfit["pawn"] in allowed
    }}


async def project(bridge, game, incoming, epoch):
    area = game.get("map")
    if (not isinstance(incoming, dict) or not area or (incoming.get("map"), incoming.get("epoch")) != (area, epoch)
            or game.get("state") != "connected" or not isinstance(epoch, str) or not REQUEST_ID.fullmatch(epoch)):
        return game
    outfits = incoming.get("outfits")
    if (not isinstance(outfits, dict) or len(outfits) > 5 or any(
            not valid_id(key) or not isinstance(row, dict) or set(row) != {"body", "head"}
            or not all(map(valid_path, row.values())) for key, row in outfits.items())):
        return game
    receipt = hashlib.sha256(json.dumps([area, epoch, outfits], sort_keys=True).encode()).hexdigest()[:32]
    if game.get(RECEIPT) == receipt:
        return game
    arguments = [value for player_id, row in sorted(outfits.items()) for value in (player_id, row["body"], row["head"])]
    return await bridge.command("native-avatar-outfits", area, receipt, *arguments, timeout=4)
