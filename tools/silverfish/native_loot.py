"""Commit and share host-authoritative rolls for qualified stock crates."""

import asyncio
import math
import time
import uuid

from . import native_host_save, party
from .authored_loot import CRATES, GUARANTEED_CRATES, LEGACY_CRATE, loot_capacity, valid_item_transform
from .native_gold import valid_guid


CRATE = LEGACY_CRATE
STATE_KEY = "native_loot_rolls"
RETIRED = "native_loot_retired_rolls"
REPLENISHED = "native_replenished_crates"
PLACEMENT = "native-loot-placement"
PLACEMENT_KEY = "native_loot_placement"
FORCED_ROLL_STATUS = "opened_fixture_seeded"
CASH_RANGES = {
    f"/Game/Classes/Items/Item_Money_{name}.Item_Money_{name}_C": limits
    for name, limits in (("CoupleBills", (6, 10)), ("SmallStack", (30, 50)),
                         ("LargeStack", (100, 200)), ("MassivePile", (3000, 5000)))
}


def public_roll(roll: dict) -> dict:
    return {key: value for key, value in roll.items() if key != "escrow"}


def _scope(host, character: str) -> str:
    if character == "host":
        return host.last_game.get("pawns", {}).get("local", {}).get("name", "")
    record = host.native_recipients().get(character, {})
    return record.get("pawn", "") if record.get("connected") else ""


def interaction(game: dict, last_sequence: int) -> tuple[int, str] | None:
    proof = game.get("native_loot_crate_interaction", {})
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    area, crate, sequence = game.get("map"), proof.get("crate"), proof.get("sequence")
    if (isinstance(crate, str) and crate in CRATES and CRATES[crate] == area and pawn
            and type(sequence) is int and last_sequence < sequence < 2**53
            and (proof.get("map"), proof.get("pawn")) == (area, pawn)
            and proof.get("entry") in ("stock", "fixture")):
        return sequence, crate


def _rolls_in_area(rolls: dict, area: str) -> dict:
    return {crate: roll for crate, map_name in CRATES.items() if map_name == area
            and (roll := rolls.get(crate)) and (roll.get("crate"), roll.get("map")) == (crate, area)}


def published(rolls: dict, area: str, *, game=None) -> dict:
    replenished = _replenished(game, rolls) if game is not None else {}
    return {crate: public_roll(roll) for crate, roll in _rolls_in_area(rolls, area).items()
            if crate not in replenished}


def _replenished(game, rolls):
    if game.get("state") != "hosting":
        return {}
    area = game.get("map")
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    observed = game.get(REPLENISHED, {})
    stock = game.get("native_loot_crates", {})
    return {crate: roll for crate, roll in _rolls_in_area(rolls, area).items()
            if pawn and observed.get(crate) == dict(map=area, crate=crate, pawn=pawn)
            and stock.get(crate, {}).get("state") == "ready"}


async def retire_replenished(host, bridge):
    if host.stopping or host.transitioning or host.native_checkpoint_unsafe or host.party.get("wipe"):
        return
    expired = _replenished(host.last_game, host.party.get(STATE_KEY, {}))
    if not expired:
        return
    candidate = party.load_party(host.party)
    retired = candidate.setdefault(RETIRED, {})
    for crate, roll in expired.items():
        if (not isinstance(roll.get("id"), str) or not party.REQUEST_ID.fullmatch(roll["id"])
                or roll["id"] in retired):
            raise RuntimeError("A replenished crate has an invalid or repeated roll identity")
        retired[roll["id"]] = crate
        del candidate[STATE_KEY][crate]
    revision = await host.checkpoint_native(bridge, candidate)
    host.party = candidate
    for crate, roll in expired.items():
        host.control.event("native_loot_roll_replenished", crate=crate, roll_id=roll["id"], revision=revision)


async def restore_legacy_placement(host, bridge) -> None:
    area = host.last_game.get("map")
    rolls = _rolls_in_area(host.party.get(STATE_KEY, {}), area)
    for crate, roll in rolls.items():
        if not any(not row.get("claimed_by") and "transform" not in row for row in roll["items"]):
            continue
        snapshot = await bridge.command(PLACEMENT, crate, timeout=3)
        report = snapshot.get(PLACEMENT_KEY, {})
        positions = report.get("positions", {})
        if ((snapshot.get("state"), snapshot.get("map"), report.get("map"), report.get("crate"))
                != ("hosting", area, area, crate)
                or set(positions) != set(range(1, loot_capacity(crate) + 1))
                or not all(valid_item_transform(value) for value in positions.values())
                or len({tuple(value["position"]) for value in positions.values()}) != len(positions)):
            raise RuntimeError("The stock crate did not report distinct positions for its exact spawner slots")
        candidate = party.load_party(host.party)
        items = candidate[STATE_KEY][crate]["items"]
        available = dict(sorted(positions.items()))
        for row in items:
            if "transform" in row:
                nearest = min(available, key=lambda slot: math.dist(
                    available[slot]["position"], row["transform"]["position"]))
                del available[nearest]
        for row in items:
            if "transform" not in row:
                row["transform"] = available.pop(min(available))
        host.last_game, host.last_seen = snapshot, time.monotonic()
        revision = await host.checkpoint_native(bridge, candidate)
        host.party = candidate
        host.control.event("native_loot_legacy_placement_restored", crate=crate, map=area, revision=revision)
        return


def default_crate(area: str) -> str | None:
    return next((crate for crate, zone in CRATES.items() if zone == area), None)


def fixture_selection(target: str | None, area: str) -> tuple[str, str | None]:
    if target is None:
        return "guest", default_crate(area)
    if not isinstance(target, str):
        return "", None
    role, separator, crate = target.partition("@")
    if separator:
        return role, crate
    if target in CRATES:
        return "guest", target
    return target, default_crate(area)

async def project_opened(bridge, game: dict, rolls: dict) -> dict:
    """Project saved open crates in this map without replaying stock rolls."""
    area = game.get("map")
    replenished = _replenished(game, rolls or {})
    for crate in _rolls_in_area(rolls or {}, area):
        if crate in replenished:
            continue
        scope = game.get("native_loot_crates", {}).get(crate, {})
        if (scope.get("map"), scope.get("crate"), scope.get("state")) != (area, crate, "ready"):
            continue
        snapshot = await bridge.command("native-loot-crate-project", "exhausted", crate, timeout=4)
        proof = snapshot.get("native_loot_crates", {}).get(crate, {})
        if snapshot.get("map") != area or (proof.get("map"), proof.get("crate"), proof.get("state")) != (
                area, crate, "exhausted"):
            raise RuntimeError("The exact stock crate did not reflect its committed roll")
        game = snapshot
    return game


class LootView:
    def __init__(self, bridge, control):
        self.bridge = bridge
        self.control = control
        self.event_offset = 0
        self.last_sequence = 0
        self.pending_event = []
        self.scope = None
        self.next_lease = 0.0
        self.claimed = set()
        self.previous_items = None

    def read_claim(self) -> list[str]:
        try:
            with (self.bridge.directory / "native-loot-view-events").open("rb") as stream:
                stream.seek(self.event_offset)
                data = stream.read(4096)
        except OSError:
            return []
        end = data.rfind(b"\n") + 1
        if not end:
            if len(data) == 4096:
                self.event_offset += len(data)
            return []
        self.event_offset += end
        return data[:end].splitlines()[-1].decode("utf-8", errors="replace").split("\t")

    async def fixture(self, action: str, target: str | None) -> dict:
        if not self.scope or target not in ("close", "previous", "next", "take"):
            raise ValueError("Select an input for the active staged crate view")
        return await self.bridge.command(action, *self.scope, target, timeout=4)

    async def sync(self, game: dict, rolls: dict | None, claim) -> dict:
        event = self.read_claim() or self.pending_event
        view = game.get("native_loot_view", {})
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        area = game.get("map")
        eligible = _rolls_in_area(rolls or {}, area)
        candidates = {}
        nearby = game.get("native_loot_view_candidates", {})
        for crate, roll in eligible.items():
            candidate = nearby.get(crate, {})
            distance = candidate.get("distance")
            if (pawn and (candidate.get("map"), candidate.get("crate"), candidate.get("pawn")) == (area, crate, pawn)
                    and type(distance) in (float, int) and 0 <= distance <= 1000
                    and party.REQUEST_ID.fullmatch(str(roll.get("id", "")))):
                candidates[crate] = distance
        if not candidates:
            self.scope = self.previous_items = None
            self.next_lease = 0.0
            self.claimed.clear()
            self.pending_event = []
            return game
        shown_crate = view.get("crate")
        if (isinstance(shown_crate, str) and shown_crate in candidates and view.get("pawn") == pawn
                and view.get("id") == eligible[shown_crate]["id"]
                and (view.get("state") in ("confirming", "claiming")
                     or tuple(event[2:5]) == (view["id"], pawn, shown_crate))):
            crate = shown_crate
        else:
            nonempty = [key for key in candidates if any(not row.get("claimed_by") for row in eligible[key]["items"])]
            crate = min(nonempty or candidates, key=lambda key: (candidates[key], key))
        roll = eligible[crate]
        scope = roll["id"], pawn, crate
        if scope != self.scope:
            self.scope = scope
            self.previous_items = None
            self.next_lease = 0.0
            self.claimed.clear()
        rows = {row["guid"]: row for row in roll["items"] if not row.get("claimed_by") and "transform" not in row}
        self.pending_event = []
        valid_event = (len(event) == 6 and event[0] == "crate-view" and tuple(event[2:5]) == scope
                       and event[1].isascii() and event[1].isdigit() and len(event[1]) <= 16
                       and self.last_sequence < (sequence := int(event[1])) < 2**53
                       and (guid := event[5]) in rows and guid not in self.claimed)
        if valid_event and tuple(view.get(key) for key in ("id", "pawn", "crate", "state", "sequence", "guid")) != (
                *scope, "claiming", sequence, guid):
            self.pending_event = event
        elif valid_event:
            self.last_sequence = sequence
            operation = uuid.uuid5(uuid.NAMESPACE_OID, f"{self.control.generation}:{scope}:{sequence}").hex
            try:
                result = await claim(operation, f"{guid}@{crate}")
                if (result["native_loot_claim"]["guid"] != guid
                        or result.get("native_loot_crate", LEGACY_CRATE) != crate):
                    raise RuntimeError("The crate response did not match the selected item")
                self.claimed.add(guid)
                self.control.event("native_stock_crate_item_taken", operation_id=operation, **result)
            except (KeyError, OSError, ValueError, RuntimeError) as error:
                self.control.event("native_stock_crate_item_rejected", operation_id=operation, reason=str(error))
                try:
                    game = await self.bridge.command("native-loot-view-result", *scope, "refresh", timeout=2)
                except (OSError, TimeoutError, RuntimeError):
                    pass
        fields = tuple(f"{guid}|{row['class_path']}" for guid, row in rows.items() if guid not in self.claimed)
        now = time.monotonic()
        if (fields or self.previous_items) and (now >= self.next_lease or fields != self.previous_items):
            self.next_lease = now + .75
            self.previous_items = fields
            try:
                return await self.bridge.command("native-loot-view-render", *scope, *fields, timeout=2)
            except (OSError, TimeoutError, RuntimeError) as error:
                if str(error) != "stock_crate_view_scope_unavailable":
                    self.control.event("native_stock_crate_view_error", reason=str(error))
        return game


def _verified_cash_credit(proof: dict, item: dict, balance: int | None) -> dict:
    credit = proof.get("cash", {})
    amount, before, after = (credit.get(key) for key in ("amount", "before", "after"))
    minimum, maximum = CASH_RANGES[item["class_path"]]
    if (proof.get("status") != "cash_credited" or credit.get("guid") != item["guid"]
            or any(type(value) is not int for value in (amount, before, after, balance))
            or not minimum <= amount <= maximum or not 0 <= before < after <= 2**31 - 1
            or after - before != amount or after != balance):
        raise RuntimeError("The exact stock crate cash credit did not verify")
    return {"amount": amount, "before": before, "after": after}


async def perform(host, bridge, character: str, action: str, target: str, operation_id: str, *,
                  fixture_nonempty: bool = False) -> dict:
    area = host.last_game.get("map")
    if (not host.native_zones or not (host.fixtures or host.native_loot_input) or host.native_profile is None
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or host.last_game.get("state") != "hosting" or area not in CRATES.values()
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_loot_scope_unavailable")
    if (action in ("approach", "seed") or fixture_nonempty) and not host.fixtures:
        raise party.RuleError("native_loot_fixture_unavailable")
    if (action not in ("claim", "inspect", "approach", "seed", "open") or not isinstance(target, str)
            or not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id)):
        raise party.RuleError("native_loot_requires_exact_crate_and_operation")
    pawn = _scope(host, character)
    if not pawn or character not in host.party["players"]:
        raise party.RuleError("native_loot_opener_unavailable")
    rolls = _rolls_in_area(host.party.get(STATE_KEY, {}), area)
    crate = target
    if action == "claim":
        guid, separator, crate = target.partition("@")
        if not valid_guid(guid):
            raise party.RuleError("native_loot_requires_exact_crate_and_operation")
        receipt = next((entry for entry in host.party["receipts"].get(character, []) if entry["id"] == operation_id), None)
        if receipt and not separator:
            request = receipt["request"]
            if len(request) != 3 or request[:2] != ["native-loot-claim", guid]:
                raise party.RuleError("operation_id_conflict")
            crate = request[2] or LEGACY_CRATE
        elif not separator:
            matches = [key for key, roll in rolls.items() if any(row.get("guid") == guid for row in roll["items"])]
            if len(matches) > 1:
                raise party.RuleError("native_loot_item_ambiguous")
            if not matches:
                raise party.RuleError("native_loot_item_unavailable")
            crate, = matches
    if not isinstance(crate, str) or crate not in CRATES or CRATES[crate] != area:
        raise party.RuleError("native_loot_requires_exact_crate_and_operation")
    retired = host.party.get(RETIRED, {})
    if action == "open" and operation_id in retired:
        raise party.RuleError("native_loot_roll_replenished" if retired[operation_id] == crate else "operation_id_conflict")
    if action == "open" and any(key != crate and roll.get("id") == operation_id
                                for key, roll in host.party.get(STATE_KEY, {}).items()):
        raise party.RuleError("operation_id_conflict")
    saved = rolls.get(crate)
    if saved and action in ("open", "inspect"):
        if crate in _replenished(host.last_game, rolls):
            raise party.RuleError("native_loot_replenishment_pending")
        return {"native_loot_roll": public_roll(saved)}
    if host.party["players"][character]["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]:
        raise party.RuleError("native_loot_opener_unavailable")

    arguments = ()
    if action == "claim":
        fingerprint = ["native-loot-claim", guid, crate]
        if receipt:
            legacy = crate == LEGACY_CRATE and receipt["request"] == ["native-loot-claim", guid, None]
            if receipt["request"] != fingerprint and not legacy:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
        item = next((row for row in saved.get("items", ()) if row.get("guid") == guid), None) if saved else None
        if not item or not saved.get("escrow") or crate in _replenished(host.last_game, rolls):
            raise party.RuleError("native_loot_item_unavailable")
        if item.get("claimed_by"):
            raise party.RuleError("native_loot_already_claimed")
        escrow = saved["escrow"]
        if escrow.get("map") != area:
            raise RuntimeError("The crate escrow belongs to a different map")
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, escrow)
        arguments = (guid, escrow["slot"])
        if (transform := item.get("transform")) is not None:
            arguments += tuple(str(axis) for axis in transform["position"])
    elif action == "open":
        arguments = (native_host_save.new_slot(), *(("fixture-nonempty",) if fixture_nonempty else ()))
    mutates = action in ("open", "seed", "claim")
    if mutates:
        host.native_checkpoint_unsafe = True
    try:
        snapshot = await bridge.command(f"native-loot-{action}", pawn, crate, *arguments, timeout=6)
        proof = snapshot.get("native_loot", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != area
                or (proof.get("action"), proof.get("map"), proof.get("pawn"), proof.get("crate"))
                != (action, area, pawn, crate)):
            raise RuntimeError("The native loot proof did not match the exact participant and stock crate")
        if not proof["ok"]:
            if mutates and proof["invoked"]:
                raise RuntimeError(proof["status"] or "mutation_unverified_native_loot")
            if mutates:
                host.native_checkpoint_unsafe = False
            raise party.RuleError(proof["status"] or "native_loot_refused")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if action == "open":
            items = proof["items"]
            calls = proof["trait_calls"]
            capacity = loot_capacity(crate)
            trait_verified = calls == 0 if crate in GUARANTEED_CRATES else 1 <= calls <= 1 + capacity
            if (not proof["invoked"] or not proof["looted"] or not trait_verified
                    or proof["item_count"] != len(items) or len(items) > capacity
                    or len({item["actor"] for item in items}) != len(items)
                    or any(not valid_guid(item.get("guid")) for item in items)
                    or len({item["guid"] for item in items}) != len(items)
                    or proof["escrow_slot"] != arguments[0]):
                raise RuntimeError("The exact stock crate roll did not verify")
            if fixture_nonempty and (proof["status"] != FORCED_ROLL_STATUS or not items or proof["trait_calls"] != 3):
                raise RuntimeError("The exact staged stock stream draw did not produce a retained item")
            frame = await asyncio.to_thread(native_host_save.record, host.native_profile, arguments[0], area,
                                            flush=True)
            roll = {"id": operation_id, "map": area, "crate": crate, "opener": character,
                    "lucky_looter": proof["lucky_looter"], "trait_source": proof["trait_source"],
                    "trait_calls": proof["trait_calls"], "items": items, "escrow": frame}
            candidate = party.load_party(host.party)
            candidate.setdefault(STATE_KEY, {})[crate] = roll
            revision = await host.checkpoint_native(bridge, candidate)
            host.party = candidate
            host.native_checkpoint_unsafe = False
            host.control.event("native_loot_roll_committed", revision=revision, **public_roll(roll))
            result = {"native_loot_roll": public_roll(roll)}
            if fixture_nonempty:
                result["native_loot_fixture"] = proof
            return result
        if action == "claim":
            if not proof["invoked"] or proof["escrow_slot"] != arguments[1]:
                raise RuntimeError("The exact stock crate item transfer did not verify")
            credit = None
            if item["class_path"] in CASH_RANGES:
                credit = _verified_cash_credit(proof, item, snapshot.get("native_wallet"))
            elif proof["status"] not in ("claimed", "already_claimed") or proof.get("cash"):
                raise RuntimeError("The exact stock crate item transfer did not verify")

            candidate = party.load_party(host.party)
            claimed = next(row for row in candidate[STATE_KEY][crate]["items"] if row["guid"] == guid)
            claimed["claimed_by"] = character
            if credit:
                candidate["economy"].update(wallet=credit["after"], native_wallet_active=True)
                claimed["wallet_credit"] = credit
            result = {"native_loot_claim": claimed, "native_loot_crate": crate}
            receipts = candidate["receipts"].setdefault(character, [])
            receipts.append({"id": operation_id, "request": fingerprint, "result": result})
            del receipts[:-party.RECENT_ACTIONS]
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}))
            host.party = candidate
            host.native_checkpoint_unsafe = False
            host.control.event("native_loot_claim_committed", revision=revision, **claimed)
            return result
        if action == "seed":
            if not proof["lucky_looter"]:
                raise RuntimeError("The exact stock guest Lucky Looter fixture did not verify")
            await host.checkpoint_native(bridge, require_guest_frames=frozenset({character} - {"host"}))
            host.native_checkpoint_unsafe = False
        return {"native_loot": proof}
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
