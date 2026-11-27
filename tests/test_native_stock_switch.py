from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from tools.silverfish import native_stock_door as doors, native_stock_switch as switches, runtime


class StockSwitchResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_latch_interaction_updates_the_exact_linked_door_before_the_census(self):
        area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        prefix = area + ".CrossRoads_P:PersistentLevel."
        latch = "DoorLatch_BP_C " + prefix + "DoorLatch_BP_C_2"
        door = "Door_Metal_03_BP_C " + prefix + "Door_Metal_03_Window_BP_C_16"
        other = door[:-2] + "17"
        ticket, epoch, local = "a" * 32, "b" * 32, "exact local pawn"
        shape = {"locked": True, "open": False, "yaw": 0}
        game = {"state": "hosting", "map": area, "sequence": 8, "pawns": {"local": {"name": local}},
                doors.KEY: {door: shape, other: shape}}
        host = SimpleNamespace(
            zone_epoch=epoch, last_game=game, native_recipients=lambda: {"guest": {"connected": True, "pawn": "bound guest"}},
            transitioning=False, stopping=False, native_checkpoint_unsafe=False,
            party={"players": {"guest": {"downed": False}}, "wipe": None, "pending_spawns": {}, "receipts": {}},
        )
        raw = (f"native-stock-interaction-reply\t1\t{ticket}\thosting\t{area}\t8\t{local}\taccepted\t"
               f"stock switch interaction dispatched\n{switches.TAG}\t{area}\t{latch}\t0\t90\t0\t0\t-\t-\tdoor={door}\n")
        captured = runtime.Bridge._local_stock_interaction_reply(raw, (ticket, switches.ACTION, latch, "bound guest"))
        bridge = SimpleNamespace(command=AsyncMock(return_value=captured))
        result = await switches.interact(host, bridge, "guest", f"{epoch}@{latch}", ticket)
        bridge.command.assert_awaited_once_with(switches.ACTION, latch, "bound guest", timeout=3)
        self.assertEqual(host.last_game[doors.KEY], {door: dict(shape, locked=False), other: shape})
        self.assertEqual(host.last_game[switches.KEY][latch]["door"], door)
        self.assertIs(game[doors.KEY][door], shape)

        connected = dict(game, state="connected")
        projected = dict(captured, state="connected")
        client = SimpleNamespace(last_game=connected, zone_epoch=epoch, gameplay={}, needs_travel=False,
                                 server_transitioning=False, cancel_gameplay_snapshot=AsyncMock(),
                                 bridge=SimpleNamespace(command=AsyncMock(return_value=projected)))
        await switches.apply_interaction(client, result[switches.KEY], latch)
        client.bridge.command.assert_awaited_once_with(switches.PROJECT, latch, area, "0", "90.0", "0.0", "0.0",
                                                      f"door={door}", timeout=2)
        self.assertEqual(client.last_game[doors.KEY], host.last_game[doors.KEY])
        with self.assertRaisesRegex(RuntimeError, "invalid state"):
            runtime.Bridge._local_stock_interaction_reply(raw.replace("door=" + door, "door=../wrong"),
                                                         (ticket, switches.ACTION, latch, "bound guest"))
        bridge.command.reset_mock()
        bridge.command.return_value = dict(captured, ack={"message": "stock latch interaction dispatched"})
        operation = "c" * 32
        pending = await switches.interact(host, bridge, "guest", f"{epoch}@{latch}", operation)
        self.assertEqual(pending, {"native_stock_switch_interaction": {"actor": latch, "powered": False, "pending": True}})
        self.assertEqual(await switches.interact(host, bridge, "guest", f"{epoch}@{latch}", operation), pending)
        bridge.command.assert_awaited_once_with(switches.ACTION, latch, "bound guest", timeout=3)

    async def test_guest_power_report_applies_absolute_result_once(self):
        area = "/CommunityMaps/Shelter"
        actor = f"Prop_Radio01_C {area}.Shelter:PersistentLevel.Radio_1"
        epoch, nonce = "e" * 32, "a" * 32
        off = {"powered": False, "angles": [0, 0, 0], "lights": {}}
        game = {"map": area, "state": "hosting", switches.KEY: {actor: off}}
        host = SimpleNamespace(zone_epoch=epoch, last_game=game, native_recipients=lambda: {"guest": {
            "connected": True, "pawn": "qualified guest"}}, transitioning=False, stopping=False,
            native_checkpoint_unsafe=False, party={"players": {"guest": {"downed": False}}, "wipe": None,
                                                   "pending_spawns": {}, "receipts": {}})
        reply = dict(game, ack={"message": "stock switch report applied"})
        bridge = SimpleNamespace(command=AsyncMock(return_value=reply))
        local = switches.local_outcome(f"stock-switch-state:12:0:{actor}")
        self.assertEqual(local, (actor, "0", "12"))
        for invalid in ("12:2", "0:0", "9007199254740992:0", "１２:0"):
            self.assertIsNone(switches.local_outcome(f"stock-switch-state:{invalid}:{actor}"))
        request = f"{epoch}@{actor}@0"
        result = await switches.interact(host, bridge, "guest", request, nonce, switches.REPORT)
        self.assertEqual(result["native_stock_switch_interaction"], {"actor": actor, "powered": False, "origin": "player"})
        self.assertEqual(await switches.interact(host, bridge, "guest", request, nonce, switches.REPORT), result)
        bridge.command.assert_awaited_once_with(switches.REPORT, actor, "qualified guest", "0", timeout=3)
        with self.assertRaisesRegex(switches.party.RuleError, "operation_id_conflict"):
            await switches.interact(host, bridge, "guest", f"{epoch}@{actor}@1", nonce, switches.REPORT)

        connected = dict(game, state="connected")
        bridge.command.reset_mock()
        bridge.command.return_value = connected
        frame = result[switches.KEY]
        self.assertIs(await switches.project(bridge, connected, frame, epoch), connected)
        bridge.command.assert_not_awaited()
        self.assertIs(await switches.project(bridge, connected, frame, epoch, sequence="12"), connected)
        bridge.command.assert_awaited_once_with(switches.PROJECT, actor, area, "0", "0", "0", "0", "ack=12", timeout=2)

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
        self.assertIsNone(switches.input_target(switches.IGNITION_TARGET + "../Prop_Candle_01_C_1"))

    async def test_late_join_projects_removed_derived_lamp_on_a_mounted_map(self):
        area = "/CommunityMaps/Shelter/Shelter"
        actor = f"Red-Lamp_C {area}.Shelter:PersistentLevel.Red-Lamp_C_1"
        fields = [switches.TAG, area, actor, "0", "0", "0", "0", "-", "-"]
        intact = switches.parse([*fields, "intact"], "connected", area)
        removed = switches.parse([*fields, "removed"], "hosting", area)
        epoch = "e" * 32
        game = {"map": area, "state": "connected", switches.KEY: {actor: intact}}
        updated = {**game, switches.KEY: {actor: removed}}
        bridge = SimpleNamespace(command=AsyncMock(return_value=updated))
        frame = {"map": area, "epoch": epoch, "switches": {actor: removed}}
        self.assertIs(await switches.project(bridge, game, frame, "f" * 32), game)
        bridge.command.assert_not_awaited()
        self.assertIs(await switches.project(bridge, game, frame, epoch), updated)
        bridge.command.assert_awaited_once_with(switches.PROJECT, actor, area, "0", "0.0", "0.0", "0.0", "removed", timeout=2)

    async def test_ignition_is_qualified_by_the_host_lamp_state(self):
        area = "/CommunityMaps/Shelter"
        actor = f"CustomFixture_C {area}.Shelter:PersistentLevel.CustomFixture_C_1"
        epoch, nonce = "e" * 32, "a" * 32
        game = {"map": area, "state": "hosting", switches.KEY: {actor: {"powered": False}}}
        host = SimpleNamespace(zone_epoch=epoch, last_game=game, native_recipients=lambda: {"guest": {
            "connected": True, "pawn": "qualified guest"}}, transitioning=False, stopping=False,
            native_checkpoint_unsafe=False, party={"players": {"guest": {"downed": False}}, "wipe": None,
                                                   "pending_spawns": {}, "receipts": {}})
        reply = dict(game, ack={"message": "stock switch interaction dispatched"},
                     **{switches.KEY: {actor: {"powered": True, "lamp": "intact"}}})
        bridge = SimpleNamespace(command=AsyncMock(return_value=reply))
        with self.assertRaisesRegex(switches.party.RuleError, "stock_switch_scope_unavailable"):
            await switches.interact(host, bridge, "guest", epoch + "@" + actor, nonce, switches.IGNITE)
        bridge.command.assert_not_awaited()
        game[switches.KEY][actor]["lamp"] = "intact"
        result = await switches.interact(host, bridge, "guest", epoch + "@" + actor, nonce, switches.IGNITE)
        bridge.command.assert_awaited_once_with(switches.IGNITE, actor, "qualified guest", timeout=3)
        self.assertTrue(result["native_stock_switch_interaction"]["powered"])

    async def test_projection_routes_live_streamed_actors_and_skips_a_hidden_level(self):
        area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
        epoch = "e" * 32
        prefix = "/CommunityMaps/LoadedHarbor.Harbor:PersistentLevel."
        actor = "LightSwitch_BP_C " + prefix + "LightSwitch_BP_C_1"
        hidden = actor[:-1] + "2"
        inactive = actor[:-1] + "3"
        bulb = "WallLamp_01_C " + prefix + "WallLamp_01_C_1"
        fields = [switches.TAG, area, actor, "1", "0", "0", "0", "-", bulb + "|1|1"]
        on = switches.parse(fields, "hosting", area)
        off = dict(on, powered=False, lights={bulb: [False, False]})
        game = {"state": "connected", "map": area, switches.KEY: {hidden: off, actor: off}}
        updated = {**game, switches.KEY: {hidden: off, actor: on}}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[RuntimeError("stock_switch_unavailable"), updated]))
        frame = {"map": area, "epoch": epoch, "switches": {hidden: on, inactive: on, actor: on}}
        self.assertIs(await switches.project(bridge, game, frame, "f" * 32), game)
        self.assertIs(await switches.project(bridge, game, frame, epoch), updated)
        self.assertEqual([call.args[1] for call in bridge.command.await_args_list], [hidden, actor])
        self.assertEqual(bridge.command.await_args.args[-1], bulb + "|1|1")

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

        client.last_game = initial
        newer = dict(updated, sequence=17)

        async def delayed_projection(*_args, **_kwargs):
            client.last_game = newer
            return updated

        client.bridge.command.side_effect = delayed_projection
        await switches.apply_interaction(client, response, radio)
        self.assertIs(client.last_game, newer)

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

    def test_compact_switch_reply_uses_visit_map_for_a_streamed_actor(self):
        area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
        sublevel = "/Game/Classes/Maps/Maps_Dungeons/CoopSwitchSublevel"
        switch = f"CircutBox_BP_C {sublevel}.Tut_Dock_P:PersistentLevel.CircutBox_BP_C_1"
        nonce = "a" * 32
        raw = (
            f"native-stock-interaction-reply\t1\t{nonce}\thosting\t{area}\t12\thost pawn\taccepted\t"
            f"stock switch interaction dispatched\n{switches.TAG}\t{area}\t{switch}\t1\t0\t0\t0\t-\t-\n"
        )
        fields = (nonce, switches.ACTION, switch, "guest pawn")
        reply = runtime.Bridge._local_stock_interaction_reply(raw, fields)
        self.assertEqual(reply["map"], area)
        self.assertTrue(reply[switches.KEY][switch]["powered"])

        projected = raw.replace("hosting", "connected").replace("interaction dispatched", "state projected")
        projection = (nonce, switches.PROJECT, switch, area, "1", "0", "0", "0")
        reply = runtime.Bridge._local_stock_interaction_reply(projected, projection)
        self.assertTrue(reply[switches.KEY][switch]["powered"])
        with self.assertRaises(RuntimeError):
            runtime.Bridge._local_stock_interaction_reply(projected, (*projection[:3], sublevel, *projection[4:]))
