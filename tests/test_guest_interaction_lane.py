import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import service


class GuestInteractionLaneTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_while_the_view_loop_waits_and_discards_stale_input(self):
        release_view = asyncio.Event()
        action_started = asyncio.Event()
        action_release = asyncio.Event()
        drained = asyncio.Event()
        client = service.Client.__new__(service.Client)
        client.stopping = client.needs_travel = client.server_transitioning = False
        client.host_session, client.zone_epoch = "session", "visit"
        client.expected_zone = "/Game/Classes/Maps/Test_P"
        client.last_game = {"state": "connected", "map": client.expected_zone, "pawns": {"local": {"name": "pawn"}}}
        client.transport = SimpleNamespace(connected=True)
        client.invite = {"native_loot_input": False}
        client.control = SimpleNamespace(event=Mock())
        client.service_crate_interaction = AsyncMock()
        def ready(*, crates):
            if action_started.is_set():
                drained.set()
                return False
            return True

        client.bridge = SimpleNamespace(
            stock_interaction_ready=ready,
            interactions=Mock(return_value=[("first", "object"), ("later", "object")]),
        )

        async def apply(_scope, _operation, _target):
            action_started.set()
            await action_release.wait()
            return {}

        client.apply_stock_interaction = AsyncMock(side_effect=apply)
        view = asyncio.create_task(release_view.wait())
        lane = asyncio.create_task(client.run_stock_interactions())
        try:
            await asyncio.wait_for(action_started.wait(), 1)
            self.assertFalse(view.done())
            client.zone_epoch = "new visit"
            action_release.set()
            await asyncio.wait_for(drained.wait(), 1)
            client.stopping = True
            await asyncio.wait_for(lane, 1)
            client.apply_stock_interaction.assert_awaited_once_with(
                ("session", "visit", client.expected_zone, "pawn"), "first", "object")
        finally:
            client.stopping = True
            action_release.set()
            release_view.set()
            await asyncio.gather(view, lane)
