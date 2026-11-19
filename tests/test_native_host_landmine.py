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
                    "same_transform": saved, "position": [5, 6, 7] if placed else None}, **updates)}

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

    def test_tsv_decodes_distinct_placed_actor_and_saved_location(self):
        frame = mine.parse(("native-host-landmine", OBSERVATORY, PAWN, "persisted", GUID, "stock mine", "stock projectile",
                            "1", "1", "1", "1", "0", "0", "1", "5", "6", "7"))
        self.assertEqual(frame, self.snapshot("persisted")[mine.KEY])
