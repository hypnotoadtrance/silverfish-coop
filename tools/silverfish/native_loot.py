"""Commit and share host-authoritative rolls for qualified stock crates."""

import asyncio
import math
import time
import uuid

from . import native_host_save, native_pickup_audio, native_stock_door, party
from .authored_cash import CASH_RANGES
from .authored_loot import (CRATES, DEFERRED_PRY_CLASSES, LEGACY_CRATE, key_unlock_only, lockable_container, loot_capacity, random_lid,
                            valid_item_transform, valid_lid_angle, valid_loot_trait_calls)
from .native_gold import valid_guid


CRATE = LEGACY_CRATE
STATE_KEY = "native_loot_rolls"
CRATE_DIAGNOSTIC = "native-loot-crate-diagnostic"
CRATE_DIAGNOSTICS = "native_loot_crate_diagnostics"
RETIRED = "native_loot_retired_rolls"
REPLENISHED = "native_replenished_crates"
VEHICLE_LOCKS = "native_loot_vehicle_locks"
STOCK_ALARMS = "native_loot_stock_alarms"
POWER_BOX_STATES = "native_loot_power_boxes"
ELECTRONIC_OPENS = "native_loot_electronic_powered_opens"
ELECTRONIC_POWER_FIXTURE = "native-loot-electronic-power-fixture"
KEY_UNLOCKS = "native_loot_key_unlocks"
UNLOCK_RESULT = "native_loot_unlock"
TORCH_USE = "native-guest-vehicle-blowtorch"
TORCH_RESULT = "native_guest_blowtorch"
CROWBAR_USE = "native-guest-vehicle-crowbar"
CROWBAR_RESULT = "native_guest_crowbar"
LOCKPICK = "native-loot-guest-lockpick"
LOCKPICK_INPUTS = "native_loot_lockpick_inputs"
LOCKPICK_RESULT = "native_loot_lockpick_result"
PRY_TOOL_ACTIONS = {"blowtorch": TORCH_USE, "crowbar": CROWBAR_USE}
PRY_TOOL_RESULTS = {"blowtorch": TORCH_RESULT, "crowbar": CROWBAR_RESULT}
PLACEMENT = "native-loot-placement"
STACK_MERGE_TAG = "native-loot-stack-merge"
PLACEMENT_KEY = "native_loot_placement"
FORCED_ROLL_STATUS = "opened_fixture_seeded"
SPECIAL_MEMORY_CAPABILITY = "native_loot_special_memory"
INSPECTION_BYTES = "native_loot_inspection_reply_bytes"


def public_roll(roll: dict) -> dict:
    return {key: value for key, value in roll.items() if key != "escrow"}


async def flush_pending_escrows(host, bridge, state):
    from . import (native_enemy_loot, native_guest_drop_record, native_meet_broker, native_mosby_tape,
                   native_prototype_drill, native_stock_enemy_drops, native_subway_valve, native_wire_quest)

    ledgers = STATE_KEY, native_stock_enemy_drops.STATE, native_enemy_loot.STATE_KEY, native_guest_drop_record.STATE
    pending = {(ledger, identity): roll["escrow"]
               for ledger in ledgers
               for identity, roll in state.get(ledger, {}).items()
               if (roll.get("escrow") or {}).get("pending") is True}
    for ledger in (native_wire_quest.REWARD_KEY, native_meet_broker.REWARDS.key, native_mosby_tape.REWARDS,
                   native_prototype_drill.REWARDS, native_subway_valve.PENDING):
        escrow = state.get(ledger, {}).get("escrow", {})
        if escrow.get("pending") is True:
            pending[(ledger, None)] = escrow
    if not pending:
        return state, {}
    slots = [row["slot"] for row in pending.values()]
    for start in range(0, len(slots), 16):
        snapshot = await bridge.command("native-loot-flush-escrows", *slots[start:start + 16])
        if snapshot.get("state") != "hosting" or snapshot.get("map") != host.last_game.get("map"):
            raise RuntimeError("The stock loot escrow save changed campaign map")
        host.last_game, host.last_seen = snapshot, time.monotonic()

    def read_frames():
        return {key: native_host_save.record(host.native_profile, row["slot"], row["map"], flush=True)
                for key, row in pending.items()}

    records = await asyncio.to_thread(read_frames)
    updated = dict(state)
    for ledger in {key[0] for key in records}:
        updated[ledger] = dict(state[ledger])
    for (ledger, identity), frame in records.items():
        if identity is None:
            updated[ledger]["escrow"] = frame
        else:
            updated[ledger][identity] = dict(state[ledger][identity], escrow=frame)
    return updated, records


def pry_defers_unlock(crate: str) -> bool:
    return crate.partition(" ")[0] in DEFERRED_PRY_CLASSES


def _scope(host, character: str) -> str:
    if character == "host":
        return host.last_game.get("pawns", {}).get("local", {}).get("name", "")
    record = host.native_recipients().get(character, {})
    return record.get("pawn", "") if record.get("connected") else ""


def interactions(game: dict, last_sequence: int) -> list[tuple[int, str, str]]:
    proofs = game.get("native_loot_crate_interactions", [game.get("native_loot_crate_interaction", {})])
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    area = game.get("map")
    pending = {}
    for proof in proofs:
        crate, sequence, entry = (proof.get(key) for key in ("crate", "sequence", "entry"))
        if (isinstance(crate, str) and crate in CRATES and CRATES[crate] == area and pawn
                and type(sequence) is int and last_sequence < sequence < 2**53
                and (proof.get("map"), proof.get("pawn")) == (area, pawn)
                and (entry in ("stock", "fixture") or (entry == "pried"
                     and game.get("state") == "hosting" and lockable_container(crate, area)))):
            pending[sequence] = (sequence, crate, entry)
    return sorted(pending.values())


class OpenIntents:
    """Merge early stock F receipts with the compatible status stream."""

    def __init__(self) -> None:
        self.last_status = 0
        self.early = set()

    def take(self, game: dict, inputs: list[bytes]) -> list[tuple[int, str, str, str]]:
        status = interactions(game, self.last_status)
        pending = {sequence: (sequence, crate, entry, "status") for sequence, crate, entry in status}
        area, pawn = game.get("map"), game.get("pawns", {}).get("local", {}).get("name")
        if area and pawn and game.get("state") in ("hosting", "connected"):
            for record in inputs:
                try:
                    fields = record.decode("ascii").rstrip("\n").split("\t")
                except UnicodeError:
                    continue
                if len(fields) != 5:
                    continue
                tag, source, crate, owner, raw_sequence = fields
                if (tag != "loot-crate-open" or source != area or owner != pawn or CRATES.get(crate) != area
                        or not 0 < len(raw_sequence) <= 16 or not raw_sequence.isdigit()):
                    continue
                sequence = int(raw_sequence)
                if self.last_status < sequence < 2**53:
                    pending.setdefault(sequence, (sequence, crate, "stock", "memory_input"))

        ready = []
        for sequence, crate, entry, source in sorted(pending.values()):
            if sequence in self.early:
                continue
            if source == "memory_input":
                # If status stops advancing, let its existing fallback handle later inputs.
                if len(self.early) >= 128:
                    continue
                self.early.add(sequence)
            ready.append((sequence, crate, entry, source))
        if status:
            self.last_status = status[-1][0]
            self.early = {sequence for sequence in self.early if sequence > self.last_status}
        return ready


def interaction(game: dict, last_sequence: int) -> tuple[int, str] | None:
    pending = interactions(game, last_sequence)
    if pending:
        sequence, crate, _ = pending[-1]
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
    slots = [escrow["slot"] for roll in expired.values()
             if (escrow := roll.get("escrow")) and escrow.get("pending") is True]
    for start in range(0, len(slots), 16):
        host.last_game = await bridge.command("native-loot-release-escrows", *slots[start:start + 16])
        host.last_seen = time.monotonic()
    host.pending_host_save = bool(host.pending_host_save)
    host.party = candidate
    for crate, roll in expired.items():
        host.control.event("native_loot_roll_replenished", crate=crate, roll_id=roll["id"])


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
        host.pending_host_save = bool(host.pending_host_save)
        host.party = candidate
        host.control.event("native_loot_legacy_placement_restored", crate=crate, map=area)
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


async def crate_diagnostic(bridge, game, crate) -> dict:
    """Read one authored crate on the exact local pawn's current map."""
    phase, area = game.get("state"), game.get("map")
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if (phase not in ("hosting", "connected") or not isinstance(pawn, str) or not pawn or not area
            or not isinstance(crate, str) or CRATES.get(crate) != area):
        raise ValueError("Select an exact authored crate in the active local map")
    snapshot = await bridge.command(CRATE_DIAGNOSTIC, area, pawn, crate, timeout=2, update_status=False)
    row = snapshot.get(CRATE_DIAGNOSTICS, {}).get(crate, {})
    if ((snapshot.get("state"), snapshot.get("map"), snapshot.get("pawns", {}).get("local", {}).get("name")) !=
            (phase, area, pawn) or (row.get("phase"), row.get("map"), row.get("crate")) != (phase, area, crate)):
        raise RuntimeError("The exact local crate diagnostic did not verify its scope")
    return {"native_loot_crate_diagnostic": row}


def _opening_arguments(game, crate, lid, angle):
    scope = game.get("native_loot_crates", {}).get(crate, {})
    if ((scope.get("map"), scope.get("crate")) != (game.get("map"), crate)
            or scope.get("state") not in {"ready", "exhausted"}):
        return None
    if lid and not valid_lid_angle(lid, angle):
        raise RuntimeError("The committed stock lid pose is invalid")
    if scope.get("state") == "exhausted" and (not lid or _same_lid_angle(lid, scope, angle)):
        return None
    return (str(angle),) if lid else ()


def _verify_opened(snapshot, area, crate, lid, angle):
    proof = snapshot.get("native_loot_crates", {}).get(crate, {})
    if (snapshot.get("map") != area or (proof.get("map"), proof.get("crate"), proof.get("state")) !=
            (area, crate, "exhausted") or (lid and not _same_lid_angle(lid, proof, angle))):
        raise RuntimeError("The exact stock crate did not reflect its committed roll")


async def project_opened(bridge, game: dict, rolls: dict) -> dict:
    """Project saved open crates in this map without replaying stock rolls."""
    area = game.get("map")
    replenished = _replenished(game, rolls or {})
    for crate, roll in _rolls_in_area(rolls or {}, area).items():
        if crate in replenished:
            continue
        lid = random_lid(crate)
        angle = roll.get(lid[0]) if lid else None
        arguments = _opening_arguments(game, crate, lid, angle)
        if arguments is None:
            continue
        snapshot = await bridge.command("native-loot-crate-project", "exhausted", crate, *arguments, timeout=4)
        _verify_opened(snapshot, area, crate, lid, angle)
        game = snapshot
    return game


class GuestCrateProjection:
    """Restore accepted lid poses from fresh host rolls between guest input passes."""

    attempts_per_turn = 2
    turn_seconds = .150
    retry_seconds = .750

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.scope = None
        self.last_attempt = None
        self.prioritized_sequence = 0
        self.deferred = {}

    def _defer(self, client, crate, desired, error) -> None:
        reason = str(error)[:200] or "stock lid readback timed out"
        previous = self.deferred.get(crate)
        self.deferred[crate] = (desired, time.monotonic() + self.retry_seconds, reason)
        if previous is None or (previous[0], previous[2]) != (desired, reason):
            client.control.event("native_loot_guest_lid_deferred", crate=crate, reason=reason)

    @staticmethod
    def _scope(client, game):
        area = game.get("map")
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        scope = (client.host_session, client.zone_epoch, area, pawn)
        if (client.stopping or client.needs_travel or client.server_transitioning or not client.transport.game_access
                or game.get("state") != "connected" or area != client.expected_zone or not all(scope)):
            return None
        return scope

    async def sync(self, client, *, confirmed_crate=None) -> None:
        game, now = client.last_game, time.monotonic()
        scope = self._scope(client, game)
        if scope is None:
            self.reset()
            return
        area = scope[2]
        if scope != self.scope:
            self.reset()
            self.scope = scope
        if confirmed_crate is None and now - client.gameplay_seen >= 3:
            return

        rolls = _rolls_in_area(client.gameplay.get(STATE_KEY, {}), area)
        for crate in self.deferred.keys() - rolls.keys():
            del self.deferred[crate]
        order = list(rolls)
        if self.last_attempt in rolls:
            start = order.index(self.last_attempt) + 1
            order = order[start:] + order[:start]
        # A fresh local press should see its confirmed lid before the restored backlog.
        pressed = interaction(game, self.prioritized_sequence)
        if confirmed_crate is not None:
            order = [confirmed_crate] if confirmed_crate in rolls else []
        elif pressed and pressed[1] in rolls:
            self.prioritized_sequence, crate = pressed
            order.remove(crate)
            order.insert(0, crate)

        deadline = now + self.turn_seconds
        attempts = 0
        for crate in order:
            now = time.monotonic()
            if (attempts >= self.attempts_per_turn or now >= deadline
                    or (confirmed_crate is None and now - client.gameplay_seen >= 3)):
                break
            lid = random_lid(crate)
            roll = rolls[crate]
            angle = roll.get(lid[0]) if lid else None
            desired = (roll.get("id"), angle)
            retry = self.deferred.get(crate)
            if retry and retry[0] == desired and now < retry[1]:
                continue
            try:
                arguments = _opening_arguments(game, crate, lid, angle)
                if arguments is None:
                    self.deferred.pop(crate, None)
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                attempts += 1
                self.last_attempt = crate
                # Include queue waiting as well as the native acknowledgement in the turn's budget.
                async with asyncio.timeout(remaining):
                    snapshot = await client.bridge.command(
                        "native-loot-crate-project", "exhausted", crate, *arguments, timeout=remaining, update_status=False)
                if self._scope(client, client.last_game) != scope or self._scope(client, snapshot) != scope:
                    self.reset()
                    return
                _verify_opened(snapshot, area, crate, lid, angle)
                if snapshot.get("sequence", 0) >= client.last_game.get("sequence", 0):
                    client.last_game = snapshot
                game = client.last_game
                self.deferred.pop(crate, None)
            except (OSError, RuntimeError) as error:
                if self._scope(client, client.last_game) != scope:
                    self.reset()
                    return
                self._defer(client, crate, desired, error)


async def _project_container_signal(bridge, game, published, frame, command, flag):
    area = game.get("map")
    current = game.get(frame, {})
    if game.get("state") != "connected" or not isinstance(published, dict) or not current:
        return game
    updates = []
    for actor, before in current.items():
        after = published.get(actor)
        if (not isinstance(after, dict) or CRATES.get(actor) != area or after == before or after.get("map") != area
                or any(type(after.get(field)) is not bool for field in (flag, "audible"))):
            continue
        updates.append((actor, str(int(after[flag])), str(int(after["audible"]))))
        if len(updates) == 24:
            break
    if not updates:
        return game
    return await bridge.command(command, area, *(value for update in updates for value in update), timeout=3)


async def project_stock_alarms(bridge, game: dict, published_alarms: dict | None) -> dict:
    return await _project_container_signal(bridge, game, published_alarms, STOCK_ALARMS, "native-loot-stock-alarm-project", "lit")


async def project_power_boxes(bridge, game: dict, published_boxes: dict | None) -> dict:
    return await _project_container_signal(bridge, game, published_boxes, POWER_BOX_STATES, "native-loot-power-box-project", "powered")


async def observe_powered_electronics(host, bridge) -> None:
    game = host.last_game
    player = host.party["players"].get("host", {})
    if (game.get("state") != "hosting" or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or host.party.get("wipe") or player.get("downed", True)):
        return
    openings = sorted(game.get(ELECTRONIC_OPENS, {}).values(), key=lambda row: row["sequence"])
    for row in openings[:4]:
        crate, sequence = row["crate"], row["sequence"]
        if host.last_game.get(ELECTRONIC_OPENS, {}).get(crate) != row:
            continue
        operation = uuid.uuid5(uuid.NAMESPACE_OID, f"{host.control.generation}:{host.zone_epoch}:electronic:{crate}:{sequence}").hex
        try:
            await perform(host, bridge, "host", "open", crate, operation, power_sequence=sequence, defer_save=True)
        except party.RuleError as error:
            host.control.event("native_stock_crate_interaction_rejected", sequence=sequence,
                               operation_id=operation, crate=crate, entry="powered", reason=str(error))
        else:
            host.control.event("native_stock_crate_interaction", sequence=sequence,
                               operation_id=operation, crate=crate, entry="powered")


def _same_lid_angle(lid, row, expected):
    observed = row.get(lid[0])
    return valid_lid_angle(lid, observed) and abs(observed - expected) < .05


async def _settled_native_lid(bridge, opening, crate, lid):
    area, sequence = opening["map"], opening["sequence"]
    owner = opening.get("pawns", {}).get("local", {}).get("name")
    stable_angle, stable_since = None, 0.0

    def settled(snapshot):
        nonlocal sequence, stable_angle, stable_since
        if (snapshot.get("state"), snapshot.get("map"), snapshot.get("pawns", {}).get("local", {}).get("name")) != (
                "hosting", area, owner) or snapshot.get("sequence", 0) <= sequence:
            return False
        sequence = snapshot["sequence"]
        row = snapshot.get("native_loot_crates", {}).get(crate, {})
        angle = row.get(lid[0])
        if ((row.get("map"), row.get("crate"), row.get("state")) != (area, crate, "exhausted")
                or not valid_lid_angle(lid, angle)):
            stable_angle = None
            return False
        now = time.monotonic()
        if stable_angle is None or abs(stable_angle - angle) >= .002:
            stable_angle, stable_since = angle, now
            return False
        return now - stable_since >= .25

    # The original MoveComponentTo action keeps ticking on the host game thread.
    # Wait for new status frames showing its settled lid before committing the roll.
    return await bridge.wait_for(settled, timeout=4)


async def project_vehicle_locks(bridge, game, locks, *, restore_keys=False):
    area = game.get("map")
    observed = game.get(VEHICLE_LOCKS, {})
    phase = "hosting" if restore_keys else "connected"
    if not isinstance(locks, dict) or game.get("state") != phase:
        return game
    qualifies = key_unlock_only if restore_keys else lockable_container
    missing = [(crate, value) for crate, value in locks.items() if crate in observed and qualifies(crate, area)
               and type(value) is bool and value != observed[crate]]
    command = "native-loot-restore-key-unlocks" if restore_keys else "native-loot-vehicle-locks"
    for offset in range(0, len(missing), 32):
        batch = missing[offset:offset + 32]
        args = tuple(part for crate, value in batch for part in (crate, str(int(value))))
        game = await bridge.command(command, area, *args, timeout=4)
        readback = game.get(VEHICLE_LOCKS, {})
        if (game.get("state"), game.get("map")) != (phase, area) or any(readback.get(key) is not value for key, value in batch):
            raise RuntimeError("The stock container locks did not match the co-op state")
    return game


async def restore_key_unlocks(host, bridge):
    if host.stopping or host.transitioning or host.native_checkpoint_unsafe or host.party.get("wipe"):
        return
    rows = host.party.get(KEY_UNLOCKS, {})
    if not rows:
        return
    game = host.last_game
    area = game.get("map")
    visible = game.get("native_loot_crates", {})
    locks = {crate: False for crate, row in rows.items() if key_unlock_only(crate, area)
             and row["opener"] in host.party["players"] and party.REQUEST_ID.fullmatch(row["id"])
             and visible.get(crate, {}).get("state") == "ready"}
    snapshot = await project_vehicle_locks(bridge, game, locks, restore_keys=True)
    if snapshot is not game:
        host.last_game, host.last_seen = snapshot, time.monotonic()


def lockpick_door(actor, area):
    return actor not in CRATES and bool(area) and native_stock_door.actor_map(actor) == area


def parse_lockpick(fields):
    _, area, crate, pawn, sequence, guid, unlocked, quantity, spent = fields
    if (not (lockable_container(crate, area) or lockpick_door(crate, area))
            or not pawn.startswith("Player_FP_C ")
            or not 0 < int(sequence) < 2**53 or not valid_guid(guid) or unlocked not in ("0", "1")
            or not 0 < int(quantity) <= 2**31 - 1 or spent not in ("0", "1") or unlocked == spent == "0"):
        raise ValueError("Invalid stock lockpick result")
    return dict(map=area, crate=crate, pawn=pawn, sequence=int(sequence), guid=guid, unlocked=unlocked,
                quantity=quantity, spent=spent)


async def settle_lockpick(host, bridge, character, target, operation):
    from .native_inventory import retain_guest_save

    game, pawn = host.last_game, _scope(host, character)
    area = game.get("map")
    try:
        epoch, crate, guid, unlocked, quantity, spent = target.split("@")
        outcome = parse_lockpick(("loot-lockpick", area, crate, pawn, "1", guid, unlocked, quantity, spent))
    except (AttributeError, TypeError, ValueError) as error:
        raise party.RuleError("stock_lockpick_refused_arguments") from error
    if (not host.native_loot_input or character == "host" or not pawn or epoch != host.zone_epoch
            or game.get("state") != "hosting" or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or host.party.get("wipe") or character in host.party["pending_spawns"] or host.party["players"][character]["downed"]):
        raise party.RuleError("stock_lockpick_refused_scope")
    fingerprint = [LOCKPICK, target, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if not await retain_guest_save(host, bridge, character, pawn) or epoch != host.zone_epoch or _scope(host, character) != pawn:
        raise party.RuleError("stock_lockpick_refused_detached_save")

    try:
        frame = await bridge.command(LOCKPICK, crate, pawn, guid, outcome["unlocked"], outcome["quantity"], outcome["spent"], timeout=3)
    except RuntimeError as error:
        if str(error).startswith("stock_lockpick_refused_"):
            raise party.RuleError(str(error)) from error
        raise
    locked = frame.get("ack", {}).get("message")
    if ((frame.get("state"), frame.get("map")) != ("hosting", area) or locked not in ("locked", "unlocked")
            or unlocked == "1" and locked != "unlocked"):
        raise RuntimeError("The stock lockpick result could not be verified")
    host.last_game, host.last_seen = frame, time.monotonic()
    remaining = int(quantity) - int(spent)
    if remaining == 0:
        host.party["players"][character].get("native_inventory", {}).pop(guid, None)
        host.native_pickup.forget_committed_row(character, guid)
    proof = {"map": area, "crate": crate, "guid": guid, "locked": locked == "locked", "remaining": remaining}
    result = {LOCKPICK_RESULT: proof}
    if lockpick_door(crate, area):
        delta = native_stock_door.frame(frame, epoch, crate)
        if delta:
            result[native_stock_door.DELTA] = delta
    recent = host.party["receipts"].setdefault(character, [])
    recent.append({"id": operation, "request": fingerprint, "result": result})
    del recent[:-party.RECENT_ACTIONS]
    return result


async def use_vehicle_pry_tool(host, bridge, character, target, operation_id, action):
    kind = action.removeprefix("native-guest-vehicle-")
    if PRY_TOOL_ACTIONS.get(kind) != action:
        raise party.RuleError("stock_vehicle_pry_tool_unavailable")
    result_key = PRY_TOOL_RESULTS[kind]
    epoch, _, selection = target.partition("@") if isinstance(target, str) else ("", "", "")
    guid, _, crate = selection.partition("@")
    game = host.last_game
    area, pawn = game.get("map"), _scope(host, character)
    if (not host.native_loot_input or not pawn or character == "host" or not epoch or epoch != host.zone_epoch
            or not valid_guid(guid) or not lockable_container(crate, area) or not party.REQUEST_ID.fullmatch(operation_id)
            or game.get("state") != "hosting" or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or host.party["wipe"] or character in host.party["pending_spawns"] or host.party["players"][character]["downed"]):
        raise party.RuleError(f"stock_{kind}_scope_unavailable")
    fingerprint = [action, target, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if (game.get(VEHICLE_LOCKS, {}).get(crate) is not True or crate in host.party.get(STATE_KEY, {})
            or host.smg_holds is not None and host.smg_holds.strokes):
        raise party.RuleError(f"stock_{kind}_vehicle_not_locked")
    defer_save = game.get(SPECIAL_MEMORY_CAPABILITY) is True
    if defer_save:
        from .native_inventory import retain_guest_save

        if not await retain_guest_save(host, bridge, character, pawn):
            raise party.RuleError("native_guest_save_retention_unavailable")
        if host.zone_epoch != epoch or _scope(host, character) != pawn:
            raise party.RuleError(f"stock_{kind}_scope_unavailable")
    before_sequence = game.get("native_loot_guest_pries", {}).get(crate, {}).get("sequence", 0)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(f"native-loot-guest-{kind}-use", crate, pawn, guid, timeout=4)
        except RuntimeError as error:
            if str(error).startswith(f"stock_{kind}_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(result_key, {})
        deferred_unlock = pry_defers_unlock(crate)
        if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("crate"), proof.get("pawn"), proof.get("guid"))
                != ("hosting", area, area, crate, pawn, guid)
                or snapshot.get(VEHICLE_LOCKS, {}).get(crate) is not deferred_unlock):
            raise RuntimeError(f"The stock {kind} did not report the authenticated tool and expected latch state")

        def ready(frame):
            row = frame.get("native_loot_guest_pries", {}).get(crate, {})
            return ((frame.get("state"), frame.get("map"), row.get("map"), row.get("pawn")) == ("hosting", area, area, pawn)
                    and row.get("sequence", 0) > before_sequence)

        host.last_game = snapshot if ready(snapshot) else await bridge.wait_for(ready, timeout=4)
        host.last_seen = time.monotonic()
        return await perform(host, bridge, character, "open", crate, operation_id, pried=True,
                             tool_receipt=(fingerprint, result_key, proof), defer_save=defer_save)
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True


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


def _valid_stack_merge(guid, inventory_guid, amount, before, after) -> bool:
    return (valid_guid(guid) and valid_guid(inventory_guid) and guid != inventory_guid
            and all(type(value) is int for value in (amount, before, after))
            and 0 < amount and 0 < before < after < 2**31 and after - before == amount)


def parse_stack_merge(fields) -> dict:
    guid, inventory_guid, *counts = fields
    if len(counts) != 3 or not all(count.isascii() and count.isdecimal() and len(count) <= 10 for count in counts):
        raise ValueError("Invalid stock loot stack merge counts")
    amount, before, after = map(int, counts)
    if not _valid_stack_merge(guid, inventory_guid, amount, before, after):
        raise ValueError("Invalid stock loot stack merge")
    return dict(guid=guid, inventory_guid=inventory_guid, amount=amount, before=before, after=after)


def verified_stack_merge(proof: dict, source_guid: str, source_count: int | None) -> dict:
    guid, inventory_guid, amount, before, after = (
        proof.get(key) for key in ("guid", "inventory_guid", "amount", "before", "after")
    )
    if (not _valid_stack_merge(guid, inventory_guid, amount, before, after)
            or guid != source_guid or type(source_count) is not int or amount != source_count):
        raise RuntimeError("The exact stock loot stack merge did not verify")
    return dict(inventory_guid=inventory_guid, stack_merge=dict(amount=amount, before=before, after=after))


async def perform(host, bridge, character: str, action: str, target: str, operation_id: str, *,
                  fixture_nonempty: bool = False, pried: bool = False, tool_receipt=None,
                  power_sequence: int | None = None, defer_save: bool = False) -> dict:
    area = host.last_game.get("map")
    if (not host.native_zones or not (host.fixtures or host.native_loot_input) or host.native_profile is None
            or host.stopping or host.transitioning or (host.native_checkpoint_unsafe and tool_receipt is None)
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
    if pried and (action != "open" or (character != "host" and tool_receipt is None) or fixture_nonempty or not lockable_container(crate, area)):
        raise party.RuleError("native_loot_pry_receipt_unavailable")
    if power_sequence is not None:
        expected = dict(map=area, crate=crate, pawn=pawn, sequence=power_sequence)
        if (action != "open" or character != "host" or fixture_nonempty or pried or tool_receipt is not None
                or type(power_sequence) is not int or not 0 < power_sequence < 2**53
                or not crate.startswith("ElectronicLocker_01_BP_C ")
                or host.last_game.get(ELECTRONIC_OPENS, {}).get(crate) != expected):
            raise party.RuleError("stock_electronic_power_receipt_unavailable")
    retired = host.party.get(RETIRED, {})
    if action == "open" and operation_id in retired:
        raise party.RuleError("native_loot_roll_replenished" if retired[operation_id] == crate else "operation_id_conflict")
    if action == "open" and any(key != crate and roll.get("id") == operation_id
                                for key, roll in host.party.get(STATE_KEY, {}).items()):
        raise party.RuleError("operation_id_conflict")
    unlock_request = ["native-loot-open", crate, None]
    if action == "open" and tool_receipt is None:
        prior_unlock = next((entry for entry in host.party["receipts"].get(character, [])
                             if entry["id"] == operation_id and UNLOCK_RESULT in entry["result"]), None)
        if prior_unlock:
            if prior_unlock["request"] != unlock_request:
                raise party.RuleError("operation_id_conflict")
            return prior_unlock["result"]
        for unlocked, row in host.party.get(KEY_UNLOCKS, {}).items():
            if row["id"] == operation_id:
                if (unlocked, row["opener"]) != (crate, character):
                    raise party.RuleError("operation_id_conflict")
                return {UNLOCK_RESULT: {"map": area, "crate": crate, "opener": character, "locked": False}}
        if crate in host.party.get(KEY_UNLOCKS, {}) and host.last_game.get(VEHICLE_LOCKS, {}).get(crate) is not False:
            raise party.RuleError("native_loot_key_unlock_restoring")
    saved = rolls.get(crate)
    if saved and action in ("open", "inspect"):
        if crate in _replenished(host.last_game, rolls):
            raise party.RuleError("native_loot_replenishment_pending")
        return {"native_loot_roll": public_roll(saved)}
    if host.party["players"][character]["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]:
        raise party.RuleError("native_loot_opener_unavailable")

    arguments = ()
    memory_escrow = False
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
        from .native_world_loot import crate_identity, item_is_moving

        if item_is_moving(host, crate_identity(saved["id"], guid)):
            raise party.RuleError("native_world_loot_item_in_motion")
        escrow = saved["escrow"]
        if escrow.get("map") != area:
            raise RuntimeError("The crate escrow belongs to a different map")
        if not escrow.get("pending"):
            await asyncio.to_thread(native_host_save.verify_record, host.native_profile, escrow)
        arguments = (guid, escrow["slot"])
        if (transform := item.get("transform")) is not None:
            arguments += tuple(str(axis) for axis in transform["position"])
        if defer_save and character != "host" and host.last_game.get("native_guest_save_retention"):
            arguments += ("defer-save",)
    elif action == "open":
        arguments = (native_host_save.new_slot(),)
        capability = SPECIAL_MEMORY_CAPABILITY if pried or power_sequence is not None else "native_guest_save_retention"
        memory_escrow = defer_save and not fixture_nonempty and host.last_game.get(capability) is True
        if fixture_nonempty:
            arguments += ("fixture-nonempty",)
        elif pried:
            arguments += ("stock-vehicle-pried",)
        elif power_sequence is not None:
            arguments += ("stock-electronic-powered", str(power_sequence))
        if memory_escrow:
            arguments += ("memory-escrow",)
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
            if mutates and tool_receipt is None:
                host.native_checkpoint_unsafe = False
            raise party.RuleError(proof["status"] or "native_loot_refused")
        if action == "inspect" and INSPECTION_BYTES in snapshot:
            return {"native_loot": proof, INSPECTION_BYTES: snapshot[INSPECTION_BYTES]}
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if action == "open":
            if proof.get("status") == "unlocked":
                if (pried or fixture_nonempty or tool_receipt or not key_unlock_only(crate, area)
                        or not proof["invoked"] or proof["looted"] or proof["trait_calls"] or proof["item_count"] or proof["items"]
                        or proof["escrow_slot"] != "-" or snapshot.get(VEHICLE_LOCKS, {}).get(crate) is not False
                        or snapshot.get("native_loot_crates", {}).get(crate, {}).get("state") != "ready"):
                    raise RuntimeError("The stock key unlock did not verify")
                candidate = party.load_party(host.party)
                candidate.setdefault(KEY_UNLOCKS, {})[crate] = {"id": operation_id, "opener": character}
                result = {UNLOCK_RESULT: {"map": area, "crate": crate, "opener": character, "locked": False}}
                receipts = candidate["receipts"].setdefault(character, [])
                receipts.append({"id": operation_id, "request": unlock_request, "result": result})
                del receipts[:-party.RECENT_ACTIONS]
                revision = None
                if memory_escrow:
                    host.pending_host_save = bool(host.pending_host_save)
                else:
                    revision = await host.checkpoint_native(bridge, candidate)
                host.party = candidate
                host.native_checkpoint_unsafe = False
                host.control.event("native_loot_key_unlocked", revision=revision, operation_id=operation_id, **result[UNLOCK_RESULT])
                return result
            items = proof["items"]
            calls = proof["trait_calls"]
            capacity = loot_capacity(crate)
            if (not proof["invoked"] or not proof["looted"] or not valid_loot_trait_calls(crate, calls)
                    or proof["item_count"] != len(items) or len(items) > capacity
                    or len({item["actor"] for item in items}) != len(items)
                    or any(not valid_guid(item.get("guid")) for item in items)
                    or len({item["guid"] for item in items}) != len(items)
                    or proof["escrow_slot"] != arguments[0]):
                raise RuntimeError("The exact stock crate roll did not verify")
            if fixture_nonempty and (proof["status"] != FORCED_ROLL_STATUS or not items or proof["trait_calls"] != 3):
                raise RuntimeError("The exact staged stock stream draw did not produce a retained item")
            if (proof.get("memory_escrow") is True) != memory_escrow:
                raise RuntimeError("The stock crate roll did not verify its requested save mode")
            if memory_escrow:
                frame = {"slot": arguments[0], "map": area, "pending": True}
            else:
                frame = await asyncio.to_thread(native_host_save.record, host.native_profile, arguments[0], area,
                                                flush=True)
            roll = {"id": operation_id, "map": area, "crate": crate, "opener": character,
                    "lucky_looter": proof["lucky_looter"], "trait_source": proof["trait_source"],
                    "trait_calls": proof["trait_calls"], "items": items, "escrow": frame}
            if lid := random_lid(crate):
                settled = await _settled_native_lid(bridge, snapshot, crate, lid)
                roll[lid[0]] = settled["native_loot_crates"][crate][lid[0]]
                host.last_game, host.last_seen = settled, time.monotonic()
            candidate = party.load_party(host.party)
            if crate in candidate.get(KEY_UNLOCKS, {}):
                del candidate[KEY_UNLOCKS][crate]
            candidate.setdefault(STATE_KEY, {})[crate] = roll
            result = {"native_loot_roll": public_roll(roll)}
            if tool_receipt:
                request, result_key, tool = tool_receipt
                result[result_key] = tool
                receipts = candidate["receipts"].setdefault(character, [])
                receipts.append({"id": operation_id, "request": request, "result": result})
                del receipts[:-party.RECENT_ACTIONS]
            if memory_escrow:
                # The typed item escrow exists, but the world change belongs to the next stock checkpoint.
                host.pending_host_save = bool(host.pending_host_save)
            elif tool_receipt:
                revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
            else:
                revision = await host.checkpoint_native(bridge, candidate)
            host.party = candidate
            host.native_checkpoint_unsafe = False
            if memory_escrow:
                host.control.event("native_loot_roll_accepted", **result["native_loot_roll"])
            else:
                host.control.event("native_loot_roll_committed", revision=revision, **result["native_loot_roll"])
            if fixture_nonempty:
                result["native_loot_fixture"] = proof
            return result
        if action == "claim":
            if not proof["invoked"] or proof["escrow_slot"] != arguments[1]:
                raise RuntimeError("The exact stock crate item transfer did not verify")
            credit, merge = None, {}
            if item["class_path"] in CASH_RANGES:
                credit = _verified_cash_credit(proof, item, snapshot.get("native_wallet"))
            elif proof["status"] == "merged" and "cash" not in proof:
                merge = verified_stack_merge(proof.get("merge", {}), guid, item.get("ammo"))
            elif proof["status"] not in ("claimed", "already_claimed") or "cash" in proof or "merge" in proof:
                raise RuntimeError("The exact stock crate item transfer did not verify")

            candidate = party.load_party(host.party)
            claimed = next(row for row in candidate[STATE_KEY][crate]["items"] if row["guid"] == guid)
            claimed["claimed_by"] = character
            if credit:
                candidate["economy"].update(wallet=credit["after"], native_wallet_active=True)
                claimed["wallet_credit"] = credit
            result = {"native_loot_claim": {**claimed, **merge}, "native_loot_crate": crate}
            receipts = candidate["receipts"].setdefault(character, [])
            receipts.append({"id": operation_id, "request": fingerprint, "result": result})
            del receipts[:-party.RECENT_ACTIONS]
            deferred = defer_save and (character == "host" or proof.get("retained") is True)
            if deferred:
                if character == "host":
                    host.pending_host_save = bool(host.pending_host_save or merge)
                else:
                    host.queue_guest_save(character, pawn)
            else:
                revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}),
                                                        typed_inventory=bool(merge))
            host.party = candidate
            host.native_checkpoint_unsafe = False
            if deferred:
                host.control.event("native_loot_claim_accepted", operation_id=operation_id, **result["native_loot_claim"])
            else:
                host.control.event("native_loot_claim_committed", revision=revision, **result["native_loot_claim"])
            native_pickup_audio.committed(host, pawn, operation_id, claimed["class_path"], credit=credit)
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
