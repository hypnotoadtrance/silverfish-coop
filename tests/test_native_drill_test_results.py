import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_host_save, native_mosby_tape as tape, party
from tools.silverfish.campaign import CampaignFormatError
from tools.silverfish.native_tape_profiles import DRILL, DRILL_QUEST, PUMP, drill_download_unlocked


GUEST = "guest-owner"
HOST_PAWN = "Player_FP_C exact Scientist host"
GUEST_PAWN = "Player_FP_C exact Scientist guest"
BLANK = "7B" * 16
OPERATION = "7a" * 16


def completion():
    return {"quest_id": DRILL_QUEST, "step": -10, "mosby_drill_test": 1,
            "test_results": {"station": DRILL.station, "class_path": DRILL.product}}


def product(actor):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{DRILL.namespace}:{actor}:{BLANK}").hex.upper()


def frame(pawn, action="preflight", state="offer", filled="-", *, step="-10", flag="2", blank=BLANK):
    return [DRILL.prefix, DRILL.verb(action), DRILL.map, pawn, DRILL.station, state, blank, filled,
            step, "100", "11", "1", "1", "0", "0", flag]


def listening(*, completed=True):
    state = party.new_party()
    party.add_player(state, "host")
    party.add_player(state, GUEST)
    if completed:
        state["native_quests"] = {DRILL_QUEST: completion()}
    snapshot = {"state": "hosting", "map": DRILL.map, "pawns": {"local": {"name": HOST_PAWN}}}
    return SimpleNamespace(party=state, native_profile=True, native_guest=True, native_zones=True, last_seen=time.monotonic(),
                           last_game=snapshot, transitioning=False, stopping=False, native_checkpoint_unsafe=False,
                           native_recipients=lambda: {GUEST: {"pawn": GUEST_PAWN, "connected": True}},
                           checkpoint_native=AsyncMock(return_value=17), control=Mock())


def snapshot(host, row):
    return {**host.last_game, DRILL.key: tape.parse_exchange(row)}


class DrillTestResultsTests(unittest.IsolatedAsyncioTestCase):
    def test_result_wire_requires_completed_step_and_proven_current_stock_flag(self):
        for flag in ("1", "2", "3"):
            proof = tape.parse_exchange(frame(GUEST_PAWN, flag=flag))
            self.assertEqual((proof["quest_step"], proof["mosby_drill_test"]), (-10, int(flag)))
            self.assertEqual(proof["receive"], {"class_path": DRILL.product, "quantity": 1, "cash": 0})
        invalid = [frame(GUEST_PAWN, flag="0"), frame(GUEST_PAWN, flag="4"), frame(GUEST_PAWN, step="1"),
                   frame(GUEST_PAWN)[:-1], [*frame(GUEST_PAWN)[:4], PUMP.station, *frame(GUEST_PAWN)[5:]]]
        for proof in invalid:
            with self.subTest(proof=proof), self.assertRaisesRegex(ValueError, "stock station exchange proof"):
                tape.parse_exchange(proof)

    async def test_host_and_guest_require_durable_exact_completion_before_bridge_commands(self):
        host = listening(completed=False)
        bridge = SimpleNamespace(command=AsyncMock())
        for actor in ("host", GUEST):
            with self.subTest(actor=actor), self.assertRaisesRegex(party.RuleError, "durable_completion_required"):
                await tape.preflight(host, bridge, actor, DRILL.verb("preflight"), BLANK)
            with self.subTest(actor=actor), self.assertRaisesRegex(party.RuleError, "durable_completion_required"):
                await tape.convert(host, bridge, actor, BLANK, OPERATION, profile=DRILL)
        host.party["native_quests"] = {DRILL_QUEST: dict(completion(), mosby_drill_test=True)}
        self.assertFalse(drill_download_unlocked(host.party))
        host.party["native_quests"][DRILL_QUEST] = dict(completion(), test_results={"station": PUMP.station,
                                                                                  "class_path": DRILL.product})
        self.assertFalse(drill_download_unlocked(host.party))
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()

    async def test_each_role_commits_exact_custody_and_replay_uses_current_stock(self):
        for actor, pawn in (("host", HOST_PAWN), (GUEST, GUEST_PAWN)):
            with self.subTest(actor=actor):
                host = listening()
                generated = product(actor)
                preflight = snapshot(host, frame(pawn))
                converted = snapshot(host, frame(pawn, "convert", "exchanged", generated))
                replay = snapshot(host, frame(pawn, blank="-", flag="3"))
                bridge = SimpleNamespace(command=AsyncMock(side_effect=(preflight, converted, replay)))
                result = await tape.convert(host, bridge, actor, BLANK, OPERATION, profile=DRILL)
                self.assertEqual((result["owner"], result["filled_guid"], result["mosby_drill_test"]), (actor, generated, 2))
                self.assertEqual(host.party[DRILL.ledger][BLANK], {key: value for key, value in result.items() if key != "effect"})
                self.assertEqual(host.checkpoint_native.await_args.kwargs["require_guest_frames"],
                                 frozenset({actor} - {"host"}))
                self.assertEqual(bridge.command.await_args_list[1].args, (DRILL.verb("convert"), pawn, BLANK, generated))
                self.assertFalse(host.native_checkpoint_unsafe)
                saved = {"world": {"party": host.party}, "characters": {GUEST: {}}}
                self.assertEqual(native_host_save._escrow_frames(saved), [])
                repeated = await tape.convert(host, bridge, actor, BLANK, OPERATION, profile=DRILL)
                self.assertEqual(repeated, dict(result, effect="drill_test_results_tape_already_exchanged"))
                self.assertEqual(host.checkpoint_native.await_count, 2)
                with self.assertRaisesRegex(party.RuleError, "blank_already_exchanged"):
                    await tape.convert(host, bridge, GUEST if actor == "host" else "host", BLANK, OPERATION, profile=DRILL)
                self.assertEqual(bridge.command.await_count, 3)
                host.party["native_quests"].clear()
                with self.assertRaises(CampaignFormatError):
                    native_host_save._escrow_frames(saved)

    async def test_changed_stock_flag_fences_unverified_exchange(self):
        host = listening()
        before = snapshot(host, frame(GUEST_PAWN))
        after = snapshot(host, frame(GUEST_PAWN, "convert", "exchanged", product(GUEST), flag="3"))
        bridge = SimpleNamespace(command=AsyncMock(side_effect=(before, after)))
        with self.assertRaisesRegex(RuntimeError, "did not reconcile"):
            await tape.convert(host, bridge, GUEST, BLANK, OPERATION, profile=DRILL)
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
        self.assertFalse(host.transitioning)
        self.assertNotIn(DRILL.ledger, host.party)
        self.assertEqual(host.checkpoint_native.await_count, 1)
