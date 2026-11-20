import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from bootstrap import coordinator


class ReceiptTests(unittest.TestCase):
    def test_only_a_complete_versioned_open_receipt_is_accepted(self):
        row = b"1\topen-coop\t" + b"a" * 32 + b"\t" + b"b" * 32 + b"\n"
        self.assertEqual(coordinator.parse_receipt(row), coordinator.Receipt("a" * 32, "b" * 32))
        for unsafe in (row + b"extra", row.replace(b"open-coop", b"host"), row.replace(b"a" * 32, b"0" * 32),
                       row.replace(b"b" * 32, b"B" * 32), row[:-1]):
            with self.subTest(row=unsafe), self.assertRaises(ValueError):
                coordinator.parse_receipt(unsafe)

    @unittest.skipUnless(os.name == "nt", "Uses an inherited Windows pipe")
    def test_windowless_process_reads_one_complete_pipe_receipt(self):
        row = b"1\topen-coop\t" + b"a" * 32 + b"\t" + b"b" * 32 + b"\n"
        code = ("from bootstrap.coordinator import Receipt, read_inherited_receipt\n"
                "assert read_inherited_receipt() == Receipt('a' * 32, 'b' * 32)")
        for payload, expected in ((row, 0), (row + b"x", 1)):
            with self.subTest(expected=expected):
                child = subprocess.run([sys.executable, "-B", "-c", code], input=payload, capture_output=True,
                                       cwd=Path(__file__).resolve().parents[1], timeout=5,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(child.returncode, expected, child.stderr.decode(errors="replace"))

    @unittest.skipUnless(os.name == "nt", "Uses inherited Windows pipes")
    def test_windowless_process_writes_the_exact_readiness_receipt(self):
        ids = b"a" * 32 + b"\t" + b"b" * 32 + b"\n"
        code = ("from bootstrap.coordinator import read_inherited_receipt, write_inherited_ready\n"
                "write_inherited_ready(read_inherited_receipt())")
        child = subprocess.run([sys.executable, "-B", "-c", code], input=b"1\topen-coop\t" + ids, capture_output=True,
                               cwd=Path(__file__).resolve().parents[1], timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(child.returncode, 0, child.stderr.decode(errors="replace"))
        self.assertEqual(child.stdout, b"1\tcoop-title-ready\t" + ids)

    @unittest.skipUnless(os.name == "nt", "Uses an inherited Windows pipe")
    def test_readiness_survives_a_disconnected_stock_pipe(self):
        import _winapi

        reader, writer = _winapi.CreatePipe(None, 0)
        _winapi.CloseHandle(reader)
        with patch.object(coordinator, "_inherited_pipe", return_value=writer):
            self.assertIs(coordinator.write_inherited_ready(coordinator.Receipt("a" * 32, "b" * 32)), False)


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name).resolve()
        self.config = self.work / "coordinator.json"
        self.data = {"schema": 1, "work": str(self.work), "host": {"campaign": "trail", "bind": "127.0.0.1", "port": 27015},
                     "join": None}

    def write(self):
        self.config.write_text(json.dumps(self.data), encoding="utf-8")

    async def test_opens_one_qualified_plan_per_generation_and_publishes_status(self):
        self.write()
        receipt = coordinator.Receipt("a" * 32, "b" * 32)
        with patch.object(coordinator, "staged_title_menu", new_callable=AsyncMock) as menu:
            await coordinator.open_coop(receipt, self.config)
            with self.assertRaises(FileExistsError):
                await coordinator.open_coop(coordinator.Receipt(receipt.generation, "c" * 32), self.config)
        menu.assert_awaited_once()
        self.assertIs(menu.await_args.kwargs["require_embedded"], True)
        plan, = menu.await_args.args
        self.assertEqual((plan.work, plan.campaign, plan.bind, plan.port), (self.work, "trail", "127.0.0.1", 27015))
        status = self.work / "bootstrap-receipts" / (receipt.generation + ".json")
        self.assertEqual(coordinator.runtime.read_json(status)["state"], "stopped")

    async def test_failed_start_reports_the_exception_type_without_path_or_invite_data(self):
        self.write()
        receipt = coordinator.Receipt("d" * 32, "e" * 32)
        with patch.object(coordinator, "staged_title_menu", side_effect=RuntimeError("private-path")), self.assertRaises(RuntimeError):
            await coordinator.open_coop(receipt, self.config)
        status = self.work / "bootstrap-receipts" / (receipt.generation + ".json")
        self.assertEqual(coordinator.runtime.read_json(status), {
            "schema": 1, "generation": receipt.generation, "request_id": receipt.request_id,
            "state": "failed", "phase": "starting_title", "error_type": "RuntimeError",
        })

    async def test_publishes_readiness_for_the_exact_title_request(self):
        self.write()
        receipt = coordinator.Receipt("a" * 32, "b" * 32)
        status = self.work / "bootstrap-receipts" / (receipt.generation + ".json")
        sent = []

        async def menu(_plan, *, on_title_ready, require_embedded, headless):
            self.assertTrue(require_embedded)
            self.assertTrue(headless)
            self.assertEqual(coordinator.runtime.read_json(status)["state"], "opening")
            on_title_ready()

        def send(acknowledged):
            self.assertEqual(coordinator.runtime.read_json(status)["state"], "title_ready")
            sent.append(acknowledged)

        with (patch.object(coordinator, "staged_title_menu", side_effect=menu),
              patch.dict(coordinator.os.environ, {"SILVERFISH_COOP_BOOTSTRAP_HEADLESS": "1"})):
            await coordinator.open_coop(receipt, self.config, report_ready=send)
        self.assertEqual(sent, [receipt])

    async def test_packaged_title_refuses_a_stage_without_its_embedded_runtime(self):
        self.write()
        receipt = coordinator.Receipt("a" * 32, "b" * 32)
        with (patch.object(coordinator.runtime, "qualified_authored_routes", return_value=True),
              patch("tools.silverfish.embedded_title_plan.installed", return_value=False),
              patch.object(coordinator.runtime, "launch") as launch):
            with self.assertRaisesRegex(ValueError, "matched embedded runtime"):
                await coordinator.open_coop(receipt, self.config)
        launch.assert_not_called()

    def test_join_can_be_configured_before_an_invitation_exists(self):
        self.data["host"] = None
        self.data["join"] = {"profile": "guest", "invite": None, "character": None}
        self.write()
        plan = coordinator.load_plan(self.config)
        self.assertEqual((plan.interactive_join, plan.profile, plan.invite, plan.character), (True, "guest", None, None))
        self.data["join"]["character"] = "a8d8d498-8d4c-4bb3-b39c-2f594a783a78"
        self.write()
        with self.assertRaisesRegex(ValueError, "after importing an invitation"):
            coordinator.load_plan(self.config)

    def test_invitation_must_be_inside_the_coop_workspace(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        invite = Path(temp.name).resolve() / "private.invite.json"
        invite.write_text("{}", encoding="utf-8")
        self.data["join"] = {"profile": "guest", "invite": str(invite), "character": None}
        self.write()
        with self.assertRaisesRegex(ValueError, "private co-op workspace"):
            coordinator.load_plan(self.config)
