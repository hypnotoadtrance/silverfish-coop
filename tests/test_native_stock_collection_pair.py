import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_authored_pickup as pickup, native_computer_board as collection
from tools.silverfish import native_quest, native_rewards, native_wire_board, party
from tools.silverfish.travel import BLACK_MARKET, NORTHEAST_RAIL, OBSERVATORY, SHOPKEEP


OWNER = "guest"
PAWN = "Player_FP_C exact enrolled guest"
GUID = "9A" * 16


def frame(spec, verb, *, settled=False):
    area = spec.redeem_map
    xp = spec.fixture_xp or 0
    return {"state": "hosting", "map": area, spec.key: {
        "operation": spec.command(verb), "map": area, "pawn": PAWN, "board": collection.BOARDS[area],
        "quest": spec.quest_id, "can_turn_in": True, "step_before": 1, "step_after": -10 if settled else 1,
        "cash_before": 10, "cash_after": 10 + (spec.cash if settled else 0),
        "xp_before": xp, "xp_after": xp + int(settled),
        "objective_guid": GUID, "objective_owner": "guest",
        "reputation": {faction: {"before": 0, "after": 100 if settled else 0} for faction in spec.factions},
    }}


class StockCollectionPairTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_board_and_placed_owner_can_settle_once(self):
        for spec, board, source_map, source_actor in (
            (collection.MOLASSES, SHOPKEEP, NORTHEAST_RAIL, "Item_Molasis_C_1"),
            (collection.SAVLO_KEYCARD, OBSERVATORY, pickup.TRENCH, "KeyCard_Savlo_BP_C_1"),
        ):
            with self.subTest(quest=spec.quest_id):
                self.assertEqual(native_wire_board.fixture_selection(board, spec.quest_id), (spec.quest_id,))
                with self.assertRaises(party.RuleError):
                    native_wire_board.fixture_selection(BLACK_MARKET, spec.quest_id)
                source = spec.pickup_source
                area, placed, item = pickup.SOURCES[source]
                self.assertEqual(area, source_map)
                self.assertEqual(placed.rsplit(".", 1)[-1], source_actor)
                data = party.new_party()
                for actor in ("host", OWNER):
                    party.add_player(data, actor)
                row = {"source": source, "map": area, "actor": placed, "class_path": item, "guid": GUID, "owner": "host"}
                data[pickup.STATE_KEY] = {source: row}
                host = SimpleNamespace(
                    party=data, native_profile=True, native_guest=True, native_zones=True,
                    native_checkpoint_unsafe=False, transitioning=False, stopping=False,
                    last_game={"state": "hosting", "map": board}, last_seen=time.monotonic(),
                    roles=lambda: {"host": "host", "guest": OWNER},
                    native_recipients=lambda: {OWNER: {"pawn": PAWN, "connected": True}},
                    checkpoint_native=AsyncMock(return_value=7), control=Mock(),
                )
                bridge = SimpleNamespace(command=AsyncMock(return_value=frame(spec, "preflight")))

                async def turn_in():
                    return await native_quest.action(host, bridge, OWNER, "native-quest-turnin", spec.quest_id)

                with self.assertRaisesRegex(party.RuleError, "objective_required"):
                    await turn_in()
                host.checkpoint_native.assert_not_awaited()
                row.update(owner=OWNER, actor=placed + "_1")
                with self.assertRaisesRegex(party.RuleError, "objective_required"):
                    await turn_in()
                host.checkpoint_native.assert_not_awaited()
                row["actor"] = placed

                bridge.command.side_effect = [frame(spec, "preflight"), frame(spec, "turn-in", settled=True)]
                receipt = await turn_in()
                self.assertEqual((receipt[spec.consumed_field], receipt["cash"], receipt["xp"]), (GUID, spec.cash, 1))
                self.assertEqual(host.checkpoint_native.await_args.kwargs["require_guest_frames"], {OWNER})
                self.assertEqual(host.party["players"][OWNER][native_rewards.PENDING], 1)
                self.assertEqual((await turn_in())["effect"], "native_quest_already_settled")
                self.assertEqual(host.checkpoint_native.await_count, 2)
