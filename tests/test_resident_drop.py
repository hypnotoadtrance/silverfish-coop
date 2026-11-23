"""Verify the drag-and-drop package layout offline."""

from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from bootstrap import install_stage, install_stock, release_bundle, release_setup, resident_drop
from tools.silverfish import runtime


class ResidentDropTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.root = Path(directory).resolve()
        self.shipping = self.write(self.root / "Stock Shipping.exe", b"test stock executable")
        shipping_sha = sha256(self.shipping.read_bytes()).hexdigest()
        self.enterContext(patch.object(resident_drop, "_SHIPPING_SHA256", shipping_sha))

        gameplay = bytearray(96)
        gameplay[:2] = b"MZ"
        gameplay[60:64] = b"\x40\x00\x00\x00"
        gameplay[64:70] = b"PE\0\0\x64\x86"
        gameplay[87] = 0x20
        gameplay[88:90] = b"\x0b\x02"
        gameplay.extend(b"SilverfishSetNativeGuestWorldStart\0SilverfishSetNativePropCarryMovement\0")
        self.gameplay = self.write(self.root / "native.dll", gameplay)

        self.ue4ss = self.root / "UE4SS"
        pins = install_stock.UE4SS_OFFICIAL.copy()
        for relative, contents in ((runtime.CPP_RUNTIME_DLL, b"loader"), (install_stock.PROXY, b"proxy"),
                                   (install_stock.SETTINGS, b"settings")):
            self.write(self.ue4ss / relative, contents)
            pins[relative] = sha256(contents).hexdigest()
        self.enterContext(patch.object(install_stock, "UE4SS_OFFICIAL", pins))
        self.write(self.ue4ss / "ue4ss/LICENSE", b"notice")
        self.signatures = self.root / "Signatures"
        signature = b"approved signature"
        self.write(self.signatures / install_stock.SIGNATURE.name, signature)
        self.enterContext(patch.object(install_stock, "SIGNATURE_SHA256", sha256(signature).hexdigest()))

        self.scripts = {
            "main.lua": b"return true\r\n",
            "authored_zone_maps.lua": b"return {}\n",
            "authored_wall_routes.lua": b"return {}\n",
        }
        sources = {f"mod/SilverfishCoop/Scripts/{name}": data for name, data in self.scripts.items()}
        self.enterContext(patch.object(release_bundle, "_sources", return_value=sources))
        manifest = b'{"schema":1}'
        self.runtime_folder = "runtime-" + sha256(manifest).hexdigest()[:16]
        self.runtime = {f"{release_setup.RUNTIME}/{install_stage.MANIFEST}": manifest,
                        f"{release_setup.RUNTIME}/python314.dll": b"python dll"}
        self.embedded = b"MZ" + "resident-portable.json".encode("utf-16-le")
        self.packed = self.enterContext(patch.object(release_bundle, "_runtime",
                                                    return_value=(self.runtime, self.embedded)))

    def write(self, path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def assemble(self, filename, *, include_loader=False, steam_api=None):
        return resident_drop.assemble(
            self.root / filename, version="fixture-1", revision="a" * 40, shipping=self.shipping,
            cpp_addon=self.gameplay, embedded_addon=self.root / "embedded.dll",
            embedded_runtime=self.root / "runtime", ue4ss=self.ue4ss,
            signatures=self.signatures if include_loader else None, include_loader=include_loader, steam_api=steam_api,
        )

    def test_update_contains_only_mod_files_and_versioned_runtime(self):
        with patch.dict(os.environ, {"SILVERFISH_COOP_BRIDGE": "private-path", "SILVERFISH_COOP_HEADLESS": "1"}):
            result = self.assemble("update.zip")
        binary = runtime.SHIPPING.parent
        mods = binary / "ue4ss/Mods"
        embedded = mods / install_stage.MOD
        runtime_path = embedded / self.runtime_folder
        activation = [mods / module / "enabled.txt" for module in ("SilverfishCoop", runtime.CPP_MOD_NAME, install_stage.MOD)]
        files = {
            "SilverFish/Silverfish-Coop-README.txt", runtime.PORTABLE_STAGE_MANIFEST.as_posix(),
            (binary / runtime.CPP_ADDON_DLL).as_posix(), (embedded / "dlls/main.dll").as_posix(),
            (embedded / "resident-portable.json").as_posix(),
            (runtime_path / "runtime-manifest.json").as_posix(), (runtime_path / "python314.dll").as_posix(),
            *(path.as_posix() for path in activation),
            *((binary / runtime.MOD_SCRIPTS / name).as_posix() for name in self.scripts),
        }

        with ZipFile(result["file"]) as archive:
            self.assertEqual(set(archive.namelist()), files)
            self.assertIsNone(archive.testzip())
            self.assertTrue(all(archive.read(path.as_posix()) == b"" for path in activation))
            stage = json.loads(archive.read(runtime.PORTABLE_STAGE_MANIFEST.as_posix()))
            self.assertEqual(stage["lua_files"]["main.lua"], sha256(b"return true\n").hexdigest())
            self.assertEqual(stage["cpp_death_lab"]["addon"], sha256(self.gameplay.read_bytes()).hexdigest())
            descriptor = json.loads(archive.read((embedded / "resident-portable.json").as_posix()))
            self.assertEqual(set(descriptor), {"schema", "shipping_sha256", "features", "isolated_features"})
            self.assertEqual(descriptor["shipping_sha256"], result["shipping_sha256"])
            features = descriptor["features"]
            self.assertEqual(features["SILVERFISH_COOP_UNIFIED_TITLE"], "1")
            self.assertEqual(features["SILVERFISH_COOP_NATIVE_DEATH"], "cpp")
            self.assertNotIn("SILVERFISH_COOP_BRIDGE", features)
            self.assertNotIn("SILVERFISH_COOP_HEADLESS", features)
            self.assertNotIn("SILVERFISH_COOP_EMBEDDED_STARTUP", features)
            self.assertEqual(descriptor["isolated_features"]["SILVERFISH_COOP_HEADLESS"], "1")
            self.assertEqual(descriptor["isolated_features"]["SILVERFISH_COOP_TITLE_FIXTURES"], "1")
            self.assertFalse(features.keys() & descriptor["isolated_features"].keys())

    def test_first_time_variant_adds_only_approved_loader_files(self):
        update = self.assemble("update.zip")
        full = self.assemble("full.zip", include_loader=True)
        binary = runtime.SHIPPING.parent
        with ZipFile(update["file"]) as mod_only, ZipFile(full["file"]) as first_time:
            added = set(first_time.namelist()) - set(mod_only.namelist())
            self.assertEqual(added, {
                (binary / runtime.CPP_RUNTIME_DLL).as_posix(),
                (binary / install_stock.PROXY).as_posix(),
                (binary / install_stock.SETTINGS).as_posix(),
                (binary / "ue4ss/LICENSE").as_posix(),
                (binary / "ue4ss" / install_stock.SIGNATURE).as_posix(),
            })

    def test_rejects_gameplay_addon_missing_a_resident_binding(self):
        image = self.gameplay.read_bytes()
        for name in ("SilverfishSetNativeGuestWorldStart", "SilverfishSetNativePropCarryMovement"):
            with self.subTest(binding=name):
                self.gameplay.write_bytes(image.replace(name.encode("ascii"), b""))
                with self.assertRaisesRegex(ValueError, name):
                    self.assemble("stale.zip")
                self.assertFalse((self.root / "stale.zip").exists())

    def test_steam_trial_bundles_only_the_qualified_redistributable_and_instructions(self):
        redistributable = self.gameplay.read_bytes()
        with patch.object(resident_drop, "STEAM_DLL_SHA256", sha256(redistributable).hexdigest()):
            release = self.assemble("steam.zip", steam_api=self.gameplay)
        with ZipFile(release["file"]) as archive:
            self.assertEqual(archive.read(resident_drop.STEAM_DLL.as_posix()), redistributable)
            self.assertIn(b"Invite Steam Friend", archive.read("SilverFish/Silverfish-Coop-README.txt"))
            self.assertFalse(any(name.endswith("steam_appid.txt") for name in archive.namelist()))

    def test_overwrite_preserves_previous_runtime_and_local_configuration(self):
        installed = self.root / "previous install"
        with ZipFile(self.assemble("previous.zip", include_loader=True)["file"]) as archive:
            archive.extractall(installed)

        bin_dir = installed / runtime.SHIPPING.parent
        mods = bin_dir / "ue4ss/Mods"
        embedded = mods / install_stage.MOD
        old_runtime = embedded / self.runtime_folder
        workspace_records = {
            embedded / "resident-install.json": b'{"schema":1,"work":"prior_workspace"}',
            mods / "mods.txt": b"UnrelatedMod : 1",
            mods / "UnrelatedMod/Scripts/main.lua": b"print('keep')",
            bin_dir / runtime.SHIPPING.name: b"original stock binary",
        }
        for file, contents in workspace_records.items():
            self.write(file, contents)

        new_manifest = b'{"schema":1,"version":"next"}'
        new_folder = "runtime-" + sha256(new_manifest).hexdigest()[:16]
        new_dll = self.embedded + b"next"
        self.packed.return_value = ({
            f"{release_setup.RUNTIME}/{install_stage.MANIFEST}": new_manifest,
            f"{release_setup.RUNTIME}/python314.dll": b"new runtime",
        }, new_dll)
        with ZipFile(self.assemble("overwrite.zip", include_loader=True)["file"]) as archive:
            archive.extractall(installed)

        self.assertEqual((embedded / "dlls/main.dll").read_bytes(), new_dll)
        self.assertEqual((embedded / new_folder / install_stage.MANIFEST).read_bytes(), new_manifest)
        self.assertEqual((old_runtime / install_stage.MANIFEST).read_bytes(), self.runtime[f"{release_setup.RUNTIME}/{install_stage.MANIFEST}"])
        self.assertEqual({file: file.read_bytes() for file in workspace_records}, workspace_records)

    def test_rejects_old_native_addon_and_mismatched_stock(self):
        self.packed.return_value = self.runtime, b"resident-portable.json"
        with self.assertRaisesRegex(ValueError, "does not support portable startup"):
            self.assemble("old.zip")
        self.assertFalse((self.root / "old.zip").exists())

        self.shipping.write_bytes(b"unsupported")
        with self.assertRaisesRegex(ValueError, "Shipping.exe does not match"):
            self.assemble("wrong-stock.zip")
        self.assertFalse((self.root / "wrong-stock.zip").exists())
