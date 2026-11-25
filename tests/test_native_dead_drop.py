"""Check the exact Dead Drop authority, lease and durable claim boundary."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tools.silverfish import native_authored_pickup as pickup, native_dead_drop as drop, party
from tools.silverfish.travel import CITY


EPOCH = "a" * 32
PAWN = f"Player_FP_C {drop.WORLD}:PersistentLevel.Player_FP_C_41"
POSE = {"position": [-17391, 16852, 474], "rotation": [3, 91, -1], "scale": [1, 1, 1]}
NEW_POSE = {**POSE, "position": [-18641, 17108, 522]}


def axes(pose):
    return tuple(str(axis) for name in ("position", "rotation", "scale") for axis in pose[name])


def stock_game(phase, state, *, epoch=None, pawn=PAWN, transform=POSE):
    report = {"state": state, "map": drop.MAP, "actor": drop.ACTOR, "pawn": pawn, "epoch": epoch}
    if state in ("stock-active", "projected"):
        report["transform"] = transform
    return {"state": phase, "map": drop.MAP, "pawns": {"local": {"name": pawn}}, drop.KEY: report}


def accepted():
    return {"native_quests": {drop.QUEST: {"quest_id": drop.QUEST, "step": 1}}}


def host():
    return SimpleNamespace(native_guest=True, native_zones=True, native_profile=True, stopping=False, transitioning=False,
                           native_checkpoint_unsafe=False, last_seen=time.monotonic(), zone_epoch=EPOCH, party=accepted(),
                           last_game=stock_game("hosting", "stock-active"), checkpoint_native=AsyncMock())


class DeadDropFrameTests(unittest.TestCase):
    def test_phase_epoch_map_and_exact_actor_are_required(self):
        row = [drop.TAG, "projected", drop.MAP, drop.ACTOR, PAWN, EPOCH, *axes(POSE)]
        self.assertEqual(drop.parse(row, "connected", drop.MAP), stock_game("connected", "projected", epoch=EPOCH)[drop.KEY])
        for wrong in ("-", "0" * 32, "X" * 32):
            row[5] = wrong
            with self.assertRaisesRegex(ValueError, "identity"):
                drop.parse(row, "connected", drop.MAP)
        row[5] = EPOCH
        for phase, area in (("hosting", drop.MAP), ("connected", CITY)):
            with self.assertRaisesRegex(ValueError, "identity"):
                drop.parse(row, phase, area)

    def test_authority_requires_canonical_pose_and_committed_unclaimed_step(self):
        source = host()
        self.assertEqual(drop.frame(source), {"map": drop.MAP, "quest": drop.QUEST, "actor": drop.ACTOR,
                                               "step": 1, "epoch": EPOCH, "transform": POSE})
        source.last_game[drop.KEY]["transform"] = NEW_POSE
        self.assertEqual(drop.frame(source)["transform"], NEW_POSE)
        source.last_game[drop.KEY]["state"] = "hidden"
        self.assertEqual(drop.frame(source), {})
        source.last_game[drop.KEY]["state"] = "stock-active"
        source.party[drop.PICKUPS] = {drop.SOURCE: {"guid": "B" * 32}}
        self.assertEqual(drop.frame(source), {})
        source.party.pop(drop.PICKUPS)
        source.party["native_quests"][drop.QUEST]["step"] = -10
        self.assertEqual(drop.frame(source), {})
        source.party = accepted()
        source.last_seen -= 3
        self.assertEqual(drop.frame(source), {})
        source.last_seen = time.monotonic()
        source.transitioning = True
        self.assertEqual(drop.frame(source), {})


class DeadDropProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_renew_only_fresh_exact_epoch_then_retire_when_claimed(self):
        hidden = stock_game("connected", "hidden")
        projected = stock_game("connected", "projected", epoch=EPOCH)
        published = accepted()
        published[drop.FRAME] = drop.frame(host())
        moved = stock_game("connected", "projected", epoch=EPOCH, transform=NEW_POSE)
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[projected, moved, moved, hidden]))
        client = SimpleNamespace(last_game=hidden, zone_epoch=EPOCH, host_session="session", needs_travel=False,
                                 server_transitioning=False, gameplay_seen=time.monotonic(), gameplay=published,
                                 dead_drop_projection=(None, 0.0), bridge=bridge)

        await drop.project_client(client)
        bridge.command.assert_awaited_once_with("native-dead-drop-project", EPOCH, PAWN, *axes(POSE), timeout=3)
        await drop.project_client(client)
        self.assertEqual(bridge.command.await_count, 1)
        published[drop.FRAME]["transform"] = NEW_POSE
        await drop.project_client(client)
        bridge.command.assert_awaited_with("native-dead-drop-project", EPOCH, PAWN, *axes(NEW_POSE), timeout=3)
        self.assertEqual(bridge.command.await_count, 2)
        client.dead_drop_projection = (client.dead_drop_projection[0], time.monotonic() - 1)
        await drop.project_client(client)
        self.assertEqual(bridge.command.await_count, 3)
        published[drop.PICKUPS] = {drop.SOURCE: {"guid": "B" * 32}}
        await drop.project_client(client)
        bridge.command.assert_awaited_with("native-dead-drop-clear", timeout=3)
        self.assertEqual(client.last_game, hidden)
        await drop.project_client(client)
        self.assertEqual(bridge.command.await_count, 4)

    async def test_claim_disallows_inactive_and_stale_epoch_before_checkpoint(self):
        source = host()
        source.party = party.new_party()
        bridge = SimpleNamespace(command=AsyncMock())
        for target, reason in ((f"{drop.SOURCE}@{EPOCH}", "dead_drop_inactive"),
                               (f"{drop.SOURCE}@" + "b" * 32, "stale_zone")):
            with self.subTest(target=target), self.assertRaisesRegex(party.RuleError, reason):
                await pickup.claim(source, bridge, "guest", target, "c" * 32)
        source.checkpoint_native.assert_not_awaited()
        bridge.command.assert_not_awaited()
