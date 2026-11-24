import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_storm_ambience as weather, runtime
from tools.silverfish.travel import OBSERVATORY


VARIANT = "/Game/Classes/Weather/Storms/EatherFlare_BP.EatherFlare_BP_C"
WIND = "/Game/Assets/Sounds/WAVs/Ambiant_Wav/Exterior_Amb/Wind_Strong_01.Wind_Strong_01"
FOG = "/Game/Assets/Materials/Mat_Functions/Mat_FlareFog.Mat_FlareFog"
EPOCH = "c" * 32
HOST = [weather.HOST_TAG, "active", VARIANT, "1", ".9", "4", "-", WIND, ".5"]
HOST_FOG = [weather.HOST_FOG_TAG, VARIANT, FOG, ".2", "50000", ".1", ".2", ".3", "1"]
GUEST = [weather.GUEST_TAG, EPOCH, "active", VARIANT, "1", ".9", "-", WIND, ".5", "0", "0", "1"]


class StockStormAmbienceTests(unittest.IsolatedAsyncioTestCase):
    def test_stock_variant_and_fog_parse_as_one_scoped_frame(self):
        bridge = runtime.Bridge(Path("."), Mock())

        def parse_status(role, phase, *rows):
            lines = [("schema", "1"), ("seq", "1"), ("session", role, phase, OBSERVATORY, "0", "0"), *rows]
            return bridge._parse_status("\n".join("\t".join(line) for line in lines))

        snapshot = parse_status("host", "hosting", HOST, HOST_FOG)
        self.assertEqual(snapshot[weather.KEY]["fog"]["start_distance"], 50000.0)
        self.assertTrue(weather.valid(snapshot[weather.KEY]))
        guest_fog = [weather.GUEST_FOG_TAG, EPOCH, *HOST_FOG[1:]]
        snapshot = parse_status("client", "connected", GUEST, guest_fog, [weather.GUARD_TAG, "2"])
        self.assertTrue(snapshot[weather.GUEST]["fog_registered"])
        self.assertEqual(snapshot[weather.GUEST]["fog"]["material"], FOG)
        self.assertEqual(snapshot[weather.GUARD], 2)

        self.assertIsNone(parse_status("host", "hosting", [*HOST[:7], "/Game/Mod.Different", "1"]))
        self.assertIsNone(parse_status("host", "hosting", HOST, [*HOST_FOG[:2], WIND, *HOST_FOG[3:]]))
        self.assertIsNone(parse_status("client", "connected", GUEST, [guest_fog[0], "a" * 32, *guest_fog[2:]]))

    async def test_refreshes_current_visit_then_clears_on_idle(self):
        game = {"state": "connected", "map": OBSERVATORY,
                "native_world_view": {"mode": "applied", "map": OBSERVATORY, "epoch": EPOCH}}
        frame = {**weather.parse_host(HOST), "epoch": EPOCH, "map": OBSERVATORY}
        weather.add_fog(frame, HOST_FOG)
        idle = {"phase": "idle", "epoch": EPOCH, "map": OBSERVATORY}
        rendered = {**game, "ack": {"message": "projected"}, weather.GUEST: weather.parse_guest(GUEST)}
        cleared = {**game, "ack": {"message": "cleared"}, weather.GUEST: {"epoch": EPOCH, "phase": "idle"}}
        bridge = Mock(command=AsyncMock(side_effect=[rendered, rendered, cleared]))
        control = Mock()
        projection = weather.Projection()
        with patch.object(weather.time, "monotonic", side_effect=[10, 10.5, 11.1, 11.2]):
            for state in (frame, frame, frame, idle):
                await projection.sync(bridge, control, game, state, EPOCH, .1)
            await projection.sync(bridge, control, game, frame, EPOCH, 2)
            await projection.sync(bridge, control, game, {**frame, "epoch": "a" * 32}, EPOCH, .1)

        self.assertEqual(bridge.command.await_count, 3)
        self.assertEqual(bridge.command.await_args_list[0], bridge.command.await_args_list[1])
        bridge.command.assert_awaited_with(weather.COMMAND, EPOCH, "idle", timeout=1.5)
        self.assertEqual([entry.kwargs["phase"] for entry in control.event.call_args_list], ["active", "idle"])


if __name__ == "__main__":
    unittest.main()
