from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from tools.silverfish import native_enemy_loot, native_host_save, party
from tools.silverfish.campaign import CampaignFormatError
from tools.silverfish.runtime import MAP
from tools.silverfish.travel import DEAD_BRIDGE


class DeadBridgeEnemyLootTests(unittest.TestCase):
    def test_watch_requires_exact_map_and_placed_actor(self):
        watch = native_enemy_loot.watch_target
        cqc = native_enemy_loot.CQC
        sibling = cqc.removesuffix("_0") + "_1"
        self.assertEqual(watch({"map": DEAD_BRIDGE}, cqc), cqc)
        self.assertIsNone(watch({"map": DEAD_BRIDGE}, sibling))
        self.assertIsNone(watch({"map": MAP, "native_bandit": {}}, cqc))
        self.assertEqual(watch({"map": MAP, "native_bandit": {"npc": "stock-bandit"}}, "stock-bandit"), "stock-bandit")

    def test_campaign_escrow_accepts_only_the_authored_cqc_shell_drop(self):
        guid = "A" * 32
        frame = {"map": DEAD_BRIDGE}
        roll = dict(map=DEAD_BRIDGE, bandit=native_enemy_loot.CQC, class_path=native_enemy_loot.SHELLS, guid=guid,
                    quantity=6, position=[35272, 1413, 191], killer="guest", claimed_by=None, escrow=frame)
        records = {guid: roll}
        checkpoint = {"world": {"party": {"native_enemy_loot_rolls": records}}, "characters": {"guest": {}}}
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [frame])
        for invalid in (dict(roll, bandit=roll["bandit"].removesuffix("_0") + "_1"), dict(roll, class_path="not-shells"),
                        dict(roll, quantity=10), dict(roll, escrow={"map": MAP})):
            with self.subTest(roll=invalid), self.assertRaises(CampaignFormatError):
                records[guid] = invalid
                native_host_save._escrow_frames(checkpoint)
        records[guid] = dict(roll, claimed_by="host")
        records[guid].pop("escrow")
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [])
        records[guid] = dict(roll, map=MAP, bandit="stock-bandit", escrow={"map": MAP})
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [{"map": MAP}])

    def test_dead_bridge_gate_requires_both_explicit_fixture_environment_flags(self):
        game = {"state": "hosting", "map": DEAD_BRIDGE}
        host = SimpleNamespace(fixtures=True, native_guest=True, native_profile=True, stopping=False, transitioning=False,
                               native_checkpoint_unsafe=False, party={"wipe": None}, last_game=game, last_seen=time.monotonic())
        opt_in = {"SILVERFISH_COOP_NATIVE_ENEMY_LOOT": "1", "SILVERFISH_COOP_NATIVE_NPC_TEST": "1"}
        with patch.dict("os.environ", opt_in, clear=True):
            self.assertEqual(native_enemy_loot._available(host), DEAD_BRIDGE)
        for flag in opt_in:
            with self.subTest(missing=flag), patch.dict("os.environ", {key: value for key, value in opt_in.items() if key != flag},
                                                      clear=True), self.assertRaisesRegex(party.RuleError, "unavailable"):
                native_enemy_loot._available(host)
        with patch.dict("os.environ", clear=True):
            game["map"] = MAP
            self.assertEqual(native_enemy_loot._available(host), MAP)
