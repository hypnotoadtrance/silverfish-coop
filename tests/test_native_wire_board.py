import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_wire_board as wire


class WireBoardTests(unittest.IsolatedAsyncioTestCase):
    async def test_stock_jammer_click_uses_only_the_exact_host_board(self):
        bridge = SimpleNamespace(input_namespace=uuid.uuid4(), native_wire_board_requests=Mock(), command=AsyncMock())
        control = SimpleNamespace(event=Mock())
        view = wire.WireBoardView(bridge, control)
        action = AsyncMock()
        pawn, area = "Player_FP_C guest-owner", wire.OBSERVATORY
        board = wire.BOARDS[area]

        async def press(sequence, kind, quest, target=board):
            stock = dict(map=area, pawn=pawn, board=target, quest=quest, state=wire.WAITING[kind],
                         sequence=sequence, open=sequence)
            snapshot = {"map": area, "pawns": {"local": {"name": pawn}}, "native_wire_board_stock": stock}
            bridge.native_wire_board_requests.return_value = [(sequence, sequence, kind, area, pawn, target, quest)]
            bridge.command.return_value = snapshot
            return await view.sync(snapshot, "epoch", True, action)

        lake = wire.LAKE_JAMMER.quest
        crossroads = wire.CROSSROADS_JAMMER.quest
        await press(1, "stock-start", lake, wire.BOARDS[wire.BLACK_MARKET])
        await press(2, "stock-start", wire.native_computer_board.BLACK_BOX.quest_id)
        action.assert_not_awaited()
        action.return_value = {"quest_id": lake, "step": 1}
        await press(3, "stock-start", lake)
        await press(3, "stock-start", lake)
        action.assert_awaited_once()
        self.assertEqual(action.await_args.args[0::2], ("native-quest-start", lake))
        bridge.command.assert_awaited_once_with("native-wire-board-stock-result", "3", area, pawn, lake, "accepted", timeout=2)
        action.return_value = {"quest_id": crossroads, "step": -10}
        await press(4, "stock-turnin", crossroads)
        self.assertEqual(action.await_args.args[0::2], ("native-quest-turnin", crossroads))
        self.assertEqual(bridge.command.await_args.args[-1], "completed")

    async def test_board_requires_current_rendered_offer_before_accepting(self):
        pawn = "Player_FP_C test-owner"

        def frame(sequence, opened, state):
            return {
                "map": wire.BOROUGH,
                "pawns": {"local": {"name": pawn}},
                "native_wire_board": {"map": wire.BOROUGH, "pawn": pawn, "board": wire.BOARD,
                                      "quest": wire.QUEST, "sequence": sequence, "open": opened, "state": state},
            }

        def request(sequence, opened, kind):
            return [(sequence, opened, kind, wire.BOROUGH, pawn, wire.BOARD, wire.QUEST)]

        bridge = SimpleNamespace(input_namespace=uuid.uuid4(), native_wire_board_requests=Mock(), command=AsyncMock())
        control = SimpleNamespace(event=Mock())
        view = wire.WireBoardView(bridge, control)
        proof = {"map": wire.BOROUGH, "board": wire.BOARD, "id": wire.QUEST, "pawn": "Player_FP_C authority"}
        action = AsyncMock(return_value={"native_wire_quest": {**proof, "state": "missing"}})
        bridge.native_wire_board_requests.return_value = request(1, 1, "view")
        await view.sync(frame(0, 0, "armed"), "epoch", True, action)
        action.assert_not_awaited()

        bridge.native_wire_board_requests.return_value = []
        bridge.command.return_value = frame(1, 1, "offer")
        await view.sync(frame(1, 1, "loading"), "epoch", True, action)
        self.assertEqual([call.args[0] for call in action.await_args_list], ["native-wire-quest-preflight"])

        bridge.native_wire_board_requests.return_value = request(2, 1, "start")
        bridge.command.return_value = frame(2, 1, "missing")
        action.return_value = {"native_wire_quest": {**proof, "state": "active"}}
        await view.sync(frame(2, 1, "starting"), "epoch", True, action)
        await view.sync(frame(2, 1, "starting"), "epoch", True, action)
        self.assertEqual([call.args[0] for call in action.await_args_list],
                         ["native-wire-quest-preflight", "native-quest-start", "native-wire-quest-preflight"])

        bridge.native_wire_board_requests.return_value = request(3, 1, "turnin")
        bridge.command.return_value = frame(3, 1, "unavailable")
        await view.sync(frame(3, 1, "redeeming"), "epoch", True, action)
        self.assertEqual(action.await_count, 3)
        self.assertIn("native_wire_board_stale_confirmation",
                      [call.kwargs.get("reason") for call in control.event.call_args_list])

    async def test_selection_and_redemption_are_bound_to_the_exact_board_and_contract(self):
        pawn = "Player_FP_C receiving-client"
        computer = wire.native_computer_board.QUEST_ID
        bridge = SimpleNamespace(input_namespace=uuid.uuid4(), native_wire_board_requests=Mock(), command=AsyncMock())
        action = AsyncMock()
        view = wire.WireBoardView(bridge, SimpleNamespace(event=Mock()))

        async def press(area, quest, sequence, opened, kind, expected):
            board = wire.BOARDS[area]
            scope = {"map": area, "pawn": pawn, "board": board, "quest": quest, "sequence": sequence, "open": opened}
            snapshot = {"map": area, "pawns": {"local": {"name": pawn}},
                        "native_wire_board": {**scope, "state": wire.WAITING[kind]}}
            bridge.native_wire_board_requests.return_value = [(sequence, opened, kind, area, pawn, board, quest)]
            bridge.command.return_value = {**snapshot, "native_wire_board": {**scope, "state": expected}}
            result = await view.sync(snapshot, area, True, action)
            return result["native_wire_board"]["state"]

        borough = wire.BOROUGH
        action.return_value = {"native_wire_quest": {"map": borough, "board": wire.BOARD, "id": wire.QUEST, "state": "missing"}}
        self.assertEqual(await press(borough, wire.QUEST, 1, 1, "view", "offer"), "offer")
        self.assertEqual(await press(borough, computer, 2, 1, "start", "unavailable"), "unavailable")
        self.assertEqual(action.await_count, 1)

        proof = {"quest": computer, "step_after": 1, "objective_guid": "A" * 32}
        action.return_value = {"native_computer_board": {**proof, "map": borough, "board": wire.BOARD, "can_turn_in": False}}
        self.assertEqual(await press(borough, computer, 3, 3, "view", "elsewhere"), "elsewhere")
        self.assertEqual(await press(borough, computer, 4, 3, "turnin", "unavailable"), "unavailable")
        self.assertEqual(action.await_count, 2)

        scientist = wire.SCIENTIST_SHOP
        action.return_value = {"native_computer_board": {
            **proof, "map": scientist, "board": wire.BOARDS[scientist], "can_turn_in": True,
        }}
        photo = wire.native_turret_photo.QUEST_ID
        self.assertEqual(await press(scientist, photo, 5, 5, "view", "unavailable"), "loading")
        self.assertEqual(action.await_count, 2)
        self.assertEqual(await press(scientist, computer, 6, 6, "view", "ready"), "ready")
        action.return_value = {"quest_id": computer, "step": -10}
        self.assertEqual(await press(scientist, computer, 7, 6, "turnin", "completed"), "completed")
        self.assertEqual(action.await_args.args[0::2], ("native-quest-turnin", computer))

    async def test_photo_menu_tracks_the_stock_offer_and_final_step(self):
        view = wire.WireBoardView(None, None)
        quest = wire.native_turret_photo.QUEST_ID
        action = AsyncMock(side_effect=wire.party.RuleError("stock_turret_photo_quest_not_offered"))
        self.assertEqual(await view._inspect(wire.BOROUGH, wire.BOARD, quest, "event", action), "not_offered")
        action.side_effect = None
        proof = {"map": wire.BOROUGH, "target": wire.BOARD, "quest": quest}
        for step, expected in ((None, "offer"), (1, "missing"), (2, "missing"), (3, "ready"), (-10, "completed")):
            action.return_value = {"native_turret_photo": {**proof, "step_after": step}}
            self.assertEqual(await view._inspect(wire.BOROUGH, wire.BOARD, quest, "event", action), expected)


    async def test_lake_jammer_menu_uses_the_exact_stock_board_and_objective_step(self):
        view = wire.WireBoardView(None, None)
        spec = wire.LAKE_JAMMER
        self.assertIn(spec.quest, wire.QUESTS[spec.board_map])
        self.assertEqual(spec.board, wire.BOARDS[spec.board_map])
        self.assertNotIn(spec.quest, wire.QUESTS[wire.BOROUGH])
        action = AsyncMock(side_effect=wire.party.RuleError("stock_lake_jammer_quest_not_offered"))
        self.assertEqual(await view._inspect(spec.board_map, spec.board, spec.quest, "event", action), "not_offered")
        action.side_effect = None
        proof = {"map": spec.board_map, "board": spec.board, "quest": spec.quest}
        for step, expected in ((None, "offer"), (1, "missing"), (2, "ready"), (-7, "completed"), (-10, "completed")):
            action.return_value = {spec.key: {**proof, "step_after": step}}
            self.assertEqual(await view._inspect(spec.board_map, spec.board, spec.quest, "event", action), expected)
        with self.assertRaisesRegex(RuntimeError, "did not identify the selected stock contract"):
            await view._inspect(wire.BOROUGH, wire.BOARD, spec.quest, "event", action)
