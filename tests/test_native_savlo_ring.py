import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_authored_pickup as pickup, native_computer_board as collection
from tools.silverfish import native_quest, native_rewards, native_wire_board, party
from tools.silverfish.travel import SCAV_BAR


RING = collection.SAVLO_RING
BOARD = collection.BOARDS[SCAV_BAR]
GUID = "DB" * 16
PAWNS = {"host": "Player_FP_C local host", "guest": "Player_FP_C enrolled ring owner"}


def report(verb, authority, before, after, *, cash_after=70, xp_before=10, xp_after=10, rep_after=-300, objective=True):
    return {"state": "hosting", "map": SCAV_BAR, RING.key: {
        "operation": RING.command(verb), "map": SCAV_BAR, "pawn": PAWNS[authority], "board": BOARD,
        "quest": RING.quest_id, "can_turn_in": True, "step_before": before, "step_after": after,
        "cash_before": 70, "cash_after": cash_after, "xp_before": xp_before, "xp_after": xp_after,
        "objective_guid": GUID if objective else "-", "objective_owner": "guest" if objective else "-",
        "reputation": {"5": {"before": -300, "after": rep_after}},
    }}


class SavloRingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state = party.new_party()
        for player in PAWNS:
            party.add_player(state, player)
        self.host = SimpleNamespace(
            party=state, fixtures=True, native_profile=True, native_zones=True, native_guest=True,
            native_checkpoint_unsafe=False, transitioning=False, stopping=False, last_seen=time.monotonic(),
            last_game={"state": "hosting", "map": SCAV_BAR, "pawns": {"local": {"name": PAWNS["host"]}}},
            roles=lambda: {"host": "host", "guest": "guest"},
            native_recipients=lambda: {"guest": {"pawn": PAWNS["guest"], "connected": True}},
            checkpoint_native=AsyncMock(return_value=7), control=Mock(),
        )
        self.bridge = SimpleNamespace(command=AsyncMock())

    async def test_xp_fixture_requires_fresh_headless_scope_and_only_credits_stock_xp(self):
        self.host.fixtures = False
        with self.assertRaisesRegex(party.RuleError, "fixture_unavailable"):
            await collection.xp_fixture(self.host, self.bridge, RING)
        self.bridge.command.assert_not_awaited()

        self.host.fixtures = True
        self.bridge.command.return_value = report("xp-fixture", "host", None, None,
                                                  xp_before=0, objective=False)
        result = await collection.xp_fixture(self.host, self.bridge, RING)
        self.assertEqual(result[RING.key]["xp_after"], 10)
        self.assertEqual(self.host.checkpoint_native.await_count, 2)
        self.assertFalse(self.host.native_checkpoint_unsafe)
        self.assertNotIn("native_quests", self.host.party)

    async def test_only_exact_placed_ring_redeems_and_negative_rep_is_paired_once(self):
        actor = "guest"
        self.assertEqual(native_wire_board.fixture_selection(SCAV_BAR, RING.quest_id), (RING.quest_id,))
        source = RING.pickup_source
        area, placed, item = pickup.SOURCES[source]
        self.host.party[pickup.STATE_KEY] = {
            source: {"source": source, "map": area, "actor": placed, "class_path": item, "guid": GUID, "owner": actor},
        }
        self.bridge.command.side_effect = [
            report("preflight", actor, 1, 1),
            report("turn-in", actor, 1, -10, cash_after=20070, xp_after=11, rep_after=-500),
        ]
        receipt = await native_quest.action(self.host, self.bridge, actor, "native-quest-turnin", RING.quest_id)
        self.assertEqual((receipt["consumed_ring"], receipt["cash"], receipt["xp"], receipt["reputation"]),
                         (GUID, 20000, 1, {"5": -500}))
        self.assertEqual(self.host.checkpoint_native.await_args.kwargs["require_guest_frames"], {actor})
        self.assertEqual(self.host.party["players"][actor][native_rewards.PENDING], 1)
        replay = await native_quest.action(self.host, self.bridge, actor, "native-quest-turnin", RING.quest_id)
        self.assertEqual(replay["effect"], "native_quest_already_settled")
        self.assertEqual(self.host.checkpoint_native.await_count, 2)
