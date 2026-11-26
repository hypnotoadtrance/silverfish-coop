"""Share ordinary loose stock items by their placed world identity."""

import asyncio
import re
import time

from . import native_inventory, native_loot, native_pickup_audio, party
from .content_paths import item_class_name
from .native_gold import valid_guid
from .native_map_preflight import valid_campaign_package
from .native_guest_drop_record import valid_position

STATE = "native_loose_pickup_claims"
KEY = "native_loose_pickups"
ROW = "native-loose-pickup"
CAPABILITY = "native_loose_pickup_version"
VIEW_SCOPE = "native_loose_pickup_view_scope"
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
RESPAWN_HIDDEN_ROW = "native-loose-pickup-respawn-hidden"
RESPAWN_HIDDEN = "native_loose_pickup_respawn_hidden"
_PREFIX = "loose:"
_PATH = re.compile(r"(/[A-Za-z0-9_/]+)\.([A-Za-z0-9_]+):PersistentLevel\.[A-Za-z0-9_-]+")
_SLOT = re.compile(r"SFCoop_[0-9a-f]{32}")


def actor_map(path):
    if not isinstance(path, str) or len(path) > 568:
        return None
    parent, separator, actor = path.partition("|")
    raw = actor if separator else parent
    match = _PATH.fullmatch(raw) if len(raw) <= 384 else None
    if not match or not valid_campaign_package(match[1]):
        return None
    if separator:
        return parent if valid_campaign_package(parent) and parent != match[1] else None
    return match[1] if match[1].rsplit("/", 1)[-1] == match[2] else None


def target_path(value):
    if isinstance(value, str) and value.startswith(_PREFIX) and actor_map(value[len(_PREFIX):]):
        return value[len(_PREFIX):]
    return None


def _stock_source(area, path, class_path, mode):
    return (isinstance(area, str) and actor_map(path) == area and item_class_name(class_path)
            and type(mode) is int and mode in (0, 1, 2))


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
    if len(fields) not in (14, 15, 20) or fields[1] != area or phase != "hosting":
        raise ValueError("Invalid loose stock item transfer")
    _, _, actor, class_path, mode, pawn, slot, status, ok, applied, guid, amount, before, after = fields[:14]
    method = fields[14:15]
    if method and (method[0] not in ("disk", "memory") or method[0] == "memory" and class_path in native_loot.CASH_RANGES):
        raise ValueError("Invalid loose stock item transfer method")
    if (not _stock_source(area, actor, class_path, int(mode)) or not _SLOT.fullmatch(slot)
            or ok not in ("0", "1") or applied not in ("0", "1") or (guid != "-" and not valid_guid(guid))
            or len(status) > 256 or not all(value.isascii() and value.isdecimal() and len(value) < 11
                                            for value in (amount, before, after))):
        raise ValueError("Invalid loose stock item transfer receipt")
    proof = dict(map=area, actor=actor, class_path=class_path, mode=int(mode), pawn=pawn, slot=slot,
                 status=status, ok=ok == "1", applied=applied == "1", guid=guid,
                 amount=int(amount), before=int(before), after=int(after), memory=method == ["memory"])
    if status == "merged":
        if (len(fields) != 20 or not proof["memory"] or (ok, applied) != ("1", "1")
                or class_path in native_loot.CASH_RANGES or any(proof[key] for key in ("amount", "before", "after"))):
            raise ValueError("Invalid loose stock stack merge receipt")
        proof["merge"] = native_loot.parse_stack_merge(fields[15:])
        if proof["merge"]["guid"] != guid:
            raise ValueError("Loose stock stack merge source changed")
    elif len(fields) == 20:
        raise ValueError("Unexpected loose stock stack merge receipt")
    return proof


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
        if "stack_merge" in row or "inventory_guid" in row:
            merge = row.get("stack_merge")
            if (row["class_path"] in native_loot.CASH_RANGES or type(merge) is not dict
                    or merge.keys() != {"amount", "before", "after"}):
                raise ValueError("Invalid placed stock pickup stack merge")
            try:
                native_loot.verified_stack_merge(
                    dict(merge, guid=row["guid"], inventory_guid=row.get("inventory_guid")), row["guid"], merge["amount"],
                )
            except RuntimeError as error:
                raise ValueError("Invalid placed stock pickup stack merge") from error


class Visibility:
    def __init__(self):
        self.epoch = None
        self.replenished = set()
        self.next_check = 0.0
        self.streamed = PickupView("hosting")

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
                   if row["map"] == game.get("map") and row["mode"] in (0, 1) and row["epoch"] != epoch
                   and path not in self.replenished]
        for index in range(0, len(pending), 64):
            batch = pending[index:index + 64]
            snapshot = await bridge.command(CHECK, *batch, timeout=4)
            if host.zone_epoch != epoch or host.transitioning:
                return
            observed = snapshot.get(CHECK_KEY, {})
            if (snapshot.get("state"), snapshot.get("map")) != ("hosting", game.get("map")) or observed.keys() != set(batch):
                raise RuntimeError("The placed stock replenishment reply did not match the requested map and sources")
            self.replenished.update(path for path, available in observed.items() if available)
            host.last_game, host.last_seen = snapshot, time.monotonic()
        await self.streamed.project(host, bridge, [path for path in self.hidden(host) if "|" in path])

    def hidden(self, host):
        area, epoch = host.last_game.get("map"), host.zone_epoch
        replenished = self.replenished if self.epoch == epoch else ()
        claimed = {path for path, row in host.party.get(STATE, {}).items()
                   if row["map"] == area and (row["epoch"] == epoch or row["mode"] == 2 or path not in replenished)}
        claimed.update(host.last_game.get(RESPAWN_HIDDEN, ()))
        return sorted(claimed)


class PickupView:
    def __init__(self, phase="connected"):
        self.phase = phase
        self.scope = None
        self.hidden = set()

    async def project(self, service, bridge, hidden):
        game = service.last_game
        area, generation = game.get("map"), game.get(VIEW_SCOPE)
        if (game.get(CAPABILITY) != 1 or game.get("state") != self.phase
                or type(generation) is not int or generation <= 0 or not isinstance(hidden, list)
                or any(actor_map(path) != area for path in hidden)):
            return
        scope = (area, generation)
        if self.scope != scope:
            self.scope = scope
            self.hidden.clear()

        desired = set(hidden)
        updates = (("hide", sorted(desired - self.hidden)), ("show", sorted(self.hidden - desired)))
        for mode, paths in updates:
            for index in range(0, len(paths), 64):
                batch = paths[index:index + 64]
                reply = await bridge.command(PROJECT, str(generation), mode, *batch, timeout=4)
                if (reply.get("map"), reply.get("state"), reply.get(VIEW_SCOPE)) != (area, self.phase, generation):
                    raise RuntimeError("The loose pickup projection changed worlds")
                if mode == "hide":
                    self.hidden.update(batch)
                else:
                    self.hidden.difference_update(batch)
                service.last_game = reply


async def refresh_claimed_stock_view(client):
    client.guest_ui.queue_stock_inventory(client)
    try:
        await client.loose_pickup_view.project(client, client.bridge, client.gameplay.get(STATE))
    except (OSError, RuntimeError, ValueError) as error:
        client.control.event("native_loose_pickup_view_refresh_unavailable", reason=str(error))


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
    retained = character == "host" or await native_inventory.retain_guest_save(host, bridge, character, pawn)
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
        is_cash = row["class_path"] in native_loot.CASH_RANGES
        deferred = retained and (proof.get("memory") or is_cash)
        if is_cash:
            credit = native_loot._verified_cash_credit(
                {"status": proof["status"], "cash": proof}, dict(row, guid="-"), snapshot.get("native_wallet"))
            row["wallet_credit"] = credit
        else:
            if proof["status"] not in ("claimed", "merged") or not valid_guid(proof["guid"]):
                raise RuntimeError("The placed stock item did not transfer")
            row["guid"] = proof["guid"]
            if proof["status"] == "merged":
                if not proof.get("memory"):
                    raise RuntimeError("The placed stock stack merge requires a typed inventory checkpoint")
                merge = proof.get("merge", {})
                row.update(native_loot.verified_stack_merge(merge, proof["guid"], merge.get("amount")))
            if not proof.get("memory"):
                await asyncio.to_thread(native_host_save.record, host.native_profile, slot, area, flush=True)
        if deferred:
            candidate = native_loot.copy_pending_loot(host.party, STATE, character=character, cash=is_cash)
        else:
            candidate = party.load_party(host.party)
        if is_cash:
            candidate["economy"].update(wallet=credit["after"], native_wallet_active=True)
        candidate.setdefault(STATE, {})[path] = row
        result = {PROOF: row}
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        native_loot.remember_claim_snapshot(host, bridge, snapshot)
        revision = None
        if not deferred:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}),
                                                    typed_inventory=proof.get("memory", False))
        elif character == "host":
            host.pending_host_save = bool(host.pending_host_save or proof.get("memory"))
        host.party = candidate
        host.loose_pickups.replenished.discard(path)
        host.native_checkpoint_unsafe = False
        host.control.event("native_loose_pickup_claimed", revision=revision, operation_id=operation,
                           escrow="memory" if proof.get("memory") else "disk",
                           bridge_reply_bytes=snapshot.get(native_loot.CLAIM_REPLY_BYTES), **row)
        native_pickup_audio.committed(host, pawn, operation, row["class_path"], credit=row.get("wallet_credit"))
        return result
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True
