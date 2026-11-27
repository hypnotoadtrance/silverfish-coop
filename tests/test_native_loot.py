import asyncio
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from tools.silverfish import (
    link, native_host_save, native_loose_pickup, native_loot, native_stock_enemy_drops, native_world_loot, party, runtime,
)
from tools.silverfish.authored_loot import (CRATES, CRATE_NAMES_BY_INDEX, LEGACY_CRATE, OFFICE_DESK,
                                           PLACEMENTS, OVERRIDDEN_PLACEMENTS, SMALL_CABINET, full_name,
                                           lockable_container, loot_capacity, random_lid)
from tools.silverfish.campaign import CampaignFormatError
from tools.silverfish.local_bridge import LocalCommandNotSubmitted
from tools.silverfish.service import Host
from tools.silverfish.generate_authored_loot_registry import outputs, survey
from tools.silverfish.travel import CISTERN, CITY, CROSSROADS, DEAD_BRIDGE, LAB_E206, LAKE, OBSERVATORY, RAIL_YARD


OTHER_CRATE = full_name(CROSSROADS, "LootCrate_Small_BP_C_9")
BRIDGE_CRATE = full_name(DEAD_BRIDGE, "LootCrate_Small_BP_C_1")
PAWN = "exact local pawn"
ITEM = {"actor": "stock Bandage", "class_path": "/Game/Classes/Items/Item_Bandage.Item_Bandage_C", "guid": "A" * 32}
OPERATION = "f" * 32


class GuestLockpickTests(unittest.IsolatedAsyncioTestCase):
    async def test_reported_unlock_can_settle_once_without_discarding_stale_stack(self):
        await self.check_reported_unlock("unlocked:pick-stack-stale",
                                         {"remaining": 0, "inventory_reconciled": False},
                                         "native_guest_lockpick_stack_stale")

    async def test_reported_unlock_retains_the_newer_count_of_the_exact_pick_stack(self):
        await self.check_reported_unlock("unlocked:1", {"remaining": 1, "reported_remaining": 0},
                                         "native_guest_lockpick_stack_reconciled", reported_remaining=0, remaining=1)

    async def check_reported_unlock(self, ack, outcome, event, **details):
        crate = next(name for name in CRATES if name.startswith("LootLocker_Small_BP_C ")
                     and CRATES[name] == OBSERVATORY)
        character, pawn, guid, epoch = "guest", "Player_FP_C exact guest", "A" * 32, "e" * 32
        inventory = {guid: {"quantity": 1}}
        host = SimpleNamespace(
            native_loot_input=True, zone_epoch=epoch, stopping=False, transitioning=False,
            native_checkpoint_unsafe=False, native_pickup=Mock(), control=Mock(),
            last_game={"state": "hosting", "map": OBSERVATORY},
            native_recipients=lambda: {character: {"connected": True, "pawn": pawn}},
            party={"players": {character: {"downed": False, "native_inventory": inventory}},
                   "pending_spawns": {}, "receipts": {}},
        )
        frame = {"state": "hosting", "map": OBSERVATORY, "ack": {"message": ack}}
        bridge = SimpleNamespace(command=AsyncMock(return_value=frame))
        target = "@".join((epoch, crate, guid, "1", "1", "1"))
        with patch("tools.silverfish.native_inventory.retain_guest_save", AsyncMock(return_value=True)):
            first = await native_loot.settle_lockpick(host, bridge, character, target, OPERATION)
            repeated = await native_loot.settle_lockpick(host, bridge, character, target, OPERATION)
        self.assertIs(repeated, first)
        self.assertEqual(first[native_loot.LOCKPICK_RESULT], {
            "map": OBSERVATORY, "crate": crate, "guid": guid, "locked": False, **outcome,
        })
        bridge.command.assert_awaited_once_with(native_loot.LOCKPICK, crate, pawn, guid, "1", "1", "1", timeout=3)
        self.assertIn(guid, inventory)
        host.native_pickup.forget_committed_row.assert_not_called()
        host.control.event.assert_called_once_with(event, actor=crate, character=character,
                                                   operation_id=OPERATION, **details)


class AuthoredCrateRegistryTests(unittest.TestCase):
    def test_compact_status_indices_match_every_generated_lua_profile(self):
        registry = outputs()["mod/SilverfishCoop/Scripts/authored_loot_registry.lua"]
        recorded = re.findall(r'index = (\d+), map = "([^"]+)",\s+name = "([^"]+)"', registry)
        expected = [(str(index), CRATES[crate], crate) for index, crate in enumerate(CRATE_NAMES_BY_INDEX[1:], 1)]
        self.assertEqual(recorded, expected)

    def test_stock_lockpick_result_can_target_a_secure_locker_with_a_key(self):
        crate = next(name for name in CRATES if name.startswith("LootLocker_Secure_BP_C "))
        area = CRATES[crate]
        self.assertFalse(native_loot.lockpick_door(crate, area))
        fields = ["loot-lockpick", area, crate, "Player_FP_C exact guest", "1", "A" * 32, "1", "1", "1"]
        self.assertEqual(native_loot.parse_lockpick(fields)["crate"], crate)
        fields[1] = OBSERVATORY
        with self.assertRaises(ValueError):
            native_loot.parse_lockpick(fields)

    def test_stock_lockpick_result_accepts_exact_hinged_actor_shape_in_current_map(self):
        door = full_name(CROSSROADS, "Door_Wood_04_BP_C_1719")
        fields = ["loot-lockpick", CROSSROADS, door, "Player_FP_C exact guest", "1", "A" * 32, "1", "1", "1"]
        self.assertEqual(native_loot.parse_lockpick(fields)["crate"], door)
        for invalid in (door.replace("PersistentLevel", "OtherLevel"), door.replace(CROSSROADS, OBSERVATORY)):
            fields[2] = invalid
            with self.assertRaises(ValueError):
                native_loot.parse_lockpick(fields)

    def test_first_three_profiles_and_rollout_counts_match_generated_files(self):
        self.assertEqual(tuple(CRATES)[:3], (LEGACY_CRATE, OTHER_CRATE, BRIDGE_CRATE))
        self.assertEqual(len(PLACEMENTS), 287)
        self.assertEqual(len(OVERRIDDEN_PLACEMENTS), 55)
        self.assertEqual({area: sum(row[0] == area for row in PLACEMENTS) for area in (CITY, RAIL_YARD, CISTERN)},
                         {CITY: 7, RAIL_YARD: 7, CISTERN: 5})
        repository = Path(__file__).resolve().parents[1]
        for relative, content in outputs().items():
            self.assertEqual((repository / relative).read_text(encoding="utf-8"), content)

    def test_survey_qualifies_known_row_only_overrides_and_exact_maps(self):
        report = [r"PACKAGE Maps_OverWorld\City_01_P.umap"]
        common = "Loot_Spawner1=Loot_Spawner1; Loot_Spawner=Loot_Spawner; SpawnSalt=-17; RootComponent=DefaultSceneRoot"
        excluded = ("Loot_Override=Row", "Replenish_Mode=1", "GuaranteeSpawn=False", "IgnoreKarma=False",
                    "Ping_IfOpened=False", "Loot Table_Items=Stock", "SpawnerExpectedValue=1")
        for index, override in enumerate(("", *excluded)):
            report.append(f"LootCrate_Small_BP_C LootCrate_Small_BP_C_{index} {common}" + (f"; {override}" if override else ""))
        report.extend((r"PACKAGE Maps_OverWorld\Unknown_P.umap", f"LootCrate_Small_BP_C LootCrate_Small_BP_C_0 {common}"))
        report.insert(1, f"LootCrate_Small_BP_C LootCrate_Small_BP_C_20 {common}; "
                         "Loot_Override=DataTableRowHandle(Industrial)")
        maps, placed, stock = survey("\n".join(report))
        self.assertEqual(len(maps), 2)
        self.assertEqual(len(placed), len(excluded) + 3)
        self.assertEqual(stock, {(CITY, "LootCrate_Small_BP_C_0"): (-17, None),
                                 (CITY, "LootCrate_Small_BP_C_20"): (-17, "Industrial")})

    def test_office_desk_exact_generated_identity_keeps_its_stock_subclass(self):
        self.assertEqual(CRATES[OFFICE_DESK], LAKE)
        for index in (1, 2, 4, 7, 8, 9):
            self.assertEqual(loot_capacity(full_name(LAKE, f"LootCabinet_Desk_BP_C_{index}")), 4)
        low_tables = {loot_capacity(crate) for crate in CRATES if crate.startswith("LootCabinet_LowTable_BP_C ")}
        self.assertEqual(low_tables, {3})
        generated = outputs()
        native = generated["native/authored_loot_registry.hpp"]
        lua = generated["mod/SilverfishCoop/Scripts/authored_loot_registry.lua"]
        self.assertIn("0x449, 4, false, true};", native)
        self.assertIn('-205206528, STR("Office"), true, 2, &office_desk, {-7851936, -32237128, -43421604, -7392800}, true', native)
        self.assertIn("salt = -205206528, replenish_mode = 2, office_desk = true, guarantee_spawn = true", lua)


class LoosePickupVisibilityTests(unittest.IsolatedAsyncioTestCase):
    def test_unusable_quest_census_keeps_the_listen_world_and_other_pickups(self):
        loose = native_loose_pickup
        area = OBSERVATORY
        world = f"{area}.Observitory_P"
        source = world + ":PersistentLevel.Item_Bandage_C_1"
        item_class = ITEM["class_path"]
        header = [loose.QUEST_HEADER, "1"]
        valid = [loose.QUEST_ROW, world, source, "Collect_Test", item_class,
                 "12", "15", "17", "0", "90", "0", "1", "1", "1"]
        bad = [*valid[:-1], "0"]
        stock = [loose.ROW, area, source, item_class, "1", "available", "12", "15", "17"]
        bridge = runtime.Bridge(None, None)
        preamble = [["schema", "1"], ["session", "host", "hosting", area, "1", "0"]]

        for rows in ([header, valid, bad], [header, valid, valid], [valid], [header, header]):
            with self.subTest(rows=rows):
                text = "".join("\t".join(row) + "\n" for row in (*preamble, *rows, stock))
                report = bridge._parse_status(text)
                self.assertIsNotNone(report)
                self.assertEqual((report["state"], report["map"]), ("hosting", area))
                self.assertEqual(report[loose.KEY][source]["state"], "available")
                self.assertNotIn(loose.QUEST_SOURCES, report)

    async def test_quest_placement_renews_only_fresh_frames_and_honors_an_empty_census(self):
        loose = native_loose_pickup
        area, epoch = OBSERVATORY, "1" * 32
        world = f"{area}.Observitory_P"
        path = world + ":PersistentLevel.Item_Bandage_C_1"
        axes = ["12", "15", "17", "0", "90", "0", "1", "1", "1"]
        fields = [loose.QUEST_ROW, world, path, "Collect_Test", ITEM["class_path"], *axes]
        source, row = loose.parse_quest_placement(fields, "hosting", area)
        with self.assertRaises(ValueError):
            loose.parse_quest_placement(fields, "connected", area)
        status = {"state": "connected", "map": area, loose.VIEW_SCOPE: 1, loose.CAPABILITY: 1}

        async def project(action, *_arguments, **_options):
            return {**status, "ack": {"message": "1"}} if action == loose.QUEST_RENEW else status

        client = SimpleNamespace(last_game=status, zone_epoch=epoch, host_session="live host", needs_travel=False,
                                 server_transitioning=False, gameplay_seen=10, control=Mock(),
                                 bridge=SimpleNamespace(command=AsyncMock(side_effect=project)))
        view = loose.QuestPlacementView()
        frame = {"map": area, "epoch": epoch, "items": {source: row}}
        client.gameplay = {loose.STATE: [], loose.QUEST_FRAME: frame}
        with patch.object(loose.time, "monotonic", return_value=10) as clock:
            await view.project(client)
            clock.return_value = 10.3
            await view.project(client)
            self.assertEqual(client.bridge.command.await_count, 1)
            clock.return_value = 10.7
            await view.project(client)
            self.assertEqual(client.bridge.command.await_count, 2)
            client.bridge.command.assert_awaited_with(
                loose.QUEST_RENEW, epoch, path, "1", timeout=1.5, update_status=False)
            clock.return_value = 12
            await view.project(client)
            client.gameplay_seen = 12
            client.gameplay.pop(loose.QUEST_FRAME)
            await view.project(client)
            self.assertEqual(client.bridge.command.await_count, 2)
            client.gameplay[loose.QUEST_FRAME] = {**frame, "items": {}}
            await view.project(client)
        client.bridge.command.assert_awaited_with(loose.QUEST_CLEAR, epoch, path, timeout=1.5, update_status=False)
        self.assertFalse(view.leases)

    async def test_full_quest_frame_keeps_live_sources_and_retires_only_invalid_entries(self):
        loose = native_loose_pickup
        area, epoch = CROSSROADS, "1" * 32
        world = f"{area}.{area.rsplit('/', 1)[-1]}"
        sources = [f"{world}:PersistentLevel.Item_Bandage_C_{index:02d}" for index in range(loose.QUEST_LIMIT)]
        row = {"quest": "Collect_Test", "class_path": ITEM["class_path"],
               "transform": {"position": [12, 15, 17], "rotation": [0, 90, 0], "scale": [1, 1, 1]}}
        records = [("schema", "1"), ("session", "host", "hosting", area, "1", "0"), (loose.QUEST_HEADER, "1")]
        records.extend((loose.QUEST_ROW, world, source, row["quest"], row["class_path"],
                        "12", "15", "17", "0", "90", "0", "1", "1", "1") for source in sources)
        text = "".join("\t".join(record) + "\n" for record in records)
        items = runtime.Bridge(None, None)._parse_status(text)[loose.QUEST_SOURCES]
        self.assertEqual(items, dict.fromkeys(sources, row))
        frame = {"map": area, "epoch": epoch, "items": items}
        snapshot = {"ok": True, "gameplay": {loose.STATE: [], loose.QUEST_FRAME: frame}}
        decoded = link._decode_response(link._encode_response(snapshot, snapshot=True))["result"]
        self.assertEqual(decoded, snapshot)
        status = {"state": "connected", "map": area, loose.VIEW_SCOPE: 7, loose.CAPABILITY: 1}
        now, expires = [10.0], {}
        missed = set()

        async def project(action, visit, source, *arguments, **_options):
            now[0] += .1
            self.assertEqual(visit, epoch)
            if action == loose.QUEST_PROJECT:
                self.assertIn(source, items)
                expires[source] = now[0] + 2.6
            elif action == loose.QUEST_CLEAR:
                expires.pop(source, None)
            else:
                self.assertEqual(action, loose.QUEST_RENEW)
                *other_sources, generation = arguments
                self.assertEqual(generation, "7")
                self.assertLessEqual(1 + len(other_sources), 32)
                mask = ""
                for path in (source, *other_sources):
                    self.assertIn(path, items)
                    self.assertNotIn(path, client.gameplay[loose.STATE])
                    if path in missed or expires[path] <= now[0]:
                        mask += "0"
                    else:
                        expires[path] = now[0] + 2.6
                        mask += "1"
                request = ["command", action, visit, source, *arguments]
                fields = [loose.QUEST_REPLY, "1", request[0], "connected", area, visit, source, "accepted", mask]
                return loose.parse_quest_reply("\t".join(fields) + "\n", request)
            return status

        bridge = SimpleNamespace(command=AsyncMock(side_effect=project))
        client = SimpleNamespace(last_game=status, zone_epoch=epoch, host_session="live host", needs_travel=False,
                                 server_transitioning=False, gameplay_seen=now[0], control=Mock(), bridge=bridge,
                                 gameplay=decoded["gameplay"])
        items = client.gameplay[loose.QUEST_FRAME]["items"]
        view = loose.QuestPlacementView()
        with patch.object(loose.time, "monotonic", side_effect=lambda: now[0]):
            for _ in range((len(sources) + 3) // 4 + 8):
                client.gameplay_seen = now[0]
                await view.project(client)
                now[0] += .25
                self.assertTrue(all(expiry > now[0] for expiry in expires.values()))
            self.assertEqual(set(expires), set(sources))
            omitted, claimed, moved, unavailable = sources[:4]
            del items[omitted]
            client.gameplay[loose.STATE].append(claimed)
            items[moved] = {**row, "transform": {**row["transform"], "position": [100, 200, 300]}}
            missed.add(unavailable)
            client.gameplay_seen = now[0] = now[0] + .65
            prior_count = bridge.command.await_count
            await view.project(client)

        requests = [call.args for call in bridge.command.await_args_list[prior_count:]]
        renewed_batches = [args[2:-1] for args in requests if args[0] == loose.QUEST_RENEW]
        self.assertEqual(len(renewed_batches), 3)
        self.assertEqual({path for batch in renewed_batches for path in batch},
                         set(sources) - {omitted, claimed, moved})
        self.assertNotIn(omitted, expires)
        self.assertNotIn(claimed, expires)
        self.assertEqual(view.leases[moved][0], items[moved])
        self.assertTrue(any(args[:3] == (loose.QUEST_PROJECT, epoch, unavailable) for args in requests))

    async def test_missing_streamed_quest_placements_do_not_starve_visible_sources(self):
        loose = native_loose_pickup
        area, epoch = OBSERVATORY, "1" * 32
        sources = [f"{area}|/FrontierPack/Maps/Yard_{index}.Yard:PersistentLevel.Item_Case_C_1" for index in range(5)]
        visible = sources[-1]
        row = {"quest": "Collect_Test", "class_path": ITEM["class_path"],
               "transform": {"position": [12, 15, 17], "rotation": [0, 90, 0], "scale": [1, 1, 1]}}
        status = {"state": "connected", "map": area, loose.VIEW_SCOPE: 1, loose.CAPABILITY: 1}
        frame = {"map": area, "epoch": epoch, "items": dict.fromkeys(sources, row)}

        async def project(action, visit, source, *arguments, **options):
            self.assertEqual((action, visit), (loose.QUEST_PROJECT, epoch))
            if source != visible:
                raise RuntimeError("quest_placement_source_unavailable")
            return status

        for delay in (.25, .8):
            with self.subTest(delay=delay):
                bridge = SimpleNamespace(command=AsyncMock(side_effect=project))
                client = SimpleNamespace(last_game=status, zone_epoch=epoch, host_session="live host", needs_travel=False,
                                         server_transitioning=False, gameplay_seen=10, control=Mock(), bridge=bridge,
                                         gameplay={loose.STATE: [], loose.QUEST_FRAME: frame})
                view = loose.QuestPlacementView()
                with patch.object(loose.time, "monotonic", return_value=10) as clock:
                    await view.project(client)
                    self.assertEqual(bridge.command.await_count, 4)
                    self.assertNotIn(visible, view.leases)
                    clock.return_value = client.gameplay_seen = 10 + delay
                    await view.project(client)
                self.assertIn(visible, view.leases)
                self.assertEqual(bridge.command.await_args_list[4].args[2], visible)
                self.assertEqual(bridge.command.await_count, 5 if delay == .25 else 8)

    async def test_failed_quest_retires_do_not_hold_back_active_renewals(self):
        loose = native_loose_pickup
        area, epoch = OBSERVATORY, "1" * 32
        sources = [f"{area}|/FrontierPack/Maps/Yard_{index}.Yard:PersistentLevel.Item_Case_C_1" for index in range(5)]
        visible = sources[-1]
        row = {"quest": "Collect_Test", "class_path": ITEM["class_path"],
               "transform": {"position": [12, 15, 17], "rotation": [0, 90, 0], "scale": [1, 1, 1]}}
        status = {"state": "connected", "map": area, loose.VIEW_SCOPE: 1, loose.CAPABILITY: 1}

        async def project(action, visit, source, *arguments, **options):
            if action == loose.QUEST_CLEAR:
                self.assertNotEqual(source, visible)
                raise RuntimeError("quest_placement_clear_scope_unavailable")
            if action == loose.QUEST_RENEW:
                return {**status, "ack": {"message": "1"}}
            return status

        bridge = SimpleNamespace(command=AsyncMock(side_effect=project))
        frame = {"map": area, "epoch": epoch, "items": dict.fromkeys(sources, row)}
        client = SimpleNamespace(last_game=status, zone_epoch=epoch, host_session="live host", needs_travel=False,
                                 server_transitioning=False, gameplay_seen=10, control=Mock(), bridge=bridge,
                                 gameplay={loose.STATE: [], loose.QUEST_FRAME: frame})
        view = loose.QuestPlacementView()
        with patch.object(loose.time, "monotonic", return_value=10) as clock:
            for tick in (10, 10.25):
                clock.return_value = client.gameplay_seen = tick
                await view.project(client)
            self.assertEqual(set(view.leases), set(sources))
            frame["items"] = {visible: row}
            for tick in (10.5, 11.3):
                clock.return_value = client.gameplay_seen = tick
                await view.project(client)
        self.assertEqual(view.leases[visible][1], 11.3)

    async def test_quest_claim_requires_retention_before_pickup_and_replays_without_writing(self):
        loose = native_loose_pickup
        area, epoch, character = OBSERVATORY, "1" * 32, "guest"
        path = f"{area}.Observitory_P:PersistentLevel.Item_Bandage_C_1"
        quest, slot = "Collect_Test", "SFCoop_" + "b" * 32
        scene = {"state": "hosting", "map": area, loose.CAPABILITY: 1,
                 loose.QUEST_SOURCES: {path: {"quest": quest, "class_path": ITEM["class_path"]}}}
        state = party.new_party()
        state["players"][character] = {"downed": False}
        host = SimpleNamespace(last_game=scene, zone_epoch=epoch, native_profile=Path("fixture"), stopping=False,
                               transitioning=False, native_checkpoint_unsafe=False, party=state,
                               control=Mock(), checkpoint_native=AsyncMock(), loose_pickups=loose.Visibility())
        bridge = SimpleNamespace(command=AsyncMock())
        target = f"loose:{path}@{epoch}"
        fields = [loose.CLAIM, area, path, ITEM["class_path"], "2", PAWN, slot, "claimed", "1", "1",
                  ITEM["guid"], "0", "0", "0", "memory", quest]
        with self.assertRaises(ValueError):
            loose.parse_claim([*fields[:14], "disk", quest], "hosting", area)
        proof = loose.parse_claim(fields, "hosting", area)
        with (patch.object(native_loot, "_scope", return_value=PAWN),
              patch.object(native_loot, "remember_claim_snapshot"),
              patch.object(native_loot.native_pickup_audio, "committed"),
              patch.object(native_host_save, "new_slot", return_value=slot),
              patch.object(native_host_save, "record") as save,
              patch("tools.silverfish.native_inventory.retain_guest_save", new_callable=AsyncMock) as retain):
            retain.return_value = False
            with self.assertRaisesRegex(party.RuleError, "memory_retention_unavailable"):
                await loose.claim(host, bridge, character, target, OPERATION)
            bridge.command.assert_not_awaited()
            retain.return_value = True
            bridge.command.return_value = {**scene, loose.PROOF: proof}
            claimed = await loose.claim(host, bridge, character, target, OPERATION)
            self.assertEqual(claimed[loose.PROOF]["quest"], quest)
            self.assertEqual(await loose.claim(host, bridge, character, target, OPERATION), claimed)
            save.assert_not_called()
        bridge.command.assert_awaited_once_with(loose.CLAIM, PAWN, slot, path, "memory-retained", timeout=7)
        host.checkpoint_native.assert_not_awaited()
        self.assertFalse(host.native_checkpoint_unsafe)

    async def test_streamed_quest_projection_and_tombstone_use_the_exact_occurrence(self):
        loose = native_loose_pickup
        area, epoch = OBSERVATORY, "1" * 32
        world = f"{area}.Observitory_P"
        level = "/FrontierPack/Maps/HideoutA.Hideout:PersistentLevel."
        raw = level + "Item_Case_C_1"
        source = f"{area}|{raw}"
        sibling = source.replace("/HideoutA.", "/HideoutB.")
        other_world = "/Game/OtherMap/Elsewhere"
        quest, kind = "Collect_Test", ITEM["class_path"]
        axes = ["12", "15", "17", "0", "90", "0", "1", "1", "1"]
        for unqualified in (raw, f"{other_world}|{raw}"):
            fields = [loose.QUEST_ROW, world, unqualified, quest, kind, *axes]
            with self.subTest(source=unqualified), self.assertRaises(ValueError):
                loose.parse_quest_placement(fields, "hosting", area)
        placements = dict(loose.parse_quest_placement([loose.QUEST_ROW, world, path, quest, kind, *axes],
                                                     "hosting", area) for path in (source, sibling))
        status = {"state": "connected", "map": area, loose.VIEW_SCOPE: 1, loose.CAPABILITY: 1}
        bridge = SimpleNamespace(command=AsyncMock(return_value=status))
        client = SimpleNamespace(last_game=status, zone_epoch=epoch, host_session="live host", needs_travel=False,
                                 server_transitioning=False, gameplay_seen=10, control=Mock(), bridge=bridge)
        view = loose.QuestPlacementView()
        frame = {"map": area, "epoch": epoch, "items": placements}
        client.gameplay = {loose.STATE: [], loose.QUEST_FRAME: frame}
        with patch.object(loose.time, "monotonic", return_value=10):
            await view.project(client)
            bridge.command.assert_any_await(loose.QUEST_PROJECT, epoch, source, quest, kind,
                                            *map(str, map(float, axes)), timeout=1.5, update_status=False)
            self.assertEqual(set(view.leases), {source, sibling})
            client.gameplay[loose.STATE] = [source]
            await view.project(client)
        bridge.command.assert_awaited_with(loose.QUEST_CLEAR, epoch, source, timeout=1.5, update_status=False)
        self.assertEqual(set(view.leases), {sibling})

        state = party.new_party()
        receipt = [loose.CLAIM, area, source, kind, "2", PAWN, "SFCoop_" + "b" * 32, "claimed", "1", "1",
                   ITEM["guid"], "0", "0", "0", "memory", quest]
        proof = loose.parse_claim(receipt, "hosting", area)
        row = {field: proof[field] for field in ("map", "actor", "class_path", "mode", "guid", "quest")}
        state[loose.STATE] = {source: dict(row, owner="host", epoch=epoch)}
        saved = party.load_party(state)
        visibility = loose.Visibility()
        later = "2" * 32
        visibility.epoch = later
        visibility.replenished.add(source)
        host = SimpleNamespace(last_game={"map": area}, party=saved, zone_epoch=later)
        self.assertEqual(visibility.hidden(host), [source])
        self.assertEqual(saved[loose.STATE][source]["quest"], quest)

    async def test_streamed_claim_uses_the_owning_campaign_and_survives_missing_local_actor(self):
        loose = native_loose_pickup
        raw = "/FrontierPack/Maps/StockroomInstance.Stockroom:PersistentLevel.Item_Bandage_C_1"
        source = f"{OBSERVATORY}|{raw}"
        self.assertEqual(loose.target_path(f"loose:{source}"), source)
        self.assertIsNone(loose.target_path(f"loose:{raw}"))
        row = [loose.ROW, OBSERVATORY, source, ITEM["class_path"], "1", "available", "10", "20", "30"]
        self.assertEqual(loose.parse(row, "connected", OBSERVATORY)["target"], f"loose:{source}")
        row[1] = "/Game/OtherPack/Elsewhere"
        with self.assertRaises(ValueError):
            loose.parse(row, "connected", row[1])
        local_scene = {"state": "connected", "map": OBSERVATORY, loose.VIEW_SCOPE: 1, loose.CAPABILITY: 1}
        client = SimpleNamespace(last_game=local_scene)
        bridge = SimpleNamespace(command=AsyncMock(return_value=local_scene))
        view = loose.PickupView()
        await view.project(client, bridge, [source])
        await view.project(client, bridge, [source])
        bridge.command.assert_awaited_once_with(loose.PROJECT, "1", "hide", source, timeout=4)
        await view.project(client, bridge, [])
        self.assertEqual(bridge.command.await_args.args, (loose.PROJECT, "1", "show", source))

    async def test_transient_award_queues_a_typed_save_without_escrow_io(self):
        loose = native_loose_pickup
        path = f"{OBSERVATORY}.Observitory_P:PersistentLevel.Item_Bandage_C_1"
        slot, epoch = "SFCoop_" + "b" * 32, "d" * 32
        scene = {"state": "hosting", "map": OBSERVATORY, loose.CAPABILITY: 1,
                 "pawns": {"local": {"name": PAWN}}}
        record = [loose.CLAIM, OBSERVATORY, path, ITEM["class_path"], "2", PAWN, slot, "claimed", "1", "1",
                  ITEM["guid"], "0", "0", "0"]
        for mode in ([], ["memory"]):
            with self.subTest(mode=mode):
                proof = loose.parse_claim([*record, *mode], "hosting", OBSERVATORY)
                bridge = SimpleNamespace(command=AsyncMock(return_value={**scene, loose.PROOF: proof}))
                state = party.new_party()
                state["players"]["host"] = {"downed": False}
                host = SimpleNamespace(last_game=scene, zone_epoch=epoch, native_profile=Path("fixture"),
                                       stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                                       party=state, control=Mock(), checkpoint_native=AsyncMock(return_value=2),
                                       loose_pickups=loose.Visibility(), pending_host_save=None)
                with patch.object(native_host_save, "new_slot", return_value=slot), patch.object(native_host_save, "record") as saved:
                    result = await loose.claim(host, bridge, "host", f"loose:{path}", OPERATION)
                self.assertEqual(result[loose.PROOF]["guid"], ITEM["guid"])
                if mode:
                    host.checkpoint_native.assert_not_awaited()
                    self.assertIs(host.pending_host_save, True)
                else:
                    self.assertFalse(host.checkpoint_native.await_args.kwargs["typed_inventory"])
                self.assertEqual(saved.call_count, int(not mode))
                self.assertFalse(host.native_checkpoint_unsafe)

    async def test_placed_stack_merge_keeps_source_and_queues_typed_save(self):
        loose = native_loose_pickup
        path = f"{OBSERVATORY}.Observitory_P:PersistentLevel.Item_Bandage_C_1"
        slot, epoch, destination = "SFCoop_" + "b" * 32, "d" * 32, "B" * 32
        scene = {"state": "hosting", "map": OBSERVATORY, loose.CAPABILITY: 1,
                 "pawns": {"local": {"name": PAWN}}}
        receipt = [loose.CLAIM, OBSERVATORY, path, ITEM["class_path"], "2", PAWN, slot, "merged", "1", "1",
                   ITEM["guid"], "0", "0", "0", "memory", ITEM["guid"], destination, "2", "4", "6"]
        with self.assertRaisesRegex(ValueError, "source changed"):
            loose.parse_claim([*receipt[:15], "C" * 32, *receipt[16:]], "hosting", OBSERVATORY)
        proof = loose.parse_claim(receipt, "hosting", OBSERVATORY)
        bridge = SimpleNamespace(command=AsyncMock(return_value={**scene, loose.PROOF: proof}))
        state = party.new_party()
        state["players"]["host"] = {"downed": False}
        host = SimpleNamespace(last_game=scene, zone_epoch=epoch, native_profile=Path("fixture"),
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                               party=state, control=Mock(), checkpoint_native=AsyncMock(return_value=2),
                               loose_pickups=loose.Visibility(), pending_host_save=None)
        with patch.object(native_host_save, "new_slot", return_value=slot), patch.object(native_host_save, "record") as saved:
            result = await loose.claim(host, bridge, "host", f"loose:{path}", OPERATION)
        row = result[loose.PROOF]
        self.assertEqual((row["actor"], row["guid"], row["inventory_guid"]), (path, ITEM["guid"], destination))
        self.assertEqual(row["stack_merge"], {"amount": 2, "before": 4, "after": 6})
        loose.validate_records(host.party, {})
        self.assertIn(path, host.loose_pickups.hidden(host))
        self.assertEqual(await loose.claim(host, bridge, "host", f"loose:{path}", OPERATION), result)
        bridge.command.assert_awaited_once()
        host.checkpoint_native.assert_not_awaited()
        self.assertIs(host.pending_host_save, True)
        saved.assert_not_called()

    async def test_revisit_waits_for_stock_availability_of_temporary_and_per_visit_items(self):
        loose = native_loose_pickup
        old_epoch, new_epoch = "a" * 32, "b" * 32
        temporary = f"{CROSSROADS}.CrossRoads_P:PersistentLevel.Item_Test_C_1"
        permanent = f"{CROSSROADS}.CrossRoads_P:PersistentLevel.Item_Test_C_2"
        per_visit = f"{CROSSROADS}.CrossRoads_P:PersistentLevel.Item_Test_C_3"
        game = {"state": "hosting", "map": CROSSROADS, loose.CAPABILITY: 1}
        ledger = {path: dict(map=CROSSROADS, epoch=old_epoch, mode=mode)
                  for mode, path in enumerate((per_visit, temporary, permanent))}
        host = SimpleNamespace(last_game=game, zone_epoch=old_epoch, party={loose.STATE: ledger},
                               transitioning=False, native_checkpoint_unsafe=False)
        view = loose.Visibility()
        self.assertEqual(set(view.hidden(host)), {temporary, permanent, per_visit})
        host.zone_epoch = new_epoch
        self.assertEqual(set(view.hidden(host)), {temporary, permanent, per_visit})
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            dict(game, **{loose.CHECK_KEY: {temporary: False, per_visit: False}}),
            dict(game, **{loose.CHECK_KEY: {temporary: True, per_visit: False}}),
            dict(game, **{loose.CHECK_KEY: {per_visit: True}}),
        ]))
        await view.refresh(host, bridge)
        self.assertEqual(set(view.hidden(host)), {temporary, permanent, per_visit})
        view.next_check = 0
        await view.refresh(host, bridge)
        self.assertEqual(set(view.hidden(host)), {permanent, per_visit})
        view.next_check = 0
        await view.refresh(host, bridge)
        self.assertEqual(view.hidden(host), [permanent])
        self.assertEqual(bridge.command.await_args_list,
                         [call(loose.CHECK, per_visit, temporary, timeout=4)] * 2 + [call(loose.CHECK, per_visit, timeout=4)])


    async def test_projection_does_not_depend_on_the_local_nearby_list(self):
        loose = native_loose_pickup
        path = f"{CROSSROADS}.CrossRoads_P:PersistentLevel.Item_Test_C_1"
        game = {"state": "connected", "map": CROSSROADS, loose.CAPABILITY: 1, loose.VIEW_SCOPE: 4}
        client = SimpleNamespace(last_game=game)
        bridge = SimpleNamespace(command=AsyncMock(return_value=game))
        view = loose.PickupView()
        await view.project(client, bridge, [path])
        await view.project(client, bridge, [path])
        await view.project(client, bridge, None)
        await view.project(client, bridge, [])
        self.assertEqual(bridge.command.await_args_list,
                         [call(loose.PROJECT, "4", "hide", path, timeout=4),
                          call(loose.PROJECT, "4", "show", path, timeout=4)])

    async def test_claims_reapply_when_the_same_map_gets_a_new_native_scope(self):
        loose = native_loose_pickup
        path = f"{CROSSROADS}.CrossRoads_P:PersistentLevel.Item_Test_C_1"
        prior = {"state": "connected", "map": CROSSROADS, loose.CAPABILITY: 1, loose.VIEW_SCOPE: 4}
        reloaded = {**prior, loose.VIEW_SCOPE: 5}
        client = SimpleNamespace(last_game=prior)
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[prior, reloaded]))
        view = loose.PickupView()
        await view.project(client, bridge, [path])
        client.last_game = reloaded
        await view.project(client, bridge, [path])
        self.assertEqual(bridge.command.await_args_list,
                         [call(loose.PROJECT, "4", "hide", path, timeout=4),
                          call(loose.PROJECT, "5", "hide", path, timeout=4)])


class NativeLootTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        native_host_save.directory(self.root).mkdir(parents=True)

    def game(self, area, **fields):
        return {"state": "hosting", "map": area, "pawns": {"local": {"name": PAWN}}, **fields}

    def host(self, area, rolls=None):
        state = party.new_party()
        party.add_player(state, "host")
        state[native_loot.STATE_KEY] = rolls or {}
        return SimpleNamespace(last_game=self.game(area), last_seen=time.monotonic(), native_zones=True,
                               native_loot_input=True, fixtures=False, native_profile=self.root, stopping=False,
                               transitioning=False, native_checkpoint_unsafe=False, party=state, control=Mock(),
                               checkpoint_native=AsyncMock(return_value=2), pending_host_save=None)

    def frame(self, area, slot=None):
        slot = slot or native_host_save.new_slot()
        native_host_save.slot_path(self.root, slot).write_bytes(b"GVAS" + bytes(32))
        return native_host_save.record(self.root, slot, area)

    def roll(self, area, crate, operation):
        return {"id": operation, "map": area, "crate": crate, "items": [dict(ITEM)], "escrow": self.frame(area)}

    def test_interaction_uses_exact_map_crate_pawn_and_monotonic_sequence(self):
        proof = {"map": DEAD_BRIDGE, "crate": BRIDGE_CRATE, "pawn": PAWN, "entry": "stock", "sequence": 4}
        game = self.game(DEAD_BRIDGE, native_loot_crate_interaction=proof)
        self.assertEqual(native_loot.interaction(game, 3), (4, BRIDGE_CRATE))
        self.assertIsNone(native_loot.interaction(game, 4))
        for change in ({"crate": OTHER_CRATE}, {"pawn": "another pawn"}, {"map": CROSSROADS}):
            with self.subTest(change=change):
                self.assertIsNone(native_loot.interaction(dict(game, native_loot_crate_interaction=proof | change), 3))

    def test_batched_container_input_keeps_each_exact_press_in_order(self):
        first = {"map": CROSSROADS, "crate": LEGACY_CRATE, "pawn": PAWN, "entry": "stock", "sequence": 11}
        second = dict(first, crate=OTHER_CRATE, sequence=13)
        foreign = dict(first, pawn="another pawn", sequence=14)
        game = self.game(CROSSROADS, native_loot_crate_interactions=[second, first, foreign, first])
        self.assertEqual(native_loot.interactions(game, 10), [(11, LEGACY_CRATE, "stock"), (13, OTHER_CRATE, "stock")])
        self.assertEqual(native_loot.interactions(game, 11), [(13, OTHER_CRATE, "stock")])

    def test_fast_container_input_merges_with_status_without_losing_older_fallback(self):
        def ring(sequence, crate=OTHER_CRATE, pawn=PAWN, area=CROSSROADS):
            return f"loot-crate-open\t{area}\t{crate}\t{pawn}\t{sequence}\n".encode()

        for role in ("hosting", "connected"):
            with self.subTest(role=role):
                inputs = native_loot.OpenIntents()
                game = self.game(CROSSROADS, state=role)
                mixed = [ring(11), ring(9), ring(11), ring(14, pawn="foreign"), ring(15, BRIDGE_CRATE)]
                self.assertEqual(inputs.take(game, mixed),
                                 [(9, OTHER_CRATE, "stock", "memory_input"), (11, OTHER_CRATE, "stock", "memory_input")])
                proof = {"map": CROSSROADS, "crate": LEGACY_CRATE, "pawn": PAWN, "entry": "fixture", "sequence": 10}
                duplicate = dict(proof, crate=OTHER_CRATE, entry="stock", sequence=11)
                game["native_loot_crate_interactions"] = [duplicate, proof]
                self.assertEqual(inputs.take(game, [ring(11)]), [(10, LEGACY_CRATE, "fixture", "status")])
                self.assertEqual(inputs.take(game, mixed), [])

    def test_status_wins_when_the_exact_container_input_arrives_on_both_paths(self):
        game = self.game(DEAD_BRIDGE, native_loot_crate_interaction={
            "map": DEAD_BRIDGE, "crate": BRIDGE_CRATE, "pawn": PAWN, "entry": "stock", "sequence": 12,
        })
        frame = f"loot-crate-open\t{DEAD_BRIDGE}\t{BRIDGE_CRATE}\t{PAWN}\t12\n".encode()
        inputs = native_loot.OpenIntents()
        self.assertEqual(inputs.take(game, [frame]), [(12, BRIDGE_CRATE, "stock", "status")])
        self.assertEqual(inputs.take(game, [frame]), [])

    def test_publication_excludes_other_maps_and_invalid_saved_identity(self):
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        records = {
            LEGACY_CRATE: self.roll(CROSSROADS, OTHER_CRATE, "2" * 32),
            OTHER_CRATE: roll,
            BRIDGE_CRATE: self.roll(DEAD_BRIDGE, BRIDGE_CRATE, "3" * 32),
        }
        self.assertEqual(native_loot.published(records, CROSSROADS), {OTHER_CRATE: native_loot.public_roll(roll)})

    def test_distant_crate_item_remains_visible_near_guest_or_while_held(self):
        transform = {"position": [3000., 0., 0.], "rotation": [0., 0., 0.], "scale": [1., 1., 1.]}
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        roll["items"][0]["transform"] = transform
        rolls = {OTHER_CRATE: roll}
        pawns = {"local": {"name": f"Player_FP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Player_FP_C_0",
                           "location": [0., 0., 0.]}}
        game = self.game(CROSSROADS, pawns=pawns)
        identity = native_world_loot.crate_identity(roll["id"], ITEM["guid"])
        self.assertEqual(native_world_loot.nearby(game, rolls, OPERATION), {})
        self.assertIn(identity, native_world_loot.nearby(game, rolls, OPERATION, pinned={identity}))
        pawns["guest"] = {"location": transform["position"]}
        self.assertIn(identity, native_world_loot.nearby(game, rolls, OPERATION))
        transform["scale"] = [0., 1., 1.]
        self.assertEqual(native_world_loot.nearby(game, rolls, OPERATION, pinned={identity}), {})

    async def test_only_authority_replenishment_retires_the_exact_saved_roll(self):
        expired = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        untouched = self.roll(CROSSROADS, LEGACY_CRATE, "2" * 32)
        host = self.host(CROSSROADS, {OTHER_CRATE: expired, LEGACY_CRATE: untouched})
        ready = dict(map=CROSSROADS, crate=OTHER_CRATE, state="ready")
        host.last_game["native_loot_crates"] = {OTHER_CRATE: ready}
        bridge = SimpleNamespace(command=AsyncMock())
        await native_loot.retire_replenished(host, bridge)
        host.checkpoint_native.assert_not_awaited()
        host.last_game[native_loot.REPLENISHED] = {OTHER_CRATE: dict(map=CROSSROADS, crate=OTHER_CRATE, pawn="guest")}
        await native_loot.retire_replenished(host, bridge)
        host.checkpoint_native.assert_not_awaited()
        host.last_game[native_loot.REPLENISHED][OTHER_CRATE]["pawn"] = PAWN
        self.assertEqual(set(native_loot.published(host.party[native_loot.STATE_KEY], CROSSROADS,
                                                  game=host.last_game)), {LEGACY_CRATE})
        self.assertIs(await native_loot.project_opened(bridge, host.last_game, host.party[native_loot.STATE_KEY]), host.last_game)
        bridge.command.assert_not_awaited()
        row = dict(phase="hosting", map=CROSSROADS, crate=OTHER_CRATE, scope="active",
                   box="live", looted=True, collision=0)
        bridge.command.return_value = self.game(CROSSROADS, **{native_loot.CRATE_DIAGNOSTICS: {OTHER_CRATE: row}})
        await native_loot.retire_replenished(host, bridge)
        self.assertEqual(host.party[native_loot.STATE_KEY][OTHER_CRATE], expired)
        self.assertNotIn(native_loot.RETIRED, host.party)

        row.update(looted=False, collision=1)
        await native_loot.retire_replenished(host, bridge)
        self.assertEqual(bridge.command.await_count, 2)
        self.assertEqual(host.party[native_loot.STATE_KEY], {LEGACY_CRATE: untouched})
        self.assertEqual(host.party[native_loot.RETIRED], {"1" * 32: OTHER_CRATE})
        host.checkpoint_native.assert_not_awaited()
        self.assertIs(host.pending_host_save, False)
        with self.assertRaisesRegex(party.RuleError, "roll_replenished"):
            await native_loot.perform(host, bridge, "host", "open", OTHER_CRATE, expired["id"])
        self.assertEqual(bridge.command.await_count, 2)
        self.assertEqual(native_loot.default_crate(CROSSROADS), LEGACY_CRATE)
        self.assertEqual(native_loot.default_crate(DEAD_BRIDGE), BRIDGE_CRATE)
        self.assertEqual(native_loot.fixture_selection("host", CROSSROADS), ("host", LEGACY_CRATE))
        self.assertEqual(native_loot.fixture_selection(f"guest2@{OTHER_CRATE}", CROSSROADS), ("guest2", OTHER_CRATE))

    def test_snapshot_reads_multiple_exact_crates_with_legacy_field(self):
        unsupported = full_name(CROSSROADS, "LootCrate_Small_BP_C_99999")
        data = [
            ("schema", "1"), ("session", "client", "connected", CROSSROADS, "0", "1"),
            ("native-loot-crate-input", CROSSROADS, LEGACY_CRATE, "exhausted"),
            ("native-loot-crate-input", CROSSROADS, OTHER_CRATE, "ready"),
            ("native-loot-crate-input", CROSSROADS, unsupported, "ready"),
            ("native-loot-crate-input", CROSSROADS, BRIDGE_CRATE, "ready"),
        ]
        (self.root / "status.tsv").write_text("".join("\t".join(fields) + "\n" for fields in data), encoding="utf-8")
        snapshot = runtime.Bridge(self.root, None).snapshot()
        self.assertEqual({crate: row["state"] for crate, row in snapshot["native_loot_crates"].items()},
                         {LEGACY_CRATE: "exhausted", OTHER_CRATE: "ready"})
        self.assertEqual(snapshot["native_loot_crates"][LEGACY_CRATE]["state"], "exhausted")

    def test_compact_stock_crate_snapshot_preserves_locks_lids_and_replenishment(self):
        angle_crate = next(crate for crate, area in CRATES.items() if area == CROSSROADS and random_lid(crate))
        locked_crate = next(crate for crate, area in CRATES.items() if area == CROSSROADS
                            and crate != angle_crate and lockable_container(crate, area))
        pose, angle, _ = random_lid(angle_crate)
        rows = [
            ("schema", "1"), ("session", "host", "hosting", CROSSROADS, "1", "0"),
            ("pawn", "local", PAWN, "100", "1", "2", "3", "local controller"),
            ("native-loot-crate-state", str(CRATE_NAMES_BY_INDEX.index(LEGACY_CRATE)), "ready", "1", "-", "-"),
            ("native-loot-crate-state", str(CRATE_NAMES_BY_INDEX.index(angle_crate)), "exhausted", "0", "-", str(angle)),
            ("native-loot-crate-state", str(CRATE_NAMES_BY_INDEX.index(locked_crate)), "ready", "0", "1", "-"),
        ]

        def parse(records):
            text = "".join("\t".join(row) + "\n" for row in records)
            return runtime.Bridge(self.root, None)._parse_status(text)

        snapshot = parse(rows)
        self.assertEqual(snapshot["native_loot_crates"][LEGACY_CRATE]["state"], "ready")
        self.assertEqual(snapshot["native_loot_crates"][angle_crate][pose], angle)
        self.assertEqual(snapshot[native_loot.REPLENISHED][LEGACY_CRATE]["pawn"], PAWN)
        self.assertEqual(snapshot[native_loot.VEHICLE_LOCKS], {locked_crate: True})
        for bad_index in ("0", str(len(CRATE_NAMES_BY_INDEX)), str(CRATE_NAMES_BY_INDEX.index(BRIDGE_CRATE))):
            self.assertIsNone(parse(rows + [("native-loot-crate-state", bad_index, "ready", "0", "-", "-")]))

    async def test_new_roll_commits_only_its_crate_and_reopen_uses_saved_roll(self):
        legacy_roll = self.roll(CROSSROADS, LEGACY_CRATE, "1" * 32)
        host = self.host(CROSSROADS, {LEGACY_CRATE: legacy_roll})

        async def open_crate(verb, pawn, crate, slot, *, timeout):
            self.assertEqual((verb, pawn, crate), ("native-loot-open", PAWN, OTHER_CRATE))
            self.frame(CROSSROADS, slot)
            proof = {"action": "open", "map": CROSSROADS, "pawn": pawn, "crate": crate, "escrow_slot": slot,
                     "ok": True, "invoked": True, "looted": True, "trait_calls": 1, "items": [ITEM],
                     "item_count": 1, "lucky_looter": False, "trait_source": "canonical"}
            return self.game(CROSSROADS, native_loot=proof)

        bridge = SimpleNamespace(command=AsyncMock(side_effect=open_crate))
        result = await native_loot.perform(host, bridge, "host", "open", OTHER_CRATE, OPERATION)
        self.assertEqual(result["native_loot_roll"]["crate"], OTHER_CRATE)
        self.assertNotIn("escrow", result["native_loot_roll"])
        self.assertEqual(host.party[native_loot.STATE_KEY][LEGACY_CRATE], legacy_roll)
        self.assertEqual(await native_loot.perform(host, bridge, "host", "open", OTHER_CRATE, OPERATION), result)
        bridge.command.assert_awaited_once()
        host.checkpoint_native.assert_awaited_once()

    async def test_trait_queries_follow_the_exact_authored_guarantee(self):
        guaranteed_crate = full_name(OBSERVATORY, "LootCrate_Small_BP_C_1")
        mixed_crate = full_name(RAIL_YARD, "LootCrate_Large_BP_C_3")
        for area, crate, calls, can_commit in (
            (OBSERVATORY, guaranteed_crate, 0, True),
            (OBSERVATORY, guaranteed_crate, 1, False),
            (RAIL_YARD, mixed_crate, 1, True),
            (RAIL_YARD, mixed_crate, 2, False),
            (CROSSROADS, OTHER_CRATE, 0, False),
        ):
            with self.subTest(crate=crate, trait_calls=calls):
                host = self.host(area)

                async def open_crate(verb, pawn, current, slot, *, timeout):
                    self.assertEqual((verb, pawn, current), ("native-loot-open", PAWN, crate))
                    self.frame(area, slot)
                    proof = {"action": "open", "map": area, "pawn": PAWN, "crate": crate, "escrow_slot": slot,
                             "ok": True, "invoked": True, "looted": True, "trait_calls": calls, "items": [ITEM],
                             "item_count": 1, "lucky_looter": True, "trait_source": "canonical"}
                    return self.game(area, native_loot=proof)

                bridge = SimpleNamespace(command=AsyncMock(side_effect=open_crate))
                if can_commit:
                    result = await native_loot.perform(host, bridge, "host", "open", crate, OPERATION)
                    self.assertEqual(result["native_loot_roll"]["trait_calls"], calls)
                    self.assertTrue(result["native_loot_roll"]["lucky_looter"])
                    host.checkpoint_native.assert_awaited_once()
                else:
                    with self.assertRaisesRegex(RuntimeError, "exact stock crate roll did not verify"):
                        await native_loot.perform(host, bridge, "host", "open", crate, OPERATION)
                    host.checkpoint_native.assert_not_awaited()
                    self.assertTrue(host.stopping)

    async def test_cabinet_commit_preserves_four_rows_and_one_immutable_escrow(self):
        items = [dict(ITEM, actor=f"stock item {index}", guid=f"{index:032X}") for index in range(1, 5)]
        for area, container, accepted in ((LAB_E206, SMALL_CABINET, True), (CROSSROADS, OTHER_CRATE, False)):
            with self.subTest(container=container):
                host = self.host(area)

                async def open_container(verb, pawn, current, slot, *, timeout):
                    self.assertEqual((verb, pawn, current), ("native-loot-open", PAWN, container))
                    self.frame(area, slot)
                    return self.game(area, native_loot={"action": "open", "map": area, "pawn": pawn, "crate": current,
                                                        "escrow_slot": slot, "ok": True, "invoked": True,
                                                        "looted": True, "trait_calls": 5 if accepted else 3,
                                                        "items": items, "item_count": 4, "lucky_looter": True,
                                                        "trait_source": "canonical"})

                bridge = SimpleNamespace(command=AsyncMock(side_effect=open_container))
                if not accepted:
                    with self.assertRaisesRegex(RuntimeError, "exact stock crate roll did not verify"):
                        await native_loot.perform(host, bridge, "host", "open", container, OPERATION)
                    host.checkpoint_native.assert_not_awaited()
                    continue

                result = await native_loot.perform(host, bridge, "host", "open", container, OPERATION)
                self.assertEqual(result["native_loot_roll"]["items"], items)
                repeated = await native_loot.perform(host, bridge, "host", "open", container, "e" * 32)
                self.assertEqual(repeated, result)
                bridge.command.assert_awaited_once()
                host.checkpoint_native.assert_awaited_once()
                saved = {"world": {"party": host.party}, "characters": {}}
                self.assertEqual(native_host_save.references(saved), [host.party[native_loot.STATE_KEY][container]["escrow"]])
                host.party[native_loot.STATE_KEY][container]["items"][-1] = dict(items[-1], guid=items[0]["guid"])
                with self.assertRaisesRegex(CampaignFormatError, "stock container escrow has an invalid identity or item"):
                    native_host_save.references(saved)

    async def test_office_desk_empty_and_nonempty_rolls_commit_once_without_stock_trait_queries(self):
        for count, queries, accepted in ((0, 0, True), (4, 0, True), (4, 1, False), (5, 0, False)):
            with self.subTest(count=count, queries=queries):
                host = self.host(LAKE)
                items = [dict(ITEM, actor=f"Office item {index}", guid=f"{index:032X}") for index in range(1, count + 1)]

                async def open_desk(verb, pawn, crate, slot, *, timeout):
                    self.assertEqual((verb, pawn, crate), ("native-loot-open", PAWN, OFFICE_DESK))
                    self.frame(LAKE, slot)
                    proof = {"action": "open", "map": LAKE, "pawn": pawn, "crate": crate, "escrow_slot": slot,
                             "ok": True, "invoked": True, "looted": True, "trait_calls": queries, "items": items,
                             "item_count": count, "lucky_looter": True, "trait_source": "canonical"}
                    return self.game(LAKE, native_loot=proof)

                bridge = SimpleNamespace(command=AsyncMock(side_effect=open_desk))
                if not accepted:
                    with self.assertRaisesRegex(RuntimeError, "exact stock crate roll did not verify"):
                        await native_loot.perform(host, bridge, "host", "open", OFFICE_DESK, OPERATION)
                    host.checkpoint_native.assert_not_awaited()
                    self.assertTrue(host.stopping)
                    continue
                result = await native_loot.perform(host, bridge, "host", "open", OFFICE_DESK, OPERATION)
                self.assertEqual(result["native_loot_roll"]["items"], items)
                repeated = await native_loot.perform(host, bridge, "host", "open", OFFICE_DESK, "e" * 32)
                self.assertEqual(repeated, result)
                bridge.command.assert_awaited_once()
                host.checkpoint_native.assert_awaited_once()
                saved = {"world": {"party": host.party}, "characters": {}}
                persisted = host.party[native_loot.STATE_KEY][OFFICE_DESK]
                self.assertEqual(native_host_save.references(saved), [persisted["escrow"]])
                persisted["trait_calls"] = 1
                with self.assertRaises(CampaignFormatError):
                    native_host_save.references(saved)

    def test_bridge_bounds_container_receipt_before_collecting_items(self):
        slot = "SFCoop_" + "a" * 32
        for area, container, declared, count, accepted in (
            (LAB_E206, SMALL_CABINET, 4, 4, True),
            (LAB_E206, SMALL_CABINET, 4, 5, False),
            (CROSSROADS, OTHER_CRATE, 4, 4, False),
            (CROSSROADS, SMALL_CABINET, 2, 2, False),
            (LAKE, OFFICE_DESK, 4, 4, True),
            (LAKE, OFFICE_DESK, 5, 5, False),
        ):
            with self.subTest(container=container, declared=declared, count=count):
                records = [
                    ("schema", "1"), ("session", "host", "hosting", area, "0", "1"),
                    ("native-loot", "open", area, container, PAWN, "1", "opened", "1", "1", "canonical", "1",
                     "100", str(declared), "1", slot),
                ]
                records.extend(("native-loot-item", f"item {index}", ITEM["class_path"], f"{index:032X}")
                               for index in range(1, count + 1))
                (self.root / "status.tsv").write_text("".join("\t".join(row) + "\n" for row in records), encoding="utf-8")
                proof = runtime.Bridge(self.root, None).snapshot()
                if accepted:
                    self.assertEqual(len(proof["native_loot"]["items"]), count)
                else:
                    self.assertNotIn("native_loot", proof or {})

    def test_stock_loot_preserves_actor_transform_and_optional_ammo(self):
        slot = "SFCoop_" + "a" * 32
        heading = f"native-loot\topen\t{CROSSROADS}\t{OTHER_CRATE}\t{PAWN}\t1\topened\t1\t1\tcanonical\t1\t100\t1\t1\t{slot}\n"
        item = f"native-loot-item\tstock actor\t{ITEM['class_path']}\t{ITEM['guid']}"
        status = self.root / "status.tsv"
        base = f"schema\t1\nsession\thost\thosting\t{CROSSROADS}\t0\t1\n" + heading + item
        status.write_text(base + "\t100\t200\t-32.5\t0\t-90\t5\t1\t1\t0.7\n", encoding="utf-8")
        old = runtime.Bridge(self.root, None).snapshot()["native_loot"]["items"][0]
        self.assertEqual(old["transform"], {"position": [100, 200, -32.5], "rotation": [0, -90, 5], "scale": [1, 1, 0.7]})
        self.assertNotIn("ammo", old)
        status.write_text(base + "\t17\t100\t200\t-32.5\t0\t-90\t5\t1\t1\t0.7\n", encoding="utf-8")
        updated = runtime.Bridge(self.root, None).snapshot()["native_loot"]["items"][0]
        self.assertEqual(updated, {**old, "ammo": 17})
        status.write_text(base + "\t2147483648\t100\t200\t-32.5\t0\t-90\t5\t1\t1\t0.7\n", encoding="utf-8")
        self.assertNotIn("native_loot", runtime.Bridge(self.root, None).snapshot() or {})
        status.write_text(base + "\t100\tnan\t-32.5\t0\t-90\t5\t1\t1\t0.7\n", encoding="utf-8")
        self.assertNotIn("native_loot", runtime.Bridge(self.root, None).snapshot() or {})

    async def test_claim_requires_exact_scope_and_retries_keep_the_original_crate(self):
        rolls = {crate: self.roll(CROSSROADS, crate, str(index) * 32)
                 for index, crate in enumerate((LEGACY_CRATE, OTHER_CRATE), start=1)}
        host = self.host(CROSSROADS, rolls)
        slot = rolls[OTHER_CRATE]["escrow"]["slot"]
        proof = {"action": "claim", "map": CROSSROADS, "pawn": PAWN, "crate": OTHER_CRATE, "escrow_slot": slot,
                 "ok": True, "invoked": True, "status": "claimed"}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.game(CROSSROADS, native_loot=proof)))
        guid = ITEM["guid"]
        with self.assertRaisesRegex(party.RuleError, "native_loot_item_ambiguous"):
            await native_loot.perform(host, bridge, "host", "claim", guid, OPERATION)
        with self.assertRaisesRegex(party.RuleError, "native_loot_requires_exact_crate_and_operation"):
            await native_loot.perform(host, bridge, "host", "claim", f"{guid}@{BRIDGE_CRATE}", OPERATION)
        bridge.command.assert_not_awaited()

        result = await native_loot.perform(host, bridge, "host", "claim", f"{guid}@{OTHER_CRATE}", OPERATION)
        self.assertEqual(result["native_loot_crate"], OTHER_CRATE)
        self.assertNotIn("claimed_by", host.party[native_loot.STATE_KEY][LEGACY_CRATE]["items"][0])
        self.assertEqual(await native_loot.perform(host, bridge, "host", "claim", guid, OPERATION), result)
        with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
            await native_loot.perform(host, bridge, "host", "claim", f"{guid}@{LEGACY_CRATE}", OPERATION)
        bridge.command.assert_awaited_once_with("native-loot-claim", PAWN, OTHER_CRATE, guid, slot, timeout=6)

    def test_escrow_merge_frames_bind_distinct_exact_guids_and_quantities(self):
        guid, destination, slot = ITEM["guid"], "B" * 32, native_host_save.new_slot()
        base = f"schema\t1\nsession\thost\thosting\t{CROSSROADS}\t0\t1\n"
        crate = f"native-loot\tclaim\t{CROSSROADS}\t{OTHER_CRATE}\t{PAWN}\t1\tmerged\t0\t0\t-\t0\t100\t0\t1\t{slot}\n"
        stock = f"{native_stock_enemy_drops.CLAIMED}\t{CROSSROADS}\t{PAWN}\t{slot}\t{guid}\tmerged"
        shells = f"native-enemy-loot-escrow\t{CROSSROADS}\tmerged\t{PAWN}\t-\t{slot}\t{guid}\t-\t-\t-\t-"
        cases = (
            (f"{crate}{native_loot.STACK_MERGE_TAG}\t{guid}\t{destination}\t4\t7\t11", "native_loot"),
            (f"{stock}\t4\t7\t11\t{destination}", native_stock_enemy_drops.CLAIM_KEY),
            (f"{shells}\t{destination}\t4\t7\t11", "native_enemy_loot_escrow"),
        )
        status = self.root / "status.tsv"
        for frame, key in cases:
            with self.subTest(key=key):
                status.write_text(base + frame + "\n", encoding="utf-8")
                proof = runtime.Bridge(self.root, None).snapshot()[key]["merge"]
                self.assertEqual(proof, dict(guid=guid, inventory_guid=destination, amount=4, before=7, after=11))
                for invalid in (frame.replace(destination, guid), frame.replace("\t11", "\t12")):
                    status.write_text(base + invalid + "\n", encoding="utf-8")
                    self.assertNotIn(key, runtime.Bridge(self.root, None).snapshot() or {})

    async def test_stock_crate_merge_commits_typed_inventory_and_replays_source_claim(self):
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        item = roll["items"][0]
        item["ammo"] = 4
        host = self.host(CROSSROADS, {OTHER_CRATE: roll})
        destination = "B" * 32
        proof = {"action": "claim", "map": CROSSROADS, "pawn": PAWN, "crate": OTHER_CRATE,
                 "escrow_slot": roll["escrow"]["slot"], "ok": True, "invoked": True, "status": "merged",
                 "merge": dict(guid=item["guid"], inventory_guid=destination, amount=4, before=7, after=11)}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.game(CROSSROADS, native_loot=proof)))
        arguments = (host, bridge, "host", "claim", f"{item['guid']}@{OTHER_CRATE}")
        result = await native_loot.perform(*arguments, OPERATION)
        self.assertEqual(result["native_loot_claim"],
                         dict(item, claimed_by="host", inventory_guid=destination,
                              stack_merge=dict(amount=4, before=7, after=11)))
        row, = host.party[native_loot.STATE_KEY][OTHER_CRATE]["items"]
        self.assertEqual(row, dict(item, claimed_by="host"))
        host.party = party.load_party(host.party)
        self.assertEqual(await native_loot.perform(*arguments, OPERATION), result)
        with self.assertRaisesRegex(party.RuleError, "native_loot_already_claimed"):
            await native_loot.perform(*arguments, "2" * 32)
        bridge.command.assert_awaited_once()
        host.checkpoint_native.assert_awaited_once()
        self.assertTrue(host.checkpoint_native.await_args.kwargs["typed_inventory"])

    async def test_stock_host_pickup_accepts_and_replays_without_a_save(self):
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        item = roll["items"][0]
        item["ammo"] = 4
        host = self.host(CROSSROADS, {OTHER_CRATE: roll})
        proof = {"action": "claim", "map": CROSSROADS, "pawn": PAWN, "crate": OTHER_CRATE,
                 "escrow_slot": roll["escrow"]["slot"], "ok": True, "invoked": True, "status": "merged",
                 "merge": dict(guid=item["guid"], inventory_guid="B" * 32, amount=4, before=7, after=11)}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            LocalCommandNotSubmitted("expired before claim"), self.game(CROSSROADS, native_loot=proof),
        ]))
        args = (host, bridge, "host", "claim", f"{item['guid']}@{OTHER_CRATE}")
        original = host.party
        unaffected = self.roll(CROSSROADS, LEGACY_CRATE, "2" * 32)
        original[native_loot.STATE_KEY][LEGACY_CRATE] = unaffected
        with self.assertRaisesRegex(party.RuleError, "^native_loot_bridge_busy$"):
            await native_loot.perform(*args, OPERATION, defer_save=True)
        self.assertIs(host.party, original)
        self.assertFalse(host.stopping or host.native_checkpoint_unsafe)
        host.control.event.assert_any_call("native_loot_command_not_submitted", action=native_loot.CLAIM,
                                           operation_id=OPERATION, reason="expired before claim")
        result = await native_loot.perform(*args, OPERATION, defer_save=True)
        self.assertNotIn("claimed_by", original[native_loot.STATE_KEY][OTHER_CRATE]["items"][0])
        self.assertNotIn("host", original["receipts"])
        self.assertIs(host.party[native_loot.STATE_KEY][LEGACY_CRATE], unaffected)
        self.assertIs(host.party["players"], original["players"])
        self.assertTrue(host.pending_host_save)
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertEqual(result["native_loot_claim"]["claimed_by"], "host")
        self.assertEqual(await native_loot.perform(*args, OPERATION, defer_save=True), result)
        with self.assertRaisesRegex(party.RuleError, "native_loot_already_claimed"):
            await native_loot.perform(*args, "2" * 32, defer_save=True)
        host.checkpoint_native.assert_not_awaited()
        self.assertEqual(bridge.command.await_count, 2)
        self.assertEqual(bridge.command.await_args_list[0], bridge.command.await_args_list[1])

    async def test_compact_guest_claim_preserves_world_cache_and_queues_exact_retained_pawn(self):
        guest, pawn = "guest", "exact guest pawn"
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        item = roll["items"][0]
        item["ammo"] = 4
        host = self.host(CROSSROADS, {OTHER_CRATE: roll})
        party.add_player(host.party, guest)
        host.native_recipients = lambda: {guest: {"pawn": pawn, "connected": True}}
        host.pending_guest_saves = {}
        host.queue_guest_save = Host.queue_guest_save.__get__(host)
        host.last_game["native_guest_save_retention"] = True
        previous = host.last_game
        bridge = runtime.Bridge(self.root, None)

        async def reply(payload, timeout):
            ticket, action, claimant, crate, guid, slot, retention = payload.decode().strip().split("\t")
            self.assertEqual((action, claimant, crate, guid, slot, retention),
                             (native_loot.CLAIM, pawn, OTHER_CRATE, item["guid"], roll["escrow"]["slot"], "defer-save"))
            return (native_loot.CLAIM_REPLY_HEADER + f"schema\t1\nseq\t9\nsession\thost\thosting\t{CROSSROADS}\t1\t0\n"
                    f"ack\t{ticket}\taccepted\tclaim\n"
                    f"native-loot\tclaim\t{CROSSROADS}\t{crate}\t{pawn}\t1\tmerged\t0\t0\t-\t0\t100\t0\t1\t{slot}\t1\t0\n"
                    f"native-loot-stack-merge\t{guid}\t{'B' * 32}\t4\t7\t11\n"
                    f"pawn\tguest\t{pawn}\t90\t11\t22\t33\tcontroller\t100\n")

        bridge.local_bridge = SimpleNamespace(command=AsyncMock(side_effect=reply))
        result = await native_loot.perform(host, bridge, guest, "claim", f"{item['guid']}@{OTHER_CRATE}", OPERATION,
                                           defer_save=True)
        self.assertEqual(result["native_loot_claim"]["stack_merge"], dict(amount=4, before=7, after=11))
        self.assertEqual(host.pending_guest_saves, {guest: (pawn, dict(map=CROSSROADS, location=[11, 22, 33], health=90))})
        self.assertIs(host.last_game, previous)
        self.assertIsNone(bridge._local_status)
        host.checkpoint_native.assert_not_awaited()

    async def test_unverified_stock_crate_merge_fences_checkpoint_and_leaves_the_item_unclaimed(self):
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        item = roll["items"][0]
        item["ammo"] = 4
        host = self.host(CROSSROADS, {OTHER_CRATE: roll})
        proof = {"action": "claim", "map": CROSSROADS, "pawn": PAWN, "crate": OTHER_CRATE,
                 "escrow_slot": roll["escrow"]["slot"], "ok": True, "invoked": True, "status": "merged",
                 "merge": dict(guid=item["guid"], inventory_guid="B" * 32, amount=5, before=7, after=12)}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.game(CROSSROADS, native_loot=proof)))
        with self.assertRaisesRegex(RuntimeError, "stack merge did not verify"):
            await native_loot.perform(host, bridge, "host", "claim", f"{item['guid']}@{OTHER_CRATE}", OPERATION)
        host.checkpoint_native.assert_not_awaited()
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
        row, = host.party[native_loot.STATE_KEY][OTHER_CRATE]["items"]
        self.assertNotIn("claimed_by", row)

    async def test_stock_enemy_claim_consumes_pending_escrow_without_saving(self):
        enemy = native_stock_enemy_drops
        host = self.host(CROSSROADS)
        frame = {"slot": native_host_save.new_slot(), "map": CROSSROADS, "pending": True}
        item = dict(ITEM, map=CROSSROADS, claimed_by=None, ammo=1, settled=True, escrow=frame,
                    transform=dict(position=[1, 2, 3], rotation=[0, 0, 0], scale=[1, 1, 1]))
        guid = item["guid"]
        host.party[enemy.STATE] = {guid: item}
        host.last_game.update({enemy.MEMORY_CAPABILITY: True, "native_world_loot_version": 1})
        proof = dict(map=CROSSROADS, pawn=PAWN, slot=frame["slot"], guid=guid, status="claimed")
        refusals = ("stock_enemy_loot_claim_scope_unavailable", "native_loot_claimant_guid_conflict",
                    "native_loot_other_owner_stack_guid_conflict", "native_loot_escrow_shape_changed",
                    "native_loot_cash_payload_unqualified")
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            *(RuntimeError(reason) for reason in refusals),
            LocalCommandNotSubmitted("stock pickup not taken"),
            self.game(CROSSROADS, **{enemy.CLAIM_KEY: proof}),
        ]))

        with patch.object(native_host_save, "verify_record") as verify:
            for reason in refusals:
                with self.subTest(reason=reason):
                    with self.assertRaisesRegex(party.RuleError, reason):
                        await enemy.claim(host, bridge, "host", guid, OPERATION)
                    self.assertFalse(host.stopping)
                    self.assertFalse(host.native_checkpoint_unsafe)
                    self.assertEqual(host.party[enemy.STATE][guid], item)
                    self.assertEqual(host.party["receipts"], {})
                    host.control.event.assert_any_call("native_stock_enemy_loot_claim_refused", operation_id=OPERATION,
                                                       guid=guid, reason=reason)
            before = host.party
            with self.assertRaisesRegex(party.RuleError, "^native_loot_bridge_busy$"):
                await enemy.claim(host, bridge, "host", guid, OPERATION)
            self.assertIs(host.party, before)
            self.assertFalse(host.stopping or host.native_checkpoint_unsafe)
            with patch.object(party, "load_party", side_effect=AssertionError("pending claim copied the whole campaign")):
                result = await enemy.claim(host, bridge, "host", guid, OPERATION)
            verify.assert_not_called()
        self.assertIs(host.party["players"], before["players"])
        self.assertIs(before[enemy.STATE][guid], item)
        self.assertIsNone(item["claimed_by"])
        self.assertEqual(result[enemy.CLAIM_KEY]["claimed_by"], "host")
        self.assertNotIn("escrow", host.party[enemy.STATE][guid])
        self.assertIsNotNone(host.pending_host_save)
        host.checkpoint_native.assert_not_awaited()

    async def test_stock_enemy_claim_keeps_unverified_stock_mutations_unsafe(self):
        enemy = native_stock_enemy_drops
        frame = {"slot": native_host_save.new_slot(), "map": CROSSROADS, "pending": True}
        item = dict(ITEM, map=CROSSROADS, claimed_by=None, ammo=1, settled=True, escrow=frame,
                    transform=dict(position=[1, 2, 3], rotation=[0, 0, 0], scale=[1, 1, 1]))
        guid = item["guid"]
        for reason in ("native_loot_claim_payload_or_owner_unverified", "mutation_unverified_native_loot_stack_readback"):
            with self.subTest(reason=reason):
                host = self.host(CROSSROADS)
                host.party[enemy.STATE] = {guid: item}
                host.last_game.update({enemy.MEMORY_CAPABILITY: True, "native_world_loot_version": 1})
                bridge = SimpleNamespace(command=AsyncMock(side_effect=RuntimeError(reason)))
                with self.assertRaisesRegex(RuntimeError, reason):
                    await enemy.claim(host, bridge, "host", guid, OPERATION)
                self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
                self.assertEqual(host.party[enemy.STATE][guid], item)
                host.checkpoint_native.assert_not_awaited()

    async def test_stock_cash_credits_wallet_once_and_persists_the_exact_receipt(self):
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        item = roll["items"][0]
        item["class_path"] = next(iter(native_loot.CASH_RANGES))
        host = self.host(CROSSROADS, {OTHER_CRATE: roll})
        proof = {"action": "claim", "map": CROSSROADS, "pawn": PAWN, "crate": OTHER_CRATE,
                 "escrow_slot": roll["escrow"]["slot"], "ok": True, "invoked": True, "status": "cash_credited",
                 "cash": {"guid": item["guid"], "amount": 8, "before": 23, "after": 31}}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.game(CROSSROADS, native_loot=proof, native_wallet=31)))
        arguments = (host, bridge, "host", "claim", f"{item['guid']}@{OTHER_CRATE}")
        result = await native_loot.perform(*arguments, OPERATION)
        self.assertEqual(result["native_loot_claim"]["wallet_credit"], {"amount": 8, "before": 23, "after": 31})
        self.assertEqual(host.party["economy"]["wallet"], 31)
        self.assertEqual(host.party["players"]["host"]["inventory"], {})
        self.assertEqual(host.party[native_loot.STATE_KEY][OTHER_CRATE]["items"][0], result["native_loot_claim"])
        self.assertEqual(await native_loot.perform(*arguments, OPERATION), result)
        with self.assertRaisesRegex(party.RuleError, "native_loot_already_claimed"):
            await native_loot.perform(*arguments, "2" * 32)
        bridge.command.assert_awaited_once()
        host.checkpoint_native.assert_awaited_once()
        self.assertFalse(host.native_checkpoint_unsafe)

    async def test_unverified_stock_cash_fences_checkpoint_and_never_falls_back_to_inventory(self):
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        item = roll["items"][0]
        item["class_path"] = next(iter(native_loot.CASH_RANGES))
        native = {"action": "claim", "map": CROSSROADS, "pawn": PAWN, "crate": OTHER_CRATE,
                  "escrow_slot": roll["escrow"]["slot"], "ok": True, "invoked": True}
        good = {"guid": item["guid"], "amount": 8, "before": 23, "after": 31}
        cases = [
            (dict(status="claimed"), 31),
            (dict(status="cash_credited", cash=good | {"guid": "B" * 32}), 31),
            (dict(status="cash_credited", cash=good | {"amount": 30, "after": 53}), 53),
            (dict(status="cash_credited", cash=good | {"before": 24}), 31),
            (dict(status="cash_credited", cash=good), 32),
        ]
        for proof, balance in cases:
            with self.subTest(proof=proof, balance=balance):
                host = self.host(CROSSROADS, {OTHER_CRATE: roll})
                snapshot = self.game(CROSSROADS, native_loot=native | proof, native_wallet=balance)
                bridge = SimpleNamespace(command=AsyncMock(return_value=snapshot))
                with self.assertRaisesRegex(RuntimeError, "cash credit did not verify"):
                    await native_loot.perform(host, bridge, "host", "claim", f"{item['guid']}@{OTHER_CRATE}", OPERATION)
                self.assertTrue(host.stopping)
                self.assertTrue(host.native_checkpoint_unsafe)
                host.checkpoint_native.assert_not_awaited()
                self.assertNotIn("claimed_by", item)

    async def test_exact_local_crate_diagnostic_stays_out_of_the_resident_snapshot_cache(self):
        bridge = runtime.Bridge(self.root, None)
        bridge.local_bridge = Mock(command=AsyncMock())
        crate = OTHER_CRATE
        for role, phase, server in (("host", "hosting", 0), ("client", "connected", 1)):
            with self.subTest(phase=phase):
                status = (f"session\t{role}\t{phase}\t{CROSSROADS}\t1\t{server}\n"
                          f"pawn\tlocal\t{PAWN}\t250\t0\t0\t0\tcontroller\t250\n"
                          f"native-loot-crate-input\t{CROSSROADS}\t{crate}\texhausted\n")
                bridge.local_bridge.read_status.return_value = "schema\t1\nseq\t40\n" + status

                async def reply(payload, timeout):
                    request_id, action, area, pawn, actor = payload.decode().strip().split("\t")
                    self.assertEqual((action, area, pawn, actor, timeout),
                                     (native_loot.CRATE_DIAGNOSTIC, CROSSROADS, PAWN, crate, 2))
                    return (f"schema\t1\nseq\t41\nack\t{request_id}\taccepted\tcrate\n" + status
                            + f"{action}\t{area}\t{actor}\t{phase}\tactive\tlive\tlive\t1\t0\t-95\n")

                bridge.local_bridge.command.side_effect = reply
                result = await native_loot.crate_diagnostic(bridge, bridge.snapshot(), crate)
                self.assertEqual(result, {"native_loot_crate_diagnostic": {
                    "map": CROSSROADS, "crate": crate, "phase": phase, "scope": "active", "door": "live",
                    "box": "live", "looted": True, "collision": 0, "roll": -95,
                }})
                ordinary = bridge.snapshot()
                self.assertEqual(ordinary["sequence"], 40)
                self.assertNotIn(native_loot.CRATE_DIAGNOSTICS, ordinary)
                self.assertEqual(ordinary["native_loot_crates"][crate]["state"], "exhausted")
                self.assertIsNone(bridge._local_status)

    async def test_crate_diagnostic_rejects_absent_proofs_and_changed_local_scope(self):
        game = self.game(CROSSROADS, state="connected")
        bridge = Mock(command=AsyncMock())
        for target in (None, BRIDGE_CRATE, "unknown crate"):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "exact authored crate"):
                await native_loot.crate_diagnostic(bridge, game, target)
        bridge.command.assert_not_awaited()

        diagnostic = {"phase": "connected", "map": CROSSROADS, "crate": OTHER_CRATE}
        scoped = dict(game, **{native_loot.CRATE_DIAGNOSTICS: {OTHER_CRATE: diagnostic}})
        for wrong in (
            game, dict(scoped, pawns={"local": {"name": "old pawn"}}), dict(scoped, map=DEAD_BRIDGE),
            dict(scoped, state="hosting"),
            dict(game, **{native_loot.CRATE_DIAGNOSTICS: {OTHER_CRATE: dict(diagnostic, crate=LEGACY_CRATE)}}),
        ):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(RuntimeError, "verify its scope"):
                bridge.command.return_value = wrong
                await native_loot.crate_diagnostic(bridge, game, OTHER_CRATE)

    def test_cash_snapshot_requires_a_single_claim_row(self):
        lines = [
            ("schema", "1"), ("session", "host", "hosting", CROSSROADS, "0", "1"),
            ("native-loot", "claim", CROSSROADS, OTHER_CRATE, PAWN, "1", "cash_credited",
             "0", "0", "", "0", "90", "0", "1", "SFCoop_" + "a" * 32),
            ("native-loot-cash", ITEM["guid"], "8", "23", "31"), ("wallet", "31"),
        ]
        status = self.root / "status.tsv"
        def snapshot(rows):
            status.write_text("".join("\t".join(fields) + "\n" for fields in rows), encoding="utf-8")
            return runtime.Bridge(self.root, None).snapshot()
        sample = snapshot(lines)
        self.assertEqual(sample["native_wallet"], 31)
        self.assertEqual(sample["native_loot"]["cash"], {"guid": ITEM["guid"], "amount": 8, "before": 23, "after": 31})
        self.assertIsNone(snapshot([*lines, lines[3]]))

    async def test_restart_projects_all_committed_local_crates_and_no_other_map(self):
        states = {crate: {"map": area, "crate": crate, "state": "ready"}
                  for crate, area in native_loot.CRATES.items()}
        game = self.game(CROSSROADS, native_loot_crates=states)
        rolls = {crate: self.roll(area, crate, f"{index:032x}")
                 for index, (crate, area) in enumerate(native_loot.CRATES.items(), start=1)}
        lids = {crate: (lid[0], (lid[1] + lid[2]) / 2) for crate in rolls if (lid := random_lid(crate))}
        for crate, (field, angle) in lids.items():
            rolls[crate][field] = angle
            states[crate].update(state="exhausted", **{field: angle + 1})

        async def project(_verb, status, crate, *pose, timeout):
            states[crate] = dict(states[crate], state=status)
            if pose:
                states[crate][lids[crate][0]] = float(pose[0])
            return self.game(CROSSROADS, native_loot_crates=dict(states))

        bridge = SimpleNamespace(command=AsyncMock(side_effect=project))
        final = await native_loot.project_opened(bridge, game, rolls)
        local_crates = [crate for crate, area in native_loot.CRATES.items() if area == CROSSROADS]
        self.assertEqual(bridge.command.await_args_list, [
            call("native-loot-crate-project", "exhausted", crate, *((str(lids[crate][1]),) if crate in lids else ()), timeout=4)
            for crate in local_crates
        ])
        self.assertTrue(all(final["native_loot_crates"][crate]["state"] == "exhausted" for crate in local_crates))
        self.assertTrue(all(final["native_loot_crates"][crate][lids[crate][0]] == lids[crate][1]
                            for crate in local_crates if crate in lids))
        self.assertEqual(final["native_loot_crates"][BRIDGE_CRATE]["state"], "ready")

    def crate_guest(self, count):
        crates = [crate for crate, area in native_loot.CRATES.items() if area == CROSSROADS][:count]
        rolls, observed = {}, {}
        for index, crate in enumerate(crates, start=1):
            rolls[crate] = {"id": f"{index:032x}", "map": CROSSROADS, "crate": crate}
            if lid := random_lid(crate):
                rolls[crate][lid[0]] = (lid[1] + lid[2]) / 2
            observed[crate] = {"map": CROSSROADS, "crate": crate, "state": "ready"}
        client = SimpleNamespace(
            stopping=False, needs_travel=False, server_transitioning=False, host_session="a" * 32,
            zone_epoch="b" * 32, expected_zone=CROSSROADS, transport=SimpleNamespace(game_access=True),
            last_game=self.game(CROSSROADS, state="connected", native_loot_crates=dict(observed)),
            gameplay={native_loot.STATE_KEY: rolls}, gameplay_seen=time.monotonic(), control=Mock(),
        )
        return client, crates, observed

    async def test_guest_lid_restoration_yields_between_bounded_passes_and_finishes(self):
        client, crates, observed = self.crate_guest(5)
        local_crate = crates[3]
        expected = [local_crate, *crates[:3], crates[4]]
        client.last_game["native_loot_crate_interactions"] = [
            {"map": CROSSROADS, "crate": local_crate, "pawn": PAWN, "sequence": 7, "entry": "stock"},
        ]
        called, presses = [], []

        async def project(action, status, crate, *pose, timeout, update_status):
            self.assertEqual((action, status), ("native-loot-crate-project", "exhausted"))
            self.assertGreater(timeout, 0)
            called.append(crate)
            if crate == local_crate:
                presses.append("F")
            observed[crate] = dict(observed[crate], state=status)
            if lid := random_lid(crate):
                observed[crate][lid[0]] = float(pose[0])
            return dict(client.last_game, native_loot_crates=dict(observed))

        client.bridge = SimpleNamespace(command=AsyncMock(side_effect=project))
        view = native_loot.GuestCrateProjection()
        await view.sync(client)
        self.assertEqual(called, expected[:2])
        self.assertEqual(presses.pop(0), "F")
        await view.sync(client)
        self.assertEqual(called, expected[:4])
        await view.sync(client)
        await view.sync(client)
        self.assertEqual(called, expected)
        self.assertTrue(all(row["state"] == "exhausted" for row in client.last_game["native_loot_crates"].values()))

    async def test_slow_guest_lid_yields_and_a_ready_sibling_goes_first_next_turn(self):
        client, crates, observed = self.crate_guest(3)
        cancelled = asyncio.Event()

        async def project(_action, status, crate, *pose, timeout, update_status):
            if crate == crates[0]:
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            if lid := random_lid(crate):
                observed[crate][lid[0]] = float(pose[0])
            observed[crate] = dict(observed[crate], state=status)
            return dict(client.last_game, native_loot_crates=dict(observed))

        client.bridge = SimpleNamespace(command=AsyncMock(side_effect=project))
        view = native_loot.GuestCrateProjection()
        view.turn_seconds = .025
        await asyncio.wait_for(view.sync(client), timeout=1)
        self.assertTrue(cancelled.is_set())
        self.assertEqual([request.args[2] for request in client.bridge.command.await_args_list], crates[:1])
        view.turn_seconds = .150
        await view.sync(client)
        self.assertEqual([request.args[2] for request in client.bridge.command.await_args_list], crates)
        client.control.event.assert_called_once_with(
            "native_loot_guest_lid_deferred", crate=crates[0], reason="stock lid readback timed out")

    async def test_guest_refusals_coalesce_and_missing_stock_is_tried_when_it_arrives(self):
        client, (refused, sibling, arriving), observed = self.crate_guest(3)
        observed.pop(arriving)
        client.last_game["native_loot_crates"] = dict(observed)
        attempts = []

        async def project(_action, status, crate, *pose, timeout, update_status):
            attempts.append(crate)
            if crate == refused and attempts.count(crate) <= 2:
                if attempts.count(crate) == 1:
                    raise RuntimeError("stock binding unavailable")
                return dict(client.last_game, native_loot_crates=dict(observed))
            if lid := random_lid(crate):
                observed[crate][lid[0]] = float(pose[0])
            observed[crate] = dict(observed[crate], state=status)
            return dict(client.last_game, native_loot_crates=dict(observed))

        client.bridge = SimpleNamespace(command=AsyncMock(side_effect=project))
        view = native_loot.GuestCrateProjection()
        now = client.gameplay_seen
        with patch.object(native_loot, "time", SimpleNamespace(monotonic=lambda: now)):
            await view.sync(client)
            self.assertEqual(attempts, [refused, sibling])
            self.assertEqual(client.last_game["native_loot_crates"][sibling]["state"], "exhausted")
            await view.sync(client)
            self.assertEqual(attempts, [refused, sibling])
            observed[arriving] = {"map": CROSSROADS, "crate": arriving, "state": "ready"}
            client.last_game["native_loot_crates"] = dict(observed)
            await view.sync(client)
            self.assertEqual(attempts, [refused, sibling, arriving])
            now += view.retry_seconds + .01
            await view.sync(client)
            self.assertEqual(client.last_game["native_loot_crates"][refused]["state"], "ready")
            self.assertIn("did not reflect", client.control.event.call_args.kwargs["reason"])
            now += view.retry_seconds + .01
            await view.sync(client)
            self.assertEqual(attempts, [refused, sibling, arriving, refused, refused])
            self.assertEqual(client.last_game["native_loot_crates"][refused]["state"], "exhausted")

    async def test_guest_lid_visit_reset_retries_and_rejects_readback_from_another_pawn(self):
        client, (first, sibling), observed = self.crate_guest(2)
        bridge = client.bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            RuntimeError("stock lid declined"), RuntimeError("stock lid declined"),
            dict(client.last_game, pawns={"local": {"name": "different native pawn"}},
                 native_loot_crates={first: dict(observed[first], state="exhausted")}),
        ]))
        view = native_loot.GuestCrateProjection()
        await view.sync(client)
        await view.sync(client)
        self.assertEqual(bridge.command.await_count, 2)
        self.assertEqual(client.control.event.call_count, 2)
        client.zone_epoch = "c" * 32
        await view.sync(client)
        self.assertEqual([request.args[2] for request in bridge.command.await_args_list], [first, sibling, first])
        self.assertEqual(client.last_game["pawns"]["local"]["name"], PAWN)
        self.assertIsNone(view.scope)
        self.assertEqual(view.deferred, {})

    async def test_immediate_lid_only_projects_the_new_roll_and_discards_a_previous_visit_reply(self):
        client, (waiting, confirmed), observed = self.crate_guest(2)
        previous_game = client.last_game
        client.gameplay_seen -= 4

        async def project(_action, status, crate, *pose, timeout, update_status):
            self.assertEqual(crate, confirmed)
            self.assertFalse(update_status)
            client.zone_epoch = "c" * 32
            client.last_game = dict(previous_game, sequence=11)
            changed = dict(observed[confirmed], state=status)
            if lid := random_lid(crate):
                changed[lid[0]] = float(pose[0])
            return dict(previous_game, sequence=12, native_loot_crates={confirmed: changed})

        client.bridge = SimpleNamespace(command=AsyncMock(side_effect=project))
        view = native_loot.GuestCrateProjection()
        await view.sync(client, confirmed_crate=confirmed)
        self.assertEqual(client.bridge.command.await_count, 1)
        self.assertEqual(client.last_game["sequence"], 11)
        self.assertEqual(client.last_game["native_loot_crates"][waiting]["state"], "ready")
        self.assertIsNone(view.scope)

    async def test_vehicle_lock_projection_applies_host_state_to_the_exact_active_vehicle(self):
        car = full_name(CROSSROADS, "StaticMeshActor_488", "Loot_Car_01_BP_C")
        van = full_name(CROSSROADS, "StaticMeshActor_1937", "Loot_Car_02_BP_C")
        key = native_loot.VEHICLE_LOCKS
        observed = {car: True, van: False, OTHER_CRATE: False}
        game = {"state": "connected", "map": CROSSROADS, key: observed}
        projected = {**game, key: {**observed, car: False}}
        bridge = SimpleNamespace(command=AsyncMock(return_value=projected))
        host = {car: False, van: False, OTHER_CRATE: True}

        self.assertEqual(await native_loot.project_vehicle_locks(bridge, game, host), projected)
        bridge.command.assert_awaited_once_with("native-loot-vehicle-locks", CROSSROADS, car, "0", timeout=4)
        bridge.command.return_value = game
        with self.assertRaisesRegex(RuntimeError, "container locks did not match"):
            await native_loot.project_vehicle_locks(bridge, game, host)
