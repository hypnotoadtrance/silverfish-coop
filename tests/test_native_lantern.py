from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from tools.silverfish import native_lantern as lantern


def observation(lit, phase="hosting"):
    return {"state": phase, "map": lantern.OBSERVATORY, lantern.KEY: lantern.parse([
        lantern.TAG, lantern.OBSERVATORY, lantern.ACTOR, "1" if lit else "0", "0", "1", "1", "2", "3",
    ], phase, lantern.OBSERVATORY)}


class ObservatoryLanternTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_epoch_applies_only_changed_exact_lantern_light(self):
        epoch = "a" * 32
        guest, after = observation(False, "connected"), observation(True, "connected")
        host = lantern.frame(observation(True), epoch)
        bridge = SimpleNamespace(command=AsyncMock(return_value=after))
        for frame in (dict(host, epoch="b" * 32), dict(host, actor="other"), dict(host, lit="1")):
            self.assertEqual(await lantern.project(bridge, guest, frame, epoch), guest)
        bridge.command.assert_not_awaited()
        self.assertEqual(await lantern.project(bridge, guest, host, epoch), after)
        self.assertEqual(await lantern.project(bridge, after, host, epoch), after)
        bridge.command.assert_awaited_once_with(lantern.ACTION, lantern.OBSERVATORY, lantern.ACTOR, "1", timeout=3)
        self.assertIsNone(lantern.frame(after, epoch), "a guest's light state is not authoritative")

    async def test_projection_needs_a_verified_light_change(self):
        epoch = "a" * 32
        guest = observation(False, "connected")
        bridge = SimpleNamespace(command=AsyncMock(return_value=guest))
        with self.assertRaisesRegex(RuntimeError, "did not match"):
            await lantern.project(bridge, guest, lantern.frame(observation(True), epoch), epoch)
