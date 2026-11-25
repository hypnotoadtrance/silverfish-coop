from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from tools.silverfish import native_stock_switch as switches, runtime


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

    async def test_compact_switch_reply_merges_only_the_verified_actor(self):
        area = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        radio = f"Prop_Radio01_C {area}.Tut_Dock_P:PersistentLevel.Prop_Radio01_C_3"
        other = radio[:-1] + "4"
        nonce, epoch, pawn = "a" * 32, "e" * 32, "guest pawn"
        fields = (nonce, switches.PROJECT, radio, area, "1", "0", "0", "0")
        raw = (
            f"native-stock-interaction-reply\t1\t{nonce}\tconnected\t{area}\t12\t{pawn}\taccepted\t"
            f"stock switch state projected\n{switches.TAG}\t{area}\t{radio}\t1\t0\t0\t0\t-\t-\n"
        )
        reply = runtime.Bridge._local_stock_interaction_reply(raw, fields)
        on = reply[switches.KEY][radio]
        off = dict(on, powered=False)
        game = {"state": "connected", "map": area, "sequence": 12, "pawns": {"local": {"name": pawn}},
                switches.KEY: {radio: off, other: off}, "native_loot_crates": {"crate": {}}}
        frame = {"map": area, "epoch": epoch, "switches": {radio: on}}
        bridge = SimpleNamespace(command=AsyncMock(return_value=reply))
        observed = await switches.project(bridge, game, frame, epoch)
        self.assertEqual(observed[switches.KEY], {radio: on, other: off})
        self.assertEqual(observed["native_loot_crates"], game["native_loot_crates"])
        stale = {**game, "sequence": 13}
        self.assertIs(await switches.project(bridge, stale, frame, epoch), stale)
        with self.assertRaises(RuntimeError):
            runtime.Bridge._local_stock_interaction_reply(raw.replace(f"{area}\t{radio}\t1", f"{area}\t{other}\t1"), fields)
