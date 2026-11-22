import unittest
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

    async def test_ordinary_quote_refusal_preserves_the_writable_campaign(self):
        self.bridge.command.side_effect = RuntimeError("barter_refused_native_balance")
        with self.assertRaisesRegex(party.RuleError, "barter_refused_native_balance"):
            await barter.perform(self.host, self.bridge, "guest", barter.EXCHANGE, self.target, OPERATION)
        self.assertFalse(self.host.native_checkpoint_unsafe or self.host.stopping)
        self.assertIn(PAYMENT, self.host.party["players"]["guest"]["native_inventory"])
