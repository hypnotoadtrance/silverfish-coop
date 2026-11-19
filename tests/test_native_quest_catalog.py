import unittest

from tools.silverfish.native_quest_catalog import parse


REWARD = "/Game/Classes/Items/Restoration/Medkits/Bandage.Bandage_C"


def summary(source, *, state="ready", count="0", emitted="0", reason=""):
    return ["native-quest-catalog", source, state, count, emitted, reason]


def quest(source, quest_id, *, state=None, step="1", final="2", cash="200", xp="1", item_count="0"):
    if state is None:
        state = "template" if source == "stock" else "active"
    return ["native-quest-entry", source, quest_id, state, step, final, cash, xp, item_count]


def reward(source, quest_id, index="0", class_path=REWARD):
    return ["native-quest-reward-item", source, quest_id, index, class_path]


class QuestCatalogTests(unittest.TestCase):
    def test_preserves_frame_sequence_and_typed_scope_specific_metadata(self):
        fields = [
            ["schema", "1"],
            summary("active", count="1", emitted="1"),
            quest("active", "Jmr_CrossRoads", state="completed", step="-10"),
            summary("stock", count="1", emitted="1"),
            quest("stock", "Jmr_CrossRoads", cash="201", item_count="2"),
            reward("stock", "Jmr_CrossRoads", index="1"),
            reward("stock", "Jmr_CrossRoads"),
        ]
        parsed = parse(iter(fields), sequence=23)
        self.assertEqual(parsed["sequence"], 23)
        self.assertEqual(parsed["active"]["entries"]["Jmr_CrossRoads"], {
            "state": "completed", "step": -10, "final_step": 2, "cash": 200, "xp": 1,
            "item_count": 0, "items": [],
        })
        self.assertEqual(parsed["stock"]["entries"]["Jmr_CrossRoads"], {
            "state": "template", "step": 1, "final_step": 2, "cash": 201, "xp": 1,
            "item_count": 2, "items": [{"index": 0, "class_path": REWARD}, {"index": 1, "class_path": REWARD}],
        })
        self.assertEqual(parse(fields, sequence=25)["sequence"], 25)
        self.assertEqual(parse(fields, sequence=4)["sequence"], 4)

    def test_absent_catalog_is_distinct_from_empty_or_unavailable(self):
        existing_quest_row = ["native-quest", "Jmr_CrossRoads", "active", "1", "2", "200", ""]
        self.assertIsNone(parse([existing_quest_row], sequence=None))
        ready = summary("active")
        unavailable = summary("stock", state="unavailable", count="-", reason="quest_table_unavailable")
        result = parse([existing_quest_row, ready, unavailable], sequence=1)
        self.assertEqual(result["active"], {"state": "ready", "total": 0, "emitted": 0, "reason": "", "entries": {}})
        self.assertEqual(result["stock"], {"state": "unavailable", "total": None, "emitted": 0,
                                           "reason": "quest_table_unavailable", "entries": {}})

    def test_partial_reward_keeps_total_and_exact_known_item_positions(self):
        result = parse([
            summary("active", state="partial", count="1", emitted="1", reason="reward_summary_limited"),
            quest("active", "Food", state="ready", step="2", item_count="9"),
            reward("active", "Food", index="7"),
            summary("stock"),
        ], sequence=280)
        entry = result["active"]["entries"]["Food"]
        self.assertEqual((entry["state"], entry["item_count"]), ("ready", 9))
        self.assertEqual(entry["items"], [{"index": 7, "class_path": REWARD}])

    def test_unqualified_or_limited_entry_summaries_remain_explicit(self):
        for total, reason in (("3", "unqualified_entries"), ("65", "entry_limit")):
            with self.subTest(total=total):
                result = parse([
                    summary("active", state="partial", count=total, emitted="1", reason=reason),
                    quest("active", "Food", state="inactive", step="0", final="0"),
                    summary("stock"),
                ], sequence=2)
                self.assertEqual(result["active"]["reason"], reason)
                self.assertEqual(result["active"]["emitted"], 1)

    def test_rejects_missing_duplicate_summary_or_invalid_freshness(self):
        for fields, sequence in (
            ([summary("active")], 1),
            ([summary("active"), summary("stock"), summary("stock")], 1),
            ([summary("active"), summary("stock")], None),
            ([summary("active"), summary("stock")], False),
            ([summary("active"), summary("stock")], 0),
        ):
            with self.subTest(fields=fields, sequence=sequence):
                with self.assertRaises(ValueError):
                    parse(fields, sequence=sequence)

    def test_rejects_inconsistent_counts_or_false_completeness(self):
        for fields in (
            [summary("active", count="1", emitted="1"), summary("stock")],
            [summary("active", count="0", emitted="1"), summary("stock")],
            [summary("active", count="1025"), summary("stock")],
            [summary("active", count="65", emitted="65"), summary("stock")],
            [summary("active", state="unavailable", reason="quest_table_unavailable"), summary("stock")],
            [summary("active", count="1", emitted="1"), quest("active", "Food", item_count="1"), summary("stock")],
            [summary("active", state="partial", reason="reward_summary_limited"), summary("stock")],
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    parse(fields, sequence=4)

    def test_rejects_invalid_typed_values_and_progress_labels(self):
        for entry in (
            quest("active", "Food", cash="-1"),
            quest("active", "Food", xp="2147483648"),
            quest("active", "Food", step="-2147483649"),
            quest("active", "Food", final="nan"),
            quest("active", "Food", item_count="01"),
            quest("active", "Food", item_count="\u0661"),
            quest("active", "Food", state="ready"),
            quest("active", "Food", state="template"),
            quest("active", "None"),
            quest("active", "Bad\tID"),
        ):
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    parse([summary("active", count="1", emitted="1"), entry, summary("stock")], sequence=100)

    def test_rejects_duplicate_quest_ids_and_counted_unavailable_data(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse([summary("active", count="2", emitted="2"), quest("active", "Food"),
                   quest("active", "food"), summary("stock")], sequence=3)
        with self.assertRaisesRegex(ValueError, "no qualified summary"):
            parse([summary("active"), summary("stock", state="unavailable", count="-", reason="missing"),
                   quest("stock", "Food")], sequence=3)

    def test_rejects_reward_ambiguity_wrong_path_or_wrong_quest_id(self):
        for details in (
            [reward("active", "FOOD")],
            [reward("active", "Food", index="1")],
            [reward("active", "Food"), reward("active", "Food")],
            [reward("active", "Food", class_path="/Engine/Telemetry.Override")],
            [reward("active", "Food", class_path="/Game/../Outside.Outside_C")],
        ):
            with self.subTest(details=details):
                with self.assertRaises(ValueError):
                    parse([summary("active", count="1", emitted="1"), quest("active", "Food", item_count="1"),
                           *details, summary("stock")], sequence=3)

    def test_rejects_unknown_scope_or_incomplete_recognized_wire_row(self):
        for fields in (
            [summary("unknown"), summary("active"), summary("stock")],
            [["native-quest-reward-item", "active", "Food"]],
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    parse(fields, sequence=3)
