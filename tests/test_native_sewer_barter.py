import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_sewer_barter as barter, party


PAWN = "Player_FP_C " + barter.PREFIX + "Player_FP_C_12345"
OTHER = "Player_FP_C " + barter.PREFIX + "Player_FP_C_99999"
LOCKPICK = "/Game/Classes/Equipment/Eqp_LockPick.Eqp_LockPick_C"
MOLASSES = "/Game/Classes/Items/Item_Molasis.Item_Molasis_C"
MASK = "/Game/Classes/Items/Item_PigmeMask.Item_PigmeMask_C"
PAYMENT = "A" * 32
REWARD = "B" * 32
OPERATION = "c" * 32


def offer_frames():
    header = [barter.OFFERS, barter.CISTERN, PAWN, barter.POST, str(barter.COUNT)]
    rows = [[barter.OFFER_ROW, str(index), "1", "0", LOCKPICK, "1", barter.MUSHROOM, "1", "Stock offer"]
            for index in range(1, barter.COUNT + 1)]
    rows[19][-1:-1] = [MOLASSES, "2", MASK, "5"]
    return header, rows


def exchange_frames():
    header = [barter.ACTION, barter.CISTERN, PAWN, barter.POST, "2", REWARD, LOCKPICK, "1", "250", "250", "1"]
    rows = [[barter.DEBIT_ROW, PAYMENT, barter.MUSHROOM, "1", "0"]]
    return header, rows


class SewerBarterReportTests(unittest.TestCase):
    def test_all_offers_and_multiple_ingredient_counts(self):
        key, proof = barter.parse(*offer_frames())
        self.assertEqual((key, proof["pawn"], len(proof["offers"])), (barter.OFFERS_KEY, PAWN, 26))
        self.assertEqual([item["quantity"] for item in proof["offers"][19]["give"]], [1, 2, 5])


    def test_projection_uses_typed_paths_and_orders_costs(self):
        offers = barter.parse(*offer_frames())[1]["offers"]
        offers[19]["hidden"] = True
        offers[19]["affordable"] = False
        arguments = barter.projection_arguments(offers)
        self.assertEqual((arguments[0], len(arguments)), ("typed-v1", 27))
        self.assertEqual(arguments[20].split("|"),
                         ["0", "1", LOCKPICK, "1", barter.MUSHROOM, "1", MOLASSES, "2", MASK, "5"])
        with self.assertRaises(ValueError):
            barter.projection_arguments(offers[:-1])
        offers[19]["give"][1] = offers[19]["give"][0]
        with self.assertRaises(ValueError):
            barter.projection_arguments(offers)

    def test_offer_report_must_have_exact_scope_and_order(self):
        header, rows = offer_frames()
        for index, value in [(1, "/WrongMap"), (2, "Player_FP_C /WrongMap.Pawn_1"), (4, "27")]:
            changed = header.copy()
            changed[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                barter.parse(changed, rows)
        with self.assertRaises(ValueError):
            barter.parse(header, rows[1:] + rows[:1])

    def test_exchange_refuses_wallet_change_or_reused_reward_identity(self):
        header, rows = exchange_frames()
        self.assertEqual(barter.parse(header, rows)[1]["spent"][0]["guid"], PAYMENT)
        header[9] = "251"
        with self.assertRaises(ValueError):
            barter.parse(header, rows)
        header[9] = "250"
        rows[0][1] = REWARD
        with self.assertRaises(ValueError):
            barter.parse(header, rows)


class SewerBarterCommitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        scope = ("session", "visit", barter.CISTERN, PAWN)
        self.proof = barter.parse(*exchange_frames())[1]
        self.original = {"players": {"guest": {"native_inventory": {PAYMENT: {}}},
                                     "another": {"native_inventory": {PAYMENT: {}}}}, "receipts": {}}
        self.host = SimpleNamespace(
            last_game={},
            dialogue_sessions=SimpleNamespace(maps={barter.CISTERN}, member_scope=Mock(return_value=scope)),
            party=self.original, checkpoint_native=AsyncMock(return_value=15),
            native_pickup=SimpleNamespace(forget_committed_row=Mock()), control=SimpleNamespace(event=Mock()),
            native_checkpoint_unsafe=False, stopping=False,
        )
        self.bridge = SimpleNamespace(command=AsyncMock(return_value={
            "map": barter.CISTERN, "state": "hosting", barter.TRADE_KEY: self.proof,
        }))

    async def test_commit_is_durable_and_replay_never_debits_again(self):
        with patch.object(party, "load_party", side_effect=lambda data: {
            "players": {name: {"native_inventory": player["native_inventory"].copy()}
                        for name, player in data["players"].items()}, "receipts": {},
        }):
            first = await barter.exchange(self.host, self.bridge, "guest", "2", OPERATION)
            again = await barter.exchange(self.host, self.bridge, "guest", "2", OPERATION)
            with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
                await barter.exchange(self.host, self.bridge, "guest", "3", OPERATION)
        self.assertEqual(first, again)
        self.assertEqual(self.host.party["players"]["guest"]["native_inventory"], {})
        self.assertEqual(self.host.party["players"]["another"]["native_inventory"], {PAYMENT: {}})
        self.bridge.command.assert_awaited_once_with(barter.ACTION, PAWN, "2", timeout=7)
        self.assertEqual(self.host.checkpoint_native.await_args.kwargs["require_guest_frames"], {"guest"})
        self.assertFalse(self.host.native_checkpoint_unsafe or self.host.stopping)

    async def test_clean_trade_refusal_keeps_the_session_writable(self):
        self.bridge.command.side_effect = RuntimeError("sewer_trade_refused_unaffordable")
        with self.assertRaisesRegex(party.RuleError, "unaffordable"):
            await barter.exchange(self.host, self.bridge, "guest", "2", OPERATION)
        self.assertFalse(self.host.native_checkpoint_unsafe or self.host.stopping)
        self.assertIs(self.host.party, self.original)

    async def test_wrong_pawn_receipt_stops_before_checkpointing_a_mutation(self):
        self.proof["pawn"] = OTHER
        with self.assertRaisesRegex(RuntimeError, "current guest and post"):
            await barter.exchange(self.host, self.bridge, "guest", "2", OPERATION)
        self.assertTrue(self.host.native_checkpoint_unsafe and self.host.stopping)
        self.host.checkpoint_native.assert_awaited_once_with(self.bridge)
