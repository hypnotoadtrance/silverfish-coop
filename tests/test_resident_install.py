from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bootstrap import install_stage, install_stock, resident_install
from tools.silverfish import runtime


class ResidentInstallTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.game = root / "Installed Game"
        self.work = root / "Co-op Workspace"
        self.source = root / "Package"
        self.source.mkdir()
        self.game.mkdir()
        (self.game / "SilverFish.exe").write_bytes(b"stock bootstrap")
        self.stock = (runtime.SHIPPING, *(runtime.PAKS / name for name in runtime.STOCK_PACKAGES))
        digest = sha256()
        for relative in self.stock:
            body = relative.as_posix().encode("ascii")
            target = self.game / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            digest.update(sha256(body).digest())
        self.install_args = dict(cpp_addon=self.source / "native.dll",
                                 embedded_addon=self.source / "embedded.dll",
                                 embedded_runtime=self.source / "runtime")
        self.activation = self.game / resident_install._ACTIVATION
        self.activation.parent.mkdir(parents=True, exist_ok=True)
        self.custom = self.activation.parent / "AnotherMod" / "Scripts" / "main.lua"
        self.custom.parent.mkdir(parents=True)
        self.custom.write_bytes(b"print('unrelated')\n")
        self.settings = self.game / resident_install._SETTINGS
        self.settings.write_bytes(b"; user preferences\r\nConsoleEnabled = 1\r\n")
        self.mods_before = b"\xef\xbb\xbf; keep order\r\nAnotherMod : 1\r\nSilverfishCoopBootstrap : 1"
        self.activation.write_bytes(self.mods_before)

        postfix = runtime.SHIPPING.parent
        files = {resident_install._SETTINGS: b"ConsoleEnabled = 0\n",
                 postfix / runtime.CPP_RUNTIME_DLL: b"loader",
                 postfix / runtime.CPP_ADDON_DLL: b"gameplay addon",
                 resident_install._EMBEDDED / "dlls" / "main.dll": b"embedded addon"}
        checkout = root / "Checkout"
        scripts = checkout / "mod" / "SilverfishCoop" / "Scripts"
        scripts.mkdir(parents=True)
        for module in ("main.lua", "authored_zone_maps.lua", "authored_wall_routes.lua"):
            (scripts / module).write_bytes(b"return nil\n")
            files[postfix / runtime.MOD_SCRIPTS / module] = (scripts / module).read_bytes()
        self.enterContext(patch.object(runtime, "REPO", checkout))
        self.enterContext(patch.object(runtime, "qualified_authored_routes", return_value=True))
        self.enterContext(patch.object(install_stage, "_verify"))
        self.build = digest.hexdigest()
        self.enterContext(patch.object(resident_install, "_GAME_BUILD", self.build))
        self.enterContext(patch.object(resident_install, "_payload", return_value=files))

    def install(self, **options):
        return resident_install.install(self.game, self.work, self.source, self.source, **self.install_args, **options)

    def test_install_publishes_resident_mods_and_preserves_stock_and_other_mods(self):
        before = {relative: (self.game / relative).read_bytes() for relative in self.stock}
        result = self.install()
        descriptor = Path(result["descriptor"])
        config = json.loads(descriptor.read_bytes())
        self.assertEqual(config["shipping_sha256"], sha256(before[runtime.SHIPPING]).hexdigest())
        self.assertEqual(config["work"], str(self.work))
        self.assertFalse(config["isolated"])
        self.assertTrue(result["activation_changed"])
        self.assertTrue(result["configuration_created"])
        self.assertFalse((self.work / "stage").exists())
        self.assertTrue(self.activation.read_bytes().startswith(self.mods_before[:-1] + b"0\r\n"))
        self.assertEqual(self.settings.read_bytes(), b"; user preferences\r\nConsoleEnabled = 1\r\n")
        self.assertEqual(self.custom.read_bytes(), b"print('unrelated')\n")
        self.assertEqual({relative: (self.game / relative).read_bytes() for relative in self.stock}, before)
        for relative in result["installed"]:
            self.assertNotIn(Path(relative), self.stock)

        again = self.install()
        self.assertEqual(again["installed"], [])
        self.assertFalse(again["activation_changed"])
        self.assertFalse(again["configuration_created"])

    def test_conflicting_loader_refused_before_activation(self):
        loader = self.game / runtime.SHIPPING.parent / runtime.CPP_RUNTIME_DLL
        loader.write_bytes(b"custom loader")
        with self.assertRaisesRegex(ValueError, "different installation file"):
            self.install()
        self.assertEqual(loader.read_bytes(), b"custom loader")
        self.assertEqual(self.activation.read_bytes(), self.mods_before)
        self.assertFalse((self.game / resident_install._DESCRIPTOR).exists())
        self.assertFalse(self.work.exists())

    def test_failed_configuration_restores_previous_mod_activation(self):
        with patch.object(resident_install.resident_boot_config, "configure", side_effect=OSError("fixture denied")):
            with self.assertRaisesRegex(OSError, "fixture denied"):
                self.install()
        self.assertEqual(self.activation.read_bytes(), self.mods_before)
        self.assertFalse((self.game / resident_install._DESCRIPTOR).exists())
        self.assertTrue(self.install()["configuration_created"])

    def test_legacy_enabled_marker_refused_without_changing_user_files(self):
        enabled = self.activation.parent / install_stock.MOD / "enabled.txt"
        enabled.parent.mkdir()
        enabled.touch()
        with self.assertRaisesRegex(ValueError, "legacy co-op bootstrap"):
            self.install()
        self.assertTrue(enabled.is_file())
        self.assertEqual(self.activation.read_bytes(), self.mods_before)


if __name__ == "__main__":
    unittest.main()
