import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.silverfish import runtime


class StageBackendTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.stage = self.work / "stage"
        game = self.stage / runtime.SHIPPING
        game.parent.mkdir(parents=True)
        game.write_bytes(b"game fixture")
        self.binaries = game.parent
        self.manifest = self.stage / "silverfish-coop-stage.json"
        self.metadata = {"schema": 1, "game_build": "a" * 64}
        runtime.write_json(self.manifest, self.metadata)

    def install_cpp_fixture(self):
        hashes = {}
        for key, relative in (("runtime", runtime.CPP_RUNTIME_DLL), ("addon", runtime.CPP_ADDON_DLL)):
            path = self.binaries / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = key.encode("ascii")
            path.write_bytes(payload)
            hashes[key] = hashlib.sha256(payload).hexdigest()
        self.metadata["cpp_death_lab"] = hashes
        runtime.write_json(self.manifest, self.metadata)

    def test_stock_and_cooked_stage_preserve_their_fingerprints(self):
        stock = runtime.staged_build(self.work)
        self.assertEqual(stock, self.metadata["game_build"])
        self.assertIsNone(runtime.native_death_backend(self.work))
        with patch.object(runtime, "has_native_death_patch", return_value=True):
            self.assertEqual(runtime.native_death_backend(self.work), "overlay")
            expected = hashlib.sha256((stock + ":native-death-lab:1").encode("ascii")).hexdigest()
            self.assertEqual(runtime.staged_build(self.work), expected)

    def test_cpp_stage_is_distinct_and_rejects_changed_binaries(self):
        stock = runtime.staged_build(self.work)
        self.install_cpp_fixture()
        self.assertEqual(runtime.native_death_backend(self.work), "cpp")
        self.assertNotEqual(runtime.staged_build(self.work), stock)
        (self.binaries / runtime.CPP_ADDON_DLL).write_bytes(b"changed add-on")
        with self.assertRaisesRegex(ValueError, "add-on|addon"):
            runtime.staged_build(self.work)

    def test_cpp_stage_rejects_mixed_overlay_or_missing_manifest(self):
        self.install_cpp_fixture()
        with patch.object(runtime, "has_native_death_patch", return_value=True):
            with self.assertRaisesRegex(ValueError, "separate stage"):
                runtime.native_death_backend(self.work)
        self.metadata.pop("cpp_death_lab")
        runtime.write_json(self.manifest, self.metadata)
        with self.assertRaisesRegex(ValueError, "missing its stage manifest"):
            runtime.native_death_backend(self.work)

    @unittest.skipUnless(os.name == "nt", "Uses the Windows game launcher")
    def test_only_headless_launches_use_unreals_offscreen_application(self):
        with patch("tools.silverfish.win_job.GameProcess") as game:
            runtime.launch(self.work, self.work / "profile", self.work / "bridge", headless=True)
            command = game.call_args.args[0]
            self.assertIn("-RenderOffScreen", command)
            self.assertIn("-nullrhi", command)

            runtime.launch(self.work, self.work / "profile", self.work / "bridge", headless=False)
            self.assertNotIn("-RenderOffScreen", game.call_args.args[0])
