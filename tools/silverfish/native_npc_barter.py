"""Qualify a stock NPC trader and settle authenticated exchanges against the host save."""

import asyncio
import hashlib
import json
import math
import re
import time
import uuid

from . import party
from .content_paths import item_class_name
from .native_gold import valid_guid


OPEN = "native-npc-barter-open"
EXCHANGE = "native-npc-barter-exchange"
PROJECT = "native-npc-barter-project"
REFRESH = "native-npc-barter-refresh"
RECOVER = "native-npc-barter-recover"
CLOSE = "native-npc-barter-close"
STOCK = "native_npc_barter"
VIEW = "native_npc_barter_view"
STOCK_VERSION = "native_npc_barter_stock_version"
REFRESH_SECONDS = 3
HOST_TRADING = "barter_refused_host_is_trading"
REQUOTE_REFUSALS = frozenset({
    "barter_refused_stock_selection", "barter_refused_stock_bounds",
    "barter_refused_wallet", "barter_refused_native_balance",
})
MAX_ITEMS = 128
MAX_CASH = 2**31 - 1
REPUTATION_FLAG = re.compile(r"Rep_[A-Za-z0-9_ -]{1,120}")
MAP = re.compile(r"/Game/Classes/Maps(?:/[A-Za-z0-9_-]+)+")
PAWN_SUFFIX = re.compile(r"[0-9]+")
STOCK_DIGEST = re.compile(r"[0-9a-f]{64}")


def pawn_in_map(pawn: str, area: str) -> bool:
    if not isinstance(area, str) or not isinstance(pawn, str) or not MAP.fullmatch(area):
        return False
    level = area.rsplit("/", 1)[1]
    prefix = f"Player_FP_C {area}.{level}:PersistentLevel.Player_FP_C_"
    return pawn.startswith(prefix) and PAWN_SUFFIX.fullmatch(pawn[len(prefix):]) is not None


def integer(raw: str, minimum: int, maximum: int) -> int:
    value = int(raw)
    if str(value) != raw or not minimum <= value <= maximum:
        raise ValueError("Invalid NPC barter amount")
    return value


def item_key(path: str, guid: str) -> bool:
    return valid_guid(guid) and item_class_name(path, 512) is not None


def parse(frame: list[str], items: list[list[str]], moved: list[list[str]], preferences: list[list[str]],
          reputation: list[str] | None = None) -> dict:
    if len(frame) != 10 or not valid_guid(frame[2]) or not pawn_in_map(frame[3], frame[1]):
        raise ValueError("Invalid native NPC barter scope")
    seed = integer(frame[8], -(2**31), 2**31 - 1)
    minimum = float(frame[9])
    if not math.isfinite(minimum) or not 0 <= minimum <= 1000000:
        raise ValueError("Invalid minimum NPC barter weapon value")
    current = integer(frame[4], 0, 2**31 - 1)
    before = None if frame[7] == "-" else integer(frame[7], 0, 2**31 - 1)
    if frame[5] not in {"0", "1"} or integer(frame[6], 0, MAX_ITEMS) != len(items) or bool(moved) != (before is not None):
        raise ValueError("Incomplete native NPC barter frame")
    available, seen = [], set()
    for row in items:
        if len(row) != 8:
            raise ValueError("Invalid native NPC barter listing")
        _, guid, path, ammo, weight, value, never_drop, quote = row
        weight, quote = float(weight), float(quote)
        if (not item_key(path, guid) or guid in seen or never_drop not in {"0", "1"} or not math.isfinite(weight)
                or not 0 <= weight <= 100000 or not math.isfinite(quote) or not 0 <= quote <= 1000000):
            raise ValueError("Invalid native NPC barter item")
        seen.add(guid)
        available.append({"guid": guid, "class_path": path, "ammo": integer(ammo, -(2**31), 2**31 - 1),
                          "weight": weight, "value": integer(value, 0, 2**31 - 1), "never_drop": never_drop == "1",
                          "quote": quote})
    transfers, changed, sources = [], set(), set()
    for row in moved:
        if len(row) not in {5, 6}:
            raise ValueError("Invalid native NPC barter transfer")
        _, direction, source, *transfer_data = row
        partial = len(transfer_data) == 3
        guid, path, amount = transfer_data if partial else (source, *transfer_data)
        if (direction not in {"give", "receive"} or not item_key(path, guid) or not valid_guid(source)
                or source in sources or guid in changed or (guid in seen) != (direction == "give")
                or (partial and (source == guid or (source in seen) != (direction == "receive")))):
            raise ValueError("Invalid native NPC barter transfer")
        changed.add(guid)
        sources.add(source)
        transfer = {"direction": direction, "guid": guid, "class_path": path, "quantity": integer(amount, 1, 1000000)}
        if partial:
            transfer["source_guid"] = source
        transfers.append(transfer)
    taste = {}
    if len(preferences) > 256:
        raise ValueError("The NPC barter preference list is too large")
    for row in preferences:
        if len(row) != 3 or not item_class_name(row[1], 512) or row[1] in taste:
            raise ValueError("Invalid native NPC barter preference")
        value = float(row[2])
        if not math.isfinite(value) or not 0 < value <= 100:
            raise ValueError("Invalid native NPC barter preference")
        taste[row[1]] = value
    proof = {"map": frame[1], "trader": frame[2], "pawn": frame[3], "cash": current, "accepts_cash": frame[5] == "1",
             "stock": available, "cash_before": before, "transfers": transfers, "seed": seed, "preferences": taste,
             "minimum_weapon_value": minimum}
    if reputation is not None:
        if (len(reputation) != 4 or not any(row["direction"] == "give" for row in transfers)
                or not REPUTATION_FLAG.fullmatch(reputation[1])):
            raise ValueError("Invalid native NPC barter reputation")
        prior = integer(reputation[2], -(2**31), 2**31 - 1)
        after = integer(reputation[3], prior + 1, 2**31 - 1)
        proof["reputation"] = {"flag": reputation[1], "before": prior, "after": after}
    return proof


def selection(encoded: str) -> dict[str, int]:
    selected = {}
    if encoded not in ("", "-"):
        for piece in encoded.split(","):
            guid, colon, amount = piece.partition(":")
            if not colon or not valid_guid(guid) or guid in selected or len(selected) >= 12:
                raise ValueError("Invalid NPC barter selection")
            selected[guid] = integer(amount, 1, 1000000)
    return selected


def parse_view(fields: list[str]) -> dict:
    if len(fields) != 9 or not valid_guid(fields[5]) or not pawn_in_map(fields[2], fields[1]):
        raise ValueError("Invalid native barter screen scope")
    mode = fields[4]
    if mode not in {"opening", "ready", "confirming", "closed"}:
        raise ValueError("Invalid native barter screen state")
    cash = integer(fields[8], 0, MAX_CASH)
    if mode == "confirming":
        give, receive = selection(fields[6]), selection(fields[7])
        if not give and not (receive and cash):
            raise ValueError("An NPC barter exchange needs an item offer or cash payment for an item")
    elif fields[6:] != ["-", "-", "0"]:
        raise ValueError("The stock barter view returned an unexpected trade")
    return {"map": fields[1], "pawn": fields[2], "sequence": integer(fields[3], 1, 2**53 - 1),
            "state": mode, "trader": fields[5], "give": fields[6], "receive": fields[7], "cash": cash}


def _scope(host, character: str) -> tuple[str, str, str]:
    area = host.last_game.get("map")
    member = host.native_recipients().get(character)
    player = host.party["players"].get(character)
    if (not member or not member["connected"] or not player or player["downed"] or host.transitioning or host.stopping
            or host.native_checkpoint_unsafe or host.party.get("wipe") is not None or character in host.party["pending_spawns"]
            or host.last_game.get("state") != "hosting" or time.monotonic() - host.last_seen > 3
            or not host.zone_epoch or not pawn_in_map(member.get("pawn"), area)):
        raise party.RuleError("native_npc_barter_unavailable")
    return host.zone_epoch, area, member["pawn"]


async def _report(host, bridge, command: str, area: str, pawn: str, trader: str, *args: str) -> dict:
    try:
        snapshot = await bridge.command(command, pawn, trader, *args, timeout=7)
    except RuntimeError as error:
        if str(error).startswith("barter_refused_"):
            raise party.RuleError(str(error)) from error
        raise
    report = snapshot.get(STOCK)
    if (snapshot.get("state") != "hosting" or snapshot.get("map") != area or not report
            or report["trader"] != trader or report["pawn"] != pawn):
        raise RuntimeError("The NPC did not verify its exact native barter stock")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return report


def stock_version(report: dict) -> str:
    fields = [report[key] for key in ("stock", "cash", "accepts_cash", "seed", "preferences", "minimum_weapon_value")]
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def perform(host, bridge, character: str, action: str, target: str | None, operation: str) -> dict:
    scope = _scope(host, character)
    if not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("invalid_operation_id")
    if not isinstance(target, str) or len(target) > 1150:
        raise party.RuleError("native_npc_barter_invalid_trader")
    parts = target.split("@")
    if len(parts) not in ((2, 3) if action == OPEN else (5,)) or parts[0] != scope[0] or not valid_guid(parts[1]):
        raise party.RuleError("native_npc_barter_invalid_trader")
    _, trader, *proposal = parts
    if action == OPEN:
        known = proposal[0] if proposal else None
        if known is not None and not STOCK_DIGEST.fullmatch(known):
            raise party.RuleError("native_npc_barter_invalid_stock_version")
        report = await _report(host, bridge, OPEN, scope[1], scope[2], trader)
        version = stock_version(report)
        return {STOCK_VERSION: version} if version == known else {STOCK: report, STOCK_VERSION: version}
    payment, offered, wanted = proposal
    cash = integer(payment, 0, MAX_CASH)
    expected = {"give": selection(offered), "receive": selection(wanted)}
    if not expected["give"] and not (expected["receive"] and cash):
        raise party.RuleError("native_npc_barter_invalid_exchange")
    fingerprint = [EXCHANGE, target, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    from .native_inventory import retain_guest_save

    defer_save = await retain_guest_save(host, bridge, character, scope[2])
    if not defer_save:
        await host.checkpoint_native(bridge)
    if _scope(host, character) != scope:
        raise party.RuleError("native_npc_barter_guest_scope_changed")
    host.native_checkpoint_unsafe = True
    try:
        try:
            result = await _report(host, bridge, EXCHANGE, scope[1], scope[2], trader, offered, wanted, payment)
        except party.RuleError:
            host.native_checkpoint_unsafe = False
            raise
        moved = {direction: {row.get("source_guid", row["guid"]): row["quantity"]
                             for row in result["transfers"] if row["direction"] == direction}
                 for direction in expected}
        if moved != expected or result["cash_before"] != result["cash"] + cash:
            raise RuntimeError("The NPC barter did not verify the exact transfer and shared funds")
        before = party.load_party(host.party)
        inventory = before["players"][character].get("native_inventory", {})
        detached = {row.get("source_guid", row["guid"]) if row["direction"] == "give" else row["guid"]
                    for row in result["transfers"]}
        for guid in detached:
            inventory.pop(guid, None)
        recorded = before["receipts"].setdefault(character, [])
        proof = {STOCK: result}
        recorded.append({"id": operation, "request": fingerprint, "result": proof})
        del recorded[:-party.RECENT_ACTIONS]
        revision = None
        if not defer_save:
            revision = await host.checkpoint_native(bridge, before, require_guest_frames=frozenset({character}))
        host.party = before
        for guid in detached:
            host.native_pickup.forget_committed_row(character, guid)
        host.native_checkpoint_unsafe = False
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.control.event("native_npc_barter_committed", character_id=character, revision=revision, **result)
    return proof


class View:
    def __init__(self):
        self.latest = 0
        self.scope = None
        self.version = None
        self.next_refresh = 0.0
        self.refresh_task = None

    def cancel_refresh(self):
        if self.refresh_task is not None:
            self.refresh_task.cancel()
            self.refresh_task = None

    async def read_stock(self, client, target):
        try:
            return await client.gameplay_action(OPEN, uuid.uuid4().hex, target)
        except (OSError, RuntimeError, ValueError):
            return None

    async def sync(self, client, active: bool):
        state = client.last_game.get(VIEW, {})
        sequence = state.get("sequence", 0)
        mode = state.get("state")
        if (not active or not mode or not client.host_session or not client.zone_epoch
                or state.get("map") != client.last_game.get("map") or not valid_guid(state.get("trader"))
                or state.get("pawn") != client.last_game.get("pawns", {}).get("local", {}).get("name")):
            self.cancel_refresh()
            self.version = None
            return
        epoch = client.zone_epoch
        trader = state["trader"]
        scope = client.host_session, epoch, client.bridge.input_namespace
        if self.scope != scope:
            self.cancel_refresh()
            self.scope, self.latest, self.version = scope, 0, None
        refresh = mode == "ready"
        action = {"opening": OPEN, "confirming": EXCHANGE}.get(mode)
        target = f"{epoch}@{trader}"
        if refresh:
            if self.refresh_task is None:
                if self.version and time.monotonic() >= self.next_refresh:
                    self.next_refresh = time.monotonic() + REFRESH_SECONDS
                    self.refresh_task = asyncio.create_task(self.read_stock(client, f"{target}@{self.version}"))
                return
            if not self.refresh_task.done():
                return
            reply = self.refresh_task.result()
            self.refresh_task = None
            if reply is None:
                return
        else:
            self.cancel_refresh()
            if not action or sequence <= self.latest:
                return
            self.latest = sequence
            if action == EXCHANGE:
                target = "@".join((target, str(state["cash"]), state["give"], state["receive"]))
            operation = uuid.uuid5(client.bridge.input_namespace, f"{client.host_session}:{epoch}:{trader}:{sequence}").hex
            self.next_refresh = time.monotonic() + REFRESH_SECONDS
        try:
            refusal = None
            if not refresh:
                try:
                    reply = await client.gameplay_action(action, operation, target)
                except ValueError as error:
                    if action != EXCHANGE:
                        raise
                    refusal = str(error)
                    if refusal == HOST_TRADING:
                        client.last_game = await client.bridge.command(RECOVER, str(sequence), trader, timeout=4)
                        if client.last_game.get("ack", {}).get("message") != "exact stock NPC barter draft kept":
                            raise RuntimeError("The stock NPC barter draft could not resume")
                        client.control.event("native_npc_barter_refused", action=action, sequence=sequence,
                                             reason=refusal, draft_kept=True)
                        return
                    if refusal not in REQUOTE_REFUSALS:
                        raise
                    reply = await self.read_stock(client, f"{epoch}@{trader}")
                    if reply is None:
                        raise
            version = reply.get(STOCK_VERSION)
            if version is not None and (not isinstance(version, str) or not STOCK_DIGEST.fullmatch(version)):
                raise ValueError("The host NPC returned an invalid stock version")
            if refresh and version == self.version and STOCK not in reply:
                return
            catalog = reply[STOCK]
            if catalog["trader"] != trader or catalog["map"] != state["map"] or len(catalog["stock"]) > MAX_ITEMS:
                raise ValueError("The host NPC returned invalid barter stock")
            items = ["|".join((row["guid"], row["class_path"], str(row["ammo"]), str(row["weight"]),
                                str(row["value"]), str(int(row["never_drop"])))) for row in catalog["stock"]]
            if action == EXCHANGE and refusal is None:
                client.guest_ui.digest = "-"
                await client.guest_ui.refresh(client)
            preferences = [f"p|{path}|{weight}" for path, weight in catalog["preferences"].items()]
            client.last_game = await client.bridge.command(
                REFRESH if refresh else RECOVER if refusal else PROJECT, str(sequence), trader,
                str(catalog["cash"]), str(catalog["seed"]),
                str(catalog["minimum_weapon_value"]), str(int(catalog["accepts_cash"])), *items, *preferences, timeout=4)
            if not refresh or client.last_game.get("ack", {}).get("message") == "exact stock NPC barter screen shown":
                self.version = version or stock_version(catalog)
                if refusal:
                    client.control.event("native_npc_barter_refused", action=action, sequence=sequence,
                                         reason=refusal, catalogue_restored=True)
                if refresh:
                    client.control.event("native_npc_barter_stock_refreshed", trader=trader, entries=len(items), cash=catalog["cash"])
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            if refresh:
                return
            self.version = None
            client.guest_ui.digest = "-"
            client.control.event("native_npc_barter_refused", action=action, sequence=sequence, reason=str(error))
            try:
                client.last_game = await client.bridge.command(CLOSE, str(sequence), timeout=3)
            except (OSError, RuntimeError, ValueError):
                pass
