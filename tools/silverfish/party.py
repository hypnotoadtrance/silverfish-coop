"""Host-owned state for the first playable co-op mechanics."""

from __future__ import annotations

from copy import deepcopy
import math
import re

from . import wipe

BASE_MAX_HEALTH = 100
INTERACT_RANGE = 250
LOOT_ID = "playtest-toy"
ITEM_ID = "toy-robot"
QUEST_ITEM = "field-token"
QUEST_CASH = 10
OFFER_ID = "training-supplies"
OFFER_ITEM = "training-bandage"
OFFER_PRICE = 10
REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
RECENT_ACTIONS = 64
MAX_NATIVE_ITEMS = 24


class RuleError(ValueError):
    """An action was refused before any campaign state changed."""


def new_party() -> dict:
    return {
        "schema": 1,
        "players": {},
        "loot": None,
        "receipts": {},
        "economy": {"wallet": 0, "stock": 2, "quest_completed": False},
        "checkpoint": None,
        "wipe": None,
        "backpacks": {},
        "pending_spawns": {},
        "death_rules": {"keep_slots": None, "drop_backpacks": False},
    }


def load_party(saved: dict | None) -> dict:
    if saved is None:
        return new_party()
    if type(saved) is not dict or saved.get("schema") != 1:
        raise ValueError("Unsupported co-op gameplay state version")
    if not isinstance(saved.get("players"), dict) or not isinstance(saved.get("receipts"), dict):
        raise ValueError("Invalid co-op gameplay state")
    state = new_party()
    state.update(deepcopy(saved))
    return state


def add_player(party: dict, actor: str) -> bool:
    if actor in party["players"]:
        return False
    party["players"][actor] = {
        "health": BASE_MAX_HEALTH,
        "max_health": BASE_MAX_HEALTH,
        "downed": False,
        "inventory": {},
    }
    return True


def seed_loot(party: dict, zone: str, position: list[float]) -> None:
    if party["loot"] is None:
        party["loot"] = {
            "id": LOOT_ID, "item": ITEM_ID, "quantity": 1,
            "map": zone, "position": list(position), "claimed_by": None,
        }


def _nearby(actor: dict, target_map: str, position: list[float]) -> bool:
    return actor["map"] == target_map and math.dist(actor["location"], position) <= INTERACT_RANGE


def _position(pawns: dict, actor: str) -> dict:
    position = pawns.get(actor)
    if position is None:
        raise RuleError("pawn_unavailable")
    return position


def _interact(party: dict, actor: str, target: str | None, pawns: dict, loot_available: bool) -> dict:
    player = party["players"][actor]
    if player["downed"]:
        raise RuleError("actor_downed")
    origin = _position(pawns, actor)
    loot = party["loot"]

    if target is None:
        candidates = []
        for other, pawn in pawns.items():
            if (other != actor and party["players"].get(other, {}).get("downed")
                    and _nearby(origin, pawn["map"], pawn["location"])):
                candidates.append((math.dist(origin["location"], pawn["location"]), other))
        if candidates:
            target = min(candidates)[1]
        else:
            packs = [bag_id for bag_id, backpack in party["backpacks"].items()
                     if backpack["owner_id"] == actor and backpack["position"]
                     and _nearby(origin, backpack["position"]["map"], backpack["position"]["location"])]
            if packs:
                target = min(packs)
            elif (loot_available and loot and loot["claimed_by"] is None
                  and _nearby(origin, loot["map"], loot["position"])):
                target = loot["id"]
            else:
                raise RuleError("no_interaction_in_range")

    backpack = party["backpacks"].get(target)
    if backpack:
        if backpack["owner_id"] != actor:
            raise RuleError("backpack_not_owned")
        position = backpack["position"]
        if not position or not _nearby(origin, position["map"], position["location"]):
            raise RuleError("target_out_of_range")
        native = backpack.get("native_inventory", {})
        if native:
            owned = player.setdefault("native_inventory", {})
            if len(owned.keys() | native.keys()) > MAX_NATIVE_ITEMS:
                raise RuleError("native_inventory_full")
            owned.update(native)
        for item, quantity in backpack["inventory"].items():
            player["inventory"][item] = player["inventory"].get(item, 0) + quantity
        del party["backpacks"][target]
        result = {"effect": "recover_backpack", "target": target, "items": backpack["inventory"]}
        if native:
            result["native_items"] = list(native)
        return result

    if loot and target == loot["id"]:
        if loot["claimed_by"] is not None:
            raise RuleError("already_claimed")
        if not loot_available:
            raise RuleError("loot_unavailable")
        if not _nearby(origin, loot["map"], loot["position"]):
            raise RuleError("target_out_of_range")
        inventory = player["inventory"]
        item = loot["item"]
        inventory[item] = inventory.get(item, 0) + loot["quantity"]
        loot["claimed_by"] = actor
        return {"effect": "pickup", "target": target, "item": item, "quantity": loot["quantity"]}

    other = party["players"].get(target)
    if target == actor or other is None or not other["downed"]:
        raise RuleError("target_not_revivable")
    position = _position(pawns, target)
    if not _nearby(origin, position["map"], position["location"]):
        raise RuleError("target_out_of_range")
    other["downed"] = False
    other["health"] = max(1, other["max_health"] * 0.10)
    return {"effect": "revive", "target": target, "health": other["health"]}


def begin_when_all_down(party: dict, eligible: tuple[str, ...], pawns: dict, now: float) -> dict | None:
    checkpoint = party["checkpoint"] or pawns.get("host")
    if not checkpoint:
        return None
    return wipe.begin_wipe(party["players"], eligible, checkpoint, now,
                           **party["death_rules"], selection_seconds=30, pawns=pawns)


def transition(party: dict, actor: str, operation_id: str, action: str, target: str | None,
               pawns: dict, *, loot_available: bool = True, test_fixtures: bool = False,
               test_damage: int | None = None, eligible: tuple[str, ...] = (),
               retained: dict | None = None, now: float = 0) -> tuple[dict, dict]:
    """Return a tentative state and result. The caller commits before publishing."""
    if not isinstance(operation_id, str) or not REQUEST_ID.fullmatch(operation_id):
        raise RuleError("invalid_operation_id")
    if target is not None and (not isinstance(target, str) or len(target) > 64):
        raise RuleError("invalid_target")
    if actor not in party["players"] or (action != "keep" and actor not in pawns):
        raise RuleError("actor_unavailable")

    fingerprint = [action, target, test_damage]
    if action == "keep":
        fingerprint.append(retained)
    receipt = next(
        (entry for entry in party["receipts"].get(actor, []) if entry["id"] == operation_id), None,
    )
    if receipt:
        if receipt["request"] != fingerprint:
            raise RuleError("operation_id_conflict")
        return party, deepcopy(receipt["result"])

    if actor in party["pending_spawns"] and action != "keep":
        raise RuleError("respawn_in_progress")
    if party["wipe"] is not None and action != "keep":
        raise RuleError("wipe_in_progress")
    candidate = deepcopy(party)
    if action == "keep":
        if not party["wipe"]:
            raise RuleError("wipe_not_active")
        try:
            candidate["wipe"] = wipe.select_items(party["wipe"], actor, {} if retained is None else retained, now=now)
        except wipe.WipeError as error:
            raise RuleError(str(error)) from None
        result = {"effect": "wipe_items_selected", "target": actor}
    elif action == "rest" and actor == "host":
        if party["players"][actor]["downed"]:
            raise RuleError("actor_downed")
        position = pawns[actor]
        candidate["checkpoint"] = {"map": position["map"], "location": list(position["location"])}
        result = {"effect": "rest_checkpoint", "map": position["map"]}
    elif action == "interact":
        result = _interact(candidate, actor, target, pawns, loot_available)
    elif action == "buy" and test_fixtures:
        if target != OFFER_ID:
            raise RuleError("unknown_shop_offer")
        if candidate["players"][actor]["downed"]:
            raise RuleError("actor_downed")
        economy = candidate["economy"]
        if economy["stock"] == 0:
            raise RuleError("out_of_stock")
        if economy["wallet"] < OFFER_PRICE:
            raise RuleError("insufficient_funds")
        player_items = candidate["players"][actor]["inventory"]
        player_items[OFFER_ITEM] = player_items.get(OFFER_ITEM, 0) + 1
        economy["stock"] -= 1
        economy["wallet"] -= OFFER_PRICE
        result = {"effect": "purchase", "item": OFFER_ITEM, "quantity": 1, "wallet": economy["wallet"]}
    elif action == "test-quest-reward" and actor == "host" and test_fixtures:
        economy = candidate["economy"]
        if economy["quest_completed"]:
            raise RuleError("quest_already_completed")
        recipients = sorted(set(eligible) & set(candidate["players"]))
        for recipient in recipients:
            items = candidate["players"][recipient]["inventory"]
            items[QUEST_ITEM] = items.get(QUEST_ITEM, 0) + 1
        economy["quest_completed"] = True
        economy["wallet"] += QUEST_CASH
        result = {"effect": "quest_reward", "recipients": recipients, "wallet": economy["wallet"]}
    elif actor == "host" and (action == "native-down" or (action == "test-damage" and test_fixtures)):
        if type(test_damage) is not int or not 1 <= test_damage <= 1000:
            raise RuleError("invalid_damage")
        victim = candidate["players"].get(target)
        if victim is None or target not in pawns:
            raise RuleError("target_unavailable")
        if victim["downed"]:
            raise RuleError("target_already_downed")
        victim["health"] = 0 if action == "native-down" else max(0, victim["health"] - test_damage)
        victim["downed"] = victim["health"] == 0
        result = {"effect": "downed" if victim["downed"] else "damage", "target": target,
                  "health": victim["health"]}
        if victim["downed"]:
            candidate["wipe"] = begin_when_all_down(candidate, eligible, pawns, now)
            if candidate["wipe"]:
                result["effect"] = "party_wipe"
    else:
        raise RuleError("action_unavailable")

    receipts = candidate["receipts"].setdefault(actor, [])
    receipts.append({"id": operation_id, "request": fingerprint, "result": result})
    del receipts[:-RECENT_ACTIONS]
    return candidate, deepcopy(result)


def public_state(party: dict, roles: dict[str, str], *, test_fixtures: bool) -> dict:
    return {
        "schema": 1,
        "test_fixtures": test_fixtures,
        "players": {
            role: {"character_id": actor, **deepcopy(party["players"][actor])}
            for role, actor in roles.items() if actor in party["players"]
        },
        "loot": deepcopy(party["loot"]) if test_fixtures else None,
        "checkpoint": deepcopy(party["checkpoint"]),
        "wipe": ({
            key: deepcopy(party["wipe"][key]) for key in ("id", "roster", "deadline", "keep_slots", "choices")
        } if party["wipe"] else None),
        "backpacks": deepcopy(party["backpacks"]),
        "pending_spawns": deepcopy(party["pending_spawns"]),
        "economy": {
            **deepcopy(party["economy"]),
            "offer": {"id": OFFER_ID, "price": OFFER_PRICE, "item": OFFER_ITEM},
        } if test_fixtures else None,
    }
