import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

from tools.silverfish import native_host_save, native_loot, party, runtime
from tools.silverfish.authored_loot import LEGACY_CRATE, full_name
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE


OTHER_CRATE = full_name(CROSSROADS, "LootCrate_Small_BP_C_9")
BRIDGE_CRATE = full_name(DEAD_BRIDGE, "LootCrate_Small_BP_C_1")
PAWN = "exact local pawn"
ITEM = {"actor": "stock Bandage", "class_path": "/Game/Classes/Items/Item_Bandage.Item_Bandage_C", "guid": "A" * 32}
OPERATION = "f" * 32


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
                               checkpoint_native=AsyncMock(return_value=2))

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

    def test_publication_excludes_other_maps_and_invalid_saved_identity(self):
        roll = self.roll(CROSSROADS, OTHER_CRATE, "1" * 32)
        records = {
            LEGACY_CRATE: self.roll(CROSSROADS, OTHER_CRATE, "2" * 32),
            OTHER_CRATE: roll,
            BRIDGE_CRATE: self.roll(DEAD_BRIDGE, BRIDGE_CRATE, "3" * 32),
        }
        self.assertEqual(native_loot.published(records, CROSSROADS), {OTHER_CRATE: native_loot.public_roll(roll)})
        self.assertEqual(native_loot.default_crate(CROSSROADS), LEGACY_CRATE)
        self.assertEqual(native_loot.default_crate(DEAD_BRIDGE), BRIDGE_CRATE)
        self.assertEqual(native_loot.fixture_selection("host", CROSSROADS), ("host", LEGACY_CRATE))
        self.assertEqual(native_loot.fixture_selection(f"guest2@{OTHER_CRATE}", CROSSROADS), ("guest2", OTHER_CRATE))

    def test_snapshot_reads_multiple_exact_crates_with_legacy_field(self):
        unsupported = full_name(CROSSROADS, "LootCrate_Small_BP_C_18")
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

    async def test_restart_projects_all_committed_local_crates_and_no_other_map(self):
        states = {crate: {"map": area, "crate": crate, "state": "ready"}
                  for crate, area in native_loot.CRATES.items()}
        game = self.game(CROSSROADS, native_loot_crates=states)
        rolls = {crate: self.roll(area, crate, str(index) * 32)
                 for index, (crate, area) in enumerate(native_loot.CRATES.items(), start=1)}

        async def project(_verb, status, crate, *, timeout):
            states[crate] = dict(states[crate], state=status)
            return self.game(CROSSROADS, native_loot_crates=dict(states))

        bridge = SimpleNamespace(command=AsyncMock(side_effect=project))
        final = await native_loot.project_opened(bridge, game, rolls)
        self.assertEqual(bridge.command.await_args_list, [
            call("native-loot-crate-project", "exhausted", LEGACY_CRATE, timeout=4),
            call("native-loot-crate-project", "exhausted", OTHER_CRATE, timeout=4),
        ])
        self.assertEqual(final["native_loot_crates"][BRIDGE_CRATE]["state"], "ready")

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
