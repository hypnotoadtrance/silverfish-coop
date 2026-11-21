from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bootstrap import install_stage, resident_boot_config
from tools.silverfish import runtime


class ResidentBootConfigTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.game = root / "Steam Layout"
        self.work = root / "Co-op Workspace"
        self.work.mkdir()
        binaries = self.game / runtime.SHIPPING.parent
        mods = binaries / "ue4ss" / "Mods"
        self.activation = mods / "mods.txt"
        self.descriptor = mods / install_stage.MOD / resident_boot_config.DESCRIPTOR
        self.descriptor.parent.mkdir(parents=True)
        self.activation.write_text(f"SilverfishCoopNative : 1\nSilverfishCoop : 1\n{install_stage.MOD} : 1\nAnotherMod : 1\n")

        digest = sha256()
        for relative in (runtime.SHIPPING, *(runtime.PAKS / name for name in runtime.STOCK_PACKAGES)):
            body = relative.as_posix().encode()
            path = self.game / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            digest.update(sha256(body).digest())
        native = {}
        for role, relative in (("runtime", runtime.CPP_RUNTIME_DLL), ("addon", runtime.CPP_ADDON_DLL)):
            dll = binaries / relative
            dll.parent.mkdir(parents=True, exist_ok=True)
            dll.write_bytes(role.encode())
            native[role] = sha256(role.encode()).hexdigest()
        runtime.write_json(self.game / "silverfish-coop-stage.json",
                           {"schema": 1, "game_build": digest.hexdigest(), "cpp_death_lab": native})
        for scripts in (binaries / runtime.MOD_SCRIPTS, root / "mod/SilverfishCoop/Scripts"):
            scripts.mkdir(parents=True)
            for module in ("main.lua", "authored_zone_maps.lua", "authored_wall_routes.lua"):
                (scripts / module).write_bytes(b"return nil\n")
        self.enterContext(patch.object(runtime, "REPO", root))
        self.enterContext(patch.object(runtime, "qualified_authored_routes", return_value=True))
        self.enterContext(patch.object(install_stage, "_verify"))

    def test_writes_only_generated_features_and_is_idempotent(self):
        activation = self.activation.read_bytes()
        with patch.dict(os.environ, {"SILVERFISH_COOP_FORK_FIXTURE": "inheritance canary"}):
            destination = resident_boot_config.configure(self.game, self.work, isolated=True,
                                                         seat="test-seat", bind="::1")
        self.assertEqual(destination, self.descriptor)
        settings = json.loads(destination.read_bytes())
        self.assertEqual({key: value for key, value in settings.items() if key != "features"}, {
            "schema": 1, "work": str(self.work), "seat": "test-seat", "campaign": "default",
            "bind": "::1", "port": 7777, "isolated": True,
        })
        features = settings["features"]
        self.assertEqual(features["SILVERFISH_COOP_UNIFIED_TITLE"], "1")
        self.assertEqual(features["SILVERFISH_COOP_HEADLESS"], "1")
        self.assertNotIn("SILVERFISH_COOP_FORK_FIXTURE", features)
        self.assertNotIn("SILVERFISH_COOP_BRIDGE", features)
        self.assertNotIn("SILVERFISH_COOP_EMBEDDED_STARTUP", features)
        title = self.work / "titles" / "test-seat"
        self.assertTrue((title / "run" / "game-bridge").is_dir())
        self.assertTrue((title / "game-user").is_dir())
        original_mtime = destination.stat().st_mtime_ns
        resident_boot_config.configure(self.game, self.work, isolated=True, seat="test-seat", bind="::1")
        self.assertEqual(destination.stat().st_mtime_ns, original_mtime)
        self.assertEqual(self.activation.read_bytes(), activation)

    def test_production_config_refuses_replacement_and_keeps_existing_content(self):
        resident_boot_config.configure(self.game, self.work)
        original = self.descriptor.read_bytes()
        self.assertNotIn("SILVERFISH_COOP_HEADLESS", json.loads(original)["features"])
        self.assertFalse((self.work / "titles" / "local" / "game-user").exists())
        with self.assertRaisesRegex(ValueError, "different resident startup configuration"):
            resident_boot_config.configure(self.game, self.work, campaign="different")
        self.assertEqual(self.descriptor.read_bytes(), original)

    def test_stale_game_manifest_refused_before_writing_configuration(self):
        (self.game / runtime.SHIPPING).write_bytes(b"updated executable")
        with self.assertRaisesRegex(ValueError, "game build differs"):
            resident_boot_config.configure(self.game, self.work)
        self.assertFalse(self.descriptor.exists())
        self.assertFalse((self.work / "titles").exists())


if __name__ == "__main__":
    unittest.main()
