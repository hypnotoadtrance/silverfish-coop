from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from tools.silverfish import native_stock_switch as switches


class StockSwitchResponseTests(unittest.IsolatedAsyncioTestCase):
    def test_candle_variants_share_the_stock_switch_protocol(self):
        area = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        for suffix in ("01", "02", "03"):
            kind = f"Prop_Candle_{suffix}_C"
            actor = f"{kind} {area}.Tut_Dock_P:PersistentLevel.{kind}_7"
            self.assertEqual(switches.target_actor(switches.TARGET + actor), actor)
            self.assertEqual(switches.parse([switches.TAG, area, actor, "0", "0", "0", "0", "-", "-"],
                                           "hosting", area)["powered"], False)
            with self.assertRaises(ValueError):
                switches.parse([switches.TAG, area, actor, "1", "0", "0", "0", "-", "-"], "hosting", "/wrong-map")

    def test_oil_lamps_use_placed_switch_state_and_accept_guest_ignition(self):
        area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        prefix = area + ".CrossRoads_P:PersistentLevel."
        actor = "Prop_Lantern_01_C " + prefix + "Prop_Lantern_01_C_3"
        self.assertEqual(switches.input_target(switches.IGNITION_TARGET + actor), (switches.IGNITE, actor))
        self.assertEqual(switches.input_target(switches.TARGET + actor), (switches.ACTION, actor))
        self.assertTrue(switches.parse([switches.TAG, area, actor, "1", "0", "0", "0", "-", "-"],
                                       "connected", area)["powered"])
        candle = "Prop_Candle_01_C " + prefix + "Prop_Candle_01_C_1"
        self.assertIsNone(switches.input_target(switches.IGNITION_TARGET + candle))

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
