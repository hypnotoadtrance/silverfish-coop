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
           raw_units=1, remaining=0, consumed=None, additional="-", rewards="-", followups=None):
    applied = mode == "apply"
    if consumed is None:
        consumed = guid if applied else "-"
    fields = (board.COMMAND, verb, mode, SHOPKEEP, PAWN, board_name, quest,
              "applied" if applied else "qualified", "-" if before is None else str(before),
              str(expected) if applied else "-" if before is None else str(before),
              str(expected), "17", str(17 + cash if applied else 17), "3", str(3 + xp if applied else 3),
              str(cash), str(xp), guid, str(raw_units) if guid != "-" else "-",
              str(remaining) if guid != "-" else "-", consumed, additional, rewards)
    if followups is not None:
        fields = (*fields, followups)
    return {"state": "hosting", "map": SHOPKEEP, board.KEY: board.parse(fields)}


class DynamicBoardTests(unittest.IsolatedAsyncioTestCase):
    def host(self):
        saved = party.new_party()
        for character in ("host", "guest", "offline"):
            party.add_player(saved, character)
        return SimpleNamespace(
            native_profile=True, native_zones=True, native_guest=True, stopping=False, transitioning=False,
            allowed_maps={SHOPKEEP},
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

    async def test_reward_only_contract_retains_the_guest_and_checks_live_receipt(self):
        quest = "WorkshopReward"
        reward = "/FrontierPack/Items/Waterskin.Waterskin_C"
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            result(quest, "turnin", "inspect", 2, -10, cash=500, xp=1, rewards=reward),
            result(quest, "turnin", "apply", 2, -10, cash=500, xp=1, rewards=reward),
        ]))
        with patch.object(board.native_inventory, "retain_guest_save", new_callable=AsyncMock, return_value=True) as retain:
            paid = await board.perform(host, bridge, "guest", "turnin", quest)
        retain.assert_awaited_once_with(host, bridge, "guest", PAWN)
        self.assertEqual(bridge.command.await_args.args[-1], reward)
        self.assertEqual((paid["item_rewards"], paid["item_reward_owner"]), ([reward], "guest"))
        self.assertEqual(paid["recipients"], ["guest", "host"])
        self.assertFalse(host.pending_host_save or host.native_checkpoint_unsafe)
        host.checkpoint_native.assert_not_awaited()
        invalid = result(quest, "turnin", "apply", 2, -10, rewards=reward)[board.KEY]
        before = result(quest, "turnin", "inspect", 2, -10)[board.KEY]
        with self.assertRaisesRegex(RuntimeError, "inspected item rewards"):
            board._receipt("guest", before, invalid)

    async def test_multiple_objectives_bind_every_debit_and_pay_once(self):
        armor = "Clct_Armor_Simmi"
        helmet, split = "34" * 16, "23" * 16
        inspected = f"{helmet}:0:0:-"
        applied = f"{helmet}:0:0:{helmet}"
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            result(armor, "turnin", "inspect", 1, -10, cash=8000, xp=1, guid=GUID,
                   raw_units=2, remaining=1, additional=inspected.replace(":0:0:", ":-1:0:")),
            result(armor, "turnin", "apply", 1, -10, cash=8000, xp=1, guid=GUID,
                   raw_units=2, remaining=1, consumed=split, additional=applied.replace(":0:0:", ":-1:0:")),
        ]))
        with patch.object(board.native_inventory, "retain_guest_save", return_value=True) as retain:
            paid = await board.perform(host, bridge, "guest", "turnin", armor)
            retain.assert_awaited_once()
        self.assertTrue(bridge.command.await_args.args[-1].endswith(f"|{helmet}:-1:0"))
        self.assertEqual([(row["objective_guid"], row["objective_units_remaining"], row["consumed_guid"])
                          for row in paid["objective_deposits"]], [(GUID, 1, split), (helmet, 0, helmet)])
        self.assertEqual((paid["cash"], paid["wallet"]), (8000, 8017))
        self.assertEqual((await board.perform(host, bridge, "guest", "turnin", armor))["effect"],
                         "native_quest_already_settled")
        self.assertEqual(bridge.command.await_count, 2)

    def test_invalid_additional_objectives_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unqualified additional"):
            result(EYE, "turnin", "inspect", 1, -10, guid=GUID, additional=f"{GUID}:1:0:-")
        with self.assertRaisesRegex(ValueError, "Unexpected additional"):
            result(EYE, "start", "inspect", None, 1, additional=f"{GUID}:1:0:-")

    async def test_stock_quest_followups_are_bound_to_the_turnin_and_replay(self):
        next_quests = "NewQuest:1:0,ExistingQuest:-10:1"
        before = result(EYE, "turnin", "inspect", 2, -10, cash=801, followups=next_quests)
        applied = result(EYE, "turnin", "apply", 2, -10, cash=801, followups=next_quests)
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[before, applied]))
        paid = await board.perform(host, bridge, "guest", "turnin", EYE)
        self.assertTrue(bridge.command.await_args.args[-1].endswith(f"|followups={next_quests}"))
        self.assertEqual(paid["followups"], [
            {"quest_id": "NewQuest", "step": 1, "present": False},
            {"quest_id": "ExistingQuest", "step": -10, "present": True},
        ])
        replay = await board.perform(host, bridge, "guest", "turnin", EYE)
        self.assertEqual(replay, {**paid, "effect": "native_quest_already_settled"})
        self.assertEqual(bridge.command.await_count, 2)
        host.checkpoint_native.assert_not_awaited()
        wrong = result(EYE, "turnin", "apply", 2, -10, cash=801, followups="OtherQuest:1:0")[board.KEY]
        with self.assertRaisesRegex(RuntimeError, "inspected follow-ups"):
            board._receipt("guest", before[board.KEY], wrong)
        for invalid in (f"{EYE}:1:0", "NewQuest:01:0", "NewQuest:0:0", "None:1:0",
                        "NewQuest:1:0,NewQuest:2:1", "NewQuest:1:2"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                result(EYE, "turnin", "inspect", 2, -10, followups=invalid)

    def test_largest_supported_objective_receipt_survives_parsing(self):
        deposited = ",".join(f"{index:032X}:2147483647:2147483646:{index + 100:032X}"
                             for index in range(1, board.MAX_OBJECTIVES))
        self.assertGreater(len(deposited), 1200)
        proof = result(EYE, "turnin", "apply", 1, -10, guid=GUID, additional=deposited)
        self.assertEqual(len(proof[board.KEY]["additional_deposits"]), board.MAX_OBJECTIVES - 1)

    async def test_selected_board_cannot_be_substituted_by_another_board_in_the_same_map(self):
        host = self.host()
        host.zone_epoch = VISIT
        second = BOARD.removesuffix("_1") + "_2"
        bridge = SimpleNamespace(command=AsyncMock())
        selection = board.menu_target(VISIT, second, EYE)

        specialist = native_computer_board.MOLASSES.quest_id
        specialist_selection = board.menu_target(VISIT, second, specialist)
        with self.assertRaisesRegex(party.RuleError, "native_board_contract_requires_authored_board"):
            await native_quest.action(host, bridge, "guest", "native-quest-turnin", specialist_selection)
        bridge.command.assert_not_awaited()
        bridge.command.side_effect = (
            result(specialist, "start", "inspect", None, 1, board_name=second),
            result(specialist, "start", "apply", None, 1, board_name=second),
        )
        started = await native_quest.action(host, bridge, "guest", "native-quest-start", specialist_selection)
        self.assertEqual(started["step"], 1)
        self.assertEqual([call.args[4] for call in bridge.command.await_args_list], [second, second])
        bridge.command.reset_mock()
        bridge.command.side_effect = None

        with self.assertRaisesRegex(party.RuleError, "native_board_contract_stale_visit"):
            await native_quest.action(
                host, bridge, "guest", "native-quest-start", board.menu_target("34" * 16, BOARD, EYE))
        bridge.command.assert_not_awaited()

        bridge.command.return_value = result(EYE, "start", "inspect", None, 1, board_name=BOARD)
        with self.assertRaisesRegex(RuntimeError, "did not identify the current actor and board"):
            await native_quest.action(host, bridge, "guest", "native-quest-start", selection)
        bridge.command.assert_awaited_once_with(board.COMMAND, "start", "inspect", PAWN, second, EYE, timeout=5)
        self.assertEqual(set(host.party["native_quests"]), {specialist})
        self.assertFalse(host.pending_host_save or host.native_checkpoint_unsafe)

        bridge.command.reset_mock()
        bridge.command.side_effect = (
            result(EYE, "start", "inspect", None, 1, board_name=second),
            result(EYE, "start", "apply", None, 1, board_name=second),
        )
        started = await native_quest.action(host, bridge, "guest", "native-quest-start", selection)
        self.assertEqual(started["step"], 1)
        self.assertEqual([call.args[4] for call in bridge.command.await_args_list], [second, second])

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

    async def test_unchanged_receipt_can_report_concurrent_stock_completion(self):
        host = self.host()
        already = result(UNREGISTERED_KILL, "turnin", "apply", -10, -10)
        already[board.KEY].update(state="already", expected_step=None, cash_before=50, cash_after=50)
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            result(UNREGISTERED_KILL, "turnin", "inspect", 2, -10, cash=3000, xp=1),
            already,
        ]))
        reply = await board.perform(host, bridge, "guest", "turnin", UNREGISTERED_KILL)
        self.assertEqual(reply, {"effect": "native_quest_present", "quest_id": UNREGISTERED_KILL, "step": -10})
        self.assertEqual(host.party.get("native_quests", {}), {})
        self.assertFalse(host.native_checkpoint_unsafe or host.stopping or host.transitioning)
        host.checkpoint_native.assert_not_awaited()

    async def test_refusal_can_retry_and_settle_against_the_current_wallet(self):
        host = self.host()
        inspected = result(UNREGISTERED_KILL, "turnin", "inspect", 2, -10, cash=3000, xp=1)
        paid = result(UNREGISTERED_KILL, "turnin", "apply", 2, -10, cash=3000, xp=1)
        paid[board.KEY].update(cash_before=25, cash_after=3025, xp_before=7, xp_after=8)
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            inspected, RuntimeError("stock_board_contract_inspection_changed"), inspected, paid,
        ]))
        with self.assertRaisesRegex(party.RuleError, "inspection_changed"):
            await board.perform(host, bridge, "guest", "turnin", UNREGISTERED_KILL)
        self.assertEqual(host.party.get("native_quests", {}), {})
        self.assertFalse(host.native_checkpoint_unsafe or host.stopping or host.transitioning)
        reply = await board.perform(host, bridge, "guest", "turnin", UNREGISTERED_KILL)
        self.assertEqual((reply["wallet"], reply["cash"], reply["xp"]), (3025, 3000, 1))
        self.assertEqual(host.party["economy"]["wallet"], 3025)
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
        self.assertEqual(bridge.command.await_args.args[-3:], (GUID, "2", "1|-10|801|1|1"))
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
        bridge.command.assert_awaited_once_with(board.CATALOG, PAWN, BOARD, "-", "-", timeout=5)
        host.checkpoint_native.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, "both offered and ready"):
            board.parse_catalog((*fields[:-1], UNREGISTERED_KILL))
        with self.assertRaisesRegex(ValueError, "Invalid stock board catalog entries"):
            board.parse_catalog((*fields[:4], "2", *fields[5:]))

    async def test_gunsmith_catalog_requires_an_allowed_map_and_reads_the_exact_board(self):
        host = self.host()
        area = "/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P"
        actor = f"BountyBoard_BP_C {area}.Shop_Gunsmith_P:PersistentLevel.BountyBoard_BP_C_0"
        host.last_game["map"] = area
        proof = {"map": area, "pawn": PAWN, "board": actor, "offers": [UNREGISTERED_KILL], "turnins": []}
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": area, board.CATALOG_KEY: proof,
        }))
        with self.assertRaisesRegex(party.RuleError, "native_board_contract_unavailable"):
            await board.catalog(host, bridge, "guest", actor)
        bridge.command.assert_not_awaited()
        host.allowed_maps.add(area)
        self.assertEqual(await board.catalog(host, bridge, "guest", actor), {board.CATALOG_KEY: proof})
        bridge.command.assert_awaited_once_with(board.CATALOG, PAWN, actor, "-", "-", timeout=5)

    async def test_catalog_uses_unregistered_board_and_contract_identities(self):
        host = self.host()
        actor = f"Community-Bounties_C {SHOPKEEP}.Shop_ShopKeep_P:PersistentLevel.SideBoard_1"
        quest = "Community_Missing_Supplies"
        proof = {"map": SHOPKEEP, "pawn": PAWN, "board": actor, "offers": [quest], "turnins": []}
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": SHOPKEEP, board.CATALOG_KEY: proof,
        }))
        self.assertEqual(await board.catalog(host, bridge, "guest", actor), {board.CATALOG_KEY: proof})
        bridge.command.assert_awaited_once_with(board.CATALOG, PAWN, actor, "-", "-", timeout=5)
        self.assertIsNone(board.board_map(actor.replace(".Shop_ShopKeep_P", ".Other")))
        area = "/CommunityWorld/Maps/Shop_ShopKeep_P"
        self.assertEqual(board.board_map(actor.replace(SHOPKEEP, area)), area)
        self.assertIsNone(board.board_map(actor.replace(SHOPKEEP, "/CommunityWorld//Shop_ShopKeep_P")))

    async def test_resident_catalog_receipt_does_not_replace_world_status(self):
        host = self.host()
        original = host.last_game, host.last_seen
        bridge = runtime.Bridge(Path("unused"), None)
        bridge._local_status = host.last_game

        async def reply(request, timeout):
            nonce = request.split(b"\t", 1)[0].decode()
            return (f"{board.MENU_REPLY}\t1\t{nonce}\thosting\t{SHOPKEEP}\taccepted\tverified\n"
                    f"{board.CATALOG}\t{SHOPKEEP}\t{PAWN}\t{BOARD}\t1\t1\t{UNREGISTERED_KILL}\t{EYE}\n")

        bridge.local_bridge = SimpleNamespace(command=reply)
        answer = await board.catalog(host, bridge, "guest", BOARD)
        self.assertEqual(answer[board.CATALOG_KEY]["offers"], [UNREGISTERED_KILL])
        self.assertEqual(answer[board.CATALOG_KEY]["turnins"], [EYE])
        self.assertIs(bridge._local_status, original[0])
        self.assertIs(host.last_game, original[0])
        self.assertEqual(host.last_seen, original[1])
        with self.assertRaisesRegex(RuntimeError, "did not match the current world"):
            await bridge.command(board.CATALOG, "Player_FP_C Other", BOARD, "-", "-")

    def test_streamed_board_identity_is_explicit_and_current_map_remains_separate(self):
        area = SHOPKEEP
        raw = "BountyBoard_BP_C /ContentPack/Maps/MarketInstance.Market:PersistentLevel.BountyBoard_BP_C_3"
        target = "stream:" + raw
        self.assertTrue(board.board_matches_map(target, area))
        self.assertFalse(board.board_matches_map(raw, area))
        self.assertFalse(board.board_matches_map(target, "/ContentPack/Maps/MarketInstance"))
        self.assertFalse(board.board_matches_map(target, "/Game/Classes/Maps/MainMenu_P"))
        visit = "a" * 32
        self.assertEqual(board.menu_selection(board.menu_target(visit, target, "Clt_Example")), (visit, target, "Clt_Example"))

    async def test_resident_stock_menu_accepts_full_catalog_and_rejects_a_stale_readback(self):
        bridge = runtime.Bridge(Path("unused"), None)
        options = ",".join(f"Unregistered_Quest_Number_{index}" for index in range(64))
        self.assertGreater(len(options), 1024)
        original = {"state": "connected", "map": SHOPKEEP}
        bridge._local_status = original

        async def reply(request, timeout):
            nonce, action, _, area, pawn, offered, ready = request.decode().rstrip("\n").split("\t")
            self.assertEqual((action, area, pawn, offered, ready), (board.MENU_OPEN, SHOPKEEP, PAWN, options, "-"))
            return (f"{board.MENU_REPLY}\t1\t{nonce}\tconnected\t{area}\taccepted\tverified\n"
                    f"native-wire-board-stock\t{area}\t{pawn}\t{BOARD}\t-\tvisible\t12\t12\n")

        bridge.local_bridge = SimpleNamespace(command=reply)
        answer = await bridge.command(board.MENU_OPEN, "12", SHOPKEEP, PAWN, options, "-")
        self.assertEqual(answer[board.MENU_KEY]["open"], 12)
        self.assertIs(bridge._local_status, original)
        with self.assertRaisesRegex(RuntimeError, "did not verify the requested menu"):
            await bridge.command(board.MENU_OPEN, "13", SHOPKEEP, PAWN, options, "-")

    def test_repeated_proof_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = runtime.Bridge(Path(directory), None)
            valid = f"{board.COMMAND}\tstart\tinspect\t{SHOPKEEP}\t{PAWN}\t{BOARD}\t{EYE}\tqualified\t-\t-\t1\t17\t17\t3\t3\t0\t0\t-\t-\t-\t-"
            prefix = f"schema\t1\nsession\thost\thosting\t{SHOPKEEP}\t1\t0\n"
            self.assertEqual(bridge._parse_status(prefix + valid + "\n")[board.KEY]["quest"], EYE)
            self.assertIsNone(bridge._parse_status(prefix + valid + "\n" + valid + "\n"))
