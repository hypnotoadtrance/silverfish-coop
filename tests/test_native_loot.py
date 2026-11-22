import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from tools.silverfish import native_host_save, native_loose_pickup, native_loot, native_stock_enemy_drops, party, runtime
from tools.silverfish.authored_loot import (CRATES, LEGACY_CRATE, OFFICE_DESK, PLACEMENTS, OVERRIDDEN_PLACEMENTS,
                                           SMALL_CABINET, full_name, loot_capacity, random_lid)
from tools.silverfish.campaign import CampaignFormatError
from tools.silverfish.generate_authored_loot_registry import outputs, survey
from tools.silverfish.travel import CISTERN, CITY, CROSSROADS, DEAD_BRIDGE, LAB_E206, LAKE, OBSERVATORY, RAIL_YARD


OTHER_CRATE = full_name(CROSSROADS, "LootCrate_Small_BP_C_9")
BRIDGE_CRATE = full_name(DEAD_BRIDGE, "LootCrate_Small_BP_C_1")
PAWN = "exact local pawn"
ITEM = {"actor": "stock Bandage", "class_path": "/Game/Classes/Items/Item_Bandage.Item_Bandage_C", "guid": "A" * 32}
OPERATION = "f" * 32


class AuthoredCrateRegistryTests(unittest.TestCase):
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
    async def test_transient_award_requires_typed_checkpoint_without_escrow_io(self):
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
                                       loose_pickups=loose.Visibility())
                with patch.object(native_host_save, "new_slot", return_value=slot), patch.object(native_host_save, "record") as saved:
                    result = await loose.claim(host, bridge, "host", f"loose:{path}", OPERATION)
                self.assertEqual(result[loose.PROOF]["guid"], ITEM["guid"])
                self.assertEqual(host.checkpoint_native.await_args.kwargs["typed_inventory"], bool(mode))
                self.assertEqual(saved.call_count, int(not mode))
                self.assertFalse(host.native_checkpoint_unsafe)

    async def test_placed_stack_merge_keeps_source_and_checkpoints_exact_recipient(self):
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
                               loose_pickups=loose.Visibility())
        with patch.object(native_host_save, "new_slot", return_value=slot), patch.object(native_host_save, "record") as saved:
            result = await loose.claim(host, bridge, "host", f"loose:{path}", OPERATION)
        row = result[loose.PROOF]
        self.assertEqual((row["actor"], row["guid"], row["inventory_guid"]), (path, ITEM["guid"], destination))
        self.assertEqual(row["stack_merge"], {"amount": 2, "before": 4, "after": 6})
        loose.validate_records(host.party, {})
        self.assertIn(path, host.loose_pickups.hidden(host))
        self.assertEqual(await loose.claim(host, bridge, "host", f"loose:{path}", OPERATION), result)
        bridge.command.assert_awaited_once()
        self.assertTrue(host.checkpoint_native.await_args.kwargs["typed_inventory"])
        saved.assert_not_called()

    async def test_temporary_item_reappears_only_after_stock_item_returns(self):
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
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            dict(game, **{loose.CHECK_KEY: {temporary: False}}),
            dict(game, **{loose.CHECK_KEY: {temporary: True}}),
        ]))
        await view.refresh(host, bridge)
        self.assertEqual(set(view.hidden(host)), {temporary, permanent})
        view.next_check = 0
        await view.refresh(host, bridge)
        self.assertEqual(view.hidden(host), [permanent])
        self.assertEqual(bridge.command.await_args_list, [call(loose.CHECK, temporary, timeout=4)] * 2)


    async def test_projection_restores_the_loaded_actor_after_the_host_releases_its_suppression(self):
        loose = native_loose_pickup
        path = f"{CROSSROADS}.CrossRoads_P:PersistentLevel.Item_Test_C_1"
        game = {"state": "connected", "map": CROSSROADS, loose.CAPABILITY: 1}
        shown = dict(game, **{loose.KEY: {path: {"state": "available"}}})
        hidden = dict(game, **{loose.KEY: {path: {"state": "claimed"}}})
        client = SimpleNamespace(last_game=shown)
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[hidden, shown]))
        await loose.project(client, bridge, [path])
        self.assertEqual(client.last_game, hidden)
        await loose.project(client, bridge, None)
        self.assertEqual(client.last_game, hidden)
        await loose.project(client, bridge, [])
        self.assertEqual(client.last_game, shown)
        self.assertEqual(bridge.command.await_args_list,
                         [call(loose.PROJECT, "hide", path, timeout=4), call(loose.PROJECT, "show", path, timeout=4)])


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
                               checkpoint_native=AsyncMock(return_value=2), pending_native_loot_save=None)

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

    def test_publication_excludes_other_maps_and_invalid_saved_identity(self):
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        records = {
            LEGACY_CRATE: self.roll(CROSSROADS, OTHER_CRATE, "2" * 32),
            OTHER_CRATE: roll,
            BRIDGE_CRATE: self.roll(DEAD_BRIDGE, BRIDGE_CRATE, "3" * 32),
        }
        self.assertEqual(native_loot.published(records, CROSSROADS), {OTHER_CRATE: native_loot.public_roll(roll)})

    async def test_legacy_crate_uses_the_unoccupied_stock_slot_without_rerolling(self):
        claimed = {"position": [21., 20., 30.], "rotation": [0., 0., 0.], "scale": [1., 1., 1.]}
        restored = dict(claimed, position=[40., 40., 30.])
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        unclaimed = dict(ITEM, actor="another stock Bandage", guid="B" * 32)
        roll["items"] = [dict(ITEM, transform=claimed, claimed_by="host"), unclaimed]
        host = self.host(CROSSROADS, {OTHER_CRATE: roll})
        slots = {1: restored, 2: dict(claimed, position=[20., 20., 30.])}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.game(CROSSROADS, native_loot_placement={
            "map": CROSSROADS, "crate": OTHER_CRATE, "positions": slots})))
        await native_loot.restore_legacy_placement(host, bridge)
        expected = {**roll, "items": [roll["items"][0], dict(unclaimed, transform=restored)]}
        self.assertEqual(host.party[native_loot.STATE_KEY][OTHER_CRATE], expected)
        bridge.command.assert_awaited_once_with(native_loot.PLACEMENT, OTHER_CRATE, timeout=3)
        host.checkpoint_native.assert_awaited_once_with(bridge, host.party)
        await native_loot.restore_legacy_placement(host, bridge)
        bridge.command.assert_awaited_once()

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
        await native_loot.retire_replenished(host, bridge)
        self.assertEqual(host.party[native_loot.STATE_KEY], {LEGACY_CRATE: untouched})
        self.assertEqual(host.party[native_loot.RETIRED], {"1" * 32: OTHER_CRATE})
        host.checkpoint_native.assert_awaited_once_with(bridge, host.party)
        with self.assertRaisesRegex(party.RuleError, "roll_replenished"):
            await native_loot.perform(host, bridge, "host", "open", OTHER_CRATE, expired["id"])
        bridge.command.assert_not_awaited()
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
            ("native-loot-view-nearby", CROSSROADS, LEGACY_CRATE, PAWN, "25.5"),
            ("native-loot-view", PAWN, OPERATION, "claiming", "1", ITEM["guid"], LEGACY_CRATE),
        ]
        (self.root / "status.tsv").write_text("".join("\t".join(fields) + "\n" for fields in data), encoding="utf-8")
        snapshot = runtime.Bridge(self.root, None).snapshot()
        self.assertEqual({crate: row["state"] for crate, row in snapshot["native_loot_crates"].items()},
                         {LEGACY_CRATE: "exhausted", OTHER_CRATE: "ready"})
        self.assertEqual(snapshot["native_loot_crate_input"], snapshot["native_loot_crates"][LEGACY_CRATE])
        self.assertEqual(snapshot["native_loot_view_candidates"][LEGACY_CRATE]["distance"], 25.5)
        self.assertEqual(snapshot["native_loot_view"]["crate"], LEGACY_CRATE)

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

        legacy_result = {"native_loot_claim": dict(ITEM, claimed_by="host")}
        host.party["receipts"]["host"].append({"id": "e" * 32, "request": ["native-loot-claim", guid, None],
                                               "result": legacy_result})
        self.assertEqual(await native_loot.perform(host, bridge, "host", "claim", guid, "e" * 32), legacy_result)
        bridge.command.assert_awaited_once()

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
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.game(CROSSROADS, native_loot=proof)))
        args = (host, bridge, "host", "claim", f"{item['guid']}@{OTHER_CRATE}")
        result = await native_loot.perform(*args, OPERATION, defer_save=True)
        self.assertTrue(host.pending_native_loot_save)
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertEqual(result["native_loot_claim"]["claimed_by"], "host")
        self.assertEqual(await native_loot.perform(*args, OPERATION, defer_save=True), result)
        with self.assertRaisesRegex(party.RuleError, "native_loot_already_claimed"):
            await native_loot.perform(*args, "2" * 32, defer_save=True)
        host.checkpoint_native.assert_not_awaited()
        bridge.command.assert_awaited_once()

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

    async def test_view_claim_follows_rendered_crate_even_when_another_is_nearer(self):
        rolls = {crate: self.roll(CROSSROADS, crate, str(index) * 32)
                 for index, crate in enumerate((LEGACY_CRATE, OTHER_CRATE), start=1)}
        game = self.game(CROSSROADS, native_loot_view_candidates={
            crate: {"map": CROSSROADS, "crate": crate, "pawn": PAWN, "distance": distance}
            for crate, distance in ((LEGACY_CRATE, 5), (OTHER_CRATE, 30))
        })
        bridge = SimpleNamespace(directory=self.root, command=AsyncMock(return_value=game))
        view = native_loot.LootView(bridge, Mock(generation="test"))
        claim = AsyncMock(return_value={"native_loot_claim": ITEM, "native_loot_crate": OTHER_CRATE})
        await view.sync(game, rolls, claim)
        bridge.command.assert_awaited_once_with("native-loot-view-render", rolls[LEGACY_CRATE]["id"], PAWN, LEGACY_CRATE,
                                               f"{ITEM['guid']}|{ITEM['class_path']}", timeout=2)
        scope = rolls[OTHER_CRATE]["id"], PAWN, OTHER_CRATE
        shown = dict(id=scope[0], pawn=scope[1], crate=scope[2], state="ready", guid=ITEM["guid"])
        game["native_loot_view"] = shown
        events = self.root / "native-loot-view-events"
        events.write_text("\t".join(("crate-view", "1", *scope, ITEM["guid"])) + "\n", encoding="utf-8")
        await view.sync(game, rolls, claim)
        claim.assert_not_awaited()
        shown.update(state="claiming", sequence=1)
        await view.sync(game, rolls, claim)
        claim.assert_awaited_once()
        self.assertEqual(claim.await_args.args[1], f"{ITEM['guid']}@{OTHER_CRATE}")
