import unittest
from uuid import uuid4

from tools.silverfish import party


class PartyTests(unittest.TestCase):
    def setUp(self):
        self.state = party.new_party()
        self.guest = str(uuid4())
        self.pawns = {
            "host": {"map": "test", "location": [0, 0, 0]},
            self.guest: {"map": "test", "location": [80, 0, 0]},
        }
        for actor in self.pawns:
            party.add_player(self.state, actor)
        party.seed_loot(self.state, "test", [40, 80, 0])

    def apply(self, actor, action, target=None, key=None, **values):
        self.state, result = party.transition(
            self.state, actor, key or uuid4().hex, action, target, self.pawns,
            eligible=tuple(self.pawns), test_fixtures=True, **values,
        )
        return result

    def test_one_pickup_and_idempotent_replay(self):
        identifier = uuid4().hex
        first = self.apply(self.guest, "interact", party.LOOT_ID, identifier)
        self.assertEqual(self.apply(self.guest, "interact", party.LOOT_ID, identifier), first)
        self.assertEqual(self.state["players"][self.guest]["inventory"], {party.ITEM_ID: 1})
        with self.assertRaisesRegex(party.RuleError, "already_claimed"):
            self.apply("host", "interact", party.LOOT_ID)
        with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
            self.apply(self.guest, "interact", None, identifier)

    def test_revive_requires_living_nearby_teammate(self):
        self.apply("host", "test-damage", self.guest, test_damage=100)
        with self.assertRaisesRegex(party.RuleError, "actor_downed"):
            self.apply(self.guest, "interact")
        self.pawns["host"]["location"] = [1000, 0, 0]
        with self.assertRaisesRegex(party.RuleError, "target_out_of_range"):
            self.apply("host", "interact", self.guest)
        self.pawns["host"]["location"] = [60, 0, 0]
        revive = self.apply("host", "interact", self.guest)
        self.assertEqual(revive["health"], 10)
        self.assertFalse(self.state["players"][self.guest]["downed"])

    def test_shared_balance_and_separate_inventories(self):
        reward = self.apply("host", "test-quest-reward")
        self.assertEqual(reward["wallet"], party.QUEST_CASH)
        self.assertCountEqual(reward["recipients"], ["host", self.guest])
        self.apply(self.guest, "buy", party.OFFER_ID)
        with self.assertRaisesRegex(party.RuleError, "insufficient_funds"):
            self.apply("host", "buy", party.OFFER_ID)
        self.assertEqual(self.state["economy"]["stock"], 1)
        self.assertEqual(self.state["players"]["host"]["inventory"], {party.QUEST_ITEM: 1})
        self.assertEqual(self.state["players"][self.guest]["inventory"],
                         {party.QUEST_ITEM: 1, party.OFFER_ITEM: 1})

    def test_wipe_locks_actions_and_records_owned_selections(self):
        self.state["death_rules"] = {"keep_slots": 1, "drop_backpacks": True}
        self.apply("host", "test-quest-reward")
        self.apply("host", "rest")
        self.apply("host", "test-damage", self.guest, test_damage=100)
        result = self.apply("host", "native-down", "host", test_damage=100, now=100)
        self.assertEqual(result["effect"], "party_wipe")
        self.assertCountEqual(self.state["wipe"]["roster"], ["host", self.guest])
        with self.assertRaisesRegex(party.RuleError, "wipe_in_progress"):
            self.apply("host", "interact")
        self.apply(self.guest, "keep", retained={party.QUEST_ITEM: 1}, now=101)
        self.assertEqual(self.state["wipe"]["choices"], {self.guest: {party.QUEST_ITEM: 1}})
