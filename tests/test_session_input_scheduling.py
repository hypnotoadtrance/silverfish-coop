import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import runtime, service


class SessionInputSchedulingTests(unittest.IsolatedAsyncioTestCase):
    def guest(self, request):
        control = Mock()
        transport = SimpleNamespace(address=("127.0.0.1", 7777), request=request, session_end=None)
        client = service.Client(control, transport, {"character_id": "guest"}, Mock())
        self.addAsyncCleanup(client.cancel_gameplay_snapshot)
        return client

    async def test_slow_snapshot_does_not_hold_guest_input_loop_or_start_duplicate(self):
        release = asyncio.Event()

        async def request(payload, *, timeout):
            await release.wait()
            return {"ok": True, "gameplay": {"players": {}}, "zone_epoch": "new", "zone": "CrossRoads"}

        transport_request = AsyncMock(side_effect=request)
        client = self.guest(transport_request)
        client.zone_epoch = "old"
        await asyncio.wait_for(client.refresh_gameplay(project=False, wait=False), .5)
        client.next_snapshot = 0
        await client.refresh_gameplay(project=False, wait=False)
        await asyncio.sleep(0)
        transport_request.assert_awaited_once_with({"action": "snapshot"}, timeout=2)
        self.assertFalse(client.gameplay_snapshot_ready())
        self.assertEqual(client.zone_epoch, "old")
        release.set()
        await client.refresh_gameplay(project=False)
        self.assertTrue(client.needs_travel)
        self.assertEqual(client.zone_epoch, "new")
        self.assertIsNone(client.snapshot_request)

    async def test_cancellation_discards_a_completed_snapshot_from_old_session(self):
        request = AsyncMock(return_value={"ok": True, "gameplay": {"old": True}, "zone_epoch": "retired"})
        client = self.guest(request)
        await client.refresh_gameplay(project=False, wait=False)
        await asyncio.sleep(0)
        self.assertTrue(client.gameplay_snapshot_ready())
        await client.cancel_gameplay_snapshot()
        await client.refresh_gameplay(project=False, wait=False)
        self.assertEqual(client.gameplay, {})
        self.assertIsNone(client.snapshot_request)

    async def test_bridge_idle_wait_wakes_for_a_host_request_without_local_input(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), Mock())
            requests = asyncio.Queue()
            wait = asyncio.create_task(bridge.wait_for_interaction(5, wake_if=lambda: not requests.empty()))
            await asyncio.sleep(0)
            requests.put_nowait("guest interaction")
            await asyncio.wait_for(wait, .5)
            self.assertEqual(requests.get_nowait(), "guest interaction")

    async def test_guest_idle_wait_wakes_for_new_primary_and_inventory_events(self):
        streams = (
            ("native-guest-primary-events", "primary_input_offset"),
            ("native-inventory-view-events", "inventory_view_offset"),
        )
        for filename, offset in streams:
            with self.subTest(filename=filename), TemporaryDirectory() as temporary:
                bridge = runtime.Bridge(Path(temporary), Mock())
                path = bridge.directory / filename
                path.write_bytes(b"consumed\n")
                setattr(bridge, offset, path.stat().st_size)
                waiting = asyncio.create_task(bridge.wait_for_interaction(5, guest=True))
                self.addAsyncCleanup(self.cancel_wait, waiting)
                await asyncio.sleep(.05)
                self.assertFalse(waiting.done())
                with path.open("ab") as stream:
                    stream.write(b"new press\n")
                await asyncio.wait_for(waiting, .20)

    async def cancel_wait(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
