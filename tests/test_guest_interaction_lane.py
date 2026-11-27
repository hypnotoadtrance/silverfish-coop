import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import uuid

from tools.silverfish import service
from tools.silverfish.travel import OBSERVATORY


class GuestInteractionLaneTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_switch_reports_keep_their_order_during_a_pending_pickup(self):
        client = service.Client.__new__(service.Client)
        client.stopping = client.needs_travel = client.server_transitioning = False
        client.host_session, client.zone_epoch, client.expected_zone = "session", "visit", OBSERVATORY
        client.last_game = {"state": "connected", "map": OBSERVATORY, "pawns": {"local": {"name": "pawn"}}}
        client.transport = SimpleNamespace(connected=True)
        client.control = SimpleNamespace(event=Mock())
        client.stock_door_requests = {}
        pickup_started, finish_pickup, carry_polled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        first_report, finish_report, second_report = asyncio.Event(), asyncio.Event(), asyncio.Event()
        read_this_tick = []
        client.prop_carry = SimpleNamespace(poll_local_edges=lambda _scope: pickup_started.is_set() and carry_polled.set())
        client.bridge = SimpleNamespace(
            local_bridge=object(),
            stock_interaction_ready=lambda *, crates: bool(read_this_tick),
            interactions=lambda: [read_this_tick.pop(0) for _ in range(len(read_this_tick))],
        )
        radio = f"Prop_Radio01_C {OBSERVATORY}.Observitory_P:PersistentLevel.Prop_Radio01_C_1"
        read_this_tick.extend([("pickup", None), ("on", f"stock-switch-state:2:1:{radio}"),
                               ("off", f"stock-switch-state:3:0:{radio}")])

        async def apply(action, operation, target):
            if operation == "pickup":
                pickup_started.set()
                await finish_pickup.wait()
            elif operation == "on":
                self.assertEqual((action, target), (service.native_stock_switch.REPORT, f"{radio}@1"))
                first_report.set()
                await finish_report.wait()
            elif operation == "off":
                self.assertEqual((action, target), (service.native_stock_switch.REPORT, f"{radio}@0"))
                second_report.set()
            return {}

        client.gameplay_action = AsyncMock(side_effect=apply)
        worker = asyncio.create_task(client.run_stock_interactions())
        try:
            await asyncio.wait_for(pickup_started.wait(), 1)
            await asyncio.wait_for(first_report.wait(), 1)
            await asyncio.wait_for(carry_polled.wait(), 1)
            self.assertFalse(second_report.is_set())
            finish_report.set()
            await asyncio.wait_for(second_report.wait(), 1)
            self.assertFalse(finish_pickup.is_set())
            self.assertEqual([entry.args[1] for entry in client.gameplay_action.await_args_list],
                             ["pickup", "on", "off"])
        finally:
            client.stopping = True
            finish_pickup.set()
            finish_report.set()
            await asyncio.wait_for(worker, 1)

    async def test_a_second_crate_and_ordinary_interact_finish_during_a_pending_crate_reply(self):
        waiting_for_host, release_roll, ordinary_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        client = service.Client.__new__(service.Client)
        client.stopping = client.needs_travel = client.server_transitioning = False
        client.host_session, client.zone_epoch, client.expected_zone = "session", "visit", "map"
        client.last_game = {"state": "connected", "map": "map", "pawns": {"local": {"name": "pawn"}}}
        client.transport = SimpleNamespace(connected=True, game_access=True)
        client.invite = {"native_loot_input": True, "character_id": "guest"}
        client.control = SimpleNamespace(event=Mock())
        client.stock_door_requests = {}
        client.prop_carry = SimpleNamespace(poll_local_edges=Mock())
        presses = [(1, "crate", "pawn", "memory_input")]
        client.bridge = SimpleNamespace(
            local_bridge=object(), input_namespace=uuid.UUID(int=1),
            stock_interaction_ready=lambda *, crates: bool(presses) if crates else not ordinary_done.is_set(),
            native_crate_inputs=lambda: [],
            interactions=Mock(return_value=[("pickup", "object")]),
        )

        def take(_game, _inputs):
            batch = presses.copy()
            presses.clear()
            return batch

        client.crate_intents = SimpleNamespace(take=take)
        lid_confirmed, second_crate_done = asyncio.Event(), asyncio.Event()

        async def project(_client, *, confirmed_crate=None):
            if confirmed_crate == "crate":
                lid_confirmed.set()

        projection = AsyncMock(side_effect=project)
        client.crate_lids = service.native_loot.GuestCrateProjection()
        client.crate_lids.sync = projection
        roll = {"id": "roll", "map": "map", "crate": "crate"}
        client.gameplay = {service.native_loot.STATE_KEY: {"crate": roll}}

        async def open_crate(_action, _operation, crate):
            if crate == "crate":
                waiting_for_host.set()
                await release_roll.wait()
            else:
                second_crate_done.set()
            return {"native_loot_roll": {"id": "roll", "map": "map", "crate": crate}}

        async def pickup(_scope, _operation, _target):
            ordinary_done.set()
            return {}

        client.gameplay_action = AsyncMock(side_effect=open_crate)
        client.apply_stock_interaction = AsyncMock(side_effect=pickup)
        crate_worker = asyncio.create_task(client.run_stock_crates())
        projection_worker = asyncio.create_task(client.crate_lids.run(client))
        ordinary_worker = None
        try:
            await asyncio.wait_for(waiting_for_host.wait(), 1)
            ordinary_worker = asyncio.create_task(client.run_stock_interactions())
            await asyncio.wait_for(ordinary_done.wait(), 1)
            presses.extend(((2, "crate", "pawn", "memory_input"), (3, "second", "pawn", "memory_input")))
            await asyncio.wait_for(second_crate_done.wait(), 1)
            self.assertEqual([call.args[2] for call in client.gameplay_action.await_args_list], ["crate", "second"])
            self.assertFalse(release_roll.is_set())
            client.apply_stock_interaction.assert_awaited_once_with(("session", "visit", "map", "pawn"), "pickup", "object")
            self.assertTrue(all(not call.kwargs for call in projection.await_args_list))
            release_roll.set()
            await asyncio.wait_for(lid_confirmed.wait(), 1)
        finally:
            client.stopping = True
            release_roll.set()
            workers = [task for task in (crate_worker, projection_worker, ordinary_worker) if task is not None]
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        self.assertEqual([call.kwargs["confirmed_crate"] for call in projection.await_args_list if call.kwargs], ["crate"])

    async def test_crate_requests_dispatch_while_lid_readback_is_pending(self):
        client = service.Client.__new__(service.Client)
        client.stopping = client.needs_travel = client.server_transitioning = False
        client.host_session, client.zone_epoch, client.expected_zone = "session", "visit", "map"
        client.last_game = {"state": "connected", "map": "map", "pawns": {"local": {"name": "pawn"}}}
        client.transport = SimpleNamespace(connected=True, game_access=True)
        client.invite = {"native_loot_input": True, "character_id": "guest"}
        client.control = SimpleNamespace(event=Mock())
        records = []
        client.bridge = SimpleNamespace(
            local_bridge=object(), input_namespace=uuid.UUID(int=1),
            stock_interaction_ready=lambda *, crates: bool(records),
            native_crate_inputs=lambda: [],
        )
        client.crate_intents = SimpleNamespace(take=lambda _game, _inputs: [records.pop(0)])
        guest_rolls = {crate: {"id": crate, "map": "map", "crate": crate} for crate in ("first", "second", "stale")}
        client.gameplay = {service.native_loot.STATE_KEY: guest_rolls}
        received = {crate: asyncio.Event() for crate in guest_rolls}
        background_started, release_background = asyncio.Event(), asyncio.Event()
        confirmed_started, release_confirmed = asyncio.Event(), asyncio.Event()
        fresh_lid = asyncio.Event()
        attempts = []

        async def project(_client, *, confirmed_crate=None):
            attempts.append(confirmed_crate)
            if confirmed_crate is None and not background_started.is_set():
                background_started.set()
                await release_background.wait()
            elif confirmed_crate == "first":
                confirmed_started.set()
                await release_confirmed.wait()
            elif confirmed_crate == "second":
                fresh_lid.set()

        async def open_crate(_action, _operation, crate):
            received[crate].set()
            return {"native_loot_roll": guest_rolls[crate]}

        client.gameplay_action = AsyncMock(side_effect=open_crate)
        client.crate_lids = service.native_loot.GuestCrateProjection()
        client.crate_lids.sync = AsyncMock(side_effect=project)
        input_worker = asyncio.create_task(client.run_stock_crates())
        projection_worker = asyncio.create_task(client.crate_lids.run(client))
        try:
            await asyncio.wait_for(background_started.wait(), 1)
            records.append((1, "first", "pawn", "memory_input"))
            await asyncio.wait_for(received["first"].wait(), .3)
            self.assertEqual(attempts, [None])
            release_background.set()
            await asyncio.wait_for(confirmed_started.wait(), 1)

            records.append((2, "stale", "pawn", "memory_input"))
            await asyncio.wait_for(received["stale"].wait(), .3)
            client.zone_epoch = "next-visit"
            records.append((3, "second", "pawn", "memory_input"))
            await asyncio.wait_for(received["second"].wait(), .3)
            self.assertEqual(attempts, [None, "first"])
            release_confirmed.set()
            await asyncio.wait_for(fresh_lid.wait(), 1)
            self.assertNotIn("stale", attempts)
            self.assertEqual(attempts[:3], [None, "first", "second"])
        finally:
            input_worker.cancel()
            projection_worker.cancel()
            await asyncio.gather(input_worker, projection_worker, return_exceptions=True)

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
        client.prop_carry = SimpleNamespace(poll_local_edges=Mock())
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


class GuestCombatInputLaneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ("map", "local pawn", "A" * 32, "rusty-pistol")
        area, pawn, guid, kind = self.scope
        self.client = client = service.Client.__new__(service.Client)
        client.__dict__.update(
            guest_primary_input=True, viewer_role="guest", stopping=False, host_session="session",
            zone_epoch="visit", expected_zone=area, needs_travel=False, server_transitioning=False,
            transport=SimpleNamespace(connected=True), gameplay_seen=service.time.monotonic(),
            invite={"character_id": "guest"},
            gameplay={"players": {"guest": {"character_id": "guest", "downed": False}},
                      "native_equipped_weapon": {"map": area, "pawn": "host replica", "guid": guid, "kind": kind}},
            last_game={"state": "connected", "map": area, "pawns": {"local": {"name": pawn}}},
            primary_scope=self.scope, primary_request=("session", "visit", self.scope, ()),
            next_primary_renewal=service.time.monotonic() + 10,
            last_primary_intent=0, melee_input=None, control=Mock(), tube_reload=None,
            bridge=Mock(input_namespace=uuid.UUID(int=1)),
        )

    async def test_standing_input_does_not_wait_for_view_updates_and_drops_an_old_visit(self):
        client = self.client
        area, pawn, guid, _ = self.scope
        waiting, resume, reloaded = asyncio.Event(), asyncio.Event(), asyncio.Event()
        client.bridge.snapshot.return_value = client.last_game
        client.bridge.native_primary_input_requests.side_effect = [[(1, *self.scope), (2, *self.scope)], []]
        client.bridge.take_guest_reload_input.return_value = None

        async def fire(*_):
            waiting.set()
            await resume.wait()
            return {}

        async def reload(active, game):
            self.assertFalse(active)
            self.assertEqual(game["map"], area)
            client.stopping = True
            reloaded.set()

        client.gameplay_action = AsyncMock(side_effect=fire)
        client.service_reload_input = AsyncMock(side_effect=reload)
        worker = asyncio.create_task(client.run_stock_combat_inputs())
        try:
            await asyncio.wait_for(waiting.wait(), 1)
            operation = uuid.uuid5(client.bridge.input_namespace, f"session:visit:{pawn}:primary:1").hex
            client.gameplay_action.assert_awaited_once_with("native-guest-weapon-fire", operation, guid)
            client.service_reload_input.assert_not_awaited()
            client.zone_epoch = "new visit"
            resume.set()
            await asyncio.wait_for(reloaded.wait(), 1)
            self.assertEqual(client.last_primary_intent, 2)
            client.gameplay_action.assert_awaited_once()
        finally:
            client.stopping = True
            resume.set()
            await asyncio.wait_for(worker, 1)

    async def test_new_weapon_scope_is_requested_before_the_prior_lease_renews(self):
        client = self.client
        new_scope = (*self.scope[:2], "B" * 32, "revolver-9mm")
        status = dict(zip(("map", "pawn", "guid", "kind"), new_scope, strict=True))
        client.gameplay["native_equipped_weapon"].update(guid=new_scope[2], kind=new_scope[3])
        client.bridge.command = AsyncMock(return_value={**client.last_game, "native_guest_primary_scope": status})
        await client.sync_primary_scope(True)
        await client.sync_primary_scope(True)
        client.bridge.command.assert_awaited_once_with("native-guest-primary-scope", *new_scope, timeout=2)
        self.assertEqual(client.primary_scope, new_scope)
