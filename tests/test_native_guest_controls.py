import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from tools.silverfish import runtime, service


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
