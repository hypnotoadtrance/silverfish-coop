import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_kill_volume as volume, party
from tools.silverfish.runtime import Bridge
from tools.silverfish.service import Host
from tools.silverfish.travel import CROSSROADS, NOTHING


PAWN = volume.PAWN + "12"
CHARACTER = "test-character"


def status(state="armed", fault="-"):
    return ["native-kill-volume", CROSSROADS, volume.VOLUME, state, "2", "0", fault]


class KillVolumeTests(unittest.IsolatedAsyncioTestCase):
    def test_closed_world_gate_and_exact_recovery_snapshot(self):
        report = volume.parse_state(status(), "hosting", CROSSROADS)
        recovered = ["native-kill-volume-recovered", PAWN, "2", "teammate", "10", "120", "500", "0.0"]
        self.assertEqual(volume.parse_recovered(recovered, "hosting", report),
                         {"pawn": PAWN, "sequence": 2, "placement": "teammate", "location": [10, 120, 500], "native_health_at_down": 0})
        self.assertIsNone(volume.parse_recovered([*recovered[:-1], "-"], "hosting", report)["native_health_at_down"])
        self.assertEqual(volume.parse_state(status(), "connected", CROSSROADS)["overlaps_vetoed"], 2)
        with self.assertRaises(ValueError):
            volume.parse_state(status(), "hosting", NOTHING)
        with self.assertRaises(ValueError):
            volume.parse_state(status("pending"), "connected", CROSSROADS)
        for index, invalid in ((1, PAWN + "extra"), (2, "0"), (4, "nan"), (6, "-22575"), (7, "3.452")):
            with self.subTest(index=index), self.assertRaises(ValueError):
                volume.parse_recovered([*recovered[:index], invalid, *recovered[index+1:]], "hosting", report)

    def test_bridge_rejects_duplicate_claim_of_one_stock_overlap(self):
        first = ["native-kill-volume-recovered", PAWN, "2", "teammate", "10", "120", "500"]
        other = [*first[:1], volume.PAWN + "14", *first[2:]]
        core = [["schema", "1"], ["seq", "1"], ["session", "host", "hosting", CROSSROADS, "1", "0"], status()]
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "status.tsv"
            bridge = Bridge(directory, None)
            path.write_text("\n".join("\t".join(row) for row in (*core, first)) + "\n", encoding="ascii")
            self.assertEqual(bridge.snapshot()[volume.RECOVERED][PAWN]["sequence"], 2)
            path.write_text("\n".join("\t".join(row) for row in (*core, first, other)) + "\n", encoding="ascii")
            self.assertIsNone(bridge.snapshot())

    def test_unresolved_crossing_preserves_previous_checkpoint(self):
        host = Host.__new__(Host)
        host.last_game = {"state": "hosting", "map": CROSSROADS, volume.KEY: {"state": "pending"}}
        host.store = Mock()
        host.stopping = host.native_checkpoint_unsafe = False
        with self.assertRaisesRegex(RuntimeError, volume.PENDING):
            host.checkpoint()
        host.store.status.assert_not_called()
        self.assertFalse(host.stopping or host.native_checkpoint_unsafe)
        host.last_game[volume.KEY] = {"state": "fault", "fault": "safe_floor_unavailable"}
        with self.assertRaisesRegex(RuntimeError, "safe_floor_unavailable"):
            host.checkpoint()
        host.store.status.assert_not_called()
        self.assertTrue(host.stopping and host.native_checkpoint_unsafe)

    async def test_fixture_authenticates_only_exact_connected_saved_pawn(self):
        game = {"state": "hosting", "map": CROSSROADS, volume.KEY: {"state": "armed"}}
        recipient = {"pawn": PAWN, "connected": False}
        host = SimpleNamespace(last_game=game, last_seen=time.monotonic(), native_guest=True, expanded_zones=True,
                               transitioning=False, stopping=False, party={"players": {CHARACTER: {"downed": False}}},
                               roles=lambda: {"guest": CHARACTER}, native_recipients=lambda: {CHARACTER: recipient})
        bridge = SimpleNamespace(command=AsyncMock(return_value=dict(game, **{volume.KEY: {"state": "pending"}})))
        with self.assertRaisesRegex(party.RuleError, "standing_pawn_required"):
            await volume.fixture(host, bridge, "guest")
        bridge.command.assert_not_awaited()
        recipient["connected"] = True
        receipt = await volume.fixture(host, bridge, "guest")
        self.assertEqual((receipt["pawn"], receipt["volume"], receipt["state"]), (PAWN, volume.VOLUME, "falling"))
        bridge.command.assert_awaited_once_with(volume.FIXTURE, PAWN, timeout=5)
