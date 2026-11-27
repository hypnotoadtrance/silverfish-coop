import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

from tools.silverfish import native_authored_pickup as pickup
from tools.silverfish import native_black_box as black_box
from tools.silverfish import native_computer_board as collections
from tools.silverfish import native_board_contract, native_quest, native_rewards, native_wire_board, party
from tools.silverfish.runtime import Bridge
from tools.silverfish.travel import BLACK_MARKET, OBSERVATORY


OWNER = "guest"
PAWN = "Player_FP_C exact enrolled guest"
GUID = "9A" * 16
SPEC = collections.BLACK_BOX


def proof(verb, *, completed=False):
    return {"state": "hosting", "map": OBSERVATORY, SPEC.key: {
        "operation": SPEC.command(verb), "map": OBSERVATORY, "pawn": PAWN, "board": collections.BOARDS[OBSERVATORY],
        "quest": SPEC.quest_id, "can_turn_in": True, "step_before": 1, "step_after": -10 if completed else 1,
        "cash_before": 25, "cash_after": 25 + (12000 if completed else 0),
        "xp_before": 12, "xp_after": 12 + int(completed), "objective_guid": GUID, "objective_owner": "guest",
        "reputation": {"8": {"before": 0, "after": 50 if completed else 0}},
        "completion": {"before": 0, "before_found": False, "after": int(completed), "after_found": completed},
    }}


def scoped_host(area):
    data = party.new_party()
    for character in ("host", OWNER):
        party.add_player(data, character)
    return SimpleNamespace(
        party=data, native_profile=True, native_guest=True, native_zones=True,
        native_checkpoint_unsafe=False, transitioning=False, stopping=False,
        last_game={"state": "hosting", "map": area}, last_seen=time.monotonic(),
        roles=lambda: {"host": "host", "guest": OWNER},
        native_recipients=lambda: {OWNER: {"pawn": PAWN, "connected": True}},
        checkpoint_native=AsyncMock(return_value=15), control=Mock(),
    )


class BlackBoxStatusTests(unittest.TestCase):
    def test_status_requires_one_exact_companion(self):
        primary = ("schema\t1\nseq\t2\n"
                   f"session\thost\thosting\t{OBSERVATORY}\t1\t0\n"
                   f"{SPEC.prefix}\t{SPEC.command('turn-in')}\t{OBSERVATORY}\t{PAWN}\t{collections.BOARDS[OBSERVATORY]}\t"
                   f"{SPEC.quest_id}\t1\t-10\t25\t12025\t12\t13\t{GUID}\tguest\t1\n"
                   f"{SPEC.prefix}-reputation\t8\t0\t50\n")
        exact = f"{black_box.COMPLETION_TAG}\t{black_box.COMPLETION_FLAG}\t0\t0\t1\t1\n"
        with tempfile.TemporaryDirectory() as temp:
            bridge = Bridge(Path(temp), None)
            status = bridge.directory / "status.tsv"
            for row, accepted in ((exact, True), ("", False), (exact * 2, False),
                                  (exact.replace("Simmi_BlackBox", "OtherFlag"), False),
                                  (exact.replace("\t1\t1", "\t1\t0"), False)):
                with self.subTest(row=row):
                    status.write_text(primary + row, encoding="utf-8")
                    observed = bridge.snapshot()[SPEC.key]
                    self.assertEqual(black_box.valid_completion(observed), accepted)


class BlackBoxSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_board_and_quest_only(self):
        bridge = SimpleNamespace(command=AsyncMock())
        self.assertEqual(native_wire_board.fixture_selection(OBSERVATORY, SPEC.quest_id), (SPEC.quest_id,))
        self.assertEqual(native_wire_board.fixture_selection(BLACK_MARKET, SPEC.quest_id), (SPEC.quest_id,))
        self.assertEqual(pickup.select_source(black_box.MAP, black_box.SOURCE), black_box.SOURCE)
        with self.assertRaises(party.RuleError):
            pickup.select_source(OBSERVATORY, black_box.SOURCE)
        for area, refusal in ((BLACK_MARKET, "stock_black_box_observatory_redemption_required"),
                              (black_box.MAP, "native_black_box_unavailable")):
            with self.subTest(area=area), self.assertRaisesRegex(party.RuleError, refusal):
                await native_quest.action(scoped_host(area), bridge, OWNER, "native-quest-turnin", SPEC.quest_id)
        bridge.command.assert_not_awaited()
        host = scoped_host(OBSERVATORY)
        host.allowed_maps = {OBSERVATORY}
        bridge.command.side_effect = RuntimeError("stock_board_contract_wrong_turnin")
        lookalike = SPEC.quest_id + "_1"
        with self.assertRaisesRegex(party.RuleError, "stock_board_contract_wrong_turnin"):
            await native_quest.action(host, bridge, OWNER, "native-quest-turnin", lookalike)
        bridge.command.assert_awaited_once_with(native_board_contract.COMMAND, "turnin", "inspect", PAWN,
                                               collections.BOARDS[OBSERVATORY], lookalike, timeout=5)

    async def test_exact_placed_recording_is_consumed_once_and_guest_save_is_retained(self):
        host = scoped_host(OBSERVATORY)
        host.pending_host_save = None
        host.pending_guest_saves = {}
        host.queue_guest_save = lambda character, pawn: host.pending_guest_saves.update({character: (pawn, None)})
        bridge = SimpleNamespace(command=AsyncMock(return_value=proof("preflight")))
        receipt = {"source": black_box.SOURCE, "map": black_box.MAP, "actor": black_box.ACTOR + "_1",
                   "class_path": black_box.CLASS, "guid": GUID, "owner": OWNER}
        host.party[pickup.STATE_KEY] = {black_box.SOURCE: receipt}
        with self.assertRaisesRegex(party.RuleError, "objective_required"):
            await collections.turn_in(host, bridge, OWNER, spec=SPEC)
        host.checkpoint_native.assert_not_awaited()

        receipt["actor"] = black_box.ACTOR
        before = dict(proof("preflight"), native_guest_save_retention=True)
        retained = {"state": "hosting", "map": OBSERVATORY, "native_guest_save_retention": True}
        bridge.command.reset_mock()
        bridge.command.side_effect = [before, retained, proof("turn-in", completed=True)]
        settled = await collections.turn_in(host, bridge, OWNER, spec=SPEC)
        self.assertEqual((settled["consumed_radio"], settled["cash"], settled["xp"], settled["completion_flag"]),
                         (GUID, 12000, 1, black_box.COMPLETION_FLAG))
        self.assertEqual(host.pending_guest_saves, {OWNER: (PAWN, None)})
        self.assertIs(host.pending_host_save, False)
        self.assertEqual(bridge.command.await_args_list, [
            call(SPEC.command("preflight"), PAWN, timeout=5),
            call("native-guest-save-retain", PAWN, OWNER, timeout=3),
            call(SPEC.command("turn-in"), PAWN, GUID, "guest", timeout=8),
        ])
        self.assertEqual(host.party["players"][OWNER][native_rewards.PENDING], 1)
        self.assertEqual(host.party["native_quests"][SPEC.quest_id]["completion_flag"], black_box.COMPLETION_FLAG)
        replay = await collections.turn_in(host, bridge, OWNER, spec=SPEC)
        self.assertEqual((replay["effect"], replay["consumed_radio"]), ("native_quest_already_settled", GUID))
        host.checkpoint_native.assert_not_awaited()
        self.assertEqual(bridge.command.await_count, 3)

    async def test_after_stock_mutation_missing_flag_blocks_commit(self):
        host = scoped_host(OBSERVATORY)
        host.party[pickup.STATE_KEY] = {black_box.SOURCE: {
            "source": black_box.SOURCE, "map": black_box.MAP, "actor": black_box.ACTOR,
            "class_path": black_box.CLASS, "guid": GUID, "owner": OWNER,
        }}
        frame = proof("turn-in", completed=True)
        del frame[SPEC.key]["completion"]
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[proof("preflight"), frame]))
        with self.assertRaisesRegex(RuntimeError, "canonical completion flag"):
            await collections.turn_in(host, bridge, OWNER, spec=SPEC)
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
        self.assertNotIn(SPEC.quest_id, host.party.get("native_quests", {}))
        self.assertEqual(host.checkpoint_native.await_count, 1)

    async def test_fixture_uses_only_exact_host_xp_gate(self):
        host = scoped_host(OBSERVATORY)
        host.fixtures = False
        host.last_game["pawns"] = {"local": {"name": PAWN}}
        bridge = SimpleNamespace(command=AsyncMock())
        with self.assertRaisesRegex(party.RuleError, "xp_fixture_unavailable"):
            await collections.xp_fixture(host, bridge, SPEC)
        bridge.command.assert_not_awaited()

        frame = proof("xp-fixture")
        frame[SPEC.key].update(step_before=None, step_after=None, xp_before=0, objective_guid="-", objective_owner="-")
        bridge.command.return_value = frame
        host.fixtures = True
        applied = await collections.xp_fixture(host, bridge, SPEC)
        self.assertEqual((applied[SPEC.key]["xp_before"], applied[SPEC.key]["xp_after"]), (0, 12))
        self.assertEqual(applied[SPEC.key]["cash_before"], applied[SPEC.key]["cash_after"])
        bridge.command.assert_awaited_once_with(SPEC.command("xp-fixture"), PAWN, timeout=5)
        self.assertFalse(host.party.get("native_quests"))
