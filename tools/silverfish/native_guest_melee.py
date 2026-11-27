"""Scope an authenticated guest's stock melee attacks and diagnostic fixtures."""

from dataclasses import dataclass
import math
import re
import time
import uuid

from . import native_authored_pickup, native_inventory, party
from .enemy_loot_profiles import BY_ACTOR, BY_MAP
from .local_bridge import LocalCommandNotSubmitted
from .native_gold import valid_guid
from .travel import CROSSROADS, DEAD_BRIDGE


EQUIP = "native-guest-melee-canary-equip"
CANARY = "native-guest-melee-canary"
DAMAGE = "native-guest-melee-damage-canary"
SWING = "native-guest-melee-swing"
INPUT = "native-guest-melee-input"
INPUT_RECORD = "native-guest-melee-edge"
INPUT_STATE = "native-guest-melee-input-state"
COMPACT_ACTIONS = frozenset((INPUT + "-start", INPUT + "-edge", SWING + "-start"))
REPLY_HEADER = "native-guest-melee-reply\t1\n"
REPLY_BYTES = "native_guest_melee_reply_bytes"
INPUT_KEY = "native_guest_melee_input_state"
INPUT_FIXTURE = "native-guest-melee-input-fixture"
INPUT_FIXTURE_KEY = "native_guest_melee_input_fixture"
AIM_FIXTURE = "native-guest-melee-aim-fixture"
CONTACT_FIXTURE = "native-guest-melee-contact-fixture"
KIND = "old-pipe"
KINDS = (KIND, "melee")
EQUIP_KEY = "native_guest_melee_equip"
CANARY_KEY = "native_guest_melee_canary"
SWING_KEY = "native_guest_melee_swing"
AIM_KEY = "native_guest_melee_aim_fixture"
CONTACT_KEY = "native_guest_melee_contact_fixture"
TARGET = BY_MAP[DEAD_BRIDGE].actors[0]
CONTACT_STAGES = frozenset({"awaiting_contact", "camera_identity", "camera_vector_abi", "hit_result_abi",
                            "line_call_abi", "sphere_call_abi", "trace_parameters_abi", "line_contact", "line_too_near",
                            "sphere_empty", "sphere_bounds", "sphere_contact", "sphere_other_actor",
                            "target_health", "target_identity", "selected_contact"})
EDGES = frozenset({"press", "release", "cancel"})
TAP = "tap"
MAX_TAP_MS = 120
TERMINAL = frozenset({"settled", "cancelled"})
DIRTY = "mutation_unverified_guest_melee_"


def registered_target(area: str, target: str) -> bool:
    profile = BY_ACTOR.get(target) if isinstance(target, str) else None
    return profile is not None and profile.map == area


def ordinary_map(area) -> bool:
    return isinstance(area, str) and re.fullmatch(r"/Game/Classes/Maps/(?:[\w-]+/)*[\w-]+", area, re.ASCII) is not None


def ordinary_target(area, target) -> bool:
    if not ordinary_map(area) or not isinstance(target, str) or len(target) > 512:
        return False
    level = area.rsplit("/", 1)[1]
    prefix = re.escape(f"{area}.{level}:PersistentLevel.")
    return re.fullmatch(rf"\w+ {prefix}\w+", target, re.ASCII) is not None


def pawn_in_map(area, pawn) -> bool:
    return (ordinary_map(area) and isinstance(pawn, str) and len(pawn) <= 512
            and pawn.startswith(f"Player_FP_C {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.Player_FP_C_")
            and ordinary_target(area, pawn))


def input_record(raw: bytes):
    try:
        marker, sequence, area, pawn, guid, kind, edge, stroke, duration = raw.decode("utf-8").split("\t")
    except (UnicodeError, ValueError):
        return None
    if (marker != INPUT_RECORD or not pawn_in_map(area, pawn) or not valid_guid(guid) or kind not in KINDS
            or edge not in EDGES or any(not (number.isascii() and number.isdecimal() and len(number) <= 16)
                                       for number in (sequence, stroke, duration))):
        return None
    sequence, stroke, duration = int(sequence), int(stroke), int(duration)
    if (not 0 < stroke <= sequence < 2**53 or (edge == "press") != (sequence == stroke)
            or not 0 <= duration <= 9000 or edge == "press" and duration != 0):
        return None
    return sequence, area, pawn, guid, kind, edge, stroke, duration


def parse_input_state(fields):
    if len(fields) != 12:
        return None
    _, area, pawn, guid, serial, status, presses, releases, contacts, hits, charged, combo = fields
    if (not pawn_in_map(area, pawn) or not valid_guid(guid)
            or (status not in {"pending", *TERMINAL} and not status.startswith(DIRTY))):
        return None
    try:
        serial, presses, releases, contacts, hits, charged, combo = map(
            int, (serial, presses, releases, contacts, hits, charged, combo))
    except ValueError:
        return None
    if (not 0 < serial < 2**53 or not 0 < presses < 2**31 or not 0 <= hits <= contacts <= presses
            or not 0 <= charged + combo <= contacts or min(charged, combo, releases) < 0
            or presses - releases not in (0, 1) or status in TERMINAL and presses != releases):
        return None
    return dict(map=area, pawn=pawn, guid=guid, serial=serial, status=status, presses=presses, releases=releases,
                contacts=contacts, hits=hits, charged=charged, combo=combo)


def verified_damage(proof: dict, *, fixture=False) -> bool:
    values = tuple(proof.get(key) for key in ("enemy_health_before", "enemy_health_after", "stock_damage"))
    if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
        return False
    before, after, amount = values
    counters = proof.get("sweeps"), proof.get("damage_calls")
    eligible = registered_target if fixture else ordinary_target
    return (eligible(proof.get("map"), proof.get("target"))
            and all(type(counter) is int and counter == 1 for counter in counters)
            and before > 0 and after < before and amount > 0
            and (not fixture or 100 <= before <= 160 and after > 0 and amount <= 90)
            and (math.isclose(before - after, amount, rel_tol=0, abs_tol=1e-6)
                 or not fixture and after == 0 and amount >= before - 1e-6))


def parse(fields: list[str]) -> tuple[str, dict] | None:
    if not fields:
        return None
    if fields[0] == "native-guest-melee-equip" and len(fields) == 6 and fields[4] in ("0", "1"):
        return EQUIP_KEY, dict(zip(("map", "pawn", "guid"), fields[1:4]), ok=fields[4] == "1", status=fields[5])
    if fields[0] == AIM_FIXTURE and len(fields) == 5 and registered_target(fields[1], fields[4]) and valid_guid(fields[3]):
        return AIM_KEY, dict(zip(("map", "pawn", "guid", "target"), fields[1:]))
    if fields[0] == CONTACT_FIXTURE and len(fields) in (14, 15):
        area, pawn, guid, serial, target, stage, component = fields[1:8]
        camera_scope = fields[14] if len(fields) == 15 else None
        if (not registered_target(area, target) or not valid_guid(guid) or stage not in CONTACT_STAGES
                or camera_scope not in (None, "unchanged", "restored", "restore_unverified")
                or not pawn.startswith("Player_FP_C " + area + ".") or len(pawn) > 512
                or not component or len(component) > 512 or len(serial) > 16 or not serial.isascii() or not serial.isdecimal()):
            return None
        try:
            initial, current, selected, eye_drift, facing, target_drift = map(float, fields[8:14])
        except ValueError:
            return None
        if (not 1 <= int(serial) < 2**53 or not 100 <= initial <= 160 or not -1 <= current <= 1_000_000
                or not -1 <= selected <= 1_000_000 or not -1 <= eye_drift <= 1_000_000
                or not (facing == -2 or -1 <= facing <= 1) or not -1 <= target_drift <= 1_000_000):
            return None
        return CONTACT_KEY, dict(map=area, pawn=pawn, guid=guid, serial=int(serial), target=target, stage=stage,
                                 component=component, camera_scope=camera_scope, initial_health=initial,
                                 contact_health=None if current == -1 else current,
                                 selected_health=None if selected == -1 else selected,
                                 camera_displacement=None if eye_drift == -1 else eye_drift,
                                 camera_direction_dot=None if facing == -2 else facing,
                                 target_displacement=None if target_drift == -1 else target_drift)
    ordinary = fields[0] == SWING
    if (fields[0] in (CANARY, SWING) and len(fields) in ((18,) if ordinary else (12, 18))
            and fields[10] in ("0", "1") and fields[11] in ("0", "1")):
        proof = dict(zip(("map", "pawn", "guid"), fields[1:4]), serial=int(fields[4]), status=fields[5],
                     presses=int(fields[6]), releases=int(fields[7]), contacts=int(fields[8]),
                     qualified=int(fields[9]), settled=fields[10] == "1", passive_free=fields[11] == "1")
        if len(fields) == 18:
            proof.update(target=fields[12], sweeps=int(fields[13]), damage_calls=int(fields[14]),
                         enemy_health_before=float(fields[15]), enemy_health_after=float(fields[16]), stock_damage=float(fields[17]))
            miss = ordinary and proof["status"] == "stock_miss_verified"
            cancelled = ordinary and proof["status"] == "stock_swing_cancelled"
            target = proof["target"]
            if ordinary:
                eligible = ordinary_map(proof["map"]) and (target == "-" or ordinary_target(proof["map"], target))
            else:
                eligible = (proof["map"], target) == (DEAD_BRIDGE, TARGET)
            if (not eligible or proof["status"] == "surface_verified"
                    or proof["settled"] != (proof["status"] == "stock_damage_verified" or miss)
                    or not 0 <= proof["damage_calls"] <= proof["sweeps"] <= 1
                    or ((miss or cancelled) and not verified_miss(proof))
                    or (proof["settled"] and not miss and not verified_damage(proof, fixture=not ordinary))):
                return None
        elif proof["map"] != CROSSROADS or proof["status"] == "stock_damage_verified":
            return None
        return SWING_KEY if ordinary else CANARY_KEY, proof
    return None


def verified_miss(proof: dict) -> bool:
    return (ordinary_map(proof.get("map")) and proof.get("target") == "-"
            and tuple(proof.get(key) for key in ("sweeps", "damage_calls", "enemy_health_before", "enemy_health_after",
                                                "stock_damage")) == (0, 0, -1, -1, -1))


def is_equipped(equipment, area, guid):
    return (ordinary_map(area) and valid_guid(guid) and isinstance(equipment, dict)
            and equipment.get("kind") in KINDS and equipment.get("guid") == guid)


def request_target(guid: str, epoch: str) -> str:
    if not valid_guid(guid) or not isinstance(epoch, str) or not party.REQUEST_ID.fullmatch(epoch):
        raise party.RuleError("native_guest_melee_exact_epoch_and_guid_required")
    return f"{epoch}@{guid}"


def current_guid(target: str | None, epoch: str) -> str:
    observed, delimiter, guid = target.partition("@") if isinstance(target, str) else ("", "", "")
    if not delimiter or not epoch or observed != epoch or not valid_guid(guid):
        raise party.RuleError("native_guest_melee_exact_epoch_and_guid_required")
    return guid


@dataclass
class InputScope:
    character: str
    epoch: str
    area: str
    pawn: str
    guid: str
    frame: dict
    held: str | None = None
    reply_sequence: int | None = None

    def observes(self, frame: dict) -> bool:
        return (frame.get("map"), frame.get("pawn"), frame.get("guid"), frame.get("serial")) == (
            self.area, self.pawn, self.guid, self.frame["serial"])


class AuthorityInput:
    """Match each physical input edge to the same authenticated stock weapon."""

    def __init__(self, host):
        self.host = host
        self.scope: InputScope | None = None
        self.receipts: dict[tuple[str, str, str, str, str], dict] = {}

    def _fail(self, reason):
        self.host.native_checkpoint_unsafe = self.host.stopping = True
        raise RuntimeError(reason)

    def _proof(self, snapshot, scope=None):
        frame = snapshot.get(INPUT_KEY, {})
        if (scope is None or scope.observes(frame)) and frame.get("status", "").startswith(DIRTY):
            self._fail(frame["status"])
        if scope is not None and scope.observes(frame):
            if any(frame[key] < scope.frame[key] for key in ("presses", "releases", "contacts", "hits", "charged", "combo")):
                self._fail("The stock melee edge counters regressed")
            scope.frame = frame
        return frame

    def observe(self):
        host = self.host
        if not self.scope:
            return
        scope = self.scope
        if scope.epoch != host.zone_epoch or (host.last_game.get("map") or scope.area) != scope.area:
            self.scope = None
            return
        if scope.frame["status"] in TERMINAL:
            frame = scope.frame
        elif scope.reply_sequence is not None and host.last_game.get("sequence", -1) <= scope.reply_sequence:
            return
        else:
            frame = self._proof(host.last_game, scope)
        if scope.observes(frame) and frame["status"] in TERMINAL:
            host.control.event("native_guest_melee_input_settled", character_id=scope.character, **frame)
            self.scope = None

    async def request(self, bridge, character, target, operation):
        parts = target.split("@") if isinstance(target, str) else []
        if len(parts) != 4:
            raise party.RuleError("native_guest_melee_exact_edge_required")
        epoch, guid, stroke, edge = parts
        host = self.host
        if (not epoch or epoch != host.zone_epoch or not valid_guid(guid) or edge not in {*EDGES, TAP}
                or not party.REQUEST_ID.fullmatch(stroke)
                or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation)):
            raise party.RuleError("native_guest_melee_exact_edge_required")
        receipt = character, epoch, guid, stroke, edge
        if receipt in self.receipts:
            return self.receipts[receipt]

        self.observe()
        scope = self.scope
        owner = host.native_recipients().get(character) or {}
        area, pawn = host.last_game.get("map"), owner.get("pawn")
        if scope and (character, epoch, area, pawn, guid) != (
                scope.character, scope.epoch, scope.area, scope.pawn, scope.guid):
            raise party.RuleError("native_guest_melee_combat_busy")
        if not host.native_guest_controls or host.stopping or host.native_checkpoint_unsafe or not pawn:
            raise party.RuleError("native_guest_melee_swing_unavailable")
        starting = edge in ("press", TAP)
        if starting:
            equipped = host.last_game.get("native_guest_equipped_weapons", {}).get(pawn)
            if (host.transitioning or host.party.get("wipe") is not None or not owner.get("connected")
                    or host.party["players"].get(character, {}).get("downed")
                    or character in host.party["pending_spawns"] or host.last_game.get("state") != "hosting"
                    or time.monotonic() - host.last_seen > 3 or not is_equipped(equipped, area, guid)
                    or scope and scope.held or host.smg_holds and host.smg_holds.strokes
                    or any(receipt[:4] + (previous,) in self.receipts for previous in (*EDGES, TAP))):
                raise party.RuleError("native_guest_melee_combat_busy")
            if scope is None and not await native_inventory.retain_guest_save(host, bridge, character, pawn):
                raise party.RuleError("native_guest_melee_save_retention_unavailable")
        elif not scope or scope.held != stroke:
            raise party.RuleError("native_guest_melee_exact_edge_required")
        prior_presses, prior_releases = (scope.frame["presses"], scope.frame["releases"]) if scope else (0, 0)
        if scope:
            command = INPUT + "-edge", str(scope.frame["serial"]), edge
        else:
            command = (INPUT + "-start", pawn, guid, TAP) if edge == TAP else (INPUT + "-start", pawn, guid)
        try:
            snapshot = await bridge.command(*command, timeout=3)
        except RuntimeError as error:
            if isinstance(error, LocalCommandNotSubmitted) or str(error).startswith("native_guest_melee_"):
                raise party.RuleError(str(error)) from error
            self._fail(str(error))
        except (OSError, ValueError, TimeoutError) as error:
            self._fail(str(error))
        frame = self._proof(snapshot, scope)
        if (snapshot.get("state"), snapshot.get("map"), frame.get("map"), frame.get("pawn"), frame.get("guid")) != (
                "hosting", area, area, pawn, guid) or (scope is not None and not scope.observes(frame)):
            self._fail("The stock melee edge did not report its exact owner")
        if (frame.get("presses"), frame.get("releases")) != (prior_presses + starting,
                                                         prior_releases + (edge != "press")):
            self._fail("The stock melee input did not execute the exact edge")
        if scope is None:
            scope = self.scope = InputScope(character, epoch, area, pawn, guid, frame)
        scope.held = stroke if edge == "press" else None
        result = {INPUT_KEY: frame}
        if REPLY_BYTES in snapshot:
            # A frame published before this mutation can still be awaiting the host loop.
            scope.reply_sequence = snapshot["sequence"]
            result[REPLY_BYTES] = snapshot[REPLY_BYTES]
        else:
            scope.reply_sequence = None
            host.last_game, host.last_seen = snapshot, time.monotonic()
        self.receipts[receipt] = result
        if len(self.receipts) > 256:
            self.receipts.pop(next(iter(self.receipts)))
        return result


class GuestInput:
    def __init__(self, client):
        self.client = client
        self.sequence = 0

    async def service(self, scope) -> None:
        client = self.client
        session, epoch = client.host_session, client.zone_epoch
        inputs = client.bridge.native_melee_input_requests()
        for index, (sequence, area, pawn, guid, kind, edge, number, _) in enumerate(inputs):
            if sequence <= self.sequence:
                continue
            if edge == "press" and index + 1 < len(inputs):
                following = inputs[index + 1]
                # The native clock measures the actual input interval. A stalled
                # worker can drain a long hold and its release together.
                if following[:7] == (sequence + 1, area, pawn, guid, kind, "release", number) and following[7] <= MAX_TAP_MS:
                    sequence, edge = following[0], TAP
            self.sequence = sequence
            if not session or not epoch or (area, pawn, guid, kind) != scope or (
                    client.host_session, client.zone_epoch) != (session, epoch):
                continue
            token = uuid.uuid5(client.bridge.input_namespace, f"{session}:{epoch}:melee:{number}").hex
            operation = uuid.uuid5(client.bridge.input_namespace, f"{token}:{edge}:{sequence}").hex
            try:
                result = await client.gameplay_action(INPUT, operation, f"{epoch}@{guid}@{token}@{edge}")
                client.control.event("native_guest_melee_edge", operation_id=operation, sequence=sequence, edge=edge, **result)
            except (OSError, RuntimeError, ValueError) as error:
                client.control.event("native_guest_melee_edge_rejected", operation_id=operation, sequence=sequence,
                                     edge=edge, reason=str(error))


async def perform(host, bridge, action: str, character: str, guid: str, operation_id: str, *, fixture_lane: int | None = None) -> dict:
    area = host.last_game.get("map")
    ordinary = action == SWING
    fixtures = {DAMAGE: (DEAD_BRIDGE,), CANARY: (CROSSROADS,), EQUIP: (CROSSROADS, DEAD_BRIDGE),
                AIM_FIXTURE: tuple(BY_MAP)}
    map_ok = ordinary_map(area) if ordinary else area in fixtures.get(action, ())
    if (not host.native_guest or (not ordinary and not host.fixtures) or (ordinary and not host.native_guest_controls)
            or host.native_profile is None or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or not map_ok or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_melee_swing_unavailable" if ordinary else "native_guest_melee_fixture_unavailable")
    if (not isinstance(character, str) or not valid_guid(guid) or not isinstance(operation_id, str)
            or not party.REQUEST_ID.fullmatch(operation_id)):
        raise party.RuleError("native_guest_melee_exact_guid_required")
    if fixture_lane is not None and (action != AIM_FIXTURE or type(fixture_lane) is not int
                                     or not 1 <= fixture_lane <= len(BY_MAP[area].actors)):
        raise party.RuleError("native_guest_melee_fixture_registry_target_required")
    fingerprint = [action, guid, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    owner = host.native_recipients().get(character) or {}
    pawn = owner.get("pawn") if owner.get("connected") else None
    if (not pawn or host.party["wipe"] is not None or character in host.party["pending_spawns"]
            or host.party["players"].get(character, {}).get("downed")):
        raise party.RuleError("native_guest_melee_exact_owner_required")
    if ordinary or action == AIM_FIXTURE:
        equipped = host.last_game.get("native_guest_equipped_weapons", {}).get(pawn, {})
        if not is_equipped(equipped, area, guid):
            raise party.RuleError("native_guest_melee_exact_equipment_required")
        if ordinary and ((host.smg_holds is not None and host.smg_holds.strokes)
                         or (input_scope := getattr(host, "melee_input", None)) and input_scope.scope):
            raise party.RuleError("native_guest_melee_combat_busy")
    else:
        claimed = host.party.get(native_authored_pickup.STATE_KEY, {}).get(native_authored_pickup.OLD_PIPE, {})
        if ((claimed.get("owner"), claimed.get("guid")) != (character, guid)
                or area == DEAD_BRIDGE and tuple(claimed.get(key) for key in ("source", "map", "actor", "class_path")) != (
                    native_authored_pickup.OLD_PIPE, *native_authored_pickup.SOURCES[native_authored_pickup.OLD_PIPE])):
            raise party.RuleError("native_guest_melee_exact_authored_claim_required")

    def exact(snapshot: dict, key: str) -> dict:
        proof = snapshot.get(key, {})
        if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("guid")) == (
                "hosting", area, area, pawn, guid):
            return proof
        return {}

    if action == AIM_FIXTURE:
        target = BY_MAP[area].actors[(fixture_lane or 1) - 1]
        snapshot = await bridge.command(action, pawn, guid, target, timeout=5)
        proof = exact(snapshot, AIM_KEY)
        if not proof or proof.get("target") != target:
            raise RuntimeError("The staged stock Old Pipe aim directive did not match the guest")
        return {AIM_KEY: proof}

    key = SWING_KEY if ordinary else EQUIP_KEY if action == EQUIP else CANARY_KEY
    deferred = ordinary and await native_inventory.retain_guest_save(host, bridge, character, pawn)
    if ordinary and not deferred:
        raise party.RuleError("native_guest_melee_save_retention_unavailable")
    if not deferred:
        await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(action if action == EQUIP else action + "-start", pawn, guid, timeout=5)
        except RuntimeError as error:
            if isinstance(error, LocalCommandNotSubmitted) or str(error).startswith("native_guest_melee_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        reply_bytes = snapshot.get(REPLY_BYTES)
        proof = exact(snapshot, key)
        if not proof:
            raise RuntimeError("The stock melee receipt does not match the exact guest and GUID")
        if action == EQUIP:
            if proof.get("ok") is not True:
                status = proof.get("status", "")
                if not status or status.startswith("mutation_unverified_"):
                    raise RuntimeError(status or "The stock Old Pipe equip could not be verified")
                host.native_checkpoint_unsafe = False
                raise party.RuleError(status)
            if proof.get("status") != "old_pipe_equipped":
                raise RuntimeError("The exact stock Old Pipe was not equipped")
        else:
            serial = proof.get("serial")
            if type(serial) is not int or serial < 1:
                raise RuntimeError("The stock melee animation did not report a bound serial")
            if proof.get("status") == "pending":
                def terminal(frame):
                    current = exact(frame, key)
                    return current.get("serial") == serial and current.get("status") != "pending"

                snapshot = await bridge.wait_for(terminal, timeout=5)
                proof = exact(snapshot, key)
            if action == DAMAGE and proof.get("status") == "stock_damage_aim_fenced":
                host.native_checkpoint_unsafe = False
                raise party.RuleError("native_guest_melee_stock_damage_contact_aim_changed")
            if ordinary and proof.get("status") == "stock_swing_cancelled" and verified_miss(proof):
                host.native_checkpoint_unsafe = False
                raise party.RuleError("native_guest_melee_swing_cancelled")
            miss = ordinary and proof.get("status") == "stock_miss_verified"
            expected = "stock_miss_verified" if miss else "stock_damage_verified" if ordinary or action == DAMAGE else "surface_verified"
            if (proof.get("status"), proof.get("presses"), proof.get("releases"), proof.get("contacts"),
                    proof.get("qualified"), proof.get("settled"), proof.get("passive_free")) != (
                    expected, 1, 1, 1, 1, True, True) or (miss and not verified_miss(proof)) or (
                    (ordinary or action == DAMAGE) and not miss and not verified_damage(proof, fixture=not ordinary)) or (
                    action == DAMAGE and proof.get("target") != TARGET):
                raise RuntimeError(proof.get("status") or "The fenced stock animation did not settle")
        if REPLY_BYTES not in snapshot:
            host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {key: proof}
        if reply_bytes is not None:
            result[REPLY_BYTES] = reply_bytes
        if deferred:
            candidate = host.party.copy()
            candidate["receipts"] = host.party["receipts"].copy()
            candidate["receipts"][character] = list(candidate["receipts"].get(character, ()))
        else:
            candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        if not deferred:
            await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        return result
    except (OSError, RuntimeError, ValueError, TimeoutError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
