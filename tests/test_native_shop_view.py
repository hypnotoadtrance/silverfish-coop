import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish.native_finite_shop import BALL_AMMO, GUNSMITH, MERCHANTS
from tools.silverfish.native_shop_view import ShopView


class MerchantPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_adjacent_page_and_current_quote_required(self):
        actor = MERCHANTS[GUNSMITH]
        pawn, sequence = "exact stock guest", 14
        rows = [
            {"guid": f"{index:032X}", "quantity": 1, "price": 20 + index, "class_path": BALL_AMMO}
            for index in range(1, 13)
        ]
        snapshot = {"map": GUNSMITH, "pawns": {"local": {"name": pawn}}}
        base = {"sequence": sequence, "map": GUNSMITH, "pawn": pawn, "actor": actor}

        def game(state, event, offset, wanted, **selection):
            return {
                **snapshot,
                "native_shop_view": {**base, "state": state, "visible": min(len(rows) - offset, 8), **selection},
                "native_shop_view_page": {**base, "offset": offset, "wanted_offset": wanted,
                                          "pending_event": event, "render_event": 0},
            }

        async def query(action, _operation, target):
            if action == "native-finite-shop-buy":
                self.assertEqual(target, f"{rows[8]['guid']}@{rows[8]['price']}")
                del rows[7:]
                return {"native_finite_purchase": {"wallet_after": 801}}
            return {"native_finite_shop": {"map": GUNSMITH, "actor": actor, "wallet_after": 801,
                                           "count": len(rows), "offers": list(rows)}}

        async def render(command, *arguments, timeout):
            if command == "native-shop-view-unavailable":
                return {}
            self.assertEqual(command, "native-shop-view-render-page")
            _, event, _, _, offset = arguments[:5]
            shown = game("ready", 0, int(offset), int(offset))
            shown["native_shop_view_page"]["render_event"] = int(event)
            return shown

        bridge = SimpleNamespace(native_shop_view_requests=Mock(), command=AsyncMock(side_effect=render),
                                 input_namespace=uuid.UUID(int=1))
        client = SimpleNamespace(bridge=bridge, last_game=game("pending", 1, 0, 0),
                                 host_session="server", zone_epoch="zone", stopping=False,
                                 gameplay_action=AsyncMock(side_effect=query), control=Mock())
        view = ShopView()

        def event(number, kind, first="-", second="-"):
            bridge.native_shop_view_requests.return_value = [(number, sequence, kind, GUNSMITH, pawn, actor, first, second)]

        event(1, "open")
        await view.sync(client, True)
        self.assertEqual(client.last_game["native_shop_view_page"]["offset"], 0)

        event(2, "page", "0", "8")
        await view.sync(client, True)
        self.assertEqual(bridge.command.await_count, 1)
        client.last_game = game("paging", 2, 0, 8)
        bridge.native_shop_view_requests.return_value = []
        await view.sync(client, True)
        self.assertEqual(client.last_game["native_shop_view_page"]["offset"], 8)

        earlier = rows[0]
        event(3, "buy", earlier["guid"], str(earlier["price"]))
        client.last_game = game("buying", 3, 8, 8, guid=earlier["guid"], price=earlier["price"])
        await view.sync(client, True)
        self.assertEqual(client.gameplay_action.await_count, 2)
        self.assertEqual(bridge.command.await_args.args[0], "native-shop-view-unavailable")

        event(4, "open")
        client.last_game = game("pending", 4, 0, 0)
        await view.sync(client, True)
        event(5, "page", "0", "8")
        client.last_game = game("paging", 5, 0, 8)
        await view.sync(client, True)

        current = rows[8]
        event(6, "buy", current["guid"], str(current["price"]))
        client.last_game = game("buying", 6, 8, 8, guid=current["guid"], price=current["price"])
        await view.sync(client, True)
        self.assertEqual(client.last_game["native_shop_view_page"]["offset"], 0)

    async def test_fresh_client_process_can_buy_the_same_reusable_offer_again(self):
        actor, pawn = MERCHANTS[GUNSMITH], "Player_FP_C exact same guest"
        row = {"guid": "A" * 32, "quantity": 200, "price": 100, "class_path": BALL_AMMO}
        base = {"sequence": 1, "map": GUNSMITH, "pawn": pawn, "actor": actor}
        receipts = {}
        wallet = 1000

        def game(state, pending, rendered=0):
            return {
                "map": GUNSMITH, "pawns": {"local": {"name": pawn}},
                "native_shop_view": {**base, "state": state, "visible": 1, "guid": row["guid"], "price": row["price"]},
                "native_shop_view_page": {**base, "offset": 0, "wanted_offset": 0,
                                          "pending_event": pending, "render_event": rendered},
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

        async def render(command, *arguments, timeout):
            self.assertEqual(command, "native-shop-view-render-page")
            return game("ready", 0, int(arguments[1]))

        for game_instance in (1, 2):
            bridge = SimpleNamespace(native_shop_view_requests=Mock(), command=AsyncMock(side_effect=render),
                                     input_namespace=uuid.UUID(int=game_instance))
            client = SimpleNamespace(bridge=bridge, last_game=game("pending", 1), host_session="same server",
                                     zone_epoch="same zone", stopping=False, gameplay_action=query, control=Mock())
            view = ShopView()
            bridge.native_shop_view_requests.return_value = [(1, 1, "open", GUNSMITH, pawn, actor, "-", "-")]
            await view.sync(client, True)

            client.last_game = game("buying", 2)
            bridge.native_shop_view_requests.return_value = [(2, 1, "buy", GUNSMITH, pawn, actor, row["guid"], str(row["price"]))]
            await view.sync(client, True)
            self.assertEqual(client.last_game["native_shop_view_page"]["render_event"], 2)
            self.assertEqual(len(receipts), game_instance)

            client.last_game = game("buying", 2)
            await view.sync(client, True)
            self.assertEqual(len(receipts), game_instance)

        self.assertEqual(len(receipts), 2)
        self.assertEqual(wallet, 800)
