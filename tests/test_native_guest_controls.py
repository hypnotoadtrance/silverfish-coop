import asyncio
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch
import uuid

from tools.silverfish import native_guest_ui, native_inventory_grant, party, runtime, service


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
            self.assertEqual(returned, {**{key: value for key, value in first.items() if key != "payload"},
                                        "revision": first["revision"] + 1})
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

    async def test_compact_capture_keeps_the_periodic_snapshot_and_checks_the_body(self):
        def compact(request, _):
            nonce = request.partition(b"\t")[0].decode("ascii")
            return (f"native-guest-ui-read-reply\t1\t{nonce}\thosting\t{self.area}\taccepted\t"
                    f"captured co-op guest view\nnative-guest-ui-body\t{len(self.body)}\n" + self.body.decode("ascii"))

        self.bridge.local_bridge.command.side_effect = compact
        state, contents = await self.bridge.guest_ui_command(self.target)
        self.assertEqual((state["state"], state["map"], state["ack"]["message"]),
                         ("hosting", self.area, "captured co-op guest view"))
        self.assertEqual(contents, self.body)
        self.assertIs(self.bridge._local_status, self.prior)
        for invalid in (lambda raw: raw.replace("\thosting\t", "\tconnected\t"),
                        lambda raw: raw.replace(f"body\t{len(self.body)}", "body\t1")):
            self.bridge.local_bridge.command.side_effect = lambda request, timeout: invalid(compact(request, timeout))
            with self.assertRaises(RuntimeError):
                await self.bridge.guest_ui_command(self.target)
            self.assertIs(self.bridge._local_status, self.prior)

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

    async def test_compact_projection_leaves_current_inventory_context_and_status_intact(self):
        owner = str(self.character)
        current = {"state": "connected", "map": self.area, "save_scope": self.character.hex,
                   "pawns": {"local": {"name": self.target}}, runtime.GUEST_UI_MEMORY: True}
        client = SimpleNamespace(last_game=current, zone_epoch="a" * 32, bridge=self.bridge, control=Mock())
        saved = {"sequence": 47}
        self.bridge._local_status = saved

        def compact(request, _):
            nonce = request.partition(b"\t")[0].decode("ascii")
            return (f"native-guest-ui-project-reply\t1\t{nonce}\tconnected\t{self.area}\t{self.character.hex}\t"
                    f"{self.target}\taccepted\tstock co-op inventory and Journal projected 0 0 0 0 0\n")

        self.bridge.local_bridge.command.side_effect = compact
        view = native_guest_ui.GuestView()
        self.assertTrue(await view.project(client, owner, self.body.decode()))
        self.assertIs(client.last_game, current)
        self.assertIs(self.bridge._local_status, saved)
        self.assertGreater(client.control.event.call_args.kwargs["local_reply_bytes"], 0)

        self.bridge.local_bridge.command.side_effect = lambda request, timeout: compact(
            request, timeout).replace(self.target, self.target + "2")
        self.assertFalse(await view.project(client, owner, self.body.decode(), layout_sequence="5"))
        client.control.event.assert_called_once()
        self.assertIs(client.last_game, current)
        self.assertIs(self.bridge._local_status, saved)

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

    async def test_resident_projection_refreshes_the_open_backpack_on_its_projection_ticket(self):
        owner, epoch = str(self.character), "a" * 32
        current = {"state": "connected", "map": self.area, "save_scope": self.character.hex,
                   "pawns": {"local": {"name": self.target}}, runtime.GUEST_UI_MEMORY: True,
                   "native_inventory_view": {"state": "stock"}}
        client = SimpleNamespace(last_game=current, host_session="session", zone_epoch=epoch, bridge=self.bridge,
                                 expected_zone=self.area, invite={"character_id": owner}, control=Mock())

        def compact(request, _):
            header, _, payload = request.partition(b"\n")
            nonce, action, character, visit = header.decode().split("\t")
            self.assertEqual((action, character, visit, payload),
                             ("native-guest-ui-project-memory", owner, "refresh=" + epoch, self.body))
            return (f"native-guest-ui-project-reply\t1\t{nonce}\tconnected\t{self.area}\t{self.character.hex}\t"
                    f"{self.target}\taccepted\tstock co-op inventory and Journal projected 1 0 0 0 0 stock-menu-refreshed\n")

        self.bridge.local_bridge.command.side_effect = compact
        view = native_guest_ui.GuestView()
        self.assertTrue(await view.project(client, owner, self.body.decode(), refresh_stock=True))
        self.bridge.local_bridge.command.assert_awaited_once()
        self.assertIs(client.last_game, current)
        self.assertEqual(client.control.event.call_args.kwargs["stock_menu"], "refreshed")
        with self.assertRaises(ValueError):
            await self.bridge.guest_ui_command(self.target, refresh_epoch=epoch)
        self.bridge.local_bridge.command.assert_awaited_once()

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
        client = SimpleNamespace(last_game=guest_game, host_session="session", zone_epoch=zone,
                                 expected_zone=self.area, invite={"character_id": owner}, bridge=guest_bridge, control=Mock())
        with patch.object(runtime, "write_text", side_effect=AssertionError("unexpected disk write")):
            await native_guest_ui.GuestView().receive(client, result, self.area, zone, owner)
        guest_bridge.guest_ui_command.assert_awaited_once_with(owner, payload=self.body)
        guest_bridge.command.assert_not_awaited()
        self.assertIs(client.last_game, guest_receipt)

    async def test_pending_layout_defers_an_ordinary_inventory_projection(self):
        owner = str(self.character)
        game = {"state": "connected", "map": self.area, "save_scope": self.character.hex,
                runtime.GUEST_UI_MEMORY: True}
        deferred = dict(game, ack={"message": "stock personal inventory update pending"})
        accepted = dict(game, ack={"message": "stock co-op inventory and Journal projected 0 0 0 0 0"})
        bridge = SimpleNamespace(local_bridge=object(),
                                 guest_ui_command=AsyncMock(side_effect=[(deferred, None), (accepted, None)]))
        client = SimpleNamespace(last_game=game, bridge=bridge, control=Mock())
        view = native_guest_ui.GuestView()
        self.assertFalse(await view.project(client, owner, self.body.decode()))
        self.assertIs(client.last_game, game)
        self.assertTrue(await view.project(client, owner, self.body.decode(), layout_sequence="5"))
        bridge.guest_ui_command.assert_awaited_with(owner, payload=self.body, layout_sequence="5")
        self.assertIs(client.last_game, accepted)


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

    async def test_layout_recovery_waits_for_newer_local_edits(self):
        self.release.set()
        self.view.receive.side_effect = [False, True]
        await self.view.refresh(self.client, layout_sequence="5")
        scope = self.view.inventory_scope(self.client)
        self.assertEqual(self.view.pending_stock_refresh, scope)
        self.client.bridge.command.assert_not_awaited()
        await self.view.close(preserve_stock=True)
        self.assertEqual(self.view.pending_stock_refresh, scope)

        await self.view.sync(self.client, True)
        await self.view.request
        await self.view.sync(self.client, True)
        self.view.receive.assert_awaited_with(self.client, {"capture": 2}, self.area, self.epoch, str(self.character),
                                             refresh_stock=True)
        self.client.bridge.command.assert_not_awaited()
        self.assertIsNone(self.view.pending_stock_refresh)

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
        self.view.receive.assert_awaited_once_with(self.client, {"capture": 2}, self.area, self.epoch, str(self.character),
                                                  refresh_stock=True)
        self.client.bridge.command.assert_not_awaited()

    async def test_pickup_invalidates_inventory_before_stock_screen_opens(self):
        self.game["native_inventory_view"]["state"] = "closed"
        self.view.identity = (self.session, self.epoch, self.area)
        self.view.digest = "a" * 64
        self.view.queue_stock_inventory(self.client)
        self.assertTrue(self.view.inventory_needs_refresh(self.client))
        await self.view.sync(self.client, True)
        self.assertTrue(self.view.inventory_needs_refresh(self.client))

        self.release.set()
        await self.view.request
        await self.view.sync(self.client, True)
        self.assertFalse(self.view.inventory_needs_refresh(self.client))
        self.view.receive.assert_awaited_once_with(
            self.client, {"capture": 1}, self.area, self.epoch, str(self.character), refresh_stock=False)
        self.client.bridge.command.assert_not_awaited()

    async def test_refresh_does_not_rebuild_closed_or_replaced_stock_view(self):
        self.view.queue_stock_inventory(self.client)
        await self.view.sync(self.client, True)
        self.game["pawns"]["local"]["name"] = "replacement"
        self.release.set()
        await self.view.request
        await self.view.sync(self.client, True)
        self.client.bridge.command.assert_not_awaited()
        self.view.receive.assert_not_awaited()

    async def test_pickup_snapshot_fills_a_gap_between_delivered_claim_replies(self):
        client, view = self.client, self.view
        scope = view.inventory_scope(client)
        view.identity = scope[:3]
        view.next_poll = 105.0
        client.stock_interaction_scope = lambda: scope
        client.gameplay = {native_inventory_grant.PICKUP_REVISION: 4}
        present = AsyncMock(return_value={"pickup_inventory_result": "added"})
        claim = {"guid": "A" * 32, "class_path": "/Game/Classes/Items/Item_Bandage.Item_Bandage_C",
                 "claimed_by": str(self.character)}
        row = dict(guid=claim["guid"], text="(Item=Bandage)", owner=str(self.character),
                   map=self.area, epoch=self.epoch, previous_pickup=2, revision=4)
        _, applied = await view.present_pickup(client, scope, "newer", claim, row, present)
        self.assertTrue(applied)
        self.assertEqual(view.pickup_revision, 4)
        self.assertEqual(view.pickup_covered, 0)
        clock = Mock(return_value=100.0)
        with patch("tools.silverfish.native_guest_ui.time", SimpleNamespace(monotonic=clock)):
            await view.sync(client, True)
            client.gameplay_action.assert_not_awaited()
            clock.return_value = 100.16
            await view.sync(client, True)
            self.release.set()
            await view.request
            await view.sync(client, True)
        self.assertEqual(self.amount, 1)
        client.control.event.assert_any_call("native_pickup_inventory_recovery_requested", revision=4)
        view.receive.assert_awaited_once_with(client, {"capture": 1}, self.area, self.epoch, str(self.character),
                                             refresh_stock=True)

    async def test_delivered_pickup_covers_its_snapshot_notification(self):
        client, view = self.client, self.view
        scope = view.inventory_scope(client)
        view.identity = scope[:3]
        view.next_poll = 105.0
        client.stock_interaction_scope = lambda: scope
        client.gameplay = {native_inventory_grant.PICKUP_REVISION: 2}
        present = AsyncMock(return_value={"pickup_inventory_result": "added"})
        claim = {"guid": "A" * 32, "class_path": "/Game/Classes/Items/Item_Bandage.Item_Bandage_C",
                 "claimed_by": str(self.character)}
        row = dict(guid=claim["guid"], text="(Item=Bandage)", owner=str(self.character),
                   map=self.area, epoch=self.epoch, previous_pickup=0, revision=2)
        clock = Mock(return_value=100.0)
        with patch("tools.silverfish.native_guest_ui.time", SimpleNamespace(monotonic=clock)):
            await view.sync(client, True)
            _, applied = await view.present_pickup(client, scope, "arrived", claim, row, present)
            self.assertTrue(applied)
            clock.return_value = 100.3
            await view.sync(client, True)
        self.assertEqual(view.pickup_covered, 2)
        self.assertIsNone(view.request)
        client.gameplay_action.assert_not_awaited()

    async def test_out_of_order_pickups_reject_old_rows_for_the_same_item_and_older_full_views(self):
        del self.view.receive
        client, owner = self.client, str(self.character)
        scope = self.view.inventory_scope(client)
        guid, item_class = "A" * 32, "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
        operation = "b" * 32
        client.stock_interaction_scope = lambda: scope
        present = AsyncMock(side_effect=({"pickup_inventory_result": "merged"}, {"pickup_inventory_result": "added"}))
        claim = {"guid": "C" * 32, "inventory_guid": guid, "class_path": item_class,
                 "claimed_by": owner, "stack_merge": {"before": 3}}
        row = dict(owner=owner, map=self.area, epoch=self.epoch, revision=4, guid=guid, text="(Item=Bandage)")
        _, applied = await self.view.present_pickup(client, scope, operation, claim, row, present)
        self.assertTrue(applied)
        present.assert_awaited_once_with(inventory=(guid, item_class, row["text"], "3"))
        other_guid = "F" * 32
        other_claim = {"guid": other_guid, "class_path": item_class, "owner": owner}
        other_row = dict(row, revision=3, guid=other_guid)
        _, applied = await self.view.present_pickup(self.client, scope, operation, other_claim, other_row, present)
        self.assertTrue(applied)
        present.assert_awaited_with(inventory=(other_guid, item_class, other_row["text"], "-"))
        _, applied = await self.view.present_pickup(self.client, scope, operation, claim, dict(row, revision=2), present)
        self.assertTrue(applied)
        self.assertEqual(present.await_count, 2)
        self.assertFalse(await self.view.receive(client, {"revision": 3}, self.area, self.epoch, owner))
        client.bridge.command.assert_not_awaited()
        self.assertEqual(self.view.pickup_revision, 4)


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

        async def command(operation, area, session, epoch, token, name, health, maximum, *vitals, timeout):
            self.assertEqual((operation, area, session, epoch, token, name, timeout),
                             (native_guest_ui.HEALTH_PROJECT, self.area, self.session, self.epoch, self.token.hex,
                              self.body["name"], 1.5))
            self.body.update(health=float(health), max_health=float(maximum))
            if vitals:
                view = self.game.setdefault(native_guest_ui.VITALS, {}).setdefault(name, {})
                for field, value in zip(("food", "regen", "stim", "fatigue", "intox", "sickness", "rads"), vitals):
                    if value != "-":
                        view[field] = float(value)
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

    def test_vitals_are_from_the_exact_bound_pawn_and_independent_of_downed_hp(self):
        member = {"connected": True, "alias": "guest2", "pawn": self.pawn}
        character = str(self.token)
        player = {"character_id": character, "downed": True}
        game = {"state": "hosting", "map": self.area, "pawns": {"guest2": self.body},
                native_guest_ui.VITALS: {self.pawn: {"food": 54.2, "regen": 112.8, "stim": 3},
                                        "Player_FP_C Unrelated": {"food": 196.1, "regen": 249.9, "stim": 50}}}
        frame = native_guest_ui.health_frame(game, self.epoch, character, member, player)
        self.assertEqual((frame["health"], frame["food"], frame["regen"], frame["stim"]), (0, 54.2, 112.8, 3))
        del game[native_guest_ui.VITALS][self.pawn]
        frame = native_guest_ui.health_frame(game, self.epoch, character, member, player)
        self.assertNotIn("food", frame)
        self.assertNotIn("regen", frame)
        self.assertNotIn("stim", frame)

    async def test_radiation_can_change_and_clear_without_changing_health(self):
        self.frame["rads"] = 1.7
        await self.view.sync_health(self.client, True)
        view = self.game[native_guest_ui.VITALS][self.pawn]
        self.assertEqual(view, {"rads": 1.7})
        self.frame["rads"] = 0
        self.client.gameplay_seen = time.monotonic()
        await self.view.sync_health(self.client, True)
        self.assertEqual(view, {"rads": 0})
        self.assertEqual(self.bridge.command.await_count, 2)

    async def test_food_and_regeneration_ceiling_update_with_unchanged_hp(self):
        self.frame.update(food=54.2, regen=112.8)
        await self.view.sync_health(self.client, True)
        view = self.game[native_guest_ui.VITALS][self.pawn]
        self.assertEqual(view, {"food": 54.2, "regen": 112.8})
        self.frame["food"] = 84.2
        self.client.gameplay_seen = time.monotonic()
        await self.view.sync_health(self.client, True)
        self.assertEqual(view, {"food": 84.2, "regen": 112.8})
        self.frame["regen"] = 79.1
        self.client.gameplay_seen = time.monotonic()
        await self.view.sync_health(self.client, True)
        self.assertEqual(view, {"food": 84.2, "regen": 79.1})
        self.assertEqual((self.body["health"], self.body["max_health"]), (74, 250))
        self.frame["food"] = 84.195
        self.client.gameplay_seen = time.monotonic()
        await self.view.sync_health(self.client, True)
        self.assertEqual(self.bridge.command.await_count, 3)
        for stim in (3, 0):
            self.frame["stim"] = stim
            self.client.gameplay_seen = time.monotonic()
            await self.view.sync_health(self.client, True)
            self.assertEqual(view["stim"], stim)

    async def test_personal_flags_update_even_when_other_vitals_are_unchanged(self):
        self.frame.update(fatigue=31, intox=9)
        await self.view.sync_health(self.client, True)
        self.assertEqual(self.game[native_guest_ui.VITALS][self.pawn], {"fatigue": 31, "intox": 9})
        self.frame.update(fatigue=32, intox=0)
        self.client.gameplay_seen = time.monotonic()
        await self.view.sync_health(self.client, True)
        self.assertEqual(self.game[native_guest_ui.VITALS][self.pawn], {"fatigue": 32, "intox": 0})

    async def test_unqualified_optional_values_leave_the_legacy_health_projection_usable(self):
        self.frame.update(food=True, regen=251, stim=float("inf"))
        await self.view.sync_health(self.client, True)
        self.assertEqual(self.body["health"], 74)
        self.assertNotIn(native_guest_ui.VITALS, self.game)
        self.frame["food"] = 0
        self.client.gameplay_seen = time.monotonic()
        await self.view.sync_health(self.client, True)
        self.assertEqual(self.game[native_guest_ui.VITALS][self.pawn], {"food": 0})

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


class NativeGuestInventoryViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        self.pawn = "Player_FP_C Guest"
        self.displayed = {"state": "pending", "sequence": 3, "map": self.area, "pawn": self.pawn, "offset": 0,
                          "total": 1, "count": 1}
        self.game = {"state": "connected", "map": self.area, "pawns": {"local": {"name": self.pawn}},
                     "native_inventory_view": self.displayed}
        self.bridge = SimpleNamespace(input_namespace=uuid.uuid4(), snapshot=Mock(return_value=self.game),
                                      command=AsyncMock(return_value=self.game))
        self.client = service.Client(Mock(), SimpleNamespace(address=("127.0.0.1", 8888)), {"character_id": "guest"},
                                     self.bridge)
        self.client.host_session, self.client.zone_epoch = "session", "zone"
        self.client.gameplay_seen = time.monotonic()
        self.client.last_game = self.game

    async def test_unready_backpack_releases_the_pending_stock_request(self):
        self.bridge.native_inventory_view_requests = Mock(return_value=[(3, "open", self.area, self.pawn)])
        self.client.gameplay_action = AsyncMock()
        released = dict(self.game, native_inventory_view=dict(self.displayed, state="error"))
        self.bridge.command.return_value = released
        await self.client.service_inventory_view(True)
        self.client.gameplay_action.assert_not_awaited()
        self.bridge.command.assert_awaited_once_with("native-inventory-stock-unavailable", "3", self.area, timeout=2)
        self.assertEqual(self.client.last_game, released)
        self.assertEqual(self.client.control.event.call_args.args, ("native_stock_inventory_unavailable",))

    async def test_pending_grant_keeps_stock_backpack_open_on_current_local_inventory(self):
        character = uuid.uuid4()
        self.client.invite["character_id"] = str(character)
        self.client.expected_zone = self.area
        self.client.gameplay = dict.fromkeys((
            service.native_guest_drop_record.CAPABILITY, service.native_inventory.STORE_CAPABILITY,
            service.native_inventory.SORT_CAPABILITY, service.native_inventory.SPLIT_CAPABILITY,
        ), 1)
        self.bridge.native_inventory_view_requests = Mock(return_value=[(3, "open", self.area, self.pawn)])
        base = self.game | {"save_scope": character.hex, "native_inventory_view": self.displayed | {"state": "stock"}}
        for already_pending in (True, False):
            with self.subTest(already_pending=already_pending):
                self.client.last_game = base.copy()
                self.client.guest_ui.pending_stock_refresh = None
                if already_pending:
                    self.client.last_game[service.native_inventory_grant.READY] = 7
                self.client.gameplay_action = AsyncMock(return_value={"native_guest_ui": {}})
                self.client.guest_ui.receive = AsyncMock(return_value=False)
                self.bridge.command.reset_mock(return_value=True)
                self.bridge.command.return_value = base
                await self.client.service_inventory_view(True)
                self.bridge.command.assert_awaited_once_with("native-inventory-stock-open", "3", "zone", timeout=5)
                self.assertEqual(self.client.gameplay_action.await_count, 0 if already_pending else 1)
                self.assertTrue(self.client.guest_ui.inventory_needs_refresh(self.client))

    def test_stock_open_and_close_ignore_old_page_events_and_preserve_fragmented_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            bridge = runtime.Bridge(path, None)
            events = path / "native-inventory-view-events"
            events.write_text(f"inventory-view\t3\topen\t{self.area}\t{self.pawn}\n"
                              f"inventory-view\t3\tpage\t{self.area}\t{self.pawn}\t15\n"
                              f"inventory-view\t3\tpage\t{self.area}\t{self.pawn}\t1", encoding="utf-8")
            self.assertEqual(bridge.native_inventory_view_requests(), [(3, "open", self.area, self.pawn)])
            with events.open("a", encoding="utf-8") as stream:
                stream.write(f"6\ninvalid\ninventory-view\t3\tclosed\t{self.area}\t{self.pawn}\n")
            self.assertEqual(bridge.native_inventory_view_requests(), [
                (3, "closed", self.area, self.pawn),
            ])
            self.assertEqual(bridge.native_inventory_view_requests(), [])
