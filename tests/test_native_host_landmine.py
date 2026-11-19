import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tools.silverfish import native_host_landmine as mine, party
from tools.silverfish.travel import OBSERVATORY


GUID = "A1" * 16
PREPARE = "a" * 32
DEPLOY = "b" * 32
PAWN = "exact local host"


class NativeHostLandmineTests(unittest.IsolatedAsyncioTestCase):
    def host(self):
        state = party.new_party()
        for member in ("host", "guest"):
            party.add_player(state, member)
        return SimpleNamespace(native_guest=True, native_zones=True, fixtures=True, native_profile=True, stopping=False,
                               transitioning=False, native_checkpoint_unsafe=False, party=state,
                               last_game=self.snapshot("equipped"), last_seen=time.monotonic(),
                               native_recipients=lambda: {"guest": {"connected": True}},
                               checkpoint_native=AsyncMock())

    def snapshot(self, state, **updates):
        launched = state in ("released", "placed", "persisted")
        placed = state in ("placed", "persisted")
        saved = state == "persisted"
        return {"state": "hosting", "map": OBSERVATORY, "pawns": {"local": {"name": PAWN}},
                mine.KEY: dict({"map": OBSERVATORY, "pawn": PAWN, "state": state, "guid": GUID,
                    "actor": "stock mine" if placed else "-", "projectile": "stock projectile" if launched else "-",
                    "presses": int(state != "equipped"), "releases": int(launched), "actors": int(placed),
                    "saved": int(saved), "remaining": int(not launched), "guest_remaining": 0,
                    "same_transform": saved, "position": [5, 6, 7] if placed else None,
                    "active_projectiles": int(launched and not placed), "projectile_position": None,
                    "aim_dot": 1.0 if launched else None,
                    "projectile_state": "retired" if placed else "active" if launched else "unwatched"}, **updates)}

    def bridge(self, host, **persist_updates):
        next_frame = iter((self.snapshot("primed"), self.snapshot("placed")))

        async def command(action, pawn, *, timeout):
            self.assertTrue(host.native_checkpoint_unsafe)
            self.assertEqual(pawn, PAWN)
            step = {mine.PREPARE: "equipped", "native-host-landmine-press-fixture": "pressed",
                    "native-host-landmine-release-fixture": "released",
                    "native-host-landmine-persist-fixture": "persisted"}[action]
            return self.snapshot(step, **(persist_updates if step == "persisted" else {}))

        async def wait_for(condition, timeout):
            self.assertTrue(host.native_checkpoint_unsafe)
            packet = next(next_frame)
            self.assertTrue(condition(packet))
            return packet

        return SimpleNamespace(command=AsyncMock(side_effect=command), wait_for=AsyncMock(side_effect=wait_for))

    async def test_fenced_stock_lifecycle_publishes_one_replayable_typed_record(self):
        host = self.host()
        bridge = self.bridge(host)
        await mine.perform(host, bridge, mine.PREPARE, None, PREPARE)
        result = await mine.perform(host, bridge, mine.DEPLOY, GUID, DEPLOY)
        self.assertEqual(result, {mine.KEY: self.snapshot("persisted")[mine.KEY]})
        self.assertEqual(await mine.perform(host, bridge, mine.DEPLOY, GUID, DEPLOY), result)
        self.assertEqual([call.args[0] for call in bridge.command.await_args_list], [
            mine.PREPARE, "native-host-landmine-press-fixture", "native-host-landmine-release-fixture",
            "native-host-landmine-persist-fixture",
        ])
        self.assertEqual(bridge.wait_for.await_count, 2)
        self.assertEqual(host.checkpoint_native.await_args.kwargs["require_guest_frames"], frozenset({"guest"}))
        self.assertFalse(host.native_checkpoint_unsafe)

    async def test_duplicate_world_actors_never_commit(self):
        host = self.host()
        bridge = self.bridge(host, actors=2)
        await mine.perform(host, bridge, mine.PREPARE, None, PREPARE)
        with self.assertRaisesRegex(RuntimeError, "world frame"):
            await mine.perform(host, bridge, mine.DEPLOY, GUID, DEPLOY)
        self.assertEqual(host.checkpoint_native.await_count, 3)
        self.assertEqual([r["id"] for r in host.party["receipts"]["host"]], [PREPARE])
        self.assertTrue(host.native_checkpoint_unsafe)
        self.assertTrue(host.stopping)

    async def test_failed_placement_retains_the_last_exact_projectile_frame(self):
        host = self.host()
        bridge = self.bridge(host)
        await mine.perform(host, bridge, mine.PREPARE, None, PREPARE)
        frames = iter((self.snapshot("primed"), self.snapshot("released", active_projectiles=1,
                                                           projectile_position=[101, 102, 103])))

        async def wait_for(condition, timeout):
            packet = next(frames)
            if not condition(packet):
                raise TimeoutError("raw bridge timeout")
            return packet

        bridge.wait_for.side_effect = wait_for
        with self.assertRaisesRegex(RuntimeError, r"stock_host_landmine_placement_timeout:.*\[101,102,103\]"):
            await mine.perform(host, bridge, mine.DEPLOY, GUID, DEPLOY)
        self.assertEqual(bridge.command.await_count, 3)
        self.assertEqual(host.checkpoint_native.await_count, 3)
        self.assertTrue(host.stopping)

    async def test_missing_projectile_position_cannot_substitute_for_exact_retirement(self):
        host = self.host()
        bridge = self.bridge(host)
        await mine.perform(host, bridge, mine.PREPARE, None, PREPARE)
        frames = iter((self.snapshot("primed"), self.snapshot("placed", projectile_state="retirement_pending")))

        async def wait_for(condition, timeout):
            packet = next(frames)
            self.assertTrue(condition(packet))
            return packet

        bridge.wait_for.side_effect = wait_for
        with self.assertRaisesRegex(RuntimeError, r"stock_host_landmine_placement_retirement_unverified:.*retirement_pending"):
            await mine.perform(host, bridge, mine.DEPLOY, GUID, DEPLOY)
        self.assertEqual(bridge.command.await_count, 3)
        self.assertEqual(host.checkpoint_native.await_count, 3)
        self.assertTrue(host.stopping)

    async def test_exact_retirement_does_not_clear_a_second_projectile(self):
        host = self.host()
        bridge = self.bridge(host, active_projectiles=1)
        await mine.perform(host, bridge, mine.PREPARE, None, PREPARE)
        with self.assertRaisesRegex(RuntimeError, r"stock_host_landmine_persist_retirement_unverified:.*active_projectiles.*1"):
            await mine.perform(host, bridge, mine.DEPLOY, GUID, DEPLOY)
        self.assertEqual(bridge.command.await_count, 4)
        self.assertEqual(host.checkpoint_native.await_count, 3)
        self.assertTrue(host.stopping)

    def test_tsv_decodes_distinct_placed_actor_and_saved_location(self):
        frame = mine.parse(("native-host-landmine", OBSERVATORY, PAWN, "persisted", GUID, "stock mine", "stock projectile",
                            "1", "1", "1", "1", "0", "0", "1", "5", "6", "7"))
        self.assertTrue(self.snapshot("persisted")[mine.KEY].items() >= frame.items())
        extended = mine.parse(["native-host-landmine", OBSERVATORY, PAWN, "released", GUID, "-", "stock projectile",
                              "1", "1", "0", "0", "0", "0", "0", "-", "-", "-", "1", "5", "6", "7", "0.999", "active"])
        self.assertEqual(extended["projectile_position"], [5, 6, 7])
        self.assertEqual(extended["aim_dot"], .999)
        self.assertEqual(extended["projectile_state"], "active")
