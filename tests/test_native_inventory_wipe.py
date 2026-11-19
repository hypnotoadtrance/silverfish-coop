import json
import tempfile
import unittest
import uuid
from pathlib import Path

from tools.silverfish import native_host_save, native_inventory_wipe as native_wipe
from tools.silverfish.travel import CROSSROADS


class NativeInventoryWipeTests(unittest.TestCase):
    def setUp(self):
        self.profile = Path(self.enterContext(tempfile.TemporaryDirectory()))
        native_host_save.directory(self.profile).mkdir(parents=True)
        self.guest = str(uuid.uuid4())
        self.wipe_id = uuid.uuid4().hex
        self.frames = {"host": self.frame(), self.guest: self.frame(self.guest)}
        self.items = {
            "host": [
                self.item(1, "equipment", cannot_keep=True),
                self.item(2, "backpack", index=3, stackable=True, quantity=37, important=True),
                self.item(3, "backpack", path=native_wipe.BLACK_STONE, cannot_keep=True),
                self.item(4, "backpack", index=1, important=True, cannot_keep=True),
                self.item(5, "helmet"),
            ],
            self.guest: [self.item(1, "armor", important=True), self.item(6, "equipment")],
        }

    def frame(self, character_id=None):
        slot = native_host_save.new_slot()
        native_host_save.slot_path(self.profile, slot).write_bytes(b"GVAS" + bytes(32))
        frame = native_host_save.record(self.profile, slot, CROSSROADS)
        if character_id is not None:
            frame["character_id"] = character_id
        return frame

    @staticmethod
    def item(number, source, *, index=0, path="/Game/Classes/Items/Bandage.Bandage_C", quantity=1,
             stackable=False, important=False, cannot_keep=False):
        return {"guid": f"{number:032X}", "source": source, "index": index, "class_path": path,
                "quantity": quantity, "stackable": stackable, "important": important, "cannot_keep": cannot_keep}

    def censuses(self):
        return {actor: {"slot": frame["slot"], "sha256": frame["sha256"],
                        "complete": True, "items": self.items[actor]} for actor, frame in self.frames.items()}

    def freeze(self, slots, *, drop=False, lose=False, censuses=None):
        return native_wipe.freeze(self.profile, self.wipe_id, list(self.frames), self.frames,
                                  self.censuses() if censuses is None else censuses,
                                  keep_slots=slots, drop_bone_pile=drop, lose_slot_per_death=lose)

    def guid(self, number):
        return f"{number:032X}"

    def test_stock_black_stone_lock_and_owner_only_important_bags_are_deterministic(self):
        original = {actor: native_host_save.slot_path(self.profile, frame["slot"]).read_bytes()
                    for actor, frame in self.frames.items()}
        draft = self.freeze(2, lose=True)
        choices = {"host": [self.guid(1)], self.guest: [self.guid(6)]}
        planned = native_wipe.plan(self.profile, draft, choices)
        host = planned["owners"]["host"]
        self.assertEqual(host["kept"], [self.guid(3), self.guid(1)])
        self.assertEqual(host["dropped"], [self.guid(2)])
        self.assertEqual(host["discarded"], [self.guid(4), self.guid(5)])
        self.assertEqual(host["forced"], [self.guid(3)])
        self.assertFalse(host["preserve_layout"])
        self.assertEqual(planned["owners"][self.guest]["kept"], [self.guid(6)])
        self.assertEqual(planned["owners"][self.guest]["dropped"], [self.guid(1)])
        self.assertEqual(planned["receipt"]["next_keep_slots"], 1)
        self.assertEqual(planned["receipt"]["owners"]["host"],
                         {"kept": 2, "dropped": 1, "discarded": 2, "forced": 1})
        self.assertNotIn(self.frames["host"]["sha256"], json.dumps(planned["receipt"]))
        host_bag_id = uuid.uuid5(uuid.UUID(hex=self.wipe_id), "host").hex
        guest_bag_id = uuid.uuid5(uuid.UUID(hex=self.wipe_id), self.guest).hex
        self.assertEqual(native_wipe.bag_for(planned, host_bag_id, "host")["guids"], [self.guid(2)])
        self.assertEqual(native_wipe.bag_for(planned, guest_bag_id, self.guest)["source"], self.frames[self.guest])
        with self.assertRaisesRegex(native_wipe.InventoryWipeError, "bag_not_owned"):
            native_wipe.bag_for(planned, host_bag_id, self.guest)
        with self.assertRaisesRegex(native_wipe.InventoryWipeError, "bag_not_owned"):
            native_wipe.bag_for(planned, guest_bag_id, "host")
        reloaded = json.loads(json.dumps(draft))
        reloaded["roster"].reverse()
        reloaded["inventories"]["host"]["items"].reverse()
        self.assertEqual(planned, native_wipe.plan(self.profile, reloaded, dict(reversed(list(choices.items())))))
        for actor, frame in self.frames.items():
            self.assertEqual(native_host_save.slot_path(self.profile, frame["slot"]).read_bytes(), original[actor])

    def test_zero_and_no_penalty_settings_have_different_results(self):
        extraction = native_wipe.plan(self.profile, self.freeze(0, drop=True), {})
        host = extraction["owners"]["host"]
        self.assertEqual(host["kept"], [self.guid(3)])
        self.assertEqual(host["dropped"], [self.guid(2), self.guid(1), self.guid(5)])
        self.assertEqual(host["discarded"], [self.guid(4)])
        preserved = native_wipe.plan(self.profile, self.freeze(None, drop=True, lose=True), {})
        self.assertEqual(preserved["owners"]["host"]["kept"], [self.guid(n) for n in (3, 4, 2, 1, 5)])
        self.assertTrue(preserved["owners"]["host"]["preserve_layout"])
        self.assertIsNone(preserved["receipt"]["next_keep_slots"])
        self.assertEqual(preserved["bags"], {})

    def test_choice_rejects_locked_absent_excluded_and_duplicate_rows(self):
        draft = self.freeze(1)
        for actor, numbers, error in (
            ("host", [3], "black_stone_keep_slot_locked"),
            ("host", [4], "item_cannot_be_kept"),
            ("host", [2], "too_many_native_keep_slots"),
            ("host", [6], "item_unavailable"),
            (self.guest, [1, 1], "invalid_native_wipe_choice"),
            (str(uuid.uuid4()), [], "choice_not_owned"),
        ):
            with self.subTest(actor=actor, numbers=numbers), self.assertRaisesRegex(native_wipe.InventoryWipeError, error):
                native_wipe.plan(self.profile, draft, {actor: [self.guid(n) for n in numbers]})
        with self.assertRaisesRegex(native_wipe.InventoryWipeError, "selection_unavailable"):
            native_wipe.plan(self.profile, self.freeze(None), {self.guest: [self.guid(1)]})
        self.assertEqual(native_wipe.plan(self.profile, draft, {})["owners"]["host"]["kept"], [self.guid(3)])

    def test_compact_census_checks_exact_owner_complete_frame_and_inventory_positions(self):
        corrupt = self.censuses()
        corrupt[self.guest]["complete"] = False
        with self.assertRaisesRegex(native_wipe.InventoryWipeError, "inventory_frame_mismatch"):
            self.freeze(1, censuses=corrupt)
        original = self.frames[self.guest]
        self.frames[self.guest] = dict(original, character_id=str(uuid.uuid4()))
        with self.assertRaisesRegex(native_wipe.InventoryWipeError, "invalid_native_frame"):
            self.freeze(1)
        self.frames[self.guest] = dict(self.frames["host"], character_id=self.guest)
        with self.assertRaisesRegex(native_wipe.InventoryWipeError, "shared_native_frame"):
            self.freeze(1)
        self.frames[self.guest] = original
        for item, error in (
            (self.item(7, "armor", index=1), "item_identity"),
            (self.item(7, "helmet", quantity=2), "item_quantity"),
            (self.item(7, "armor"), "ambiguous_native_item_identity"),
            (self.item(1, "backpack"), "ambiguous_native_item_identity"),
            (dict(self.item(7, "backpack"), typed_payload={}), "invalid_native_item"),
        ):
            with self.subTest(item=item), self.assertRaisesRegex(native_wipe.InventoryWipeError, error):
                census = self.censuses()
                census[self.guest]["items"] = [*self.items[self.guest], item]
                self.freeze(1, censuses=census)

    def test_recovery_refuses_changed_census_or_missing_native_generation(self):
        draft = self.freeze(2)
        altered = json.loads(json.dumps(draft))
        altered["inventories"]["host"]["items"][2]["quantity"] = 38
        with self.assertRaisesRegex(native_wipe.InventoryWipeError, "draft_changed"):
            native_wipe.plan(self.profile, altered, {})
        guest_path = native_host_save.slot_path(self.profile, self.frames[self.guest]["slot"])
        guest_path.write_bytes(b"GVAS" + bytes(33))
        with self.assertRaisesRegex(native_wipe.InventoryWipeError, "invalid_native_frame"):
            native_wipe.plan(self.profile, draft, {})
