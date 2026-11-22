import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_board_contract as board, native_computer_board, native_quest, native_rewards, party, runtime
from tools.silverfish.travel import SHOPKEEP


EYE = "Clt_Eye_02"
UNREGISTERED_KILL = "Asn_EggHead"
GUID = "19" * 16
PAWN = "Player_FP_C ExactGuest"
BOARD = native_computer_board.BOARDS[SHOPKEEP]


def result(quest, verb, mode, before, expected, *, cash=0, xp=0, guid="-", board_name=BOARD):
    applied = mode == "apply"
    fields = (board.COMMAND, verb, mode, SHOPKEEP, PAWN, board_name, quest,
              "applied" if applied else "qualified", "-" if before is None else str(before),
              str(expected) if applied else "-" if before is None else str(before),
              str(expected), "17", str(17 + cash if applied else 17), "3", str(3 + xp if applied else 3),
              str(cash), str(xp), guid)
    return {"state": "hosting", "map": SHOPKEEP, board.KEY: board.parse(fields)}


class DynamicBoardTests(unittest.IsolatedAsyncioTestCase):
    def host(self):
        saved = party.new_party()
        for character in ("host", "guest", "offline"):
            party.add_player(saved, character)
        return SimpleNamespace(
            native_profile=True, native_zones=True, native_guest=True, stopping=False, transitioning=False,
            native_checkpoint_unsafe=False, pending_host_save=False, party=saved,
            last_seen=time.monotonic(), last_game={"state": "hosting", "map": SHOPKEEP},
            checkpoint_native=AsyncMock(), checkpoint=Mock(), control=Mock(),
            roles=lambda: {"host": "host", "guest": "guest"},
            native_recipients=lambda: {"guest": {"pawn": PAWN, "connected": True}},
        )

    async def test_unregistered_collection_uses_selected_stock_recipe_then_replays_once(self):
        self.assertNotIn(EYE, native_computer_board.CONTRACTS)
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            result(EYE, "start", "inspect", None, 1),
            result(EYE, "start", "apply", None, 1),
            result(EYE, "turnin", "inspect", 1, -10, cash=801, xp=1, guid=GUID),
            result(EYE, "turnin", "apply", 1, -10, cash=801, xp=1, guid=GUID),
        ]))
        started = await native_quest.action(host, bridge, "guest", "native-quest-start", EYE)
        self.assertEqual(started, {"effect": "native_quest_changed", "quest_id": EYE, "step": 1, "actor": "guest"})
        with patch.object(board.native_inventory, "retain_guest_save", new_callable=AsyncMock) as retain:
            retain.return_value = True
            paid = await native_quest.action(host, bridge, "guest", "native-quest-turnin", EYE)
            replay = await native_quest.action(host, bridge, "guest", "native-quest-turnin", EYE)
            retain.assert_awaited_once_with(host, bridge, "guest", PAWN)
        self.assertEqual((paid["step"], paid["cash"], paid["wallet"], paid["consumed_guid"], paid["objective_owner"]),
                         (-10, 801, 818, GUID, "guest"))
        self.assertEqual(paid["recipients"], ["guest", "host"])
        self.assertEqual(replay, {**paid, "effect": "native_quest_already_settled"})
        self.assertEqual(host.party["players"]["guest"][native_rewards.PENDING], 1)
        self.assertEqual(host.party["players"]["host"]["quest_xp"], 1)
        self.assertNotIn(native_rewards.PENDING, host.party["players"]["offline"])
        self.assertEqual(host.party["economy"]["wallet"], 818)
        self.assertTrue(host.pending_host_save)
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertEqual(bridge.command.await_count, 4)
        host.checkpoint_native.assert_not_awaited()
        host.checkpoint.assert_not_called()

    async def test_stock_terminal_without_deposit_uses_native_progress_and_no_retention(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            RuntimeError("stock_board_contract_objective_not_complete"),
            result(UNREGISTERED_KILL, "turnin", "inspect", 2, -10, cash=3000, xp=1),
            result(UNREGISTERED_KILL, "turnin", "apply", 2, -10, cash=3000, xp=1),
        ]))
        with patch.object(board.native_inventory, "retain_guest_save", new_callable=AsyncMock) as retain:
            with self.assertRaisesRegex(party.RuleError, "objective_not_complete"):
                await board.perform(host, bridge, "guest", "turnin", UNREGISTERED_KILL)
            self.assertFalse(host.pending_host_save)
            paid = await board.perform(host, bridge, "guest", "turnin", UNREGISTERED_KILL)
            retain.assert_not_awaited()
        self.assertEqual((paid["step"], paid["cash"]), (-10, 3000))
        self.assertNotIn("consumed_guid", paid)
        host.checkpoint_native.assert_not_awaited()

    async def test_refuses_wrong_board_and_missing_retention_without_mutation(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(return_value=
            result(EYE, "turnin", "inspect", 1, -10, cash=801, xp=1, guid=GUID)))
        with self.assertRaisesRegex(party.RuleError, "unavailable"):
            await board.perform(host, bridge, "guest", "turnin", EYE, board="wrong board")
        bridge.command.assert_not_awaited()
        with patch.object(board.native_inventory, "retain_guest_save", new_callable=AsyncMock) as retain:
            retain.return_value = False
            with self.assertRaisesRegex(party.RuleError, "retention_unavailable"):
                await board.perform(host, bridge, "guest", "turnin", EYE)
        self.assertEqual(bridge.command.await_count, 1)
        self.assertFalse(host.native_checkpoint_unsafe or host.stopping or host.pending_host_save)
        host.checkpoint_native.assert_not_awaited()

    async def test_mismatched_native_mutation_cannot_publish_a_receipt(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            result(UNREGISTERED_KILL, "turnin", "inspect", 2, -10, cash=3000, xp=1),
            result(UNREGISTERED_KILL, "turnin", "apply", 2, -10, cash=3001, xp=1),
        ]))
        with self.assertRaisesRegex(RuntimeError, "differs from"):
            await board.perform(host, bridge, "guest", "turnin", UNREGISTERED_KILL)
        self.assertEqual(host.party.get("native_quests", {}), {})
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
        host.checkpoint_native.assert_not_awaited()

    def test_repeated_proof_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = runtime.Bridge(Path(directory), None)
            valid = f"{board.COMMAND}\tstart\tinspect\t{SHOPKEEP}\t{PAWN}\t{BOARD}\t{EYE}\tqualified\t-\t-\t1\t17\t17\t3\t3\t0\t0\t-"
            prefix = f"schema\t1\nsession\thost\thosting\t{SHOPKEEP}\t1\t0\n"
            self.assertEqual(bridge._parse_status(prefix + valid + "\n")[board.KEY]["quest"], EYE)
            self.assertIsNone(bridge._parse_status(prefix + valid + "\n" + valid + "\n"))
