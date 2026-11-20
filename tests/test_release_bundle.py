"""Offline release bundle and setup regressions."""

from contextlib import ExitStack
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from bootstrap import install_stage, install_stock, release_bundle as bundle, release_setup as setup
from tools.silverfish import runtime


class BundleTests(unittest.TestCase):
    def test_archive_is_repeatable_and_detects_tampering(self):
        files = {name: ("fixture " + name).encode() for name in setup.SETUP_FILES}
        files["Setup.ps1"] = bundle.SETUP
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first, second = root / "first.zip", root / "second.zip"
            bundle._write_archive(first, "test-1", "a" * 40, files)
            bundle._write_archive(second, "test-1", "a" * 40, files)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with ZipFile(first) as archive:
                self.assertEqual(set(archive.namelist()), set(files) | {"release.json"})
                archive.extractall(root / "unpacked")
            extracted = root / "unpacked"
            self.assertEqual(setup.verify_release(extracted)["version"], "test-1")
            self.assertIn(b"-I -B $setup @setupArguments", (extracted / "Setup.ps1").read_bytes())
            path = extracted / setup.EMBEDDED_ADDON
            path.write_bytes(b"!" * len(files[setup.EMBEDDED_ADDON]))
            with self.assertRaisesRegex(ValueError, "differs from its checksum"):
                setup.verify_release(extracted)

    def test_archive_refuses_stock_or_private_data(self):
        contents = {name: b"fixture" for name in setup.SETUP_FILES}
        with TemporaryDirectory() as directory:
            output = Path(directory) / "release.zip"
            names = ("SilverFish-Win64-Shipping.exe", "payload/runtime/stock.ucas",
                     "payload/runtime/user/host-identity.json", "payload/runtime/user/player.sav",
                     "payload/runtime/client/foo.invite.json", "payload/runtime/keys/host.pem")
            for name in names:
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unexpected release member"):
                    bundle._write_archive(output, "test-1", "a" * 40, {**contents, name: b"private payload"})
                self.assertFalse(output.exists())

    def test_coordinator_requires_complete_matching_source_payload(self):
        expected = {"tools/silverfish/example.py": b"worker = 1\n"}
        name = "tools\\silverfish\\example.py"
        reader = SimpleNamespace(toc={name: (0, 0, 12, 0, "b")}, extract=lambda _: b"worker = 1\r\n")
        bundle._check_worker_sources(reader, expected)
        reader.extract = lambda _: b"worker = 2\n"
        with self.assertRaisesRegex(ValueError, "source bytes differ"):
            bundle._check_worker_sources(reader, expected)
        reader.toc = {}
        with self.assertRaisesRegex(ValueError, "source list differs"):
            bundle._check_worker_sources(reader, expected)


class SetupTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.release = self.root / "release"
        self.args = SimpleNamespace(game=self.root / "Steam Game", work=self.root / "coop",
                                    ue4ss=self.root / "UE4SS", signatures=self.root / "signatures",
                                    host_campaign="coop", bind="127.0.0.1", port=7777, join_profile="guest")
        self.manifest = {"version": "test-1", "files": {setup.GAMEPLAY_ADDON: {"sha256": "a" * 64}}}

    def mocked_setup(self, *, present):
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local-data")}))
            stack.enter_context(patch.object(install_stock, "_ordinary", side_effect=lambda path, **_: present
                                             if path == self.args.work / "stage" else True))
            pinned = stack.enter_context(patch.object(install_stock, "_pinned"))
            stack.enter_context(patch.object(install_stock, "_bootstrap_payload"))
            verified = stack.enter_context(patch.object(install_stock, "_verify_stage"))
            calls = []
            staged = stack.enter_context(patch.object(
                install_stage, "install", side_effect=lambda *args, **kwargs: calls.append("stage") or {"activated": not present},
            ))
            stock = stack.enter_context(patch.object(
                install_stock, "install", side_effect=lambda *args, **kwargs: calls.append("stock") or {"activated": not present},
            ))
            result = setup._install_bundle(self.release, self.manifest, self.args)
        self.assertEqual(calls, ["stage", "stock"])
        self.assertEqual(result["stage"]["activated"], not present)
        self.assertEqual(result["stock"]["activated"], not present)
        return staged, stock, verified, pinned

    def test_initial_setup_uses_explicit_paths_and_interactive_join(self):
        staged, stock, verified, _ = self.mocked_setup(present=False)
        self.assertEqual(staged.call_args.args, (self.args.work, self.release / setup.RUNTIME,
                                                 self.release / setup.EMBEDDED_ADDON))
        self.assertEqual(staged.call_args.kwargs, dict(game=self.args.game, ue4ss=self.args.ue4ss,
                                                     signatures=self.args.signatures,
                                                     cpp_addon=self.release / setup.GAMEPLAY_ADDON))
        self.assertEqual(stock.call_args.kwargs["host"], dict(campaign="coop", bind="127.0.0.1", port=7777))
        self.assertEqual(stock.call_args.kwargs["join"], dict(profile="guest", invite=None, character=None))
        verified.assert_not_called()

    def test_rerun_reuses_the_exact_completed_stage(self):
        staged, _, verified, pinned = self.mocked_setup(present=True)
        self.assertEqual(staged.call_args.kwargs, {})
        verified.assert_called_once_with(self.args.game, self.args.work)
        pinned.assert_any_call(self.args.work / "stage" / runtime.SHIPPING.parent / runtime.CPP_ADDON_DLL, "a" * 64)

    def test_original_save_workspace_refused_before_installation(self):
        self.args.work = self.root / "local-data" / "SilverFish" / "new-coop"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local-data")}), \
                patch.object(install_stage, "install") as staged, patch.object(install_stock, "install") as stock:
            with self.assertRaisesRegex(ValueError, "single-player saves"):
                setup._install_bundle(self.release, self.manifest, self.args)
            staged.assert_not_called()
            stock.assert_not_called()
