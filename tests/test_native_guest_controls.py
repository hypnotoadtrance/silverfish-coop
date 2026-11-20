import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch
import uuid

from tools.silverfish import native_guest_consumable, native_guest_weapon, runtime, service


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
