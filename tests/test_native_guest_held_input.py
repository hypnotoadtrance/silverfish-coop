import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

from tools.silverfish.native_smg_hold import ACTION, AIM_ACTION, ALL, KEY, Authority, GuestInput
from tools.silverfish.runtime import Bridge, MAP


PAWN = f"Player_FP_C {MAP}.PlaytestMap_01:PersistentLevel.Player_FP_C_1"
GUID = "A" * 32
VIEW = "1.000000,0.000000,0.000000"
TURNED = "0.000000,1.000000,0.000000"


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

    def tap(self, sequence):
        return f"native-guest-primary-intent\t{sequence}\t{MAP}\t{PAWN}\t{GUID}\trusty-pistol"

    def test_legacy_primary_does_not_consume_an_incomplete_record(self):
        path = self.directory / "native-guest-primary-events"
        path.write_text(self.tap(1) + "\n" + self.tap(2), encoding="utf-8")
        self.assertEqual(self.bridge.native_primary_input_requests(), [(1, MAP, PAWN, GUID, "rusty-pistol")])
        self.assertEqual(self.bridge.native_primary_input_requests(), [])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n")
        self.assertEqual(self.bridge.native_primary_input_requests(), [(2, MAP, PAWN, GUID, "rusty-pistol")])
        self.assertEqual(self.bridge.native_primary_input_requests(), [])

    def test_memory_routes_exact_ordered_rows_and_ignores_old_file_intents(self):
        (self.directory / "native-guest-primary-events").write_text(self.tap(95) + "\n", encoding="utf-8")
        (self.directory / "native-guest-held-fire-events").write_text(self.row(96, "press", 96) + "\n", encoding="utf-8")
        batch = [self.tap(8), self.row(1, "press", 1), self.tap(9), self.row(2, "release", 1)]
        self.bridge.local_bridge = Mock(input_ready=Mock(side_effect=[True, False, False, False]),
                                      read_input=Mock(return_value=[row.encode() for row in batch]))
        self.assertEqual(self.bridge.guest_held_fire_requests(), [(1, MAP, PAWN, GUID, "press", 1),
                                                                  (2, MAP, PAWN, GUID, "release", 1)])
        self.assertEqual(self.bridge.native_primary_input_requests(), [(8, MAP, PAWN, GUID, "rusty-pistol"),
                                                                        (9, MAP, PAWN, GUID, "rusty-pistol")])
        self.assertEqual(self.bridge.guest_held_fire_requests(), [])
        self.assertEqual(self.bridge.native_primary_input_requests(), [])
        self.bridge.local_bridge.read_input.assert_called_once_with(64)

    def test_memory_leaves_native_rows_queued_while_a_collector_is_full(self):
        self.bridge._local_held_fire_records.extend(self.row(seq, "press", seq).encode() for seq in range(1, 65))
        self.bridge.local_bridge = Mock(input_ready=Mock(return_value=True),
                                      read_input=Mock(return_value=[self.tap(8).encode()]))
        self.assertEqual(self.bridge.native_primary_input_requests(), [])
        self.bridge.local_bridge.read_input.assert_not_called()
        self.assertEqual(self.bridge.guest_held_fire_requests(), [(seq, MAP, PAWN, GUID, "press", seq) for seq in range(1, 65)])
        self.assertEqual(self.bridge.native_primary_input_requests(), [(8, MAP, PAWN, GUID, "rusty-pistol")])
        self.bridge.local_bridge.read_input.assert_called_once_with(64)

    def test_memory_preserves_trigger_edges_and_keeps_only_the_latest_aim(self):
        aim = f"native-guest-held-aim\t{MAP}\t{PAWN}\t{GUID}\t1\t"
        contact_view = f"{TURNED};0.000,0.000,0.000;0.000,400.000,0.000;0.000,1.000,0.000"
        contact_view += f"|{MAP}.PlaytestMap_01:PersistentLevel.NPC_1|0.000,398.000,0.000"
        rows = [self.row(1, "press", 1) + "\t" + VIEW, aim + VIEW, aim + contact_view,
                aim + contact_view.replace(":PersistentLevel.", ":BadLevel."), self.row(2, "release", 1)]
        self.bridge.local_bridge = Mock(read_input=Mock(return_value=[row.encode() for row in rows]))
        self.assertEqual(self.bridge.guest_held_fire_requests(), [
            (1, MAP, PAWN, GUID, "press", 1, VIEW), (2, MAP, PAWN, GUID, "release", 1),
        ])
        self.assertEqual(self.bridge.take_guest_held_aim(), (MAP, PAWN, GUID, 1, contact_view))
        self.assertIsNone(self.bridge.take_guest_held_aim())

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

    def test_publication_receipts_are_scoped_to_the_current_weapon(self):
        status = self.directory / "status.tsv"
        base = f"schema\t1\nsession\tclient\tconnected\t{MAP}\t0\t1\n"
        scope = f"native-guest-primary-scope\t{MAP}\t{PAWN}\t{GUID}\t"
        row = f"native-guest-primary-publication\t{MAP}\t{PAWN}\t{GUID}\t"
        fixture = f"native-guest-held-fire-fixture\t{MAP}\t{PAWN}\t{GUID}\t5\t4\trelease\n"
        for kind, lane, automatic in (("smg-standard", "memory", True), ("light-ar-01", "memory", True),
                                      ("rusty-pistol", "file", False)):
            with self.subTest(kind=kind, lane=lane):
                status.write_text(base + scope + kind + "\tstock_held\n" + row + f"tap\t4\t{lane}\n"
                                  + row + f"held\t5\t{lane}\n" + fixture, encoding="utf-8")
                snapshot = self.bridge.snapshot()
                result = snapshot["native_guest_primary_publication"]
                self.assertEqual(result["tap"], dict(map=MAP, pawn=PAWN, guid=GUID, sequence=4, lane=lane))
                self.assertEqual("held" in result, automatic)
                self.assertEqual("native_guest_held_fire_fixture" in snapshot, automatic)
        other = "B" * 32
        scope = scope.replace(GUID, other)
        status.write_text(base + scope + "smg-standard\tstock_held\n" + row + "held\t5\tmemory\n", encoding="utf-8")
        self.assertNotIn("native_guest_primary_publication", self.bridge.snapshot())


class HeldFireAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_network_reply_keeps_the_host_trigger_receipt(self):
        epoch, token = "d" * 32, "e" * 32
        member = {"connected": True, "pawn": PAWN}
        host = SimpleNamespace(
            authenticated_guest=lambda _: ("guest-id", "guest-character"),
            native_recipients=lambda: {"guest-character": member},
            stopping=False, transitioning=False, native_checkpoint_unsafe=False, session_stop_reason=None,
            zone_epoch=epoch, allowed_maps={MAP}, party={"wipe": None, "players": {}},
            last_game={"state": "hosting", "map": MAP,
                       "native_guest_equipped_weapons": {PAWN: {"guid": GUID, "kind": "light-ar-01"}}},
            control=Mock(), checkpoint_native=AsyncMock(),
        )
        authority = Authority(host)
        started = asyncio.Event()
        acknowledge = asyncio.Event()

        async def held_fire_command(operation, *args):
            if operation == "start":
                started.set()
                await acknowledge.wait()
            else:
                self.assertEqual((operation, args), ("stop", ("1",)))
            proof = {"map": MAP, "pawn": PAWN, "serial": 1, "guid": GUID, "rounds": 1,
                     "projectiles": 1, "before": 3, "after": 2,
                     "status": "active" if operation == "start" else "released"}
            return {"map": MAP, ALL: {PAWN: proof}}

        authority.bridge = SimpleNamespace(held_fire_command=AsyncMock(side_effect=held_fire_command))
        target = "@".join((epoch, GUID, token, "press"))
        with patch("tools.silverfish.native_smg_hold.native_inventory.retain_guest_save",
                   new_callable=AsyncMock, return_value=True):
            request = asyncio.create_task(authority.authenticated("guest-id", target))
            async with asyncio.timeout(2):
                await started.wait()
            edge = authority.edge_task
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertFalse(host.stopping)
            self.assertTrue(authority.lock.locked())
            member["connected"] = False
            acknowledge.set()
            self.assertTrue((await edge)["ok"])

        await authority.settle(authority.bridge)
        self.assertEqual([call.args[0] for call in authority.bridge.held_fire_command.await_args_list], ["start", "stop"])
        self.assertEqual(authority.strokes, {})
        self.assertFalse(host.stopping)
        self.assertFalse(host.native_checkpoint_unsafe)
        host.checkpoint_native.assert_not_awaited()


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
        client, controller = self.guest([(1, MAP, PAWN, GUID, "press", 1, VIEW), (2, MAP, PAWN, GUID, "release", 1)])
        await controller.service(True)
        client.gameplay_action.assert_awaited_once()
        action, _, target = client.gameplay_action.await_args.args
        self.assertEqual((action, target), ("native-guest-weapon-fire", f"{GUID}@{client.zone_epoch}@{VIEW}"))

    async def test_release_does_not_wait_for_a_pending_aim_reply(self):
        client, controller = self.guest([(1, MAP, PAWN, GUID, "press", 1, VIEW)], [], [(2, MAP, PAWN, GUID, "release", 1)])
        waiting = asyncio.Event()

        async def aim(request, *, timeout):
            self.assertEqual(request["action"], AIM_ACTION)
            self.assertTrue(request["target"].endswith("@" + TURNED))
            await waiting.wait()
            return {"ok": True}

        client.transport = SimpleNamespace(request=aim)
        client.bridge.take_guest_held_aim.return_value = (MAP, PAWN, GUID, 1, TURNED)
        client.last_game["native_guest_held_fire"] = dict(state="held", map=MAP, pawn=PAWN, guid=GUID, stroke=1)
        await controller.service(True)
        await controller.service(True)
        try:
            await asyncio.sleep(0)
            self.assertFalse(controller.aim_task.done())
            await controller.service(True)
            self.assertTrue(client.gameplay_action.await_args.args[-1].endswith("@release"))
            self.assertIsNone(controller.stroke)
        finally:
            waiting.set()
            await controller.aim_task

    async def test_authority_lane_ignores_stale_ack_and_leaves_campaign_command(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bridge = Bridge(directory, Mock(poll=Mock(return_value=None)))
            ordinary, command, reply = (directory / name for name in (
                "command.tsv", "held-fire-command.tsv", "held-fire-reply.tsv",
            ))
            ordinary.write_text("checkpoint\n", encoding="utf-8")
            reply.write_text(f"held-fire-ack\t{'a' * 32}\taccepted\t{MAP}\tstale\n", encoding="utf-8")
            pending = asyncio.create_task(bridge.held_fire_command("stop", "1"))
            await asyncio.sleep(0)
            nonce, action, serial = command.read_text(encoding="utf-8").strip().split("\t")
            self.assertEqual((action, serial), ("native-guest-smg-hold-stop", "1"))
            self.assertFalse(pending.done())
            reply.write_text(f"held-fire-ack\t{nonce}\taccepted\t{MAP}\tverified\n"
                             f"native-guest-smg-hold\t{MAP}\t{PAWN}\t{GUID}\t1\treleased\t5\t3\t2\t2\n",
                             encoding="utf-8")
            self.assertEqual((await pending)[ALL][PAWN]["projectiles"], 2)
            self.assertFalse(command.exists())
            self.assertEqual(ordinary.read_text(encoding="utf-8"), "checkpoint\n")

    async def test_resident_authority_uses_matched_compact_receipt_without_mailboxes(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bridge = Bridge(directory, Mock())

            async def command(payload, timeout):
                nonce, action, serial = payload.decode().strip().split("\t")
                self.assertEqual((action, serial, timeout), ("native-guest-smg-hold-stop", "1", 1.2))
                return (f"held-fire-ack\t{nonce}\taccepted\t{MAP}\tverified\n"
                        f"native-guest-smg-hold\t{MAP}\t{PAWN}\t{GUID}\t1\treleased\t5\t3\t2\t2\n")

            bridge.local_bridge = SimpleNamespace(command=command)
            self.assertEqual((await bridge.held_fire_command("stop", "1"))[ALL][PAWN]["projectiles"], 2)
            self.assertEqual(list(directory.iterdir()), [])
            bridge.local_bridge.command = AsyncMock(return_value=f"held-fire-ack\t{'a' * 32}\taccepted\t{MAP}\twrong\n")
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                await bridge.held_fire_command("stop", "1")
