"""Settle stock and personal consumable use in an authenticated guest's own save."""

import asyncio
from collections import deque
from dataclasses import dataclass
import math
import re
import time
import uuid

from . import party
from .local_bridge import LocalCommandNotSubmitted
from .native_gold import valid_guid
from .native_host_dig import PACKAGE_CLASS as MISSING_CONTACT_CLASS


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
MAIL_PACKAGE = "native-guest-mail-package"
MISSING_CONTACT = "native-guest-missing-contact-package"
MISSING_CONTACT_ENTRY = "use-missing-contact-package"
MISSING_CONTACT_QUEST = "Note_Farmhouse"
MAIL_CLASSES = frozenset({
    "/Game/Classes/Items/Package_Mail.Package_Mail_C",
    "/Game/Classes/Items/Package_Mail_Small.Package_Mail_Small_C",
})
PERSONAL = "native-guest-personal-consumable"
COOKED_MEAL = "native-guest-cooked-meal"
PERSONAL_KEY = "native_guest_personal_consumable"
PERSONAL_ENTRY = "use-personal-consumable"
PERSONAL_PREVIEW = "native-guest-personal-consumable-preview"
PERSONAL_PREVIEW_KEY = "native_guest_personal_consumable_preview"
PERSONAL_PREVIEW_COMMAND = "native-inventory-stock-personal-preview"
PERSONAL_VITALS = ("food", "health", "regen", "stim", "fatigue", "intox", "sickness")
_PERSONAL_LIMITS = (200, 1_000_000, 1_000_000, 1_000_000, 100, 100)
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z", re.ASCII)
_GENERATED_CLASS = re.compile(r"/[A-Za-z0-9_/-]+\.[A-Za-z0-9_-]+_C\Z")
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?\Z", re.ASCII)
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
    fatigue: int = 0
    instant: float | None = None
    stim: float = 0
    antibiotic: bool = False


MEALS = {
    MRE: Meal(MRE_CLASS, 30, 10, 25),
    CANNED_PEACHES: Meal(CANNED_PEACHES_CLASS, 12, 0, 8),
    CANNED_BEANS: Meal(CANNED_BEANS_CLASS, 14, 0, 14),
    BROWN_MUSHROOM: Meal(BROWN_MUSHROOM_CLASS, 3.5, 0, 1),
    "native-guest-red-mushroom": Meal("/Game/Classes/Items/Item_RedMushroom.Item_RedMushroom_C", 3, -50, 3, 60),
    "native-guest-honey-egg": Meal("/Game/Classes/Items/Item_HoneyEgg.Item_HoneyEgg_C", 5, 0, 6, fatigue=5),
    **{
        f"native-guest-{kind}": Meal(f"/Game/Classes/Items/Item_{asset}.Item_{asset}_C", food, 0, value, infection)
        for kind, asset, food, value, infection in (
            ("canned-beef", "CannedBeef", 16, 30, 8),
            ("leftovers", "Leftovers", 18, 5, 0),
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
MEDICINE = {
    f"native-guest-{kind}": Meal(f"/Game/Classes/Items/Item_{asset}.Item_{asset}_C", 0, gain, value, infection,
                                 instant=instant, stim=stim, antibiotic=kind == "antibiotics")
    for kind, asset, gain, value, infection, instant, stim in (
        ("dirty-bandage", "DirtyBandage", 45, 3, 5, 0, 0),
        ("gauze", "Gauze", 60, 30, 0, 25, 0),
        ("medical-salve", "MedSalve", 30, 40, -5, 20, 3),
        ("large-medkit", "MedKit_Large", 100, 100, -5, 50, 4),
        ("stim-pills", "StimPills", 0, 15, -5, 0, 4),
        ("antibiotics", "Antibiotics", 0, 100, -100, 0, 0),
    )
}
STOCK_CONSUMABLES = MEALS | MEDICINE
COMMANDS = {BANDAGE_CLASS: ACTION, **{profile.class_path: action for action, profile in STOCK_CONSUMABLES.items()},
            **dict.fromkeys(STARTER_BAGS.values(), STARTER_BAG), **dict.fromkeys(MAIL_CLASSES, MAIL_PACKAGE)}
RESULT = {**{action: action.replace("-", "_") for action in COMMANDS.values()}, PERSONAL: PERSONAL_KEY,
          MISSING_CONTACT: MISSING_CONTACT.replace("-", "_")}


def _personal_identity(guid, item_class, quantity):
    if (not valid_guid(guid) or not isinstance(item_class, str) or len(item_class) > 256
            or _GENERATED_CLASS.fullmatch(item_class) is None or item_class in COMMANDS
            or not isinstance(quantity, str) or not 1 <= len(quantity) <= 10
            or not quantity.isascii() or not quantity.isdecimal()
            or not 1 <= int(quantity) < 2**31 or str(int(quantity)) != quantity):
        raise ValueError("Invalid personal consumable item")
    return int(quantity)


def _personal_values(values, *, delta):
    if (len(values) not in (4, 6, 7)
            or any(not 1 <= len(value) <= 25 or _DECIMAL.fullmatch(value) is None for value in values)
            or any(_INTEGER.fullmatch(value) is None for value in values[4:])):
        raise ValueError("Invalid personal consumable vitals")
    values = [*values, *(["0"] * (len(PERSONAL_VITALS) - len(values)))]
    parsed = (*map(float, values[:4]), *map(int, values[4:]))
    if any(not math.isfinite(value) or not (-limit if delta else 0) <= value <= limit
           for value, limit in zip(parsed[:-1], _PERSONAL_LIMITS, strict=True)):
        raise ValueError("Personal consumable vitals exceeded their bounds")
    minimum, maximum = (-120, 120) if delta else (-2**31, 2**31 - 1)
    if not minimum <= parsed[-1] <= maximum:
        raise ValueError("Personal consumable vitals exceeded their bounds")
    if not delta and parsed[1] <= 0:
        raise ValueError("Personal consumable health is not positive")
    return parsed


def parse_personal(fields, *, preview=False):
    if preview:
        if len(fields) not in (11, 13, 14) or fields[0] != PERSONAL_PREVIEW:
            raise ValueError("Invalid personal consumable preview")
        _, area, pawn, guid, item_class, before, after, *values = fields
    else:
        if len(fields) not in (12, 14, 15) or fields[:2] != [PERSONAL, "used"]:
            raise ValueError("Invalid personal consumable receipt")
        _, _, area, pawn, guid, item_class, before, after, *values = fields
    count = _personal_identity(guid, item_class, before)
    if len(pawn) > 512 or not pawn.startswith("Player_FP_C " + area + ".") or after != str(count - 1):
        raise ValueError("The exact personal consumable did not debit one owned unit")
    return {"state": "previewed" if preview else "used", "map": area, "pawn": pawn,
            "guid": guid, "class_path": item_class, "quantity_before": count, "quantity_after": count - 1,
            "deltas" if preview else "vitals": dict(zip(PERSONAL_VITALS, _personal_values(values, delta=preview), strict=True))}


def personal_target(epoch, preview):
    deltas = ",".join(format(preview["deltas"][key], ".17g") for key in PERSONAL_VITALS)
    return f'{epoch}@{preview["guid"]}|{preview["class_path"]}|{preview["quantity_before"]}|{deltas}'


async def settle_personal_preview(client, area, epoch, operation, guid, sequence):
    game = client.last_game
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    session = client.host_session
    snapshot = await client.bridge.command(PERSONAL_PREVIEW_COMMAND, epoch, str(sequence), timeout=4)
    preview = snapshot.get(PERSONAL_PREVIEW_KEY, {})
    if (not session or not pawn or client.host_session != session or client.zone_epoch != epoch
            or not client.transport.game_access or client.suspended or client.needs_travel or client.server_transitioning
            or (snapshot.get("state"), snapshot.get("map"), snapshot.get("pawns", {}).get("local", {}).get("name"))
                != ("connected", area, pawn)
            or tuple(preview.get(field) for field in ("state", "map", "pawn", "guid")) != ("previewed", area, pawn, guid)):
        raise ValueError("The personal consumable preview is no longer current")
    result = await client.gameplay_action(PERSONAL, operation, personal_target(epoch, preview))
    receipt = result[PERSONAL_KEY]
    if ((receipt.get("owner"), receipt.get("epoch")) != (client.invite["character_id"], epoch)
            or any(receipt.get(field) != preview[field] for field in ("class_path", "quantity_before", "quantity_after"))):
        raise ValueError("The host did not verify the exact personal consumable")
    return receipt


def _cooking_effect(value):
    food, health, regen, fatigue, intox = value.split(",")
    effect = (float(food), float(health), float(regen), int(fatigue), int(intox))
    if not any(effect) or not all(low <= amount <= high for amount, (low, high) in zip(
            effect, ((-200, 200), (0, 1000), (0, 1000), (-100, 0), (-100, 0)), strict=True)):
        raise ValueError("Invalid cooked meal effect")
    return effect


class GuestCookedMeals:
    def __init__(self):
        self.scope = None
        self.pending = deque()
        self.request = None
        self.last_sequence = 0
        self.retry_after = 0.0
        self.health_seen = None

    async def _cancel_request(self):
        if self.request is not None:
            request, self.request = self.request, None
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)

    async def close(self):
        await self._cancel_request()
        self.scope = None
        self.pending.clear()
        self.last_sequence = 0
        self.retry_after = 0.0
        self.health_seen = None

    def _collect(self, client, scope):
        session, epoch, area, pawn = scope
        outcomes = {}
        for raw in client.bridge.native_cooked_meals():
            try:
                label, serial, reported_area, owner, effect = raw.decode().rstrip("\n").split("\t")
                sequence = int(serial)
                if (label != COOKED_MEAL or (reported_area, owner) != (area, pawn)
                        or not self.last_sequence < sequence < 2**53):
                    continue
                _cooking_effect(effect)
                outcomes.setdefault(sequence, (serial, effect))
            except ValueError as error:
                client.control.event("native_guest_cooked_meal_rejected", reason=str(error))
        for sequence, (serial, effect) in sorted(outcomes.items()):
            self.last_sequence = sequence
            if len(self.pending) >= 16:
                client.control.event("native_guest_cooked_meal_rejected", reason="cooked_meal_backlog_full")
                continue
            operation = uuid.uuid5(client.bridge.input_namespace, f"{session}:{epoch}:{COOKED_MEAL}:{serial}").hex
            self.pending.append((operation, f"{epoch}@{effect}"))

    @staticmethod
    async def _send(client, scope, operation, target):
        if client.suspended or not client.transport.game_access or client.stock_interaction_scope() != scope:
            raise OSError("game_connection_unavailable")
        return await client.gameplay_action(COOKED_MEAL, operation, target)

    async def sync(self, client, active):
        """Report local meals in order. Keep older host vitals off the acting guest."""
        scope = client.stock_interaction_scope() if active and not client.suspended and client.transport.game_access else None
        visit = client.host_session, client.zone_epoch, client.expected_zone
        if self.scope is not None and (self.scope[:3] != visit or scope is not None and self.scope != scope):
            await self.close()
            scope = client.stock_interaction_scope() if active and not client.suspended and client.transport.game_access else None
        if scope is None:
            await self._cancel_request()
            return bool(self.pending or self.health_seen is not None)
        self.scope = scope
        if self.request is not None and self.request.done():
            request, self.request = self.request, None
            operation, _ = self.pending[0]
            settled = True
            try:
                result = request.result()
            except OSError:
                # The host may already have applied the delta before the reply was lost.
                settled = False
                self.retry_after = time.monotonic() + .8
            except (RuntimeError, ValueError) as error:
                if str(error) in {"native_actions_busy", "game_connection_unavailable"}:
                    settled = False
                    self.retry_after = time.monotonic() + .8
                else:
                    client.control.event("native_guest_cooked_meal_rejected", operation_id=operation, reason=str(error))
            else:
                client.control.event("native_guest_cooked_meal", operation_id=operation, **result)
            if settled:
                self.pending.popleft()
                # A snapshot already requested could contain vitals from before the host applied the meal.
                await client.cancel_gameplay_snapshot()
                self.health_seen = client.gameplay_seen
                if client.stock_interaction_scope() != scope:
                    return True
                client.next_snapshot = 0.0
        self._collect(client, scope)
        if self.pending and self.request is None and time.monotonic() >= self.retry_after:
            self.request = asyncio.create_task(self._send(client, scope, *self.pending[0]))
            self.request.add_done_callback(lambda _: client.bridge.wake_guest_update())
        if self.health_seen is not None and client.gameplay_seen > self.health_seen:
            self.health_seen = None
        return bool(self.pending or self.health_seen is not None)


async def use_cooked_meal(host, bridge, character, target, operation):
    from .native_inventory import retain_guest_save

    if (not isinstance(target, str) or len(target) > 192
            or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError("invalid_cooked_meal")
    epoch, separator, effect = target.partition("@")
    try:
        amounts = _cooking_effect(effect)
    except ValueError as error:
        raise party.RuleError("invalid_cooked_meal") from error
    game = host.last_game
    if (not separator or epoch != host.zone_epoch or not host.native_guest or host.native_profile is None
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe
            or game.get("state") != "hosting" or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("guest_cooked_meal_unavailable")
    request = [COOKED_MEAL, target, None]
    outcome_epoch, outcomes = host.cooked_meal_outcomes
    if outcome_epoch != epoch:
        outcomes = {}
        host.cooked_meal_outcomes = epoch, outcomes
    key = character, operation
    if key in outcomes:
        original, outcome = outcomes[key]
        if original != target:
            raise party.RuleError("operation_id_conflict")
        if isinstance(outcome, str):
            raise RuntimeError(outcome)
        return outcome
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != request:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    player = host.party["players"].get(character)
    owner = host.native_recipients().get(character, {})
    pawn = owner.get("pawn") if owner.get("connected") else None
    if not player or player["downed"] or host.party["wipe"] is not None or not pawn or character in host.party["pending_spawns"]:
        raise party.RuleError("guest_cooked_meal_owner_unavailable")
    if not await retain_guest_save(host, bridge, character, pawn):
        raise party.RuleError("guest_cooked_meal_working_save_unavailable")
    if epoch != host.zone_epoch or host.native_recipients().get(character, {}).get("pawn") != pawn:
        raise party.RuleError("guest_cooked_meal_visit_changed")
    # If the local command executes and loses its reply, retrying the delta is unsafe.
    unknown = "guest_cooked_meal_outcome_unavailable"
    outcomes[key] = target, unknown
    try:
        snapshot = await bridge.command(COOKED_MEAL, pawn, *map(str, amounts), timeout=4)
        message = snapshot.get("ack", {}).get("message", "").split()
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != game.get("map")
                or len(message) != 7 or message[:2] != ["cooked", "meal"]):
            raise RuntimeError("The guest cooked meal could not be verified")
        food, health, regen, fatigue, intox = map(float, message[2:])
        if not (0 <= food <= 200 and 0 <= health <= 1_000_000 and 0 <= regen <= 1_000_000
                and 0 <= fatigue <= 100 and 0 <= intox <= 100):
            raise RuntimeError("The guest cooked meal has invalid readback")
    except LocalCommandNotSubmitted as error:
        del outcomes[key]
        raise party.RuleError("native_actions_busy") from error
    except (OSError, RuntimeError, ValueError) as error:
        if str(error) == "guest_cooked_meal_refused_scope":
            outcomes[key] = target, str(error)
            raise
        host.control.event("host_guest_cooked_meal_outcome_uncertain", operation_id=operation, reason=str(error))
        raise RuntimeError(unknown) from error
    host.last_game, host.last_seen = snapshot, time.monotonic()
    result = {"cooked_meal": dict(food=food, health=health, regen=regen, fatigue=int(fatigue), intox=int(intox))}
    outcomes[key] = target, result
    recent = host.party["receipts"].setdefault(character, [])
    recent.append({"id": operation, "request": request, "result": result})
    del recent[:-party.RECENT_ACTIONS]
    return result


async def use_personal(host, bridge, character, target, operation_id):
    from .native_inventory import retain_guest_save

    if not isinstance(target, str) or len(target) > 512 or not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_personal_consumable_request")
    epoch, separator, payload = target.partition("@")
    if (not separator or not party.REQUEST_ID.fullmatch(epoch) or epoch != host.zone_epoch):
        raise party.RuleError("personal_consumable_visit_changed")
    try:
        guid, item_class, count, raw_deltas = payload.split("|")
        before = _personal_identity(guid, item_class, count)
        deltas = _personal_values(raw_deltas.split(","), delta=True)
    except ValueError as error:
        raise party.RuleError("invalid_personal_consumable_request") from error
    game = host.last_game
    if (not host.native_guest or host.native_profile is None or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or game.get("state") != "hosting" or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_personal_consumable_unavailable")
    owner = host.native_recipients().get(character)
    player = host.party["players"].get(character)
    if not owner or not owner.get("connected") or not owner.get("pawn") or not player:
        raise party.RuleError("guest_not_active")

    fingerprint = [PERSONAL, target, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if player["downed"] or host.party["wipe"] is not None or character in host.party["pending_spawns"]:
        raise party.RuleError("guest_personal_consumable_refused_downed_or_respawning")
    area, pawn = game["map"], owner["pawn"]
    if not await retain_guest_save(host, bridge, character, pawn):
        raise party.RuleError("guest_personal_consumable_refused_working_save_unavailable")
    current_owner = host.native_recipients().get(character, {})
    if (epoch != host.zone_epoch or host.last_game.get("map") != area
            or not current_owner.get("connected") or current_owner.get("pawn") != pawn):
        raise party.RuleError("personal_consumable_visit_changed")

    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(PERSONAL, pawn, guid, item_class, count,
                                            *(format(value, ".17g") for value in deltas), timeout=6)
        except RuntimeError as error:
            if str(error).startswith("guest_personal_consumable_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(PERSONAL_KEY, {})
        if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", area)
                or tuple(proof.get(key) for key in ("state", "map", "pawn", "guid", "class_path",
                                                    "quantity_before", "quantity_after"))
                != ("used", area, pawn, guid, item_class, before, before - 1)
                or not isinstance(proof.get("vitals"), dict) or proof["vitals"].keys() != set(PERSONAL_VITALS)):
            raise RuntimeError("The host did not verify one exact personal consumable debit")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        candidate = party.load_party(host.party)
        if before == 1:
            candidate["players"][character].get("native_inventory", {}).pop(guid, None)
        result = {PERSONAL_KEY: dict(proof, owner=character, epoch=epoch)}
        recent = candidate["receipts"].setdefault(character, [])
        recent.append({"id": operation_id, "request": fingerprint, "result": result})
        del recent[:-party.RECENT_ACTIONS]
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    if before == 1:
        host.native_pickup.forget_committed_row(character, guid)
    host.control.event("native_guest_personal_consumable_accepted", character_id=character, operation_id=operation_id,
                       item_class=item_class, guid=guid, quantity=before - 1, vitals=proof["vitals"])
    return result


def parse_meal(fields: list[str]) -> dict:
    if len(fields) not in (17, 19, 22, 25, 27):
        raise ValueError("Invalid stock food report size")
    (tag, status, area, pawn, guid, initial, remaining, food_before, food_after,
     regen_before, regen_after, hp, maximum, intox_before, intox_after, host_intox, hemophilia, *food_traits) = fields
    meal = STOCK_CONSUMABLES.get(tag)
    if (meal is None or status != "used" or not valid_guid(guid) or len(pawn) > 512
            or not pawn.startswith("Player_FP_C " + area + ".") or hemophilia not in ("0", "1")):
        raise ValueError("Invalid stock food identity")
    count, after = int(initial), int(remaining)
    food, restored, regen, buffered, health, max_health = map(float, (food_before, food_after, regen_before, regen_after, hp, maximum))
    intox_start, intox_end, shared_intox = map(int, (intox_before, intox_after, host_intox))
    trait = hemophilia == "1"
    gourmand, healthy_eating = map(int, food_traits[:2]) if food_traits else (0, 0)
    effects = {}
    nominal_dose = meal.infection
    instant = (meal.instant or 0) * (0.6 if trait else 1)
    if meal.instant is not None:
        if len(food_traits) != (10 if meal.antibiotic else 8):
            raise ValueError("Missing stock medicine effect proof")
        hp, stim_before, stim_after = map(float, food_traits[5:8])
        if (hp != min(health + instant, max_health) or not math.isfinite(stim_before) or stim_before < 0
                or stim_after != max(stim_before, meal.stim)):
            raise ValueError("Invalid stock medicine effect proof")
        effects.update(health_after=hp, stim_before=stim_before, stim_after=stim_after)
        if meal.antibiotic:
            resistance_before, resistance_after = map(int, food_traits[8:])
            if not 0 <= resistance_before < resistance_after < 2**31 or resistance_after - resistance_before > 3:
                raise ValueError("Invalid stock antibiotic resistance proof")
            nominal_dose += resistance_before
            effects.update(antibiotic_resistance_before=resistance_before, antibiotic_resistance_after=resistance_after)
    infection = {}
    if len(food_traits) >= 5:
        sick_before, sick_after, dose = map(int, food_traits[2:5])
        if dose > 0:
            infection_matches = 1 <= sick_after - sick_before <= dose
        else:
            infection_matches = sick_after == sick_before + (dose if sick_before > 0 else 0)
        if (not all(-2**31 <= value < 2**31 for value in (sick_before, sick_after))
                or (dose != nominal_dose and (dose != 0 or nominal_dose < 0)) or not infection_matches):
            raise ValueError("Invalid stock food infection proof")
        infection = {"sickness_before": sick_before, "sickness_after": sick_after, "infection_dose": dose}
    elif meal.infection:
        raise ValueError("Missing stock food infection proof")
    fatigue = {}
    if meal.fatigue:
        if len(food_traits) != 8:
            raise ValueError("Missing stock food fatigue proof")
        before_fatigue, after_fatigue, minimum_fatigue = map(int, food_traits[5:])
        if (not 0 <= before_fatigue <= 100 or minimum_fatigue not in (0, 15)
                or after_fatigue != min(max(before_fatigue + meal.fatigue, minimum_fatigue), 100)):
            raise ValueError("Invalid stock food fatigue proof")
        fatigue = {"fatigue_before": before_fatigue, "fatigue_after": after_fatigue}
    elif meal.instant is None and len(food_traits) > 5:
        raise ValueError("Unexpected stock food fatigue proof")
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
            or not 0 <= regen <= max_health or buffered != min(max(min(regen + instant, max_health) + gain, 25), max_health)
            or not 0 <= intox_start <= 100 or intox_end != min(max(intox_start + intoxication_delta, 0), 100)
            or not 0 <= shared_intox <= 100):
        raise ValueError("Invalid stock food effect proof")
    return {"state": status, "map": area, "pawn": pawn, "guid": guid, "class_path": meal.class_path,
            "quantity_before": count, "quantity_after": after, "food_before": food, "food_after": restored,
            "regen_before": regen, "regen_after": buffered, "health_before": health, "health_after": health,
            "max_health": max_health, "hemophilia": trait, "gourmand": gourmand, "healthy_eating": healthy_eating,
            "intox_before": intox_start, "intox_after": intox_end,
            "host_intox_before": shared_intox, "host_intox_after": shared_intox, **infection, **fatigue, **effects}


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


def parse_mail_package(fields: list[str]) -> dict:
    if len(fields) != 6:
        raise ValueError("Invalid stock mail package report size")
    action, state, area, pawn, guid, item_class = fields
    if (action != MAIL_PACKAGE or state != "used" or item_class not in MAIL_CLASSES or not valid_guid(guid)
            or len(pawn) > 512 or not pawn.startswith("Player_FP_C " + area + ".")):
        raise ValueError("Invalid stock mail package receipt")
    return {"state": state, "map": area, "pawn": pawn, "guid": guid, "class_path": item_class,
            "quantity_before": 1, "quantity_after": 0}


def parse_missing_contact_package(fields: list[str]) -> dict:
    if len(fields) != 11:
        raise ValueError("Invalid Missing Contact package report size")
    action, state, area, pawn, guid, awarded, previous, *balances = fields
    awards = awarded.split(",")
    if (action != MISSING_CONTACT or state != "used" or not valid_guid(guid) or len(pawn) > 512
            or not pawn.startswith("Player_FP_C " + area + ".") or len(awards) != 2 or not starter_awards(guid, awards)
            or previous != "-" and previous not in ("0", "1", "2", "-7", "-10")
            or any(len(value) > 11 or _INTEGER.fullmatch(value) is None for value in balances)):
        raise ValueError("Invalid Missing Contact package receipt")
    cash_before, cash, rep_before, rep = map(int, balances)
    if (any(not -2**31 <= value < 2**31 for value in (cash_before, cash, rep_before, rep))
            or cash > cash_before or (rep != rep_before if previous == "-" else rep >= rep_before)):
        raise ValueError("Invalid Missing Contact failure balances")
    return {"state": state, "map": area, "pawn": pawn, "guid": guid, "class_path": MISSING_CONTACT_CLASS,
            "quantity_before": 1, "quantity_after": 0, "awarded_guids": awards,
            "step_before": None if previous == "-" else int(previous), "step": -7, "xp": 1,
            "wallet_before": cash_before, "wallet": cash, "reputation_before": rep_before, "reputation": rep}


async def use(host, bridge, character: str, target: str, operation_id: str, *, action: str = ACTION) -> dict:
    from .native_inventory import retain_guest_save
    from . import native_host_dig_live, native_rewards

    if action not in RESULT:
        raise party.RuleError("native_guest_consumable_unavailable")
    key = RESULT[action]
    kind = key.removeprefix("native_guest_")
    meal = STOCK_CONSUMABLES.get(action)
    starter = action == STARTER_BAG
    mail = action == MAIL_PACKAGE
    sealed = action == MISSING_CONTACT
    guid, separator, confirmed = target.partition("|") if sealed and isinstance(target, str) else (target, "", "")
    if not valid_guid(guid) or sealed and (not separator or confirmed not in ("0", "1")):
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
    fingerprint = [action, target, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if player["downed"] or host.party["wipe"] is not None or character in host.party["pending_spawns"]:
        raise party.RuleError(f"guest_{kind}_refused_downed_or_respawning")
    if starter and host.last_game.get("native_paired_save_capture") is not True:
        raise party.RuleError("guest_starter_bag_refused_checkpoint")
    if sealed and not native_host_dig_live.may_open_package(host.party, guid, character):
        raise party.RuleError("guest_missing_contact_package_refused_custody")
    area, pawn = host.last_game["map"], owner["pawn"]
    deferred = await retain_guest_save(host, bridge, character, pawn)
    if not deferred:
        if action in MEDICINE or mail or sealed:
            raise party.RuleError(f"guest_{kind}_refused_working_save_unavailable")
        await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        try:
            arguments = (guid, confirmed) if sealed else (guid,)
            snapshot = await bridge.command(action, pawn, *arguments, timeout=6)
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
        elif mail:
            if before != 1 or proof.get("class_path") not in MAIL_CLASSES:
                raise RuntimeError("The native mail package did not verify its stock class")
        elif sealed:
            if (before != 1 or proof.get("class_path") != MISSING_CONTACT_CLASS
                    or not starter_awards(guid, proof.get("awarded_guids")) or proof.get("step") != -7
                    or snapshot.get("native_wallet") != proof.get("wallet")):
                raise RuntimeError("The Missing Contact package and shared failure were not verified")
        elif (any(type(value) not in (int, float) for value in (hp, regen, maximum, proof.get("regen_before"), proof.get("health_after")))
                or not 0 < hp <= maximum or not 25 <= regen <= maximum or not 0 <= proof["regen_before"] <= maximum
                or type(proof.get("hemophilia")) is not bool
                or proof["health_after"] != min(hp + ((meal and meal.instant) or 0) * (0.6 if proof["hemophilia"] else 1), maximum)):
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
        if sealed:
            native_host_dig_live.opened_package(candidate, guid, character)
            recipients = sorted(set(host.roles().values()) & candidate["players"].keys())
            for recipient in recipients:
                member = candidate["players"][recipient]
                member["quest_xp"] = member.get("quest_xp", 0) + proof["xp"]
                if recipient != "host":
                    native_rewards.queue(member, proof["xp"])
            candidate["economy"].update(wallet=proof["wallet"], native_wallet_active=True)
            candidate.setdefault("native_quests", {})[MISSING_CONTACT_QUEST] = {
                "quest_id": MISSING_CONTACT_QUEST, "actor": character, "step": proof["step"],
                "consumed_package": guid, "wallet": proof["wallet"], "reputation": {"8": proof["reputation"]},
                "recipients": recipients, "xp": proof["xp"],
            }
            host.pending_host_save = bool(host.pending_host_save)
        result = {key: proof}
        recent = candidate["receipts"].setdefault(character, [])
        recent.append({"id": operation_id, "request": fingerprint, "result": result})
        del recent[:-party.RECENT_ACTIONS]
        capture = {"typed_inventory": True} if starter else {}
        revision = None
        if not deferred:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}), **capture)
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party = candidate
    if meal is None or after == 0:
        host.native_pickup.forget_committed_row(character, guid)
    host.native_checkpoint_unsafe = False
    details = {"awarded_guids": proof["awarded_guids"]} if starter or sealed else {} if mail else {"regen": regen}
    if meal is not None:
        details.update(food=proof["food_after"], intoxication=proof["intox_after"])
    host.control.event(f"{key}_{'accepted' if deferred else 'committed'}", character_id=character, operation_id=operation_id,
                       guid=guid, revision=revision, quantity=after, **details)
    return result
