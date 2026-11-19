from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import uuid

from tools.silverfish.native_smg_hold import ACTION, KEY, GuestInput
from tools.silverfish.runtime import Bridge, MAP


PAWN = f"Player_FP_C {MAP}.PlaytestMap_01:PersistentLevel.Player_FP_C_1"
GUID = "A" * 32


class GuestHeldInputBridgeTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.bridge = Bridge(self.directory, Mock())

    def row(self, sequence, edge, stroke, *, pawn=PAWN, guid=GUID):
        return f"native-guest-held-fire-intent\t{sequence}\t{MAP}\t{pawn}\t{guid}\t{edge}\t{stroke}"

    def test_partial_terminal_and_ordered_exact_edges_are_consumed_once(self):
        path = self.directory / "native-guest-held-fire-events"
        path.write_text(self.row(1, "press", 1) + "\n" + self.row(2, "release", 1), encoding="utf-8")
        self.assertEqual(self.bridge.guest_held_fire_requests(), [(1, MAP, PAWN, GUID, "press", 1)])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n" + self.row(1, "press", 1) + "\n" + self.row(3, "cancel", 3) + "\n")
            stream.write(self.row(3, "press", 3, pawn="invalid") + "\n")
            stream.write(self.row(3, "press", 3) + "\n" + self.row(4, "cancel", 3) + "\n")
        self.assertEqual(self.bridge.guest_held_fire_requests(), [
            (2, MAP, PAWN, GUID, "release", 1), (3, MAP, PAWN, GUID, "press", 3), (4, MAP, PAWN, GUID, "cancel", 3),
        ])
        self.assertEqual(self.bridge.guest_held_fire_requests(), [])
        self.assertEqual(self.bridge.native_primary_input_requests(), [])

    def test_snapshot_accepts_only_active_exact_standard_smg_scope(self):
        status = self.directory / "status.tsv"
        base = f"schema\t1\nsession\tclient\tconnected\t{MAP}\t0\t1\n"
        scope = f"native-guest-primary-scope\t{MAP}\t{PAWN}\t{GUID}\t"
        held = f"native-guest-held-fire\t{MAP}\t{PAWN}\t{GUID}\t15\theld\theadless-fixture\n"
        fixture = f"native-guest-held-fire-fixture\t{MAP}\t{PAWN}\t{GUID}\t15\t15\tpress\n"
        status.write_text(base + scope + "smg-standard\tstock_held\n" + held + fixture, encoding="utf-8")
        report = self.bridge.snapshot()
        self.assertEqual(report["native_guest_held_fire"], {
            "map": MAP, "pawn": PAWN, "guid": GUID, "stroke": 15, "state": "held", "proof": "headless-fixture",
        })
        self.assertEqual(report["native_guest_held_fire_fixture"]["sequence"], 15)
        for other in ("smg", "light-pistol"):
            status.write_text(base + scope + other + "\tstock_held\n" + held + fixture, encoding="utf-8")
            self.assertNotIn("native_guest_held_fire", self.bridge.snapshot())
            self.assertNotIn("native_guest_held_fire_fixture", self.bridge.snapshot())

    def test_snapshot_allows_completed_fixture_with_no_held_state(self):
        status = self.directory / "status.tsv"
        status.write_text(
            f"schema\t1\nsession\tclient\tconnected\t{MAP}\t0\t1\n"
            f"native-guest-primary-scope\t{MAP}\t{PAWN}\t{GUID}\tsmg-standard\tstock_held\n"
            f"native-guest-held-fire-fixture\t{MAP}\t{PAWN}\t{GUID}\t40\t38\trelease\n",
            encoding="utf-8",
        )
        snapshot = self.bridge.snapshot()
        self.assertNotIn("native_guest_held_fire", snapshot)
        self.assertEqual(snapshot["native_guest_held_fire_fixture"]["stroke"], 38)
        self.assertEqual(snapshot["native_guest_held_fire_fixture"]["edge"], "release")


class GuestHeldInputRoutingTests(unittest.IsolatedAsyncioTestCase):
    def guest(self, *batches):
        client = SimpleNamespace(
            bridge=Mock(input_namespace=uuid.uuid4(), guest_held_fire_requests=Mock(side_effect=batches)),
            control=Mock(), gameplay_action=AsyncMock(return_value={KEY: {"status": "active"}}),
            stopping=False, host_session="c" * 32, zone_epoch="d" * 32, viewer_role="guest",
            invite={"character_id": "character"}, gameplay_seen=0,
            gameplay={
                "players": {"guest": {"character_id": "character", "downed": False}},
                "native_equipped_weapon": {"kind": "smg-standard", "map": MAP, "guid": GUID},
                "wipe": None,
            },
            last_game={"state": "connected", "map": MAP, "pawns": {"local": {"name": PAWN}}},
        )
        return client, GuestInput(client)

    async def test_qualified_press_before_heartbeat_and_ordered_release_use_same_token(self):
        client, controller = self.guest([(1, MAP, PAWN, GUID, "press", 1)], [(2, MAP, PAWN, GUID, "release", 1)])
        await controller.service(True)
        await controller.service(True)
        calls = [request.args for request in client.gameplay_action.await_args_list]
        self.assertEqual([action for action, _, _ in calls], [ACTION, ACTION])
        first, second = (target.split("@") for _, _, target in calls)
        self.assertEqual(first[:3], second[:3])
        self.assertEqual([first[3], second[3]], ["press", "release"])
        self.assertIsNone(controller.stroke)

    async def test_press_and_release_in_one_frame_is_one_stock_tap(self):
        client, controller = self.guest([(1, MAP, PAWN, GUID, "press", 1), (2, MAP, PAWN, GUID, "release", 1)])
        await controller.service(True)
        client.gameplay_action.assert_awaited_once()
        action, _, target = client.gameplay_action.await_args.args
        self.assertEqual((action, target), ("native-guest-weapon-fire", GUID))
