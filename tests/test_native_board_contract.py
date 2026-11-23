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
VISIT = "12" * 16


def result(quest, verb, mode, before, expected, *, cash=0, xp=0, guid="-", board_name=BOARD,
           raw_units=1, remaining=0, consumed=None):
    applied = mode == "apply"
    if consumed is None:
        consumed = guid if applied else "-"
    fields = (board.COMMAND, verb, mode, SHOPKEEP, PAWN, board_name, quest,
              "applied" if applied else "qualified", "-" if before is None else str(before),
              str(expected) if applied else "-" if before is None else str(before),
              str(expected), "17", str(17 + cash if applied else 17), "3", str(3 + xp if applied else 3),
              str(cash), str(xp), guid, str(raw_units) if guid != "-" else "-",
              str(remaining) if guid != "-" else "-", consumed)
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
        self.assertIs(host.pending_host_save, False)
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertEqual(bridge.command.await_count, 4)
        host.checkpoint_native.assert_not_awaited()
        host.checkpoint.assert_not_called()

    async def test_selected_board_cannot_be_substituted_by_another_board_in_the_same_map(self):
        host = self.host()
        host.zone_epoch = VISIT
        second = BOARD.removesuffix("_1") + "_2"
        bridge = SimpleNamespace(command=AsyncMock())
        selection = board.menu_target(VISIT, BOARD, EYE)

        # The other board occupies the same level and could offer the same contract.
        # Its availability cannot replace the board chosen by the opening receipt.
        with patch.dict(native_computer_board.BOARDS, {SHOPKEEP: second}):
            with self.assertRaisesRegex(party.RuleError, "native_board_contract_unavailable"):
                await native_quest.action(host, bridge, "guest", "native-quest-start", selection)
        bridge.command.assert_not_awaited()

        with self.assertRaisesRegex(party.RuleError, "native_board_contract_stale_visit"):
            await native_quest.action(
                host, bridge, "guest", "native-quest-start", board.menu_target("34" * 16, BOARD, EYE))
        bridge.command.assert_not_awaited()

        bridge.command.return_value = result(EYE, "start", "inspect", None, 1, board_name=second)
        with self.assertRaisesRegex(RuntimeError, "did not identify the current actor and board"):
            await native_quest.action(host, bridge, "guest", "native-quest-start", selection)
        bridge.command.assert_awaited_once_with(board.COMMAND, "start", "inspect", PAWN, BOARD, EYE, timeout=5)
        self.assertEqual(host.party.get("native_quests", {}), {})
        self.assertFalse(host.pending_host_save or host.native_checkpoint_unsafe)

        bridge.command.reset_mock()
        bridge.command.side_effect = (
            result(EYE, "start", "inspect", None, 1), result(EYE, "start", "apply", None, 1),
        )
        started = await native_quest.action(host, bridge, "guest", "native-quest-start", selection)
        self.assertEqual(started["step"], 1)
        self.assertEqual([call.args[4] for call in bridge.command.await_args_list], [BOARD, BOARD])

    async def test_current_board_visit_is_rechecked_after_awaiting_inspection(self):
        host = self.host()
        host.zone_epoch = VISIT

        async def inspect(*_arguments, **_options):
            host.zone_epoch = "34" * 16
            return result(EYE, "start", "inspect", None, 1)

        bridge = SimpleNamespace(command=AsyncMock(side_effect=inspect))
        with self.assertRaisesRegex(party.RuleError, "native_board_contract_stale_visit"):
            await native_quest.action(
                host, bridge, "guest", "native-quest-start", board.menu_target(VISIT, BOARD, EYE))
        self.assertEqual(bridge.command.await_count, 1)
        self.assertFalse(host.native_checkpoint_unsafe or host.stopping)
        self.assertEqual(host.party.get("native_quests", {}), {})

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
        self.assertIs(host.pending_host_save, False)
        host.checkpoint_native.assert_not_awaited()

    async def test_guest_deposit_preserves_the_original_stack_and_counts_one_unit(self):
        host = self.host()
        split_guid = "37" * 16
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            result(EYE, "turnin", "inspect", 1, -10, cash=801, xp=1, guid=GUID, raw_units=2, remaining=1),
            result(EYE, "turnin", "apply", 1, -10, cash=801, xp=1, guid=GUID, raw_units=2,
                   remaining=1, consumed=split_guid),
        ]))
        with patch.object(board.native_inventory, "retain_guest_save", new_callable=AsyncMock) as retain:
            retain.return_value = True
            paid = await board.perform(host, bridge, "guest", "turnin", EYE)
        self.assertEqual((paid["objective_guid"], paid["consumed_guid"]), (GUID, split_guid))
        self.assertEqual((paid["objective_units_before"], paid["objective_units_consumed"],
                          paid["objective_units_remaining"]), (2, 1, 1))
        self.assertEqual(bridge.command.await_args.args[-2:], (GUID, "2"))
        bridge.command.reset_mock()
        replay = await board.perform(host, bridge, "guest", "turnin", EYE)
        self.assertEqual(replay["objective_units_remaining"], 1)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()

    async def test_split_receipt_cannot_consume_the_original_remainder_guid(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            result(EYE, "turnin", "inspect", 1, -10, cash=801, xp=1, guid=GUID, raw_units=2, remaining=1),
            result(EYE, "turnin", "apply", 1, -10, cash=801, xp=1, guid=GUID, raw_units=2,
                   remaining=1, consumed=GUID),
        ]))
        with patch.object(board.native_inventory, "retain_guest_save", new_callable=AsyncMock) as retain:
            retain.return_value = True
            with self.assertRaisesRegex(RuntimeError, "wrong consumed or remaining item"):
                await board.perform(host, bridge, "guest", "turnin", EYE)
        self.assertEqual(host.party.get("native_quests", {}), {})
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
        host.checkpoint_native.assert_not_awaited()

    async def test_partial_host_deposit_keeps_guid_for_typed_checkpoint(self):
        host = self.host()
        host.last_game["pawns"] = {"local": {"name": PAWN}}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            result(EYE, "turnin", "inspect", 1, -10, cash=801, xp=1, guid=GUID, raw_units=3, remaining=2),
            result(EYE, "turnin", "apply", 1, -10, cash=801, xp=1, guid=GUID, raw_units=3,
                   remaining=2, consumed="-"),
        ]))
        paid = await board.perform(host, bridge, "host", "turnin", EYE)
        self.assertEqual((paid["objective_guid"], paid["objective_units_remaining"]), (GUID, 2))
        self.assertNotIn("consumed_guid", paid)
        self.assertIs(host.pending_host_save, True)
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

    async def test_host_objective_only_marks_deferred_typed_inventory_capture(self):
        host = self.host()
        host.last_game["pawns"] = {"local": {"name": PAWN}}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            result(EYE, "turnin", "inspect", 1, -10, cash=801, xp=1, guid=GUID),
            result(EYE, "turnin", "apply", 1, -10, cash=801, xp=1, guid=GUID),
        ]))
        paid = await board.perform(host, bridge, "host", "turnin", EYE)
        self.assertEqual(paid["objective_owner"], "host")
        self.assertIs(host.pending_host_save, True)
        host.checkpoint_native.assert_not_awaited()

    async def test_catalog_binds_both_sets_to_exact_board_and_actor(self):
        host = self.host()
        fields = (board.CATALOG, SHOPKEEP, PAWN, BOARD, "1", "1", UNREGISTERED_KILL, EYE)
        view = board.parse_catalog(fields)
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": SHOPKEEP, board.CATALOG_KEY: view,
        }))
        self.assertEqual(await board.catalog(host, bridge, "guest", BOARD), {board.CATALOG_KEY: {
            "map": SHOPKEEP, "pawn": PAWN, "board": BOARD, "offers": [UNREGISTERED_KILL], "turnins": [EYE],
        }})
        bridge.command.assert_awaited_once_with(board.CATALOG, PAWN, BOARD, timeout=5)
        host.checkpoint_native.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, "both offered and ready"):
            board.parse_catalog((*fields[:-1], UNREGISTERED_KILL))
        with self.assertRaisesRegex(ValueError, "Invalid stock board catalog entries"):
            board.parse_catalog((*fields[:4], "2", *fields[5:]))

    def test_repeated_proof_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = runtime.Bridge(Path(directory), None)
            valid = f"{board.COMMAND}\tstart\tinspect\t{SHOPKEEP}\t{PAWN}\t{BOARD}\t{EYE}\tqualified\t-\t-\t1\t17\t17\t3\t3\t0\t0\t-\t-\t-\t-"
            prefix = f"schema\t1\nsession\thost\thosting\t{SHOPKEEP}\t1\t0\n"
            self.assertEqual(bridge._parse_status(prefix + valid + "\n")[board.KEY]["quest"], EYE)
            self.assertIsNone(bridge._parse_status(prefix + valid + "\n" + valid + "\n"))
