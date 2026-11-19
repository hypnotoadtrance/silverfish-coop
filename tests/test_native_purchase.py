from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import party
from tools.silverfish.native_pickup import CLASS_PATH, NativePickup


class NativePurchaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state = party.new_party()
        party.add_player(state, "scout")
        state["economy"]["wallet"] = 50
        self.host = SimpleNamespace(
            native_guest=True, fixtures=True, stopping=False, transitioning=False, guest_pawn="ScoutPawn",
            guest={"character_id": "scout", "disconnected": None}, zone_epoch="first",
            roles=lambda: {"host": "host", "guest": "scout"}, party=state, checkpoint=Mock(return_value=2),
            control=Mock(),
        )
        self.bound = {"state": "bound", "pawn": "ScoutPawn"}
        self.host.last_game = {"state": "hosting", "remote_clients": 1, "pawns": {"guest": {"name": "ScoutPawn"}},
                               "native_guest_save": self.bound}
        self.shop = "Shop_BP_C_1"
        self.native = NativePickup()
        self.bridge = SimpleNamespace(command=AsyncMock(side_effect=self.game_reply))

    async def game_reply(self, command, pawn, *arguments, timeout):
        if command == "native-shop-abort":
            return {}
        state = "prepared" if command == "native-shop-prepare" else "committed"
        if state == "committed":
            self.assertEqual(self.host.party["economy"]["wallet"], 35)
            self.host.checkpoint.assert_called_once()
        return {
            "native_guest_save": self.bound,
            "native_shop_purchase": {"state": state, "guid": arguments[-1], "shop": self.shop, "pawn": pawn,
                                     "price": 15, "class_path": CLASS_PATH, "ammo": 1, "weight": 0.05,
                                     "value": 15, "never_drop": False},
        }

    async def test_paid_stock_purchase_commits_once_before_native_append(self):
        operation = "1" * 32
        purchase = await self.native.buy(self.host, self.bridge, "scout", operation, self.shop)
        self.assertEqual(purchase["wallet"], 35)
        self.assertEqual(await self.native.buy(self.host, self.bridge, "scout", operation, self.shop), purchase)
        self.assertEqual(self.bridge.command.await_count, 2)
        self.assertEqual(self.native.projected(), [purchase["guid"]])

    async def test_insufficient_cash_releases_quote_without_appending(self):
        self.host.party["economy"]["wallet"] = 14
        with self.assertRaisesRegex(party.RuleError, "insufficient_funds"):
            await self.native.buy(self.host, self.bridge, "scout", "2" * 32, self.shop)
        self.host.checkpoint.assert_not_called()
        self.assertEqual(self.host.party["economy"]["wallet"], 14)
        self.assertEqual([call.args[0] for call in self.bridge.command.await_args_list],
                         ["native-shop-prepare", "native-shop-abort"])
