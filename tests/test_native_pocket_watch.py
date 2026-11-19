import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_authored_pickup, native_computer_board, native_quest
from tools.silverfish import native_rewards, native_wire_board, party, runtime
from tools.silverfish.travel import CROSSROADS, SCAV_BAR


WATCH = native_computer_board.POCKET_WATCH
BOARD = native_computer_board.BOARDS[SCAV_BAR]
GUID = "C1" * 16
ACTOR = "medic"
PAWN = "Player_FP_C exact enrolled guest"


def frame(operation, after, *, cash=70, xp=0):
    report = {
        "operation": WATCH.command(operation), "map": SCAV_BAR, "pawn": PAWN, "board": BOARD,
        "quest": WATCH.quest_id, "can_turn_in": True, "step_before": 1, "step_after": after,
        "cash_before": 70, "cash_after": cash, "xp_before": 0, "xp_after": xp,
        "objective_guid": GUID, "objective_owner": "guest", "reputation": {},
    }
    return {"state": "hosting", "map": SCAV_BAR, WATCH.key: report}


class PocketWatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_placed_watch_owner_can_redeem_and_claims_once(self):
        state = party.new_party()
        for player in ("host", ACTOR):
            party.add_player(state, player)
        host = SimpleNamespace(party=state, native_profile=True, native_zones=True, native_guest=True,
                               native_checkpoint_unsafe=False, transitioning=False, stopping=False,
                               last_game={"state": "hosting", "map": SCAV_BAR}, last_seen=time.monotonic(),
                               roles=lambda: {"host": "host", "guest": ACTOR},
                               native_recipients=lambda: {ACTOR: {"pawn": PAWN, "connected": True}},
                               checkpoint_native=AsyncMock(return_value=7), control=Mock())
        bridge = SimpleNamespace(command=AsyncMock(return_value=frame("preflight", 1)))

        async def turn_in():
            return await native_quest.action(host, bridge, ACTOR, "native-quest-turnin", WATCH.quest_id)

        source = WATCH.pickup_source
        area, placed, item = native_authored_pickup.SOURCES[source]
        self.assertEqual(area, CROSSROADS)
        host.party[native_authored_pickup.STATE_KEY] = {
            source: {"source": source, "map": area, "actor": placed, "class_path": item, "guid": GUID, "owner": "host"},
        }
        with self.assertRaisesRegex(party.RuleError, "objective_required"):
            await turn_in()
        host.checkpoint_native.assert_not_awaited()

        host.party[native_authored_pickup.STATE_KEY][source]["owner"] = ACTOR
        bridge.command.side_effect = [frame("preflight", 1), frame("turn-in", -10, cash=1070, xp=1)]
        receipt = await turn_in()
        self.assertEqual((receipt["consumed_watch"], receipt["cash"], receipt["xp"], receipt["reputation"]),
                         (GUID, 1000, 1, {}))
        self.assertEqual(host.checkpoint_native.await_args.kwargs["require_guest_frames"], {ACTOR})
        self.assertEqual(host.party["players"][ACTOR][native_rewards.PENDING], 1)
        self.assertEqual((await turn_in())["effect"], "native_quest_already_settled")
        self.assertEqual(host.checkpoint_native.await_count, 2)

    def test_empty_reputation_proof_round_trips_on_exact_scav_bar_board(self):
        self.assertEqual(native_wire_board.fixture_selection(SCAV_BAR, WATCH.quest_id), (WATCH.quest_id,))
        with self.assertRaises(party.RuleError):
            native_wire_board.fixture_selection(CROSSROADS, WATCH.quest_id)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            rows = (("schema", "1"), ("session", "host", "hosting", SCAV_BAR, "1", "0"),
                    (WATCH.prefix, WATCH.command("turn-in"), SCAV_BAR, PAWN, BOARD, WATCH.quest_id,
                     "1", "-10", "70", "1070", "0", "1", GUID, "guest", "1"))
            (path / "status.tsv").write_text("\n".join("\t".join(row) for row in rows), encoding="utf-8")
            self.assertEqual(runtime.Bridge(path, Mock()).snapshot()[WATCH.key],
                             frame("turn-in", -10, cash=1070, xp=1)[WATCH.key])
