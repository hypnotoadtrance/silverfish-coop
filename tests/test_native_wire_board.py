import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish.authored_routes import MAPS as EXPANDED_MAPS

from tools.silverfish import native_board_contract, native_rewards, native_turret_photo as photo, native_wire_board as wire, party, runtime


class WireBoardTests(unittest.IsolatedAsyncioTestCase):
    async def test_turret_contract_and_shutter_receipt_stay_pending_until_checkpoint(self):
        data = party.new_party()
        party.add_player(data, "host")
        party.add_player(data, "guest")
        pawn, camera = "Player_FP_C Photographer", "91" * 16
        host = SimpleNamespace(
            native_guest=True, native_zones=True, native_profile=True, stopping=False, transitioning=False,
            native_checkpoint_unsafe=False, pending_host_save=None, party=data, camera_ready_at={},
            checkpoint_native=AsyncMock(), control=Mock(), last_seen=time.monotonic(),
            roles=lambda: {"host": "host", "guest": "guest"},
            native_recipients=lambda: {"guest": {"pawn": pawn, "connected": True}},
        )

        def snapshot(area, target, action, before, after, *, cash=0, xp=0):
            return {"state": "hosting", "map": area,
                    "native_guest_equipped_weapons": {pawn: {"kind": "camera", "guid": camera}},
                    "native_turret_photo": {
                        "action": action, "map": area, "pawn": pawn, "quest": photo.QUEST_ID, "target": target,
                        "step_before": before, "step_after": after, "cash_before": 5, "cash_after": 5 + cash,
                        "xp_before": 4, "xp_after": 4 + xp, "rep_after": 25, "camera": camera, "visibility": .85,
                    }}

        area = photo.BOROUGH
        board = photo.BOARDS[area]
        targets = sorted(photo.TARGETS)
        host.last_game = {"state": "hosting", "map": area}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            snapshot(area, board, "start", None, 1),
            snapshot(photo.DEAD_BRIDGE, targets[0], "capture", 1, 2),
            snapshot(photo.DEAD_BRIDGE, targets[1], "capture", 2, 3),
            snapshot(area, board, "turn_in", 3, -10, cash=700, xp=1),
        ]))
        started = await photo.advance(host, bridge, "guest", "native-turret-photo-start")
        self.assertEqual(started["step"], 1)
        host.last_game = {"state": "hosting", "map": photo.DEAD_BRIDGE,
                          "native_guest_equipped_weapons": {pawn: {"kind": "camera", "guid": camera}}}
        operation = uuid.uuid4().hex
        first = await photo.shutter(host, bridge, "guest", camera, operation)
        self.assertEqual(await photo.shutter(host, bridge, "guest", camera, operation), first)
        self.assertEqual(first["step"], 2)
        second = await photo.advance(host, bridge, "guest", photo.CAPTURE, targets[1])
        self.assertEqual(set(second["photos"]), photo.TARGETS)
        host.last_game = {"state": "hosting", "map": area}
        paid = await photo.advance(host, bridge, "guest", "native-turret-photo-turn-in")
        self.assertEqual((paid["step"], paid["wallet"]), (-10, 705))
        self.assertEqual(host.party["players"]["guest"][native_rewards.PENDING], 1)
        self.assertIs(host.pending_host_save, False)
        self.assertEqual(bridge.command.await_count, 4)
        host.checkpoint_native.assert_not_awaited()

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

    async def test_interact_opens_verified_stock_menu_for_unregistered_offer_and_deposit(self):
        area, pawn = wire.SHOPKEEP, "Player_FP_C ClientView"
        board = wire.BOARDS[area]
        quest = "Clt_Eye_02"
        visit = "12" * 16
        other = "Clt_Molasis"
        bridge = SimpleNamespace(input_namespace=uuid.uuid4(), native_wire_board_requests=Mock(), command=AsyncMock())
        control = SimpleNamespace(event=Mock())
        view = wire.WireBoardView(bridge, control)
        self.assertNotIn(quest, wire.QUESTS[area])
        action = AsyncMock()

        def frame(shown, sequence, opened, id):
            return {"map": area, "pawns": {"local": {"name": pawn}}, "native_wire_board_stock": {
                "map": area, "pawn": pawn, "board": board, "quest": id, "state": shown, "sequence": sequence, "open": opened,
            }}

        async def press(kind, sequence, opened, id, epoch=visit):
            snapshot = frame(wire.WAITING[kind], sequence, opened, id)
            bridge.native_wire_board_requests.return_value = [(sequence, opened, kind, area, pawn, board, id)]
            return await view.sync(snapshot, epoch, True, action)

        action.return_value = {native_board_contract.CATALOG_KEY: {
            "map": area, "pawn": "Player_FP_C AuthorityGuest", "board": board, "offers": [quest], "turnins": [],
        }}
        bridge.command.return_value = frame("visible", 1, 1, "-")
        self.assertEqual((await press("stock-view", 1, 1, "-"))["native_wire_board_stock"]["state"], "visible")
        bridge.command.assert_awaited_once_with("native-wire-board-stock-open", "1", area, pawn, quest, "-", timeout=3)

        await press("stock-start", 2, 1, other)
        await press("stock-start", 3, 1, quest, epoch="stale visit")
        self.assertEqual(action.await_count, 1)
        action.return_value = {"quest_id": quest, "step": 1}
        await press("stock-start", 4, 1, quest)
        self.assertEqual(action.await_args.args[0::2], ("native-quest-start", native_board_contract.menu_target(visit, board, quest)))
        self.assertEqual(bridge.command.await_args.args, ("native-wire-board-stock-result", "4", area, pawn, quest, "accepted"))
        await press("stock-start", 4, 1, quest)
        self.assertEqual(action.await_count, 2)

        action.return_value = {native_board_contract.CATALOG_KEY: {
            "map": area, "pawn": "Player_FP_C AuthorityGuest", "board": board, "offers": [], "turnins": [quest],
        }}
        bridge.command.return_value = frame("visible", 5, 5, "-")
        self.assertEqual((await press("stock-view", 5, 5, "-"))["native_wire_board_stock"]["state"], "visible")
        self.assertEqual(bridge.command.await_args.args, ("native-wire-board-stock-open", "5", area, pawn, "-", quest))
        action.return_value = {"quest_id": quest, "step": -10}
        await press("stock-turnin", 6, 5, quest)
        self.assertEqual(action.await_args.args[0::2], ("native-quest-turnin", native_board_contract.menu_target(visit, board, quest)))
        self.assertEqual(bridge.command.await_args.args, ("native-wire-board-stock-result", "6", area, pawn, quest, "completed"))
        self.assertIsNone(view.stock_open)

    async def test_discovers_real_stock_board_identity_beyond_specialist_placements(self):
        gunsmith = "/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P"
        gunsmith_board = f"BountyBoard_BP_C {gunsmith}.Shop_Gunsmith_P:PersistentLevel.BountyBoard_BP_C_0"
        self.assertIn(gunsmith, EXPANDED_MAPS)
        for area, board in ((wire.SHOPKEEP, wire.BOARDS[wire.SHOPKEEP].removesuffix("_1") + "_2"),
                            (gunsmith, gunsmith_board)):
            with self.subTest(area=area, board=board):
                self.assertEqual(native_board_contract.board_map(board), area)
                self.assertFalse(wire.catalog_specialists(area, board))
                self.assertEqual(wire.stock_fixture_board(area, board), (board,))
                pawn, visit, quest = "Player_FP_C ClientView", "56" * 16, "Clt_Eye_02"
                session = "23" * 16
                view_key = f"{session}:{visit}"
                command = AsyncMock()
                requests = Mock()
                bridge = SimpleNamespace(input_namespace=uuid.uuid4(), native_wire_board_requests=requests, command=command)
                view = wire.WireBoardView(bridge, Mock())
                action = AsyncMock(return_value={native_board_contract.CATALOG_KEY: {
                    "map": area, "pawn": "Player_FP_C AuthorityGuest", "board": board, "offers": [quest], "turnins": [],
                }})

                def frame(state, sequence, opened, choice):
                    return {"map": area, "pawns": {"local": {"name": pawn}}, "native_wire_board_stock": {
                        "map": area, "pawn": pawn, "board": board, "quest": choice, "state": state,
                        "sequence": sequence, "open": opened,
                    }}

                requests.return_value = [(1, 1, "stock-view", area, pawn, board, "-")]
                command.return_value = frame("visible", 1, 1, "-")
                await view.sync(frame("loading", 1, 1, "-"), view_key, True, action, visit=visit)
                self.assertEqual(action.await_args.args[0::2], (native_board_contract.CATALOG, board))
                self.assertEqual(view.stock_open.identity, (view_key, area, pawn, board, 1))

                requests.return_value = [(2, 1, "stock-start", area, pawn, board, quest)]
                action.return_value = {"quest_id": quest, "step": 1}
                await view.sync(frame("starting", 2, 1, quest), f"{'34' * 16}:{visit}", True, action, visit=visit)
                self.assertEqual(action.await_count, 1)

                requests.return_value = [(3, 1, "stock-start", area, pawn, board, quest)]
                await view.sync(frame("starting", 3, 1, quest), view_key, True, action, visit=visit)
                self.assertEqual(action.await_args.args[0::2], (
                    "native-quest-start", native_board_contract.menu_target(visit, board, quest),
                ))

    def test_renews_embedded_board_lease_without_a_file(self):
        bridge = SimpleNamespace(local_bridge=Mock())
        wire.renew(bridge, "/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P", 901)
        bridge.local_bridge.publish_stock_board_heartbeat.assert_called_once_with(901)

    def test_embedded_board_and_ordinary_input_are_delivered_once_to_their_own_consumers(self):
        area, board = wire.SHOPKEEP, wire.BOARDS[wire.SHOPKEEP]
        pawn = "Player_FP_C ClientView"
        board_record = f"wire-board\t6\t6\tstock-view\t{area}\t{pawn}\t{board}\t-".encode()
        for board_first in (False, True):
            with self.subTest(board_first=board_first), tempfile.TemporaryDirectory() as directory:
                bridge = runtime.Bridge(Path(directory), None)
                bridge.local_bridge = Mock(input_ready=Mock(side_effect=[True, False, False, False]),
                                           read_input=Mock(return_value=[b"interact\t4", board_record]))
                identity = uuid.uuid5(bridge.input_namespace, "4").hex
                wanted = [(6, 6, "stock-view", area, pawn, board, "-")]
                if board_first:
                    self.assertEqual(bridge.native_wire_board_requests(), wanted)
                    self.assertEqual(bridge.interactions(), [(identity, None)])
                else:
                    self.assertEqual(bridge.interactions(), [(identity, None)])
                    self.assertEqual(bridge.native_wire_board_requests(), wanted)
                self.assertEqual(bridge.native_wire_board_requests(), [])
                self.assertEqual(bridge.interactions(), [])
                bridge.local_bridge.read_input.assert_called_once()
                self.assertEqual(list(Path(directory).iterdir()), [])

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
