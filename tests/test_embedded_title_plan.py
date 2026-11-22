import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tools.silverfish import embedded_startup as startup, embedded_title_plan as plans, runtime, service
from tools.silverfish.title_handoff import Claim
from tools.silverfish.travel import OBSERVATORY


class EmbeddedTitlePlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_resident_steam_host_uses_a_loopback_listener_and_a_separate_unreal_port(self):
        work = Path(self.enterContext(TemporaryDirectory())).resolve(strict=True)
        campaign = work / "campaigns" / "trial"
        steam_roles = []

        @asynccontextmanager
        async def playing(*, role):
            steam_roles.append(role)
            yield

        steam = SimpleNamespace(playing=playing, pump=SimpleNamespace(done=lambda: False))
        binding = startup._resident_host_plan(
            work, campaign, work / "bridge", work / "saved", False, "0.0.0.0", 7777,
            host_options={}, steam=steam,
        )
        choice = Claim("host", "b" * 32, "c" * 32, binding, host_settings=("", "", ""),
                       direct_password="test pass", stock_creator=True, steam_access="public")
        with (patch.object(service, "menu_host_start", return_value=("wanderer", OBSERVATORY)),
              patch.object(runtime, "spare_udp_port", return_value=28101),
              patch.object(startup, "EmbeddedEngine") as engine,
              patch.object(service, "host_game", new_callable=AsyncMock) as host):
            engine.return_value.observe_title = AsyncMock()
            plan = binding.resolve_claim(choice)
            await plan.start()
            self.assertEqual(host.call_args.args, (work, campaign, "127.0.0.1", 0, False))
            self.assertEqual(engine.call_args.args[-1], 28101)
            self.assertEqual(host.call_args.kwargs["steam_access"], "public")
            self.assertEqual(host.call_args.kwargs["direct_password"], "test pass")
            self.assertEqual(steam_roles, ["host"])
            self.assertTrue(plan.ready({"endpoint": {"address": "127.0.0.1", "port": 28100}}))
            self.assertFalse(plan.ready({"endpoint": {"address": "0.0.0.0", "port": 28100}}))

    async def test_host_plan_publishes_only_for_the_owned_game_and_accepts_its_receipt(self):
        work = Path(self.enterContext(TemporaryDirectory())).resolve(strict=True)
        campaign = work / "campaigns" / "trial"
        process = SimpleNamespace(pid=27891, poll=lambda: None)
        launched = asyncio.Event()

        def launch(_work, profile, bridge, headless, port, **options):
            self.assertEqual((profile, headless, port), (campaign / "game-user", True, 28101))
            self.assertTrue(options["embedded_engine"])
            self.assertTrue(options["runtime_listener"])
            self.assertEqual(options["embedded_startup"], bridge.name)
            profile.mkdir(parents=True)
            bridge.mkdir(parents=True)
            launched.set()
            return process

        with (patch.object(runtime, "staged_build", return_value="a" * 64),
              patch.object(runtime, "spare_udp_port", side_effect=(28100, 28101)),
              patch.object(runtime, "launch", side_effect=launch),
              patch.object(runtime, "stop_game", new_callable=AsyncMock) as stop):
            plan = plans.host_plan(work, campaign, "127.0.0.1", 28100, "wanderer", OBSERVATORY, headless=True)
            claim = Claim("host", "b" * 32, "c" * 32, plan)
            task = asyncio.create_task(plan.start(claim))
            try:
                await asyncio.wait_for(launched.wait(), 1)
                bridge, = (campaign / "run" / "game-bridge").iterdir()
                request = startup.parse_request((bridge / startup.REQUEST_FILE).read_bytes())
                self.assertEqual((request.pid, request.title_request_id, request.title_generation),
                                 (process.pid, claim.request_id, claim.generation))
                self.assertEqual(request.selection, startup.HostSelection("127.0.0.1", 28100, 28101, "wanderer"))
                self.assertIsNone(plan.producer_pid(claim))
                (bridge / startup.ACCEPTED_FILE).write_bytes(startup._record(startup._receipt(request)))
                self.assertIsNone(plan.producer_pid(replace(claim, request_id="d" * 32)))
                self.assertEqual(plan.producer_pid(claim), process.pid)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            stop.assert_awaited_once_with(process)
