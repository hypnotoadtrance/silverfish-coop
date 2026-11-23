import asyncio
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch
import uuid

from tools.silverfish import native_guest_consumable, native_guest_ui, native_guest_weapon, party, runtime, service


class NativeGuestControlInvitationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        stage = self.work / "stage"
        game = stage / runtime.SHIPPING
        game.parent.mkdir(parents=True)
        game.write_bytes(b"game fixture")
        hashes = {}
        for key, relative in (("runtime", runtime.CPP_RUNTIME_DLL), ("addon", runtime.CPP_ADDON_DLL)):
            library = game.parent / relative
            library.parent.mkdir(parents=True, exist_ok=True)
            contents = key.encode("ascii")
            library.write_bytes(contents)
            hashes[key] = hashlib.sha256(contents).hexdigest()
        runtime.write_json(stage / "silverfish-coop-stage.json",
                           {"schema": 1, "game_build": "a" * 64, "cpp_death_lab": hashes})
        self.scripts = game.parent / "ue4ss" / "Mods" / "SilverfishCoop" / "Scripts"
        self.scripts.mkdir(parents=True, exist_ok=True)
        (self.scripts / "main.lua").write_text("-- co-op entry point\n", encoding="utf-8")
        for name, marker in (
            ("native_inventory_view.lua", "SilverfishSetNativeInventoryViewScope"),
            ("native_guest_primary_input.lua", "SilverfishSetNativeGuestPrimaryScope"),
            ("native_guest_reload_input.lua", "SilverfishReadNativeGuestReloadIntent"),
            ("native_guest_weapon.lua", "native-guest-equipped-weapon"),
        ):
            (self.scripts / name).write_text(f"-- {marker}\n", encoding="utf-8")
        self.invite_file = self.work / "invite.json"
        self.invite = {
            "schema": 1, "host": "127.0.0.1", "port": 7777, "certificate": "fixture",
            "guest_id": "guest", "credential": "fixture", "character_id": "character",
            "game_build": runtime.staged_build(self.work), "protocol_version": service.PROTOCOL, "native_zones": True,
        }

    async def launch_flags(self, **options):
        runtime.write_json(self.invite_file, self.invite)
        transport = AsyncMock()
        with patch.object(service.link, "start_client", return_value=transport), patch.object(
            service, "launch", side_effect=RuntimeError("offline launch intercepted"),
        ) as launch:
            with self.assertRaisesRegex(RuntimeError, "offline launch intercepted"):
                await service.join_game(self.work, self.work / "client", self.invite_file, False, **options)
        transport.close.assert_awaited_once()
        return launch.call_args.kwargs

    async def test_native_invite_arms_all_three_controls_with_normal_join(self):
        self.invite["native_guest_controls"] = True
        flags = await self.launch_flags()
        self.assertTrue(flags["guest_inventory_view"])
        self.assertTrue(flags["guest_primary_input"])
        self.assertTrue(flags["guest_reload_input"])

    async def test_legacy_zone_invite_keeps_explicit_guest_switches(self):
        flags = await self.launch_flags(guest_reload_input=True)
        self.assertFalse(flags["guest_inventory_view"])
        self.assertFalse(flags["guest_primary_input"])
        self.assertTrue(flags["guest_reload_input"])

    async def test_missing_script_marker_refuses_advertised_controls_before_network_or_launch(self):
        self.invite["native_guest_controls"] = True
        (self.scripts / "native_guest_reload_input.lua").write_text("-- old script\n", encoding="utf-8")
        self.invite["game_build"] = runtime.staged_build(self.work)
        runtime.write_json(self.invite_file, self.invite)
        with patch.object(service.link, "start_client") as network, patch.object(service, "launch") as launch:
            with self.assertRaisesRegex(ValueError, "Restage the native client"):
                await service.join_game(self.work, self.work / "client", self.invite_file, False)
        network.assert_not_called()
        launch.assert_not_called()

    def test_guest_controls_requires_boolean_and_native_zone_invite(self):
        for controls, zones in ((1, True), (True, False)):
            with self.subTest(controls=controls, zones=zones):
                runtime.write_json(self.invite_file, {**self.invite, "native_guest_controls": controls, "native_zones": zones})
                with self.assertRaisesRegex(ValueError, "Invitation"):
                    service.load_invite(self.invite_file, self.invite["game_build"])


class NativeGuestUIOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_view_only_uses_the_authenticated_pawn_and_omits_unchanged_content(self):
        area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
        character, other = str(uuid.uuid4()), str(uuid.uuid4())
        host = SimpleNamespace(last_game={"state": "hosting", "map": area}, zone_epoch="c" * 32,
                               transitioning=False, stopping=False, native_checkpoint_unsafe=False,
                               native_recipients=lambda: {character: {"pawn": "exact-pawn", "connected": True}})
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            contents = f"{area}\t{character}\n".encode()

            async def command(action, pawn, nonce, *, timeout):
                self.assertEqual((action, pawn), (native_guest_ui.ACTION, "exact-pawn"))
                (directory / f"native-guest-ui-{nonce}.tsv").write_bytes(contents)
                return {"state": "hosting", "map": area, "ack": {"message": "captured co-op guest view"}}

            bridge = SimpleNamespace(directory=directory, command=AsyncMock(side_effect=command))
            with self.assertRaises(party.RuleError):
                await native_guest_ui.capture(host, bridge, other, "-")
            bridge.command.assert_not_awaited()
            first = (await native_guest_ui.capture(host, bridge, character, "-"))["native_guest_ui"]
            self.assertEqual(first["owner"], character)
            self.assertIn("payload", first)
            returned = (await native_guest_ui.capture(host, bridge, character, first["digest"]))["native_guest_ui"]
            self.assertEqual(returned, {key: value for key, value in first.items() if key != "payload"})
            self.assertEqual(list(directory.iterdir()), [])

    async def test_projection_reports_settled_quests_and_stock_attachments(self):
        character = str(uuid.uuid4())
        response = {"ack": {"message": "stock co-op inventory and Journal projected 1 1 2 1 3"}}
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bridge = SimpleNamespace(directory=directory, command=AsyncMock(return_value=response))
            client = SimpleNamespace(bridge=bridge, control=Mock())

            await native_guest_ui.GuestView().project(client, character, "fixture\n")

            self.assertIs(client.last_game, response)
            client.control.event.assert_called_once_with(
                "native_guest_ui_projected", items=1, notes=1, quests=2, completed=1, attachments=3,
            )
            self.assertEqual(list(directory.iterdir()), [])


class ResidentGuestUIBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
        self.character = uuid.uuid4()
        self.target = f"Player_FP_C {self.area}.Observitory_P:PersistentLevel.Player_FP_C_1"
        self.body = f"{self.area}\t{self.character}\ninventory-layout\t0\t0\t-1\ncash\t0\n".encode("ascii")
        self.bridge = runtime.Bridge(Path("unused-resident-fixture"), None)
        self.bridge.local_bridge = SimpleNamespace(command=AsyncMock())
        self.prior = self.bridge._local_status

    def reply(self, request, *, hosting=True, accepted=True, message=None, nonce=None):
        nonce = nonce or request.partition(b"\t")[0].decode("ascii")
        seat = "host\thosting" if hosting else "client\tconnected"
        result = "accepted" if accepted else "error"
        message = message or ("captured co-op guest view" if hosting else
                              "stock co-op inventory and Journal projected 0 0 0 0 0")
        return (f"schema\t1\nseq\t1\nack\t{nonce}\t{result}\t{message}\n"
                f"session\t{seat}\t{self.area}\t0\t0\n"
                f"native-save-scope\t{self.character.hex}\nnative-guest-ui-memory\t1\n")

    async def test_exact_capture_body_does_not_enter_periodic_status(self):
        async def command(request, timeout):
            nonce, action, pawn = request.rstrip(b"\n").split(b"\t")
            self.assertEqual((len(nonce), action.decode(), pawn.decode(), timeout),
                             (32, "native-guest-ui-read-memory", self.target, 3))
            return self.reply(request) + f"native-guest-ui-body\t{len(self.body)}\n" + self.body.decode()

        self.bridge.local_bridge.command.side_effect = command
        game, body = await self.bridge.guest_ui_command(self.target, timeout=3)
        self.assertEqual(body, self.body)
        self.assertTrue(game[runtime.GUEST_UI_MEMORY])
        self.assertIs(self.bridge._local_status, game)
        self.assertNotIn("inventory-layout", self.bridge._status_text)

    async def test_exact_projection_body_and_ordinary_command_share_the_existing_bridge(self):
        async def command(request, timeout):
            header, _, body = request.partition(b"\n")
            _, action, target = header.split(b"\t")
            if action == b"native-guest-ui-project-memory":
                self.assertEqual(target.decode(), str(self.character))
                self.assertEqual(body, self.body)
                self.assertEqual(timeout, 4)
                return self.reply(request, hosting=False)
            self.assertEqual((action, target, body), (b"native-test", b"ordinary", b""))
            return self.reply(request, hosting=False, message="ordinary reply")

        self.bridge.local_bridge.command.side_effect = command
        game, body = await self.bridge.guest_ui_command(str(self.character), payload=self.body)
        self.assertEqual(game["save_scope"], self.character.hex)
        self.assertIsNone(body)
        regular = await self.bridge.command("native-test", "ordinary")
        self.assertEqual(regular["ack"]["message"], "ordinary reply")

    async def test_wrong_ticket_missing_body_bad_length_and_oversize_are_refused(self):
        for error in ("wrong-ticket", "missing", "wrong-size", "oversize", "reserved", "refused"):
            with self.subTest(error=error):
                async def command(request, _):
                    if error == "wrong-ticket":
                        return self.reply(request, nonce="wrong")
                    if error == "refused":
                        return self.reply(request, accepted=False, message="stock UI unavailable")
                    status = self.reply(request)
                    if error == "missing":
                        return status
                    if error == "wrong-size":
                        return status + "native-guest-ui-body\t7\n" + self.body.decode()
                    payload = ("x" * runtime.GUEST_UI_MAX_BYTES + "\n") if error == "oversize" else "bad\r\n"
                    return status + f"native-guest-ui-body\t{len(payload)}\n" + payload

                self.bridge.local_bridge.command.side_effect = command
                with self.assertRaises((RuntimeError, ValueError)):
                    await self.bridge.guest_ui_command(self.target)
                self.assertIs(self.bridge._local_status, self.prior)

        self.bridge.local_bridge.command.reset_mock()
        for payload in (b"", b"x" * runtime.GUEST_UI_MAX_BYTES + b"\n", b"x\n" * 129, b"a\0\n"):
            with self.assertRaises(ValueError):
                await self.bridge.guest_ui_command(str(self.character), payload=payload)
        self.bridge.local_bridge.command.assert_not_awaited()

    async def test_prefix_in_target_and_unsolicited_project_reply_body_are_refused(self):
        with self.assertRaises(ValueError):
            await self.bridge.guest_ui_command(self.target + "\n" + self.body.decode())
        self.bridge.local_bridge.command.assert_not_awaited()
        self.bridge.local_bridge.command.side_effect = lambda request, timeout: (
            self.reply(request, hosting=False) + "native-guest-ui-body\t1\n\n")
        with self.assertRaisesRegex(RuntimeError, "unexpected body"):
            await self.bridge.guest_ui_command(str(self.character), payload=self.body)
        self.assertIs(self.bridge._local_status, self.prior)

    async def test_host_and_guest_view_transfer_do_not_use_temporary_files(self):
        zone = "d" * 32
        owner = str(self.character)
        host_state = {"state": "hosting", "map": self.area, runtime.GUEST_UI_MEMORY: True}
        host = SimpleNamespace(last_game=host_state, zone_epoch=zone, transitioning=False, stopping=False,
                               native_checkpoint_unsafe=False, native_recipients=lambda: {
                                   owner: {"pawn": self.target, "connected": True}})
        host_receipt = {**host_state, "ack": {"message": "captured co-op guest view"}}
        host_bridge = SimpleNamespace(local_bridge=object(), command=AsyncMock(),
                                      guest_ui_command=AsyncMock(return_value=(host_receipt, self.body)))
        result = (await native_guest_ui.capture(host, host_bridge, owner, "-"))["native_guest_ui"]
        host_bridge.guest_ui_command.assert_awaited_once_with(self.target, timeout=3)
        host_bridge.command.assert_not_awaited()
        self.assertEqual(result["digest"], hashlib.sha256(self.body).hexdigest())

        guest_game = {"state": "connected", "map": self.area, "save_scope": self.character.hex,
                      runtime.GUEST_UI_MEMORY: True}
        guest_receipt = {**guest_game, "ack": {"message": "stock co-op inventory and Journal projected 0 0 0 0 0"}}
        guest_bridge = SimpleNamespace(local_bridge=object(), command=AsyncMock(),
                                       guest_ui_command=AsyncMock(return_value=(guest_receipt, None)))
        client = SimpleNamespace(last_game=guest_game, zone_epoch=zone, bridge=guest_bridge, control=Mock())
        with patch.object(runtime, "write_text", side_effect=AssertionError("unexpected disk write")):
            await native_guest_ui.GuestView().receive(client, result, self.area, zone, owner)
        guest_bridge.guest_ui_command.assert_awaited_once_with(owner, payload=self.body)
        guest_bridge.command.assert_not_awaited()
        self.assertIs(client.last_game, guest_receipt)


class NativeGuestStockPickupViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.view = native_guest_ui.GuestView()
        self.addAsyncCleanup(self.view.close)
        self.view.receive = AsyncMock()
        self.character = uuid.uuid4()
        self.area, self.epoch, self.session = "/Game/Test", "e" * 32, "a" * 32
        self.game = {"map": self.area, "state": "connected", "save_scope": self.character.hex,
                     "native_inventory_view": {"state": "stock"}, "pawns": {"local": {"name": "pawn"}}}
        self.client = SimpleNamespace(last_game=self.game, expected_zone=self.area, host_session=self.session,
                                      zone_epoch=self.epoch, invite={"character_id": str(self.character)},
                                      bridge=SimpleNamespace(command=AsyncMock(return_value=self.game)),
                                      control=Mock())
        self.release = asyncio.Event()
        self.amount = 0

        async def fetch(*_):
            self.amount += 1
            await self.release.wait()
            return {"native_guest_ui": {"capture": self.amount}}

        self.client.gameplay_action = AsyncMock(side_effect=fetch)

    async def test_pickups_coalesce_without_waiting_for_stale_inventory_reply(self):
        await self.view.sync(self.client, True)
        await asyncio.sleep(0)
        self.assertEqual(self.amount, 1)
        self.view.queue_stock_inventory(self.client)
        self.view.queue_stock_inventory(self.client)
        await self.view.sync(self.client, True)
        self.assertFalse(self.view.request.done())
        self.view.receive.assert_not_awaited()

        self.release.set()
        await self.view.request
        await self.view.sync(self.client, True)
        await self.view.request
        await self.view.sync(self.client, True)

        self.assertEqual(self.amount, 2)
        self.view.receive.assert_awaited_once_with(self.client, {"capture": 2}, self.area, self.epoch, str(self.character))
        self.client.bridge.command.assert_awaited_once_with("native-inventory-stock-refresh", self.epoch, timeout=4)

    async def test_refresh_does_not_rebuild_closed_or_replaced_stock_view(self):
        self.view.queue_stock_inventory(self.client)
        await self.view.sync(self.client, True)
        self.game["pawns"]["local"]["name"] = "replacement"
        self.release.set()
        await self.view.request
        await self.view.sync(self.client, True)
        self.client.bridge.command.assert_not_awaited()
        self.view.receive.assert_not_awaited()


class NativeGuestHealthViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
        self.token = uuid.uuid4()
        self.pawn = f"Player_FP_C {self.area}.Observitory_P:PersistentLevel.Player_FP_C_1"
        self.epoch = "b" * 32
        self.session = "a" * 32
        self.body = {"name": self.pawn, "health": 250, "max_health": 250}
        self.frame = {"owner": str(self.token), "map": self.area, "epoch": self.epoch, "health": 74, "maximum": 250}
        self.game = {"state": "connected", "map": self.area, "save_scope": self.token.hex,
                     "pawns": {"local": self.body}, "native_client_damage": {"pawn": self.pawn, "dead": False},
                     "ack": {"message": "stock co-op guest health projected"}}

        async def command(operation, area, session, epoch, token, name, health, maximum, *, timeout):
            self.assertEqual((operation, area, session, epoch, token, name, timeout),
                             (native_guest_ui.HEALTH_PROJECT, self.area, self.session, self.epoch, self.token.hex,
                              self.body["name"], 1.5))
            self.body.update(health=float(health), max_health=float(maximum))
            return self.game

        self.bridge = SimpleNamespace(command=AsyncMock(side_effect=command))
        self.client = SimpleNamespace(last_game=self.game, bridge=self.bridge, control=Mock(),
                                      gameplay={native_guest_ui.HEALTH: self.frame}, gameplay_seen=time.monotonic(),
                                      expected_zone=self.area, host_session=self.session, zone_epoch=self.epoch,
                                      invite={"character_id": str(self.token)})
        self.view = native_guest_ui.GuestView()

    def test_owner_snapshot_uses_the_detached_live_pawn(self):
        member = {"connected": True, "alias": "guest", "pawn": self.pawn}
        player = {"character_id": str(self.token), "health": 250, "downed": False}
        game = {"state": "hosting", "map": self.area, "pawns": {"guest": {**self.body, "health": 74}}}
        self.assertEqual(native_guest_ui.health_frame(game, self.epoch, str(self.token), member, player), self.frame)
        self.assertIsNone(native_guest_ui.health_frame(game, self.epoch, str(uuid.uuid4()), member, player))
        self.assertIsNone(native_guest_ui.health_frame(game, self.epoch, str(self.token), {**member, "pawn": "stale"}, player))
        player["downed"] = True
        self.assertEqual(native_guest_ui.health_frame(game, self.epoch, str(self.token), member, player)["health"], 0)

    async def test_live_health_coalesces_and_updates_through_heal_and_rejoin(self):
        await self.view.sync_health(self.client, True)
        self.assertEqual(self.body["health"], 74)
        self.client.gameplay_seen = time.monotonic()
        await self.view.sync_health(self.client, True)
        self.bridge.command.assert_awaited_once()
        self.body["health"] = 80  # Stock local regeneration cannot replace host authority.
        self.client.gameplay_seen = time.monotonic()
        await self.view.sync_health(self.client, True)
        self.assertEqual(self.body["health"], 74)
        self.frame["health"] = 0
        self.client.gameplay_seen = time.monotonic()
        await self.view.sync_health(self.client, True)
        self.assertEqual(self.body["health"], 0)
        self.frame["health"] = 25
        self.client.gameplay_seen = time.monotonic()
        await self.view.sync_health(self.client, True)
        self.assertEqual(self.body["health"], 25)
        await self.view.sync_health(self.client, False)
        self.body["name"] = self.pawn.replace("_C_1", "_C_7")
        self.game["native_client_damage"]["pawn"] = self.body["name"]
        await self.view.sync_health(self.client, True)
        self.assertEqual(self.bridge.command.await_count, 5)

    async def test_stale_or_mismatched_receipts_leave_the_game_untouched(self):
        for key, value in (("owner", str(uuid.uuid4())), ("epoch", "c" * 32), ("map", "/Game/Invalid"),
                           ("health", float("inf")), ("maximum", 0)):
            with self.subTest(key=key):
                self.client.gameplay[native_guest_ui.HEALTH] = {**self.frame, key: value}
                await self.view.sync_health(self.client, True)
        self.client.gameplay[native_guest_ui.HEALTH] = self.frame
        self.game["save_scope"] = uuid.uuid4().hex
        await self.view.sync_health(self.client, True)
        self.game["save_scope"] = self.token.hex
        self.game["native_client_damage"]["dead"] = True
        await self.view.sync_health(self.client, True)
        self.game["native_client_damage"]["dead"] = False
        self.client.gameplay_seen = time.monotonic() - 3
        await self.view.sync_health(self.client, True)
        self.bridge.command.assert_not_awaited()
        self.assertEqual(self.body["health"], 250)


class NativeGuestInventoryUseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        self.pawn = "Player_FP_C Guest"
        self.guid = "ABCD" * 8
        self.use = {"sequence": 3, "map": self.area, "pawn": self.pawn, "guid": self.guid, "state": "using"}
        self.displayed = {"state": "rendered", "sequence": 3, "map": self.area, "pawn": self.pawn, "offset": 0,
                          "total": 1, "count": 1}
        self.game = {"state": "connected", "map": self.area, "pawns": {"local": {"name": self.pawn}},
                     "native_guest_inventory_use_view": self.use, "native_inventory_view": self.displayed}
        self.bridge = SimpleNamespace(input_namespace=uuid.uuid4(), snapshot=Mock(return_value=self.game),
                                      command=AsyncMock(return_value=self.game))
        self.client = service.Client(Mock(), SimpleNamespace(address=("127.0.0.1", 8888)), {"character_id": "guest"},
                                     self.bridge)
        self.client.host_session, self.client.zone_epoch = "session", "zone"
        self.client.gameplay_seen = time.monotonic()
        self.client.last_game = self.game
        self.page = {"owner": "guest", "offset": 0, "total": 1, "items": [
            {"guid": self.guid, "source": "backpack", "class_path": native_guest_consumable.BANDAGE_CLASS}
        ]}
        self.result = {"native_guest_bandage": {"state": "used", "map": self.area, "guid": self.guid}}
        self.operation = uuid.uuid5(self.bridge.input_namespace, f"session:zone:{self.pawn}:inventory-use:3").hex

    async def test_confirmed_stock_bandage_is_dispatched_once_and_reports_success(self):
        self.client.gameplay_action = AsyncMock(side_effect=[{"native_inventory": self.page}, self.result])
        await self.client.service_inventory_use(True)
        await self.client.service_inventory_use(True)
        self.assertEqual(self.client.gameplay_action.await_args_list, [
            call("native-inventory-list", self.operation, "0"),
            call(native_guest_consumable.ACTION, self.operation, self.guid),
        ])
        self.bridge.command.assert_awaited_once_with("native-inventory-view-use-result", "3", self.area, self.guid,
                                                     "used", timeout=2)
        self.client.control.event.assert_called_once_with("native_guest_inventory_used", operation_id=self.operation,
                                                          view_sequence=3, **self.result)

    async def test_wrong_owner_slot_class_and_source_cannot_dispatch_use(self):
        valid = self.page["items"][0]
        for change in ({"owner": "other"}, {"offset": 1}, {"items": [valid | {"source": "equipment"}]},
                       {"items": [valid | {"guid": "BCDE" * 8}]},
                       {"items": [valid | {"class_path": native_guest_consumable.BANDAGE_CLASS + "Dirty"}]}):
            with self.subTest(change=change):
                self.client.last_inventory_use = 0
                self.bridge.command.reset_mock()
                self.client.gameplay_action = AsyncMock(return_value={"native_inventory": self.page | change})
                await self.client.service_inventory_use(True)
                self.client.gameplay_action.assert_awaited_once_with("native-inventory-list", self.operation, "0")
                self.bridge.command.assert_awaited_once_with("native-inventory-view-use-result", "3", self.area,
                                                             self.guid, "refused", timeout=2)

    async def test_exact_mre_dispatches_only_with_the_invited_capability(self):
        page = self.page | {"items": [self.page["items"][0] | {"class_path": native_guest_consumable.MRE_CLASS}]}
        self.client.gameplay_action = AsyncMock(return_value={"native_inventory": page})
        await self.client.service_inventory_use(True)
        self.client.gameplay_action.assert_awaited_once_with("native-inventory-list", self.operation, "0")
        self.client.last_inventory_use = 0
        self.client.invite["native_guest_mre"] = True
        self.client.gameplay_action = AsyncMock(side_effect=[{"native_inventory": page},
            {"native_guest_mre": {"state": "used", "map": self.area, "guid": self.guid}}])
        await self.client.service_inventory_use(True)
        self.client.gameplay_action.assert_awaited_with(native_guest_consumable.MRE, self.operation, self.guid)

    async def test_confirmation_and_stale_client_identity_do_not_contact_host(self):
        for changed, active in (({"state": "confirming"}, True), ({"map": "old-zone"}, True),
                                ({"pawn": "old-pawn"}, True), ({}, False)):
            with self.subTest(changed=changed, active=active):
                self.client.last_inventory_use = 0
                self.client.last_game = self.game | {"native_guest_inventory_use_view": self.use | changed}
                self.client.gameplay_action = AsyncMock()
                await self.client.service_inventory_use(active)
                self.client.gameplay_action.assert_not_awaited()
        self.bridge.command.assert_not_awaited()

    async def test_world_change_after_listing_does_not_dispatch_use(self):
        self.bridge.snapshot.return_value = self.game | {"map": "new-zone"}
        self.client.gameplay_action = AsyncMock(return_value={"native_inventory": self.page})
        await self.client.service_inventory_use(True)
        self.client.gameplay_action.assert_awaited_once_with("native-inventory-list", self.operation, "0")

    async def test_native_refusal_does_not_display_success(self):
        self.client.gameplay_action = AsyncMock(side_effect=[
            {"native_inventory": self.page}, ValueError("guest_bandage_refused_downed_or_respawning"),
        ])
        await self.client.service_inventory_use(True)
        self.bridge.command.assert_awaited_once_with("native-inventory-view-use-result", "3", self.area, self.guid,
                                                     "refused", timeout=2)

    async def test_existing_open_view_can_request_adjacent_page_for_same_guest(self):
        self.displayed.update(total=17, count=16)
        scope = ("session", "zone", 3, self.area, self.pawn)
        self.client.inventory_view_scope = (*scope, 0)
        self.bridge.native_inventory_view_requests = Mock(return_value=[(3, "page", self.area, self.pawn, 16)])
        self.page.update(offset=16, total=17)
        self.page["items"][0].update(index=16, quantity=1)
        page_view = dict(self.displayed, offset=16, count=1)
        self.bridge.command.return_value = dict(self.game, native_inventory_view=page_view)
        self.client.gameplay_action = AsyncMock(return_value={"native_inventory": self.page})
        await self.client.service_inventory_view(True)
        operation = uuid.uuid5(self.bridge.input_namespace, f"session:zone:{self.pawn}:inventory:3:16").hex
        self.client.gameplay_action.assert_awaited_once_with("native-inventory-list", operation, "16")
        self.bridge.command.assert_awaited_once_with("native-inventory-view-render", "3", self.area, "16", "17",
            f"{self.guid}|backpack|16|1|{native_guest_consumable.BANDAGE_CLASS}", timeout=5)
        self.assertEqual(self.client.inventory_view_scope, (*scope, 16))

        self.bridge.native_inventory_view_requests.return_value = [(3, "page", self.area, self.pawn, 48)]
        self.client.gameplay_action.reset_mock()
        self.bridge.command.reset_mock()
        await self.client.service_inventory_view(True)
        self.client.gameplay_action.assert_not_awaited()
        self.bridge.command.assert_awaited_once_with("native-inventory-view-unavailable", "3", self.area, "48", timeout=2)

    async def test_qualified_item_can_be_used_on_second_page_only_while_still_displayed(self):
        self.displayed.update(offset=16, total=17)
        self.page.update(offset=16, total=17)
        for current, accepted in ((dict(self.game, native_inventory_view=dict(self.displayed, offset=0)), False),
                                  (self.game, True)):
            with self.subTest(accepted=accepted):
                self.bridge.snapshot.return_value = current
                self.client.last_inventory_use = 0
                self.client.gameplay_action = AsyncMock(side_effect=[{"native_inventory": self.page}, self.result])
                await self.client.service_inventory_use(True)
                check = [call("native-inventory-list", self.operation, "16")]
                if accepted:
                    check.append(call(native_guest_consumable.ACTION, self.operation, self.guid))
                self.assertEqual(self.client.gameplay_action.await_args_list, check)

    async def test_qualified_equipment_reads_exact_row_on_current_page(self):
        self.displayed.update(offset=16, total=17)
        item_class = sorted(native_guest_weapon.CLASS_PATHS)[0]
        self.page.update(offset=16, total=17)
        self.page["items"][0]["class_path"] = item_class
        self.game["native_guest_inventory_equip_view"] = dict(self.use, state="equipping")
        self.bridge.native_inventory_equip_requests = Mock(return_value=[(3, self.area, self.pawn, self.guid, item_class)])
        self.client.gameplay_action = AsyncMock(side_effect=[{"native_inventory": self.page}, {"ok": True}])
        await self.client.service_inventory_equip(True)
        operation = uuid.uuid5(self.bridge.input_namespace, f"session:zone:{self.pawn}:inventory-equip:3").hex
        self.assertEqual(self.client.gameplay_action.await_args_list, [
            call("native-inventory-list", operation, "16"), call("native-guest-weapon-equip", operation, self.guid),
        ])
        self.bridge.command.assert_awaited_once_with("native-inventory-view-equip-result", "3", self.area, self.guid,
                                                     "equipped", timeout=2)

    def test_stock_open_close_and_valid_page_events_preserve_fragmented_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            bridge = runtime.Bridge(path, None)
            events = path / "native-inventory-view-events"
            events.write_text(f"inventory-view\t3\topen\t{self.area}\t{self.pawn}\n"
                              f"inventory-view\t3\tpage\t{self.area}\t{self.pawn}\t15\n"
                              f"inventory-view\t3\tpage\t{self.area}\t{self.pawn}\t1", encoding="utf-8")
            self.assertEqual(bridge.native_inventory_view_requests(), [(3, "open", self.area, self.pawn, 0)])
            with events.open("a", encoding="utf-8") as stream:
                stream.write(f"6\ninvalid\ninventory-view\t3\tclosed\t{self.area}\t{self.pawn}\n")
            self.assertEqual(bridge.native_inventory_view_requests(), [
                (3, "page", self.area, self.pawn, 16), (3, "closed", self.area, self.pawn, 0),
            ])
            self.assertEqual(bridge.native_inventory_view_requests(), [])

    def test_status_snapshot_reports_only_valid_inventory_use_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            snapshot = runtime.Bridge(path, None)
            prefix = f"schema\t1\nsession\tguest\tconnected\t{self.area}\t0\t1\n"
            status = path / "status.tsv"
            for sequence, guid, state in (("3", self.guid, "using"), ("0", self.guid, "using"),
                                          (str(2**53), self.guid, "using"), ("3", "0" * 32, "using"),
                                          ("3", self.guid, "unknown")):
                with self.subTest(sequence=sequence, guid=guid, state=state):
                    status.write_text(prefix +
                                      f"native-guest-inventory-use-view\t{sequence}\t{self.area}\t{self.pawn}\t{guid}\t{state}\n",
                                      encoding="utf-8")
                    self.assertEqual(snapshot.snapshot().get("native_guest_inventory_use_view"),
                                     self.use if (sequence, guid, state) == ("3", self.guid, "using") else None)
