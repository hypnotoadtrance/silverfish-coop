import asyncio
from pathlib import Path
import tempfile
import unittest

from tools.silverfish.__main__ import _events
from tools.silverfish.control import Control, status


class ControlEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_resident_gameplay_events_wait_in_memory_until_requested(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with Control(run, buffer_events=True) as control:
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

    async def test_recent_events_stay_bounded_and_are_readable_after_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with Control(run, buffer_events=True) as control:
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

    async def test_legacy_diagnostic_log_remains_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with Control(run) as control:
                control.event("legacy")
                control.event("latest_legacy")

            result = await _events(run, 1)
            self.assertEqual([entry["event"] for entry in result["events"]], ["latest_legacy"])
