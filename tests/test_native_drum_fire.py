import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from tools.silverfish import native_drum_fire as fire
from tools.silverfish.runtime import Bridge


class GroundFireTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_snapshot_applies_once_to_the_current_visit_and_projects_empty_state(self):
        area, epoch = "/Game/Classes/Maps/CrossRoads", "a" * 32
        view = "1|0,2|4,10,20,30,0,90,0,1,1,1"
        host = {"state": "hosting", "map": area, fire.KEY: fire.parse_host("3", [view])}
        game = {"state": "connected", "map": area}
        accepted = {**game, fire.VIEW: {"epoch": epoch, "revision": 3, "desired": 1, "rendered": 1}}
        bridge = AsyncMock()
        bridge.command.return_value = accepted

        frame = fire.host_frame(host, epoch)
        self.assertIs(await fire.project(bridge, game, frame, "b" * 32), game)
        self.assertIs(await fire.project(bridge, game, frame, epoch), accepted)
        self.assertIs(await fire.project(bridge, accepted, frame, epoch), accepted)
        bridge.command.assert_awaited_once_with("native-drum-fire-view", area, epoch, "3", view, timeout=1.5)

        host[fire.KEY] = fire.parse_host("4", [])
        await fire.project(bridge, accepted, fire.host_frame(host, epoch), epoch)
        bridge.command.assert_awaited_with("native-drum-fire-view", area, epoch, "4", timeout=1.5)

    async def test_maximum_view_uses_one_bounded_bridge_ticket(self):
        area, epoch = "/Game/Classes/Maps/CrossRoads", "a" * 32
        part = ",-10000000.000,-10000000.000,-10000000.000,-360.000,-360.000,-360.000,10.000,10.000,10.000"
        content = ";".join(f"{i}{part}" for i in range(1, fire.MAX_PARTICLES + 1))
        fields = [f"{i}|-10000000.000,-10000000.000|{content}" for i in range(1, fire.MAX_FIELDS + 1)]
        host = {"state": "hosting", "map": area, fire.KEY: fire.parse_host("1", fields)}
        game = {"state": "connected", "map": area}
        bridge = AsyncMock()
        await fire.project(bridge, game, fire.host_frame(host, epoch), epoch)

        bridge.command.assert_awaited_once()
        arguments = bridge.command.await_args.args
        self.assertEqual("".join(arguments[4:]), "~".join(fields))
        self.assertTrue(all(len(arg) <= 1024 for arg in arguments))
        self.assertLess(len("\t".join(arguments)) + 34, 32768)

    def test_malformed_field_group_is_atomic_and_guest_view_is_typed(self):
        area, epoch = "/Game/Classes/Maps/CrossRoads", "a" * 32
        part = "1,10,20,30,0,90,0,1,1,1"
        view = f"1|0,2|{part}"
        self.assertIsNone(fire.parse_host("1", [view, f"01|0,2|{part}"]))
        self.assertIsNone(fire.parse_host("1", [view, f"2|0,2|{part};{part}"]))
        self.assertIsNone(fire.parse_host("1", [view.replace(",90,", ",nan,")]))
        self.assertIsNone(fire.parse_host("1", [view.replace("|0,2|", "|/Game/Bad|")]))
        bridge = Bridge(Path("unused"), None)
        host = (f"schema\t1\nseq\t20\nsession\thost\thosting\t{area}\t1\t0\n"
                f"native-drum-fire-host\t3\t{view}\n")
        guest = (f"schema\t1\nseq\t21\nsession\tclient\tconnected\t{area}\t1\t1\n"
                 f"native-drum-fire-view\t{epoch}\t3\t1\t0\n")
        self.assertEqual(bridge._parse_status(host)[fire.KEY], {"revision": 3, "fields": [view]})
        self.assertEqual(bridge._parse_status(guest)[fire.VIEW], {"epoch": epoch, "revision": 3, "desired": 1, "rendered": 0})
