import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_wire_board as wire


class WireBoardTests(unittest.IsolatedAsyncioTestCase):
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
        action = AsyncMock(return_value={"native_wire_quest": {"state": "missing"}})
        bridge.native_wire_board_requests.return_value = request(1, 1, "view")
        await view.sync(frame(0, 0, "armed"), "epoch", True, action)
        action.assert_not_awaited()

        bridge.native_wire_board_requests.return_value = []
        bridge.command.return_value = frame(1, 1, "offer")
        await view.sync(frame(1, 1, "loading"), "epoch", True, action)
        self.assertEqual([call.args[0] for call in action.await_args_list], ["native-wire-quest-preflight"])

        bridge.native_wire_board_requests.return_value = request(2, 1, "start")
        bridge.command.return_value = frame(2, 1, "missing")
        action.return_value = {"native_wire_quest": {"state": "active"}}
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
