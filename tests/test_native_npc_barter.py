import asyncio
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_npc_barter as barter, party
from tools.silverfish.travel import OBSERVATORY


TRADER = "025127D84BAC8B776150CCA7772B0300"
PAWN = f"Player_FP_C {OBSERVATORY}.Observitory_P:PersistentLevel.Player_FP_C_123"
OFFER = "A" * 32
PAYMENT = "B" * 32
OPERATION = "d" * 32
EPOCH = "c" * 32
BANDAGE = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
BEANS = "/Game/Classes/Items/Item_CannedBeans.Item_CannedBeans_C"


def exchange():
    summary = ["native-npc-barter-frame", OBSERVATORY, TRADER, PAWN, "30", "1", "1", "50", "123", "55.0"]
    stock = [["native-npc-barter-item", PAYMENT, BEANS, "2", "0.6", "30", "0", "32.5"]]
    moved = [["native-npc-barter-transfer", "give", PAYMENT, BEANS, "2"],
             ["native-npc-barter-transfer", "receive", OFFER, BANDAGE, "1"]]
    return summary, stock, moved, [["native-npc-barter-preference", BEANS, "0.9"]]


class BarterFrameTests(unittest.TestCase):
    def test_recognizes_whole_stock_rows_and_exact_transfer_direction(self):
        summary, stock, moved, preferences = exchange()
        proof = barter.parse(summary, stock, moved, preferences)
        self.assertEqual(proof["stock"][0]["quote"], 32.5)
        self.assertEqual(proof["transfers"][1]["guid"], OFFER)
        moved[1][2] = PAYMENT
        with self.assertRaises(ValueError):
            barter.parse(summary, stock, moved, preferences)

    def test_frame_qualifies_the_pawn_and_stable_merchant_on_another_map(self):
        from tools.silverfish.travel import SCIENTIST_SHOP

        summary, stock, moved, preferences = exchange()
        summary[1] = SCIENTIST_SHOP
        summary[2] = "3805FA9841394BDAFFD8D1A4ACD293DE"
        summary[3] = f"Player_FP_C {SCIENTIST_SHOP}.Shop_Scientist_01:PersistentLevel.Player_FP_C_345"
        proof = barter.parse(summary, stock, moved, preferences)
        self.assertEqual(proof["trader"], summary[2])
        summary[3] = PAWN
        with self.assertRaises(ValueError):
            barter.parse(summary, stock, moved, preferences)

    def test_positive_reputation_is_bound_to_an_item_contribution(self):
        summary, stock, moved, preferences = exchange()
        reputation = ["native-npc-barter-reputation", "Rep_Loners", "-20", "9"]
        proof = barter.parse(summary, stock, moved[:1], preferences, reputation)
        self.assertEqual(proof["reputation"], {"flag": "Rep_Loners", "before": -20, "after": 9})
        for invalid in (reputation[:3], [reputation[0], "Player_Cash", "-20", "9"],
                        [*reputation[:3], "-20"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                barter.parse(summary, stock, moved[:1], preferences, invalid)
        with self.assertRaises(ValueError):
            barter.parse(summary, stock, moved[1:], preferences, reputation)

    def test_stock_confirm_supports_item_only_offers_and_full_signed_wallet_range(self):
        view = [barter.VIEW, OBSERVATORY, PAWN, "2", "confirming", TRADER,
                f"{PAYMENT}:2", "-", "0"]
        self.assertEqual(barter.parse_view(view)["give"], f"{PAYMENT}:2")
        view[6:] = ["-", f"{OFFER}:1", str(barter.MAX_CASH)]
        self.assertEqual(barter.parse_view(view)["cash"], barter.MAX_CASH)
        view[8] = str(barter.MAX_CASH + 1)
        with self.assertRaises(ValueError):
            barter.parse_view(view)
        view[6:] = ["-", "-", "800"]
        with self.assertRaises(ValueError):
            barter.parse_view(view)

    def test_duplicate_guid_and_malformed_amount_are_refused(self):
        for encoded in (f"{OFFER}:2,{OFFER}:2", f"{OFFER}:0", f"{OFFER}:01", f"{OFFER}:1,"):
            with self.subTest(encoded=encoded), self.assertRaises(ValueError):
                barter.selection(encoded)


class BarterCommitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host = SimpleNamespace(
            native_recipients=lambda: {"guest": {"connected": True, "pawn": PAWN}},
            party={"players": {"guest": {"downed": False, "native_inventory": {PAYMENT: {}}}},
                   "receipts": {}, "wipe": None, "pending_spawns": {}},
            transitioning=False, stopping=False, native_checkpoint_unsafe=False, zone_epoch=EPOCH,
            last_seen=barter.time.monotonic(), last_game={"state": "hosting", "map": OBSERVATORY},
            native_pickup=SimpleNamespace(forget_committed_row=Mock()),
            checkpoint_native=AsyncMock(return_value=11), control=SimpleNamespace(event=Mock()),
        )
        self.bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": OBSERVATORY, barter.STOCK: barter.parse(*exchange()),
        }))
        self.target = f"{EPOCH}@{TRADER}@20@{PAYMENT}:2@{OFFER}:1"

    async def test_whole_native_exchange_replays_after_required_guest_checkpoint(self):
        from copy import deepcopy

        with patch.object(party, "load_party", side_effect=deepcopy):
            first = await barter.perform(self.host, self.bridge, "guest", barter.EXCHANGE, self.target, OPERATION)
            second = await barter.perform(self.host, self.bridge, "guest", barter.EXCHANGE, self.target, OPERATION)
        self.assertEqual(first, second)
        self.bridge.command.assert_awaited_once_with(barter.EXCHANGE, PAWN, TRADER, f"{PAYMENT}:2", f"{OFFER}:1", "20", timeout=7)
        self.assertEqual(self.host.checkpoint_native.await_args.kwargs["require_guest_frames"], {"guest"})
        self.assertEqual(self.host.party["players"]["guest"]["native_inventory"], {})
        self.assertFalse(self.host.native_checkpoint_unsafe)

    async def test_item_donation_commits_reputation_once_without_spending_cash(self):
        from copy import deepcopy

        summary, stock, moved, preferences = exchange()
        summary[4] = summary[7] = "400"
        reputation = ["native-npc-barter-reputation", "Rep_Loners", "100", "150"]
        self.bridge.command.return_value[barter.STOCK] = barter.parse(summary, stock, moved[:1], preferences, reputation)
        target = f"{EPOCH}@{TRADER}@0@{PAYMENT}:2@-"
        with patch.object(party, "load_party", side_effect=deepcopy):
            first = await barter.perform(self.host, self.bridge, "guest", barter.EXCHANGE, target, OPERATION)
            replay = await barter.perform(self.host, self.bridge, "guest", barter.EXCHANGE, target, OPERATION)
        self.assertEqual(first, replay)
        self.assertEqual(first[barter.STOCK]["reputation"]["after"], 150)
        self.bridge.command.assert_awaited_once_with(barter.EXCHANGE, PAWN, TRADER, f"{PAYMENT}:2", "-", "0", timeout=7)
        self.assertEqual(self.host.party["receipts"]["guest"][0]["result"], first)

    async def test_ordinary_quote_refusal_preserves_the_writable_campaign(self):
        self.bridge.command.side_effect = RuntimeError("barter_refused_native_balance")
        with self.assertRaisesRegex(party.RuleError, "barter_refused_native_balance"):
            await barter.perform(self.host, self.bridge, "guest", barter.EXCHANGE, self.target, OPERATION)
        self.assertFalse(self.host.native_checkpoint_unsafe or self.host.stopping)
        self.assertIn(PAYMENT, self.host.party["players"]["guest"]["native_inventory"])

    async def test_unchanged_stock_read_returns_only_the_version_and_skips_checkpoints(self):
        target = f"{EPOCH}@{TRADER}"
        first = await barter.perform(self.host, self.bridge, "guest", barter.OPEN, target, OPERATION)
        version = first[barter.STOCK_VERSION]
        unchanged = await barter.perform(self.host, self.bridge, "guest", barter.OPEN, f"{target}@{version}", OPERATION)
        self.assertEqual(unchanged, {barter.STOCK_VERSION: version})
        self.host.checkpoint_native.assert_not_awaited()


class BarterViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_stock_stays_pending_until_the_open_screen_accepts_it(self):
        before = barter.parse(*exchange())
        changed = {**before, "cash": 31}
        changed_version = barter.stock_version(changed)
        frame = {"map": OBSERVATORY, "pawns": {"local": {"name": PAWN}}, barter.VIEW: {
            "map": OBSERVATORY, "pawn": PAWN, "state": "ready", "sequence": 2, "trader": TRADER,
        }}
        bridge = SimpleNamespace(input_namespace=uuid.uuid4(), command=AsyncMock(side_effect=[
            {**frame, "ack": {"message": "stock NPC barter selection pending"}},
            {**frame, "ack": {"message": "exact stock NPC barter screen shown"}},
        ]))
        client = SimpleNamespace(
            host_session="host", zone_epoch=EPOCH, bridge=bridge, last_game=frame, control=SimpleNamespace(event=Mock()),
            gameplay_action=AsyncMock(return_value={barter.STOCK: changed, barter.STOCK_VERSION: changed_version}),
        )
        view = barter.View()
        view.scope = client.host_session, EPOCH, bridge.input_namespace
        view.version = barter.stock_version(before)
        await view.sync(client, True)
        bridge.command.assert_not_awaited()
        await asyncio.sleep(0)
        await view.sync(client, True)
        self.assertEqual(view.version, barter.stock_version(before))
        view.next_refresh = 0
        await view.sync(client, True)
        await asyncio.sleep(0)
        await view.sync(client, True)
        self.assertEqual(view.version, changed_version)
        self.assertEqual(bridge.command.await_args.args[:4], (barter.REFRESH, "2", TRADER, "31"))
        client.control.event.assert_called_once_with("native_npc_barter_stock_refreshed", trader=TRADER, entries=1, cash=31)
