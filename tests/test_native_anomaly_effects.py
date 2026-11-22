import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish.native_anomaly_effects import (
    CANISTERS, ELECTRODE_PHASES, FIXED_EXPLOSIVES, Playback, VORTEX_SENSORS,
    project_canisters, project_electrodes, project_fixed_explosives, project_sensors,
)
from tools.silverfish.native_damage_feedback import Playback as HurtPlayback


class AnomalyPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def test_psychic_and_physical_feedback_from_one_hit_both_play_once(self):
        playback = HurtPlayback()
        epoch = "a" * 32
        game = {"state": "connected", "map": "Crossroads", "pawns": {"local": {"name": "GuestPawn"}}}
        frame = {"map": game["map"], "epoch": epoch, "cues": [
            [10, 6, 0, 0, 0, 16], [11, 7, 0, 0, 0, 16],
            [12, 8, 1, 0, 0, 16], [13, 38, 0, 0, 0, 0],
        ]}
        result = {**game, "ack": {"message": "guest hurt feedback played"}}
        bridge = Mock(command=AsyncMock(return_value=result))
        control = Mock()
        for _ in range(2):
            await playback.sync(bridge, control, game, frame, epoch)
        self.assertEqual([call.kwargs["serial"] for call in control.event.call_args_list], [11, 13])
        self.assertEqual([call.kwargs["sound_mask"] for call in control.event.call_args_list], [16, 0])
        self.assertEqual(bridge.command.await_count, 2)

    async def test_spatial_cues_play_once_per_authenticated_visit(self):
        playback = Playback()
        epoch = "a" * 32
        game = {"state": "connected", "map": "Crossroads", "pawns": {"local": {"location": [0, 0, 0]}}}
        frame = {"map": game["map"], "epoch": epoch, "cues": [
            [1, "charge", [100, 0, 0], 1],
            [1, "charge", [100, 0, 0], 1],
            [2, "charge", [4100, 0, 0], 1],
            [3, "blast", [4100, 0, 0], 1],
            [4, "blast", [0, 0, 0], float("inf")],
            [5, "unknown", [0, 0, 0], 1],
            [6, "vortex", [100, 0, 0], 1],
            [8, "electrode", [200, 0, 0], 1.5, .65],
            [9, "electrode", [200, 0, 0], 1, float("inf")],
        ]}
        snapshot = {**game, "ack": {"message": "1:1:0,3:1:0,3:1:0,8:1:0,6:1:1,7:0:0"}}
        bridge = Mock(command=AsyncMock(return_value=snapshot))
        control = Mock()
        self.assertIs(await playback.sync(bridge, control, game, frame, epoch, "host"), snapshot)
        bridge.command.assert_awaited_once_with(
            "native-anomaly-play-cues", game["map"], epoch, "1|charge|100|0|0|1|1", "3|blast|4100|0|0|1|1",
            "6|vortex|100|0|0|1|1", "8|electrode|200|0|0|1.5|0.65", timeout=1.5,
        )
        self.assertEqual([call.kwargs["serial"] for call in control.event.call_args_list], [1, 3, 8, 6])
        self.assertTrue(all(call.kwargs["particles"] for call in control.event.call_args_list))
        self.assertTrue(control.event.call_args.kwargs["animated"])
        await playback.sync(bridge, control, game, frame, epoch, "host")
        await playback.sync(bridge, control, game, frame, "b" * 32, "host")
        self.assertEqual(bridge.command.await_count, 1)

    async def test_sensor_projection_only_updates_changed_placed_actors_in_the_current_visit(self):
        epoch = "b" * 32
        game = {"state": "connected", "map": "Funnel", VORTEX_SENSORS: [
            {"path": "Trap1", "relative": [0, 0, 110]},
            {"path": "Trap2", "relative": [0, 0, 150]},
            {"path": None, "relative": [0, 0, 195]},
        ]}
        frame = {"map": game["map"], "epoch": epoch, "sensors": [
            ["Trap1", [0, 0, 185]], ["Trap2", [0, 0, 150]], ["Missing", [0, 0, 102]], [None, [0, 0, 185]],
        ]}
        bridge = Mock(command=AsyncMock(return_value=game))
        await project_sensors(bridge, game, frame, "a" * 32)
        bridge.command.assert_not_awaited()
        await project_sensors(bridge, game, frame, epoch)
        bridge.command.assert_awaited_once_with("native-vortex-apply-sensors", "Funnel", epoch, "Trap1|0|0|185", timeout=1.5)

    async def test_electrode_phases_address_placed_and_unique_generated_actors(self):
        epoch = "b" * 32
        game = {"state": "connected", "map": "Lab", ELECTRODE_PHASES: [
            {"path": "Destroyed", "location": [10, 0, 0], "phase": 1},
            {"path": None, "location": [20, 0, 0], "phase": 0},
            {"path": None, "location": [30, 0, 0], "phase": 0},
            {"path": None, "location": [31, 0, 0], "phase": 0},
        ]}
        frame = {"map": "Lab", "epoch": epoch, "electrodes": [
            ["Destroyed", [10, 0, 0], 2], [None, [20, 0, 0], 1],
            [None, [30, 0, 0], 1], [None, [20, 0, 0], 2], ["Missing", [40, 0, 0], 1],
        ]}
        bridge = Mock(command=AsyncMock(return_value=game))
        await project_electrodes(bridge, game, frame, "a" * 32)
        bridge.command.assert_not_awaited()
        await project_electrodes(bridge, game, frame, epoch)
        bridge.command.assert_awaited_once_with(
            "native-electrode-apply-phase", "Lab", epoch, "Destroyed|10|0|0|2", "-|20|0|0|1", timeout=1.5)

    async def test_canister_projection_only_sends_changed_actors_in_the_current_guest_visit(self):
        epoch = "b" * 32
        game = {"state": "connected", "map": "Crossroads", CANISTERS: {
            "Removed": {"phase": 1}, "Igniting": {"phase": 0}, "StillCold": {"phase": 0}, "Invalid": {"phase": 0},
        }}
        frame = {"map": "Crossroads", "epoch": epoch, "props": {
            "Removed": 2, "Igniting": 1, "StillCold": 0, "Missing": 1, "Invalid": True,
        }}
        bridge = Mock(command=AsyncMock(return_value=game))
        await project_canisters(bridge, game, frame, "a" * 32)
        bridge.command.assert_not_awaited()
        await project_canisters(bridge, game, frame, epoch)
        bridge.command.assert_awaited_once_with(
            "native-canister-apply", "Crossroads", epoch, "Removed|2", "Igniting|1", timeout=1.5)

    async def test_fixed_prop_projection_repairs_both_stock_flag_and_mesh(self):
        epoch = "c" * 32
        game = {"state": "connected", "map": "Dry Dock", FIXED_EXPLOSIVES: {
            "Broken": {"destroyed": False, "damaged_mesh": False},
            "Reset": {"destroyed": True, "damaged_mesh": True},
            "WrongMesh": {"destroyed": True, "damaged_mesh": False},
            "Matching": {"destroyed": True, "damaged_mesh": True},
        }}
        frame = {"map": "Dry Dock", "epoch": epoch, "props": {
            "Broken": True, "Reset": False, "WrongMesh": True, "Matching": True, "Unknown": True,
        }}
        bridge = Mock(command=AsyncMock(return_value=game))
        await project_fixed_explosives(bridge, game, frame, "b" * 32)
        bridge.command.assert_not_awaited()
        await project_fixed_explosives(bridge, game, frame, epoch)
        bridge.command.assert_awaited_once_with(
            "native-fixed-explosives-apply", "Dry Dock", epoch, "Broken|1", "Reset|0", "WrongMesh|1", timeout=1.5)
