"""Persist staged stock Bandage claims before projecting them into Unreal."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import re
import uuid

from . import party


CLASS_PATH = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
MAX_NATIVE_ITEMS = party.MAX_NATIVE_ITEMS
STAGED_SHOPS = ("Shop_BP_C_0", "Shop_BP_C_1")
_GUID = re.compile(r"[0-9A-F]{32}\Z")


@dataclass(frozen=True)
class _Binding:
    character: str
    pawn: str
    zone: str


@dataclass(frozen=True)
class _Source:
    zone: str
    guid: str
    actor: str | None = None


def _bound(game: dict, pawn: str) -> bool:
    members = game.get("native_guest_save_members")
    saved = members.get(pawn, {}) if members else game.get("native_guest_save") or {}
    return saved.get("state") == "bound" and saved.get("pawn") == pawn


def _bindings(host, *, connected: bool = False) -> dict[str, _Binding]:
    game = host.last_game
    if (not host.native_guest or not host.fixtures or host.stopping or host.transitioning
            or game.get("state") != "hosting"):
        return {}
    recipients = getattr(host, "native_recipients", None)
    if recipients is not None:
        bindings = {}
        for character, member in recipients().items():
            pawn = member.get("pawn")
            if pawn and (member["connected"] or not connected) and _bound(game, pawn):
                bindings[character] = _Binding(character, pawn, host.zone_epoch)
        return bindings

    # Paired host adapters expose one exact guest and the original save row.
    pawn = host.guest_pawn
    character = host.roles().get("guest")
    if (not host.guest or not character or not pawn or
            game.get("remote_clients") != 1 or host.guest["character_id"] != character or
            (connected and host.guest["disconnected"] is not None) or
            game.get("pawns", {}).get("guest", {}).get("name") != pawn or not _bound(game, pawn)):
        return {}
    return {character: _Binding(character, pawn, host.zone_epoch)}


def _source_name(value: object) -> bool:
    return (isinstance(value, str) and 16 < len(value) <= 512 and value.isprintable() and
            value.startswith("Item_Bandage_C /Game/"))


def _row(snapshot: dict, state: str, binding: _Binding, guid: str, *, actor: str | None = None) -> dict:
    row = snapshot.get("native_pickup") or {}
    if (not _bound(snapshot, binding.pawn) or
            row.get("state") != state or row.get("guid") != guid or row.get("class_path") != CLASS_PATH or
            row.get("pawn") != binding.pawn or
            (actor is not None and row.get("actor") != actor)):
        raise RuntimeError("Native pickup was not verified by the exact bound guest")
    return row


def _payload(record: dict) -> dict:
    ammo, weight, value = record.get("ammo"), record.get("weight"), record.get("value")
    never_drop = record.get("never_drop")
    if (record.get("class_path") != CLASS_PATH or type(ammo) is not int or not -1 <= ammo <= 1_000_000 or
            type(weight) not in (int, float) or not 0 <= weight <= 1_000_000 or
            type(value) is not int or not 0 <= value <= 1_000_000_000 or type(never_drop) is not bool):
        raise ValueError("Invalid native pickup metadata")
    return {"class_path": CLASS_PATH, "ammo": ammo, "weight": weight, "value": value, "never_drop": never_drop}


def _confirmed_item(guid: str, record: dict) -> dict:
    return {"effect": "native_pickup", "target": record["source_actor"], "guid": guid, "item": _payload(record)}


def _shop_row(snapshot: dict, state: str, binding: _Binding, shop: str, guid: str) -> dict:
    row = snapshot.get("native_shop_purchase", {})
    if (not _bound(snapshot, binding.pawn) or row.get("state") != state or row.get("guid") != guid
            or row.get("shop") != shop or row.get("pawn") != binding.pawn
            or type(row.get("price")) is not int or not 0 <= row["price"] <= 1_000_000_000):
        raise RuntimeError("The live merchant did not verify this guest's purchase")
    return row


class NativePickup:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._source: _Source | None = None
        self._pending: tuple[_Binding, _Source] | None = None
        self._pending_shop: dict[_Binding, str] = {}
        self._recheck_pending = False
        self._projected: dict[_Binding, set[str]] = {}
        self._restore_offset = 0

    def projected(self, character_id: str | None = None) -> list[str]:
        return sorted({guid for binding, items in self._projected.items()
                       if character_id is None or binding.character == character_id for guid in items})

    def invalidate(self, character_id: str | None = None) -> None:
        """Clear transient projection tracking before the exact pawn receives a new Save."""
        for records in (self._projected, self._pending_shop):
            for binding in tuple(records):
                if character_id is None or binding.character == character_id:
                    del records[binding]
        if self._pending and (character_id is None or self._pending[0].character == character_id):
            self._recheck_pending = True

    def _track(self, host, bindings: dict[str, _Binding]) -> None:
        exact = set(bindings.values())
        for records in (self._projected, self._pending_shop):
            for previous in records.keys() - exact:
                del records[previous]
        if self._source and self._source.zone != host.zone_epoch:
            self._source = None
        if self._pending:
            if self._pending[1].zone != host.zone_epoch:
                self._pending = None
                self._recheck_pending = False
            elif self._pending[0] not in exact:
                self._recheck_pending = True

    def forget_committed_row(self, character: str, guid: str) -> None:
        """Leave this row to the detached save after a durable native transaction."""
        for binding, rows in self._projected.items():
            if binding.character == character:
                rows.discard(guid)
        for binding, pending in tuple(self._pending_shop.items()):
            if binding.character == character and pending == guid:
                del self._pending_shop[binding]
        if self._pending and self._pending[0].character == character and self._pending[1].guid == guid:
            if self._source == self._pending[1]:
                self._source = None
            self._pending = None
            self._recheck_pending = False

    @staticmethod
    def _claimant(host, actor: str | None) -> tuple[_Binding, dict]:
        bindings = _bindings(host, connected=True)
        binding = bindings.get(actor) if actor is not None else next(iter(bindings.values()), None)
        if not binding:
            raise party.RuleError("native_guest_unavailable")
        player = host.party["players"].get(binding.character)
        if not player:
            raise party.RuleError("native_guest_unavailable")
        return binding, player

    @staticmethod
    def _require_standing(host, binding: _Binding, player: dict) -> None:
        if binding.character in host.party["pending_spawns"] or host.party["wipe"] is not None:
            raise party.RuleError("native_guest_unavailable")
        if player["downed"]:
            raise party.RuleError("actor_downed")

    async def spawn(self, host, bridge, character_id: str | None = None) -> dict:
        async with self._lock:
            binding, player = self._claimant(host, character_id)
            self._track(host, _bindings(host))
            self._require_standing(host, binding, player)
            if len(player.get("native_inventory", {})) >= MAX_NATIVE_ITEMS:
                raise party.RuleError("native_inventory_full")
            if self._pending and self._pending[1].zone == binding.zone:
                raise party.RuleError("native_pickup_pending")
            if not self._source:
                self._source = _Source(binding.zone, uuid.uuid4().hex.upper())
            guid = self._source.guid
            snapshot = await bridge.command("native-pickup-spawn", binding.pawn, guid, timeout=4)
            row = _row(snapshot, "spawned", binding, guid)
            if not _source_name(row.get("actor")):
                raise RuntimeError("The native pickup did not expose a stock Bandage actor")
            self._source = _Source(binding.zone, guid, row["actor"])
            return {"native_pickup": dict(row)}

    async def _project(self, bridge, binding: _Binding, guid: str, record: dict) -> None:
        projected = self._projected.setdefault(binding, set())
        if guid in projected:
            return
        if not _GUID.fullmatch(guid) or guid == "0" * 32:
            raise ValueError("Invalid saved native pickup GUID")
        payload = _payload(record)
        if self._pending_shop.get(binding) == guid:
            snapshot = await bridge.command("native-shop-commit", binding.pawn, guid, timeout=4)
            committed = _shop_row(snapshot, "committed", binding, record["source_shop"], guid)
            if _payload(committed) != payload or committed["price"] != record["price"]:
                raise RuntimeError("The applied merchant item differs from the durable purchase")
            del self._pending_shop[binding]
            projected.add(guid)
            return
        pending_owner, source = self._pending or (None, None)
        owns_pending = pending_owner and pending_owner.character == binding.character and source.guid == guid
        if owns_pending and pending_owner == binding and not self._recheck_pending:
            snapshot = await bridge.command("native-pickup-commit", binding.pawn, guid, timeout=4)
            row = _row(snapshot, "committed", binding, guid, actor=source.actor)
            if _payload(row) != payload:
                raise RuntimeError("The applied native pickup does not match its saved item")
        else:
            source_actor = (record["source_actor"],) if "source_actor" in record else ()
            snapshot = await bridge.command(
                "native-pickup-restore", binding.pawn, guid, str(payload["ammo"]), str(payload["weight"]),
                str(payload["value"]), "1" if payload["never_drop"] else "0", *source_actor, timeout=4,
            )
            row = _row(snapshot, "restored", binding, guid)
            if _payload(row) != payload:
                raise RuntimeError("The restored native pickup does not match its saved item")
        if owns_pending:
            self._pending = None
            if self._source == source:
                self._source = None
            self._recheck_pending = False
        projected.add(guid)

    async def claim(self, host, bridge, actor_character_id: str, operation_id: str, actor_full_name: str) -> dict:
        if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
            raise party.RuleError("invalid_operation_id")
        if not _source_name(actor_full_name):
            raise party.RuleError("invalid_native_pickup_actor")

        async with self._lock:
            binding, player = self._claimant(host, actor_character_id)
            self._track(host, _bindings(host))
            inventory = player.get("native_inventory", {})
            for guid, record in inventory.items():
                if record.get("operation_id") == operation_id:
                    if record.get("source_actor") != actor_full_name:
                        raise party.RuleError("operation_id_conflict")
                    await self._project(bridge, binding, guid, record)
                    return _confirmed_item(guid, record)
            self._require_standing(host, binding, player)
            if any(entry["id"] == operation_id for entry in host.party["receipts"].get(binding.character, [])):
                raise party.RuleError("operation_id_conflict")
            if len(inventory) >= MAX_NATIVE_ITEMS:
                raise party.RuleError("native_inventory_full")

            source = self._source
            if not source or source.actor != actor_full_name:
                raise party.RuleError("native_pickup_unavailable")
            if self._pending and self._pending[1].guid == source.guid:
                raise party.RuleError("native_pickup_already_claimed")

            try:
                snapshot = await bridge.command(
                    "native-pickup-prepare", binding.pawn, source.actor, source.guid, timeout=4,
                )
                current, current_player = self._claimant(host, actor_character_id)
                if current != binding:
                    raise party.RuleError("native_guest_unavailable")
                self._require_standing(host, current, current_player)
                prepared = _row(snapshot, "prepared", binding, source.guid, actor=source.actor)
                record = {**_payload(prepared), "source_actor": source.actor, "operation_id": operation_id}
                result = _confirmed_item(source.guid, record)
                candidate = party.load_party(host.party)
                candidate["players"][binding.character].setdefault("native_inventory", {})[source.guid] = record
                receipts = candidate["receipts"].setdefault(binding.character, [])
                if any(entry["id"] == operation_id for entry in receipts):
                    raise party.RuleError("operation_id_conflict")
                receipts.append({"id": operation_id, "request": ["native-pickup", source.actor, None],
                                 "result": result})
                del receipts[:-party.RECENT_ACTIONS]
                revision = host.checkpoint(candidate)
            except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
                with suppress(OSError, RuntimeError, ValueError):
                    await bridge.command("native-pickup-abort", binding.pawn, source.guid, timeout=2)
                raise

            host.party = candidate
            self._pending = (binding, source)
            self._recheck_pending = False
            with suppress(OSError):
                host.control.event("native_pickup_saved", character_id=binding.character,
                                   operation_id=operation_id, revision=revision, **result)
            await self._project(bridge, binding, source.guid, record)
            return result

    async def buy(self, host, bridge, character: str, operation_id: str, shop: str) -> dict:
        if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
            raise party.RuleError("invalid_operation_id")
        if shop not in STAGED_SHOPS:
            raise party.RuleError("unknown_native_shop")
        async with self._lock:
            binding, player = self._claimant(host, character)
            self._track(host, _bindings(host))
            inventory = player.get("native_inventory", {})
            for guid, record in inventory.items():
                if record.get("operation_id") != operation_id:
                    continue
                if record.get("source_shop") != shop:
                    raise party.RuleError("operation_id_conflict")
                await self._project(bridge, binding, guid, record)
                return record["result"]
            self._require_standing(host, binding, player)
            if any(entry["id"] == operation_id for entry in host.party["receipts"].get(character, [])):
                raise party.RuleError("operation_id_conflict")
            if binding in self._pending_shop:
                raise party.RuleError("native_purchase_pending")
            if len(inventory) >= MAX_NATIVE_ITEMS:
                raise party.RuleError("native_inventory_full")

            guid = uuid.uuid4().hex.upper()
            try:
                snapshot = await bridge.command("native-shop-prepare", binding.pawn, shop, guid, timeout=4)
                current, current_player = self._claimant(host, character)
                if current != binding:
                    raise party.RuleError("native_guest_unavailable")
                self._require_standing(host, current, current_player)
                row = _shop_row(snapshot, "prepared", binding, shop, guid)
                candidate = party.load_party(host.party)
                price = row["price"]
                if candidate["economy"]["wallet"] < price:
                    raise party.RuleError("insufficient_funds")
                candidate["economy"]["wallet"] -= price
                candidate["economy"]["native_wallet_active"] = True
                item = _payload(row)
                result = {"effect": "native_shop_purchase", "shop": shop, "guid": guid, "item": item,
                          "price": price, "wallet": candidate["economy"]["wallet"]}
                record = {**item, "source_shop": shop, "operation_id": operation_id, "price": price, "result": result}
                candidate["players"][character].setdefault("native_inventory", {})[guid] = record
                receipts = candidate["receipts"].setdefault(character, [])
                receipts.append({"id": operation_id, "request": ["native-shop-buy", shop, None], "result": result})
                del receipts[:-party.RECENT_ACTIONS]
                revision = host.checkpoint(candidate)
            except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
                with suppress(OSError, RuntimeError, ValueError):
                    await bridge.command("native-shop-abort", binding.pawn, guid, timeout=2)
                raise

            host.party = candidate
            self._pending_shop[binding] = guid
            with suppress(OSError):
                host.control.event("native_purchase_saved", character_id=character,
                                   operation_id=operation_id, revision=revision, **result)
            await self._project(bridge, binding, guid, record)
            return result

    async def restore(self, host, bridge) -> None:
        """Project at most one missing native item per tick to keep the host responsive."""
        async with self._lock:
            bindings = _bindings(host)
            self._track(host, bindings)
            members = list(bindings.values())
            if not members:
                return
            start = self._restore_offset % len(members)
            for offset in range(len(members)):
                index = (start + offset) % len(members)
                self._restore_offset = index + 1
                if await self._restore_member(host, bridge, members[index]):
                    return

    async def _restore_member(self, host, bridge, binding: _Binding) -> bool:
        player = host.party["players"].get(binding.character, {})
        inventory = player.get("native_inventory", {})
        projected = self._projected.setdefault(binding, set())
        pending_owner, source = self._pending or (None, None)
        pickup_guid = source.guid if pending_owner and pending_owner.character == binding.character else None
        shop_guid = self._pending_shop.get(binding)
        pending = {guid for guid in (pickup_guid, shop_guid) if guid is not None}
        removed = (projected | pending) - inventory.keys()
        if removed:
            guid = min(removed)
            actor = (source.actor,) if guid == pickup_guid else ()
            snapshot = await bridge.command("native-pickup-remove", binding.pawn, guid, *actor, timeout=4)
            _row(snapshot, "removed", binding, guid)
            projected.discard(guid)
            if guid == pickup_guid:
                self._pending = None
                if self._source == source:
                    self._source = None
                self._recheck_pending = False
            if guid == shop_guid:
                with suppress(OSError, RuntimeError):
                    await bridge.command("native-shop-abort", binding.pawn, guid, timeout=2)
                del self._pending_shop[binding]
            return True

        guid = pickup_guid or shop_guid or next((item for item in inventory if item not in projected), None)
        if guid is None:
            return False
        await self._project(bridge, binding, guid, inventory[guid])
        return True
