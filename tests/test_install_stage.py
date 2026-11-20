import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bootstrap import install_stage
from tools.silverfish import runtime


class EmbeddedStageInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.work = self.root / "private"
        self.source = self.root / "runtime"
        self.addon = self.root / "embedded.dll"
        self.checkout = self.root / "checkout"
        self.lua = self.checkout / "mod/SilverfishCoop/Scripts/main.lua"
        self.lua.parent.mkdir(parents=True)
        self.lua.write_bytes(b"return nil\n")
        worker = {
            "app/tools/silverfish/__init__.py": b"",
            "app/tools/silverfish/embedded_startup.py": b"def run(): pass\n",
        }
        for name, content in worker.items():
            python = self.checkout / name.removeprefix("app/")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(content)
        override = patch.object(runtime, "REPO", self.checkout)
        override.start()
        self.addCleanup(override.stop)
        payload = {
            **worker, "app/tools/__init__.py": b"", "python314.dll": b"Python DLL",
            "python314.zip": b"standard library", "python314._pth": b"python314.zip\n.\nsite-packages\napp\n",
            "site-packages/example.py": b"package fixture",
        }
        for name, content in payload.items():
            file = self.source / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        manifest = {
            "schema": 1, "platform": "win-amd64", "python_version": "3.14.6", "python_library": "python314.dll",
            "module_paths": ["python314.zip", ".", "site-packages", "app"],
            "payload_bytes": sum(map(len, payload.values())),
            "files": {name: hashlib.sha256(content).hexdigest() for name, content in payload.items()},
        }
        raw = json.dumps(manifest).encode("utf-8")
        (self.source / "runtime-manifest.json").write_bytes(raw)
        self.addon.write_bytes(b"MZ embedded pin " + hashlib.sha256(raw).hexdigest().encode("ascii"))
        self.stage = self.work / "stage"
        self.mods = self.stage / runtime.SHIPPING.parent / "ue4ss/Mods"
        self.activation = self.mods / "mods.txt"

    def make_stage(self):
        binaries = (self.stage / runtime.SHIPPING).parent
        files = {runtime.SHIPPING.name: b"shipping", runtime.CPP_RUNTIME_DLL: b"native loader",
                 runtime.CPP_ADDON_DLL: b"native add-on", runtime.MOD_SCRIPTS / "main.lua": self.lua.read_bytes(),
                 "ue4ss/Mods/mods.txt": b"SilverfishCoopNative : 1\nSilverfishCoop : 1\nMine : 0"}
        for name, content in files.items():
            file = binaries / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        runtime.write_json(self.stage / "silverfish-coop-stage.json", {
            "schema": 1, "game_build": "a" * 64, "cpp_death_lab": {
                "runtime": hashlib.sha256(files[runtime.CPP_RUNTIME_DLL]).hexdigest(),
                "addon": hashlib.sha256(files[runtime.CPP_ADDON_DLL]).hexdigest(),
            },
        })

    def run_install(self, **kwargs):
        return install_stage.install(self.work, self.source, self.addon, **kwargs)

    def test_existing_stage_activates_once_and_preserves_other_rows(self):
        self.make_stage()
        result = self.run_install()
        self.assertTrue(result["activated"])
        self.assertIn("Mine : 0\nSilverfishCoopEmbeddedRuntime : 1\n", self.activation.read_text())
        self.assertEqual((self.mods / install_stage.MOD / "dlls/main.dll").read_bytes(), self.addon.read_bytes())
        original = self.activation.stat().st_mtime_ns
        repeated = self.run_install()
        self.assertFalse(repeated.pop("activated"))
        result.pop("activated")
        self.assertEqual(repeated, result)
        self.assertEqual(original, self.activation.stat().st_mtime_ns)

    def test_damaged_package_and_mismatched_pin_do_not_touch_stage(self):
        self.make_stage()
        before = self.activation.read_bytes()
        payload = self.source / "python314.dll"
        original = payload.read_bytes()
        payload.write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "differs from its manifest"):
            self.run_install()
        payload.write_bytes(original)
        (self.source / "surprise.py").touch()
        with self.assertRaisesRegex(ValueError, "differs from its manifest"):
            self.run_install()
        (self.source / "surprise.py").unlink()
        self.addon.write_bytes(b"MZ other add-on")
        with self.assertRaisesRegex(ValueError, "SHA-256 pin"):
            self.run_install()
        self.assertEqual(self.activation.read_bytes(), before)
        self.assertFalse((self.mods / install_stage.MOD).exists())

    def test_interrupted_activation_recovers_the_same_verified_copy(self):
        self.make_stage()
        before = self.activation.read_bytes()
        with patch.object(runtime, "write_text", side_effect=PermissionError("fixture")):
            with self.assertRaises(PermissionError):
                self.run_install()
        self.assertEqual(self.activation.read_bytes(), before)
        installed = self.mods / install_stage.MOD / "dlls/main.dll"
        self.assertTrue(installed.is_file())
        last_write = installed.stat().st_mtime_ns
        self.assertTrue(self.run_install()["activated"])
        self.assertEqual(installed.stat().st_mtime_ns, last_write)

    def test_conflicting_activation_is_preserved(self):
        self.make_stage()
        before = b"Mine : 1\nSilverfishCoopEmbeddedRuntime : 0\n"
        self.activation.write_bytes(before)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.run_install()
        self.assertEqual(self.activation.read_bytes(), before)
        self.assertFalse((self.mods / install_stage.MOD).exists())

    def test_fresh_stage_copies_stock_and_publishes_with_runtime_enabled(self):
        game, loader, signatures = self.root / "game", self.root / "loader", self.root / "signatures"
        files = {game / runtime.MANIFEST: (f"SilverFish.exe\t1\n{runtime.SHIPPING.as_posix()}\t1\n").encode(),
                 game / "SilverFish.exe": b"stock launcher", game / runtime.SHIPPING: b"stock shipping",
                 loader / "dwmapi.dll": b"proxy", loader / "ue4ss/UE4SS.dll": b"native loader",
                 loader / "ue4ss/UE4SS-settings.ini": b"ConsoleEnabled = 1\n",
                 signatures / "StaticConstructObject.lua": b"return nil\n"}
        files.update({game / runtime.PAKS / name: name.encode() for name in runtime.STOCK_PACKAGES})
        for file, content in files.items():
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        result = self.run_install(game=game, ue4ss=loader, signatures=signatures, cpp_addon=self.addon)
        self.assertEqual(result["stage"], str(self.stage))
        self.assertTrue(result["activated"])
        self.assertEqual(runtime.staged_build(self.work), result["build"])
        self.assertEqual(list(self.work.iterdir()), [self.stage])
        self.assertIn("SilverfishCoopEmbeddedRuntime : 1\n", self.activation.read_text())
        self.assertTrue(all(file.read_bytes() == content for file, content in files.items()))
        self.assertFalse((game / runtime.SHIPPING.parent / "ue4ss").exists())
        with self.assertRaisesRegex(ValueError, "must be separate"):
            install_stage.install(game / "bad", self.source, self.addon, game=game, ue4ss=loader,
                                  signatures=signatures, cpp_addon=self.addon)
        self.assertFalse((game / "bad").exists())


if __name__ == "__main__":
    unittest.main()
