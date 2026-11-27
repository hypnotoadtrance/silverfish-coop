"""Keep the guest merchant screen tied to current, authenticated stock quotes."""

import asyncio
import contextlib
import math
import re
import time
import uuid

from . import runtime
from .content_paths import item_class_name
from .native_finite_shop import CASTAWAY_ACTOR, MAX_PRICE, STOCK_REVISION, merchant_in_map, valid_stock_revision
from .native_gold import valid_guid


MAX_OFFERS = 128
STOCK_RECHECK_INTERVAL = 4
WAITING = {"open": "pending", "buy": "buying", "sell": "selling"}
_CARD = re.compile(r"stock:(shelf|limited|buyback|backpack):([0-9A-F]{32})\Z")
_QUANTITY_CARD = re.compile(r"stock-(open|cancel|amount):(shelf|limited|buyback):([0-9A-F]{32})(?::([1-9][0-9]{0,9}))?\Z")
_REFRESH_AFTER_REFUSAL = {
    "native_finite_shop_quote_changed", "native_finite_shop_item_unavailable", "insufficient_funds",
    "insufficient_merchant_funds", "finite_shop_refused_shop_quote_changed",
    "finite_shop_refused_shop_funds_unavailable", "finite_shop_refused_shop_offer_no_longer_available",
    "finite_shop_refused_shop_sale_stock_item_requires_special_handling",
    "native_scientist_purchase_profile_unavailable", "finite_shop_refused_shop_scientist_purchase_profile_unavailable",
    "native_finite_shop_invalid_quantity", "finite_shop_refused_shop_quantity_unavailable",
    "finite_shop_refused_shop_quantity_row_unverified", "finite_shop_refused_shop_quantity_remainder_unverified",
}


def valid_fixture(target):
    if not isinstance(target, str):
        return False
    match = _CARD.fullmatch(target)
    quantity = _QUANTITY_CARD.fullmatch(target)
    return (target in {"open", "back"}
            or match is not None and valid_guid(match[2])
            or quantity is not None and valid_guid(quantity[3])
            and ((quantity[1] == "amount") == (quantity[4] is not None))
            and (quantity[4] is None or int(quantity[4]) <= MAX_PRICE))


def decimal(value, maximum):
    return (int(value) if isinstance(value, str) and value.isascii() and value.isdigit()
            and len(value) <= 10 and int(value) <= maximum else None)


class ShopView:
    def __init__(self):
        self.pending = None
        self.rendered = None
        self.refresh_after = 0.0
        self.stock_catalog = None
        self.stock_poll = None
        self.stock_poll_after = 0.0
        self.stock_revision = None
        self.stock_refresh_needed = False

    def _clear_stock_poll(self):
        if self.stock_poll is not None:
            self.stock_poll.cancel()
            self.stock_poll = None
        self.stock_catalog = None
        self.stock_revision = None
        self.stock_refresh_needed = False

    async def _poll_stock(self, client, catalog):
        scope = catalog[0]
        with contextlib.suppress(KeyError, TypeError, ValueError, OSError, RuntimeError):
            listing = await self._listing(client, scope, uuid.uuid4().hex)
            return scope, listing["wallet_after"], listing["offers"]

    async def _sync_stock(self, client, epoch, game, now):
        catalog = self.stock_catalog
        if (not catalog or not game.get("stock") or game.get("state") != "ready"
                or catalog[0] != (*epoch, *(game.get(key) for key in ("sequence", "map", "pawn", "actor")))):
            self._clear_stock_poll()
            return
        if self.stock_poll and self.stock_poll.done():
            result = self.stock_poll.result()
            self.stock_poll = None
            if result is not None and result[0] == catalog[0] and result != catalog:
                self.stock_refresh_needed = True
        current = client.gameplay.get(STOCK_REVISION)
        current = current if isinstance(current, dict) else {}
        revision = current.get("revision")
        scoped = (current.get("epoch"), current.get("map"), current.get("actor")) == (epoch[1], game["map"], game["actor"])
        revision_available = (scoped and valid_stock_revision(revision) and self.stock_revision is not None
                              and revision >= self.stock_revision)
        if revision_available and revision > self.stock_revision:
            self.stock_refresh_needed = True
        wallet = client.gameplay.get("shared_wallet")
        if type(wallet) is int and wallet != game.get("wallet"):
            self.stock_refresh_needed = True
        if self.stock_refresh_needed:
            if now >= self.refresh_after:
                self.refresh_after = now + .75
                with contextlib.suppress(OSError, RuntimeError, ValueError):
                    snapshot = await client.bridge.command("native-shop-view-refresh-stock",
                                                           str(game["sequence"]), game["map"], timeout=2)
                    client.last_game = snapshot
                    if snapshot.get("ack", {}).get("message") == "stock shop refresh queued":
                        self.stock_refresh_needed = False
        elif not revision_available and self.stock_poll is None and now >= self.stock_poll_after:
            self.stock_poll_after = now + STOCK_RECHECK_INTERVAL
            self.stock_poll = asyncio.create_task(self._poll_stock(client, catalog))

    async def sync(self, client, active):
        events = client.bridge.native_shop_view_requests()
        epoch = client.host_session, client.zone_epoch
        if not active or client.stopping or not all(epoch):
            self.pending = self.rendered = None
            self._clear_stock_poll()
            return
        now = time.monotonic()
        if events:
            self.pending = (epoch, now, events[-1])
        if self.pending and (self.pending[0] != epoch or now - self.pending[1] > 2):
            self.pending = None
        if not self.pending:
            await self._sync_stock(client, epoch, client.last_game.get("native_shop_view", {}), now)
            return
        event, sequence, kind, area, pawn, actor, guid, price = self.pending[2]
        scope = (*epoch, sequence, area, pawn, actor)
        if kind == "close":
            self.pending = None
            if self.rendered and self.rendered[0] == scope:
                self.rendered = None
            self._clear_stock_poll()
            return
        game = client.last_game
        view = game.get("native_shop_view", {})
        if (area != game.get("map") or not merchant_in_map(area, actor)
                or not view.get("stock") or pawn != game.get("pawns", {}).get("local", {}).get("name")
                or tuple(view.get(key) for key in ("sequence", "map", "pawn", "actor", "state"))
                != (sequence, area, pawn, actor, WAITING[kind])):
            return
        request = game.get("native_shop_view_request", {})
        if tuple(request.get(key) for key in ("sequence", "map", "pawn", "actor", "pending_event")) != (
                sequence, area, pawn, actor, event):
            return
        self.pending = None
        identifier = f"{epoch[0]}:{epoch[1]}:{pawn}:shop:{sequence}:{event}"
        operation = uuid.uuid5(client.bridge.input_namespace, identifier).hex
        try:
            self._validate_selection(kind, scope, guid, price, view)
            if kind in {"buy", "sell"}:
                try:
                    options = {"merchant": actor} if actor == CASTAWAY_ACTOR else {}
                    result = await client.gameplay_action(f"native-finite-shop-{kind}", operation, f"{guid}@{price}", **options)
                except ValueError as error:
                    if str(error) not in _REFRESH_AFTER_REFUSAL:
                        raise
                    client.control.event("native_shop_view_trade_refused", operation_id=operation, reason=str(error))
                else:
                    client.control.event("native_shop_view_sold" if kind == "sell" else "native_shop_view_purchased",
                                         operation_id=operation, **result)
            await self._render(client, scope, operation, event)
        except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
            self.rendered = None
            client.control.event("native_shop_view_rejected", operation_id=operation, reason=str(error))
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                await client.bridge.command("native-shop-view-unavailable", str(sequence), str(event), area, timeout=2)

    def _validate_selection(self, kind, scope, guid, raw, view):
        if kind == "open":
            self.rendered = None
            return
        if not self.rendered or self.rendered[0] != scope:
            raise ValueError("The merchant view has no current rendered quotes")
        raw_price, slash, raw_quantity = raw.partition("/")
        amount = decimal(raw_price, MAX_PRICE)
        quantity = decimal(raw_quantity, MAX_PRICE) if slash else None
        if slash and (kind != "buy" or not quantity):
            raise ValueError("The stock merchant purchase has no confirmed quantity")
        quotes = self.rendered[1]
        if (amount is None or not valid_guid(guid) or view.get("guid") != guid or view.get("price") != amount
                or kind == "buy" and (guid not in quotes or not slash and quotes[guid] != amount)):
            raise ValueError("The stock merchant selection did not match the displayed quote")

    async def _listing(self, client, scope, operation):
        _, _, _, area, _, actor = scope
        options = {"merchant": actor} if actor == CASTAWAY_ACTOR else {}
        response = await client.gameplay_action("native-finite-shop-list", operation, None, **options)
        listing = response["native_finite_shop"]
        total, wallet, offers = listing["count"], listing["wallet_after"], listing["offers"]
        if (type(total) is not int or not 0 <= total <= MAX_OFFERS or type(wallet) is not int
                or not 0 <= wallet <= MAX_PRICE or type(offers) is not list or len(offers) != total
                or (listing.get("map"), listing.get("actor")) != (area, actor)):
            raise ValueError("The host returned an invalid native merchant catalog")
        return listing

    async def _render(self, client, scope, operation, event):
        listing = await self._listing(client, scope, operation)
        revision = listing.get("stock_revision")
        if revision is not None and not valid_stock_revision(revision):
            raise ValueError("The host returned an invalid native merchant revision")
        await self._render_stock(client, scope, operation, event, listing["wallet_after"], listing["offers"], revision)

    async def _render_stock(self, client, scope, operation, event, wallet, offers, revision):
        expected_host, expected_visit, sequence, area, pawn, actor = scope
        lines, quotes = [], {}
        for offer in offers:
            guid, path, source = offer["guid"], offer["class_path"], offer["source"]
            price, value, ammo, weight, protected = (offer[key] for key in ("price", "value", "ammo", "weight", "never_drop"))
            markup = offer.get("markup")
            if (not valid_guid(guid) or guid in quotes or not item_class_name(path, 512)
                    or source not in {"shelf", "limited", "buyback"}
                    or type(price) is not int or not 0 <= price <= MAX_PRICE
                    or type(value) is not int or not 0 <= value <= MAX_PRICE
                    or type(ammo) is not int or not -2**31 <= ammo <= MAX_PRICE or type(protected) is not bool
                    or type(weight) not in {int, float} or not math.isfinite(weight) or not 0 <= weight <= 100000
                    or markup is not None and (type(markup) not in {int, float} or not 0 <= markup < 1e8
                                               or math.ceil(markup * value) != price)):
                raise ValueError("The host returned an invalid native merchant card")
            quotes[guid] = price
            fields = f"{guid}\t{path}\t{source}\t{price}\t{value}\t{ammo}\t{weight}\t{int(protected)}"
            lines.append((fields if markup is None else f"{fields}\t{markup!r}") + "\n")
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
        self.rendered = (scope, quotes)
        self._clear_stock_poll()
        now = time.monotonic()
        self.refresh_after = now + .75
        self.stock_poll_after = now + STOCK_RECHECK_INTERVAL
        self.stock_catalog = (scope, wallet, offers)
        self.stock_revision = revision
        client.last_game = snapshot
        client.control.event("native_shop_view_rendered", operation_id=operation, sequence=sequence,
                             total=len(offers), offset=0, stock=True)
