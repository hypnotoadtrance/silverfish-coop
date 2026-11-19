from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_enemy_loot, native_host_save, party
from tools.silverfish.campaign import CampaignFormatError
from tools.silverfish.runtime import Bridge, MAP
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


class DeadBridgeDeathViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.epoch, self.guid = "a" * 32, "B" * 32
        absent = {"map": DEAD_BRIDGE, "actor": native_enemy_loot.CQC, "state": "present", "hidden": False, "collision": True}
        terminal = dict(absent, state="projected", epoch=self.epoch, guid=self.guid, hidden=True, collision=False)
        view = native_enemy_loot.DEATH_VIEW
        records = {self.guid: dict(
            map=DEAD_BRIDGE, bandit=native_enemy_loot.CQC, epoch=self.epoch, guid=self.guid,
            actor=native_enemy_loot.SHELL_ACTOR + "2147480954", class_path=native_enemy_loot.SHELLS, quantity=5,
        )}
        self.client = SimpleNamespace(
            zone_epoch=self.epoch, expected_zone=DEAD_BRIDGE, transport=SimpleNamespace(connected=True),
            stopping=False, needs_travel=False, server_transitioning=False, gameplay_seen=time.monotonic(),
            gameplay={native_enemy_loot.STATE_KEY: records}, control=Mock(),
            last_game={"state": "connected", "map": DEAD_BRIDGE, view: absent},
        )
        self.client.bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "connected", "map": DEAD_BRIDGE, view: terminal,
        }))
        self.records = records

    async def test_reapplies_committed_defeat_per_replica_and_skips_readback(self):
        await native_enemy_loot.project_client(self.client)
        self.client.bridge.command.assert_awaited_once_with(
            "native-enemy-loot-project-death", native_enemy_loot.CQC, self.epoch, self.guid, timeout=3,
        )
        await native_enemy_loot.project_client(self.client)
        self.assertEqual(self.client.bridge.command.await_count, 1)
        fresh = dict(self.client.last_game[native_enemy_loot.DEATH_VIEW], state="present", hidden=False, collision=True)
        self.client.last_game = dict(self.client.last_game, native_enemy_loot_death_view=fresh)
        await native_enemy_loot.project_client(self.client)
        self.assertEqual(self.client.bridge.command.await_count, 2)

    async def test_refuses_previous_visit_disconnected_and_wrong_source(self):
        self.client.zone_epoch = "c" * 32
        await native_enemy_loot.project_client(self.client)
        self.client.zone_epoch = self.epoch
        self.client.transport.connected = False
        await native_enemy_loot.project_client(self.client)
        self.client.transport.connected = True
        self.records[self.guid]["bandit"] = native_enemy_loot.CQC.removesuffix("_0") + "_1"
        await native_enemy_loot.project_client(self.client)
        self.client.bridge.command.assert_not_awaited()

    def test_refuses_duplicate_or_fabricated_shell_roll(self):
        alternate = "C" * 32
        self.records[alternate] = dict(self.records[self.guid], guid=alternate)
        self.assertIsNone(native_enemy_loot.defeated_cqc(self.records, self.epoch))
        del self.records[alternate]
        self.records[self.guid]["actor"] = native_enemy_loot.CQC
        self.assertIsNone(native_enemy_loot.defeated_cqc(self.records, self.epoch))

    def test_bridge_requires_projected_collision_and_visibility_readback(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = Bridge(root, Mock())
            prelude = f"schema\t1\nsession\tclient\tconnected\t{DEAD_BRIDGE}\t0\t1\n"
            row = "\t".join(("native-enemy-loot-death-view", DEAD_BRIDGE, native_enemy_loot.CQC,
                             "projected", self.epoch, self.guid))
            status = root / "status.tsv"
            status.write_text(prelude + row + "\t1\t0\n", encoding="utf-8")
            self.assertEqual(bridge.snapshot()[native_enemy_loot.DEATH_VIEW]["guid"], self.guid)
            status.write_text(prelude + row + "\t0\t1\n", encoding="utf-8")
            self.assertNotIn(native_enemy_loot.DEATH_VIEW, bridge.snapshot())
