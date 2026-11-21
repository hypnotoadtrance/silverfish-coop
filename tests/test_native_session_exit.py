import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_session_exit as pause


class PauseExitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 900
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        run = root / "campaigns" / "test" / "run"
        self.bridge = run / "game-bridge" / ("a" * 32)
        self.bridge.mkdir(parents=True)
        self.control = SimpleNamespace(run=run, event=Mock())
        self.mailbox = pause.PauseExit(self.bridge, self.control, "host", clock=lambda: self.now)
        self.finish = AsyncMock(return_value={"revision": 14, "stopping": True})
        self.scope = ("control", "zone")
        self.addCleanup(self.mailbox.close)

    def offer(self):
        descriptor = (self.bridge / pause.SESSION).read_bytes()
        version, token, role, expires = descriptor.decode("ascii").removesuffix("\n").split("\t")
        self.assertEqual((version, role), ("1", "host"))
        self.assertFalse(descriptor.endswith(b"\r\n"))
        return token, int(expires)

    def request(self, token, serial=1, role="host", reason="save_and_quit"):
        (self.bridge / pause.REQUEST).write_bytes(f"1\t{token}\t{serial}\t{role}\t{reason}\n".encode("ascii"))

    async def test_durable_receipt_reports_the_stock_reason_once(self):
        await self.mailbox.poll(self.scope, self.finish)
        token, expiry = self.offer()
        self.assertEqual(expiry, self.now + pause.OFFER_SECONDS)
        self.request(token)
        await self.mailbox.poll(self.scope, self.finish)
        operation, = self.finish.await_args.args
        self.assertRegex(operation, r"^[a-f0-9]{32}$")
        self.assertEqual((self.bridge / pause.RESPONSE).read_bytes(), f"1\t{token}\t1\tok\tstopping\n".encode())
        self.control.event.assert_called_once_with("stock_pause_exit", operation_id=operation,
                                                   reason="save_and_quit", result="ok",
                                                   receipt={"revision": 14, "stopping": True})
        self.request(token)
        await self.mailbox.poll(self.scope, self.finish)
        self.finish.assert_awaited_once()

    async def test_different_role_scope_and_elapsed_offer_cannot_end_the_session(self):
        await self.mailbox.poll(self.scope, self.finish)
        first, expiry = self.offer()
        self.request(first, role="guest")
        await self.mailbox.poll(self.scope, self.finish)
        self.request(first)
        self.now = expiry
        await self.mailbox.poll(self.scope, self.finish)
        second, _ = self.offer()
        self.assertNotEqual(first, second)
        self.request(second)
        await self.mailbox.poll(("control", "next-zone"), self.finish)
        third, _ = self.offer()
        self.request(third)
        await self.mailbox.poll(None, self.finish)
        self.assertFalse((self.bridge / pause.SESSION).exists())
        self.assertFalse((self.bridge / pause.REQUEST).exists())
        self.finish.assert_not_awaited()

    async def test_refused_checkpoint_permits_only_a_new_stock_press(self):
        await self.mailbox.poll(self.scope, self.finish)
        token, _ = self.offer()
        self.finish.side_effect = [RuntimeError("pausing state has no exact save frame"), self.finish.return_value]
        self.request(token, reason="quit_to_menu")
        await self.mailbox.poll(self.scope, self.finish)
        response = self.bridge / pause.RESPONSE
        self.assertTrue(response.read_bytes().endswith(b"\terror\texit_refused\n"))
        self.request(token, reason="quit_to_menu")
        await self.mailbox.poll(self.scope, self.finish)
        self.finish.assert_awaited_once()
        next_token, _ = self.offer()
        self.assertNotEqual(token, next_token)
        self.request(next_token, serial=2, reason="quit_to_menu")
        await self.mailbox.poll(self.scope, self.finish)
        self.assertEqual(self.finish.await_count, 2)
        self.assertNotEqual(*[call.args[0] for call in self.finish.await_args_list])
        self.assertTrue(response.read_bytes().endswith(b"\tok\tstopping\n"))

    async def test_press_made_before_expiry_survives_a_checkpoint_delay(self):
        await self.mailbox.poll(self.scope, self.finish)
        token, expiry = self.offer()
        self.request(token)
        os.utime(self.bridge / pause.REQUEST, (expiry - 1, expiry - 1))
        self.now = expiry + 3
        await self.mailbox.poll(self.scope, self.finish)
        self.finish.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
