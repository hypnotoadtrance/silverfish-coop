import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_prop_carry as carry, party, runtime


class CarrierPoseTests(unittest.TestCase):
    def test_carrier_targets_reach_the_game_thread_with_a_stale_pawn_snapshot(self):
        epoch, lease, character = "a" * 32, "b" * 32, "c" * 32
        service = SimpleNamespace(zone_epoch=epoch, last_game={"pawns": {"guest": {
            "name": "guest pawn", "location": [0, 0, 0],
        }}})
        host = carry.Host(service)
        host._reset()
        host.holders[character] = {"lease": lease, "pawn": "guest pawn", "sample": 0, "deadline": 3}

        with (TemporaryDirectory() as temporary, patch.object(host, "_pawn", return_value="guest pawn"),
              patch.object(carry.time, "monotonic", return_value=2)):
            bridge = SimpleNamespace(directory=Path(temporary), local_bridge=None)
            target = f"{epoch}@{lease}@1@400@0@0@0@0@0"
            self.assertEqual(host.aim(bridge, character, target), {"ok": True})
            queued = (bridge.directory / "native-prop-carry-poses").read_text(encoding="utf-8")
            self.assertEqual(queued.rstrip().split("\t")[:3], [lease, "1", "400.0"])

    def test_embedded_worker_publishes_latest_poses_without_creating_a_file(self):
        epoch, lease, character = "a" * 32, "b" * 32, "c" * 32
        service = SimpleNamespace(zone_epoch=epoch, last_game={})
        host = carry.Host(service)
        host._reset()
        host.holders[character] = {"lease": lease, "pawn": "guest pawn", "sample": 0, "deadline": 3}

        with (TemporaryDirectory() as temporary, patch.object(host, "_pawn", return_value="guest pawn"),
              patch.object(carry.time, "monotonic", return_value=2)):
            local_bridge = Mock()
            local_bridge.publish_prop_poses.side_effect = (True, True, False)
            bridge = SimpleNamespace(directory=Path(temporary), local_bridge=local_bridge)
            for sample in (1, 2):
                self.assertEqual(host.aim(bridge, character, f"{epoch}@{lease}@{sample}@400@0@0@0@0@0"),
                                 {"ok": True})
            frame = local_bridge.publish_prop_poses.call_args.args[0]
            self.assertEqual(frame.splitlines()[0].split("\t")[:3], [lease, "2", "400.0"])
            with self.assertRaisesRegex(RuntimeError, "could not queue the local prop poses"):
                host.aim(bridge, character, f"{epoch}@{lease}@3@400@0@0@0@0@0")
            self.assertEqual(host.holders[character]["sample"], 2)
            self.assertEqual(list(bridge.directory.iterdir()), [])

    def test_unordered_datagrams_apply_only_newer_poses_for_the_current_open_lease(self):
        epoch, lease, character = "a" * 32, "b" * 32, "c" * 32
        position = (400.1, -39.3, 2300.7, 11.4, -163.1, 0.0)
        service = SimpleNamespace(zone_epoch=epoch, last_game={})
        host = carry.Host(service)
        host._reset()
        host.holders[character] = {"lease": lease, "pawn": "guest pawn", "sample": 0, "deadline": 3}
        bridge = SimpleNamespace(local_bridge=Mock())
        bridge.local_bridge.publish_prop_poses.return_value = True

        def apply(number, *, scope=epoch, holder=lease):
            data = carry.pack_pose(scope, holder, number, position)
            self.assertEqual(len(data), 58)
            self.assertEqual(carry.unpack_pose(data), (scope, holder, number, position))
            host.apply_pose(bridge, character, *carry.unpack_pose(data))

        with (patch.object(host, "_pawn", return_value="guest pawn"),
              patch.object(carry.time, "monotonic", return_value=2)):
            apply(1)
            apply(3)
            for stale in (2, 3):
                with self.assertRaisesRegex(party.RuleError, "stale_pose"):
                    apply(stale)
            with self.assertRaisesRegex(party.RuleError, "wrong_holder"):
                apply(4, holder="d" * 32)
            host.holders[character]["closing"] = True
            with self.assertRaisesRegex(party.RuleError, "wrong_holder"):
                apply(4)
            service.zone_epoch = "e" * 32
            with self.assertRaisesRegex(party.RuleError, "invalid_pose"):
                apply(5)

        frames = bridge.local_bridge.publish_prop_poses
        self.assertEqual(frames.call_count, 2)
        self.assertEqual(frames.call_args.args[0].split("\t")[:3], [lease, "3", "400.1"])
        with self.assertRaisesRegex(ValueError, "frame length"):
            carry.unpack_pose(b"short")


class CorpseCarryPayloadTests(unittest.TestCase):
    def setUp(self):
        self.area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        self.edge = ["native-prop-carry-input", self.area, "1", "guest", "grab",
                     "/Game/Classes/NPCs/NPC_WildBoar.NPC_WildBoar_C", "10", "20", "30", "-40", "10"]

    def test_stock_prop_and_exact_corpse_inputs_share_the_lease_format(self):
        self.assertNotIn("corpse", carry.parse(self.edge, "connected", self.area))
        result = carry.parse([*self.edge, "65535", "leg_f_R"], "connected", self.area)
        self.assertEqual((result["corpse"], result["bone"], result["pose"][0]), (65535, "leg_f_R", 10))
        receipt = [carry.ACTION, self.area, "a" * 32, "2", "drop", "guest", "boar", "65535", "leg_f_R"]
        result = carry.parse(receipt, "hosting", self.area)
        self.assertEqual((result["corpse"], result["bone"], result["edge"]), (65535, "leg_f_R", "drop"))

    def test_corpse_input_requires_a_numbered_marker_and_named_body(self):
        for marker, bone in (("0", "pelvis"), ("65536", "pelvis"), ("1", "None"), ("1", "pelvis@extra")):
            with self.subTest(marker=marker, bone=bone), self.assertRaises(ValueError):
                carry.parse([*self.edge, marker, bone], "connected", self.area)
        self.edge[4] = "drop"
        with self.assertRaises(ValueError):
            carry.parse([*self.edge, "1", "pelvis"], "connected", self.area)

    def test_authored_fuel_drums_accept_the_cooked_instance_name(self):
        actor = f"Prop_FuelDrum_C {self.area}.CrossRoads_P:PersistentLevel.Prop_FeulDrum_C_33"
        self.assertEqual(carry.parse([carry.BARREL_ROW, self.area, actor, "31"], "hosting", self.area)["actor"], actor)
        with self.assertRaises(ValueError):
            carry.parse([carry.BARREL_ROW, self.area, actor.replace("_33", "_2147482112"), "31"], "hosting", self.area)

    def test_placed_grabs_are_bound_to_an_actor_in_the_current_map(self):
        path = f"{self.area}.CrossRoads_P:PersistentLevel.Item_CannedSoup_C_1"
        fields = [*self.edge, path]
        fields[5] = "/Game/Classes/Items/Item_CannedSoup.Item_CannedSoup_C"
        self.assertEqual(carry.parse(fields, "connected", self.area)["placement"], path)
        with self.assertRaises(ValueError):
            carry.parse([*fields[:-1], path.replace("CrossRoads_P", "Observitory_P")], "connected", self.area)

    def test_cooked_ammo_kits_keep_their_hyphenated_class_and_placement(self):
        area = "/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P"
        path = f"{area}.FortLeto_01_P:PersistentLevel.AmmoKit_5-56Inter_C_3"
        actor = f"AmmoKit_5-56Inter_C {path}"
        item_class = "/Game/Classes/Items/AmmoKit_5-56Inter.AmmoKit_5-56Inter_C"
        row = [carry.PLACED_PROP_ROW, area, actor, item_class, "13.116963386536"]
        self.assertEqual(carry.parse(row, "hosting", area)["actor"], actor)
        request = [self.edge[0], area, *self.edge[2:5], item_class, *self.edge[6:], path]
        self.assertEqual(carry.parse(request, "connected", area)["placement"], path)


class ReleaseHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_refused_dynamic_prop_grab_clears_the_predicted_local_grip(self):
        bridge = SimpleNamespace(command=AsyncMock(), input_namespace=uuid.NAMESPACE_URL)
        gameplay_action = AsyncMock(side_effect=RuntimeError("native_prop_carry_stock_target_unavailable"))
        guest = carry.Guest(SimpleNamespace(bridge=bridge, gameplay_action=gameplay_action, control=Mock()))
        guest.epoch = "a" * 32
        await guest._operate({
            "edge": "grab", "sequence": 3, "pose": (100, 200, 300, 10, 20),
            "class_path": "/Game/Assets/StaticMeshes/Mesh_Props/Prop_ScrapWood.Prop_ScrapWood_C",
        })
        bridge.command.assert_awaited_once_with("native-prop-carry-refuse", "3", timeout=2)
        self.assertIsNone(guest.lease)
        self.assertFalse(guest.local_pose_ready.is_set())

    async def test_reliable_release_keeps_its_solved_pose_when_transient_sample_is_missing(self):
        epoch, lease, character = "a" * 32, "b" * 32, "c" * 32
        area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        actor = f"Prop_TypeWriter_C {area}.CrossRoads_P:PersistentLevel.Prop_TypeWriter_C_3"
        pose = (820.1, -280, 300, 0, -20, 4, 120, -130, -20, 300, 0, -20)
        raw_pose = list(map(str, pose))
        local_edge = ["native-prop-carry-input", area, "4", "guest pawn", "throw",
                      "/Game/Assets/Prop_TypeWriter.Prop_TypeWriter_C", "800", "-300", "250", "10", "-50"]
        release = carry.parse([*local_edge, *raw_pose], "connected", area)
        self.assertEqual(release["release_pose"], pose)
        with self.assertRaisesRegex(ValueError, "coordinates"):
            carry.parse([*local_edge, *raw_pose[:-1], "nan"], "connected", area)
        receipt_row = [carry.ACTION, area, lease, "4", "throw", "guest pawn", actor]
        proof = carry.parse([*receipt_row, "release-pose"], "hosting", area)
        self.assertTrue(proof["release_pose_applied"])

        service = SimpleNamespace(zone_epoch=epoch, last_game={"map": area}, control=Mock())
        host = carry.Host(service)
        host._reset()
        holder = {"lease": lease, "pawn": "guest pawn", "sample": 0, "sequence": 2,
                  "deadline": 50, "identity": {"placement": actor.split(" ", 1)[1]}}
        host.holders[character] = holder

        async def execute(*args, **kwargs):
            self.assertTrue(holder["closing"])
            self.assertEqual(args[:7], (carry.ACTION, lease, "guest pawn", "throw", "4", "10.0", "-50.0"))
            self.assertEqual(tuple(map(float, args[7:])), pose)
            return {carry.PROOF: proof}

        bridge = SimpleNamespace(command=AsyncMock(side_effect=execute), input_namespace=uuid.NAMESPACE_URL)
        service.bridge = bridge
        service.gameplay = {carry.RELEASE_POSE: True}

        async def gameplay_action(action, operation, target):
            self.assertEqual(action, carry.ACTION)
            return await host.operate(bridge, character, target)

        service.gameplay_action = gameplay_action
        guest = carry.Guest(service)
        guest.epoch, guest.lease, guest.grab = epoch, lease, 2
        with patch.object(host, "_pawn", return_value="guest pawn"):
            await guest._operate(release)
        bridge.command.assert_awaited_once()
        self.assertIsNone(guest.lease)
        self.assertNotIn(character, host.holders)

    async def test_guest_uses_the_stock_release_format_with_an_older_host(self):
        epoch, lease = "a" * 32, "b" * 32
        service = SimpleNamespace(bridge=SimpleNamespace(input_namespace=uuid.NAMESPACE_URL),
                                  gameplay={}, gameplay_action=AsyncMock(return_value={carry.PROOF: {"sequence": 2}}))
        guest = carry.Guest(service)
        guest.epoch, guest.lease, guest.grab = epoch, lease, 1
        await guest._operate({"edge": "drop", "sequence": 2, "pose": (0, 0, 0, 0, 0),
                              "release_pose": (1,) * 12})
        self.assertEqual(service.gameplay_action.await_args.args[2], f"{epoch}@{lease}@drop@2@0@0")


class GuestPoseTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_carry_edges_dispatch_before_snapshots_and_reject_stale_scope(self):
        area, epoch, pawn = "/Game/Classes/Maps/Test", "a" * 32, "local pawn"
        scope = ("host", epoch, area, pawn)
        bridge = runtime.Bridge(Path("unused"), None)
        bridge.local_bridge = Mock()
        stock_class = "/Game/Classes/Props/Prop_Test.Prop_Test_C"

        def record(number, edge, *, scene=area, holder=pawn):
            return f"native-prop-carry-input\t{scene}\t{number}\t{holder}\t{edge}\t{stock_class}\t1\t2\t3\t0\t0\n".encode()

        grab, release = record(1, "grab"), record(2, "throw")
        bridge.local_bridge.read_input.side_effect = [
            [record(8, "grab", scene=area + "_old"), record(9, "grab", holder="another pawn"), grab, release],
            [record(3, "grab")], [], [],
        ]
        game = {"state": "connected", "map": area, "pawns": {"local": {"name": pawn}}}
        service = SimpleNamespace(bridge=bridge, last_game=game, zone_epoch=epoch,
                                  transport=SimpleNamespace(game_access=True))
        guest = carry.Guest(service)
        guest._operate = AsyncMock()
        guest.poll(True)
        guest.poll_local_edges(scope)
        await guest.action
        self.assertEqual(guest._operate.await_args.args[0]["edge"], "grab")

        guest.poll_local_edges(("host", "b" * 32, area, pawn))
        game[carry.INPUT] = [carry.parse(line.decode().strip().split("\t"), "connected", area)
                             for line in (grab, release)]
        guest.poll(True)
        await guest.action
        guest.poll_local_edges(scope)
        self.assertEqual([call.args[0]["edge"] for call in guest._operate.await_args_list], ["grab", "throw"])
        self.assertTrue(all(call.args[0]["input_source"] == "local_bridge" for call in guest._operate.await_args_list))

    async def test_grab_receipt_arms_before_a_periodic_snapshot_and_accounts_for_delay(self):
        epoch = "a" * 32
        area = "/Game/Test"
        placement = f"{area}.Test:PersistentLevel.Prop_ScrapWood_C_7"
        actor = f"Prop_ScrapWood_C {placement}"
        pawn = "guest pawn"
        clock = Mock(return_value=2.0)
        authority = SimpleNamespace(zone_epoch=epoch, last_game={"map": area}, control=Mock())
        host = carry.Host(authority)

        async def execute(_action, lease, reported_pawn, edge, number, *_target, timeout):
            self.assertEqual((reported_pawn, edge, number), (pawn, "grab", "4"))
            proof = {"lease": lease, "sequence": 4, "edge": edge, "actor": actor, "pawn": pawn}
            held = {"serial": 4, "pawn": pawn, "actor": actor, "remaining": 900}
            return {"map": area, "sequence": 20, carry.PROOF: proof, carry.HELD: {lease: held}}

        authority_bridge = SimpleNamespace(command=AsyncMock(side_effect=execute))
        delay = .12

        async def gameplay_action(action, _operation, target):
            self.assertEqual(action, carry.ACTION)
            receipt = await host.operate(authority_bridge, "guest", target)
            clock.return_value += delay
            return receipt

        bridge = SimpleNamespace(command=AsyncMock(), input_namespace=uuid.NAMESPACE_URL, local_bridge=object())
        game = {carry.FILTER_READY: True}
        service = SimpleNamespace(bridge=bridge, last_game=game, gameplay_action=gameplay_action, gameplay={})
        request = {"edge": "grab", "sequence": 4, "pose": (400, 0, 100, 20, 0), "placement": placement,
                   "class_path": "/Game/Assets/Prop_ScrapWood.Prop_ScrapWood_C"}

        with (patch.object(carry, "time", SimpleNamespace(monotonic=clock)),
              patch.object(host, "_pawn", return_value=pawn)):
            guest = carry.Guest(service)
            guest.epoch = epoch
            await guest._operate(request)
            self.assertIsNotNone(guest.filter_job)
            await guest.filter_job
            call = bridge.command.await_args.args
            self.assertEqual(call[:4], ("native-prop-carry-arm-filter", guest.lease, "4", actor))
            self.assertIn(int(call[4]), (779, 780))
            self.assertEqual(guest.filter_version, 20)
            self.assertTrue(authority.control.event.call_args.kwargs["filter_confirmed"])

            bridge.command.reset_mock()
            host.holders.clear()
            delay = .8
            guest = carry.Guest(service)
            guest.epoch = epoch
            await guest._operate(request)
            self.assertIsNone(guest.filter_job)
            self.assertEqual(guest.filter_version, 0)
            bridge.command.assert_not_awaited()

    async def test_local_samples_can_move_the_current_grip_between_status_frames(self):
        epoch, lease = "a" * 32, "b" * 32
        transport = SimpleNamespace(game_access=True, send_prop_pose=Mock(return_value=True))
        game = {"state": "connected", "map": "/Game/Test", "pawns": {"local": {"name": "local pawn"}}}
        service = SimpleNamespace(zone_epoch=epoch, expected_zone="/Game/Test",
                                  needs_travel=False, server_transitioning=False,
                                  gameplay={carry.POSE_DATAGRAM: True}, transport=transport, last_game=game)
        guest = carry.Guest(service)
        guest.epoch, guest.lease, guest.grab = epoch, lease, 4
        guest._forward_local_pose(b"unowned pawn\t4\t400\t100\t80\t0\t45\t0", 2)
        guest._forward_local_pose(b"local pawn\t3\t400\t100\t80\t0\t45\t0", 2)
        guest._forward_local_pose(b"local pawn\t4\t400\t100\tinf\t0\t45\t0", 2)
        transport.send_prop_pose.assert_not_called()

        guest._forward_local_pose(b"local pawn\t4\t400\t100\t80\t0\t45\t0", 2)
        guest._forward_local_pose(b"local pawn\t4\t410\t100\t80\t0\t47\t0", 2.02)
        guest._forward_local_pose(b"local pawn\t4\t425\t100\t80\t0\t50\t0", 2.06)
        self.assertEqual([carry.unpack_pose(call.args[0]) for call in transport.send_prop_pose.call_args_list], [
            (epoch, lease, 1, (400, 100, 80, 0, 45, 0)),
            (epoch, lease, 2, (425, 100, 80, 0, 50, 0)),
        ])

        guest._forward_local_pose(b"local pawn\t4\t425\t100\t80\t0\t50\t0", 2.12)
        service.needs_travel = True
        guest._forward_local_pose(b"local pawn\t4\t440\t100\t80\t0\t55\t0", 2.26)
        service.needs_travel = False
        service.zone_epoch = "c" * 32
        guest._forward_local_pose(b"local pawn\t4\t440\t100\t80\t0\t55\t0", 2.32)
        self.assertEqual(transport.send_prop_pose.call_count, 2)

    async def test_uses_datagrams_only_after_the_host_advertises_support(self):
        epoch, lease = "a" * 32, "b" * 32
        position = (400.1, -39.3, 2300.7, 11.4, -163.1, 0.0)
        transport = SimpleNamespace(send_prop_pose=Mock(return_value=True), request=AsyncMock(return_value={"ok": True}))
        service = SimpleNamespace(zone_epoch=epoch, gameplay={}, transport=transport, last_game={
            "state": "connected", carry.TARGET: {"grab": 1, "pose": position},
        })
        guest = carry.Guest(service)
        guest.epoch, guest.lease, guest.grab = epoch, lease, 1

        with patch.object(carry, "time", SimpleNamespace(monotonic=Mock(side_effect=(2, 3)))):
            guest.poll(True)
            await guest.pose
            reliable = transport.request.await_args.args[0]
            self.assertEqual(reliable["action"], carry.AIM)
            self.assertEqual(reliable["target"].split("@")[:3], [epoch, lease, "1"])
            transport.send_prop_pose.assert_not_called()

            service.gameplay[carry.POSE_DATAGRAM] = True
            guest.poll(True)
            packed = transport.send_prop_pose.call_args.args[0]
            self.assertEqual(carry.unpack_pose(packed), (epoch, lease, 2, position))
            transport.request.assert_awaited_once()

    async def test_active_filter_requires_a_new_exact_host_grip_receipt(self):
        epoch, lease, character = "a" * 32, "b" * 32, "c" * 32
        actor = "Prop_TypeWriter_C /Game/Test.Test:PersistentLevel.Prop_TypeWriter_C_3"
        game = {"sequence": 10, carry.HELD: {lease: {
            "lease": lease, "serial": 4, "pawn": "guest pawn", "actor": actor, "remaining": 950,
        }}}
        authority = SimpleNamespace(zone_epoch=epoch, last_game=game, last_seen=2)
        host = carry.Host(authority)
        host._reset()
        holder = {"lease": lease, "pawn": "guest pawn", "sequence": 4, "deadline": 3.3,
                  "identity": {"placement": actor.split(" ", 1)[1]}, "actor": actor}
        host.holders[character] = holder
        service = SimpleNamespace(gameplay={}, gameplay_seen=2.05, last_game={},
                                  bridge=SimpleNamespace(command=AsyncMock(), local_bridge=object()))
        with patch.object(carry, "os", SimpleNamespace(getenv=Mock(return_value=None))):
            guest = carry.Guest(service)
        self.assertTrue(guest.filter_enabled)
        self.assertFalse(guest.filter_file_fixture)
        guest.epoch, guest.lease, guest.grab, guest.filter_actor = epoch, lease, 4, actor
        guest.filter_placed = True
        grip = {"grab": 4, "actor": actor}

        clock = Mock(return_value=2.1)
        with (patch.object(carry, "time", SimpleNamespace(monotonic=clock)),
              patch.object(host, "_pawn", return_value="guest pawn")):
            frame = host.grip_confirmation(character)
            self.assertIsNotNone(frame)
            service.gameplay[carry.FILTER] = frame
            guest._poll_filter(grip)
            self.assertIsNone(guest.filter_job)
            service.last_game[carry.FILTER_READY] = carry.parse(
                (carry.FILTER_READY_ROW, "/Game/Test"), "connected", "/Game/Test")
            service.bridge.local_bridge = None
            guest._poll_filter(grip)
            self.assertIsNone(guest.filter_job)
            service.bridge.local_bridge = object()
            service.gameplay[carry.FILTER] = dict(frame, actor=actor + "_wrong")
            guest._poll_filter(grip)
            self.assertIsNone(guest.filter_job)
            service.gameplay[carry.FILTER] = frame
            guest._poll_filter({**grip, "actor": actor + "_other"})
            self.assertIsNone(guest.filter_job)
            guest._poll_filter(grip)
            await guest.filter_job
            service.bridge.command.assert_awaited_once()
            self.assertEqual(service.bridge.command.await_args.args[:4],
                             ("native-prop-carry-arm-filter", lease, "4", actor))
            self.assertGreater(int(service.bridge.command.await_args.args[4]), 700)
            guest._poll_filter(grip)
            service.bridge.command.assert_awaited_once()
            clock.return_value = 2.6
            self.assertIsNone(host.grip_confirmation(character))
            clock.return_value = 2.1
            holder["closing"] = True
            self.assertIsNone(host.grip_confirmation(character))

    async def test_the_host_lease_arms_the_exact_guest_replica_of_a_dynamic_prop(self):
        epoch, character = "a" * 32, "c" * 32
        area = "/Game/Test"
        path = f"{area}.Test:PersistentLevel.Prop_ScrapWood_C_"
        authority_actor, local_actor = (f"Prop_ScrapWood_C {path}{index}" for index in (700, 80))
        source = ["native-prop-carry-target", area, "4", "400", "0", "100", "20", "0", "0"]
        grip = carry.parse([*source, local_actor], "connected", area)
        self.assertEqual(grip["actor"], local_actor)
        self.assertNotIn("actor", carry.parse(source, "connected", area))
        with self.assertRaises(ValueError):
            carry.parse([*source, local_actor.replace("Test", "PreviousMap")], "connected", area)

        async def gameplay_action(action, _operation, target):
            self.assertEqual(action, carry.ACTION)
            lease = target.split("@")[1]
            return {carry.PROOF: {"lease": lease, "sequence": 4, "edge": "grab", "actor": authority_actor}}

        bridge = SimpleNamespace(command=AsyncMock(), input_namespace=uuid.NAMESPACE_URL, local_bridge=object())
        service = SimpleNamespace(zone_epoch=epoch, last_seen=2, gameplay_seen=2.02, bridge=bridge,
                                  gameplay_action=gameplay_action, gameplay={}, last_game={carry.FILTER_READY: True})
        guest, host = carry.Guest(service), carry.Host(service)
        guest.epoch = epoch
        host._reset()
        await guest._operate({"edge": "grab", "sequence": 4, "pose": (400, 0, 100, 20, 0),
                              "class_path": "/Game/Assets/Prop_ScrapWood.Prop_ScrapWood_C"})
        lease = guest.lease
        host.holders[character] = {"actor": authority_actor, "deadline": 3.3, "lease": lease, "pawn": "guest",
                                   "sequence": 4, "identity": {}}
        service.last_game.update(sequence=10)
        held = {"serial": 4, "pawn": "guest", "actor": authority_actor, "remaining": 900}
        service.last_game[carry.HELD] = {lease: held}
        with (patch.object(carry, "time", SimpleNamespace(monotonic=Mock(return_value=2.1))),
              patch.object(host, "_pawn", return_value="guest")):
            held["actor"] = local_actor
            self.assertIsNone(host.grip_confirmation(character))
            held["actor"] = authority_actor
            service.gameplay[carry.FILTER] = host.grip_confirmation(character)
            guest._poll_filter({**grip, "grab": 3})
            self.assertIsNone(guest.filter_job)
            guest._poll_filter(grip)
            await guest.filter_job
        bridge.command.assert_awaited_once()
        self.assertEqual(bridge.command.await_args.args[:4], ("native-prop-carry-arm-filter", lease, "4", local_actor))

    def test_stock_filter_can_be_disabled_on_both_seats_for_a_baseline(self):
        with patch.object(carry, "os", SimpleNamespace(getenv=Mock(return_value="0"))):
            guest = carry.Guest(SimpleNamespace())
        self.assertFalse(guest.filter_enabled)
        self.assertFalse(guest.filter_file_fixture)
