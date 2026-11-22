"""Share ordinary loose stock items by their placed world identity."""

import asyncio
import re
import time

from . import native_loot, native_pickup_audio, party
from .native_gold import valid_guid
from .native_guest_drop_record import valid_position

STATE = "native_loose_pickup_claims"
KEY = "native_loose_pickups"
ROW = "native-loose-pickup"
CAPABILITY = "native_loose_pickup_version"
CLAIM = "native-loose-pickup-claim"
PROOF = "native_loose_pickup_claim"
FIXTURE = "native-loose-pickup-fixture"
NEARBY_FIXTURE = "native-loose-pickup-nearby-fixture"
STOCK_CARD_FIXTURE = "native-inventory-stock-nearby-fixture"
EXPIRE_FIXTURE = "native-loose-pickup-expire-fixture"
APPROACH = "native-loose-pickup-approach"
PROJECT = "native-loose-pickup-project"
CHECK = "native-loose-pickup-check"
CHECK_ROW = "native-loose-pickup-check-result"
CHECK_KEY = "native_loose_pickup_check"
_PREFIX = "loose:"
_CLASS = re.compile(r"/Game/Classes/(?:[A-Za-z0-9_]+/)*([A-Za-z0-9_]+)\.\1_C")
_PATH = re.compile(r"(/Game/Classes/Maps/[A-Za-z0-9_/]+)\.([A-Za-z0-9_]+):PersistentLevel\.[A-Za-z0-9_]+")
_SLOT = re.compile(r"SFCoop_[0-9a-f]{32}")


def actor_map(path):
    match = _PATH.fullmatch(path) if isinstance(path, str) and len(path) <= 384 else None
    if match and match[1].rsplit("/", 1)[-1] == match[2]:
        return match[1]
    return None


def target_path(value):
    if isinstance(value, str) and value.startswith(_PREFIX) and actor_map(value[len(_PREFIX):]):
        return value[len(_PREFIX):]
    return None


def _stock_source(area, path, class_path, mode):
    return (isinstance(area, str) and actor_map(path) == area and isinstance(class_path, str)
            and len(class_path) <= 256 and _CLASS.fullmatch(class_path) and type(mode) is int and mode in (0, 1, 2))


def parse(fields, phase, area):
    if len(fields) != 9 or fields[1] != area or phase not in ("hosting", "connected"):
        raise ValueError("Invalid loose stock item report")
    _, _, path, class_path, mode, state, *axes = fields
    location = list(map(float, axes))
    if (not _stock_source(area, path, class_path, int(mode)) or state not in ("available", "claimed")
            or not valid_position(location)):
        raise ValueError("Invalid loose stock item identity")
    return dict(map=area, actor=path, class_path=class_path, mode=int(mode), state=state, location=location,
                target=_PREFIX + path)


def parse_claim(fields, phase, area):
    if len(fields) not in (14, 15) or fields[1] != area or phase != "hosting":
        raise ValueError("Invalid loose stock item transfer")
    _, _, actor, class_path, mode, pawn, slot, status, ok, applied, guid, amount, before, after, *method = fields
    if method and (method[0] not in ("disk", "memory") or method[0] == "memory" and class_path in native_loot.CASH_RANGES):
        raise ValueError("Invalid loose stock item transfer method")
    if (not _stock_source(area, actor, class_path, int(mode)) or not _SLOT.fullmatch(slot)
            or ok not in ("0", "1") or applied not in ("0", "1") or (guid != "-" and not valid_guid(guid))
            or len(status) > 256 or not all(value.isascii() and value.isdecimal() and len(value) < 11
                                            for value in (amount, before, after))):
        raise ValueError("Invalid loose stock item transfer receipt")
    return dict(map=area, actor=actor, class_path=class_path, mode=int(mode), pawn=pawn, slot=slot,
                status=status, ok=ok == "1", applied=applied == "1", guid=guid,
                amount=int(amount), before=int(before), after=int(after), memory=method == ["memory"])


def validate_records(data, members):
    records = data.get(STATE, {})
    owners = set(members) | {"host"}
    if not isinstance(records, dict):
        raise ValueError("Invalid placed stock pickup ledger")
    for path, row in records.items():
        if (not isinstance(row, dict) or row.get("actor") != path
                or not _stock_source(row.get("map"), path, row.get("class_path"), row.get("mode"))
                or not isinstance(row.get("owner"), str) or row["owner"] not in owners
                or not isinstance(row.get("epoch"), str) or not party.REQUEST_ID.fullmatch(row["epoch"])
                or (row["class_path"] not in native_loot.CASH_RANGES and not valid_guid(row.get("guid")))):
            raise ValueError("Invalid placed stock pickup record")


class Visibility:
    def __init__(self):
        self.epoch = None
        self.replenished = set()
        self.next_check = 0.0

    async def refresh(self, host, bridge):
        game, epoch = host.last_game, host.zone_epoch
        if self.epoch != epoch:
            self.epoch, self.replenished, self.next_check = epoch, set(), 0.0
        now = time.monotonic()
        if (host.transitioning or host.native_checkpoint_unsafe or game.get(CAPABILITY) != 1
                or now < self.next_check):
            return
        self.next_check = now + 2
        pending = [path for path, row in host.party.get(STATE, {}).items()
                   if row["map"] == game.get("map") and row["mode"] == 1 and row["epoch"] != epoch
                   and path not in self.replenished]
        for index in range(0, len(pending), 64):
            batch = pending[index:index + 64]
            snapshot = await bridge.command(CHECK, *batch, timeout=4)
            observed = snapshot.get(CHECK_KEY, {})
            if (snapshot.get("state"), snapshot.get("map")) != ("hosting", game.get("map")) or observed.keys() != set(batch):
                raise RuntimeError("The placed stock replenishment reply did not match the requested map and sources")
            self.replenished.update(path for path, available in observed.items() if available)
            host.last_game, host.last_seen = snapshot, time.monotonic()

    def hidden(self, host):
        area, epoch = host.last_game.get("map"), host.zone_epoch
        replenished = self.replenished if self.epoch == epoch else ()
        return [path for path, row in host.party.get(STATE, {}).items()
                if row["map"] == area and (row["epoch"] == epoch or row["mode"] == 2
                                          or row["mode"] == 1 and path not in replenished)]


async def project(service, bridge, hidden):
    game = service.last_game
    area = game.get("map")
    if (game.get(CAPABILITY) != 1 or game.get("state") != "connected" or not isinstance(hidden, list)
            or any(actor_map(path) != area for path in hidden)):
        return
    hidden_set = set(hidden)
    nearby = game.get(KEY, {})
    updates = [("hide", [path for path, row in nearby.items() if path in hidden_set and row["state"] == "available"]),
               ("show", [path for path, row in nearby.items() if path not in hidden_set and row["state"] == "claimed"])]
    for mode, paths in updates:
        if not paths:
            continue
        reply = await bridge.command(PROJECT, mode, *paths, timeout=4)
        if (reply.get("map"), reply.get("state")) != (area, "connected"):
            raise RuntimeError("The loose pickup projection changed worlds")
        service.last_game = reply


async def refresh_claimed_stock_view(client):
    if client.last_game.get("native_inventory_view", {}).get("state") != "stock":
        return
    try:
        await project(client, client.bridge, client.gameplay.get(STATE))
    except (OSError, RuntimeError, ValueError) as error:
        client.control.event("native_loose_pickup_view_refresh_unavailable", reason=str(error))
    await client.guest_ui.refresh_stock_inventory(client)


async def expire_fixture(host, bridge, target):
    path = target_path(target)
    roll = host.party.get(STATE, {}).get(path, {})
    game = host.last_game
    if (not path or roll.get("mode") != 1 or game.get("state") != "hosting"
            or host.native_checkpoint_unsafe or host.stopping or host.transitioning):
        raise party.RuleError("loose_pickup_fixture_scope_unavailable")
    snapshot = await bridge.command(EXPIRE_FIXTURE, path, timeout=7)
    message = snapshot.get("ack", {}).get("message", "")
    match = re.fullmatch(r"stock temporary loot expired after ([1-9]|1[0-2]) daily updates", message)
    if not match or (snapshot.get("state"), snapshot.get("map")) != ("hosting", game.get("map")):
        raise RuntimeError("Stock loose item expiration did not verify")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    revision = await host.checkpoint_native(bridge)
    return {"actor": path, "days": int(match[1]), "revision": revision}


async def claim(host, bridge, character, requested, operation, *, local_input=False):
    from . import native_host_save

    target, _, epoch = requested.partition("@") if isinstance(requested, str) else ("", "", "")
    path = target_path(target)
    area = actor_map(path)
    game = host.last_game
    if (not area or area != game.get("map") or game.get("state") != "hosting" or game.get(CAPABILITY) != 1
            or not host.native_profile or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError("loose_pickup_scope_unavailable")
    if character != "host" and epoch != host.zone_epoch:
        raise party.RuleError("loose_pickup_stale_zone")
    fingerprint = [CLAIM, target, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if path in host.loose_pickups.hidden(host):
        raise party.RuleError("loose_pickup_already_claimed")
    pawn = native_loot._scope(host, character)
    player = host.party["players"].get(character)
    if not pawn or not player or player["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]:
        raise party.RuleError("loose_pickup_claimant_unavailable")
    slot = native_host_save.new_slot()
    host.native_checkpoint_unsafe = True
    try:
        try:
            receipt = ("stock-input",) if local_input and character == "host" else ()
            snapshot = await bridge.command(CLAIM, pawn, slot, path, *receipt, timeout=7)
        except RuntimeError as error:
            if str(error) == "loose_pickup_source_unavailable":
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(PROOF, {})
        if tuple(proof.get(key) for key in ("map", "actor", "pawn", "slot")) != (area, path, pawn, slot):
            raise RuntimeError("The placed stock item receipt did not match the claimant and source")
        if not proof["ok"] and not proof["applied"]:
            host.native_checkpoint_unsafe = False
            raise party.RuleError(proof["status"])
        if not (proof["ok"] and proof["applied"]):
            raise RuntimeError(proof["status"])
        row = {key: proof[key] for key in ("map", "actor", "class_path", "mode")}
        row.update(epoch=host.zone_epoch, owner=character)
        candidate = party.load_party(host.party)
        if row["class_path"] in native_loot.CASH_RANGES:
            credit = native_loot._verified_cash_credit(
                {"status": proof["status"], "cash": proof}, dict(row, guid="-"), snapshot.get("native_wallet"))
            row["wallet_credit"] = credit
            candidate["economy"].update(wallet=credit["after"], native_wallet_active=True)
        else:
            if proof["status"] != "claimed" or not valid_guid(proof["guid"]):
                raise RuntimeError("The placed stock item did not transfer")
            row["guid"] = proof["guid"]
            if not proof.get("memory"):
                await asyncio.to_thread(native_host_save.record, host.native_profile, slot, area, flush=True)
        candidate.setdefault(STATE, {})[path] = row
        result = {PROOF: row}
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        host.last_game, host.last_seen = snapshot, time.monotonic()
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}),
                                                typed_inventory=proof.get("memory", False))
        host.party = candidate
        host.loose_pickups.replenished.discard(path)
        host.native_checkpoint_unsafe = False
        host.control.event("native_loose_pickup_claimed", revision=revision, operation_id=operation,
                           escrow="memory" if proof.get("memory") else "disk", **row)
        native_pickup_audio.committed(host, pawn, operation, row["class_path"])
        return result
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True
