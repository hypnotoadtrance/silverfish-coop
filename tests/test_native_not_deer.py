"""Validate the exact stock NotDeer spawn and replica wire."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from live_campaign_npc_smoke import LEVEL, MAP, SQUAD_PREFIX, client_replica, read_npcs
from tools.silverfish.runtime import Bridge


REPLICA = f"NPC_NotDeer_C {LEVEL}NPC_NotDeer_C_2147482910"
MARKER = f"AI_Spawner_Squad_BP_C {LEVEL}AI_Spawner_Squad_BP_C_37"
SQUAD = f"{SQUAD_PREFIX}NotDeer.NotDeer"


class NativeNotDeerTests(unittest.TestCase):
    def test_qualified_replica_and_exact_original_stock_veto(self):
        frame = [
            ["schema", "1"],
            ["seq", "7"],
            ["session", "client", "connected", MAP, "0", "1"],
            ["native-campaign-npc", MAP, "client", "0", "stock_client_not_deer_load_denied", MARKER, SQUAD, "0", "0", "1"],
            ["native-campaign-npc-census", MAP, "client", "1", "0", "1", "0", "0", "0", "1"],
            ["native-campaign-npc-saved-squads", MAP, "client", "1", "1", "stock_saved_squad_denied", MARKER, SQUAD],
            ["native-campaign-npc-network", MAP, "client", "0", "0", "0", "1", "-", "-", "-"],
            ["native-campaign-npc-notdeer", MAP, "client", REPLICA],
        ]
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "game-bridge" / "active"
            directory.mkdir(parents=True)
            path = directory / "status.tsv"
            bridge = Bridge(directory, None)

            def publish():
                path.write_text("\n".join("\t".join(row) for row in frame) + "\n", encoding="utf-8")
                return bridge.snapshot()

            report = publish()["native_campaign_npc"]
            self.assertEqual((report["family"], report["replica"]), ("notdeer", REPLICA))
            self.assertTrue(client_replica(read_npcs(root, "client"), 1, True, "notdeer"))
            frame[-1][3] = REPLICA.replace("NPC_NotDeer_C_", "NPC_Flayer_C_")
            self.assertIsNone(publish())
            frame[-1][3] = REPLICA
            frame[4][5] = "2"
            self.assertIsNone(publish())
            frame[4][5] = "1"
            frame[4][7] = "1"
            self.assertIsNone(publish())
            self.assertFalse(client_replica(read_npcs(root, "client"), 1, True, "notdeer"))
