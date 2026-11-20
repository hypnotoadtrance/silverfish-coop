from hashlib import sha256
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from bootstrap import coordinator, install_stage, install_stock as installer, package
from tools.silverfish import runtime


class StockInstallerTests(unittest.TestCase):
    def setUp(self):
        checkout = Path(__file__).resolve().parents[1]
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix=".stock-install-test-", dir=checkout))).resolve()
        self.game = self.root / "fake-steam" / "Project Silverfish"
        self.work = self.root / "private"
        self.stage = self.work / "stage"
        self.external = self.root / "inputs"
        self.release = self.external / "ue4ss"
        self.signatures = self.external / "UE4SS_Signatures"
        self.config = self.root / "local-config" / "SilverfishCoop" / "coordinator.json"
        self.archive = self.external / "Bootstrap.zip"
        self.binaries = self.game / runtime.SHIPPING.parent
        self.mods = self.binaries / "ue4ss/Mods"
        self.activation = self.mods / "mods.txt"
        self.mod = self.mods / installer.MOD
        self.host = {"campaign": "new-world", "bind": "127.0.0.1", "port": 7777}
        self.official = {installer.PROXY: b"MZ pinned proxy", runtime.CPP_RUNTIME_DLL: b"MZ pinned core",
                         installer.SETTINGS: b"[Overrides]\nModsFolderPath =\n[Debug]\nConsoleEnabled = 0\n"
                                             b"GuiConsoleEnabled = 0\nGuiConsoleVisible = 0\n"}
        for relative, data in self.official.items():
            self.write(self.release / relative, data)
        self.signature = b"-- pinned FText signature\n"
        self.write(self.signatures / installer.SIGNATURE.name, self.signature)
        self.enterContext(patch.object(installer, "UE4SS_OFFICIAL", {
            relative: sha256(body).hexdigest() for relative, body in self.official.items()
        }))
        self.enterContext(patch.object(installer, "SIGNATURE_SHA256", sha256(self.signature).hexdigest()))

        self.write(self.game / "SilverFish.exe", b"Steam stub")
        self.write(self.game / runtime.MANIFEST, f"SilverFish.exe\t1\n{runtime.SHIPPING.as_posix()}\t2\n".encode())
        for path in (runtime.SHIPPING, *(runtime.PAKS / name for name in runtime.STOCK_PACKAGES)):
            data = path.as_posix().encode("ascii")
            self.write(self.game / path, data)
            self.write(self.stage / path, data)
        stage_build = installer._stock_signature(self.game, self.stage)
        stage_binaries = self.stage / runtime.SHIPPING.parent
        self.write(stage_binaries / installer.PROXY, self.official[installer.PROXY])
        self.write(stage_binaries / installer.SETTINGS, self.official[installer.SETTINGS])
        self.write(stage_binaries / runtime.CPP_RUNTIME_DLL, b"MZ native stage core")
        self.write(stage_binaries / runtime.CPP_ADDON_DLL, b"MZ native stage addon")
        self.stage_activation = self.write(stage_binaries / "ue4ss/Mods/mods.txt",
                                           b"SilverfishCoopNative : 1\nSilverfishCoop : 1\nSilverfishCoopEmbeddedRuntime : 1\n")
        runtime.write_json(self.stage / "silverfish-coop-stage.json", {
            "schema": 1,
            "game_build": stage_build,
            "cpp_death_lab": {
                "runtime": runtime._file_sha256(stage_binaries / runtime.CPP_RUNTIME_DLL),
                "addon": runtime._file_sha256(stage_binaries / runtime.CPP_ADDON_DLL),
            },
        })
        test_repo = self.root / "fixture-checkout"
        for script_root in (test_repo / "mod/SilverfishCoop/Scripts", stage_binaries / runtime.MOD_SCRIPTS):
            self.write(script_root / "main.lua", b"-- verified stage fixture\n")
        module = self.stage_activation.parent / install_stage.MOD
        self.embedded = module / "runtime"
        assets = {name: name.encode("ascii") for name in install_stage.STARTUP_FILES}
        assets["python314._pth"] = ("\n".join(install_stage.MODULE_PATHS) + "\n").encode("ascii")
        worker_entry = "app/tools/silverfish/embedded_startup.py"
        assets[worker_entry] = b"# matched embedded worker fixture\n"
        for name, data in assets.items():
            self.write(self.embedded / name, data)
            if name.startswith("app/tools/silverfish/"):
                self.write(test_repo / name.removeprefix("app/"), data)
        (self.embedded / "site-packages").mkdir()
        manifest_path = self.embedded / install_stage.MANIFEST
        runtime.write_json(manifest_path, {
            "schema": 1, "platform": "win-amd64", "python_library": "python314.dll", "python_version": "3.14.0",
            "module_paths": install_stage.MODULE_PATHS, "payload_bytes": sum(map(len, assets.values())),
            "files": {name: sha256(data).hexdigest() for name, data in assets.items()},
        })
        self.write(module / "dlls/main.dll", b"MZ embedded stage addon\0"
                   + sha256(manifest_path.read_bytes()).hexdigest().encode("ascii"))
        self.enterContext(patch.object(runtime, "REPO", test_repo))
        self.enterContext(patch.object(runtime, "qualified_authored_routes",
                                     side_effect=lambda work: runtime.native_death_backend(work) == "cpp"))
        worker = self.write(self.external / "SilverfishCoopCoordinator.exe", b"MZ coordinator test fixture")
        bridge = self.write(self.external / "main.dll", b"MZ title adapter test fixture\0"
                            + sha256(worker.read_bytes()).hexdigest().encode("ascii"))
        package.package(self.archive, native_dll=bridge, coordinator=worker)

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def install(self, **options):
        return installer.install(self.game, self.work, self.release, self.signatures, self.archive,
                                 **{"host": self.host, "config": self.config, **options})

    def test_fresh_install_writes_only_the_package_config_and_official_loader(self):
        original_pak = self.game / runtime.PAKS / "global.ucas"
        before = original_pak.read_bytes()
        actual = installer._atomic
        published = []

        def order(path, data, **options):
            if path == self.activation:
                self.assertTrue(self.config.is_file())
                self.assertTrue(self.mod.joinpath(installer.WORKER).is_file())
                self.assertTrue((self.binaries / installer.PROXY).is_file())
            actual(path, data, **options)
            published.append(path)

        with patch.object(installer, "_atomic", side_effect=order):
            result = self.install()
        self.assertTrue(result["activated"] and result["config_created"])
        self.assertEqual(published[-1], self.activation)
        self.assertEqual(before, original_pak.read_bytes())
        self.assertEqual(self.activation.read_bytes(), b"SilverfishCoopBootstrap : 1\n")
        self.assertEqual(coordinator.load_plan(self.config).campaign, self.host["campaign"])
        self.assertEqual(installer._files(self.binaries / "ue4ss"), {
            Path("UE4SS.dll"), Path("UE4SS-settings.ini"), installer.SIGNATURE, Path("Mods/mods.txt"),
            *(Path("Mods") / installer.MOD / item for item in (*installer.BOOTSTRAP_SCRIPTS, installer.TITLE_DLL, installer.WORKER)),
        })
        again = self.install()
        self.assertEqual(again["installed"], [])
        self.assertFalse(again["activated"] or again["config_created"])

    def test_compatible_preexisting_mods_and_custom_settings_keep_their_bytes(self):
        before = {
            self.binaries / installer.SETTINGS: b"[Overrides]\r\nControllingModsTxt =\r\n[Debug]\r\nConsoleEnabled = 1\r\n",
            self.binaries / installer.PROXY: self.official[installer.PROXY],
            self.binaries / runtime.CPP_RUNTIME_DLL: self.official[runtime.CPP_RUNTIME_DLL],
            self.binaries / "ue4ss/UE4SS_Signatures/AnotherSignature.lua": b"-- other mod",
            self.mods / "AnotherMod/Scripts/main.lua": b"-- untouched",
            self.mods / "AnotherMod/enabled.txt": b"",
        }
        for path, data in before.items():
            self.write(path, data)
        entries = (b"\xef\xbb\xbf; arbitrary retained comment\r\n"
                   b"AnotherMod : 1\r\nSilverfishCoopBootstrap : 0\r\nAnotherDisabled : 0")
        self.write(self.activation, entries)
        self.assertTrue(self.install()["activated"])
        self.assertEqual(self.activation.read_bytes(), entries.replace(b"Bootstrap : 0", b"Bootstrap : 1"))
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_recovery_after_interrupted_activation_reuses_exact_inputs(self):
        existing = self.write(self.activation, b"Another : 1\nSilverfishCoopBootstrap : 0\n")
        original = existing.read_bytes()
        atomic = installer._atomic

        def interrupted(path, data, **options):
            if path == self.activation:
                raise OSError("interrupted immediately before activation")
            atomic(path, data, **options)

        with patch.object(installer, "_atomic", side_effect=interrupted), self.assertRaisesRegex(OSError, "interrupted"):
            self.install()
        self.assertEqual(existing.read_bytes(), original)
        self.assertTrue(self.config.is_file() and self.mod.is_dir())
        result = self.install()
        self.assertEqual(result["installed"], [])
        self.assertFalse(result["config_created"])
        self.assertTrue(result["activated"])

    def test_conflicting_stock_loader_configuration_and_active_rows_fail_closed(self):
        failures = {
            self.binaries / installer.PROXY: (b"MZ different proxy", "different loader"),
            self.binaries / installer.SETTINGS: (b"[Overrides]\n+ModsFolderPaths = OtherMods\n", "redirect"),
            self.activation: (b"SilverfishCoopBootstrap : 1\n", "already enabled"),
            self.config: (b'{"schema":1,"work":"another workspace"}', "different local"),
            self.mod / "enabled.txt": (b"", "different or incomplete"),
        }
        for path, (data, reason) in failures.items():
            with self.subTest(path=path):
                self.write(path, data)
                with self.assertRaisesRegex(ValueError, reason):
                    self.install()
                self.assertEqual(path.read_bytes(), data)
                self.assertFalse((self.mod / installer.WORKER).exists())
                self.assertFalse((self.binaries / runtime.CPP_RUNTIME_DLL).exists())
                path.unlink()

    def test_modified_stage_and_missing_invite_never_publish_stock_changes(self):
        path = self.stage / runtime.PAKS / "global.ucas"
        original = path.read_bytes()
        path.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "staged and stock game builds differ"):
            self.install()
        path.write_bytes(original)
        outside = self.write(self.external / "friend.invite.json", b"{}")
        with self.assertRaisesRegex(ValueError, "private co-op workspace"):
            self.install(host=None, join={"profile": "friend", "invite": outside, "character": None})
        self.assertFalse(self.config.exists() or self.mod.exists() or self.activation.exists())

    def test_missing_or_mismatched_embedded_runtime_never_enables_the_stock_bootstrap(self):
        original = self.stage_activation.read_bytes()
        self.stage_activation.write_bytes(original.replace(b"EmbeddedRuntime : 1", b"EmbeddedRuntime : 0"))
        with self.assertRaisesRegex(ValueError, "requires its enabled embedded runtime"):
            self.install()
        self.stage_activation.write_bytes(original)
        self.write(self.embedded / "python314.zip", b"modified package")
        with self.assertRaisesRegex(ValueError, "differs from its manifest"):
            self.install()
        self.assertFalse(self.config.exists() or self.mod.exists() or self.activation.exists())

    def test_join_config_uses_a_validated_private_invite_without_copying_credentials(self):
        character = "ff7de067-5c91-4c8f-befd-3e5bde50bc69"
        invitation = self.work / "invitations" / "friend.invite.json"
        claimed = {"schema": 1, "host": "127.0.0.1", "port": 7778, "certificate": "fixture cert",
                   "guest_id": character, "credential": "isolated fixture credential", "character_id": character,
                   "game_build": runtime.staged_build(self.work), "protocol_version": 1}
        runtime.write_json(invitation, claimed)
        self.install(host=None, join={"profile": "friend", "invite": invitation, "character": None})
        plan = coordinator.load_plan(self.config)
        self.assertEqual((plan.campaign, plan.profile, plan.invite, plan.character), (None, "friend", invitation, None))
        self.assertNotIn(claimed["credential"], self.config.read_text())
        self.assertEqual(json.loads(invitation.read_text()), claimed)

    def test_archive_member_symlink_and_other_worker_pin_are_refused(self):
        with ZipFile(self.archive) as source:
            payload = {name: source.read(name) for name in source.namelist()}
        for change in ("unmatched worker", "symbolic link"):
            with self.subTest(change=change):
                with ZipFile(self.archive, "w") as target:
                    for name, data in payload.items():
                        if name.endswith("Coordinator.exe") and change == "unmatched worker":
                            data += b"tampered"
                        info = ZipInfo(name)
                        if name.endswith("Scripts/main.lua") and change == "symbolic link":
                            info.create_system = 3
                            info.external_attr = (stat.S_IFLNK | 0o777) << 16
                        target.writestr(info, data)
                with self.assertRaisesRegex(ValueError, "same SHA-256 pin|five ordinary files"):
                    self.install()
                self.assertFalse(self.mod.exists() or self.config.exists())


if __name__ == "__main__":
    unittest.main()
