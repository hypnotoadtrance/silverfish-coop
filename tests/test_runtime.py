import asyncio
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import runtime, service


class BridgeWalkTests(unittest.IsolatedAsyncioTestCase):
    async def test_continuous_walk_uses_the_native_three_second_limit(self):
        bridge = runtime.Bridge(Path("unused"), None)
        with patch.object(bridge, "command", new_callable=AsyncMock) as command:
            await bridge.walk(0, 1, 3000, continuous=True)
            command.assert_awaited_once_with("walk-continuous", "0", "1", "3000")
            with self.assertRaisesRegex(ValueError, "50 to 3000 ms"):
                await bridge.walk(0, 1, 3001, continuous=True)
            command.assert_awaited_once()

    async def test_discrete_walk_keeps_its_one_second_limit(self):
        bridge = runtime.Bridge(Path("unused"), None)
        with patch.object(bridge, "command", new_callable=AsyncMock) as command:
            await bridge.walk(0, 1, 1000)
            command.assert_awaited_once_with("walk", "0", "1", "1000")
            with self.assertRaisesRegex(ValueError, "50 to 1000 ms"):
                await bridge.walk(0, 1, 1001)
            command.assert_awaited_once()


class LocalCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedded_command_accepts_exact_receipt_without_file_polling(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)

            async def reply(payload, timeout):
                request, action, argument = payload.decode().rstrip("\n").split("\t")
                self.assertEqual((action, argument, timeout), ("walk", "20", 3))
                return (f"schema\t1\nseq\t42\nack\t{request}\taccepted\tmove\n"
                        "session\tclient\tconnected\tObservatory\t1\t0\n")

            bridge.local_bridge = AsyncMock()
            bridge.local_bridge.command.side_effect = reply
            result = await bridge.command("walk", "20", timeout=3)
            self.assertEqual(result["sequence"], 42)
            self.assertEqual(result["ack"]["message"], "move")
            self.assertEqual(list(bridge.directory.iterdir()), [])

    async def test_embedded_inputs_wake_worker_and_keep_file_fallback_in_order(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)
            bridge.local_bridge = Mock()
            bridge.local_bridge.input_ready.return_value = True
            bridge.local_bridge.read_input.return_value = [b"interact\t8", b"interact\t10"]
            (bridge.directory / "input.tsv").write_bytes(b"interact\t9\n")
            await asyncio.wait_for(bridge.wait_for_interaction(1), .2)
            self.assertEqual(bridge.interactions(), [
                (uuid.uuid5(bridge.input_namespace, str(sequence)).hex, None) for sequence in (8, 9, 10)
            ])

    async def test_bad_embedded_receipt_does_not_fall_back_to_replay(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)
            bridge.local_bridge = AsyncMock()
            bridge.local_bridge.command.return_value = (
                "schema\t1\nseq\t42\nack\tother-request\taccepted\n"
                "session\tclient\tconnected\tObservatory\t1\t0\n"
            )
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                await bridge.command("leave")
            bridge.local_bridge.command.assert_awaited_once()
            self.assertEqual(list(bridge.directory.iterdir()), [])


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

    def test_portable_manifest_overrides_an_older_root_manifest(self):
        expected = runtime.staged_build(self.work)
        portable = self.stage / runtime.PORTABLE_STAGE_MANIFEST
        runtime.write_json(portable, self.metadata)
        runtime.write_json(self.manifest, {"schema": 1, "game_build": "b" * 64})
        self.assertEqual(runtime.stage_manifest(self.stage), portable)
        self.assertEqual(runtime.staged_build(self.work), expected)
        portable.unlink()
        self.assertEqual(runtime.stage_manifest(self.stage), self.manifest)
        self.assertNotEqual(runtime.staged_build(self.work), expected)

    def test_portable_update_fingerprints_only_its_current_lua_files(self):
        expected = runtime.staged_build(self.work)
        main = self.scripts / "main.lua"
        self.metadata["lua_files"] = {"main.lua": hashlib.sha256(main.read_bytes()).hexdigest()}
        runtime.write_json(self.manifest, self.metadata)
        (self.scripts / "old_release.lua").write_bytes(b"return {'no longer shipped'}\n")
        with runtime.installed_content(self.work, self.stage):
            runtime.verify_installed_lua(self.work)
            self.assertTrue(runtime.staged_script(self.work, "main.lua"))
            self.assertFalse(runtime.staged_script(self.work, "old_release.lua"))
            self.assertEqual(runtime.staged_build(self.work), expected)

            main.write_bytes(b"return nil\r\n")
            self.assertEqual(runtime.staged_build(self.work), expected)
            main.write_bytes(b"return {}\n")
            with self.assertRaisesRegex(ValueError, "differs from the release"):
                runtime.verify_installed_lua(self.work)
            main.unlink()
            with self.assertRaisesRegex(ValueError, "Missing installed co-op script"):
                runtime.verify_installed_lua(self.work)

    def test_portable_lua_inventory_refuses_redirected_file_names(self):
        self.metadata["lua_files"] = {"main.lua": "a" * 64, "../outside.lua": "a" * 64}
        runtime.write_json(self.manifest, self.metadata)
        with self.assertRaisesRegex(ValueError, "file list is invalid"):
            runtime.verify_installed_lua(self.work)

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
        paths = self.work, self.work / "profile", self.work / "bridge"
        with patch.dict(os.environ), patch("tools.silverfish.win_job.GameProcess") as game:
            os.environ.pop("SILVERFISH_COOP_RENDER_CAPTURE", None)
            for port in (17432, None):
                runtime.launch(*paths, headless=True, port=port)
                command = game.call_args.args[0]
                self.assertIn("-RenderOffScreen", command)
                self.assertIn("-nullrhi", command)

            runtime.launch(*paths, headless=False)
            self.assertNotIn("-RenderOffScreen", game.call_args.args[0])

    @unittest.skipUnless(os.name == "nt", "Uses the Windows game launcher")
    def test_headless_capture_enables_gpu_only_for_the_selected_peer(self):
        paths = self.work, self.work / "profile", self.work / "bridge"
        flag = "SILVERFISH_COOP_RENDER_CAPTURE"
        fixture = "SILVERFISH_COOP_FIXTURE_CAPTURE"
        with patch.dict(os.environ, {fixture: "inherited"}), patch("tools.silverfish.win_job.GameProcess") as game:
            for capture in ("host", "guest", "unknown"):
                os.environ[flag] = capture
                for role, port in (("host", 17432), ("guest", None)):
                    with self.subTest(capture=capture, role=role):
                        runtime.launch(*paths, headless=True, port=port)
                        command = game.call_args.args[0]
                        self.assertEqual("-nullrhi" in command, capture != role)
                        self.assertIn("-RenderOffScreen", command)
                        self.assertIn("DefaultViewportMouseCaptureMode=NoCapture", command)
                        self.assertIn("DefaultViewportMouseLockMode=DoNotLock", command)
                        self.assertNotIn(flag, game.call_args.kwargs["env"])
                        self.assertEqual(game.call_args.kwargs["env"].get(fixture), "1" if capture == role else None)
                        startup = game.call_args.kwargs["startupinfo"]
                        self.assertTrue(startup.dwFlags & runtime.subprocess.STARTF_USESHOWWINDOW)
                        self.assertEqual(startup.wShowWindow, runtime.subprocess.SW_HIDE)

    @unittest.skipUnless(os.name == "nt", "Uses the Windows game launcher")
    def test_title_and_foreground_do_not_use_the_guest_capture_setting(self):
        paths = self.work, self.work / "profile", self.work / "bridge"
        fixture = "SILVERFISH_COOP_FIXTURE_CAPTURE"
        with (patch.dict(os.environ, {"SILVERFISH_COOP_RENDER_CAPTURE": "guest", fixture: "1"}),
              patch("tools.silverfish.win_job.GameProcess") as game):
            runtime.launch(*paths, headless=True, title_fixture=True)
            self.assertIn("-nullrhi", game.call_args.args[0])
            self.assertNotIn(fixture, game.call_args.kwargs["env"])
            runtime.launch(*paths, headless=False)
            self.assertNotIn("-RenderOffScreen", game.call_args.args[0])
            self.assertNotIn(fixture, game.call_args.kwargs["env"])

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

    @unittest.skipUnless(os.name == "nt", "Uses the Windows game launcher")
    def test_cqc_enemy_loot_is_explicit_and_independent_from_fixtures(self):
        self.install_cpp_fixture()
        flag = "SILVERFISH_COOP_NATIVE_CQC_LOOT"
        paths = self.work, self.work / "profile", self.work / "bridge"
        host = dict(headless=False, port=17432, native_events=True, native_guest=True, native_zones=True, native_loot_input=True)
        with patch.dict(os.environ, {flag: "1"}), patch("tools.silverfish.win_job.GameProcess") as game:
            runtime.launch(*paths, **host)
            self.assertNotIn(flag, game.call_args.kwargs["env"])
            runtime.launch(*paths, native_dead_bridge_enemy_loot=True, **host)
            self.assertEqual(game.call_args.kwargs["env"][flag], "1")
            self.assertNotIn("SILVERFISH_COOP_NATIVE_FIXTURES", game.call_args.kwargs["env"])
            with self.assertRaisesRegex(ValueError, "native campaign host"):
                runtime.launch(*paths, headless=True, native_zones=True, native_dead_bridge_enemy_loot=True)
