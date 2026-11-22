import asyncio
from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_title_handoff import ready
from tools.silverfish import embedded_engine as embedded
from tools.silverfish import runtime
from tools.silverfish.native_title_entry import MAP


class GameThreadBridge:
    def __init__(self, directory, process):
        self.directory = directory
        self.process = process
        self.frames = []
        self.commands = []
        self.acknowledgement = 3
        self.blocked = None
        self.local_bridge = None

    async def wait_for(self, condition, timeout):
        while self.frames:
            await asyncio.sleep(0)
            frame = self.frames.pop(0)
            if condition(frame):
                return frame
        raise TimeoutError("No qualified advancing frame")

    async def command(self, action, *arguments, timeout):
        self.commands.append((action, arguments, timeout))
        if self.blocked:
            await self.blocked.wait()
        return {"sequence": self.acknowledgement}


class EmbeddedEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.work = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.executable = self.work / "stage" / runtime.SHIPPING
        self.executable.parent.mkdir(parents=True)
        self.executable.touch()
        runtime.write_json(self.work / "stage" / "silverfish-coop-stage.json",
                           {"schema": 1, "game_build": "a" * 64})
        self.campaign = self.work / "campaigns" / "Host"
        self.client = self.work / "clients" / "Guest"
        for directory in (self.campaign, self.client):
            self.bridge_path(directory).mkdir(parents=True)
            (directory / "game-user" / "Saved").mkdir(parents=True)
        self.enterContext(patch.object(embedded.EmbeddedEngine, "_used", False))
        self.startup = self.enterContext(patch.object(embedded, "_current_startup"))
        self.startup.return_value = self.process_spec(self.campaign)
        self.bridge_type = self.enterContext(patch.object(runtime, "Bridge", side_effect=self.make_bridge))
        self.frames = [self.frame(1), self.frame(2)]

    def bridge_path(self, directory):
        return directory / "run" / "game-bridge" / ("b" * 32)

    def process_spec(self, directory, *, host=True):
        argv = [str(self.executable), f"-userdir={directory / 'game-user'}"]
        if not host:
            argv.append("-MULTIHOME=127.0.0.1")
        return embedded._Startup(os.getpid(), self.executable, tuple(argv), {
            embedded.BRIDGE_ENV: str(self.bridge_path(directory)),
            embedded.OPT_IN_ENV: "1", "SILVERFISH_COOP_NATIVE_ZONE": "1",
        })

    def frame(self, sequence, directory=None, **receipt):
        directory = directory or self.campaign
        snapshot = ready(sequence)
        snapshot[embedded.STARTUP_KEY] = {
            "map": MAP, "sequence": sequence, "saved_directory": str(directory / "game-user" / "Saved"), **receipt,
        }
        return snapshot

    def make_bridge(self, directory, process):
        bridge = GameThreadBridge(directory, process)
        bridge.frames.extend(self.frames)
        return bridge

    async def host(self, port=28016):
        return await embedded.EmbeddedEngine.acquire(self.work, self.campaign, role="host", internal_port=port,
                                                     required_env={"SILVERFISH_COOP_NATIVE_ZONE": "1"})

    async def test_host_acquires_current_process_once_and_releases_through_stock_leave(self):
        engine = await self.host()
        self.assertEqual(engine.pid, os.getpid())
        self.assertIsNone(engine.poll())
        self.assertIs(engine.bridge.process, engine)
        self.assertEqual(engine.bridge.directory, self.bridge_path(self.campaign))
        self.assertEqual(engine.internal_port, 28016)
        self.assertFalse(hasattr(engine, "kill"))
        self.assertFalse(hasattr(engine, "close_job"))
        with self.assertRaisesRegex(RuntimeError, "already used"):
            await self.host()
        engine.bridge.frames.extend([self.frame(3), self.frame(4)])
        with patch.object(runtime, "stop_game", side_effect=AssertionError("Must never stop its own process")):
            self.assertEqual((await engine.release())["sequence"], 4)
        self.assertEqual(engine.bridge.commands, [("leave", (), 12)])
        self.assertEqual(engine.poll(), 0)
        self.assertEqual(engine.returncode, 0)
        with self.assertRaisesRegex(RuntimeError, "not active"):
            await engine.release()
        with self.assertRaisesRegex(RuntimeError, "already used"):
            await self.host()

    async def test_client_requires_its_own_role_and_no_startup_port(self):
        client = self.process_spec(self.client, host=False)
        self.startup.return_value = replace(client, argv=(*client.argv, "-port=28016"))
        with self.assertRaisesRegex(ValueError, "startup network options"):
            await embedded.EmbeddedEngine.acquire(self.work, self.client, role="client")
        self.startup.return_value = client
        self.frames = [self.frame(1, self.client), self.frame(2, self.client)]
        engine = await embedded.EmbeddedEngine.acquire(self.work, self.client, role="client")
        self.assertEqual(engine.bridge.directory, self.bridge_path(self.client))
        self.assertIsNone(engine.internal_port)

    async def test_refuses_wrong_process_executable_or_role_mailbox_before_access(self):
        original = self.startup.return_value
        wrong = self.work / "other.exe"
        wrong.touch()
        cases = (
            replace(original, pid=original.pid + 1),
            replace(original, image=wrong),
            replace(original, argv=(str(wrong), *original.argv[1:])),
            replace(original, environment={**original.environment, embedded.BRIDGE_ENV: str(self.bridge_path(self.client))}),
            replace(original, environment={**original.environment, embedded.OPT_IN_ENV: "0"}),
            replace(original, environment={**original.environment, "SILVERFISH_COOP_NATIVE_ZONE": None}),
        )
        for startup in cases:
            with self.subTest(startup=startup), self.assertRaises(ValueError):
                self.startup.return_value = startup
                await self.host()
        self.bridge_type.assert_not_called()
        self.startup.return_value = original
        with self.assertRaisesRegex(ValueError, "private co-op port"):
            await self.host(0)

    async def test_refuses_ambiguous_or_unbound_network_and_profile_flags(self):
        original = self.startup.return_value
        invalid = (
            (*original.argv, "-UserDir=C:\\Other"),
            (*original.argv, "/port=28016"),
            (*original.argv, '-ini:Something:"Port=28016"'),
            (original.argv[0], f"-userdir={self.client / 'game-user'}", *original.argv[2:]),
            (*original.argv, "-MULTIHOME=0.0.0.0"),
            (*original.argv, "-port=28016"),
            original.argv[:-1],
        )
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.startup.return_value = replace(original, argv=args)
                await self.host()
        self.bridge_type.assert_not_called()

    async def test_resident_title_seat_and_campaign_are_independent(self):
        role = self.campaign
        seat = self.work / "titles" / "Player"
        profile = seat / "game-user"
        (profile / "Saved").mkdir(parents=True)
        source = self.process_spec(role)
        startup = replace(source, argv=(source.argv[0], f"-userdir={profile}"))
        with self.assertRaisesRegex(ValueError, "isolated user directory"):
            embedded._qualify(startup, self.work, role, "host", {})

        flags = {"SILVERFISH_COOP_RESIDENT_TITLE": "1"}
        self.startup.return_value = replace(startup, environment={**startup.environment, **flags})
        self.frames = [self.frame(1, seat), self.frame(2, seat)]
        engine = await embedded.EmbeddedEngine.acquire(
            self.work, role, role="host", internal_port=28016, required_env=flags)
        self.assertEqual(engine.session_directory, role)
        self.assertEqual(engine.profile, profile)
        self.assertEqual(engine.bridge.directory, self.bridge_path(role))

    def test_installed_title_qualifies_the_stock_profile_without_launcher_arguments(self):
        game = Path(self.enterContext(TemporaryDirectory())).resolve()
        image = game / runtime.SHIPPING
        image.parent.mkdir(parents=True)
        image.touch()
        runtime.write_json(game / "silverfish-coop-stage.json", {"schema": 1, "game_build": "b" * 64})
        title = self.work / "titles" / "Player"
        bridge = self.bridge_path(title)
        bridge.mkdir(parents=True)
        profile = self.work / "account" / "SilverFish"
        (profile / "Saved").mkdir(parents=True)
        flags = {embedded.UNIFIED_ENV: "1", embedded.INSTALLED_ENV: "1", "SILVERFISH_COOP_RESIDENT_TITLE": "1"}
        startup = embedded._Startup(os.getpid(), image, (str(image),), {
            **flags, embedded.BRIDGE_ENV: str(bridge), embedded.OPT_IN_ENV: "1",
        })
        with runtime.installed_content(self.work, game), patch.object(embedded, "_stock_profile", return_value=profile):
            self.assertEqual(embedded._qualify(startup, self.work, title, "title", flags), (profile / "Saved", bridge))
            override = replace(startup, argv=(str(image), f"-userdir={self.campaign / 'game-user'}"))
            with self.assertRaisesRegex(ValueError, "stock player directory"):
                embedded._qualify(override, self.work, title, "title", flags)

    async def test_effective_directory_other_than_os_userdir_fails_closed(self):
        self.frames = [self.frame(1, self.client)]
        with self.assertRaisesRegex(ValueError, "effective Unreal save directory"):
            await self.host()
        with self.assertRaisesRegex(RuntimeError, "already used"):
            await self.host()

    @unittest.skipUnless(os.name == "nt", "Uses a Windows directory junction")
    async def test_savegames_junction_is_refused_before_using_the_bridge(self):
        import _winapi

        destination = self.client / "game-user" / "Saved"
        junction = self.campaign / "game-user" / "Saved" / "SaveGames"
        _winapi.CreateJunction(str(destination), str(junction))
        self.addCleanup(junction.rmdir)
        with self.assertRaisesRegex(ValueError, "reparse point"):
            await self.host()
        self.bridge_type.assert_not_called()
        self.assertTrue(destination.is_dir())

    async def test_requires_two_strictly_advancing_stock_frames_with_current_effective_receipt(self):
        stale = self.frame(1)
        stale["sequence"] = 2
        moved = self.frame(2)
        moved["mode"] = "host"
        self.frames = [self.frame(1), moved, stale, self.frame(2), self.frame(2)]
        with self.assertRaises(TimeoutError):
            await self.host()

    async def test_retirement_timeout_is_terminal_without_stopping_process(self):
        engine = await self.host()
        engine.bridge.frames.append(self.frame(3))
        with self.assertRaises(TimeoutError):
            await engine.release(timeout=1)
        self.assertEqual(engine.poll(), 1)
        self.assertEqual(engine.bridge.commands, [("leave", (), 1)])
        with self.assertRaisesRegex(RuntimeError, "not active"):
            await engine.release()

    @unittest.skipUnless(os.name == "nt", "Uses a Windows directory junction")
    async def test_nested_config_junction_added_before_release_refuses_stock_leave(self):
        import _winapi

        engine = await self.host()
        destination = self.client / "game-user" / "Saved"
        config = self.campaign / "game-user" / "Saved" / "Config"
        config.mkdir()
        junction = config / "Windows"
        _winapi.CreateJunction(str(destination), str(junction))
        self.addCleanup(junction.rmdir)
        with self.assertRaisesRegex(ValueError, "reparse point"):
            await engine.release()
        self.assertEqual(engine.returncode, 1)
        self.assertEqual(engine.bridge.commands, [])

    async def test_concurrent_release_cannot_issue_a_second_leave(self):
        engine = await self.host()
        engine.bridge.blocked = asyncio.Event()
        engine.bridge.frames.append(self.frame(4))
        pending = asyncio.create_task(engine.release())
        await asyncio.sleep(0)
        try:
            with self.assertRaisesRegex(RuntimeError, "not active"):
                await engine.release()
            self.assertEqual(len(engine.bridge.commands), 1)
        finally:
            engine.bridge.blocked.set()
            await pending


class WindowsStartupTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows startup proof")
    def test_reads_the_current_os_process_without_a_subprocess(self):
        startup = embedded._current_startup(("SystemRoot",))
        self.assertEqual(startup.pid, os.getpid())
        self.assertTrue(startup.image.is_absolute() and startup.image.is_file())
        self.assertTrue(startup.argv)
        self.assertEqual(startup.environment["SystemRoot"], os.environ["SystemRoot"])

    def test_preflight_allows_a_fresh_or_regular_private_saved_tree(self):
        workspace = Path(self.enterContext(TemporaryDirectory())).resolve()
        profile = workspace / "roles" / "test" / "game-user"
        embedded.assert_private_saved_tree(profile)
        self.assertFalse(profile.exists())
        profile.mkdir(parents=True)
        embedded.assert_private_saved_tree(profile)
        slot = profile / "Saved" / "SaveGames" / "Config.sav"
        slot.parent.mkdir(parents=True)
        slot.write_bytes(b"local staged settings")
        embedded.assert_private_saved_tree(profile)
        self.assertEqual(slot.read_bytes(), b"local staged settings")
