import asyncio
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_finite_shop, native_shop_view, party, runtime
from tools.silverfish.guest_weapon_profiles import NINE_MM_BALL
from tools.silverfish.native_finite_shop import GUNSMITH, MERCHANTS, matching_report
from tools.silverfish.native_shop_view import ShopView


BALL_AMMO = NINE_MM_BALL.item_path


class MerchantScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_stock_catalog_uses_one_bounded_bridge_command(self):
        bridge = runtime.Bridge(Path("unused"), None)
        actor = MERCHANTS[GUNSMITH]
        arguments = (native_shop_view.RENDER_STOCK, "1", "2", GUNSMITH, actor, "500", "80")
        body = (b"x" * 450 + b"\n") * 80

        async def command(request, timeout):
            header, _, catalog = request.partition(b"\n")
            nonce, *fields = header.decode("ascii").split("\t")
            self.assertEqual((tuple(fields), catalog, timeout), (arguments, body, 5))
            return (f"schema\t1\nseq\t1\nsession\tclient\tconnected\t{GUNSMITH}\t1\t1\n"
                    f"ack\t{nonce}\taccepted\tverified stock shop screen\n")

        bridge.local_bridge = SimpleNamespace(command=AsyncMock(side_effect=command))
        with patch.object(runtime, "write_text") as write:
            await bridge.command(*arguments, catalog_body=body, timeout=5)
            for invalid in (None, "rows", b"missing newline", b"\r\n", b"x" * 90000 + b"\n"):
                with self.assertRaisesRegex(ValueError, "Invalid stock merchant catalog"):
                    await bridge.command(*arguments, catalog_body=invalid)
            write.assert_not_called()
        bridge.local_bridge.command.assert_awaited_once()

        bridge.local_bridge = None
        bridge.wait_for = AsyncMock(return_value={"ack": {"result": "accepted"}})
        with patch.object(runtime, "write_text") as write:
            await bridge.command(*arguments, catalog_body=body)
            write.assert_called_once()
            path, request = write.call_args.args
            header, _, catalog = request.partition("\n")
            self.assertEqual((path, tuple(header.split("\t")[1:]), catalog.encode("ascii")),
                             (bridge.directory / "command.tsv", arguments, body))

    async def test_open_stock_screen_refreshes_when_stock_changes_without_wallet_change(self):
        actor, pawn = MERCHANTS[GUNSMITH], "exact stock guest"
        guid = "A" * 32
        row = {"guid": guid, "quantity": 200, "price": 100, "class_path": BALL_AMMO}
        game = {"stock": True, "state": "ready", "sequence": 1, "map": GUNSMITH,
                "actor": actor, "pawn": pawn, "wallet": 500}
        bridge = SimpleNamespace(command=AsyncMock(return_value={"native_shop_view": game}))
        reading = asyncio.Event()

        async def query(*_args):
            await reading.wait()
            return {"native_finite_shop": {"map": GUNSMITH, "actor": actor, "wallet_after": 500,
                                           "count": 0, "offers": []}}

        client = SimpleNamespace(bridge=bridge, gameplay={"shared_wallet": 500},
                                 gameplay_action=AsyncMock(side_effect=query))
        view = ShopView()
        epoch = ("host", "visit")
        view.stock_catalog = ((*epoch, 1, GUNSMITH, pawn, actor), 500, [row])
        await view._sync_stock(client, epoch, game, 10.0)
        self.assertFalse(view.stock_poll.done())
        await view._sync_stock(client, epoch, game, 10.1)
        bridge.command.assert_not_awaited()
        reading.set()
        await view.stock_poll
        await view._sync_stock(client, epoch, game, 10.2)
        client.gameplay_action.assert_awaited_once()
        bridge.command.assert_awaited_once_with("native-shop-view-refresh-stock", "1", GUNSMITH, timeout=2)

    async def test_authoritative_revision_retries_but_only_for_the_active_merchant_and_visit(self):
        area = native_finite_shop.OBSERVATORY
        actor, regular = native_finite_shop.CASTAWAY_ACTOR, MERCHANTS[area]
        pawn = "exact stock guest"
        epoch = ("session", "current")
        base = {"stock": True, "state": "ready", "sequence": 1, "map": area,
                "actor": actor, "pawn": pawn, "wallet": 500}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            {"ack": {"message": "stock shop refresh deferred"}, "native_shop_view": base},
            {"ack": {"message": "stock shop refresh queued"}, "native_shop_view": dict(base, state="pending")},
        ]))
        key = native_finite_shop.STOCK_REVISION
        revision = {"map": area, "epoch": epoch[1], "revisions": {actor: 8, regular: 9}}
        gameplay = {"shared_wallet": 500, key: dict(revision, epoch="previous")}
        client = SimpleNamespace(bridge=bridge, gameplay=gameplay, gameplay_action=AsyncMock())
        view = ShopView()
        view.stock_catalog = ((*epoch, 1, area, pawn, actor), 500, [])
        view.stock_poll_after = 60
        view.stock_revision = 7

        await view._sync_stock(client, epoch, base, 10.0)
        gameplay[key] = dict(revision, revisions={actor: 7, regular: 9})
        await view._sync_stock(client, epoch, base, 10.1)
        bridge.command.assert_not_awaited()
        gameplay[key] = revision
        await view._sync_stock(client, epoch, base, 10.1)
        self.assertTrue(view.stock_refresh_needed)
        bridge.command.assert_awaited_once_with("native-shop-view-refresh-stock", "1", area, timeout=2)
        await view._sync_stock(client, epoch, base, 10.3)
        self.assertEqual(bridge.command.await_count, 1)
        # A dirty result must survive a deferred native refresh and a snapshot gap.
        del gameplay[native_finite_shop.STOCK_REVISION]
        await view._sync_stock(client, epoch, base, 10.9)
        self.assertFalse(view.stock_refresh_needed)
        self.assertEqual(bridge.command.await_count, 2)
        self.assertEqual(client.last_game["native_shop_view"]["state"], "pending")
        client.gameplay_action.assert_not_awaited()

    async def test_catalog_opened_before_revision_stops_polling_when_stock_is_unchanged(self):
        area = native_finite_shop.OBSERVATORY
        actor, pawn = native_finite_shop.CASTAWAY_ACTOR, "exact stock guest"
        epoch = ("host", "visit")
        game = {"stock": True, "state": "ready", "sequence": 1, "map": area,
                "actor": actor, "pawn": pawn, "wallet": 500}
        listing = {"map": area, "actor": actor, "wallet_after": 500, "count": 0, "offers": [], "stock_revision": 3}
        key = native_finite_shop.STOCK_REVISION
        client = SimpleNamespace(
            bridge=SimpleNamespace(command=AsyncMock()),
            gameplay={key: {"map": area, "epoch": epoch[1], "revisions": {actor: 3}}},
            gameplay_action=AsyncMock(return_value={"native_finite_shop": listing}),
        )
        view = ShopView()
        view.stock_catalog = ((*epoch, 1, area, pawn, actor), 500, [])

        await view._sync_stock(client, epoch, game, 10.0)
        await view.stock_poll
        await view._sync_stock(client, epoch, game, 15.0)
        self.assertEqual(view.stock_revision, 3)
        self.assertIsNone(view.stock_poll)
        client.gameplay_action.assert_awaited_once()
        client.bridge.command.assert_not_awaited()

    def test_host_bridge_keeps_both_observatory_counter_revisions(self):
        area = native_finite_shop.OBSERVATORY
        regular, castaway = MERCHANTS[area], native_finite_shop.CASTAWAY_ACTOR
        bridge = runtime.Bridge(Path("unused"), None)
        header = f"schema\t1\nseq\t1\nsession\thost\thosting\t{area}\t1\t0\n"
        rows = [f"native-finite-shop-stock-revision\t{area}\t{actor}\t{revision}\n"
                for actor, revision in ((regular, 4), (castaway, 6))]
        report = bridge._parse_status(header + "".join(rows))
        self.assertEqual(report[native_finite_shop.STOCK_REVISION], {regular: 4, castaway: 6})
        self.assertIsNone(bridge._parse_status(header + rows[0] + rows[0]))
        self.assertIn("Duplicate stock merchant revision", bridge.status_error)

    def test_merchant_revision_requires_exact_host_actor_and_positive_bounded_sequence(self):
        fields = ["native-finite-shop-stock-revision", GUNSMITH, MERCHANTS[GUNSMITH], "1"]
        self.assertEqual(native_finite_shop.parse_stock_revision(fields, "hosting", GUNSMITH),
                         {"map": GUNSMITH, "actor": MERCHANTS[GUNSMITH], "revision": 1})
        for value in ("0", str(2**53), "١", "-1"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "merchant revision"):
                native_finite_shop.parse_stock_revision([*fields[:3], value], "hosting", GUNSMITH)
        with self.assertRaisesRegex(ValueError, "merchant revision"):
            native_finite_shop.parse_stock_revision(fields, "connected", GUNSMITH)
        with self.assertRaisesRegex(ValueError, "merchant revision"):
            native_finite_shop.parse_stock_revision(fields, "hosting", native_finite_shop.OBSERVATORY)

    def test_scientist_display_accepts_complete_authority_catalog_with_purchase_validation(self):
        from tools.silverfish.native_finite_shop import SCIENTIST_SHOP

        pawn = "exact stock guest"
        other = {"guid": "D" * 32, "quantity": 3, "value": 9, "price": 12, "class_path": BALL_AMMO, "source": "shelf"}
        report = {"map": SCIENTIST_SHOP, "pawn": pawn, "actor": MERCHANTS[SCIENTIST_SHOP], "state": "listed",
                  "wallet_before": 500, "wallet_after": 500, "merchant_cash_before": 100, "merchant_cash_after": 100,
                  "count": 1, "offers": [other]}
        snapshot = {"state": "hosting", "map": SCIENTIST_SHOP, "native_finite_shop": report}
        self.assertEqual(matching_report(snapshot, SCIENTIST_SHOP, pawn, "listed", guest=True), report)
        del other["source"]
        with self.assertRaisesRegex(RuntimeError, "invalid stock offer"):
            matching_report(snapshot, SCIENTIST_SHOP, pawn, "listed", guest=True)
        other["source"] = "shelf"
        report["state"] = "purchased"
        with self.assertRaisesRegex(RuntimeError, "exact qualified stock offer"):
            matching_report(snapshot, SCIENTIST_SHOP, pawn, "purchased", guest=True)

    async def test_reusable_observatory_shelf_requires_fresh_exact_inventory_guid(self):
        area = native_finite_shop.OBSERVATORY
        guid, minted, pawn = "A" * 32, "B" * 32, "exact stock guest"
        item = {"guid": guid, "source": "shelf", "class_path": "/GuestItems/Supplies/Tonic.Tonic_C",
                "price": 50, "quantity": 10, "value": 25}
        game = {"state": "hosting", "map": area}
        listing = {"map": area, "pawn": pawn, "actor": MERCHANTS[area], "state": "listed",
                   "wallet_before": 100, "wallet_after": 100, "merchant_cash_before": 25, "merchant_cash_after": 25,
                   "count": 1, "offers": [item]}
        for received in (guid, minted):
            with self.subTest(received=received):
                chosen = dict(item, guid=received)
                bought = dict(listing, state="purchased", wallet_after=50, merchant_cash_after=75, offers=[chosen])
                host = SimpleNamespace(native_guest=True, native_zones=True, native_profile=object(),
                                       stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                                       last_game=game, last_seen=time.monotonic(), control=Mock(), checkpoint_native=AsyncMock(),
                                       native_recipients=lambda: {"guest": {"pawn": pawn, "connected": True}},
                                       party=party.load_party(None))
                host.party["players"]["guest"] = {"downed": False}
                bridge = SimpleNamespace(command=AsyncMock(side_effect=[
                    dict(game, native_finite_shop=listing), dict(game, native_finite_shop=bought),
                ]))
                operation = native_finite_shop.perform(host, bridge, "guest", "native-finite-shop-buy",
                                                       f"{guid}@50", "a" * 32)
                if received == guid:
                    with self.assertRaisesRegex(RuntimeError, "GUID already in stock"):
                        await operation
                    self.assertTrue(host.stopping)
                    self.assertFalse(host.party["receipts"])
                else:
                    result = (await operation)["native_finite_purchase"]
                    self.assertEqual((result["offer_guid"], result["stock_source"], result["offers"]),
                                     (guid, "stock_shelf", [chosen]))
                    self.assertFalse(host.native_checkpoint_unsafe)
                    self.assertEqual(host.checkpoint_native.await_args.kwargs["require_guest_frames"], frozenset({"guest"}))

    async def test_exact_stock_quantity_is_charged_and_replayed_once(self):
        guid, moved, pawn = "A" * 32, "B" * 32, "guest pawn"
        actor = MERCHANTS[GUNSMITH]
        game = {"state": "hosting", "map": GUNSMITH, "native_guest_save_retention": True}
        for source, disposition in (("shelf", "stock_shelf"), ("limited", "limited_stock")):
            with self.subTest(source=source):
                row = {"guid": guid, "class_path": BALL_AMMO, "source": source, "stackable": True,
                       "quantity": 200, "ammo": 200, "value": 600, "price": 750, "markup": 1.25}
                listing = {"state": "listed", "map": GUNSMITH, "actor": actor, "pawn": pawn, "count": 1,
                           "wallet_before": 1000, "wallet_after": 1000,
                           "merchant_cash_before": 100, "merchant_cash_after": 100, "offers": [row]}
                purchased = dict(listing, state="purchased", wallet_after=996, merchant_cash_after=104,
                                 offers=[dict(row, guid=moved, quantity=1, ammo=1, value=3, price=4)])
                host = SimpleNamespace(native_guest=True, native_zones=True, native_profile=object(), stopping=False,
                                       transitioning=False, native_checkpoint_unsafe=False, last_game=game,
                                       last_seen=time.monotonic(), control=Mock(), checkpoint_native=AsyncMock(),
                                       pending_guest_saves={"guest": (pawn,)},
                                       native_recipients=lambda: {"guest": {"pawn": pawn, "connected": True}},
                                       party=party.load_party(None))
                host.party["players"]["guest"] = {"downed": False}
                bridge = SimpleNamespace(command=AsyncMock(side_effect=[dict(game, native_finite_shop=listing),
                                                                          dict(game, native_finite_shop=purchased)]))
                target, operation = f"{guid}@4/1", "b" * 32
                args = host, bridge, "guest", "native-finite-shop-buy"
                result = await native_finite_shop.perform(*args, target, operation)
                receipt = result["native_finite_purchase"]
                self.assertEqual((receipt["requested_quantity"], receipt["stock_source"], receipt["wallet_after"]),
                                 (1, disposition, 996))
                self.assertEqual(receipt.get("remaining_quantity"), 199 if source == "limited" else None)
                self.assertEqual(await native_finite_shop.perform(*args, target, operation), result)
                bridge.command.assert_awaited_with("native-finite-shop-buy-quantity", pawn, guid, "1", "4", "1000", timeout=7)
                self.assertEqual(bridge.command.await_count, 2)
                with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
                    await native_finite_shop.perform(*args, f"{guid}@4/2", operation)

    def test_quantity_popup_quote_is_bound_to_current_stock_card(self):
        guid = "A" * 32
        scope = ("host", "visit", 1, GUNSMITH, "guest pawn", MERCHANTS[GUNSMITH])
        view = ShopView()
        view.rendered = (scope, {guid: 750})
        screen = {"stock": True, "guid": guid, "price": 4}
        view._validate_selection("buy", scope, guid, "4/1", screen)
        for raw in ("4/0", "4/", "4", "5/1", "4/1/2"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                view._validate_selection("buy", scope, guid, raw, screen)
        with self.assertRaises(ValueError):
            view._validate_selection("sell", scope, guid, "4/1", screen)
        with self.assertRaises(ValueError):
            view._validate_selection("buy", (scope[0], "older visit", *scope[2:]), guid, "4/1", screen)

    async def test_stock_catalog_uses_exact_request_and_passes_every_offer(self):
        actor, pawn = MERCHANTS[GUNSMITH], "Player_FP_C exact guest"
        base = {"sequence": 1, "map": GUNSMITH, "pawn": pawn, "actor": actor}
        game = {"map": GUNSMITH, "pawns": {"local": {"name": pawn}},
                "native_shop_view": {**base, "stock": True, "state": "pending"},
                "native_shop_view_request": {**base, "pending_event": 1}}
        offers = [{"guid": f"{index:032X}", "price": 10 + index} for index in range(1, 13)]
        bridge = SimpleNamespace(native_shop_view_requests=Mock(return_value=[(2, 1, "open", GUNSMITH, pawn, actor, "-", "-")]),
                                 input_namespace=uuid.UUID(int=1))
        client = SimpleNamespace(bridge=bridge, last_game=game, host_session="server", zone_epoch="zone",
                                 stopping=False, control=Mock(), gameplay_action=AsyncMock(return_value={
                                     "native_finite_shop": {"map": GUNSMITH, "actor": actor, "wallet_after": 500,
                                                            "count": len(offers), "offers": offers}}))
        view = ShopView()
        view._render_stock = AsyncMock()
        await view.sync(client, True)
        client.gameplay_action.assert_not_awaited()
        view._render_stock.assert_not_awaited()
        game["native_shop_view_request"]["pending_event"] = 2
        bridge.native_shop_view_requests.return_value = []
        await view.sync(client, True)
        scope = ("server", "zone", 1, GUNSMITH, pawn, actor)
        operation = uuid.uuid5(bridge.input_namespace, f"server:zone:{pawn}:shop:1:2").hex
        client.gameplay_action.assert_awaited_once_with("native-finite-shop-list", operation, None)
        view._render_stock.assert_awaited_once_with(client, scope, operation, 2, 500, offers, None)

    async def test_fresh_client_process_can_buy_the_same_reusable_offer_again(self):
        actor, pawn = MERCHANTS[GUNSMITH], "Player_FP_C exact same guest"
        row = {"guid": "A" * 32, "quantity": 200, "price": 100, "class_path": BALL_AMMO}
        base = {"sequence": 1, "map": GUNSMITH, "pawn": pawn, "actor": actor}
        receipts = {}
        wallet = 1000

        def game(state, pending, rendered=0):
            return {
                "map": GUNSMITH, "pawns": {"local": {"name": pawn}},
                "native_shop_view": {**base, "state": state, "stock": True, "visible": 1,
                                     "guid": row["guid"], "price": row["price"]},
                "native_shop_view_request": {**base, "pending_event": pending, "render_event": rendered},
            }

        async def query(action, operation, target):
            nonlocal wallet
            if action == "native-finite-shop-list":
                return {"native_finite_shop": {"map": GUNSMITH, "actor": actor, "wallet_after": wallet, "count": 1, "offers": [row]}}
            self.assertEqual(action, "native-finite-shop-buy")
            self.assertEqual(target, f"{row['guid']}@{row['price']}")
            if operation not in receipts:
                wallet -= row["price"]
                receipts[operation] = {"native_finite_purchase": {"wallet_after": wallet}}
            return receipts[operation]

        async def render_stock(client, scope, _operation, event, _wallet, offers, _revision):
            client.last_game = game("ready", 0, event)
            view.rendered = (scope, {offer["guid"]: offer["price"] for offer in offers})

        for game_instance in (1, 2):
            bridge = SimpleNamespace(native_shop_view_requests=Mock(),
                                     input_namespace=uuid.UUID(int=game_instance))
            client = SimpleNamespace(bridge=bridge, last_game=game("pending", 1), host_session="same server",
                                     zone_epoch="same zone", stopping=False, gameplay_action=query, control=Mock())
            view = ShopView()
            view._render_stock = render_stock
            bridge.native_shop_view_requests.return_value = [(1, 1, "open", GUNSMITH, pawn, actor, "-", "-")]
            await view.sync(client, True)

            client.last_game = game("buying", 2)
            bridge.native_shop_view_requests.return_value = [(2, 1, "buy", GUNSMITH, pawn, actor, row["guid"], str(row["price"]))]
            await view.sync(client, True)
            self.assertEqual(client.last_game["native_shop_view_request"]["render_event"], 2)
            self.assertEqual(len(receipts), game_instance)

            client.last_game = game("buying", 2)
            await view.sync(client, True)
            self.assertEqual(len(receipts), game_instance)

        self.assertEqual(len(receipts), 2)
        self.assertEqual(wallet, 800)
