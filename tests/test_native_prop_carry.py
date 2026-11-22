import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.silverfish import native_prop_carry as carry


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
