import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish.native_anomaly_effects import (
    ANGLER_COLORS, ANGLER_MAP, CANISTERS, ELECTRODE_PHASES, FIXED_EXPLOSIVES, OBSERVER_OPACITY, Playback, VORTEX_SENSORS,
    angler_frame, observer_frame, project_angler_colors, project_canisters, project_electrodes,
    project_fixed_explosives, project_observer_opacity, project_sensors,
)
from tools.silverfish.native_damage_feedback import Playback as HurtPlayback
from tools.silverfish.native_weather_view import SirenPlayback


class AnomalyPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def test_observer_opacity_waits_for_a_unique_replica_and_updates_its_fade(self):
        epoch = "d" * 32
        host = {"state": "hosting", "map": ANGLER_MAP,
                "pawns": {"guest": {"name": "Guest", "location": [0, 0, 0]}},
                OBSERVER_OPACITY: [[[100, 0, 0], .8, 0], [[350, 0, 0], float("nan"), 1], [[20000, 0, 0], 1, 1]]}
        frame = observer_frame(host, epoch, "Guest")
        self.assertEqual(frame["actors"], [host[OBSERVER_OPACITY][0]])
        game = {"state": "connected", "map": ANGLER_MAP,
                "native_anomaly_positions": [{"kind": "observer", "location": [100, 0, 0]},
                                               {"kind": "observer", "location": [120, 0, 0]}]}
        bridge = Mock(command=AsyncMock(return_value=game))
        await project_observer_opacity(bridge, game, frame, epoch)
        bridge.command.assert_not_awaited()
        game["native_anomaly_positions"].pop()
        game[OBSERVER_OPACITY] = [[[100, 0, 0], .85, 1]]
        await project_observer_opacity(bridge, game, frame, epoch)
        bridge.command.assert_awaited_once_with(
            "native-observer-apply-opacity", ANGLER_MAP, epoch, "100.00000|0.00000|0.00000|0.80000|0.00000", timeout=1.5)

    async def test_angler_colors_require_nearby_unambiguous_replica_and_current_visit(self):
        epoch = "c" * 32
        old, changed = [0, 0, 0, 0, 0, 0], [1, .1, .2, .3, .4, .5]
        host = {"state": "hosting", "map": ANGLER_MAP, "pawns": {"guest": {"name": "Guest", "location": [0, 0, 0]}},
                ANGLER_COLORS: [[[200, 50, 20], changed], [[4000, 10, 20], changed], [[18000, 0, 0], changed]]}
        frame = angler_frame(host, epoch, "Guest")
        self.assertEqual(len(frame["lights"]), 2)
        guest = {"state": "connected", "map": ANGLER_MAP,
                 ANGLER_COLORS: [[[220, 50, 20], old], [[3980, 10, 20], old], [[4020, 10, 20], old]]}
        bridge = Mock(command=AsyncMock(return_value=guest))
        await project_angler_colors(bridge, guest, frame, "b" * 32)
        bridge.command.assert_not_awaited()
        await project_angler_colors(bridge, guest, frame, epoch)
        bridge.command.assert_awaited_once_with(
            "native-angler-apply-colors", ANGLER_MAP, epoch,
            "200.00000|50.00000|20.00000|1.00000|0.10000|0.20000|0.30000|0.40000|0.50000", timeout=1.5)

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

    async def test_bottle_break_plays_only_the_nearby_stock_cue_without_a_live_actor(self):
        epoch = "a" * 32
        area = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        game = {"state": "connected", "map": area, "pawns": {"local": {"location": [0, 0, 0]}}}
        bridge = Mock(command=AsyncMock(return_value={**game, "ack": {"message": "7:1:0"}}))
        control = Mock()
        playback = Playback()
        frame = {"map": area, "epoch": epoch, "cues": [
            [7, "bottle", [100, 50, 2], 1, 1],
            [7, "bottle", [100, 50, 2], 1, 1],
            [8, "bottle", [8001, 0, 0], 1, 1],
            [9, "bottle", [100, 50, 2], 2, 1],
            [10, "bottle", [100, 50, 2], 1, .5],
        ]}
        await playback.sync(bridge, control, game, frame, "b" * 32, "host")
        await playback.sync(bridge, control, {**game, "state": "hosting"}, frame, epoch, "host")
        bridge.command.assert_not_awaited()

        for _ in range(2):
            await playback.sync(bridge, control, game, frame, epoch, "host")
        bridge.command.assert_awaited_once_with(
            "native-anomaly-play-cues", area, epoch, "7|bottle|100|50|2|1|1", timeout=1.5,
        )
        control.event.assert_called_once_with(
            "native_bottle_break_played", cue="bottle", serial=7, location=[100, 50, 2], map=area,
            particles=True, animated=False, scale=1, volume=1,
        )

    async def test_partial_anomaly_acknowledgment_retries_only_the_missing_cue(self):
        playback = Playback()
        epoch = "a" * 32
        game = {"state": "connected", "map": "Crossroads", "pawns": {"local": {"location": [0, 0, 0]}}}
        frame = {"map": game["map"], "epoch": epoch, "cues": [
            [1, "charge", [100, 0, 0], 1], [2, "blast", [100, 0, 0], 1],
        ]}
        bridge = Mock(command=AsyncMock(side_effect=[
            {**game, "ack": {"message": "1:1:0"}},
            {**game, "ack": {"message": "2:already"}},
        ]))
        control = Mock()
        for _ in range(3):
            await playback.sync(bridge, control, game, frame, epoch, "host")
        self.assertEqual(bridge.command.await_count, 2)
        bridge.command.assert_awaited_with(
            "native-anomaly-play-cues", game["map"], epoch, "2|blast|100|0|0|1|1", timeout=1.5)
        self.assertEqual([call.kwargs["serial"] for call in control.event.call_args_list], [1])

    async def test_active_weather_warnings_retry_by_source_without_replaying_an_older_cue(self):
        playback = SirenPlayback()
        epoch = "a" * 32
        game = {"state": "connected", "map": "Crossroads",
                "native_world_view": {"mode": "applied", "map": "Crossroads", "epoch": epoch}}
        first, second = "/Game/Crossroads:Siren_1", "/Game/Crossroads:Siren_2"
        warnings = [[1, first, 1, 20], [2, second, 3, 20], [3, first, 2, 20]]
        bridge = Mock(command=AsyncMock(side_effect=[
            TimeoutError("no receipt"),
            {**game, "ack": {"message": "stock weather siren played"}},
            {**game, "ack": {"message": "weather siren already played"}},
        ]))
        control = Mock()
        for _ in range(3):
            await playback.sync(bridge, control, game, warnings, epoch)
        self.assertEqual([call.args[2:5] for call in bridge.command.await_args_list],
                         [("3", first, "2"), ("2", second, "3"), ("3", first, "2")])
        self.assertEqual([call.kwargs["serial"] for call in control.event.call_args_list
                          if call.args == ("native_weather_siren_played",)], [2])
        await playback.sync(bridge, control, game, [[4, first, 4, 3]], epoch, snapshot_age=4)
        self.assertEqual(bridge.command.await_count, 3)

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

    async def test_fuel_drum_cooling_and_blast_playback_are_independent(self):
        epoch = "a" * 32
        area = ANGLER_MAP
        drum = f"{area}.CrossRoads_P:PersistentLevel.Prop_FeulDrum_C_33"
        game = {"state": "connected", "map": area, "pawns": {"local": {"location": [0, 0, 0]}},
                CANISTERS: {drum: {"phase": 1}}}
        cooled = {**game, CANISTERS: {drum: {"phase": 0}}}
        bridge = Mock(command=AsyncMock(return_value=cooled))
        control = Mock()
        frame = {"map": area, "epoch": epoch, "props": {drum: 0}}
        self.assertIs(await project_canisters(bridge, game, frame, epoch), cooled)
        self.assertIs(await project_canisters(bridge, cooled, frame, epoch), cooled)
        bridge.command.assert_awaited_once_with("native-canister-apply", area, epoch, f"{drum}|0", timeout=1.5)
        control.event.assert_not_called()

        cue = {"map": area, "epoch": epoch, "cues": [[1, "fueldrum", [20, 0, 0], 1, 1]]}
        bridge.command.return_value = {**cooled, "ack": {"message": "1:1:0"}}
        playback = Playback()
        for _ in range(2):
            await playback.sync(bridge, control, cooled, cue, epoch, "host")
        bridge.command.assert_awaited_with("native-anomaly-play-cues", area, epoch, "1|fueldrum|20|0|0|1|1", timeout=1.5)
        control.event.assert_called_once_with(
            "native_fuel_drum_effect_played", cue="fueldrum", serial=1, location=[20, 0, 0],
            map=area, particles=True, animated=False, scale=1, volume=1)

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
