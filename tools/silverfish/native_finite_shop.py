"""Settle the exact authored finite merchant against the shared stock wallet."""

import asyncio
import math
import time

from . import party
from .native_gold import valid_guid
from .native_guest_consumable import MRE_CLASS
from .native_tape_profiles import BLANK_CLASS
from .travel import OBSERVATORY, SCIENTIST_SHOP, SHOPKEEP


ACTOR = f"Shop_BP_C {OBSERVATORY}.Observitory_P:PersistentLevel.Shop_BP_C_2"
SCAV_BAR = "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P"
BLACK_MARKET = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
GUNSMITH = "/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P"
SMUGGLER = "/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P"
CAMERA = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
BALL_AMMO = "/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
MERCHANTS = {
    OBSERVATORY: ACTOR,
    SCAV_BAR: f"Shop_BP_C {SCAV_BAR}.Shop_ScavBar_P:PersistentLevel.Shop_BP_C_2",
    BLACK_MARKET: f"Shop_BP_C {BLACK_MARKET}.Shop_BlackMarket_01:PersistentLevel.Shop_BP_C_1",
    SCIENTIST_SHOP: f"Shop_BP_C {SCIENTIST_SHOP}.Shop_Scientist_01:PersistentLevel.Shop_BP_C_2",
    GUNSMITH: f"Shop_BP_C {GUNSMITH}.Shop_Gunsmith_P:PersistentLevel.Shop_BP_C_1",
    SHOPKEEP: f"Shop_BP_C {SHOPKEEP}.Shop_ShopKeep_P:PersistentLevel.Shop_BP_C_1",
    SMUGGLER: f"Shop_BP_C {SMUGGLER}.Shop_Smuggler_P:PersistentLevel.Shop_BP_C_1",
}
ACTIONS = ("native-finite-shop-list", "native-finite-shop-buy", "native-finite-shop-sale-quote", "native-finite-shop-sell")
MAX_PRICE = (1 << 31) - 1


def parse_summary(fields: list[str]) -> dict:
    return {"state": fields[1], "map": fields[2], "pawn": fields[3], "actor": fields[4],
            "wallet_before": int(fields[5]), "wallet_after": int(fields[6]),
            "merchant_cash_before": int(fields[7]), "merchant_cash_after": int(fields[8]),
            "count": int(fields[9]), "offers": []}


def parse_item(fields: list[str]) -> dict:
    item = {"guid": fields[1], "class_path": fields[2], "quantity": int(fields[3]),
            "value": int(fields[4]), "price": int(fields[5])}
    if len(fields) == 11:
        source, raw_ammo, raw_weight, never_drop, stacks = fields[6:]
        ammo, weight = int(raw_ammo), float(raw_weight)
        if (source not in {"shelf", "limited", "buyback", "backpack"} or not -2**31 <= ammo <= MAX_PRICE
                or not math.isfinite(weight) or not 0 <= weight <= 100000 or never_drop not in {"0", "1"}
                or stacks not in {"0", "1"} or item["quantity"] != (ammo if stacks == "1" else 1)):
            raise ValueError("The merchant returned an invalid stock inventory row")
        item.update(source=source, ammo=ammo, weight=weight, never_drop=never_drop == "1", stackable=stacks == "1")
    return item


def matching_report(snapshot: dict, area: str, pawn: str, expected_state: str, *, guest: bool) -> dict:
    report = snapshot.get("native_finite_shop", {})
    offers = report.get("offers")
    if (snapshot.get("state") != "hosting" or snapshot.get("map") != area
            or tuple(report.get(key) for key in ("state", "map", "pawn", "actor")) != (expected_state, area, pawn, MERCHANTS[area])
            or type(offers) is not list or report.get("count") != len(offers) or len(offers) > 128
            or any(type(report.get(key)) is not int or not 0 <= report[key] <= MAX_PRICE for key in (
                "wallet_before", "wallet_after", "merchant_cash_before", "merchant_cash_after"))):
        raise RuntimeError("The stock finite merchant did not return the exact authoritative report")
    guids = set()
    blank_seen = False
    for offer in offers:
        guid, path = offer.get("guid"), offer.get("class_path")
        source = offer.get("source")
        from_backpack = expected_state in {"sale-quote", "sold"}
        if (not valid_guid(guid) or guid in guids or type(path) is not str or not path.startswith("/Game/")
                or len(path) > 512 or not 1 <= offer["quantity"] <= MAX_PRICE
                or not 0 <= offer["price"] <= MAX_PRICE or type(offer.get("value")) is not int
                or source is not None and (source == "backpack") != from_backpack
                or source == "buyback" and offer["price"] != offer["value"]):
            raise RuntimeError("The finite merchant returned an invalid stock offer")
        if area == SCIENTIST_SHOP and expected_state != "listed":
            item = (path, offer["quantity"], offer["value"])
            if guest and item == (BLANK_CLASS, 1, 30) and offer["price"] <= 75 and not blank_seen:
                blank_seen = True
            elif item != (CAMERA, 1, 125):
                raise RuntimeError("The Scientist Shop did not return an exact qualified stock offer")
        guids.add(guid)
    return report


async def perform(host, bridge, character: str, action: str, target: str | None, operation_id: str) -> dict:
    area = host.last_game.get("map")
    if (not host.native_zones or not host.native_guest or host.native_profile is None or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or area not in MERCHANTS or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_finite_shop_unavailable")
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")
    guest = character != "host"
    if not guest:
        if area != SCIENTIST_SHOP or action not in ACTIONS[:2]:
            raise party.RuleError("native_scientist_host_camera_purchase_only")
        pawn = host.last_game.get("pawns", {}).get("local", {}).get("name")
    else:
        member = host.native_recipients().get(character)
        pawn = member["pawn"] if member and member["connected"] else None
    if not pawn or host.party["wipe"] is not None or character in host.party["pending_spawns"]:
        reason = "native_finite_shop_host_unavailable" if character == "host" else "native_finite_shop_guest_unavailable"
        raise party.RuleError(reason)
    if host.party["players"][character]["downed"]:
        raise party.RuleError("downed")
    listing_only = action == "native-finite-shop-list"
    quote_only = action == "native-finite-shop-sale-quote"
    sale = quote_only or action == "native-finite-shop-sell"
    if area == SCIENTIST_SHOP and sale:
        raise party.RuleError("native_scientist_camera_purchase_only")
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
    listing = matching_report(snapshot, area, pawn, "sale-quote" if sale else "listed", guest=guest)
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
    if area == SCIENTIST_SHOP and (offer["class_path"] not in ((CAMERA, BLANK_CLASS) if guest else (CAMERA,))
                                  or offer.get("source") == "buyback"
                                  or offer["class_path"] == BLANK_CLASS and offer.get("source") not in (None, "shelf")):
        raise party.RuleError("native_scientist_purchase_profile_unavailable")
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
        proof = matching_report(snapshot, area, pawn, "sold" if sale else "purchased", guest=guest)
        reported = proof["offers"]
        shelf_classes = {SCIENTIST_SHOP: (CAMERA, BLANK_CLASS), GUNSMITH: (BALL_AMMO,), SMUGGLER: (MRE_CLASS,)}
        reusable = offer.get("source") == "shelf"
        legacy_special = offer["class_path"] in shelf_classes.get(area, ())
        if not sale and len(reported) == 1 and (reusable or legacy_special):
            stock_guids = {row["guid"] for row in listing["offers"]}
            received_guid = reported[0]["guid"]
            if (reusable and received_guid in stock_guids or
                    not reusable and received_guid != guid and (offer.get("source") is not None or received_guid in stock_guids)):
                raise RuntimeError("The merchant did not verify an exact reusable shelf transfer")
            proof["offer_guid"] = guid
            proof["stock_source"] = "limited_stock" if received_guid == guid else "stock_shelf"
            reported = [dict(reported[0], guid=guid)]
        if (reported != [offer] or proof["wallet_before"] != wallet or proof["wallet_after"] != wallet + wallet_change
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
        required = frozenset({character}) if guest else frozenset()
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=required)
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
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
