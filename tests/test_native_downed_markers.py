import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.silverfish import native_downed_markers as markers
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


EPOCH = "a" * 32
VIEWER = "4aa50d80-198e-47ea-a740-2bf5376798a1"
GUEST = "aa38172c-067e-42c1-b930-081fefcc8df1"
PENDING = "ada6132a-4194-4d53-a73f-e5c2a7146260"


class MarkerTests(unittest.IsolatedAsyncioTestCase):
    def test_host_selects_current_downed_pawns_and_excludes_viewer(self):
        party = {"players": {slot: {"character_id": identity, "downed": True} for slot, identity in
                             zip(("host", "guest", "guest2", "guest3"), ("host", VIEWER, GUEST, PENDING), strict=True)},
                 "pending_spawns": {PENDING: {}}}
        positions = {identity: {"map": area, "location": [123, 456, 789]} for identity, area in
                     (("host", CROSSROADS), (VIEWER, CROSSROADS), (GUEST, OBSERVATORY), (PENDING, CROSSROADS))}
        self.assertEqual(markers.frame(party, positions, CROSSROADS, EPOCH, VIEWER), {
            "epoch": EPOCH, "map": CROSSROADS, "markers": {"host": [123, 456, 789]},
        })

    def test_status_requires_exact_distinct_markers_in_the_frame(self):
        frame = [markers.COMMAND, EPOCH, CROSSROADS, "1"]
        row = ["player-marker", EPOCH, CROSSROADS, GUEST, "10", "20", "30"]
        self.assertEqual(markers.parse(frame, [row], CROSSROADS)["markers"], {GUEST: [10.0, 20.0, 30.0]})
        with self.assertRaises(ValueError):
            markers.parse([*frame[:3], "2"], [row, row], CROSSROADS)
        with self.assertRaises(ValueError):
            markers.parse(frame, [[*row[:6], "nan"]], CROSSROADS)
        with self.assertRaises(ValueError):
            markers.parse(frame, [row], OBSERVATORY)

    async def test_client_renews_only_current_authenticated_markers_and_clears_on_revive(self):
        view = markers.MarkerView()
        bridge = SimpleNamespace(command=AsyncMock())
        public = {"players": {"guest": {"character_id": VIEWER, "downed": False},
                              "guest2": {"character_id": GUEST, "downed": True}},
                  markers.KEY: {"epoch": EPOCH, "map": CROSSROADS, "markers": {GUEST: [10, 20, 30]}}}
        client = SimpleNamespace(bridge=bridge, gameplay=public, gameplay_seen=20, zone_epoch=EPOCH,
                                 invite={"character_id": VIEWER}, expected_zone=CROSSROADS,
                                 last_game={"map": CROSSROADS}, transport=SimpleNamespace(game_access=True))
        with patch.object(markers.time, "monotonic", return_value=20):
            await view.sync(client)
            await view.sync(client)
        bridge.command.assert_awaited_once_with(markers.COMMAND, EPOCH, CROSSROADS, GUEST, "10", "20", "30", timeout=2)
        client.transport.game_access = False
        with patch.object(markers.time, "monotonic", return_value=21):
            await view.sync(client)
        self.assertEqual(bridge.command.await_count, 1)
        client.transport.game_access = True
        public["players"]["guest2"]["downed"] = False
        with patch.object(markers.time, "monotonic", return_value=21):
            await view.sync(client)
        self.assertEqual(bridge.command.await_count, 1)
        public[markers.KEY]["markers"] = {}
        with patch.object(markers.time, "monotonic", return_value=21):
            await view.sync(client)
        bridge.command.assert_awaited_with(markers.COMMAND, EPOCH, CROSSROADS, timeout=2)
