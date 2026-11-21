from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from tools.silverfish import embedded_engine, embedded_startup as startup, runtime, service
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.travel import OBSERVATORY


BUILD = "f" * 64
GENERATION = "a" * 32
REQUEST_ID = "b" * 32
BRIDGE_ID = "d" * 32
PUBLIC_PORT, PRIVATE_PORT = 27700, 27701
HOST = startup.HostSelection("127.0.0.1", PUBLIC_PORT, PRIVATE_PORT, "wanderer")


def encode(request, **overrides):
    return json.dumps({"schema": 1, **asdict(request), **overrides}).encode("utf-8")


class StartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.work = Path(self.enterContext(TemporaryDirectory())).resolve(strict=True)
        self.shipping = self.work / "stage" / runtime.SHIPPING
        self.shipping.parent.mkdir(parents=True)
        self.shipping.touch()
        runtime.write_json(self.work / "stage" / "silverfish-coop-stage.json",
                           {"schema": 1, "game_build": "a" * 64})
        self.host = self.work / "campaigns/Coop"
        self.client = self.work / "clients/Guest"
        for directory in (self.host, self.client):
            self.path(directory).mkdir(parents=True)
            (directory / "game-user/Saved").mkdir(parents=True)
        self.source = self.work / "partner.invite.json"
        self.character = str(uuid.uuid4())
        self.invite = {
            "schema": 1, "host": "127.0.0.1", "port": PUBLIC_PORT, "certificate": "mock-certificate",
            "guest_id": str(uuid.uuid4()), "credential": "private-guest-credential",
            "character_id": self.character, "game_build": BUILD, "protocol_version": service.PROTOCOL,
            "native_zones": True,
        }
        runtime.write_json(self.source, self.invite)

        self.enterContext(patch.object(runtime, "staged_build", return_value=BUILD))
        self.routes = self.enterContext(patch.object(runtime, "qualified_authored_routes", return_value=True))
        self.boot = self.enterContext(patch.object(startup, "_current_startup"))
        self.host_game = self.enterContext(patch.object(service, "host_game", new_callable=AsyncMock))
        self.join_game = self.enterContext(patch.object(service, "join_game", new_callable=AsyncMock))

    def path(self, directory):
        return directory / "run/game-bridge" / BRIDGE_ID

    def request(self, directory, selection):
        role = "host" if directory == self.host else "client"
        return startup.StartupRequest(role, GENERATION, REQUEST_ID, BRIDGE_ID, os.getpid(), BUILD,
                                      directory.name, selection)

    def join_request(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        return self.request(self.client, startup.JoinSelection(digest, self.character))

    def process(self, directory, **extra):
        argv = [str(self.shipping), f"-userdir={directory / 'game-user'}"]
        if directory == self.client:
            argv.append("-MULTIHOME=127.0.0.1")
        env = {embedded_engine.OPT_IN_ENV: "1", embedded_engine.BRIDGE_ENV: str(self.path(directory)),
               startup.STARTUP_ENV: BRIDGE_ID}
        self.boot.return_value = embedded_engine._Startup(os.getpid(), self.shipping, tuple(argv), env | extra)
        return self.boot.return_value

    def test_typed_parser_rejects_coercion_extra_fields_and_illegal_selectors(self):
        request = self.request(self.host, HOST)
        self.assertEqual(startup.parse_request(encode(request)), request)
        self.assertEqual(startup.parse_request(encode(self.join_request())), self.join_request())
        cases = [
            {"schema": True}, {"pid": True}, {"role": "guest"}, {"bridge_id": "0" * 32},
            {"title_generation": GENERATION.upper()}, {"profile": r"../SinglePlayer"}, {"profile": "NUL"},
            {"command": "not-a-game-option"}, {"selection": asdict(HOST) | {"private_port": PUBLIC_PORT}},
            {"selection": asdict(HOST) | {"public_port": "27700"}}, {"selection": asdict(HOST) | {"bind": "localhost"}},
            {"selection": asdict(HOST) | {"start": "arbitrary-map"}}, {"selection": asdict(HOST) | {"fixtures": True}},
        ]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                startup.parse_request(encode(request, **fields))
        row = encode(request)
        with self.assertRaisesRegex(ValueError, "Repeated"):
            startup.parse_request(b'{"pid":1,' + row[1:])
        with self.assertRaisesRegex(ValueError, "record size"):
            startup.parse_request(row + b" " * startup.REQUEST_LIMIT)
        with self.assertRaises(ValueError):
            startup.parse_request(encode(self.join_request(), selection={"invite_sha256": BUILD,
                                                                        "character_id": "bad"}))

    async def test_host_uses_real_call_contract_and_one_use_claim(self):
        request = self.request(self.host, HOST)
        bridge = self.path(self.host)
        self.process(self.host)
        startup.publish(bridge, request)
        self.assertFalse(startup.accepted(bridge, request))
        await startup._run()
        self.host_game.assert_awaited_once_with(
            self.work, self.host, "127.0.0.1", PUBLIC_PORT, False, embedded_port=PRIVATE_PORT,
            max_guests=4,
            native_events=True, native_guest=True, native_zones=True, native_zone_walls=True,
            expanded_zones=True, start_preset="wanderer",
        )
        self.join_game.assert_not_awaited()
        self.assertTrue((bridge / startup.CLAIM_FILE).is_file())
        self.assertFalse((bridge / startup.REQUEST_FILE).exists())
        self.assertTrue(startup.accepted(bridge, request))
        self.assertFalse(startup.accepted(bridge, replace(request, title_request_id="c" * 32)))
        self.assertFalse(startup.accepted(bridge, replace(request, pid=request.pid + 1)))
        self.assertFalse(startup.accepted(bridge, replace(request, profile="Untrusted")))
        with self.assertRaisesRegex(FileExistsError, "already been claimed"):
            await startup._run()
        with self.assertRaisesRegex(FileExistsError, "already has a request"):
            startup.publish(bridge, request)

    async def test_unified_title_arms_and_passes_qualified_gameplay_to_host(self):
        capabilities = {
            "native_shop_view": "SILVERFISH_COOP_NATIVE_SHOP_VIEW",
            "guest_regen": "SILVERFISH_COOP_NATIVE_GUEST_REGEN",
            "guest_sickness": "SILVERFISH_COOP_NATIVE_GUEST_SICKNESS",
            "guest_mre": "SILVERFISH_COOP_NATIVE_GUEST_MRE",
            "guest_held_fire": "SILVERFISH_COOP_NATIVE_GUEST_HELD_FIRE",
            "native_dead_bridge_enemy_loot": "SILVERFISH_COOP_NATIVE_CQC_LOOT",
        }
        with (patch.object(service, "native_death_backend", return_value="cpp"),
              patch.object(runtime, "native_death_backend", return_value="cpp"),
              patch.object(service, "staged_script", return_value=True),
              patch.object(service, "staged_guest_controls", return_value=True),
              patch.object(service, "menu_host_start", return_value=("wanderer", OBSERVATORY)),
              patch.object(startup, "EmbeddedEngine") as engine,
              patch.object(runtime, "spare_udp_port", return_value=PRIVATE_PORT)):
            engine.return_value.observe_title = AsyncMock()
            host = service.unified_host_options(self.work)
            title = service.unified_title_options(self.work, host_options=host)
            environment = runtime.game_environment(self.work, False, embedded_engine=True, resident_title=True,
                                                   resident_direct_join=True, unified_title=True,
                                                   embedded_startup=BRIDGE_ID, base_env={}, **title)
            self.assertEqual({name: host[name] for name in capabilities}, dict.fromkeys(capabilities, True))
            self.assertEqual({key: environment[key] for key in capabilities.values()},
                             dict.fromkeys(capabilities.values(), "1"))
            plan = startup._resident_host_plan(self.work, self.host, self.path(self.host),
                                               self.host / "game-user/Saved", False,
                                               "127.0.0.1", PUBLIC_PORT, host_options=host)
            await plan.resolve_claim(SimpleNamespace(host_settings=None)).start()
            self.host_game.assert_awaited_once_with(
                self.work, self.host, "127.0.0.1", PUBLIC_PORT, False, resident_engine=engine.return_value,
                direct_password="", stock_creator=False, resume_existing=False,
                max_guests=service.MENU_HOST_GUEST_LIMIT, start_preset="wanderer", **host,
            )

    async def test_existing_host_uses_resume_path(self):
        request = self.request(self.host, replace(HOST, start="resume"))
        with CampaignStore(self.host / "save") as store:
            store.checkpoint({"map": OBSERVATORY}, {})
        self.process(self.host)
        startup.publish(self.path(self.host), request)
        await startup._run()
        self.host_game.assert_awaited_once()
        self.assertNotIn("start_preset", self.host_game.await_args.kwargs)
        self.assertEqual(self.host_game.await_args.kwargs["max_guests"], 4)

    async def test_aborted_first_launch_still_starts_a_fresh_wanderer_campaign(self):
        self.assertEqual(service.menu_host_start(self.host), ("wanderer", OBSERVATORY))
        self.assertFalse((self.host / "save").exists())
        with CampaignStore(self.host / "save") as store:
            self.assertEqual(store.status()["world"], {})
        self.process(self.host)
        startup.publish(self.path(self.host), self.request(self.host, HOST))
        await startup._run()
        self.assertEqual(self.host_game.await_args.kwargs["start_preset"], "wanderer")

    async def test_passes_the_verified_offscreen_launch_to_the_real_service(self):
        boot = self.process(self.host, SILVERFISH_COOP_HEADLESS="1")
        self.boot.return_value = replace(boot, argv=(*boot.argv, "-nullrhi", "-RenderOffScreen"))
        startup.publish(self.path(self.host), self.request(self.host, HOST))
        await startup._run()
        self.assertIs(self.host_game.await_args.args[4], True)

    async def test_join_pins_private_invitation_and_forwards_exact_choice(self):
        request = self.join_request()
        bridge = self.path(self.client)
        self.process(self.client)
        startup.publish(bridge, request, invitation=self.source)
        self.source.write_bytes(b"Source changed after selection")

        async def use(work, directory, path, headless, character_id, *, embedded_engine):
            self.assertEqual((work, directory, headless, character_id, embedded_engine),
                             (self.work, self.client, False, self.character, True))
            self.assertEqual(path, bridge / startup.INVITE_FILE)
            self.assertEqual(service.load_invite(path, BUILD), self.invite)
            with self.assertRaises(PermissionError):
                path.write_bytes(b"Change while the game may read")
            with self.assertRaises(PermissionError):
                path.unlink()
            self.assertTrue(startup.accepted(bridge, request))

        self.join_game.side_effect = use
        await startup._run()
        self.join_game.assert_awaited_once()
        self.host_game.assert_not_awaited()
        self.assertNotIn(self.invite["credential"].encode(), (bridge / startup.ACCEPTED_FILE).read_bytes())
        self.assertNotIn(str(self.source).encode(), (bridge / startup.CLAIM_FILE).read_bytes())

    async def test_wrong_role_pid_nonce_profile_and_build_never_dispatch(self):
        request = self.request(self.host, HOST)
        bridge = self.path(self.host)
        self.process(self.host)
        for wrong in (
            replace(request, role="client", selection=self.join_request().selection),
            replace(request, pid=request.pid + 1), replace(request, bridge_id="e" * 32),
            replace(request, profile="Other"), replace(request, game_build="e" * 64),
        ):
            with self.subTest(wrong=wrong):
                (bridge / startup.REQUEST_FILE).write_bytes(encode(wrong))
                with self.assertRaisesRegex(ValueError, "identify this role|qualified staged build"):
                    await startup._run()
                self.assertFalse(startup.accepted(bridge, wrong))
                (bridge / startup.CLAIM_FILE).unlink()
        self.host_game.assert_not_awaited()
        self.join_game.assert_not_awaited()

    async def test_opt_in_image_startup_network_flags_and_start_condition_fail_closed(self):
        bridge = self.path(self.host)
        request = self.request(self.host, HOST)
        startup.publish(bridge, request)
        self.process(self.host, **{startup.STARTUP_ENV: "0" * 32})
        with self.assertRaisesRegex(ValueError, "not opted"):
            await startup._run()
        process = self.process(self.host)
        self.boot.return_value = replace(process, image=self.shipping.with_name("SilverFish.exe"))
        with self.assertRaisesRegex(ValueError, "exact staged executable"):
            await startup._run()
        self.assertTrue((bridge / startup.REQUEST_FILE).is_file())
        self.boot.return_value = replace(process, argv=(*process.argv, f"-port={PRIVATE_PORT}"))
        with self.assertRaisesRegex(ValueError, "startup network options"):
            await startup._run()
        self.assertFalse(startup.accepted(bridge, request))
        (bridge / startup.CLAIM_FILE).unlink()
        self.process(self.host)
        with CampaignStore(self.host / "save") as store:
            store.checkpoint({"map": OBSERVATORY}, {})
        startup.publish(bridge, request)
        with self.assertRaisesRegex(ValueError, "campaign changed"):
            await startup._run()
        self.assertFalse(startup.accepted(bridge, request))
        self.host_game.assert_not_awaited()

    async def test_join_rejects_edited_snapshot_or_invite_without_native_mode(self):
        bridge = self.path(self.client)
        self.process(self.client)
        request = self.join_request()
        startup.publish(bridge, request, invitation=self.source)
        (bridge / startup.INVITE_FILE).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            await startup._run()
        (bridge / startup.CLAIM_FILE).unlink()
        (bridge / startup.INVITE_FILE).unlink()
        runtime.write_json(self.source, self.invite | {"native_zones": False})
        fresh = self.join_request()
        startup.publish(bridge, fresh, invitation=self.source)
        with self.assertRaisesRegex(ValueError, "does not offer embedded"):
            await startup._run()
        self.join_game.assert_not_awaited()
        self.assertFalse(startup.accepted(bridge, fresh))

    async def test_no_record_times_out_without_ready_or_service(self):
        self.process(self.host, port=PRIVATE_PORT)
        with self.assertRaises(TimeoutError):
            await startup._run(timeout=.01)
        self.host_game.assert_not_awaited()

    def test_join_publish_rejects_wrong_workspace_or_changed_source(self):
        bridge = self.path(self.client)
        request = self.join_request()
        with self.assertRaisesRegex(ValueError, "private co-op workspace"):
            startup.publish(bridge, request, invitation=self.work.parent / self.source.name)
        self.source.write_bytes(b"The previously inspected selection changed")
        with self.assertRaisesRegex(ValueError, "selected invitation changed"):
            startup.publish(bridge, request, invitation=self.source)
        self.assertFalse((bridge / startup.INVITE_FILE).exists())
        self.assertFalse((bridge / startup.REQUEST_FILE).exists())

    @unittest.skipUnless(os.name == "nt", "Uses a Windows directory junction")
    async def test_role_bridge_refuses_reparse_point_before_claim(self):
        import _winapi

        request = self.request(self.host, HOST)
        bridge = self.path(self.host)
        foreign = self.work / "unowned"
        target = foreign / BRIDGE_ID
        target.mkdir(parents=True)
        bridge.rmdir()
        bridge.parent.rmdir()
        _winapi.CreateJunction(str(foreign), str(bridge.parent))
        self.addCleanup(bridge.parent.rmdir)
        self.process(self.host, port=PRIVATE_PORT)
        (target / startup.REQUEST_FILE).write_bytes(encode(request))
        with self.assertRaisesRegex(ValueError, "reparse point"):
            startup.publish(bridge, request)
        with self.assertRaisesRegex(ValueError, "reparse point"):
            await startup._run()
        self.assertFalse((target / startup.CLAIM_FILE).exists())
        self.host_game.assert_not_awaited()
