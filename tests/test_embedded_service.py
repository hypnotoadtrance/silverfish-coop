import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from test_title_handoff import ready
from tools.silverfish import embedded_engine as embedded, runtime, service, win_job
from tools.silverfish.native_title_entry import MAP as TITLE_MAP
from tools.silverfish.travel import OBSERVATORY


class StockBridge:
    def __init__(self, directory, process, profile, events):
        self.directory = directory
        self.process = process
        self.profile = profile
        self.events = events
        self.sequence = 0
        self.state = "ready"
        self.save_scope = None
        self.local_bridge = None

    def snapshot(self):
        self.sequence += 1
        if self.state == "ready":
            frame = ready(self.sequence)
            frame["save_scope"] = self.save_scope
            frame[embedded.STARTUP_KEY] = {
                "map": TITLE_MAP, "sequence": self.sequence, "saved_directory": str(self.profile / "Saved"),
            }
            return frame
        return {"state": self.state, "sequence": self.sequence, "map": OBSERVATORY, "pawns": {}}

    async def wait_for(self, condition, timeout=30):
        for _ in range(6):
            snapshot = self.snapshot()
            if condition(snapshot):
                return snapshot
        raise TimeoutError("Offline game bridge never became ready")

    async def command(self, action, *arguments, timeout=8):
        self.events.append((action, *arguments))
        if action in ("host", "join", "leave"):
            self.state = {"host": "hosting", "join": "connected", "leave": "ready"}[action]
        elif action == "native-save-scope-select":
            self.save_scope, = arguments
        elif action == "native-save-scope-release":
            assert self.state == "ready" and arguments == (self.save_scope,)
            self.save_scope = None
        return self.snapshot()


class EmbeddedServiceTests(unittest.IsolatedAsyncioTestCase):
    raw_port, quic_port = 28016, 28017

    def setUp(self):
        self.work = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.campaign = self.work / "campaigns" / "Host"
        self.client = self.work / "clients" / "Guest"
        self.stage_exe = self.work / "stage" / runtime.SHIPPING
        self.stage_exe.parent.mkdir(parents=True)
        self.stage_exe.touch()
        runtime.write_json(self.work / "stage" / "silverfish-coop-stage.json",
                           {"schema": 1, "game_build": "a" * 64})
        self.invite_path = self.work / "private-invitation.json"
        self.character = str(uuid.uuid4())
        runtime.write_json(self.invite_path, {
            "schema": 1, "host": "127.0.0.1", "port": self.quic_port, "certificate": "fixture",
            "guest_id": str(uuid.uuid4()), "credential": "fixture", "character_id": self.character,
            "game_build": "test-build", "protocol_version": service.PROTOCOL, "native_zones": True,
        })
        self.events = []
        self.enterContext(patch.object(embedded.EmbeddedEngine, "_used", False))
        self.enterContext(patch.object(service, "staged_build", return_value="test-build"))
        self.enterContext(patch.object(service, "native_death_backend", return_value="cpp"))
        self.enterContext(patch.object(runtime, "native_death_backend", return_value="cpp"))
        self.enterContext(patch.object(service, "qualified_authored_routes", return_value=True))
        self.enterContext(patch.object(service, "staged_guest_controls", return_value=False))
        scripts = ("guest_save.lua", "native_host_save.lua", "native_save_scope.lua")
        self.enterContext(patch.object(service, "staged_script", side_effect=lambda work, name, *_: name in scripts))
        self.enterContext(patch.object(runtime, "staged_script", side_effect=lambda work, name, *_: name in scripts))
        self.enterContext(patch.object(service, "_identity", return_value=object()))
        self.launch = self.enterContext(patch.object(service, "launch", side_effect=AssertionError("Embedded game relaunched")))
        self.kill = self.enterContext(patch.object(service, "stop_game", side_effect=AssertionError("Embedded process stopped")))
        self.enterContext(patch.object(service, "spare_udp_port", side_effect=AssertionError("Embedded port replaced")))
        self.observe_udp = self.enterContext(patch.object(service, "owns_udp_port", return_value=False))

    def simulate_boot(self, directory, *, host):
        profile = directory / "game-user"
        (profile / "Saved").mkdir(parents=True)
        mailbox = directory / "run" / "game-bridge" / ("b" * 32)
        mailbox.mkdir(parents=True)
        argv = [str(self.stage_exe), f"-userdir={profile}"]
        if not host:
            argv.append("-MULTIHOME=127.0.0.1")
        flags = runtime.game_environment(self.work, True, self.raw_port if host else None,
                                         native_events=host, native_guest=host, native_zones=True, embedded_engine=True)
        env = {key: value for key, value in flags.items() if key.startswith("SILVERFISH_COOP_")}
        env[embedded.BRIDGE_ENV] = str(mailbox)
        startup = embedded._Startup(os.getpid(), self.stage_exe, tuple(argv), env)
        self.enterContext(patch.object(embedded, "_current_startup", return_value=startup))
        self.enterContext(patch.object(runtime, "Bridge", side_effect=lambda path, engine: self.create_bridge(
            path, engine, profile,
        )))
        return mailbox

    def create_bridge(self, directory, process, profile):
        self.bridge = StockBridge(directory, process, profile, self.events)
        return self.bridge

    async def host(self):
        await service.host_game(self.work, self.campaign, "127.0.0.1", self.quic_port, True,
                                native_events=True, native_guest=True, native_zones=True,
                                start_zone=OBSERVATORY, embedded_port=self.raw_port)

    async def join(self, character=None):
        await service.join_game(self.work, self.client, self.invite_path, True, character, embedded_engine=True)

    def transport(self, port):
        async def close():
            self.events.append(("transport-closed", port))

        return SimpleNamespace(address=("127.0.0.1", port), close=AsyncMock(side_effect=close))

    @unittest.skipUnless(os.name == "nt", "Uses a Windows directory junction")
    def test_embedded_runtime_checks_nested_save_paths_before_starting_process(self):
        import _winapi

        profile = self.campaign / "game-user"
        saved = profile / "Saved"
        saved.mkdir(parents=True)
        foreign = self.work / "other-profile"
        foreign.mkdir()
        junction = saved / "SaveGames"
        _winapi.CreateJunction(str(foreign), str(junction))
        self.addCleanup(junction.rmdir)
        mailbox = self.campaign / "run" / "game-bridge" / ("b" * 32)
        with patch.object(win_job, "GameProcess", side_effect=AssertionError("Process started before profile check")) as process:
            with self.assertRaisesRegex(ValueError, "reparse point"):
                runtime.launch(self.work, profile, mailbox, True, self.raw_port,
                               native_events=True, native_guest=True, native_zones=True, embedded_engine=True)
            process.assert_not_called()

    async def test_host_netdriver_and_quic_use_different_ports_and_teardown_without_saving(self):
        mailbox = self.simulate_boot(self.campaign, host=True)
        transport = self.transport(self.quic_port)
        owns_port = iter((True, False))

        def observe_closed_socket(pid, port):
            self.assertEqual((pid, port), (os.getpid(), self.raw_port))
            self.assertEqual(self.bridge.state, "ready")
            return next(owns_port)

        self.observe_udp.side_effect = observe_closed_socket

        def verify_socket(pid, port):
            self.assertEqual((pid, port), (os.getpid(), self.raw_port))
            self.assertEqual(self.bridge.state, "hosting")
            self.events.append(("verify-raw", port))

        async def start_transport(bind, port, relay, internal, *_callbacks, **_options):
            self.assertEqual((bind, port, relay, internal), ("127.0.0.1", self.quic_port, "127.0.0.1", self.raw_port))
            self.events.append(("quic", port))
            return transport

        async def run(controller, bridge, connection):
            self.assertIs(bridge, self.bridge)
            self.assertIs(connection, transport)
            self.assertEqual(bridge.directory, mailbox)
            self.assertEqual(controller.internal_port, self.raw_port)
            self.assertEqual(controller.native_profile.id, self.bridge.save_scope)
            self.events.append(("run",))

        with (patch.object(service, "verify_loopback_socket", side_effect=verify_socket),
              patch.object(service.link, "start_host", side_effect=start_transport),
              patch.object(service.Host, "run", new=run),
              patch.object(service.Host, "checkpoint_native", new_callable=AsyncMock) as checkpoint):
            await self.host()
            checkpoint.assert_not_awaited()
        status = runtime.read_json(self.campaign / "run" / "status.json")
        scope = uuid.UUID(status["campaign"]["campaign_id"]).hex
        self.assertEqual(self.events, [
            ("native-save-scope-select", scope),
            ("host", OBSERVATORY, f"listener={self.raw_port}"), ("verify-raw", self.raw_port),
            ("quic", self.quic_port), ("run",),
            ("native-guest-clear",), ("transport-closed", self.quic_port), ("leave",),
            ("native-save-scope-release", scope),
        ])
        self.assertEqual(self.bridge.process.returncode, 0)
        self.assertEqual(status["state"], "stopped")
        self.assertEqual(self.observe_udp.call_count, 2)
        self.launch.assert_not_called()
        self.kill.assert_not_called()

    async def test_guest_uses_selected_character_and_stock_leave_without_relaunch(self):
        mailbox = self.simulate_boot(self.client, host=False)
        chosen = str(uuid.uuid4())
        transport = self.transport(28018)

        async def connect(client, *, queue):
            self.assertTrue(queue)
            self.assertEqual(client.invite["character_id"], chosen)
            self.assertEqual(client.invite["port"], self.quic_port)
            self.assertEqual(client.bridge.directory, mailbox)
            self.events.append(("admit", chosen))

        async def open_game_link(client):
            await client.bridge.command("join")

        async def run(client):
            self.assertEqual(client.bridge.state, "connected")
            self.events.append(("run",))

        with (patch.object(service.link, "start_client", return_value=transport),
              patch.object(service.Client, "connect", new=connect),
              patch.object(service.Client, "open_game_link", new=open_game_link),
              patch.object(service.Client, "run", new=run)):
            await self.join(chosen)
        scope = uuid.UUID(chosen).hex
        self.assertEqual(self.events, [("native-save-scope-select", scope), ("admit", chosen), ("join",), ("run",),
                                       ("transport-closed", 28018), ("leave",), ("native-save-scope-release", scope)])
        self.assertEqual(self.bridge.process.returncode, 0)
        status = runtime.read_json(self.client / "run" / "status.json")
        self.assertEqual((status["state"], status["character_id"]), ("stopped", chosen))
        self.observe_udp.assert_not_called()
        self.launch.assert_not_called()
        self.kill.assert_not_called()

    async def test_failed_acquisition_never_starts_host_or_guest_transport(self):
        for role, start in (("host", self.host), ("client", self.join)):
            with (self.subTest(role=role),
                  patch.object(embedded.EmbeddedEngine, "acquire", side_effect=ValueError("invalid embedded startup")) as acquire,
                  patch.object(service.link, "start_host") as host_transport,
                  patch.object(service.link, "start_client") as client_transport):
                with self.assertRaisesRegex(ValueError, "invalid embedded startup"):
                    await start()
                acquire.assert_awaited_once()
                host_transport.assert_not_called()
                client_transport.assert_not_called()
        self.launch.assert_not_called()
        self.kill.assert_not_called()

    async def test_host_raw_socket_refusal_leaves_without_publishing_quic(self):
        self.simulate_boot(self.campaign, host=True)
        with (patch.object(service, "verify_loopback_socket", side_effect=RuntimeError("raw socket declined")),
              patch.object(service.link, "start_host") as start):
            with self.assertRaisesRegex(RuntimeError, "raw socket declined"):
                await self.host()
        start.assert_not_called()
        self.assertEqual(self.events[-2], ("leave",))
        self.assertEqual(self.events[-1][0], "native-save-scope-release")
        self.assertEqual(self.bridge.process.returncode, 0)
        self.assertEqual(runtime.read_json(self.campaign / "run" / "status.json")["state"], "failed")
        self.launch.assert_not_called()
        self.kill.assert_not_called()

    async def test_host_does_not_report_clean_stop_when_udp_closure_cannot_be_read(self):
        self.simulate_boot(self.campaign, host=True)
        transport = self.transport(self.quic_port)
        self.observe_udp.side_effect = OSError("UDP owner table unavailable")
        with (patch.object(service, "verify_loopback_socket"),
              patch.object(service.link, "start_host", return_value=transport),
              patch.object(service.Host, "run", new_callable=AsyncMock),
              patch.object(service.Host, "checkpoint_native", new_callable=AsyncMock)):
            with self.assertRaisesRegex(OSError, "UDP owner table unavailable"):
                await self.host()
        self.assertEqual(self.events[-3:-1], [("transport-closed", self.quic_port), ("leave",)])
        self.assertEqual(self.events[-1][0], "native-save-scope-release")
        status = runtime.read_json(self.campaign / "run" / "status.json")
        self.assertEqual((status["state"], status["active"]), ("failed", False))
        self.observe_udp.assert_called_once_with(os.getpid(), self.raw_port)
        self.kill.assert_not_called()
