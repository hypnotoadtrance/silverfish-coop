"""Host-owned, stock mushroom patches and their physical pickup claims."""

import re
import time
import uuid

from . import native_loot, native_pickup_audio, party
from .authored_loot import valid_item_transform
from .native_gold import valid_guid

ROW = "native-mushroom"
CLAIM = "native-mushroom-claim"
KEY = "native_mushrooms"
PROOF = "native_mushroom_claim"
CAPABILITY = "native_mushroom_version"
SUPPRESSION = "native_mushroom_suppression"
APPROACH = "native-mushroom-approach-fixture"
HARVEST = "native-mushroom-harvest-fixture"
LOCATE = "native-mushroom-locate-fixture"
LIMIT = 128
_AREA = re.compile(r"/Game/Classes/Maps/[A-Za-z0-9_/]+/([A-Za-z0-9_-]+)\Z")
_ITEM = re.compile(r"/Game/Classes/Items/[A-Za-z0-9_/]+\.([A-Za-z0-9_]+_C)\Z")


def identity(patch, guid):
    return uuid.uuid5(uuid.NAMESPACE_OID, f"mushroom:{patch}:{guid}").hex


def _actor(area, path, class_name):
    match = _AREA.fullmatch(area) if isinstance(area, str) else None
    if not match or not isinstance(path, str) or len(path) > 256:
        return False
    prefix = f"{area}.{match[1]}:PersistentLevel.{class_name}_"
    return path.startswith(prefix) and re.fullmatch(r"[0-9]{1,10}", path[len(prefix):]) is not None


def parse(fields, phase, area):
    if len(fields) != 16 or fields[1] != area or phase != "hosting":
        raise ValueError("Unexpected stock mushroom report")
    _, _, patch, guid, actor, item, raw_units, *pose = fields
    match = _ITEM.fullmatch(item)
    transform_values = [float(axis) for axis in pose]
    transform = dict(position=transform_values[:3], rotation=transform_values[3:6], scale=transform_values[6:])
    units = int(raw_units)
    if (not valid_guid(guid) or not _actor(area, patch, "MushroomPatch_BP_C") or not match
            or len(item) > 256 or not _actor(area, actor, match[1]) or not -1 <= units < 2**31
            or not valid_item_transform(transform)):
        raise ValueError("Invalid stock mushroom source")
    return dict(map=area, id=identity(patch, guid), patch=patch, patch_guid=guid, actor=actor,
                class_path=item, transform=transform, amount=units)


def parse_claim(fields, phase, area):
    if len(fields) != 13 or fields[1] != area or phase != "hosting":
        raise ValueError("Unexpected stock mushroom transfer report")
    _, _, patch, guid, pawn, actor, item_guid, units, status, amount, before, after, inventory_guid = fields
    raw_units = int(units)
    if (not valid_guid(guid) or not valid_guid(item_guid) or not -1 <= raw_units < 2**31
            or not _actor(area, patch, "MushroomPatch_BP_C") or status not in ("claimed", "merged")):
        raise ValueError("Invalid stock mushroom transfer")
    result = dict(map=area, patch=patch, patch_guid=guid, pawn=pawn, actor=actor, guid=item_guid, amount=raw_units)
    if status == "merged":
        result.update(native_loot.verified_stack_merge(
            dict(guid=item_guid, inventory_guid=inventory_guid, amount=int(amount), before=int(before), after=int(after)),
            item_guid, raw_units,
        ))
    elif (amount, before, after, inventory_guid) != ("0", "0", "0", "-"):
        raise ValueError("Unexpected mushroom stack counts")
    return result


def frame(game, epoch):
    if game.get(CAPABILITY) != 1 or game.get("state") != "hosting" or not party.REQUEST_ID.fullmatch(epoch or ""):
        return None
    return {"map": game.get("map"), "epoch": epoch, "items": game.get(KEY, {})}


def visible(public, area, epoch):
    if not isinstance(public, dict) or (public.get("map"), public.get("epoch")) != (area, epoch):
        return
    rows = public.get("items")
    if not isinstance(rows, dict) or len(rows) > LIMIT:
        return
    for key, row in rows.items():
        if (isinstance(key, str) and party.REQUEST_ID.fullmatch(key) and isinstance(row, dict)
                and (row.get("map"), row.get("id")) == (area, key) and valid_guid(row.get("patch_guid"))
                and _actor(area, row.get("patch"), "MushroomPatch_BP_C")
                and identity(row["patch"], row["patch_guid"]) == key):
            yield key, row


async def claim(host, bridge, character, requested, operation):
    target, _, epoch = requested.partition("@") if isinstance(requested, str) else ("", "", "")
    game = host.last_game
    area = game.get("map")
    if (not party.REQUEST_ID.fullmatch(target) or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation)
            or epoch != host.zone_epoch or game.get(CAPABILITY) != 1 or game.get("state") != "hosting"
            or not host.native_profile or host.transitioning or host.stopping or host.native_checkpoint_unsafe):
        raise party.RuleError("native_mushroom_scope_unavailable")
    fingerprint = [CLAIM, target, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    row = game.get(KEY, {}).get(target)
    if not row:
        raise party.RuleError("native_mushroom_source_unavailable")
    pawn = native_loot._scope(host, character)
    member = host.party["players"].get(character)
    if not pawn or not member or member["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]:
        raise party.RuleError("native_mushroom_claimant_unavailable")
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(CLAIM, row["patch"], row["patch_guid"], pawn, timeout=7)
        except RuntimeError as error:
            reason = str(error)
            if reason in {"native_mushroom_source_unavailable", "native_mushroom_claim_scope_unavailable",
                          "native_mushroom_claimant_out_of_reach_or_unavailable"} or re.fullmatch(
                              r"native_mushroom_(capture|transfer):native_\w+", reason):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(reason) from error
            raise
        proof = snapshot.get(PROOF, {})
        if (tuple(proof.get(key) for key in ("map", "patch", "patch_guid", "actor", "pawn", "amount"))
                != (area, row["patch"], row["patch_guid"], row["actor"], pawn, row["amount"]) or target in snapshot.get(KEY, {})):
            raise RuntimeError("The stock mushroom checkpoint did not verify the transfer and removal")
        result = {PROOF: dict(proof, id=target, class_path=row["class_path"], owner=character)}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        host.last_game, host.last_seen = snapshot, time.monotonic()
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}),
                                                typed_inventory=True)
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_mushroom_claimed", revision=revision, operation_id=operation, **result[PROOF])
        native_pickup_audio.committed(host, pawn, operation, row["class_path"])
        return result
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True
