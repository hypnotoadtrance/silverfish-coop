"""Consume an exact supported item from the authenticated guest's own save."""

import asyncio
from dataclasses import dataclass
import math
import time

from . import party
from .native_gold import valid_guid


ACTION = "native-guest-bandage"
BANDAGE_CLASS = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
MRE = "native-guest-mre"
MRE_CLASS = "/Game/Classes/Items/Item_MRE.Item_MRE_C"
CANNED_PEACHES = "native-guest-canned-peaches"
CANNED_PEACHES_CLASS = "/Game/Classes/Items/Item_CannedPeaches.Item_CannedPeaches_C"
CANNED_BEANS = "native-guest-canned-beans"
CANNED_BEANS_CLASS = "/Game/Classes/Items/Item_CannedBeans.Item_CannedBeans_C"
BROWN_MUSHROOM = "native-guest-brown-mushroom"
BROWN_MUSHROOM_CLASS = "/Game/Classes/Items/Item_BrownMushroom.Item_BrownMushroom_C"
STARTER_BAG = "native-guest-starter-bag"
STARTER_BAGS = {
    "random": "/Game/Classes/Items/Random_GearBag_Starter.Random_GearBag_Starter_C",
    "hyperrandom": "/Game/Classes/Items/HyperRandom_GearBag_Starter.HyperRandom_GearBag_Starter_C",
}


@dataclass(frozen=True)
class Meal:
    class_path: str
    food: float
    gain: int
    unit_value: int
    infection: int = 0


MEALS = {
    MRE: Meal(MRE_CLASS, 30, 10, 25),
    CANNED_PEACHES: Meal(CANNED_PEACHES_CLASS, 12, 0, 8),
    CANNED_BEANS: Meal(CANNED_BEANS_CLASS, 14, 0, 14),
    BROWN_MUSHROOM: Meal(BROWN_MUSHROOM_CLASS, 3.5, 0, 1),
    **{
        f"native-guest-{kind}": Meal(f"/Game/Classes/Items/Item_{asset}.Item_{asset}_C", food, 0, value, infection)
        for kind, asset, food, value, infection in (
            ("canned-beef", "CannedBeef", 16, 30, 8),
            ("canned-soup", "CannedSoup", 10, 12, 3),
            ("canned-tuna", "CannedTuna", 6, 9, 4),
            ("dead-beetle", "DeadBeetle", 3, 2, 3),
            ("dog-meat", "DogMeat", 5, 3, 40),
            ("milk-bites", "MilkBites", 4, 3, 0),
            ("pork-ribs", "PorkRibs", 22, 70, 25),
            ("sausage", "Sausage", 10, 7, 20),
            ("venison", "Venison", 13, 40, 25),
        )
    },
}
COMMANDS = {BANDAGE_CLASS: ACTION, **{profile.class_path: action for action, profile in MEALS.items()},
            **dict.fromkeys(STARTER_BAGS.values(), STARTER_BAG)}
RESULT = {action: action.replace("-", "_") for action in COMMANDS.values()}


def parse_meal(fields: list[str]) -> dict:
    if len(fields) not in (17, 19, 22):
        raise ValueError("Invalid stock food report size")
    (tag, status, area, pawn, guid, initial, remaining, food_before, food_after,
     regen_before, regen_after, hp, maximum, intox_before, intox_after, host_intox, hemophilia, *food_traits) = fields
    meal = MEALS.get(tag)
    if (meal is None or status != "used" or not valid_guid(guid) or len(pawn) > 512
            or not pawn.startswith("Player_FP_C " + area + ".") or hemophilia not in ("0", "1")):
        raise ValueError("Invalid stock food identity")
    count, after = int(initial), int(remaining)
    food, restored, regen, buffered, health, max_health = map(float, (food_before, food_after, regen_before, regen_after, hp, maximum))
    intox_start, intox_end, shared_intox = map(int, (intox_before, intox_after, host_intox))
    trait = hemophilia == "1"
    gourmand, healthy_eating = map(int, food_traits[:2]) if food_traits else (0, 0)
    infection = {}
    if len(food_traits) == 5:
        sick_before, sick_after, dose = map(int, food_traits[2:])
        if (not all(-2**31 <= value < 2**31 for value in (sick_before, sick_after))
                or dose not in (0, meal.infection)
                or (not 1 <= sick_after - sick_before <= dose if dose else sick_before != sick_after)):
            raise ValueError("Invalid stock food infection proof")
        infection = {"sickness_before": sick_before, "sickness_after": sick_after, "infection_dose": dose}
    elif meal.infection:
        raise ValueError("Missing stock food infection proof")
    if not (0 <= gourmand <= 1025 and 0 <= healthy_eating <= 1025):
        raise ValueError("Invalid stock food traits")
    appetite = meal.food * (1 - gourmand * 0.2)
    gain = meal.gain + healthy_eating * appetite * 0.3
    if trait and gain > 0:
        gain *= 0.6
    intoxication_delta = math.trunc(appetite * -0.1)
    if (not 1 <= count <= (2**31 - 1) // meal.unit_value or after != count - 1
            or not all(math.isfinite(value) for value in (food, restored, regen, buffered, health, max_health))
            or not 25 <= max_health <= 10_000 or not 0 < health <= max_health
            or not 0 <= food <= 200 or restored != min(max(food + appetite, 0), 200)
            or not 0 <= regen <= max_health or buffered != min(max(regen + gain, 25), max_health)
            or not 0 <= intox_start <= 100 or intox_end != min(max(intox_start + intoxication_delta, 0), 100)
            or not 0 <= shared_intox <= 100):
        raise ValueError("Invalid stock food effect proof")
    return {"state": status, "map": area, "pawn": pawn, "guid": guid, "class_path": meal.class_path,
            "quantity_before": count, "quantity_after": after, "food_before": food, "food_after": restored,
            "regen_before": regen, "regen_after": buffered, "health_before": health, "health_after": health,
            "max_health": max_health, "hemophilia": trait, "gourmand": gourmand, "healthy_eating": healthy_eating,
            "intox_before": intox_start, "intox_after": intox_end,
            "host_intox_before": shared_intox, "host_intox_after": shared_intox, **infection}


def starter_awards(guid, awards) -> bool:
    return (type(awards) is list and 1 <= len(awards) <= 16 and all(valid_guid(award) and award != guid for award in awards)
            and len(set(awards)) == len(awards))


def parse_starter_bag(fields: list[str]) -> dict:
    if len(fields) != 7:
        raise ValueError("Invalid stock starter bag report size")
    tag, status, area, pawn, guid, kind, serialized = fields
    if not 32 <= len(serialized) <= 527:
        raise ValueError("Invalid stock starter bag awards size")
    awards = serialized.split(",")
    if (tag != STARTER_BAG or status != "used" or not valid_guid(guid) or kind not in STARTER_BAGS
            or len(pawn) > 512 or not pawn.startswith("Player_FP_C " + area + ".") or not starter_awards(guid, awards)):
        raise ValueError("Invalid stock starter bag proof")
    return {"state": status, "map": area, "pawn": pawn, "guid": guid, "class_path": STARTER_BAGS[kind],
            "quantity_before": 1, "quantity_after": 0, "awarded_guids": awards}


async def use(host, bridge, character: str, guid: str, operation_id: str, *, action: str = ACTION) -> dict:
    if action not in RESULT:
        raise party.RuleError("native_guest_consumable_unavailable")
    key = RESULT[action]
    kind = key.removeprefix("native_guest_")
    meal = MEALS.get(action)
    starter = action == STARTER_BAG
    if not valid_guid(guid):
        raise party.RuleError(f"invalid_{kind}_guid")
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")
    if (not host.native_guest or host.native_profile is None or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or time.monotonic() - host.last_seen > 3 or meal is not None and not host.guest_mre):
        raise party.RuleError(f"native_guest_{kind}_unavailable")
    owner = host.native_recipients().get(character)
    player = host.party["players"].get(character)
    if not owner or not owner["connected"] or not player:
        raise party.RuleError("guest_not_active")
    fingerprint = [action, guid, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if player["downed"] or host.party["wipe"] is not None or character in host.party["pending_spawns"]:
        raise party.RuleError(f"guest_{kind}_refused_downed_or_respawning")
    if starter and host.last_game.get("native_paired_save_capture") is not True:
        raise party.RuleError("guest_starter_bag_refused_checkpoint")
    area, pawn = host.last_game["map"], owner["pawn"]
    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(action, pawn, guid, timeout=6)
        except RuntimeError as error:
            if str(error).startswith(f"guest_{kind}_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(key, {})
        before, after, hp, regen, maximum = (proof.get(key) for key in (
            "quantity_before", "quantity_after", "health_before", "regen_after", "max_health"))
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != area
                or tuple(proof.get(key) for key in ("state", "map", "pawn", "guid")) != ("used", area, pawn, guid)
                or type(before) is not int or type(after) is not int or after < 0 or before - after != 1):
            raise RuntimeError("The native consumable did not verify the selected guest and one item debit")
        if starter:
            if (before != 1 or proof.get("class_path") not in STARTER_BAGS.values()
                    or not starter_awards(guid, proof.get("awarded_guids"))):
                raise RuntimeError("The native starter bag did not verify exact stock rewards")
        elif (any(type(value) not in (int, float) for value in (hp, regen, maximum, proof.get("regen_before")))
                or not 0 < hp <= maximum or not 25 <= regen <= maximum or not 0 <= proof["regen_before"] <= maximum
                or proof.get("health_after") != hp or type(proof.get("hemophilia")) is not bool):
            raise RuntimeError("The native consumable did not verify its exact quantity and stock regeneration")
        if action == ACTION and regen != min(max(proof["regen_before"] + (27 if proof["hemophilia"] else 45), 25), maximum):
            raise RuntimeError("The native Bandage did not apply the guest's stock regeneration effect")
        if meal is not None and (proof.get("class_path") != meal.class_path
                                 or not {"food_before", "food_after", "intox_before", "intox_after"}.issubset(proof)):
            raise RuntimeError("The native food did not verify its detached food and intoxication effects")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        candidate = party.load_party(host.party)
        if meal is None or after == 0:
            candidate["players"][character].get("native_inventory", {}).pop(guid, None)
        result = {key: proof}
        recent = candidate["receipts"].setdefault(character, [])
        recent.append({"id": operation_id, "request": fingerprint, "result": result})
        del recent[:-party.RECENT_ACTIONS]
        capture = {"typed_inventory": True} if starter else {}
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}), **capture)
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party = candidate
    if meal is None or after == 0:
        host.native_pickup.forget_committed_row(character, guid)
    host.native_checkpoint_unsafe = False
    details = {"awarded_guids": proof["awarded_guids"]} if starter else {"regen": regen}
    if meal is not None:
        details.update(food=proof["food_after"], intoxication=proof["intox_after"])
    host.control.event(f"{key}_committed", character_id=character, operation_id=operation_id,
                       guid=guid, revision=revision, quantity=after, **details)
    return result
