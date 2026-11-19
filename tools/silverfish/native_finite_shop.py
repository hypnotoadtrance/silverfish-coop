"""Settle the exact authored finite merchant against the shared stock wallet."""

import time

from . import party
from .native_gold import valid_guid
from .travel import OBSERVATORY


ACTOR = f"Shop_BP_C {OBSERVATORY}.Observitory_P:PersistentLevel.Shop_BP_C_2"
ACTIONS = ("native-finite-shop-list", "native-finite-shop-buy", "native-finite-shop-sale-quote", "native-finite-shop-sell")
MAX_PRICE = (1 << 31) - 1


def parse_summary(fields: list[str]) -> dict:
    return {"state": fields[1], "map": fields[2], "pawn": fields[3], "actor": fields[4],
            "wallet_before": int(fields[5]), "wallet_after": int(fields[6]),
            "merchant_cash_before": int(fields[7]), "merchant_cash_after": int(fields[8]),
            "count": int(fields[9]), "offers": []}


def parse_item(fields: list[str]) -> dict:
    return {"guid": fields[1], "class_path": fields[2], "quantity": int(fields[3]),
            "value": int(fields[4]), "price": int(fields[5])}


def matching_report(snapshot: dict, pawn: str, expected_state: str) -> dict:
    report = snapshot.get("native_finite_shop", {})
    offers = report.get("offers")
    if (snapshot.get("state") != "hosting" or snapshot.get("map") != OBSERVATORY
            or tuple(report.get(key) for key in ("state", "map", "pawn", "actor")) != (expected_state, OBSERVATORY, pawn, ACTOR)
            or type(offers) is not list or report.get("count") != len(offers) or len(offers) > 128
            or any(type(report.get(key)) is not int or not 0 <= report[key] <= MAX_PRICE for key in (
                "wallet_before", "wallet_after", "merchant_cash_before", "merchant_cash_after"))):
        raise RuntimeError("The stock finite merchant did not return the exact authoritative report")
    guids = set()
    for offer in offers:
        guid, path = offer.get("guid"), offer.get("class_path")
        if (not valid_guid(guid) or guid in guids or type(path) is not str or not path.startswith("/Game/")
                or len(path) > 512 or not 1 <= offer["quantity"] <= MAX_PRICE
                or not 0 <= offer["price"] <= MAX_PRICE or type(offer.get("value")) is not int):
            raise RuntimeError("The finite merchant returned an invalid stock offer")
        guids.add(guid)
    return report


async def perform(host, bridge, character: str, action: str, target: str | None, operation_id: str) -> dict:
    if (not host.native_zones or not host.native_guest or host.native_profile is None or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or host.last_game.get("map") != OBSERVATORY or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_finite_shop_unavailable")
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")
    member = host.native_recipients().get(character)
    if not member or not member["connected"] or host.party["wipe"] is not None or character in host.party["pending_spawns"]:
        raise party.RuleError("native_finite_shop_guest_unavailable")
    if host.party["players"][character]["downed"]:
        raise party.RuleError("downed")
    pawn = member["pawn"]
    listing_only = action == "native-finite-shop-list"
    quote_only = action == "native-finite-shop-sale-quote"
    sale = quote_only or action == "native-finite-shop-sell"
    if listing_only:
        if target is not None:
            raise party.RuleError("invalid_finite_shop_list_target")
    elif quote_only:
        if not valid_guid(target):
            raise party.RuleError("native_finite_shop_requires_guid")
        guid = target
    else:
        if not isinstance(target, str):
            raise party.RuleError("native_finite_shop_requires_guid_and_price")
        guid, separator, raw_price = target.partition("@")
        if (not separator or not valid_guid(guid) or not raw_price.isascii() or not raw_price.isdigit()
                or len(raw_price) > 10 or (price := int(raw_price)) > MAX_PRICE):
            raise party.RuleError("native_finite_shop_requires_guid_and_price")
        fingerprint = [action, guid, price]
        for receipt in host.party["receipts"].get(character, []):
            if receipt["id"] == operation_id:
                if receipt["request"] != fingerprint:
                    raise party.RuleError("operation_id_conflict")
                return receipt["result"]
    if sale:
        preflight = "native-finite-shop-sale-quote"
        arguments = (pawn, guid)
    else:
        preflight = "native-finite-shop-list"
        arguments = (pawn,)
    try:
        snapshot = await bridge.command(preflight, *arguments, timeout=6)
    except RuntimeError as error:
        if str(error).startswith("finite_shop_refused_"):
            raise party.RuleError(str(error)) from error
        raise
    listing = matching_report(snapshot, pawn, "sale-quote" if sale else "listed")
    if (listing["wallet_before"] != listing["wallet_after"] or listing["merchant_cash_before"] != listing["merchant_cash_after"]
            or sale and (len(listing["offers"]) != 1 or listing["offers"][0]["guid"] != guid
                         or listing["offers"][0]["price"] != listing["offers"][0]["value"])):
        raise RuntimeError("The stock offer listing changed funds")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    if listing_only:
        return {"native_finite_shop": listing}
    if quote_only:
        return {"native_finite_sale_quote": listing}
    offer = next((row for row in listing["offers"] if row["guid"] == guid), None)
    if not offer:
        raise party.RuleError("native_finite_shop_item_unavailable")
    if price != offer["price"]:
        raise party.RuleError("native_finite_shop_quote_changed")
    wallet = listing["wallet_before"]
    merchant_cash = listing["merchant_cash_before"]
    if not sale and wallet < price:
        raise party.RuleError("insufficient_funds")
    if sale and merchant_cash < price:
        raise party.RuleError("insufficient_merchant_funds")
    wallet_change = price if sale else -price
    result_name = "native_finite_sale" if sale else "native_finite_purchase"
    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(action, pawn, guid, str(offer["quantity"]), str(price), str(wallet), timeout=7)
        except RuntimeError as error:
            if str(error).startswith("finite_shop_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = matching_report(snapshot, pawn, "sold" if sale else "purchased")
        if (proof["offers"] != [offer] or proof["wallet_before"] != wallet or proof["wallet_after"] != wallet + wallet_change
                or proof["merchant_cash_before"] != merchant_cash or proof["merchant_cash_after"] != merchant_cash - wallet_change):
            raise RuntimeError("The finite merchant did not verify the exact transfer and wallet changes")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        candidate = party.load_party(host.party)
        candidate["economy"].update(wallet=proof["wallet_after"], native_wallet_active=True)
        if sale:
            candidate["players"][character].get("native_inventory", {}).pop(guid, None)
        result = {result_name: proof}
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate)
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party = candidate
    if sale:
        host.native_pickup.forget_committed_row(character, guid)
    host.native_checkpoint_unsafe = False
    host.control.event(f"{result_name}_committed", character_id=character, revision=revision,
                       guid=guid, quantity=offer["quantity"], price=price, wallet=proof["wallet_after"])
    return result
