from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from tools.silverfish.runtime import Bridge


MAP = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
LEVEL = MAP + ".CrossRoads_P:PersistentLevel."
SHOOTER = f"Player_FP_C {LEVEL}Player_FP_C_42"
VICTIM = f"NPC_Flayer_C {LEVEL}NPC_Flayer_C_12"
PROJECTILE = f"Projectile_Pistol_Light_C {LEVEL}Projectile_Pistol_Light_C_55"
TOPIC = "native-campaign-flayer-projectile"


class NativeFlayerProjectileTests(unittest.TestCase):
    def read(self, *rows, state="hosting", area=MAP):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            header = [("schema", "1"), ("seq", "8"), ("session", "native", state, area, "1", "0")]
            (path / "status.tsv").write_text("\n".join("\t".join(row) for row in (*header, *rows)), encoding="utf-8")
            return Bridge(path, Mock()).snapshot()

    def test_distinct_stock_hits_keep_exact_provenance(self):
        hit = (TOPIC, MAP, "7", SHOOTER, VICTIM, PROJECTILE, "180", "155.25", "24.75")
        recent = (TOPIC, MAP, "8", SHOOTER, VICTIM, PROJECTILE.removesuffix("55") + "56", "155.25", "140", "15.25")
        aim = ("native-campaign-flayer-shot-aim", MAP, VICTIM, SHOOTER, "421")
        snapshot = self.read(recent, hit, aim, (TOPIC + "-status", MAP, "stock_damage_verified"))
        self.assertEqual(snapshot["native_campaign_flayer_projectile_status"], "stock_damage_verified")
        self.assertEqual(snapshot["native_campaign_flayer_shot_aim"]["victim"], VICTIM)
        newest, prior = snapshot["native_campaign_flayer_projectile_hits"]
        self.assertEqual((newest["sequence"], newest["before"], newest["after"], newest["damage"]), (8, 155.25, 140, 15.25))
        self.assertEqual((prior["sequence"], prior["shooter"], prior["victim"], prior["projectile"]),
                         (7, SHOOTER, VICTIM, PROJECTILE))

    def test_observer_rejects_out_of_scope_or_inconsistent_hits(self):
        valid = (TOPIC, MAP, "1", SHOOTER, VICTIM, PROJECTILE, "180", "157.5", "22.5")
        self.assertIsNone(self.read(valid, state="connected"))
        self.assertIsNone(self.read(valid, area=MAP.replace("CrossRoads", "DeadBridge")))
        self.assertIsNone(self.read(valid, valid))
        self.assertIsNone(self.read((*valid[:-1], "24.75")))
        self.assertIsNone(self.read((*valid[:-2], "0", "180")))
        self.assertIsNone(self.read((*valid[:5], f"OtherBullet {LEVEL}OtherBullet_1", *valid[6:])))

    def test_fixture_arming_and_atomic_placement_differ(self):
        base = ("native-campaign-flayer-shot-aim", MAP, VICTIM, SHOOTER)
        key = "native_campaign_flayer_shot_aim"
        armed = self.read((*base, "-", "armed"))[key]
        applied = self.read((*base, "421", "applied"))[key]
        self.assertEqual((armed["state"], armed["reach"]), ("armed", None))
        self.assertEqual((applied["state"], applied["reach"]), ("applied", 421))
        self.assertIsNone(self.read((*base, "421", "armed")))
        self.assertIsNone(self.read((*base, "-", "applied")))
        self.assertIsNone(self.read((*base, "421", "unknown")))
