import json
import unittest
from copy import deepcopy

from tools.silverfish.wipe import WipeError, begin_wipe, finish_wipe, select_items

CHECKPOINT = {"map": "/Game/Classes/Maps/PlaytestMap_01", "location": [1, 2, 3]}
NOW = 1000


def player(items=None, *, downed=True):
    return {"max_health": 100.0, "health": 0 if downed else 60, "downed": downed,
            "inventory": {} if items is None else items}


class WipeTests(unittest.TestCase):
    def setUp(self):
        self.players = {
            "host": player({"bandages": 5, "key": 1, "toy": 2}),
            "reserved": player({"medkit": 2}),
            "offline": player({"gem": 1}, downed=False),
        }
        self.pawns = {"host": {"map": "death_zone", "location": [11, 12, 13]}}

    def begin(self, *, slots=1, drops=True, active=("reserved", "host")):
        return begin_wipe(self.players, active, CHECKPOINT, NOW,
                          keep_slots=slots, drop_backpacks=drops, selection_seconds=30, pawns=self.pawns)

    def test_no_wipe_until_all_active_or_reserved_are_downed(self):
        self.assertIsNone(self.begin(active=()))
        self.assertIsNone(self.begin(active=("host", "offline")))
        self.assertIsNone(self.begin(active=("host", "not_in_world")))
        self.assertEqual(self.begin()["roster"], ["host", "reserved"])

    def test_freezes_roster_inventories_and_checkpoint_without_mutation(self):
        before = deepcopy(self.players)
        wipe = self.begin()
        self.assertEqual(self.players, before)
        self.assertEqual(wipe["checkpoint"], CHECKPOINT)
        self.players["host"]["inventory"]["bandages"] = 0
        self.assertEqual(wipe["players"]["host"]["inventory"]["bandages"], 5)
        self.assertEqual(wipe["roster"], ["host", "reserved"])
        self.assertEqual(json.loads(json.dumps(wipe)), wipe)
        self.assertIsNone(finish_wipe(wipe, NOW + 29.9))

    def test_selection_cannot_exceed_item_count_slot_limit_or_roster(self):
        wipe = self.begin()
        before = deepcopy(wipe)
        for actor, items, error in (
            ("offline", {"gem": 1}, "actor_unavailable"),
            ("host", {"bandages": 6}, "item_unavailable"),
            ("host", {"unknown": 1}, "item_unavailable"),
            ("host", {"bandages": 1, "key": 1}, "too_many_items"),
            ("host", {"bandages": True}, "invalid_inventory"),
            ("host", {"bandages": 0}, "invalid_inventory"),
            ("host", [("bandages", 1), ("bandages", 2)], "invalid_inventory"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(WipeError, error):
                select_items(wipe, actor, items, now=NOW)
        self.assertEqual(wipe, before)
        with self.assertRaisesRegex(WipeError, "selection_closed"):
            select_items(wipe, "host", {"key": 1}, now=NOW + 30)

    def test_full_submission_restores_health_and_creates_owner_only_backpacks(self):
        start = self.begin()
        chosen = select_items(start, "host", {"bandages": 2}, now=NOW + 1)
        self.assertEqual(start["choices"], {})
        self.assertIsNone(finish_wipe(chosen, NOW + 2))
        lock = select_items(chosen, "reserved", {"medkit": 1}, now=NOW + 3)
        with self.assertRaisesRegex(WipeError, "selection_closed"):
            select_items(lock, "host", {}, now=NOW + 4)
        result = finish_wipe(lock, NOW + 4)
        self.assertEqual(result["players"], {
            "host": player({"bandages": 2}, downed=False) | {"health": 100.0},
            "reserved": player({"medkit": 1}, downed=False) | {"health": 100.0},
        })
        self.assertEqual(result["checkpoint"], CHECKPOINT)
        self.assertEqual(result["wipe_id"], start["id"])
        by_owner = {backpack["owner_id"]: backpack for backpack in result["backpacks"].values()}
        self.assertEqual(by_owner["host"]["inventory"], {"bandages": 3, "key": 1, "toy": 2})
        self.assertEqual(by_owner["host"]["position"], self.pawns["host"])
        self.assertEqual(by_owner["reserved"]["inventory"], {"medkit": 1})
        self.assertIsNone(by_owner["reserved"]["position"])
        self.assertNotEqual(by_owner["host"]["id"], by_owner["reserved"]["id"])
        self.assertEqual(finish_wipe(json.loads(json.dumps(lock)), NOW + 30), result)
        result["players"]["host"]["inventory"].clear()
        self.assertEqual(lock["choices"]["host"], {"bandages": 2})

    def test_deadline_applies_no_selection_for_absent_player(self):
        wipe = select_items(self.begin(), "host", {"toy": 2}, now=NOW + 1)
        result = finish_wipe(wipe, NOW + 30)
        self.assertEqual(result["players"]["host"]["inventory"], {"toy": 2})
        self.assertEqual(result["players"]["reserved"]["inventory"], {})
        reserved = next(pack for pack in result["backpacks"].values() if pack["owner_id"] == "reserved")
        self.assertEqual(reserved["inventory"], {"medkit": 2})

    def test_stock_wipe_waits_for_active_members_and_can_finish_after_a_lease_expires(self):
        draft = self.begin()
        draft["native"] = {"stock": True}
        draft["choices"] = {"reserved": {}}
        self.assertIsNone(finish_wipe(draft, NOW + 90, required={"host", "reserved"}))
        self.assertIsNone(finish_wipe(draft, NOW + 90, required={"host"}))
        draft["choices"] = {"host": {"key": 1}}
        self.assertIsNone(finish_wipe(draft, NOW + 90, required={"host", "reserved"}))
        result = finish_wipe(draft, NOW + 90, required={"host"})
        self.assertEqual(result["players"]["host"]["inventory"], {"key": 1})
        bag = next(bag for bag in result["backpacks"].values() if bag["owner_id"] == "reserved")
        self.assertEqual(bag["inventory"], {"medkit": 2})
        draft["choices"]["reserved"] = {"medkit": 1}
        self.assertEqual(finish_wipe(draft, NOW + 90, required={"host", "reserved"})["players"]["reserved"]["inventory"],
                         {"medkit": 1})

    def test_stock_wipe_resumes_a_previously_committed_respawn(self):
        draft = self.begin()
        draft.update(native={"stock": True}, native_committed={"owners": []}, deadline=None)
        self.assertIsNotNone(finish_wipe(draft, NOW + 90))
        with self.assertRaisesRegex(WipeError, "selection_closed"):
            select_items(draft, "host", {}, now=NOW + 90)

    def test_skips_selection_with_none_or_zero_slots(self):
        preserve = self.begin(slots=None)
        with self.assertRaisesRegex(WipeError, "selection_unavailable"):
            select_items(preserve, "host", {}, now=NOW)
        result = finish_wipe(preserve, NOW)
        self.assertEqual(result["players"]["host"]["inventory"], self.players["host"]["inventory"])
        self.assertEqual(result["backpacks"], {})

        all_lost = self.begin(slots=0, drops=False)
        result = finish_wipe(all_lost, NOW)
        self.assertEqual(result["players"]["host"]["inventory"], {})
        self.assertEqual(result["backpacks"], {})


if __name__ == "__main__":
    unittest.main()
