"""Bank and claim ammunition from exact stock enemy encounters."""

import asyncio
import math
import os
import time

from . import native_enemy_shells, native_host_save, native_loot, native_pickup_audio, native_world_loot, party
from .enemy_loot_profiles import BY_ACTOR, BY_MAP, SHELLS, for_roll
from .native_downed_markers import location
from .native_gold import valid_guid
from .native_host_save import CQC_DROP_SOURCE as CQC, CQC_DROP_SOURCES as CQC_ACTORS
from .runtime import MAP
from .travel import DEAD_BRIDGE

BANK = "native-enemy-loot-bank"
CLAIM = "native-enemy-loot-claim"
APPROACH = "native-enemy-loot-approach"
RETIRE = "native-enemy-loot-retire"
PENDING = "native_enemy_loot_roll_pending"
STATE_KEY = "native_enemy_loot_rolls"
MEMORY_CAPABILITY = "native_enemy_loot_memory"
CQC_SCOPE = "native_enemy_loot_cqc"
DEATH_VIEW = "native_enemy_loot_death_view"
SHELL_ACTOR = native_host_save.CQC_SHELL_ACTOR



def _actor_row(game, key, actor):
    by_actor = game.get(key + "_by_actor")
    if actor in BY_ACTOR and isinstance(by_actor, dict):
        return by_actor.get(actor, {})
    row = game.get(key, {})
    return row if actor in (row.get("bandit"), row.get("actor")) else {}


def source_for(game, actor):
    return _actor_row(game, "native_enemy_loot", actor)


def watch_target(game: dict, target: str | None) -> str | None:
    if not isinstance(target, str):
        return None
    area = game.get("map")
    if area == MAP and target and target == game.get("native_bandit", {}).get("npc"):
        return target
    if (area == DEAD_BRIDGE and target == CQC) or (target in BY_ACTOR and _cqc_receipt(game, target)):
        return target
    return None


def published(records: dict) -> dict:
    return {key: _public_roll(roll) for key, roll in records.items()}


def _public_roll(roll):
    return {key: value for key, value in roll.items() if key not in ("escrow", "retirement")}


def _exact_cqc_roll(roll, guid):
    profile = for_roll(roll) if isinstance(roll, dict) else None
    return (profile and profile.owns_shell(roll.get("actor"))
            and valid_guid(guid) and roll.get("guid") == guid and roll.get("class_path") == SHELLS
            and type(roll.get("quantity")) is int and 3 <= roll["quantity"] <= 9)


def defeated_cqc(records: dict, epoch: str, target: str = CQC) -> str | None:
    profile = BY_ACTOR.get(target)
    if (not profile or type(records) is not dict or not isinstance(epoch, str) or len(epoch) != 32
            or any(char not in "0123456789abcdef" for char in epoch)):
        return None
    selected = None
    for guid, roll in records.items():
        if not isinstance(roll, dict) or (roll.get("map"), roll.get("bandit"), roll.get("epoch")) != (profile.map, target, epoch):
            continue
        if selected is not None or not _exact_cqc_roll(roll, guid):
            return None
        selected = guid
    return selected


async def project_client(client) -> None:
    game, epoch = client.last_game, client.zone_epoch
    area = game.get("map")
    profile = BY_MAP.get(area)
    if (not client.transport.connected or client.stopping or client.needs_travel or client.server_transitioning
            or not profile or client.expected_zone != area or time.monotonic() - client.gameplay_seen >= 3
            or game.get("state") != "connected"):
        return
    for target in profile.actors:
        proof = _actor_row(client.last_game, DEATH_VIEW, target)
        if (proof.get("map"), proof.get("actor")) != (area, target) or proof.get("state") not in ("present", "projected"):
            continue
        guid = defeated_cqc(client.gameplay.get(STATE_KEY), epoch, target)
        if not guid or (proof.get("state"), proof.get("epoch"), proof.get("guid"), proof.get("hidden"), proof.get("collision")) == (
                "projected", epoch, guid, True, False):
            continue
        snapshot = await client.bridge.command("native-enemy-loot-project-death", target, epoch, guid, timeout=3)
        proof = _actor_row(snapshot, DEATH_VIEW, target)
        if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("actor")) != (
            "connected", area, area, target
        ) or (proof.get("state") != "stock" and (
                proof.get("state"), proof.get("epoch"), proof.get("guid"), proof.get("hidden"), proof.get("collision")
        ) != ("projected", epoch, guid, True, False)):
            raise RuntimeError("The guest did not verify the stock CQC death presentation")
        client.last_game = snapshot
        if proof["state"] == "projected":
            client.control.event("native_enemy_loot_death_projected", **proof)


def _ordinary(host, area):
    return area in BY_MAP and getattr(host, "native_dead_bridge_enemy_loot", False) is True


def _available(host, *, bank=False, fixture=False):
    area = host.last_game.get("map")
    ordinary = _ordinary(host, area)
    if ((not host.fixtures and (not ordinary or fixture)) or not host.native_guest or not host.native_profile or host.stopping
            or ((host.transitioning or host.party["wipe"]) and not (bank and ordinary))
            or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or (area not in (MAP, DEAD_BRIDGE) and not ordinary) or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_enemy_loot_unavailable")
    if area == DEAD_BRIDGE and not ordinary and any(os.environ.get(name) != "1" for name in (
        "SILVERFISH_COOP_NATIVE_ENEMY_LOOT", "SILVERFISH_COOP_NATIVE_NPC_TEST",
    )):
        raise party.RuleError("native_enemy_loot_unavailable")
    return area


async def approach(host, bridge, target: str | None) -> dict:
    area = _available(host, fixture=True)
    role, sep, guid = target.partition("|") if isinstance(target, str) else ("", "", "")
    roll = host.party.get(STATE_KEY, {}).get(guid) if sep and valid_guid(guid) else None
    character = host.roles().get(role)
    pawn = native_loot._scope(host, character) if character else None
    if (not roll or roll.get("map") != area or (area in BY_MAP and not for_roll(roll))
            or roll.get("claimed_by") is not None
            or not pawn or host.party["players"][character]["downed"]):
        raise party.RuleError("native_enemy_loot_fixture_unavailable")
    snapshot = await bridge.command(APPROACH, pawn, guid, *(str(axis) for axis in roll["position"]), timeout=5)
    proof = snapshot.get("native_enemy_loot_escrow", {})
    if (proof.get("map"), proof.get("state"), proof.get("pawn"), proof.get("guid")) != (area, "approached", pawn, guid):
        raise RuntimeError("The headless stock drop approach did not verify")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"native_enemy_loot_approach": proof}


async def _commit(host, bridge, candidate, snapshot, *, deferred=False, require_guest_frames=frozenset(),
                  typed_inventory=False):
    host.last_game, host.last_seen = snapshot, time.monotonic()
    revision = None
    if deferred:
        host.pending_host_save = bool(host.pending_host_save or typed_inventory)
    else:
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=require_guest_frames,
                                                typed_inventory=typed_inventory)
    host.party = candidate
    host.native_checkpoint_unsafe = False
    return revision


def _cqc_receipt(game, target):
    if not isinstance(target, str):
        return {}
    profile = BY_ACTOR.get(target)
    scope = _actor_row(game, CQC_SCOPE, target)
    return scope if profile and (game.get("map"), scope.get("map"), scope.get("bandit")) == (profile.map, profile.map, target) else {}


def pending(host):
    area = host.last_game.get("map")
    return _ordinary(host, area) and any(
        _cqc_receipt(host.last_game, target).get("reserved") is True for target in BY_MAP[area].actors)


def _shell_epoch(host):
    game, epoch = host.last_game, host.zone_epoch
    if (not _ordinary(host, game.get("map")) or game.get("state") != "hosting" or host.stopping
            or host.transitioning or host.party["wipe"] or host.native_checkpoint_unsafe
            or time.monotonic() - host.last_seen >= 2 or not native_enemy_shells.valid_epoch(epoch)):
        return None
    return epoch


def _eligible_drop(roll, guid, area):
    return (_exact_cqc_roll(roll, guid) and roll.get("map") == area
            and roll.get("claimed_by") is None and location(roll.get("position"))
            and native_enemy_shells.valid_epoch(roll.get("epoch")) and native_host_save.cqc_retired(roll))


def shell_candidates(host) -> dict | None:
    epoch = _shell_epoch(host)
    if not epoch:
        return None
    game = host.last_game
    area = game["map"]
    points = [point for pawn in game.get("pawns", {}).values() if location(point := pawn.get("location"))]
    eligible = []
    for guid, roll in host.party.get(STATE_KEY, {}).items():
        if _eligible_drop(roll, guid, area):
            nearest = min((math.dist(point, roll["position"]) for point in points), default=math.inf)
            eligible.append((nearest, roll["epoch"] != epoch, guid, roll))
    shells = {guid: [roll["quantity"], *roll["position"], roll["actor"]]
              for _, _, guid, roll in sorted(eligible)[:native_enemy_shells.LIMIT]}
    return {"map": area, "epoch": epoch, "shells": shells} if shells else None


def shell_frame(host) -> dict | None:
    epoch = _shell_epoch(host)
    area = host.last_game.get("map")
    proof = host.last_game.get(native_enemy_shells.AUTHORITY_KEY, {})
    eligible = proof.get("eligible")
    if (not epoch or host.last_game.get("native_enemy_shell_view_version") != 2
            or not isinstance(eligible, list) or len(eligible) > native_enemy_shells.LIMIT
            or (proof.get("map"), proof.get("epoch"), proof.get("pawn")) != (
            area, epoch, native_loot._scope(host, "host"))):
        return None
    rolls = host.party.get(STATE_KEY, {})
    shells = {guid: [roll["quantity"], *roll["position"]] for guid in eligible
              if valid_guid(guid) and _eligible_drop(roll := rolls.get(guid), guid, area)}
    return {"map": area, "epoch": epoch, "shells": shells} if shells else None


def _stock_killer(host, source):
    if not _ordinary(host, source.get("map")):
        member = host.roles().get(source.get("killer_role"))
        return member if member and native_loot._scope(host, member) == source.get("killer_pawn") else None
    target = source.get("bandit")
    profile = BY_ACTOR.get(target)
    record = _cqc_receipt(host.last_game, target)
    bounty = _actor_row(host.last_game, "native_enemy_bounty", target)
    pawn = source.get("killer_pawn")
    if (not profile or (bounty.get("map"), bounty.get("bandit"), bounty.get("cache_unchanged")) != (profile.map, target, True)
            or not isinstance(pawn, str) or not pawn.startswith(f"Player_FP_C {profile.world}:PersistentLevel.")
            or type(source.get("bounty_calls")) is not int or source["bounty_calls"] < 1):
        return None
    identity = record.get("killer_character")
    if source.get("killer_role") == "host":
        if (identity is None and pawn == native_loot._scope(host, "host") and bounty.get("source") == "stock_host"
                and bounty.get("override_count") == 0 and bounty.get("original_instances") == source.get("bounty_instances")
                and bounty.get("original_active") == source.get("bounty_active")):
            return "host"
        return None
    counts = tuple(bounty.get(field) for field in ("permanent_count", "timed_count", "gear_count"))
    if (source.get("killer_role") == "guest" and identity in host.party["players"] and identity != "host"
            and bounty.get("source") == "guest_detached" and bounty.get("override_count") == source["bounty_calls"]
            and all(type(amount) is int and amount >= 0 for amount in counts) and source.get("bounty_instances") == sum(counts)
            and source.get("bounty_active") is (sum(counts) > 0)
            and not any(other != identity and member.get("pawn") == pawn for other, member in host.native_recipients().items())):
        return identity
    return None


async def _retire(host, bridge, roll):
    host.native_checkpoint_unsafe = True
    try:
        slot, guid = roll["escrow"]["slot"], roll["guid"]
        for attempt in range(3):
            try:
                snapshot = await bridge.command(RETIRE, roll["actor"], slot, guid, timeout=6)
                break
            except RuntimeError as error:
                if attempt == 2 or str(error) != "native_enemy_loot_raw_retirement_pending":
                    raise
        result = snapshot.get("native_enemy_loot_escrow", {})
        scope = _cqc_receipt(snapshot, roll["bandit"])
        raw_proof = snapshot.get("native_enemy_loot_retire_proof")
        if ((snapshot.get("state"), snapshot.get("map"), result.get("state"), result.get("actor"), result.get("slot"), result.get("guid"))
                != ("hosting", roll["map"], "retired", roll["actor"], slot, guid)
                or raw_proof != {"schema": 1, "map": roll["map"], "actor": roll["actor"], "slot": slot, "guid": guid}
                or result.get("units") != roll["quantity"] or result.get("position") != roll["position"]
                or scope.get("captured") is not True or scope.get("reserved") is not False):
            raise RuntimeError("The committed shotgun shells did not verify exact physical retirement")
        candidate = party.load_party(host.party)
        candidate[STATE_KEY][guid]["retirement"] = native_host_save.cqc_retirement_record(roll, slot)
        return await _commit(host, bridge, candidate, snapshot, deferred=roll["escrow"].get("pending") is True)
    except (asyncio.CancelledError, OSError, RuntimeError, ValueError):
        host.stopping = True
        raise


async def observe(host, bridge):
    if not pending(host) or host.stopping or host.native_checkpoint_unsafe:
        return False
    settling = False
    try:
        for target in BY_MAP[host.last_game["map"]].actors:
            if _cqc_receipt(host.last_game, target).get("reserved") is True:
                try:
                    await bank(host, bridge, target)
                except party.RuleError as error:
                    if str(error) != "native_enemy_loot_drop_settling":
                        raise
                    settling = True
    except party.RuleError as error:
        host.native_checkpoint_unsafe = host.stopping = True
        raise RuntimeError(f"The reserved stock CQC roll could not be verified ({error})") from error
    return settling


async def bank(host, bridge, target: str | None) -> dict:
    area = _available(host, bank=True)
    profile = BY_MAP.get(area)
    cqc = _ordinary(host, area)
    source = source_for(host.last_game, target)
    if (source.get("state"), source.get("map"), source.get("loot_calls"), source.get("ammo_class")) != (
            "stock_loot_observed", area, 1, SHELLS) or target != source.get("bandit") or (profile and target not in profile.actors):
        raise party.RuleError("native_enemy_loot_exact_drop_unavailable")
    ammo = source.get("ammo_actor")
    if cqc and not profile.owns_shell(ammo):
        raise party.RuleError("native_enemy_loot_exact_drop_unavailable")
    scope = _cqc_receipt(host.last_game, target)
    for recorded in host.party.get(STATE_KEY, {}).values():
        if (recorded["epoch"], recorded["bandit"], recorded["actor"]) == (host.zone_epoch, target, ammo):
            if cqc and scope.get("reserved") is True:
                if recorded.get("escrow") is None or not scope.get("captured"):
                    raise RuntimeError("The durable CQC roll conflicts with the pending stock shell")
                await _retire(host, bridge, recorded)
            return {"native_enemy_loot_roll": _public_roll(recorded)}
        if cqc and recorded["epoch"] == host.zone_epoch and (recorded["bandit"] == target or recorded["actor"] == ammo):
            raise RuntimeError("The pending CQC stock shell collides with another banked roll")
    if cqc and (scope.get("reserved") is not True or scope.get("captured") is not False):
        raise party.RuleError("native_enemy_loot_exact_drop_unavailable")
    killer = _stock_killer(host, source)
    if not killer:
        raise party.RuleError("native_enemy_loot_exact_killer_unavailable")
    slot = native_host_save.new_slot()
    memory = cqc and host.last_game.get(MEMORY_CAPABILITY) is True
    host.native_checkpoint_unsafe = True
    try:
        try:
            options = ("memory-escrow",) if memory else ()
            snapshot = await native_loot.submit_loot_command(host, bridge, BANK, None, target, slot, ammo,
                                                             *options, timeout=7)
        except RuntimeError as error:
            if str(error) != "native_enemy_loot_drop_settling":
                raise
            host.native_checkpoint_unsafe = False
            raise party.RuleError(str(error)) from error
        proof = snapshot.get("native_enemy_loot_escrow", {})
        at, guid = proof.get("position"), proof.get("guid")
        if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("state"), proof.get("pawn"),
            proof.get("actor"), proof.get("slot"), proof.get("units")) != (
                "hosting", area, area, "captured", source["killer_pawn"], source["ammo_actor"], slot, source["ammo_quantity"]
        ) or not valid_guid(guid) or not at or any(coord is None or abs(coord) > 10000000 for coord in at):
            raise RuntimeError("The exact stock enemy drop escrow did not verify")
        if (proof.get("memory") is True) != memory:
            raise RuntimeError("The stock enemy shell did not verify its requested escrow mode")
        frame = ({"slot": slot, "map": area, "pending": True} if memory else
                 await asyncio.to_thread(native_host_save.record, host.native_profile, slot, area, flush=True))
        roll = {"map": area, "epoch": host.zone_epoch, "bandit": target, "actor": proof["actor"], "guid": guid,
                "class_path": SHELLS, "quantity": proof["units"], "position": at, "killer": killer, "claimed_by": None,
                "escrow": frame}
        if cqc:
            roll["profile"] = profile.id
        candidate = native_loot.copy_pending_loot(host.party, STATE_KEY) if memory else party.load_party(host.party)
        records = candidate.setdefault(STATE_KEY, {})
        if guid in records:
            raise RuntimeError("The stock enemy drop identity already exists")
        records[guid] = roll
        revision = await _commit(host, bridge, candidate, snapshot, deferred=memory)
        if cqc:
            revision = await _retire(host, bridge, roll)
        report = _public_roll(roll)
        host.control.event("native_enemy_loot_roll_committed", revision=revision, **report)
        return {"native_enemy_loot_roll": report}
    except (asyncio.CancelledError, OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise


async def claim(host, bridge, character: str, target: str | None, operation: str) -> dict:
    area = _available(host)
    focus = native_enemy_shells.target_parts(target)
    guid = focus[1] if focus else target
    if not valid_guid(guid) or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("native_enemy_loot_exact_claim_required")
    if focus and (not _ordinary(host, area) or focus[0] != host.zone_epoch):
        raise party.RuleError("native_enemy_loot_stale_focus")
    fingerprint = [CLAIM, target, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    roll = host.party.get(STATE_KEY, {}).get(guid)
    if not roll or roll.get("map") != area or (area in BY_MAP and not for_roll(roll)):
        raise party.RuleError("native_enemy_loot_item_unavailable")
    if roll.get("claimed_by") is not None:
        raise party.RuleError("native_enemy_loot_already_claimed")
    if focus and guid not in (shell_frame(host) or {}).get("shells", {}):
        raise party.RuleError("native_enemy_loot_stale_focus")
    record = host.party["players"].get(character)
    pawn = native_loot._scope(host, character)
    if not record or record["downed"] or not pawn or character in host.party["pending_spawns"]:
        raise party.RuleError("native_enemy_loot_claimant_unavailable")
    location = next((row.get("location") for row in host.last_game.get("pawns", {}).values()
                     if row.get("name") == pawn), None)
    world_focus = focus and host.last_game.get(native_world_loot.CAPABILITY, 0) >= 1
    if not location or any(axis is None for axis in location):
        raise party.RuleError("native_enemy_loot_out_of_reach")
    if math.dist(location, roll["position"]) > party.INTERACT_RANGE:
        raise party.RuleError("native_enemy_loot_out_of_reach")
    frame = roll["escrow"]
    deferred = _ordinary(host, area) and host.last_game.get(MEMORY_CAPABILITY) is True
    if deferred and character != "host":
        from .native_inventory import retain_guest_save

        if not await retain_guest_save(host, bridge, character, pawn):
            raise party.RuleError("native_guest_save_retention_unavailable")
    if not frame.get("pending"):
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    host.native_checkpoint_unsafe = True
    try:
        try:
            mode = "stock-exact"
            if focus:
                mode = "stock-world-focus" if world_focus else "stock-focus"
            check = (mode, roll["actor"]) if _ordinary(host, area) else ()
            snapshot = await native_loot.submit_loot_command(host, bridge, CLAIM, operation, pawn, frame["slot"], guid,
                                                             *(str(axis) for axis in roll["position"]), *check, timeout=7)
        except RuntimeError as error:
            reason = str(error)
            # These checks run before the game's inventory transaction mutates anything.
            if reason in {"native_enemy_loot_out_of_reach", "native_enemy_loot_focus_obstructed",
                          "native_enemy_loot_raw_actor_present", "native_enemy_loot_claim_unavailable",
                          "native_enemy_loot_claimant_unavailable", "native_loot_exact_claimant_unavailable",
                          "native_loot_escrow_item_unavailable", "native_loot_claimant_backpack_full"}:
                host.native_checkpoint_unsafe = False
                host.control.event("native_enemy_loot_claim_refused", operation_id=operation, reason=reason)
                raise party.RuleError(reason) from error
            raise
        proof = snapshot.get("native_enemy_loot_escrow", {})
        if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("slot"), proof.get("guid"))
                != ("hosting", area, area, pawn, frame["slot"], guid)
                or proof.get("state") not in ("claimed", "already_claimed", "merged")):
            raise RuntimeError("The typed stock enemy claim did not match its exact participant")
        merge = (native_loot.verified_stack_merge(proof.get("merge", {}), guid, roll["quantity"])
                 if proof["state"] == "merged" else {})
        if deferred:
            candidate = native_loot.copy_pending_loot(host.party, STATE_KEY, character=character)
            candidate[STATE_KEY][guid] = roll.copy()
        else:
            candidate = party.load_party(host.party)
        committed = candidate[STATE_KEY][guid]
        committed["claimed_by"] = character
        committed.pop("escrow")
        report = {**_public_roll(committed), **merge}
        result = {"native_enemy_loot_claim": report}
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await _commit(host, bridge, candidate, snapshot, deferred=deferred,
                                 require_guest_frames=frozenset({character} - {"host"}), typed_inventory=bool(merge))
        host.control.event("native_enemy_loot_claim_committed", revision=revision, operation_id=operation, **report)
        native_pickup_audio.committed(host, pawn, operation, committed["class_path"])
        return result
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
