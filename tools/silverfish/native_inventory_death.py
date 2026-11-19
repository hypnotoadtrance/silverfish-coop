"""Prepare and verify the exact stock save generations for party recovery."""

import asyncio
import math
import time
import uuid

from . import native_host_save, native_inventory, native_inventory_wipe, party
from .runtime import write_text


async def freeze(host, bridge, wipe_id: str, roster: list[str], *, keep_slots: int, drop_backpacks: bool,
                 lose_slot: bool = False) -> dict:
    await host.checkpoint_native(bridge)
    campaign = host.store.status()
    frames = {actor: native_inventory.saved_frame(host, campaign, actor) for actor in roster}
    if any(frame is None or frame["map"] != host.last_game["map"] for frame in frames.values()):
        raise RuntimeError("The party's exact stock inventory generations are unavailable")
    inventories = {actor: await native_inventory.read_complete(host, bridge, frame) for actor, frame in frames.items()}
    return await asyncio.to_thread(native_inventory_wipe.freeze, host.native_profile, wipe_id, roster, frames,
                                   inventories, keep_slots=keep_slots, drop_bone_pile=drop_backpacks,
                                   lose_slot_per_death=lose_slot)


async def prepare(host, bridge, owner: dict) -> dict:
    source = owner["source"]
    source_slot = source["slot"]
    replacement_slot, bag_slot = native_host_save.new_slot(), native_host_save.new_slot()
    counts = {name: len(owner[name]) for name in ("kept", "dropped", "discarded")}
    expected = {"map": host.last_game["map"], "source": source_slot, "replacement": replacement_slot,
                "escrow": bag_slot if counts["dropped"] else None, **counts}
    path = bridge.directory / f"wipe-{replacement_slot}.tsv"
    write_text(path, "\t".join(",".join(owner[name]) for name in counts) + "\n")
    try:
        snapshot = await bridge.command("native-wipe-prepare-inventory", source_slot, replacement_slot, bag_slot, timeout=8)
        if snapshot.get("state") != "hosting" or snapshot.get("native_wipe_inventory") != expected:
            raise RuntimeError("The game did not verify the exact prepared stock wipe generations")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        replacement = await asyncio.to_thread(native_host_save.record, host.native_profile, replacement_slot,
                                              source["map"], flush=True)
        bag = (await asyncio.to_thread(native_host_save.record, host.native_profile, bag_slot, source["map"], flush=True)
               if counts["dropped"] else None)
    finally:
        path.unlink(missing_ok=True)
    return {"replacement": replacement, "bag": bag, **counts}


def owner_pawns(host) -> dict[str, str]:
    lead = host.last_game.get("pawns", {}).get("local", {}).get("name")
    return {"host": lead, **{actor: member["pawn"] for actor, member in host.native_recipients().items()}}


async def action(host, bridge, operation: str, pawn: str, source: dict, replacement: dict | None = None) -> dict:
    if source.get("character_id") is not None:
        await asyncio.to_thread(native_host_save.guest_record, host.native_profile, source, source["character_id"])
    else:
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, source)
    arguments = [pawn, source["slot"]]
    if replacement is not None:
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, replacement)
        arguments.append(replacement["slot"])
    commands = {"apply": "native-wipe-apply-inventory", "recover": "native-wipe-recover-bag"}
    snapshot = await bridge.command(commands[operation], *arguments, timeout=7)
    report = snapshot.get("native_wipe_inventory_action", {})
    expected = (operation, host.last_game["map"], pawn, source["slot"], replacement["slot"] if replacement else None)
    if snapshot.get("state") != "hosting" or tuple(report.get(key) for key in (
            "action", "map", "pawn", "source", "replacement")) != expected:
        raise RuntimeError("The game did not qualify the exact stock inventory owner and generations")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return report


async def settle(host, bridge, draft: dict) -> bool:
    """Commit all retained inventories and bag references before the party moves."""
    stock = draft["native"]
    if draft.get("native_committed"):
        return True
    owners = owner_pawns(host)
    campaign = host.store.status()
    leases = {entry["character_id"] for entry in campaign["leases"]}
    if not owners["host"] or any(not owners.get(actor) and actor in leases for actor in stock["roster"]):
        return False
    choices = {actor: list(retained) for actor, retained in draft["choices"].items()}
    plan = await asyncio.to_thread(native_inventory_wipe.plan, host.native_profile, stock, choices)
    prepared = {actor: await prepare(host, bridge, partition) for actor, partition in plan["owners"].items()}
    candidate = party.load_party(host.party)
    candidate["wipe"]["native_committed"] = plan["receipt"]
    bags = candidate.setdefault("native_backpacks", {})
    for bag_id, bag in plan["bags"].items():
        escrow = prepared[bag["owner_id"]]["bag"]
        position = draft["drop_positions"].get(bag["owner_id"])
        if not escrow or not position or not draft["drop_backpacks"]:
            continue
        bags[bag_id] = {"owner_id": bag["owner_id"], "position": position,
                        "count": len(bag["guids"]), "escrow": escrow, "claimed": False}
    offline = {}
    host.native_checkpoint_unsafe = True
    try:
        for actor, partition in plan["owners"].items():
            replacement = prepared[actor]["replacement"]
            if pawn := owners.get(actor):
                receipt = await action(host, bridge, "apply", pawn, partition["source"], replacement)
                if not receipt["ok"] or receipt["status"] not in ("applied", "already_applied"):
                    raise RuntimeError(f"Stock inventory wipe apply failed ({receipt['status']})")
            elif actor != "host":
                previous = next(state["state"] for state in campaign["characters"] if state["character_id"] == actor)
                offline[actor] = {**previous, "native_guest_save": dict(replacement, character_id=actor)}
        before, after = (plan["receipt"][key] for key in ("keep_slots", "next_keep_slots"))
        if before != after:
            updated = await bridge.command("native-wipe-update-retention", owners["host"], str(before), str(after), timeout=6)
            if updated.get("state") != "hosting" or updated.get("map") != host.last_game["map"]:
                raise RuntimeError("The host's stock wipe retention update left its qualified world")
            host.last_game, host.last_seen = updated, time.monotonic()
        revision = await host.checkpoint_native(bridge, candidate, unleased_states=offline)
    except (OSError, RuntimeError, ValueError):
        host.stopping = True
        raise
    host.party = candidate
    host.native_checkpoint_unsafe = False
    host.control.event("native_wipe_inventories_committed", revision=revision, **plan["receipt"])
    return True


def items(host, character: str, raw_offset: str | None) -> dict:
    draft = host.party["wipe"]
    if not draft or not draft.get("native") or character not in draft["roster"]:
        raise party.RuleError("native_wipe_selection_unavailable")
    offset = 0
    if raw_offset is not None:
        if not isinstance(raw_offset, str) or not raw_offset.isascii() or not raw_offset.isdigit() or len(raw_offset) > 4:
            raise party.RuleError("invalid_inventory_offset")
        offset = int(raw_offset)
    rows = draft["native"]["inventories"][character]["items"]
    page = rows[offset:offset + native_inventory.PAGE_SIZE]
    end = offset + len(page)
    return {"native_wipe_items": {"wipe_id": draft["id"], "offset": offset, "count": len(rows),
                                   "items": page, "next_offset": end if end < len(rows) else None}}


def visible_bags(party_state: dict, character: str | None = None) -> dict:
    return {key: {field: bag[field] for field in ("owner_id", "position", "count")}
            for key, bag in party_state.get("native_backpacks", {}).items()
            if not bag["claimed"] and (character is None or bag["owner_id"] == character)}


def nearby_bag(host, character: str) -> str | None:
    origin = host.authoritative_pawns(host.roles()).get(character)
    if not origin:
        return None
    closest = []
    for bag_id, bag in visible_bags(host.party, character).items():
        position = bag["position"]
        if origin["map"] == position["map"]:
            distance = math.dist(origin["location"], position["location"])
            if distance <= party.INTERACT_RANGE:
                closest.append((distance, bag_id))
    return min(closest)[1] if closest else None


async def project_bags(service, bridge, owner: str, bags: dict) -> None:
    game, epoch = service.last_game, service.zone_epoch
    area = game.get("map")
    if not area or not isinstance(epoch, str) or not party.REQUEST_ID.fullmatch(epoch):
        return
    current = {bag_id: bag for bag_id, bag in bags.items()
               if bag["owner_id"] == owner and bag["position"]["map"] == area}
    origin = game.get("pawns", {}).get("local", {}).get("location", ())
    if len(current) > 128 and len(origin) == 3 and None not in origin:
        local_ids = sorted(current, key=lambda key: (math.dist(origin, current[key]["position"]["location"]), key))[:128]
    else:
        local_ids = sorted(current)[:128]
    rows = tuple((key, *current[key]["position"]["location"], current[key]["count"]) for key in sorted(local_ids))
    desired = (area, epoch, rows)
    observed = game.get("native_bag_projection")
    if not rows:
        if observed:
            service.last_game = await bridge.command("native-bag-projection-clear", timeout=4)
        service.bag_projection_key = None
        return
    if desired == service.bag_projection_key and observed and observed["epoch"] == epoch and observed["count"] == len(rows):
        return
    snapshot = await bridge.command("native-bag-projection-sync", owner, epoch, area,
                                     *(str(value) for row in rows for value in row), timeout=5)
    if snapshot.get("native_bag_projection") != {"map": area, "epoch": epoch, "owner": owner, "count": len(rows),
                                                 "requested": len(rows), "status": "projected"}:
        raise RuntimeError("The game did not verify the exact local backpack projection")
    service.last_game = snapshot
    service.bag_projection_key = desired


async def recover(host, bridge, character: str, bag_id: str | None, operation_id: str) -> dict:
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")
    if (host.native_profile is None or host.transitioning or host.stopping or host.native_checkpoint_unsafe
            or host.party["wipe"] is not None or character in host.party["pending_spawns"]
            or host.party["players"].get(character, {}).get("downed")):
        raise party.RuleError("native_bag_unavailable")
    if not isinstance(bag_id, str) or not party.REQUEST_ID.fullmatch(bag_id):
        raise party.RuleError("native_wipe_bag_not_owned")
    fingerprint = ["native-wipe-recover", bag_id, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    bag = host.party.get("native_backpacks", {}).get(bag_id)
    if not bag or bag["owner_id"] != character or bag["claimed"]:
        raise party.RuleError("native_wipe_bag_not_owned")
    origin = host.authoritative_pawns(host.roles()).get(character)
    position = bag["position"]
    pawn = owner_pawns(host).get(character)
    if not pawn or not origin or origin["map"] != position["map"] or math.dist(origin["location"], position["location"]) > party.INTERACT_RANGE:
        raise party.RuleError("native_wipe_bag_out_of_range")
    host.native_checkpoint_unsafe = True
    try:
        receipt = await action(host, bridge, "recover", pawn, bag["escrow"])
        if not receipt["ok"] and not receipt["status"].startswith("mutation_unverified_"):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(receipt["status"])
        if not receipt["ok"] or receipt["status"] != "recovered" or receipt["count"] != bag["count"]:
            raise RuntimeError("The stock backpack recovery did not verify all exact rows")
        candidate = party.load_party(host.party)
        prepared = candidate["native_backpacks"][bag_id]
        prepared["claimed"] = True
        prepared.pop("escrow")
        result = {"native_bag": bag_id, "count": receipt["count"]}
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate)
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party = candidate
    host.native_checkpoint_unsafe = False
    host.control.event("native_wipe_bag_recovered", owner=character, revision=revision, **result)
    return result


async def probe(host, bridge, character: str, keep_guid: str | None) -> dict:
    if (not host.fixtures or not host.native_zones or not host.native_guest or host.native_profile is None
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or character not in host.roles().values()
            or (character != "host" and character not in host.native_recipients())):
        raise party.RuleError("native_wipe_inventory_fixture_unavailable")
    roster = ["host"] if character == "host" else ["host", character]
    frozen = await freeze(host, bridge, uuid.uuid4().hex, roster, keep_slots=1, drop_backpacks=True)
    choice = {character: [keep_guid]} if keep_guid else {}
    plan = await asyncio.to_thread(native_inventory_wipe.plan, host.native_profile, frozen, choice)
    prepared = await prepare(host, bridge, plan["owners"][character])
    projected = await native_inventory.read_complete(host, bridge, prepared["replacement"])
    expected = plan["owners"][character]
    if {item["guid"] for item in projected["items"]} != set(expected["kept"]):
        raise RuntimeError("The prepared stock wipe kept a different inventory")
    return {"native_wipe_inventory": {"owner": character, "projected": projected["items"],
                                      **{name: prepared[name] for name in ("kept", "dropped", "discarded")}}}
