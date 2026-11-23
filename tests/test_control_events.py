import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.silverfish.__main__ import _events
from tools.silverfish.control import Control, current_status, status, submit
from tools.silverfish.runtime import read_json, write_json


class ControlEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_resident_gameplay_events_wait_in_memory_until_requested(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with Control(run, resident=True) as control:
                control.status(active=True, role="host", state="hosting")
                control.event("pickup", item="first")
                control.event("pickup", item="second")
                self.assertFalse((run / "events.jsonl").exists())
                self.assertNotIn("recent_events", status(run))

                requested = asyncio.create_task(_events(run, 1))
                await asyncio.sleep(0)
                self.assertEqual(list(control.requests()), [])
                result = await requested
                self.assertEqual([(entry["event"], entry["item"]) for entry in result["events"]],
                                 [("pickup", "second")])
                self.assertFalse((run / "events.jsonl").exists())
                self.assertFalse(list((run / "responses").glob("*.json")))

    async def test_recent_events_stay_bounded_and_are_readable_after_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with Control(run, resident=True) as control:
                for index in range(64):
                    control.event("pickup", index=index)
                control.event("diagnostic", detail="a" * 9_000)
                control.status(active=False, role="host", state="stopped")

            result = await _events(run, 100)
            self.assertEqual(len(result["events"]), 64)
            self.assertEqual(result["events"][0]["index"], 1)
            self.assertTrue(result["events"][-1]["details_omitted"])
            self.assertNotIn("detail", result["events"][-1])
            self.assertFalse((run / "events.jsonl").exists())

    async def test_resident_status_updates_are_readable_without_polling_disk(self):
        command = (
            "import asyncio,json,sys; from pathlib import Path; "
            "from tools.silverfish.control import current_status; "
            "print(json.dumps(asyncio.run(current_status(Path(sys.argv[1])))))"
        )
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with Control(run, resident=True) as control:
                control.status(active=True, role="host", state="hosting", game={"sequence": 1})
                file = run / "status.json"
                bootstrap = read_json(file)
                bootstrap["updated_unix"] -= 60
                write_json(file, bootstrap)
                before = file.read_bytes()

                control.status(active=True, role="host", state="hosting", game={"sequence": 2})
                self.assertEqual(status(run)["game"]["sequence"], 2)
                self.assertEqual(file.read_bytes(), before)

                requester = await asyncio.create_subprocess_exec(
                    sys.executable, "-B", "-c", command, str(run), stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                response = asyncio.create_task(requester.communicate())
                try:
                    async with asyncio.timeout(4):
                        while not response.done():
                            self.assertEqual(list(control.requests()), [])
                            await asyncio.sleep(.01)
                        stdout, stderr = await response
                finally:
                    if requester.returncode is None:
                        requester.kill()
                        await requester.wait()
                self.assertEqual(requester.returncode, 0, stderr.decode())
                self.assertEqual(json.loads(stdout)["game"]["sequence"], 2)
                self.assertEqual(file.read_bytes(), before)
                self.assertFalse(list((run / "responses").glob("*.json")))

    async def test_legacy_diagnostic_log_remains_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with Control(run) as control:
                control.event("legacy")
                control.event("latest_legacy")

            result = await _events(run, 1)
            self.assertEqual([entry["event"] for entry in result["events"]], ["latest_legacy"])

    async def test_ended_worker_is_unavailable_even_with_recent_active_descriptor(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with Control(run, resident=True) as control:
                control.status(active=True, role="host", state="hosting")

            self.assertTrue((await current_status(run))["stale"])
            with self.assertRaisesRegex(RuntimeError, "No active controller"):
                await submit(run, "events-recent", tail=1)
            self.assertFalse((run / "requests").exists())
