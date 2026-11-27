"""Share ordinary loose stock items by their placed world identity."""

import asyncio
import re
import time

from . import native_inventory, native_loot, native_pickup_audio, party
from .authored_loot import same_pose, valid_item_transform
from .content_paths import item_class_name, quest_id
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
QUEST_SOURCES = "native_authored_quest_sources"
QUEST_FRAME = "native_authored_quest_placements"
QUEST_HEADER = "native-authored-quest-sources"
QUEST_ROW = "native-authored-quest-placement"
QUEST_PROJECT = "native-quest-placement-project"
QUEST_CLEAR = "native-quest-placement-clear"
QUEST_RENEW = "native-quest-placement-renew"
QUEST_ACTIONS = (QUEST_PROJECT, QUEST_CLEAR, QUEST_RENEW)
QUEST_REPLY = "native-quest-placement-reply"
QUEST_LIMIT = 96
_QUEST_BATCH_LIMIT = 32  # The native renewal command and its memory bridge payload stay bounded.
_QUEST_RENEWAL = .65
_QUEST_MAX_AGE = 1.5
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


def _quest_source(area, path, class_path, quest):
    return (_stock_source(area, path, class_path, 2)
            and class_path not in native_loot.CASH_RANGES and quest_id(quest))


def _epoch(value):
    return isinstance(value, str) and value != "0" * 32 and party.REQUEST_ID.fullmatch(value) is not None


def parse_quest_placement(fields, phase, area):
    if len(fields) != 14 or phase != "hosting" or not area:
        raise ValueError("Invalid stock quest placement report")
    _, world, source, quest, class_path, *axes = fields
    values = list(map(float, axes))
    pose = {"position": values[:3], "rotation": values[3:6], "scale": values[6:]}
    if (world != f"{area}.{area.rsplit('/', 1)[-1]}" or not _quest_source(area, source, class_path, quest)
            or not valid_item_transform(pose)):
        raise ValueError("Invalid stock quest placement identity or pose")
    return source, {"quest": quest, "class_path": class_path, "transform": pose}


def parse_quest_census(rows, phase, area):
    header, *placements = rows
    if phase != "hosting" or not area or header != [QUEST_HEADER, "1"] or len(placements) > QUEST_LIMIT:
        raise ValueError("Invalid stock quest placement census")
    items = {}
    for fields in placements:
        if fields[0] != QUEST_ROW:
            raise ValueError("Repeated stock quest placement header")
        source, placement = parse_quest_placement(fields, phase, area)
        if source in items:
            raise ValueError("Repeated stock quest placement source")
        items[source] = placement
    return items


def parse_quest_reply(raw, request):
    fields = raw[:-1].split("\t") if len(raw) <= 1536 and raw.endswith("\n") and "\n" not in raw[:-1] else []
    if (len(request) < 4 or len(fields) != 9 or fields[:4] != [QUEST_REPLY, "1", request[0], "connected"]
            or fields[4] != actor_map(request[3]) or fields[5:7] != list(request[2:4])):
        raise RuntimeError("The stock quest projection reply did not match its local request")
    if fields[7] != "accepted":
        raise RuntimeError(fields[8] or "The stock quest projection was refused")
    if request[1] == QUEST_RENEW and (len(fields[8]) != len(request) - 4 or set(fields[8]) - {"0", "1"}):
        raise RuntimeError("The stock quest renewal reply did not match its sources")
    return {"state": "connected", "map": fields[4],
            "ack": {"id": fields[2], "result": fields[7], "message": fields[8]}}


def quest_frame(host, hidden):
    game, epoch = host.last_game, host.zone_epoch
    sources = game.get(QUEST_SOURCES)
    if (sources is None or game.get("state") != "hosting" or not _epoch(epoch) or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe or not host.native_profile
            or time.monotonic() - host.last_seen >= _QUEST_MAX_AGE):
        return None
    retired = set(hidden)
    return {"map": game["map"], "epoch": epoch, "items": {path: row for path, row in sources.items()
                                                           if path not in retired}}


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
    if len(fields) not in (14, 15, 16, 20) or fields[1] != area or phase != "hosting":
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
    if len(fields) == 16:
        quest = fields[15]
        if (proof["mode"] != 2 or not _quest_source(area, actor, class_path, quest)
                or any(proof[key] for key in ("amount", "before", "after"))
                or proof["ok"] and (not proof["memory"] or status != "claimed")):
            raise ValueError("Invalid quest placement pickup receipt")
        proof["quest"] = quest
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
        if "quest" in row and (row["mode"] != 2 or not _quest_source(row["map"], path, row["class_path"], row["quest"])
                               or "stack_merge" in row or "inventory_guid" in row):
            raise ValueError("Invalid stock quest placement pickup record")
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
        self.host_view = PickupView("hosting")

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
        claims = host.party.get(STATE, {})
        placed = [path for path in self.hidden(host) if "|" in path or "quest" in claims.get(path, {})]
        await self.host_view.project(host, bridge, placed)

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


class QuestPlacementView:
    def __init__(self):
        self.scope = None
        self.leases = {}
        self.refused = {}

    @staticmethod
    def _scope(client):
        game = client.last_game
        generation = game.get(VIEW_SCOPE)
        if (game.get("state") == "connected" and game.get(CAPABILITY) == 1 and client.host_session
                and type(generation) is int and generation > 0
                and _epoch(client.zone_epoch) and not client.needs_travel and not client.server_transitioning):
            return client.host_session, client.zone_epoch, game.get("map"), generation
        return None

    async def _apply(self, client, action, scope, path, *arguments):
        if self._scope(client) != scope or time.monotonic() - client.gameplay_seen >= _QUEST_MAX_AGE:
            return None
        _, epoch, area, _ = scope
        prior = self.refused.get(path)
        try:
            reply = await client.bridge.command(action, epoch, path, *arguments, timeout=1.5, update_status=False)
            if (reply.get("map"), reply.get("state")) != (area, "connected"):
                raise RuntimeError("The stock quest placement changed worlds")
        except (OSError, RuntimeError) as error:
            reason = str(error)
            self.refused[path] = time.monotonic(), reason
            if not prior or prior[1] != reason:
                client.control.event("native_quest_placement_projection_unavailable", actor=path, reason=reason)
            return None
        self.refused.pop(path, None)
        return reply if self._scope(client) == scope else None

    async def project(self, client):
        scope = self._scope(client)
        if self.scope != scope:
            self.scope = scope
            self.leases.clear()
            self.refused.clear()
        if scope is None or time.monotonic() - client.gameplay_seen >= _QUEST_MAX_AGE:
            return
        _, epoch, area, generation = scope
        frame = client.gameplay.get(QUEST_FRAME)
        if not isinstance(frame, dict) or (frame.get("map"), frame.get("epoch")) != (area, epoch):
            return
        items = frame.get("items")
        if not isinstance(items, dict) or len(items) > QUEST_LIMIT:
            return
        for path, row in items.items():
            if (not isinstance(row, dict) or not _quest_source(area, path, row.get("class_path"), row.get("quest"))
                    or not valid_item_transform(row.get("transform"))):
                return
        retired = client.gameplay.get(STATE)
        if not isinstance(retired, list) or any(not isinstance(path, str) for path in retired):
            return
        hidden = set(retired)
        active = items.keys() - hidden
        tracked = self.leases.keys() | active
        for path in self.refused.keys() - tracked:
            del self.refused[path]
        now = time.monotonic()
        due = []
        renew = []
        for path in tracked:
            refused_at, _ = self.refused.get(path, (-float("inf"), None))
            if now - refused_at < _QUEST_RENEWAL:
                continue
            if path not in active:
                due.append((refused_at, False, path))
                continue
            previous, at = self.leases.get(path, (None, -float("inf")))
            row = items[path]
            if (previous is None or previous["quest"] != row["quest"] or previous["class_path"] != row["class_path"]
                    or not same_pose(previous["transform"], row["transform"])):
                # An unavailable level still counts as a recent attempt. Other
                # placements should get a turn even if every refusal can retry.
                due.append((max(at, refused_at), True, path))
            elif now - at >= _QUEST_RENEWAL:
                renew.append(path)
        renew.sort()
        for index in range(0, len(renew), _QUEST_BATCH_LIMIT):
            batch = renew[index:index + _QUEST_BATCH_LIMIT]
            reply = await self._apply(client, QUEST_RENEW, scope, *batch, str(generation))
            if reply:
                renewed_at = time.monotonic()
                for path, renewed in zip(batch, reply["ack"]["message"], strict=True):
                    row, at = self.leases[path]
                    if renewed == "1":
                        self.leases[path] = row, renewed_at
                    else:
                        due.append((at, True, path))
        for _, show, path in sorted(due)[:4]:
            current = client.gameplay.get(QUEST_FRAME)
            if not isinstance(current, dict) or (current.get("map"), current.get("epoch")) != (area, epoch):
                break
            current_items = current.get("items")
            if not isinstance(current_items, dict):
                break
            claimed = path in (client.gameplay.get(STATE) or ())
            if not show:
                if (claimed or path not in current_items) and await self._apply(client, QUEST_CLEAR, scope, path):
                    self.leases.pop(path, None)
                continue
            if claimed or current_items.get(path) != items[path]:
                continue
            row = items[path]
            pose = row["transform"]
            axes = (*pose["position"], *pose["rotation"], *pose["scale"])
            if await self._apply(client, QUEST_PROJECT, scope, path, row["quest"], row["class_path"],
                                 *(str(axis) for axis in axes)):
                self.leases[path] = (row, time.monotonic())


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
    quest_placement = game.get(QUEST_SOURCES, {}).get(path)
    if quest_placement and not retained:
        raise party.RuleError("quest_placement_memory_retention_unavailable")
    host.native_checkpoint_unsafe = True
    try:
        try:
            receipt = ("stock-input",) if local_input and character == "host" else ()
            if quest_placement:
                receipt += ("memory-retained",)
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
        if quest_placement or "quest" in proof:
            if (not quest_placement or (proof.get("quest"), proof["class_path"]) !=
                    (quest_placement["quest"], quest_placement["class_path"]) or not proof.get("memory")):
                raise RuntimeError("The stock quest placement did not verify its in-memory claim")
            row["quest"] = proof["quest"]
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
