import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import uuid

from tools.silverfish import service


class GuestInteractionLaneTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_interact_finishes_before_an_earlier_crate_reply(self):
        waiting_for_host, release_roll, ordinary_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        client = service.Client.__new__(service.Client)
        client.stopping = client.needs_travel = client.server_transitioning = False
        client.host_session, client.zone_epoch, client.expected_zone = "session", "visit", "map"
        client.last_game = {"state": "connected", "map": "map", "pawns": {"local": {"name": "pawn"}}}
        client.transport = SimpleNamespace(connected=True)
        client.invite = {"native_loot_input": True, "character_id": "guest"}
        client.control = SimpleNamespace(event=Mock())
        client.stock_door_requests = {}
        inputs = iter([b"crate press"])
        client.bridge = SimpleNamespace(
            local_bridge=object(), input_namespace=uuid.UUID(int=1),
            stock_interaction_ready=lambda *, crates: not waiting_for_host.is_set() if crates else not ordinary_done.is_set(),
            native_crate_inputs=lambda: list(inputs),
            interactions=Mock(return_value=[("pickup", "object")]),
        )
        client.crate_intents = SimpleNamespace(take=Mock(return_value=[(1, "crate", "pawn", "memory_input")]))
        lid_confirmed = asyncio.Event()

        async def project(_client, *, confirmed_crate=None):
            if confirmed_crate == "crate":
                lid_confirmed.set()

        projection = AsyncMock(side_effect=project)
        client.crate_lids = SimpleNamespace(sync=projection, turn_seconds=.15)
        roll = {"id": "roll", "map": "map", "crate": "crate"}
        client.gameplay = {service.native_loot.STATE_KEY: {"crate": roll}}

        async def open_crate(_action, _operation, _crate):
            waiting_for_host.set()
            await release_roll.wait()
            return {"native_loot_roll": roll}

        async def pickup(_scope, _operation, _target):
            ordinary_done.set()
            return {}

        client.gameplay_action = AsyncMock(side_effect=open_crate)
        client.apply_stock_interaction = AsyncMock(side_effect=pickup)
        crate_worker = asyncio.create_task(client.run_stock_crates())
        ordinary_worker = None
        try:
            await asyncio.wait_for(waiting_for_host.wait(), 1)
            ordinary_worker = asyncio.create_task(client.run_stock_interactions())
            await asyncio.wait_for(ordinary_done.wait(), 1)
            client.apply_stock_interaction.assert_awaited_once_with(("session", "visit", "map", "pawn"), "pickup", "object")
            projection.assert_not_awaited()
            release_roll.set()
            await asyncio.wait_for(lid_confirmed.wait(), 1)
        finally:
            client.stopping = True
            release_roll.set()
            await asyncio.wait_for(asyncio.gather(*(task for task in (crate_worker, ordinary_worker) if task)), 1)
        self.assertEqual(projection.await_args_list[0].kwargs, {"confirmed_crate": "crate"})

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
        client.stock_door_requests = {}
        def ready(*, crates):
            if action_started.is_set():
                drained.set()
                return False
            return True

        client.bridge = SimpleNamespace(
            local_bridge=object(),
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
