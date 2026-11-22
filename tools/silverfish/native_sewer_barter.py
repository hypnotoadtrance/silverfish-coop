"""Read and commit the Sewer Thing's stock offers for one authenticated character."""

import asyncio
import re
import time

from . import party
from .native_gold import valid_guid
from .travel import CISTERN


OFFERS = "native-sewer-offers"
OFFER_ROW = "native-sewer-offer"
OFFERS_KEY = "native_sewer_offers"
ACTION = "native-sewer-trade"
DEBIT_ROW = "native-sewer-debit"
TRADE_KEY = "native_sewer_trade"
FIXTURE = "native-sewer-mushroom-fixture"
FIXTURE_KEY = "native_sewer_mushroom_fixture"
MUSHROOM = "/Game/Classes/Items/Item_BrownMushroom.Item_BrownMushroom_C"
COUNT = 26
PREFIX = CISTERN + ".Cistern_P:PersistentLevel."
POST = "TradePost_BP_C " + PREFIX + "TradePost_BP_C_1"
_ORDINAL = re.compile(r"(?:[1-9]|1[0-9]|2[0-6])")
_PAWN = re.compile(re.escape("Player_FP_C " + PREFIX) + r"Player_FP_C_[0-9]+")
_CLASS = re.compile(r"/Game/Classes/(?:[A-Za-z0-9_-]+/)*([A-Za-z0-9_-]+)\.\1_C")


def _number(word: str, minimum: int, maximum: int) -> int:
    value = int(word)
    if str(value) != word or not minimum <= value <= maximum:
        raise ValueError("Invalid Sewer Thing trade count")
    return value


def _item(path: str, count: str) -> dict:
    if len(path) > 300 or not _CLASS.fullmatch(path):
        raise ValueError("Invalid Sewer Thing trade item")
    return {"class_path": path, "quantity": _number(count, 1, 100)}


def parse(header: list[str], rows: list[list[str]]) -> tuple[str, dict]:
    if len(header) < 4 or header[1] != CISTERN or not _PAWN.fullmatch(header[2]) or header[3] != POST:
        raise ValueError("Invalid Sewer Thing barter scope")
    common = {"map": CISTERN, "pawn": header[2], "post": POST}
    if header[0] == FIXTURE and len(header) == 7 and not rows and valid_guid(header[4]) and header[5:] == [MUSHROOM, "1"]:
        return FIXTURE_KEY, {**common, "guid": header[4], **_item(header[5], header[6])}
    if header[0] == OFFERS and len(header) == 5 and header[4] == str(COUNT) and len(rows) == COUNT:
        offers = []
        for index, row in enumerate(rows, 1):
            if (len(row) not in (9, 11, 13) or row[0] != OFFER_ROW or row[1] != str(index)
                    or row[2] not in ("0", "1") or row[3] not in ("0", "1") or not 1 <= len(row[-1]) <= 500):
                raise ValueError("Invalid Sewer Thing stock offer")
            receive = _item(row[4], row[5])
            give = [_item(row[offset], row[offset + 1]) for offset in range(6, len(row) - 1, 2)]
            if len({payment["class_path"] for payment in give}) != len(give):
                raise ValueError("Repeated Sewer Thing payment class")
            offers.append(dict(ordinal=index, label=row[-1], give=give, receive=[receive],
                               affordable=row[2] == "1", hidden=row[3] == "1"))
        return OFFERS_KEY, {**common, "offers": offers}
    if (header[0] != ACTION or len(header) != 11 or not _ORDINAL.fullmatch(header[4])
            or not valid_guid(header[5]) or header[8] != header[9] or _number(header[10], 1, 32) != len(rows)):
        raise ValueError("Invalid Sewer Thing exchange report")
    reward = {"guid": header[5], **_item(header[6], header[7])}
    seen = {reward["guid"]}
    spent = []
    for row in rows:
        if len(row) != 5 or row[0] != DEBIT_ROW or not valid_guid(row[1]) or row[1] in seen:
            raise ValueError("Invalid Sewer Thing payment receipt")
        seen.add(row[1])
        spent.append({"guid": row[1], **_item(row[2], row[3]), "remaining": _number(row[4], 0, 2**31 - 1)})
    wallet = _number(header[8], -(2**31), 2**31 - 1)
    return TRADE_KEY, {**common, "ordinal": int(header[4]), "spent": spent, "receive": reward,
                       "wallet_before": wallet, "wallet_after": wallet}



def projection_arguments(offers: list[dict]) -> tuple[str, ...]:
    if not isinstance(offers, list) or len(offers) != COUNT:
        raise ValueError("The Sewer Thing's stock projection is incomplete")

    def recipe(item: dict) -> tuple[str, str]:
        if not isinstance(item, dict) or type(item.get("quantity")) is not int or not isinstance(item.get("class_path"), str):
            raise ValueError("Invalid Sewer Thing stock item")
        checked = _item(item["class_path"], str(item["quantity"]))
        return checked["class_path"], str(checked["quantity"])

    rows = []
    for ordinal, offer in enumerate(offers, 1):
        if (not isinstance(offer, dict) or type(offer.get("ordinal")) is not int or offer["ordinal"] != ordinal
                or type(offer.get("affordable")) is not bool or type(offer.get("hidden")) is not bool
                or not isinstance(offer.get("give"), list) or not 1 <= len(offer["give"]) <= 3
                or not isinstance(offer.get("receive"), list) or len(offer["receive"]) != 1):
            raise ValueError("Invalid Sewer Thing stock recipe")
        payment = sorted(recipe(item) for item in offer["give"])
        if len({path for path, _ in payment}) != len(payment):
            raise ValueError("Repeated Sewer Thing stock payment")
        fields = (str(int(offer["affordable"])), str(int(offer["hidden"])), *recipe(offer["receive"][0]),
                  *(value for item in payment for value in item))
        rows.append("|".join(fields))
    return ("typed-v1", *rows)


def _member(host, character: str) -> tuple:
    sessions = host.dialogue_sessions
    if not sessions or CISTERN not in sessions.maps:
        raise party.RuleError("native_sewer_trade_unavailable")
    scope = sessions.member_scope(host, character)
    if scope[2] != CISTERN:
        raise party.RuleError("native_sewer_trade_unavailable")
    return scope


async def _report(host, bridge, scope: tuple, action: str, key: str, *arguments: str) -> dict:
    try:
        snapshot = await bridge.command(action, scope[3], *arguments, timeout=7)
    except RuntimeError as error:
        if str(error).startswith("sewer_trade_refused_"):
            raise party.RuleError(str(error)) from error
        raise
    proof = snapshot.get(key, {})
    if (snapshot.get("state"), snapshot.get("map"), proof.get("pawn"), proof.get("post")) != (
            "hosting", CISTERN, scope[3], POST):
        raise RuntimeError("The Sewer Thing trade did not verify the current guest and post")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def offers(host, bridge, character: str) -> dict:
    scope = _member(host, character)
    return {OFFERS_KEY: await _report(host, bridge, scope, OFFERS, OFFERS_KEY)}


async def exchange(host, bridge, character: str, target: str, operation_id: str) -> dict:
    if (not isinstance(target, str) or not _ORDINAL.fullmatch(target) or not isinstance(operation_id, str)
            or not party.REQUEST_ID.fullmatch(operation_id)):
        raise party.RuleError("native_sewer_trade_requires_stock_offer_ordinal")
    scope = _member(host, character)
    fingerprint = [ACTION, target, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]

    await host.checkpoint_native(bridge)
    if _member(host, character) != scope:
        raise party.RuleError("native_sewer_trade_guest_scope_changed")
    host.native_checkpoint_unsafe = True
    try:
        try:
            proof = await _report(host, bridge, scope, ACTION, TRADE_KEY, target)
        except party.RuleError:
            host.native_checkpoint_unsafe = False
            raise
        if proof["ordinal"] != int(target):
            raise RuntimeError("The Sewer Thing trade did not verify the requested stock offer")
        candidate = party.load_party(host.party)
        # The native frames now own the exact awarded and surviving stack rows.
        inventory = candidate["players"][character].get("native_inventory", {})
        changed = [payment["guid"] for payment in proof["spent"]] + [proof["receive"]["guid"]]
        for guid in changed:
            inventory.pop(guid, None)
        result = {TRADE_KEY: proof}
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
        host.party = candidate
        for guid in changed:
            host.native_pickup.forget_committed_row(character, guid)
        host.native_checkpoint_unsafe = False
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.control.event("native_sewer_trade_committed", character_id=character, revision=revision, **proof)
    return result
