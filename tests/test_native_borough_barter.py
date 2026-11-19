import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_borough_barter as barter, native_dialogue as dialogue, party


PAWN = "Player_FP_C " + dialogue.MAMA_LEVEL + "Player_FP_C_7"
PAYMENT, REWARD = "A" * 32, "B" * 32
OPERATION = "a" * 32


def frame():
    return ["native-borough-barter", dialogue.BOROUGH, PAWN, barter.POST, "exchanged", PAYMENT,
            barter.COPPER, "1", REWARD, barter.AMMO, "25", "60", "60", "0"]


class BoroughBarterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.party = {"receipts": {}, "players": {"guest": {"native_inventory": {PAYMENT: {}}}}}
        self.host = SimpleNamespace(
            dialogue_sessions=SimpleNamespace(
                maps={dialogue.BOROUGH}, member_scope=Mock(return_value=("boot", "visit", dialogue.BOROUGH, PAWN))),
            party=self.party, checkpoint_native=AsyncMock(return_value=9), native_checkpoint_unsafe=False, stopping=False,
            native_pickup=SimpleNamespace(forget_committed_row=Mock()), control=SimpleNamespace(event=Mock()),
        )
        self.proof = barter.parse(frame())
        self.bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": dialogue.BOROUGH, "native_borough_barter": self.proof}))

    def test_parser_checks_recipe_pawn_and_unchanged_balance(self):
        self.assertEqual(self.proof["receive"], {"guid": REWARD, "class_path": barter.AMMO, "quantity": 25})
        for column, value in [(2, "Player_FP_C elsewhere"), (3, "Trader elsewhere"), (8, PAYMENT),
                              (10, "24"), (12, "59"), (13, "1")]:
            with self.subTest(column=column), self.assertRaises(ValueError):
                fields = frame()
                fields[column] = value
                barter.parse(fields)

    async def test_commit_requires_guest_frame_replays_exact_receipt_and_never_duplicates(self):
        with patch.object(party, "load_party", side_effect=lambda state: {
            "players": {"guest": {"native_inventory": dict(state["players"]["guest"]["native_inventory"])}}, "receipts": {},
        }):
            result = await barter.trade(self.host, self.bridge, "guest", PAYMENT, OPERATION)
        self.assertEqual(result["native_borough_barter"], self.proof)
        self.assertEqual(self.host.checkpoint_native.await_args.kwargs["require_guest_frames"], frozenset({"guest"}))
        self.assertEqual(self.host.party["players"]["guest"]["native_inventory"], {})
        self.host.native_pickup.forget_committed_row.assert_called_once_with("guest", PAYMENT)
        self.assertFalse(self.host.native_checkpoint_unsafe)
        self.assertEqual(await barter.trade(self.host, self.bridge, "guest", PAYMENT, OPERATION), result)
        self.bridge.command.assert_awaited_once_with(barter.ACTION, PAWN, PAYMENT, timeout=6)
        with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
            await barter.trade(self.host, self.bridge, "guest", REWARD, OPERATION)

    async def test_native_pre_mutation_refusal_keeps_campaign_available(self):
        self.bridge.command.side_effect = RuntimeError("borough_barter_refused_trade_dialogue_expired")
        with self.assertRaisesRegex(party.RuleError, "trade_dialogue_expired"):
            await barter.trade(self.host, self.bridge, "guest", PAYMENT, OPERATION)
        self.assertFalse(self.host.native_checkpoint_unsafe)
        self.assertFalse(self.host.stopping)
        self.assertIs(self.host.party, self.party)

    async def test_unverified_result_stops_before_replacing_checkpoint(self):
        self.bridge.command.side_effect = RuntimeError("mutation_unverified_borough_barter_exchange")
        with self.assertRaisesRegex(RuntimeError, "mutation_unverified"):
            await barter.trade(self.host, self.bridge, "guest", PAYMENT, OPERATION)
        self.assertTrue(self.host.native_checkpoint_unsafe)
        self.assertTrue(self.host.stopping)
        self.host.checkpoint_native.assert_awaited_once_with(self.bridge)


class MamaDialogueTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_meet_safe_routes_and_exact_trade_response(self):
        profile = dialogue.PROFILES[dialogue.BOROUGH]
        rows = {
            row: {"lines": [f"Line {i}" for i in range(lines)],
                  "choices": [{"ordinal": number, "text": "Stock response"} for number in choices]}
            for row, (lines, choices) in profile.rows.items()
        }
        game = {"state": "hosting", "map": dialogue.BOROUGH}
        host = SimpleNamespace(
            party={"players": {"guest": {"downed": False}}, "wipe": None, "pending_spawns": {}},
            native_recipients=lambda: {"guest": {"connected": True, "pawn": PAWN}}, control=SimpleNamespace(generation="boot", event=Mock()),
            zone_epoch="visit", native_zones=True, native_guest=True, native_profile=True, transitioning=False, stopping=False,
            native_checkpoint_unsafe=False, last_game=game, last_seen=time.monotonic(), checkpoint_native=AsyncMock(return_value=5),
        )
        meet, rev_orders, commands = 0, 0, []
        connection = "TestConnection@1"

        async def invoke(command, pawn, *arguments, timeout):
            nonlocal meet
            commands.append(command)
            effect = "read"
            if command == dialogue.MAMA_OPEN:
                self.assertEqual(arguments, (connection, str(meet), "60", "0", str(rev_orders)))
                effect = "opened-repeat" if meet else "opened-first"
                meet = 1
            if command == dialogue.MAMA_TRADE:
                self.assertEqual(arguments, (dialogue.MAMA_ROOT, "5", connection))
                effect = "trade-granted"
            return {**game, "native_dialogue_stock": {"map": profile.map, "npc": profile.npc, "controller": profile.controller,
                    "pawn": pawn, "connection": connection, "rows": rows,
                    "progress": {"meet": meet, "cash": 60, "xp": 0, "rev_orders": rev_orders, "effect": effect}}}

        sessions = dialogue.DialogueSessions(mama=True)
        bridge = SimpleNamespace(command=invoke)
        async def reply(command, goal, operation):
            return (await sessions.act(host, bridge, "guest", command, goal, operation * 32))["native_dialogue"]

        rev_orders = 1
        with self.assertRaisesRegex(party.RuleError, "intro_questions_hidden"):
            await reply(dialogue.OPEN, dialogue.MAMA, "1")
        self.assertEqual(meet, 0)
        rev_orders = 0
        opened = await reply(dialogue.OPEN, dialogue.MAMA, "1")
        self.assertEqual((opened["row"], opened["effect"]), (dialogue.MAMA_FIRST, "opened-first"))
        with self.assertRaisesRegex(party.RuleError, "choice_unavailable"):
            await reply(dialogue.CHOOSE, opened["token"] + "@5", "2")
        rev_orders = 1
        with self.assertRaisesRegex(party.RuleError, "choice_unavailable"):
            await reply(dialogue.CHOOSE, opened["token"] + "@3", "3")
        rev_orders = 0
        questions = await reply(dialogue.CHOOSE, opened["token"] + "@3", "3")
        self.assertEqual(questions["row"], dialogue.MAMA_QUESTIONS)
        root = await reply(dialogue.CHOOSE, questions["token"] + "@1", "4")
        self.assertEqual(root["row"], dialogue.MAMA_ROOT)
        trade = await reply(dialogue.CHOOSE, root["token"] + "@5", "5")
        self.assertEqual((trade["state"], trade["effect"]), ("closed", "trade-granted"))
        self.assertEqual(commands.count(dialogue.MAMA_TRADE), 1)
        again = await reply(dialogue.OPEN, dialogue.MAMA, "6")
        self.assertEqual((again["row"], again["progress"]["meet"]), (dialogue.MAMA_ROOT, 1))
