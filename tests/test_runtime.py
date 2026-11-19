import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.silverfish import runtime, service


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
        self.scripts = self.binaries / runtime.MOD_SCRIPTS
        self.scripts.mkdir(parents=True)
        (self.scripts / "main.lua").write_bytes(b"return nil\n")
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

    def test_stock_and_cooked_stage_have_distinct_fingerprints(self):
        stock = runtime.staged_build(self.work)
        self.assertRegex(stock, r"^[0-9a-f]{64}$")
        self.assertNotEqual(stock, self.metadata["game_build"])
        self.assertIsNone(runtime.native_death_backend(self.work))
        with patch.object(runtime, "has_native_death_patch", return_value=True):
            self.assertEqual(runtime.native_death_backend(self.work), "overlay")
            self.assertNotEqual(runtime.staged_build(self.work), stock)

    def test_lua_change_makes_old_invitation_incompatible(self):
        script = self.scripts / "commands.lua"
        script.write_bytes(b"return {}\n")
        build = runtime.staged_build(self.work)
        invite = self.work / "invite.json"
        runtime.write_json(invite, {"schema": 1, "game_build": build, "protocol_version": service.PROTOCOL,
                                   "host": "127.0.0.1", "port": 7777, "guest_id": "guest", "character_id": "scout",
                                   "certificate": "fixture", "credential": "fixture"})
        script.write_bytes(b"return {}\r\n")
        self.assertEqual(service.load_invite(invite, runtime.staged_build(self.work))["game_build"], build)

        script.write_bytes(b"return {'another command'}\n")
        with self.assertRaisesRegex(ValueError, "game/mod build are incompatible"):
            service.load_invite(invite, runtime.staged_build(self.work))

        script.unlink()
        self.assertNotEqual(runtime.staged_build(self.work), build)

    def test_launcher_code_changes_the_peer_fingerprint(self):
        launcher = self.work / "tools/silverfish/service.py"
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(b"PROTOCOL = 1\n")
        with patch.object(runtime, "REPO", self.work):
            baseline = runtime.staged_build(self.work)
            launcher.write_bytes(b"PROTOCOL = 2\n")
            self.assertNotEqual(runtime.staged_build(self.work), baseline)

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

    @unittest.skipUnless(os.name == "nt", "Uses the Windows game launcher")
    def test_reload_fixture_requires_headless_client_with_reload_input(self):
        self.install_cpp_fixture()
        fixture_flag = "SILVERFISH_COOP_NATIVE_GUEST_RELOAD_FIXTURES"
        input_flag = "SILVERFISH_COOP_NATIVE_GUEST_RELOAD_INPUT"
        paths = self.work, self.work / "profile", self.work / "bridge"
        with patch.dict(os.environ), patch("tools.silverfish.win_job.GameProcess") as game:
            os.environ.pop(fixture_flag, None)
            runtime.launch(*paths, headless=True, native_zones=True, guest_reload_input=True)
            self.assertEqual(game.call_args.kwargs["env"][fixture_flag], "1")
            self.assertEqual(game.call_args.kwargs["env"][input_flag], "1")

            os.environ[fixture_flag] = "1"
            runtime.launch(*paths, headless=False, native_zones=True, guest_reload_input=True)
            self.assertNotIn(fixture_flag, game.call_args.kwargs["env"])
            self.assertEqual(game.call_args.kwargs["env"][input_flag], "1")

            runtime.launch(*paths, headless=True, native_zones=True)
            self.assertNotIn(fixture_flag, game.call_args.kwargs["env"])
