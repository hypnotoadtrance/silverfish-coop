import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_mosby_station_view as station, native_mosby_tape as tape, native_wire_board


PAWN = "Player_FP_C local guest"
BLANK = "A" * 32
OTHER = "B" * 32
PRODUCT = "C" * 32
EPOCH = "host:zone"


def station_frame(sequence, opened, state, blank=None, product=None, pawn=PAWN):
    values = ["native-mosby-station-view", tape.STONE_MAZE, pawn, tape.STATION, state, str(sequence), str(opened), blank or "-", product or "-"]
    return {"map": tape.STONE_MAZE, "pawns": {"local": {"name": PAWN}}, station.KEY: station.parse(values)}


def offer(blank=BLANK):
    return {"native_mosby_tape": tape.parse_exchange(["native-mosby-tape", tape.PREFLIGHT, tape.STONE_MAZE,
                                                    "Player_FP_C authority guest", tape.STATION, "offer", blank or "-", "-", "1", "100", "3", "1", "1", "0", "0"])}


class StoneMazeStockControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_presses_confirm_only_the_same_host_verified_blank(self):
        async def render(action, sequence, opened, state, blank, product, *, timeout):
            self.assertEqual(action, station.RENDER)
            return station_frame(sequence, opened, state, None if blank == "-" else blank, None if product == "-" else product)

        bridge = SimpleNamespace(native_mosby_station_requests=Mock(), command=AsyncMock(side_effect=render), input_namespace=uuid.uuid4())
        control = SimpleNamespace(event=Mock())
        view = station.StationView(bridge, control)
        action = AsyncMock(return_value=offer())

        async def press(sequence, opened, kind, blank=None, pawn=PAWN, epoch=EPOCH):
            bridge.native_mosby_station_requests.return_value = [(sequence, opened, kind, pawn, blank)]
            return (await view.sync(station_frame(sequence, opened, station.WAITING[kind], blank), epoch, True, action))[station.KEY]

        await press(1, 1, "view", pawn="Player_FP_C sibling guest")
        action.assert_not_awaited()
        shown = await press(1, 1, "view")
        self.assertEqual((shown["state"], shown["blank_guid"]), ("offer", BLANK))
        self.assertEqual(await press(2, 1, "trade", OTHER), station_frame(2, 1, "unavailable")[station.KEY])
        self.assertEqual(action.await_count, 1)
        self.assertEqual(await press(3, 1, "trade", BLANK), station_frame(3, 1, "unavailable")[station.KEY])
        self.assertEqual(action.await_count, 1)
        await press(4, 4, "view")
        self.assertEqual((await press(5, 4, "trade", BLANK, epoch="new-epoch"))["state"], "unavailable")
        self.assertEqual(action.await_count, 2)
        await press(6, 6, "view")
        action.return_value = {"effect": "stone_maze_tape_exchanged", "blank_guid": BLANK, "filled_guid": PRODUCT}
        converted = await press(7, 6, "trade", BLANK)
        self.assertEqual((converted["state"], converted["filled_guid"]), ("exchanged", PRODUCT))
        self.assertEqual(action.await_args.args[0::2], (tape.CONVERT, BLANK))
        await press(7, 6, "trade", BLANK)
        self.assertEqual(action.await_count, 4)

    def test_intents_require_exact_stock_station_and_bounded_sequences(self):
        first = ["mosby-station", "4", "4", "view", PAWN, tape.STATION, "-"]
        trade = ["mosby-station", "5", "4", "trade", PAWN, tape.STATION, BLANK]
        self.assertEqual(station.parse_intent(first), (4, 4, "view", PAWN, None))
        self.assertEqual(station.parse_intent(trade), (5, 4, "trade", PAWN, BLANK))
        self.assertIsNone(station.parse_intent([*trade[:5], tape.STATION.replace("_C_1", "_C_2"), BLANK]))
        self.assertIsNone(station.parse_intent([trade[0], str(2**53), *trade[2:]]))
        self.assertIsNone(station.parse_intent([*trade[:6], "-"]))

    async def test_scientist_board_allows_only_active_stock_readings_redemption(self):
        view = native_wire_board.WireBoardView(None, None)
        quest = tape.MOSBY_QUEST
        proof = dict(map=tape.SCIENTIST_SHOP, board=tape.BOARD, quest_id=quest, objective_guid=None, objective_owner=None)
        action = AsyncMock()
        for step, objective, owner, status in ((None, None, None, "not_offered"), (1, None, None, "missing"),
                                                (1, PRODUCT, "guest", "ready"), (-10, None, None, "completed")):
            action.return_value = {"native_mosby_readings": {**proof, "step_after": step, "objective_guid": objective, "objective_owner": owner}}
            self.assertEqual(await view._inspect(tape.SCIENTIST_SHOP, tape.BOARD, quest, "operation", action), status)
            self.assertEqual(action.await_args.args[0::2], (tape.READINGS_PREFLIGHT, None))
        self.assertEqual(native_wire_board.fixture_selection(tape.SCIENTIST_SHOP, quest), (quest,))
        self.assertEqual(native_wire_board.fixture_selection(tape.SCIENTIST_SHOP, native_wire_board.native_computer_board.QUEST_ID),
                         (native_wire_board.native_computer_board.QUEST_ID,))


if __name__ == "__main__":
    unittest.main()
