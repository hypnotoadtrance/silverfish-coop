"""Keep the guest merchant overlay tied to current, authenticated stock quotes."""

import contextlib
import math
import re
import time
import uuid

from . import runtime
from .native_finite_shop import MAX_PRICE, MERCHANTS
from .native_gold import valid_guid


PAGE_SIZE = 8
MAX_OFFERS = 128
WAITING = {"open": "pending", "page": "paging", "buy": "buying", "sell": "selling"}
_CARD = re.compile(r"stock:(shelf|limited|buyback|backpack):([0-9A-F]{32})\Z")
_ITEM = re.compile(r"/Game/[A-Za-z0-9_./-]+\.[A-Za-z0-9_-]+_C\Z")
_REFRESH_AFTER_REFUSAL = {
    "native_finite_shop_quote_changed", "native_finite_shop_item_unavailable", "insufficient_funds",
    "insufficient_merchant_funds", "finite_shop_refused_shop_quote_changed",
    "finite_shop_refused_shop_funds_unavailable", "finite_shop_refused_shop_offer_no_longer_available",
    "finite_shop_refused_shop_sale_stock_item_requires_special_handling",
}


def valid_fixture(target):
    if not isinstance(target, str):
        return False
    match = _CARD.fullmatch(target)
    return (target in {"open", "up", "down", "enter", "back", "page-up", "page-down"}
            or match is not None and valid_guid(match[2]))


def decimal(value, maximum):
    return (int(value) if isinstance(value, str) and value.isascii() and value.isdigit()
            and len(value) <= 10 and int(value) <= maximum else None)


def page_offset(value):
    offset = decimal(value, MAX_OFFERS - PAGE_SIZE)
    return offset if offset is not None and offset % PAGE_SIZE == 0 else None


class ShopView:
    def __init__(self):
        self.pending = None
        self.rendered = None
        self.refresh_after = 0.0

    async def sync(self, client, active):
        events = client.bridge.native_shop_view_requests()
        epoch = client.host_session, client.zone_epoch
        if not active or client.stopping or not all(epoch):
            self.pending = self.rendered = None
            return
        now = time.monotonic()
        if events:
            self.pending = (epoch, now, events[-1])
        if self.pending and (self.pending[0] != epoch or now - self.pending[1] > 2):
            self.pending = None
        if not self.pending:
            game = client.last_game.get("native_shop_view", {})
            if game.get("stock") and game.get("state") == "ready" and now >= self.refresh_after:
                wallet = client.gameplay.get("shared_wallet")
                if type(wallet) is int and wallet != game.get("wallet"):
                    self.refresh_after = now + .75
                    with contextlib.suppress(OSError, RuntimeError, ValueError):
                        client.last_game = await client.bridge.command("native-shop-view-refresh-stock",
                                                                        str(game["sequence"]), game["map"], timeout=2)
            return
        event, sequence, kind, area, pawn, actor, guid, price = self.pending[2]
        scope = (*epoch, sequence, area, pawn, actor)
        if kind == "close":
            self.pending = None
            if self.rendered and self.rendered[0] == scope:
                self.rendered = None
            return
        game = client.last_game
        view = game.get("native_shop_view", {})
        if (area != game.get("map") or actor != MERCHANTS.get(area)
                or pawn != game.get("pawns", {}).get("local", {}).get("name")
                or tuple(view.get(key) for key in ("sequence", "map", "pawn", "actor", "state"))
                != (sequence, area, pawn, actor, WAITING[kind])):
            return
        page = game.get("native_shop_view_page", {})
        if page and tuple(page.get(key) for key in ("sequence", "map", "pawn", "actor", "pending_event")) != (
                sequence, area, pawn, actor, event):
            return
        self.pending = None
        identifier = f"{epoch[0]}:{epoch[1]}:{pawn}:shop:{sequence}:{event}"
        operation = uuid.uuid5(client.bridge.input_namespace, identifier).hex
        try:
            wanted = self._requested_offset(kind, page, scope, guid, price, view)
            if kind in {"buy", "sell"}:
                try:
                    result = await client.gameplay_action(f"native-finite-shop-{kind}", operation, f"{guid}@{price}")
                except ValueError as error:
                    if not view.get("stock") or str(error) not in _REFRESH_AFTER_REFUSAL:
                        raise
                    client.control.event("native_shop_view_trade_refused", operation_id=operation, reason=str(error))
                else:
                    client.control.event("native_shop_view_sold" if kind == "sell" else "native_shop_view_purchased",
                                         operation_id=operation, **result)
            await self._render(client, scope, operation, event, wanted, paged=bool(page), stock=view.get("stock", False))
        except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
            self.rendered = None
            client.control.event("native_shop_view_rejected", operation_id=operation, reason=str(error))
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                arguments = (str(sequence), area, str(event)) if page else (str(sequence), area)
                await client.bridge.command("native-shop-view-unavailable", *arguments, timeout=2)

    def _requested_offset(self, kind, page, scope, guid, raw, view):
        if kind == "open":
            self.rendered = None
            return 0
        offset = page.get("offset", 0)
        if not self.rendered or self.rendered[:2] != (scope, offset):
            raise ValueError("The merchant view has no current rendered quotes")
        if kind == "page":
            start, destination = page_offset(guid), page_offset(raw)
            if (not page or start != offset or destination is None or abs(destination - start) != PAGE_SIZE
                    or destination != page.get("wanted_offset")):
                raise ValueError("Choose an adjacent page from the current stock merchant view")
            return destination
        amount = decimal(raw, MAX_PRICE)
        if (amount is None or not valid_guid(guid) or view.get("guid") != guid
                or view.get("price") != amount or kind == "sell" and not view.get("stock")
                or kind == "buy" and self.rendered[2].get(guid) != amount):
            raise ValueError("The stock merchant selection did not match the displayed quote")
        return offset

    async def _render(self, client, scope, operation, event, wanted, *, paged, stock=False):
        _, _, sequence, area, pawn, actor = scope
        response = await client.gameplay_action("native-finite-shop-list", operation, None)
        listing = response["native_finite_shop"]
        total, wallet, offers = listing["count"], listing["wallet_after"], listing["offers"]
        if (type(total) is not int or not 0 <= total <= MAX_OFFERS or type(wallet) is not int
                or not 0 <= wallet <= MAX_PRICE or type(offers) is not list or len(offers) != total
                or (listing.get("map"), listing.get("actor")) != (area, actor)):
            raise ValueError("The host returned an invalid native merchant catalog")
        if stock:
            await self._render_stock(client, scope, operation, event, wallet, offers)
            return
        offset = min(wanted, max(0, (total - 1) // PAGE_SIZE) * PAGE_SIZE)
        fields, quotes = [], {}
        for row in offers[offset:offset + PAGE_SIZE]:
            guid, item_class, quantity, price = (row[key] for key in ("guid", "class_path", "quantity", "price"))
            if (not valid_guid(guid) or guid in quotes or not isinstance(item_class, str) or not item_class.startswith("/Game/")
                    or "|" in item_class or type(quantity) is not int or not 0 < quantity <= MAX_PRICE
                    or type(price) is not int or not 0 <= price <= MAX_PRICE):
                raise ValueError("The host returned an invalid merchant offer")
            fields.append(f"{guid}|{item_class}|{quantity}|{price}")
            quotes[guid] = price
        if paged:
            arguments = ("native-shop-view-render-page", str(sequence), str(event), area, actor, str(offset),
                         str(wallet), str(total), *fields)
        else:
            arguments = ("native-shop-view-render", str(sequence), area, actor, str(wallet), str(total), *fields)
        snapshot = await client.bridge.command(*arguments, timeout=5)
        observed = snapshot.get("native_shop_view", {})
        keys = ("sequence", "map", "pawn", "actor", "state", "visible")
        if tuple(observed.get(key) for key in keys) != (sequence, area, pawn, actor, "ready", len(fields)):
            raise RuntimeError("The game did not verify the authenticated merchant overlay")
        if paged:
            observed_page = snapshot.get("native_shop_view_page", {})
            if tuple(observed_page.get(key) for key in ("offset", "pending_event", "render_event")) != (offset, 0, event):
                raise RuntimeError("The game did not verify the current authenticated merchant page")
        self.rendered = (scope, offset, quotes)
        client.last_game = snapshot
        client.control.event("native_shop_view_rendered", operation_id=operation, sequence=sequence, total=total, offset=offset)

    async def _render_stock(self, client, scope, operation, event, wallet, offers):
        expected_host, expected_visit, sequence, area, pawn, actor = scope
        lines, quotes = [], {}
        for offer in offers:
            guid, path, source = offer["guid"], offer["class_path"], offer["source"]
            price, value, ammo, weight, protected = (offer[key] for key in ("price", "value", "ammo", "weight", "never_drop"))
            if (not valid_guid(guid) or guid in quotes or not _ITEM.fullmatch(path) or len(path) > 512
                    or source not in {"shelf", "limited", "buyback"}
                    or type(price) is not int or not 0 <= price <= MAX_PRICE
                    or type(value) is not int or not 0 <= value <= MAX_PRICE
                    or type(ammo) is not int or not -2**31 <= ammo <= MAX_PRICE or type(protected) is not bool
                    or type(weight) not in {int, float} or not math.isfinite(weight) or not 0 <= weight <= 100000):
                raise ValueError("The host returned an invalid native merchant card")
            quotes[guid] = price
            lines.append(f"{guid}\t{path}\t{source}\t{price}\t{value}\t{ammo}\t{weight}\t{int(protected)}\n")
        await client.guest_ui.refresh(client)
        if (client.host_session, client.zone_epoch) != (expected_host, expected_visit):
            raise ValueError("The merchant catalog belongs to another visit")
        nonce = uuid.uuid4().hex
        path = client.bridge.directory / f"native-shop-stock-{nonce}.tsv"
        try:
            runtime.write_text(path, "".join(lines), newline="")
            snapshot = await client.bridge.command("native-shop-view-render-stock", str(sequence), str(event), area, actor,
                                                   str(wallet), str(len(offers)), nonce, timeout=5)
        finally:
            with contextlib.suppress(FileNotFoundError, PermissionError):
                path.unlink()
        observed = snapshot.get("native_shop_view", {})
        keys = ("sequence", "map", "pawn", "actor", "state", "visible", "stock", "wallet")
        if tuple(observed.get(key) for key in keys) != (sequence, area, pawn, actor, "ready", len(offers), True, wallet):
            raise RuntimeError("The stock shop screen could not verify the authenticated catalog")
        self.rendered = (scope, 0, quotes)
        self.refresh_after = time.monotonic() + .75
        client.last_game = snapshot
        client.control.event("native_shop_view_rendered", operation_id=operation, sequence=sequence,
                             total=len(offers), offset=0, stock=True)
