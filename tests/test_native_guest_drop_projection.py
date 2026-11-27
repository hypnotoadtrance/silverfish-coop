import json
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, patch

from tools.silverfish import native_guest_drop as drop, native_guest_drop_record as record, native_world_loot, party
from tools.silverfish import native_guest_drop_projection as projection
from tools.silverfish import native_inventory, native_loose_pickup, native_stock_enemy_drops


MAP = record.OBSERVATORY
WORLD = f"{MAP}.Observitory_P:PersistentLevel."
PAWN = f"Player_FP_C {WORLD}Player_FP_C_404"
ACTOR = f"Ammo_Pistol_BP_C {WORLD}Ammo_Pistol_BP_C_8"
ID, OPERATION = "a" * 32, "b" * 32


class DropProjectionTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        game = {"state": "connected", "map": MAP, "pawns": {"local": {"name": PAWN, "location": [0., 0., 40.]}}}
        row = record.visible(dict(owner="owner", map=MAP, class_path=record.ITEM, guid="D" * 32, quantity=9,
                                  location=[10., 20., 40.]), ID)
        client = SimpleNamespace(last_game=game.copy(), gameplay={record.STATE: {ID: row}},
                                 invite={"character_id": "owner"}, expected_zone=MAP, stopping=False,
                                 suspended=False, needs_travel=False, server_transitioning=False,
                                 transport=SimpleNamespace(game_access=True), host_session="verified-host",
                                 zone_epoch="1" * 32, gameplay_seen=time.monotonic(),
                                 gameplay_action=AsyncMock(return_value={drop.RESULT: {**row, "state": "reclaimed"}}))

        async def bridge(command, *args, timeout):
            if command == projection.CLEAR:
                return game.copy()
            area, epoch, drop_id, *coords = args
            self.assertEqual(area, MAP)
            self.assertEqual((command, drop_id, coords), (projection.SYNC, ID, ["10.0", "20.0", "40.0"]))
            row = [projection.ROW, MAP, epoch, drop_id, PAWN, ACTOR, "0"]
            return {**game, projection.KEY: projection.parse(row, "connected", MAP)}

        client.bridge = SimpleNamespace(command=AsyncMock(side_effect=bridge))
        return client

    def test_exact_actor_status_and_local_intent_validation(self):
        epoch = "c" * 32
        row = [projection.ROW, MAP, epoch, ID, PAWN, ACTOR, "124"]
        self.assertEqual(projection.parse(row, "connected", MAP)["sequence"], 124)
        self.assertEqual(projection.parse(row, "hosting", MAP)["sequence"], 124)
        for state, changed in (("opening", row), ("connected", [*row[:5], "Item_9mm_C Fake", "124"]),
                               ("connected", [*row[:-1], str(2**53)]),
                               ("connected", [*row[:4], PAWN.replace("Observitory", "CrossRoads"), *row[5:]])):
            with self.assertRaises(ValueError):
                projection.parse(changed, state, MAP)
        self.assertEqual(projection.target_parts(f"stack-drop:{epoch}:{ID}"), (epoch, ID))
        self.assertIsNone(projection.target_parts(f"stack-drop:{epoch}:{ID.upper()}"))

    def test_unregistered_item_keeps_its_exact_class_across_shared_loot(self):
        item = "/Game/Mods/Example/Packed-Medkit.Packed-Medkit_C"
        spawn = f"Packed-Medkit_C {WORLD}Packed-Medkit_C_12"
        self.assertTrue(record.valid_quantity(item, 3))
        self.assertTrue(record.actor_matches(MAP, spawn, item))
        self.assertTrue(native_inventory.split_class_path(item))
        row = [native_world_loot.ROW, MAP, ID, OPERATION, PAWN, spawn, "4", item]
        self.assertEqual(native_world_loot.parse(row, "connected", MAP)["class_path"], item)
        fields = [native_stock_enemy_drops.TAG, MAP, "3", "1", f"Robot_C {WORLD}Robot_C_1", PAWN, spawn, item, "1"]
        self.assertEqual(native_stock_enemy_drops.parse(fields, "hosting", MAP)["class_path"], item)
        loose = f"{WORLD}PlacedMedkit_1"
        self.assertTrue(native_loose_pickup._stock_source(MAP, loose, item, 0))
        for bad_item in ("/Game/Mods//Medkit.Medkit_C", "/Game/Mods/Medkit.Other_C",
                         "/Game/Mods/../Medkit.Medkit_C", "/Script/Engine.Actor"):
            with self.subTest(path=bad_item):
                self.assertFalse(record.valid_class(bad_item))
                self.assertFalse(native_inventory.split_class_path(bad_item))
                with self.assertRaises(ValueError):
                    native_world_loot.parse([*row[:-1], bad_item], "connected", MAP)

    def test_streamed_stock_drop_keeps_the_mounted_campaign_scope_through_checkpoint(self):
        drops = native_stock_enemy_drops
        area = "/ExampleContent/Maps/SunkenCamp"
        level = "/ExampleContent/Maps/Basement_1.Basement:PersistentLevel."
        item = "/ExampleContent/Loot/SalvageKit.SalvageKit_C"
        source = f"BurriedItem_BP_C {level}BurriedItem_BP_1"
        actor = f"SalvageKit_C {level}SalvageKit_C_793"
        killer = f"Player_FP_C {area}.SunkenCamp:PersistentLevel.Player_FP_C_42"
        fields = [drops.TAG, area, "3", "1", source, killer, actor, item, "1"]
        observed = drops.parse(fields, "hosting", area)
        self.assertEqual((observed["map"], observed["source"], observed["actor"]), (area, source, actor))

        guid = "B" * 32
        frame = {"map": area, "slot": "SFCoop_" + "c" * 32}
        row = dict(map=area, source=source.split(" ", 1)[1], actor=actor, class_path=item, guid=guid,
                   ammo=0, transform={"position": [0., 0., 10.], "rotation": [0., 0., 0.], "scale": [1., 1., 1.]},
                   killer="scout", claimed_by=None, escrow=frame)
        self.assertEqual(drops.active_frames({drops.STATE: {guid: row}}, {"scout"}), [frame])

        for index, changed in (
            (4, source.replace("/Maps/", "/../")),
            (5, killer.replace(f"{area}.SunkenCamp:PersistentLevel.", level)),
            (6, actor.replace("SalvageKit_C ", "WrongLoot_C ")),
        ):
            with self.subTest(index=index, path=changed), self.assertRaises(ValueError):
                invalid = list(fields)
                invalid[index] = changed
                drops.parse(invalid, "hosting", area)
        with self.assertRaises(ValueError):
            drops.active_frames({drops.STATE: {guid: dict(row, escrow=dict(frame, map=MAP))}}, {"scout"})

    async def test_committed_world_pickup_is_not_recreated_by_an_older_snapshot(self):
        client, view = self.client(), native_world_loot.Projection()
        game, rows = client.last_game, client.gameplay[record.STATE]
        identity = native_world_loot.player_drop_identity(ID)
        bridge = SimpleNamespace(command=AsyncMock(return_value=game))
        await view.sync(bridge, game, {}, "first", client.zone_epoch, active=True, player_drops=rows)
        epoch = view.epoch
        self.assertIn(identity, view.items)
        self.assertEqual(bridge.command.await_args.args[3].rsplit("|", 1)[1], "9")
        await view.retire_claim(bridge, game, (epoch, identity))
        bridge.command.assert_awaited_with(native_world_loot.RETIRE, epoch, identity, timeout=3)
        await view.sync(bridge, game, {}, "first", client.zone_epoch, active=True, player_drops=rows)
        self.assertNotIn(identity, view.items)
        bridge.command.assert_awaited_with(native_world_loot.FRAME, MAP, epoch, timeout=3)
        await view.sync(bridge, game, {}, "second", client.zone_epoch, active=True, player_drops=rows)
        self.assertIn(identity, view.items)
        self.assertNotEqual(view.epoch, epoch)

    async def test_exact_world_pickup_receipt_does_not_wait_for_the_status_file(self):
        client = self.client()
        rows = client.gameplay[record.STATE]
        identity = native_world_loot.player_drop_identity(ID)
        for phase in ("hosting", "connected"):
            with self.subTest(phase=phase):
                view = native_world_loot.Projection()
                game = dict(client.last_game, state=phase)
                bridge = SimpleNamespace(command=AsyncMock(return_value=game))
                await view.sync(bridge, game, {}, "first", client.zone_epoch, active=True, player_drops=rows)
                epoch = view.epoch
                target = native_world_loot.target_parts(f"world-loot:{epoch}:{identity}:7")
                self.assertEqual(target, (epoch, identity, 7))
                self.assertEqual(view.claim_target(game, target), (drop.RECLAIM, ID))
                with self.assertRaisesRegex(party.RuleError, "native_world_loot_intent_stale"):
                    view.claim_target(game, target)
                for current, intent in (
                    (dict(game, pawns={"local": {"name": "next pawn"}}), (epoch, identity, 8)),
                    (dict(game, map="next map"), (epoch, identity, 8)),
                    (game, ("0" * 32, identity, 8)),
                    (game, (epoch, identity)),
                ):
                    with self.assertRaisesRegex(party.RuleError, "native_world_loot_intent_stale"):
                        view.claim_target(current, intent)

    async def test_immediate_world_loot_receipt_replaces_only_the_scoped_loot_records(self):
        client, view = self.client(), native_world_loot.Projection()
        loot = native_world_loot
        game, rows = client.last_game, client.gameplay[record.STATE]
        old_id = "f" * 32
        game.update(sequence=42, native_world_view={"rain_intensity": .5},
                    **{loot.KEY: {old_id: {}}, loot.ERROR_KEY: {old_id: {}}, loot.PHYSICS: {old_id: {}}})
        identity = loot.player_drop_identity(ID)
        reply_pawn = PAWN

        async def command(action, area, epoch, *args, timeout):
            header = (loot.FRAME_REPLY, "1", OPERATION, "connected", area, reply_pawn, epoch, "accepted", "ready")
            readback = (loot.ROW, area, epoch, identity, reply_pawn, ACTOR, "0", record.ITEM)
            reply = "\t".join(header) + "\n" + ("\t".join(readback) + "\n" if args else "")
            return loot.parse_frame_reply(reply, (OPERATION, action, area, epoch, *args))

        bridge = SimpleNamespace(command=AsyncMock(side_effect=command))
        snapshot = await view.sync(bridge, game, {}, "first", client.zone_epoch, active=True, player_drops=rows)
        self.assertEqual(snapshot[loot.KEY][identity]["actor"], ACTOR)
        self.assertEqual(snapshot["sequence"], game["sequence"])
        self.assertIs(snapshot["native_world_view"], game["native_world_view"])
        self.assertNotIn(loot.ERROR_KEY, snapshot)
        self.assertNotIn(loot.PHYSICS, snapshot)
        self.assertEqual(list(game[loot.KEY]), [old_id])

        reply_pawn = PAWN.replace("404", "405")
        with self.assertRaisesRegex(RuntimeError, "different game seat"):
            await view.sync(bridge, snapshot, {}, "first", client.zone_epoch, active=False)

    def test_resident_drop_motion_uses_the_current_host_and_overrides_older_status(self):
        drops = native_stock_enemy_drops
        game = dict(self.client().last_game, state="hosting")
        npc = f"NPC_Survivor_Worker_Light_C {WORLD}NPC_Survivor_Worker_C_2"
        fields = (drops.TAG, MAP, "1", "1", npc, "-", ACTOR, record.ITEM, "1", "moving", "200",
                  "10", "20", "130", "0", "0", "0", "1", "1", "1")
        raw = (f"stock-loot-motion-v1\t{MAP}\t{PAWN}\n" + "\t".join(fields)).encode()
        observed = drops.read_resident_motion(raw, game)
        stale_status = drops.parse([*fields[:13], "185", *fields[14:]], "hosting", MAP)
        game[drops.KEY] = {ACTOR: stale_status}
        zone = "1" * 32
        identity = drops.motion_identity(zone, ACTOR)
        motion = drops.moving_frame(game, zone, set(), observed=observed)
        self.assertEqual(motion["items"][identity]["transform"]["position"], [10, 20, 130])
        self.assertIsNone(drops.moving_frame(game, zone, {1}, observed=observed))
        self.assertIsNone(drops.read_resident_motion(raw.replace(PAWN.encode(), PAWN.replace("404", "405").encode()), game))

    async def test_enemy_drop_keeps_its_identity_when_it_lands_and_becomes_claimable(self):
        client, view = self.client(), native_world_loot.Projection()
        game, zone = client.last_game, client.zone_epoch
        source = f"Ammo_Pistol_BP_C {WORLD}Ammo_Pistol_BP_C_8"
        identity = native_stock_enemy_drops.motion_identity(zone, source)
        pose = {"position": [10, 20, 140], "rotation": [10, 20, 30], "scale": [1, 1, 1]}
        packet = [MAP, zone, 3, identity, record.ITEM, [10, 20, 140, 10, 20, 30, 1, 1, 1]]
        earlier = [*packet]
        earlier[2], earlier[5] = 2, [10, 20, 170, 10, 20, 30, 1, 1, 1]
        playback = native_stock_enemy_drops.MotionPlayback()
        moving = playback.frame([(10, json.dumps(row)) for row in (packet, earlier)], MAP, zone, active=True, now=10.1)
        self.assertEqual(moving["items"][identity]["transform"], pose)
        bridge = SimpleNamespace(command=AsyncMock(return_value=game))
        await view.sync(bridge, game, {}, "first", zone, active=True, enemy_motion=moving)
        target = view.epoch, identity, 2
        self.assertEqual(bridge.command.await_args.args[3].split("|")[:4], [identity, record.ITEM, "moving", "10"])
        with self.assertRaisesRegex(party.RuleError, "native_world_loot_intent_stale"):
            view.claim_target(game, target)

        guid = "D" * 32
        landed = dict(pose, position=[10, 20, 40])
        drops = {guid: {"map": MAP, "class_path": record.ITEM, "transform": landed, "ammo": 2,
                        "settled": True, "visual_id": identity}}
        await view.sync(bridge, game, {}, "first", zone, active=True, stock_enemy_drops=drops, enemy_motion=moving)
        self.assertEqual(list(view.items), [identity])
        self.assertEqual(bridge.command.await_args.args[3].split("|")[:6], [identity, record.ITEM, "carry", "10", "20", "40"])
        self.assertEqual(view.claim_target(game, target), (native_stock_enemy_drops.CLAIM, guid))
        self.assertEqual(playback.frame([], MAP, zone, active=True, now=11)["items"], {})

    def test_carried_motion_cannot_recreate_loot_after_its_claim(self):
        client = self.client()
        identity = native_world_loot.player_drop_identity(ID)
        rows = client.gameplay[record.STATE]
        position = [12, 20, 90]
        zone = client.zone_epoch
        packet = [MAP, zone, 1, identity, record.ITEM, [*position, 0, 10, 0, 1, 1, 1], "carry"]
        motion = native_stock_enemy_drops.MotionPlayback().frame(
            [(10, json.dumps(packet))], MAP, zone, active=True, now=10.1)
        moving, = native_world_loot.nearby(client.last_game, {}, zone, player_drops=rows,
                                          enemy_motion=motion, carried=[identity]).values()
        self.assertEqual(moving.transform["position"], position)
        self.assertIsNone(moving.claim_action)
        landed, = native_world_loot.nearby(client.last_game, {}, zone, player_drops=rows, enemy_motion=motion).values()
        self.assertEqual(landed.transform["position"], rows[ID]["location"])
        self.assertEqual(landed.claim_action, drop.RECLAIM)
        self.assertEqual(native_world_loot.nearby(client.last_game, {}, zone, enemy_motion=motion,
                                                carried=[identity]), {})

    async def test_guest_can_collect_nearest_stock_drop_from_another_guest(self):
        client, view = self.client(), projection.Projection()
        row = client.gameplay[record.STATE][ID]
        row["owner"] = "other-guest"
        client.gameplay[record.STATE][OPERATION] = dict(row, owner="owner", drop_id=OPERATION, location=[40., 40., 40.])
        client.gameplay_action.return_value = {drop.RESULT: dict(row, state="reclaimed", claimed_by="owner")}
        await view.sync(client, True)
        self.assertEqual(client.last_game[projection.KEY]["actor"], ACTOR)
        self.assertEqual(client.bridge.command.await_args.args[3:], (ID, "10.0", "20.0", "40.0"))
        target = client.last_game[projection.KEY]["epoch"], ID
        receipt = (await view.reclaim(client, OPERATION, target))[drop.RESULT]
        self.assertEqual((receipt["owner"], receipt["claimed_by"]), ("other-guest", "owner"))
        client.gameplay_action.assert_awaited_once_with(drop.RECLAIM, OPERATION, ID)
        client.suspended = True
        await view.sync(client, False)
        client.bridge.command.assert_awaited_with(projection.CLEAR, timeout=2)
        self.assertNotIn(projection.KEY, client.last_game)

    async def test_host_uses_exact_local_projection_and_claims_through_the_ledger(self):
        view = projection.Projection()
        row = dict(owner="guest", map=MAP, class_path=record.ITEM, guid="D" * 32, quantity=9,
                   location=[10., 20., 40.], claim_id=None)
        game = {"state": "hosting", "map": MAP, record.CAPABILITY: 1,
                "pawns": {"local": {"name": PAWN, "location": [0., 0., 40.]}}}
        host = SimpleNamespace(last_game=game, last_seen=time.monotonic(), native_guest=True, native_zones=True,
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                               party={record.STATE: {ID: row}}, zone_epoch="1" * 32,
                               control=SimpleNamespace(generation="host-generation"))

        async def sync(command, area, epoch, drop_id, *coords, timeout):
            self.assertEqual((command, area, drop_id, coords), (projection.SYNC, MAP, ID, ("10.0", "20.0", "40.0")))
            return dict(game, **{projection.KEY: projection.parse([projection.ROW, MAP, epoch, ID, PAWN, ACTOR, "0"],
                                                                 "hosting", MAP)})

        bridge = SimpleNamespace(command=AsyncMock(side_effect=sync))
        await view.sync_host(host, bridge)
        target = host.last_game[projection.KEY]["epoch"], ID
        result = {drop.RESULT: dict(record.visible(row, ID), state="reclaimed", claimed_by="host")}
        with patch.object(drop, "perform", AsyncMock(return_value=result)) as perform:
            self.assertEqual(await view.reclaim_host(host, bridge, OPERATION, target), result)
            perform.assert_awaited_once_with(host, bridge, "host", drop.RECLAIM, ID, OPERATION)

    async def test_stale_authenticated_snapshot_pauses_projection_without_retiring_its_identity(self):
        client, view = self.client(), projection.Projection()
        await view.sync(client, True)
        proof = client.last_game.pop(projection.KEY)
        client.gameplay_seen = time.monotonic() - 2.5
        view.next_sync = 0.
        await view.sync(client, True)
        client.bridge.command.assert_awaited_once()
        self.assertEqual(view.current, (proof["epoch"], ID, PAWN, MAP, record.ITEM))
        with self.assertRaisesRegex(party.RuleError, "intent_stale"):
            await view.reclaim(client, OPERATION, (proof["epoch"], ID))
        with self.assertRaisesRegex(party.RuleError, "fixture_scope_unavailable"):
            await view.fixture(client, ID)
        client.gameplay_action.assert_not_awaited()

        client.gameplay_seen = time.monotonic()
        await view.sync(client, True)
        self.assertEqual(client.last_game[projection.KEY], proof)
        self.assertEqual([call.args[0] for call in client.bridge.command.await_args_list],
                         [projection.SYNC, projection.SYNC])

    async def test_pistol_ledger_requires_the_exact_pistol_projection(self):
        client, view = self.client(), projection.Projection()
        client.gameplay[record.STATE][ID].update(class_path=record.LIGHT_PISTOL, quantity=1)
        pistol = f"Wep_LightPistol_C {WORLD}Wep_LightPistol_C_8"

        async def sync(command, area, epoch, drop_id, *args, timeout):
            self.assertEqual(command, projection.SYNC)
            self.assertEqual(args, ("10.0", "20.0", "40.0", record.LIGHT_PISTOL))
            row = [projection.ROW, area, epoch, drop_id, PAWN, pistol, "0"]
            return {**client.last_game, projection.KEY: projection.parse(row, "connected", area)}

        client.bridge.command.side_effect = sync
        await view.sync(client, True)
        proof = client.last_game[projection.KEY]
        self.assertEqual(proof["actor"], pistol)
        proof["actor"] = ACTOR
        with self.assertRaisesRegex(party.RuleError, "intent_stale"):
            await view.reclaim(client, OPERATION, (proof["epoch"], ID))
        client.gameplay_action.assert_not_awaited()

    async def test_old_epoch_cannot_reclaim_but_same_durable_drop_gets_new_projection(self):
        client, view = self.client(), projection.Projection()
        await view.sync(client, True)
        prior = (client.last_game[projection.KEY]["epoch"], ID)
        view.rebind()  # The same host and zone can admit a fresh transport.
        with self.assertRaisesRegex(party.RuleError, "intent_stale"):
            await view.reclaim(client, OPERATION, prior)
        client.gameplay_action.assert_not_awaited()
        await view.sync(client, True)
        fresh = (client.last_game[projection.KEY]["epoch"], ID)
        self.assertNotEqual(prior, fresh)
        result = await view.reclaim(client, OPERATION, fresh)
        self.assertEqual(result[drop.RESULT]["state"], "reclaimed")
        client.gameplay_action.assert_awaited_once_with(drop.RECLAIM, OPERATION, ID)
        await view.sync(client, True)  # An old authenticated snapshot cannot redraw the claimed projection.
        self.assertNotIn(projection.KEY, client.last_game)
        with self.assertRaisesRegex(party.RuleError, "intent_stale"):
            await view.reclaim(client, OPERATION, fresh)


    async def test_foreign_map_ledger_snapshot_cannot_rematerialize_the_old_drop(self):
        client, view = self.client(), projection.Projection()
        await view.sync(client, True)
        previous = client.last_game[projection.KEY]
        client.expected_zone = client.last_game["map"] = record.CROSSROADS
        client.last_game["pawns"] = {"local": {"name":
            f"Player_FP_C {record.CROSSROADS}.CrossRoads_P:PersistentLevel.Player_FP_C_404"}}
        with self.assertRaisesRegex(party.RuleError, "intent_stale"):
            await view.reclaim(client, OPERATION, (previous["epoch"], ID))
        await view.sync(client, True)
        client.bridge.command.assert_awaited_with(projection.CLEAR, timeout=2)
        client.gameplay_action.assert_not_awaited()
