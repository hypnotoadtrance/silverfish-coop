import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_computer_board, native_finite_shop, party
from tools.silverfish.travel import OBSERVATORY, SCIENTIST_SHOP


GUEST, PAWN, HOST_PAWN = "guest-character", "exact guest pawn", "exact host pawn"
GUID, OPERATION_ID = "A" * 32, "b" * 32


class NativeInventoryTransactionTests(unittest.IsolatedAsyncioTestCase):
    def host_without_fresh_guest_frame(self, area):
        state = party.new_party()
        for character in ("host", GUEST):
            party.add_player(state, character)
        state["economy"]["wallet"] = 1000

        async def checkpoint(bridge, proposed=None, *, require_guest_frames=frozenset()):
            if require_guest_frames:
                raise RuntimeError("The checkpoint would omit a newly modified detached guest save")
            return 10 if proposed is None else 11

        return SimpleNamespace(
            native_zones=True, native_guest=True, native_profile=True, stopping=False, transitioning=False,
            native_checkpoint_unsafe=False, last_seen=time.monotonic(), party=state, control=Mock(),
            last_game={"state": "hosting", "map": area, "pawns": {"local": {"name": HOST_PAWN}}},
            native_recipients=lambda: {GUEST: {"pawn": PAWN, "connected": True}},
            roles=lambda: {"host": "host", "guest": GUEST},
            checkpoint_native=AsyncMock(side_effect=checkpoint), native_pickup=Mock(),
        )

    def shop_frame(self, area, pawn, state, wallet_delta=0):
        item = native_finite_shop.CAMERA if area == SCIENTIST_SHOP else native_finite_shop.BALL_AMMO
        offer = {"guid": GUID, "class_path": item, "quantity": 1, "value": 125, "price": 125}
        return {"state": "hosting", "map": area, "native_finite_shop": {
            "state": state, "map": area, "pawn": pawn, "actor": native_finite_shop.MERCHANTS[area],
            "wallet_before": 1000, "wallet_after": 1000 + wallet_delta,
            "merchant_cash_before": 500, "merchant_cash_after": 500 - wallet_delta,
            "offers": [offer], "count": 1,
        }}

    async def test_guest_shop_buy_and_sale_require_the_inventory_frame_with_wallet_and_receipt(self):
        for action, before, after, delta in (("buy", "listed", "purchased", -125), ("sell", "sale-quote", "sold", 125)):
            with self.subTest(action=action):
                host = self.host_without_fresh_guest_frame(OBSERVATORY)
                bridge = SimpleNamespace(command=AsyncMock(side_effect=[
                    self.shop_frame(OBSERVATORY, PAWN, before), self.shop_frame(OBSERVATORY, PAWN, after, delta),
                ]))
                with self.assertRaisesRegex(RuntimeError, "omit a newly modified detached guest save"):
                    await native_finite_shop.perform(host, bridge, GUEST, "native-finite-shop-" + action,
                                                     f"{GUID}@125", OPERATION_ID)
                commit = host.checkpoint_native.await_args
                self.assertEqual(commit.kwargs["require_guest_frames"], {GUEST})
                self.assertEqual(commit.args[1]["economy"]["wallet"], 1000 + delta)
                self.assertEqual(commit.args[1]["receipts"][GUEST][0]["id"], OPERATION_ID)
                self.assertEqual(host.party["economy"]["wallet"], 1000)
                self.assertEqual(host.party["receipts"], {})
                self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
                host.native_pickup.forget_committed_row.assert_not_called()

    async def test_canonical_host_purchase_and_read_only_shop_operations_require_no_guest_frame(self):
        for action, target, result in (("list", None, "listed"), ("sale-quote", GUID, "sale-quote")):
            host = self.host_without_fresh_guest_frame(OBSERVATORY)
            bridge = SimpleNamespace(command=AsyncMock(return_value=self.shop_frame(OBSERVATORY, PAWN, result)))
            await native_finite_shop.perform(host, bridge, GUEST, "native-finite-shop-" + action, target, OPERATION_ID)
            host.checkpoint_native.assert_not_awaited()

        host = self.host_without_fresh_guest_frame(SCIENTIST_SHOP)
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            self.shop_frame(SCIENTIST_SHOP, HOST_PAWN, "listed"),
            self.shop_frame(SCIENTIST_SHOP, HOST_PAWN, "purchased", -125),
        ]))
        result = await native_finite_shop.perform(host, bridge, "host", "native-finite-shop-buy", f"{GUID}@125", OPERATION_ID)
        self.assertEqual(result["native_finite_purchase"]["wallet_after"], 875)
        self.assertEqual(host.checkpoint_native.await_args.kwargs.get("require_guest_frames", frozenset()), frozenset())

    async def test_computer_board_guest_turn_in_requires_the_consumed_board_frame(self):
        host = self.host_without_fresh_guest_frame(SCIENTIST_SHOP)
        host.party["native_quests"] = {native_computer_board.QUEST_ID: {"step": 1}}

        def board_frame(verb, step, cash, xp):
            return {"state": "hosting", "map": SCIENTIST_SHOP, "native_computer_board": {
                "operation": verb, "map": SCIENTIST_SHOP, "pawn": PAWN,
                "board": native_computer_board.BOARDS[SCIENTIST_SHOP], "quest": native_computer_board.QUEST_ID,
                "can_turn_in": True, "step_before": 1, "step_after": step,
                "cash_before": 1000, "cash_after": cash, "xp_before": 3, "xp_after": xp,
                "objective_guid": GUID, "objective_owner": "guest",
                "reputation": {faction: {"before": 0, "after": 0} for faction in native_computer_board.FACTIONS},
            }}

        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            board_frame(native_computer_board.PREFLIGHT, 1, 1000, 3), board_frame(native_computer_board.TURN_IN, -10, 1800, 4),
        ]))
        with self.assertRaisesRegex(RuntimeError, "omit a newly modified detached guest save"):
            await native_computer_board.turn_in(host, bridge, GUEST)
        commit = host.checkpoint_native.await_args
        self.assertEqual(commit.kwargs["require_guest_frames"], {GUEST})
        self.assertEqual(commit.args[1]["native_quests"][native_computer_board.QUEST_ID]["consumed_board"], GUID)
        self.assertEqual(host.party["native_quests"][native_computer_board.QUEST_ID], {"step": 1})
        self.assertEqual(host.party["economy"]["wallet"], 1000)
        self.assertFalse(host.transitioning)
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
