from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

from tools.silverfish import native_crate_outcome as transfer, native_loot, party
from tools.silverfish.runtime import _read_crate_state, split_crate_input_outcomes
from tools.silverfish.travel import OBSERVATORY


class CrateOutcomeTests(unittest.IsolatedAsyncioTestCase):
    crate = next(name for name, area in native_loot.CRATES.items()
                 if area == OBSERVATORY and name.endswith(":PersistentLevel.LootCrate_Small_BP_C_1"))
    epoch = "e" * 32
    item = {"actor": f"Item_Bandage_C {OBSERVATORY}.Observitory_P:PersistentLevel.Item_Bandage_C_11",
            "class_path": "/Game/Classes/Items/Item_Bandage.Item_Bandage_C", "guid": "B" * 32, "ammo": 1,
            "transform": {"position": [12, 30, 50], "rotation": [0, 0, 0], "scale": [1, 1, 1]}}

    def setUp(self):
        self.host = SimpleNamespace(
            last_game={"state": "hosting", "map": OBSERVATORY}, zone_epoch=self.epoch,
            runtime_crate_sweep=(self.epoch, ()),
            native_loot_input=True, native_profile=object(), stopping=False, transitioning=False,
            native_checkpoint_unsafe=False, party=party.new_party(), control=MagicMock(),
            native_recipients=lambda: {"guest": {"connected": True, "pawn": "guest pawn"},
                                       "friend": {"connected": True, "pawn": "friend pawn"}},
        )
        party.add_player(self.host.party, "guest")
        party.add_player(self.host.party, "friend")
        self.bridge = SimpleNamespace(local_bridge=object(), command=AsyncMock(side_effect=self.adopt))
        self.outcome = {"lucky_looter": True, "trait_source": "permanent", "trait_calls": 0,
                        "items": [deepcopy(self.item)], "typed": ["(Ammo=1)"]}

    async def adopt(self, action, pawn, crate, slot, *rows, **options):
        self.assertEqual((action, crate, pawn), (transfer.ADOPT, self.crate, "guest pawn"))
        self.assertEqual(rows, (self.item["guid"], self.item["class_path"], "(Ammo=1)"))
        return {"map": OBSERVATORY, "state": "hosting", "crate": crate, "scope": slot, "message": "adopted"}

    async def test_first_report_wins_and_a_retry_does_not_import_twice(self):
        operation = "a" * 32
        body = transfer.report_body(self.epoch, self.crate, self.outcome)
        accepted = await transfer.accept(self.host, self.bridge, "guest", body, operation)
        roll = accepted["native_loot_roll"]
        self.assertEqual(roll["items"], [self.item])
        self.assertEqual(roll["opener"], "guest")
        self.assertNotIn("typed", roll)
        self.assertTrue(self.host.party[native_loot.STATE_KEY][self.crate]["escrow"]["pending"])
        self.assertEqual(await transfer.accept(self.host, self.bridge, "guest", body, operation), accepted)

        self.outcome["items"][0]["guid"] = "C" * 32
        contender = transfer.report_body(self.epoch, self.crate, self.outcome)
        self.assertEqual(await transfer.accept(self.host, self.bridge, "friend", contender, "c" * 32), accepted)
        with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
            await transfer.accept(self.host, self.bridge, "guest", contender, operation)
        self.bridge.command.assert_awaited_once()

    async def test_wrong_visit_and_ambiguous_items_do_not_touch_the_host_crate(self):
        body = transfer.report_body("f" * 32, self.crate, self.outcome)
        with self.assertRaisesRegex(party.RuleError, "scope_unavailable"):
            await transfer.accept(self.host, self.bridge, "guest", body, "a" * 32)
        self.outcome["items"] *= 2
        self.outcome["typed"] *= 2
        body = transfer.report_body(self.epoch, self.crate, self.outcome)
        with self.assertRaisesRegex(party.RuleError, "outcome_invalid"):
            await transfer.accept(self.host, self.bridge, "guest", body, "a" * 32)
        self.bridge.command.assert_not_awaited()

    def test_early_outcome_belongs_to_its_press_and_local_pawn(self):
        game = {"map": OBSERVATORY, "state": "connected", "pawns": {"local": {"name": "guest pawn"}}}
        press = f"loot-crate-open\t{OBSERVATORY}\t{self.crate}\tguest pawn\t7".encode()
        reply = (f"native-loot-outcome-reply\t1\t7\tconnected\t{OBSERVATORY}\t{self.crate}\t7\taccepted\trolled\n"
                 "loot-traits\t0\tnone\t0\n").encode()
        presses, outcomes = split_crate_input_outcomes(game, [(press + b"\n" + reply).rstrip(b"\n")])
        self.assertEqual(presses, [press])
        self.assertEqual(native_loot.OpenIntents().take(game, presses), [(7, self.crate, "stock", "memory_input")])
        self.assertEqual(outcomes[7, self.crate]["outcome"]["items"], [])
        for changed_press, changed_reply in (
            (press.replace(b"guest pawn", b"old pawn"), reply),
            (press, reply.replace(b"\t1\t7\t", b"\t1\t6\t")),
            (press, reply.replace(b"loot-traits", b"wrong-tag")),
        ):
            presses, outcomes = split_crate_input_outcomes(game, [changed_press + b"\n" + changed_reply])
            self.assertEqual(presses, [changed_press])
            self.assertEqual(outcomes, {})

    async def test_discovered_placement_participates_in_the_same_roll_ledger(self):
        crate = f"LootCrate_Small_BP_C {OBSERVATORY}.Observitory_P:PersistentLevel.NewShedCrate"
        self.assertNotIn(crate, native_loot.CRATES)
        game = {"state": "connected", "map": OBSERVATORY, "pawns": {"local": {"name": "guest pawn"}}}
        self.assertEqual(native_loot._opening_arguments(game, crate, None, None), ())
        _read_crate_state(game, ["native-loot-crate-state", crate, "ready", "0", "-", "-"])
        self.assertEqual(game["native_loot_crates"][crate]["state"], "ready")
        self.crate = crate
        self.outcome["trait_calls"] = 1
        body = transfer.report_body(self.epoch, crate, self.outcome)
        operation = "a" * 32
        accepted = await transfer.accept(self.host, self.bridge, "guest", body, operation)
        self.assertEqual(native_loot.published(self.host.party[native_loot.STATE_KEY], OBSERVATORY),
                         {crate: accepted["native_loot_roll"]})
        self.assertEqual(await transfer.accept(self.host, self.bridge, "guest", body, operation), accepted)
        self.bridge.command.assert_awaited_once()
        with self.assertRaisesRegex(party.RuleError, "outcome_invalid"):
            transfer.decode_report(body, "/Game/Mods/OtherMap")
        with self.assertRaisesRegex(ValueError, "Invalid stock crate state"):
            _read_crate_state(game, ["native-loot-crate-state", crate.replace("NewShedCrate", "Invalid@Crate"),
                                     "ready", "0", "-", "-"])

    def test_streamed_result_belongs_to_its_parent_map_and_exact_level(self):
        level = "/Game/Mods/CoopSublevel.Cistern_P:PersistentLevel."
        crate = f"LootCrate_Small_BP_C {OBSERVATORY}|{level}NewCrate"
        self.outcome["trait_calls"] = 1
        self.outcome["items"][0]["actor"] = f"Item_Bandage_C {level}Item_Bandage_C_11"
        body = transfer.report_body(self.epoch, crate, self.outcome)
        self.assertEqual(transfer.decode_report(body, OBSERVATORY)["crate"], crate)
        for source, observed in ((crate, "/Game/Mods/OtherMap"),
                                 (crate.replace(OBSERVATORY + "|", ""), OBSERVATORY)):
            with self.assertRaisesRegex(party.RuleError, "outcome_invalid"):
                transfer.decode_report(transfer.report_body(self.epoch, source, self.outcome), observed)
        self.outcome["items"][0]["actor"] = f"Item_Bandage_C {level.replace('CoopSublevel', 'OtherSublevel')}Item_Bandage_C_11"
        with self.assertRaisesRegex(party.RuleError, "outcome_invalid"):
            transfer.decode_report(transfer.report_body(self.epoch, crate, self.outcome), OBSERVATORY)

    async def test_inherited_container_carries_its_class_through_the_shared_ledger(self):
        source = f"{OBSERVATORY}.Observitory_P:PersistentLevel.SupplyCrate_1"
        variant = "/ExampleMod/Containers/SupplyCrate.SupplyCrate_C"
        self.crate = f"LootCrate_Small_BP_C {variant} {source}"
        self.assertEqual(native_loot.loot_capacity(self.crate), 2)
        operation = "a" * 32
        body = transfer.report_body(self.epoch, self.crate, self.outcome)
        accepted = await transfer.accept(self.host, self.bridge, "guest", body, operation)
        roll = accepted["native_loot_roll"]
        self.assertEqual(roll["crate"], self.crate)
        self.assertEqual(native_loot.published(self.host.party[native_loot.STATE_KEY], OBSERVATORY), {self.crate: roll})
        for invalid in (self.crate.replace(variant, variant.removesuffix("_C")),
                        self.crate.replace("LootCrate_Small_BP_C ", "LootLocker_Secure_BP_C ")):
            with self.assertRaisesRegex(party.RuleError, "outcome_invalid"):
                transfer.decode_report(transfer.report_body(self.epoch, invalid, self.outcome), OBSERVATORY)
        self.bridge.command.assert_awaited_once()

    async def test_runtime_container_uses_the_live_ledger_without_a_durable_escrow(self):
        actor_class = "/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C"
        self.crate = f"LootCrate_Small_BP_C {actor_class} runtime:{OBSERVATORY}:3"
        body = transfer.report_body(self.epoch, self.crate, self.outcome)
        accepted = await transfer.accept(self.host, self.bridge, "guest", body, "a" * 32)
        state = self.host.party
        self.assertEqual(native_loot.published(state[native_loot.STATE_KEY], OBSERVATORY), {
            self.crate: accepted["native_loot_roll"],
        })
        self.assertNotIn(self.crate, native_loot.checkpoint_party(state)[native_loot.STATE_KEY])
        self.assertEqual(native_loot.checkpoint_party(state)["receipts"]["guest"], [])
        self.assertIn(self.crate, state[native_loot.STATE_KEY])
        self.bridge.command.reset_mock()
        pending, saves = await native_loot.flush_pending_escrows(self.host, self.bridge, state)
        self.assertIs(pending, state)
        self.assertEqual(saves, {})
        self.bridge.command.assert_not_awaited()
        self.assertIsNone(native_loot.crate_area(self.crate.rsplit(":", 1)[0] + ":65536"))

    async def test_runtime_retirement_requires_destruction_or_a_new_visit(self):
        actor_class = "/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C"
        for serial, end_play in ((3, True), (4, False)):
            with self.subTest(end_play=end_play):
                self.crate = f"LootCrate_Small_BP_C {actor_class} runtime:{OBSERVATORY}:{serial}"
                operation = f"{serial:032x}"
                body = transfer.report_body(self.host.zone_epoch, self.crate, self.outcome)
                await transfer.accept(self.host, self.bridge, "guest", body, operation)
                state = self.host.party
                roll = state[native_loot.STATE_KEY][self.crate]
                claim = {"id": "c" * 32, "request": ["native-loot-claim", self.item["guid"], self.crate],
                         "result": {"native_loot_claim": self.item, "native_loot_crate": self.crate}}
                state["receipts"]["friend"] = [claim]
                self.bridge.command.reset_mock()
                await native_loot.retire_runtime_crates(self.host, self.bridge)
                self.bridge.command.assert_not_awaited()
                self.assertIs(self.host.party, state)
                if end_play:
                    self.host.last_game[native_loot.RUNTIME_ENDED] = {self.crate: True}
                    with self.assertRaisesRegex(party.RuleError, "runtime_crate_ended"):
                        await transfer.accept(self.host, self.bridge, "guest", body, operation)
                else:
                    self.host.zone_epoch = "f" * 32
                self.bridge.command.side_effect = None
                await native_loot.retire_runtime_crates(self.host, self.bridge)
                self.bridge.command.assert_awaited_once_with("native-loot-retire-runtime-escrows",
                                                             roll["escrow"]["slot"], update_status=False)
                self.assertNotIn(self.crate, self.host.party[native_loot.STATE_KEY])
                self.assertEqual(self.host.party["receipts"]["guest"], [])
                self.assertEqual(self.host.party["receipts"]["friend"], [claim])
                self.bridge.command.side_effect = self.adopt
