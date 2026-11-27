from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

from tools.silverfish import native_crate_outcome as transfer, native_loot, party
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
