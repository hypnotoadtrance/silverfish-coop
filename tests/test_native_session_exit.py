from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call

from tools.silverfish import native_session_exit as pause


class PauseExitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 900
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        run = root / "campaigns" / "test" / "run"
        self.directory = run / "game-bridge" / ("a" * 32)
        self.directory.mkdir(parents=True)
        self.control = SimpleNamespace(run=run, event=Mock())
        self.memory = Mock(has_pause_exit=True)
        self.memory.take_pause_exit.return_value = None
        self.bridge = SimpleNamespace(directory=self.directory, local_bridge=self.memory)
        engine = SimpleNamespace(bridge=self.bridge, session_directory=run.parent, poll=lambda: None)
        self.pause = pause.PauseExit(self.bridge, self.control, "host", engine=engine, clock=lambda: self.now)
        self.finish = AsyncMock(return_value={"revision": 14, "stopping": True})
        self.scope = ("control", "zone")
        self.addCleanup(self.pause.close)

    async def test_stock_press_reaches_worker_once_without_an_exit_file(self):
        await self.pause.poll(self.scope, self.finish)
        token = self.pause.active_token()
        self.memory.offer_pause_exit.assert_called_once_with(token.encode(), 1)
        self.memory.take_pause_exit.side_effect = [(1, "save_and_quit"), None]
        await self.pause.poll(self.scope, self.finish)
        self.memory.take_pause_exit.assert_called_once_with(token.encode(), 1)
        operation, = self.finish.await_args.args
        self.assertRegex(operation, r"^[a-f0-9]{32}$")
        self.control.event.assert_called_once_with("stock_pause_exit", operation_id=operation,
                                                   reason="save_and_quit", result="ok",
                                                   receipt={"revision": 14, "stopping": True})
        await self.pause.poll(self.scope, self.finish)
        self.finish.assert_awaited_once()
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_scene_change_and_loss_revoke_offer_before_taking_any_press(self):
        await self.pause.poll(self.scope, self.finish)
        first = self.pause.active_token()
        await self.pause.poll(("control", "next-zone"), self.finish)
        second = self.pause.active_token()
        self.assertNotEqual(first, second)
        self.assertEqual(self.memory.offer_pause_exit.call_args_list[-2:],
                         [call(None, 0), call(second.encode(), 1)])
        await self.pause.poll(None, self.finish)
        self.assertIsNone(self.pause.active_token())
        self.memory.offer_pause_exit.assert_called_with(None, 0)
        self.memory.take_pause_exit.assert_not_called()
        self.finish.assert_not_awaited()

    async def test_refused_checkpoint_permits_a_new_stock_press(self):
        await self.pause.poll(self.scope, self.finish)
        token = self.pause.active_token()
        self.finish.side_effect = [RuntimeError("no exact save frame"), self.finish.return_value]
        self.memory.take_pause_exit.side_effect = [(1, "quit_to_menu"), (2, "quit_to_menu")]
        await self.pause.poll(self.scope, self.finish)
        self.assertEqual(self.control.event.call_args.kwargs["result"], "error")
        self.assertIsNone(self.pause.active_token())
        await self.pause.poll(self.scope, self.finish)
        self.assertNotEqual(self.pause.active_token(), token)
        await self.pause.poll(self.scope, self.finish)
        self.assertEqual(self.finish.await_count, 2)
        self.assertNotEqual(*[awaited.args[0] for awaited in self.finish.await_args_list])
        self.assertEqual(self.control.event.call_args.kwargs["result"], "ok")

    async def test_drains_a_press_before_rotating_the_expired_offer(self):
        await self.pause.poll(self.scope, self.finish)
        token = self.pause.active_token()
        self.memory.take_pause_exit.return_value = (1, "save_and_quit")
        self.now += pause.OFFER_SECONDS + 3
        self.assertIsNone(self.pause.active_token())
        await self.pause.poll(self.scope, self.finish)
        self.memory.take_pause_exit.assert_called_once_with(token.encode(), 1)
        self.memory.offer_pause_exit.assert_called_once()
        self.finish.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
