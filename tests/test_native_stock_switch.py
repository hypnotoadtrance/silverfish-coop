from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from tools.silverfish import native_stock_switch as switches


class StockSwitchResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_applies_exact_switch_before_another_world_snapshot(self):
        area = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        epoch = "e" * 32
        prefix = area + ".Tut_Dock_P:PersistentLevel."
        radio = "Prop_Radio01_C " + prefix + "Prop_Radio01_C_3"
        wall = "LightSwitch_02_BP_C " + prefix + "LightSwitch_02_BP_C_1"
        off = {"powered": False, "angles": [0, 0, 0], "lights": {}}
        on = dict(off, powered=True)
        cached = {"map": area, "epoch": epoch, "switches": {radio: off, wall: off}}
        response = dict(cached, switches={radio: on})
        initial = {"map": area, "state": "connected", switches.KEY: cached["switches"]}
        updated = dict(initial, **{switches.KEY: {radio: on, wall: off}})
        client = SimpleNamespace(
            zone_epoch=epoch, last_game=initial, gameplay={switches.KEY: cached},
            needs_travel=False, server_transitioning=False, cancel_gameplay_snapshot=AsyncMock(),
            bridge=SimpleNamespace(command=AsyncMock(return_value=updated)),
        )

        await switches.apply_interaction(client, dict(response, epoch="f" * 32), radio)
        await switches.apply_interaction(client, response, wall)
        client.cancel_gameplay_snapshot.assert_not_awaited()
        client.bridge.command.assert_not_awaited()

        await switches.apply_interaction(client, response, radio)
        client.cancel_gameplay_snapshot.assert_awaited_once()
        client.bridge.command.assert_awaited_once_with(
            switches.TAG + "-project", radio, area, "1", "0", "0", "0", timeout=2,
        )
        self.assertEqual(client.gameplay[switches.KEY]["switches"], {radio: on, wall: off})
        self.assertIs(client.last_game, updated)
