"""Verified stock NPC drop identities from the host's live world."""

import re
import uuid

from .native_gold import valid_guid


TAG = "native-stock-enemy-drop"
KEY = "native_stock_enemy_drops"
FIXTURE = "native-stock-enemy-escrow-fixture"
PROOF_TAG = "native-stock-enemy-escrow"
PROOF_KEY = "native_stock_enemy_escrow"
_MAP = re.compile(r"/Game/Classes/Maps(?:/[A-Za-z0-9_]+)+")
_CLASS = re.compile(r"/Game/Classes/[A-Za-z0-9_/]+\.([A-Za-z0-9_]+_C)")
_SERIAL = re.compile(r"[1-9][0-9]{0,15}")


def parse(fields, state, area):
    if (len(fields) != 9 or fields[:2] != [TAG, area] or state != "hosting" or not isinstance(area, str)
            or not _MAP.fullmatch(area) or not all(_SERIAL.fullmatch(value) and int(value) < 2**53 for value in fields[2:4])
            or fields[8] not in ("0", "1")):
        raise ValueError("Invalid stock enemy drop observation")
    _, _, sequence, roll, source, killer, actor, class_path, live = fields
    prefix = re.escape(f" {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.")
    item_class = _CLASS.fullmatch(class_path) if len(class_path) <= 256 else None
    if (not item_class or any(len(name) > 512 for name in (source, killer, actor))
            or not re.fullmatch(r"[A-Za-z0-9_]+_C" + prefix + r"[A-Za-z0-9_]+", source)
            or (killer != "-" and not re.fullmatch(r"Player_FP_C" + prefix + r"Player_FP_C_[0-9]+", killer))
            or not re.fullmatch(re.escape(item_class[1]) + prefix + r"[A-Za-z0-9_]+", actor)):
        raise ValueError("Invalid stock enemy, killer, or item identity")
    return dict(sequence=int(sequence), roll=int(roll), map=area, source=source,
                killer=None if killer == "-" else killer, actor=actor, class_path=class_path, live_at_capture=live == "1")


def parse_escrow(fields, game):
    if len(fields) != 7 or fields[1] != game.get("map") or game.get("state") != "hosting":
        raise ValueError("Invalid stock enemy drop escrow frame")
    _, area, sequence, actor, class_path, guid, ammo = fields
    observation = game.get(KEY, {}).get(actor, {})
    if (not _SERIAL.fullmatch(sequence) or observation.get("sequence") != int(sequence)
            or observation.get("class_path") != class_path or not valid_guid(guid)
            or not re.fullmatch(r"-?[0-9]{1,10}", ammo) or not -2**31 <= int(ammo) < 2**31):
        raise ValueError("Invalid stock enemy drop escrow payload")
    return dict(map=area, sequence=int(sequence), actor=actor, class_path=class_path, guid=guid, ammo=int(ammo))


async def escrow_fixture(bridge, game, actor):
    drop = game.get(KEY, {}).get(actor) if isinstance(actor, str) else None
    if not drop or game.get("state") != "hosting":
        raise ValueError("Select an observed stock NPC item from the current world")
    reply = await bridge.command(FIXTURE, str(drop["sequence"]), f"SFCoop_{uuid.uuid4().hex}", timeout=4)
    proof = reply.get(PROOF_KEY, {})
    if (proof.get("map"), proof.get("sequence"), proof.get("actor"), proof.get("class_path")) != (
            drop["map"], drop["sequence"], drop["actor"], drop["class_path"]):
        raise RuntimeError("The stock NPC drop escrow did not verify")
    return reply, {PROOF_KEY: proof}
