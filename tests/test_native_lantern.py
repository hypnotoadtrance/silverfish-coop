from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from tools.silverfish import native_lantern as lantern, native_stock_switch as switches, party


def observation(lit, phase="hosting"):
    return {"state": phase, "map": lantern.OBSERVATORY, lantern.KEY: lantern.parse([
        lantern.TAG, lantern.OBSERVATORY, lantern.ACTOR, "1" if lit else "0", "0", "1", "1", "2", "3",
    ], phase, lantern.OBSERVATORY)}


class ObservatoryLanternTests(unittest.IsolatedAsyncioTestCase):
    async def test_stock_switch_state_takes_precedence_over_an_older_lantern_sample(self):
        epoch = "a" * 32
        host = observation(False)
        rows = {lantern.ACTOR: {"powered": True, "angles": [0, 0, 0], "lights": {}}}
        stale = lantern.published(host, epoch)
        self.assertIsNotNone(stale)
        self.assertIsNone(lantern.published({**host, switches.KEY: rows}, epoch))

        guest = observation(True, "connected")
        after = observation(False, "connected")
        bridge = SimpleNamespace(command=AsyncMock(return_value=after))
        current = {"map": lantern.OBSERVATORY, "epoch": epoch, "switches": rows}
        self.assertIs(await lantern.project(bridge, guest, stale, epoch, stock_switches=current), guest)
        bridge.command.assert_not_awaited()

        previous = {**current, "epoch": "b" * 32}
        self.assertIs(await lantern.project(bridge, guest, stale, epoch, stock_switches=previous), after)
        bridge.command.assert_awaited_once_with(lantern.ACTION, lantern.OBSERVATORY, lantern.ACTOR, "0", timeout=3)

    async def test_extinguish_requires_current_guest_scope_and_verified_stock_change(self):
        epoch = "a" * 32
        pawn = "guest pawn"
        host = SimpleNamespace(zone_epoch=epoch, party={"players": {"guest": {"downed": False}}, "wipe": False},
            native_recipients=lambda: {"guest": {"connected": True, "pawn": pawn}},
            transitioning=False, stopping=False, last_game=observation(True))
        bridge = SimpleNamespace(command=AsyncMock(return_value=observation(False)))
        aim = "observatory-lantern:-10.00:180.00"
        with self.assertRaises(party.RuleError):
            await lantern.extinguish(host, bridge, "guest", f"{'b' * 32}@{aim}")
        self.assertIsNone(lantern.target_angles("observatory-lantern:0.00:181.00"))
        bridge.command.assert_not_awaited()
        result = await lantern.extinguish(host, bridge, "guest", f"{epoch}@{aim}")
        self.assertIs(result[lantern.KEY]["lit"], False)
        bridge.command.assert_awaited_once_with(lantern.EXTINGUISH, pawn, timeout=4)

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
