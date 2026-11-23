import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_computer_board, native_finite_shop, native_inventory, party
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import Control
from tools.silverfish.multi_host import MultiHost
from tools.silverfish.native_tape_profiles import BLANK_CLASS
from tools.silverfish.runtime import read_json, write_json
from tools.silverfish.service import Host
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

    def shop_frame(self, area, pawn, state, wallet_delta=0, *, item=None, guid=GUID, value=125, price=125):
        if item is None:
            item = native_finite_shop.CAMERA if area == SCIENTIST_SHOP else native_finite_shop.BALL_AMMO
        offer = {"guid": guid, "class_path": item, "quantity": 1, "value": value, "price": price}
        return {"state": "hosting", "map": area, "native_finite_shop": {
            "state": state, "map": area, "pawn": pawn, "actor": native_finite_shop.MERCHANTS[area],
            "wallet_before": 1000, "wallet_after": 1000 + wallet_delta,
            "merchant_cash_before": 500, "merchant_cash_after": 500 - wallet_delta,
            "offers": [offer], "count": 1,
        }}

    async def test_unequip_retains_detached_inventory_and_replays_one_receipt(self):
        host = self.host_without_fresh_guest_frame(OBSERVATORY)
        host.last_game.update({native_inventory.UNEQUIP_CAPABILITY: 1, "native_guest_save_retention": True})
        host.pending_guest_saves = {GUEST: (PAWN,)}
        proof = {"map": OBSERVATORY, "pawn": PAWN, "state": "unequipped", "guid": GUID, "slot": 1, "ammo": 3}
        bridge = SimpleNamespace(command=AsyncMock(return_value={**host.last_game, native_inventory.UNEQUIP_KEY: proof}))
        args = (host, bridge, GUEST, f"{GUID}|1", OPERATION_ID)
        result = await native_inventory.store_equipment(*args, unequip=True)
        self.assertEqual(result[native_inventory.UNEQUIP_KEY], dict(proof, owner=GUEST))
        self.assertEqual(await native_inventory.store_equipment(*args, unequip=True), result)
        with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
            await native_inventory.store_equipment(host, bridge, GUEST, f"{GUID}|2", OPERATION_ID, unequip=True)
        bridge.command.assert_awaited_once_with(native_inventory.UNEQUIP, PAWN, GUID, "1", timeout=6)
        host.checkpoint_native.assert_not_awaited()
        self.assertFalse(host.native_checkpoint_unsafe)

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

    async def test_scientist_guest_blank_reuses_stock_quote_and_commits_minted_guid(self):
        host = self.host_without_fresh_guest_frame(SCIENTIST_SHOP)
        host.checkpoint_native = AsyncMock(side_effect=[10, 11])
        minted = "C" * 32
        blank = {"item": BLANK_CLASS, "value": 30, "price": 47}
        listing = self.shop_frame(SCIENTIST_SHOP, PAWN, "listed", **blank)
        purchase = self.shop_frame(SCIENTIST_SHOP, PAWN, "purchased", -47, guid=minted, **blank)
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[listing, purchase]))

        purchase_action = (host, bridge, GUEST, "native-finite-shop-buy", f"{GUID}@47", OPERATION_ID)
        reply = await native_finite_shop.perform(*purchase_action)
        receipt = reply["native_finite_purchase"]
        self.assertEqual((receipt["offer_guid"], receipt["stock_source"], receipt["offers"][0]["guid"]),
                         (GUID, "stock_shelf", minted))
        self.assertEqual((receipt["wallet_after"], receipt["merchant_cash_after"]), (953, 547))
        bridge.command.assert_awaited_with("native-finite-shop-buy", PAWN, GUID, "1", "47", "1000", timeout=7)
        commit = host.checkpoint_native.await_args
        self.assertEqual(commit.kwargs["require_guest_frames"], {GUEST})
        self.assertEqual(commit.args[1]["receipts"][GUEST][0]["result"], reply)
        self.assertEqual(await native_finite_shop.perform(*purchase_action), reply)
        self.assertEqual((bridge.command.await_count, host.checkpoint_native.await_count), (2, 2))

    async def test_scientist_blank_host_report_and_reused_shelf_guid_fail_closed(self):
        blank = {"item": BLANK_CLASS, "value": 30, "price": 47}
        host = self.host_without_fresh_guest_frame(SCIENTIST_SHOP)
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.shop_frame(SCIENTIST_SHOP, HOST_PAWN, "listed", **blank)))
        with self.assertRaisesRegex(party.RuleError, "native_scientist_purchase_profile_unavailable"):
            await native_finite_shop.perform(host, bridge, "host", "native-finite-shop-buy", f"{GUID}@47", OPERATION_ID)
        host.checkpoint_native.assert_not_awaited()
        self.assertEqual(bridge.command.await_count, 1)

        host = self.host_without_fresh_guest_frame(SCIENTIST_SHOP)
        listing = self.shop_frame(SCIENTIST_SHOP, PAWN, "listed", **blank)
        listing["native_finite_shop"]["offers"][0]["source"] = "shelf"
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            listing,
            self.shop_frame(SCIENTIST_SHOP, PAWN, "purchased", -47, **blank),
        ]))
        with self.assertRaisesRegex(RuntimeError, "exact reusable shelf transfer"):
            await native_finite_shop.perform(host, bridge, GUEST, "native-finite-shop-buy", f"{GUID}@47", OPERATION_ID)
        self.assertEqual(host.party["receipts"], {})
        self.assertEqual(host.checkpoint_native.await_count, 1)
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)

    @staticmethod
    def enqueue(control, identifier, action, **arguments):
        name = identifier * 32 + ".json"
        write_json(control.run / "requests" / name, {"generation": control.generation, "action": action, **arguments})
        return control.run / "responses" / name

    async def test_unsettled_shop_purchase_cannot_checkpoint_or_run_later_commands(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        host = self.host_without_fresh_guest_frame(SCIENTIST_SHOP)
        wallet = 1000

        async def game_command(action, *args, **kwargs):
            nonlocal wallet
            if action == "native-finite-shop-list":
                return self.shop_frame(SCIENTIST_SHOP, HOST_PAWN, "listed")
            self.assertEqual(action, "native-finite-shop-buy")
            wallet -= 125
            return self.shop_frame(SCIENTIST_SHOP, HOST_PAWN, "purchased", -125)

        with Control(root / "run") as control, CampaignStore(root / "save") as store:
            async def checkpoint(_bridge, proposed=None, **kwargs):
                if proposed is not None:
                    raise OSError("simulated paired save failure")
                return store.save_world({"stock_wallet": wallet, "party": host.party})

            host.control = control
            host.checkpoint_native = AsyncMock(side_effect=checkpoint)
            bridge = SimpleNamespace(command=AsyncMock(side_effect=game_command), walk=AsyncMock())
            purchase = self.enqueue(control, "1", "native-finite-shop-buy", target=f"{GUID}@125")
            checkpoint_reply = self.enqueue(control, "2", "checkpoint")
            stop = self.enqueue(control, "3", "stop")
            walk = self.enqueue(control, "4", "walk", x=1, y=0, milliseconds=500)

            await Host.service_requests(host, bridge, None)

            self.assertEqual(read_json(purchase)["error"], "simulated paired save failure")
            self.assertEqual(wallet, 875)
            for reply in (checkpoint_reply, stop, walk):
                self.assertEqual(read_json(reply).get("error"), "native_checkpoint_unsafe")
            bridge.walk.assert_not_awaited()
            self.assertEqual(store.status()["revision"], 1)
            self.assertEqual(store.status()["world"]["stock_wallet"], 1000)
            self.assertEqual(store.status()["world"]["party"]["receipts"], {})
            self.assertTrue(host.stopping)

    async def test_orderly_stop_checkpoints_without_running_later_commands(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        host = self.host_without_fresh_guest_frame(SCIENTIST_SHOP)
        host.checkpoint_native.side_effect = (4, 5)
        host.smg_holds = None
        bridge = SimpleNamespace(walk=AsyncMock())
        with Control(root / "run") as control:
            host.control = control
            checkpoint = self.enqueue(control, "1", "checkpoint")
            stop = self.enqueue(control, "2", "stop")
            walk = self.enqueue(control, "3", "walk", x=1, y=0, milliseconds=500)
            await Host.service_requests(host, bridge, None)
            self.assertEqual(read_json(checkpoint)["revision"], 4)
            self.assertEqual(read_json(stop)["revision"], 5)
            self.assertEqual(read_json(walk).get("error"), "host_stopping")
        self.assertEqual(host.checkpoint_native.await_count, 2)
        bridge.walk.assert_not_awaited()
        self.assertTrue(host.stopping)

    def test_concurrent_guest_interaction_and_disconnect_cannot_checkpoint_pending_mutation(self):
        host = self.host_without_fresh_guest_frame(SCIENTIST_SHOP)
        host.native_checkpoint_unsafe = True
        host.checkpoint = Mock()
        host.store = Mock()
        host.guest = {"guest_id": "peer", "character_id": GUEST, "lease_id": "lease", "disconnected": None}
        host.members = Mock(grace_seconds=90)
        host.consume_guest_leave = lambda guest_id: False

        with self.assertRaisesRegex(party.RuleError, "native_transaction_busy"):
            Host.act(host, GUEST, OPERATION_ID, "interact", None)
        Host.disconnected(host, "peer")
        host.store.disconnect.assert_called_once_with("lease")
        MultiHost.disconnected(host, "peer")
        host.members.disconnect.assert_called_once_with("peer", intentional=False)
        host.checkpoint.assert_not_called()

        host.stopping = True
        create = {"action": "create-character", "name": "Second scout", "operation_id": OPERATION_ID}
        self.assertEqual(Host.guest_request(host, "peer", create), {"ok": False, "error": "native_checkpoint_unsafe"})
        host.store.create_linked_character.assert_not_called()

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
