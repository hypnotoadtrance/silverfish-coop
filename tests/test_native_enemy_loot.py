import asyncio
from types import SimpleNamespace
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_enemy_loot, native_enemy_shells, native_host_save, native_stock_enemy_drops, native_world_loot, party
from tools.silverfish.campaign import CampaignFormatError, CampaignStore
from tools.silverfish.enemy_loot_profiles import BY_MAP as ENCOUNTERS
from tools.silverfish.generate_enemy_loot_registry import outputs as enemy_registry_outputs
from tools.silverfish.runtime import Bridge, MAP
from tools.silverfish.service import Host
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, REFINERY


class StockEnemyDropSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_banking_publishes_completed_drops_before_yielding(self):
        drops = native_stock_enemy_drops
        for boundary in ("guest request", "time allowance"):
            with self.subTest(boundary=boundary):
                stock = {number: {"sequence": number, "map": CROSSROADS, "actor": f"item-{number}",
                                  "class_path": "ammo", "source": "Bandit_C NPC", "killer": None,
                                  "motion": "settled", "live_at_capture": True, "initial_height": 100}
                         for number in (2, 1, 3)}
                game = {"state": "hosting", "map": CROSSROADS, drops.KEY: stock,
                        drops.MEMORY_CAPABILITY: True, "native_world_loot_version": 1}
                existing = {"marked": "previous drop"}
                original = party.new_party()
                original[drops.STATE] = {"A" * 32: existing}
                host = SimpleNamespace(last_game=game, party=original, native_profile=object(), stopping=False,
                                       transitioning=False, native_checkpoint_unsafe=False, pending_host_save=None,
                                       processed_stock_enemy_drops=set(), zone_epoch="b" * 32, roles=dict,
                                       checkpoint_native=AsyncMock(), control=Mock())
                ready = asyncio.Event()
                clock = Mock(return_value=100.)
                order = []

                async def capture(action, serial, slot, option, *, timeout):
                    self.assertEqual((action, option), (drops.BANK, "memory-escrow"))
                    number = int(serial)
                    order.append(number)
                    if number == 1 and boundary == "guest request":
                        ready.set()
                    if number == 1 and boundary == "time allowance":
                        clock.return_value = 100.020
                    return {**game, drops.BANK_KEY: {"map": CROSSROADS, "sequence": number, "actor": f"item-{number}",
                                                    "class_path": "ammo", "slot": slot, "guid": serial * 32, "ammo": 2,
                                                    "transform": {}, "memory": True}}

                bridge = SimpleNamespace(command=AsyncMock(side_effect=capture))
                with patch.object(drops, "time", SimpleNamespace(monotonic=clock)):
                    await drops.observe(host, bridge, interaction_ready=ready.is_set)
                    self.assertEqual(order, [1])
                    self.assertFalse(host.native_checkpoint_unsafe)
                    self.assertEqual(host.processed_stock_enemy_drops, {1})
                    self.assertTrue(host.party[drops.STATE]["1" * 32]["escrow"]["pending"])
                    self.assertEqual(original[drops.STATE], {"A" * 32: existing})
                    ready.clear()
                    await drops.observe(host, bridge, interaction_ready=ready.is_set)
                self.assertEqual(order, [1, 2, 3])
                self.assertEqual(host.processed_stock_enemy_drops, {1, 2, 3})
                self.assertIs(host.party[drops.STATE]["A" * 32], existing)
                self.assertFalse(host.stopping)
                host.checkpoint_native.assert_not_awaited()


class DeadBridgeEnemyLootTests(unittest.TestCase):
    def test_physical_enemy_drop_requires_current_authority_frame(self):
        zone, guid = "b" * 32, "A" * 32
        pawn = f"Player_FP_C {DEAD_BRIDGE}.DeadBridge_P:PersistentLevel.Player_FP_C_1234"
        game = {"map": DEAD_BRIDGE, "pawns": {"local": {"name": pawn, "location": [1, 2, 3]}}}
        drop = dict(map=DEAD_BRIDGE, guid=guid, class_path=native_enemy_loot.SHELLS,
                    position=[1, 2, 3], quantity=8, claimed_by=None)
        frame = dict(map=DEAD_BRIDGE, epoch=zone, shells={guid: [8, 1, 2, 3]})
        items = native_world_loot.nearby(game, {}, zone, enemy_frame=frame, enemy_rolls={guid: drop})
        item, = items.values()
        self.assertEqual(item.claim_target, f"enemy-shell:{zone}:{guid}")
        self.assertEqual(item.class_path, drop["class_path"])
        self.assertFalse(item.ground_snap)
        for unqualified in (None, dict(frame, epoch="c" * 32), dict(frame, shells={guid: [8, 1, 2, 99]})):
            with self.subTest(frame=unqualified):
                self.assertEqual(native_world_loot.nearby(
                    game, {}, zone, enemy_frame=unqualified, enemy_rolls={guid: drop}), {})
        self.assertEqual(native_world_loot.nearby(
            game, {}, zone, enemy_frame=frame, enemy_rolls={guid: dict(drop, claimed_by="host")}), {})

    def test_refinery_profiles_bind_exact_stock_source_and_map_independently(self):
        refinery, bridge = ENCOUNTERS[REFINERY], ENCOUNTERS[DEAD_BRIDGE]
        self.assertEqual([actor.rsplit("_", 1)[1] for actor in refinery.actors], ["1", "3"])
        guid, slot = "A" * 32, "SFCoop_" + "1" * 32
        drop = dict(map=refinery.map, bandit=refinery.actors[0], profile=refinery.id, class_path=native_enemy_loot.SHELLS,
                    guid=guid, actor=refinery.shell_prefix + "10101", epoch="a" * 32, quantity=6, position=[1, 2, 3],
                    killer="host", claimed_by=None, escrow={"map": refinery.map, "slot": slot})
        records = {guid: dict(drop, retirement=native_host_save.cqc_retirement_record(drop, slot))}
        checkpoint = {"world": {"party": {native_enemy_loot.STATE_KEY: records}}, "characters": {}}
        self.assertEqual(records[guid]["retirement"]["schema"], 2)
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [drop["escrow"]])
        self.assertEqual(native_enemy_loot.defeated_cqc(records, drop["epoch"], refinery.actors[0]), guid)
        self.assertIsNone(native_enemy_loot.defeated_cqc(records, drop["epoch"], bridge.actors[0]))
        for invalid in (
            {key: value for key, value in drop.items() if key != "profile"}, {**drop, "profile": bridge.id},
            {**drop, "actor": bridge.shell_prefix + "10101"}, {**drop, "bandit": refinery.actors[0].removesuffix("_1") + "_2"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(CampaignFormatError):
                records[guid] = invalid
                native_host_save._escrow_frames(checkpoint)
        root = Path(__file__).resolve().parents[1]
        for relative, expected in enemy_registry_outputs().items():
            self.assertEqual((root / relative).read_text(encoding="utf-8"), expected)

    def test_watch_requires_exact_map_and_placed_actor(self):
        watch = native_enemy_loot.watch_target
        cqc = native_enemy_loot.CQC
        sibling = cqc.removesuffix("_0") + "_1"
        self.assertEqual(watch({"map": DEAD_BRIDGE}, cqc), cqc)
        self.assertIsNone(watch({"map": DEAD_BRIDGE}, sibling))
        self.assertIsNone(watch({"map": MAP, "native_bandit": {}}, cqc))
        self.assertEqual(watch({"map": MAP, "native_bandit": {"npc": "stock-bandit"}}, "stock-bandit"), "stock-bandit")

    def test_input_events_keep_exact_shell_visit_and_guid_distinct_from_other_targets(self):
        target = "enemy-shell:" + "b" * 32 + ":" + "A" * 32
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "input.tsv").write_bytes(
                f"interact\t1\t{target}\ninteract\t2\t{target.lower()}\n".encode("ascii")
                + b"interact\t3\t\xff\ninteract\t4\n"
            )
            bridge = Bridge(directory, Mock())
            self.assertEqual([selected for _, selected in bridge.interactions()], [target, None])
            self.assertEqual(bridge.interactions(), [])
        self.assertEqual(native_enemy_shells.parse_view(DEAD_BRIDGE, "b" * 32, "A" * 32, "A" * 32)["focused"], "A" * 32)
        self.assertIsNone(native_enemy_shells.parse_view(DEAD_BRIDGE, "b" * 32, "A" * 32, "B" * 32))

    def test_campaign_escrow_accepts_only_the_authored_cqc_shell_drop(self):
        guid = "A" * 32
        frame = {"map": DEAD_BRIDGE}
        roll = dict(map=DEAD_BRIDGE, bandit=native_enemy_loot.CQC, class_path=native_enemy_loot.SHELLS, guid=guid,
                    actor=native_enemy_loot.SHELL_ACTOR + "1001", epoch="a" * 32,
                    quantity=6, position=[35272, 1413, 191], killer="guest", claimed_by=None, escrow=frame)
        records = {guid: roll}
        checkpoint = {"world": {"party": {"native_enemy_loot_rolls": records}}, "characters": {"guest": {}}}
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [frame])
        for invalid in (dict(roll, bandit=roll["bandit"].removesuffix("_0") + "_2"), dict(roll, class_path="not-shells"),
                        dict(roll, quantity=10), dict(roll, escrow={"map": MAP})):
            with self.subTest(roll=invalid), self.assertRaises(CampaignFormatError):
                records[guid] = invalid
                native_host_save._escrow_frames(checkpoint)
        companion_guid = "B" * 32
        companion = dict(roll, bandit=native_enemy_loot.CQC_ACTORS[1], guid=companion_guid,
                         actor=native_enemy_loot.SHELL_ACTOR + "1002", escrow={"map": DEAD_BRIDGE, "slot": "other"})
        records[guid], records[companion_guid] = roll, companion
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [frame, companion["escrow"]])
        for conflict in (dict(companion, bandit=roll["bandit"]), dict(companion, actor=roll["actor"])):
            records[companion_guid] = conflict
            with self.assertRaisesRegex(CampaignFormatError, "duplicate roll"):
                native_host_save._escrow_frames(checkpoint)
        del records[companion_guid]
        records[guid] = dict(roll, claimed_by="host")
        records[guid].pop("escrow")
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [])
        records[guid] = dict(roll, map=MAP, bandit="stock-bandit", escrow={"map": MAP})
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [{"map": MAP}])

    def test_retirement_receipt_binds_its_version_and_original_roll_and_escrow(self):
        guid, slot = "A" * 32, "SFCoop_" + "1" * 32
        roll = dict(map=DEAD_BRIDGE, bandit=native_enemy_loot.CQC, class_path=native_enemy_loot.SHELLS, guid=guid,
                    actor=native_enemy_loot.SHELL_ACTOR + "1001", epoch="a" * 32, quantity=6, position=[1, 2, 3],
                    killer="host", claimed_by=None, escrow={"map": DEAD_BRIDGE, "slot": slot})
        receipt = native_host_save.cqc_retirement_record(roll, slot)
        records = {guid: dict(roll, retirement=receipt)}
        checkpoint = {"world": {"party": {native_enemy_loot.STATE_KEY: records}}, "characters": {}}
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [roll["escrow"]])
        self.assertEqual(native_enemy_loot.published(records)[guid], {key: value for key, value in roll.items() if key != "escrow"})
        for changes in ({"schema": True}, {"schema": 2}, {"guid": "F" * 32}, {"epoch": "c" * 32},
                        {"slot": "SFCoop_" + "b" * 32}, {"actor": native_enemy_loot.SHELL_ACTOR + "1002"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(CampaignFormatError, "retirement receipt"):
                records[guid]["retirement"] = {**receipt, **changes}
                native_host_save._escrow_frames(checkpoint)

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


class OrdinaryDeadBridgeLootTests(unittest.IsolatedAsyncioTestCase):
    first = "aaaaaaaa-0000-0000-0000-000000000001"
    second = "aaaaaaaa-0000-0000-0000-000000000002"
    guid = "B" * 32
    slot = "SFCoop_" + "b" * 32

    def setUp(self):
        self.target = native_enemy_loot.CQC
        self.ammo = native_enemy_loot.SHELL_ACTOR + "2147480954"
        path = f"Player_FP_C {DEAD_BRIDGE}.DeadBridge_P:PersistentLevel.Player_FP_C_"
        self.host_pawn, self.first_pawn, self.second_pawn = [path + str(index) for index in (101, 102, 103)]
        self.party = party.new_party()
        for member in ("host", self.first, self.second):
            party.add_player(self.party, member)
        self.host = SimpleNamespace(
            fixtures=False, native_guest=True, native_profile=Path("staged-profile"), stopping=False,
            transitioning=False, native_checkpoint_unsafe=False, native_dead_bridge_enemy_loot=True,
            party=self.party, zone_epoch="b" * 32, control=Mock(), checkpoint_native=AsyncMock(return_value=7),
            last_seen=time.monotonic(), roles=lambda: {"host": "host", "guest": self.first, "guest2": self.second},
            native_recipients=lambda: {self.first: {"pawn": self.first_pawn, "connected": True},
                                       self.second: {"pawn": self.second_pawn, "connected": False}},
        )
        self.scope = dict(map=DEAD_BRIDGE, bandit=self.target, killer_character=self.second, reserved=True, captured=False)
        self.source = dict(map=DEAD_BRIDGE, bandit=self.target, state="stock_loot_observed", killer_role="guest",
                           killer_pawn=self.second_pawn, ammo_actor=self.ammo, ammo_class=native_enemy_loot.SHELLS,
                           ammo_quantity=8, loot_calls=1, bounty_calls=2, bounty_instances=1, bounty_active=True)
        self.bounty = dict(map=DEAD_BRIDGE, bandit=self.target, cache_unchanged=True, source="guest_detached",
                           override_count=2, permanent_count=1, timed_count=0, gear_count=0,
                           original_instances=0, original_active=False)
        self.initial = dict(state="hosting", map=DEAD_BRIDGE, native_enemy_loot=self.source,
                            native_enemy_bounty=self.bounty, native_enemy_loot_cqc=self.scope,
                            pawns={"local": {"name": self.host_pawn, "location": [50, 2, 3]},
                                   "guest": {"name": self.first_pawn, "location": [50, 2, 3]}})
        self.host.last_game = self.initial
        self.captured = dict(map=DEAD_BRIDGE, state="captured", actor=self.ammo, pawn=self.second_pawn,
                             slot=self.slot, guid=self.guid, units=8, position=[1, 2, 3])
        self.frame = dict(slot=self.slot, map=DEAD_BRIDGE)

    @staticmethod
    def retire_proof(escrow):
        return dict(schema=1, map=DEAD_BRIDGE, actor=escrow["actor"], slot=escrow["slot"], guid=escrow["guid"])

    async def test_banks_actual_second_guest_and_retires_once_after_commit(self):
        async def game(action, *arguments, **_):
            if action == native_enemy_loot.BANK:
                self.assertTrue(self.host.native_checkpoint_unsafe)
                self.assertEqual(arguments, (self.target, self.slot, self.ammo))
                return dict(self.initial, native_enemy_loot_escrow=self.captured,
                            native_enemy_loot_cqc=dict(self.scope, captured=True))
            self.assertEqual(action, native_enemy_loot.RETIRE)
            self.assertEqual(arguments, (self.ammo, self.slot, self.guid))
            self.host.checkpoint_native.assert_awaited_once()
            stored = self.host.party[native_enemy_loot.STATE_KEY][self.guid]
            self.assertEqual((stored["killer"], stored["escrow"]), (self.second, self.frame))
            self.assertNotIn("retirement", stored)
            return dict(self.initial, native_enemy_loot_escrow=dict(self.captured, state="retired"),
                        native_enemy_loot_retire_proof=self.retire_proof(self.captured),
                        native_enemy_loot_cqc=dict(self.scope, captured=True, reserved=False))

        bridge = SimpleNamespace(command=AsyncMock(side_effect=game))
        with patch.object(native_host_save, "new_slot", return_value=self.slot), patch.object(
                native_host_save, "record", return_value=self.frame) as record, patch.dict("os.environ", clear=True):
            await native_enemy_loot.observe(self.host, bridge)
            await native_enemy_loot.observe(self.host, bridge)
            replay = await native_enemy_loot.bank(self.host, bridge, self.target)
        record.assert_called_once_with(self.host.native_profile, self.slot, DEAD_BRIDGE, flush=True)
        self.assertEqual(bridge.command.await_count, 2)
        self.assertEqual(self.host.checkpoint_native.await_count, 2)
        self.assertTrue(native_host_save.cqc_retired(self.host.party[native_enemy_loot.STATE_KEY][self.guid]))
        self.assertEqual(replay["native_enemy_loot_roll"]["killer"], self.second)
        self.assertFalse(self.host.stopping or self.host.native_checkpoint_unsafe)

    async def test_retirement_only_retries_the_same_raw_ticket_after_delayed_native_destroy(self):
        roll = dict(map=DEAD_BRIDGE, epoch=self.host.zone_epoch, bandit=self.target, actor=self.ammo,
                    guid=self.guid, quantity=8, class_path=native_enemy_loot.SHELLS, position=[1, 2, 3],
                    killer=self.second, claimed_by=None, escrow=self.frame)
        self.party[native_enemy_loot.STATE_KEY] = {self.guid: roll}
        snapshot = dict(self.initial, native_enemy_loot_escrow=dict(self.captured, state="retired"),
                        native_enemy_loot_retire_proof=self.retire_proof(self.captured),
                        native_enemy_loot_cqc=dict(self.scope, captured=True, reserved=False))
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            RuntimeError("native_enemy_loot_raw_retirement_pending"), snapshot,
        ]))
        await native_enemy_loot._retire(self.host, bridge, roll)
        self.assertEqual(bridge.command.await_count, 2)
        self.assertEqual(bridge.command.await_args_list[0], bridge.command.await_args_list[1])
        self.host.checkpoint_native.assert_awaited_once()
        self.assertTrue(native_host_save.cqc_retired(self.host.party[native_enemy_loot.STATE_KEY][self.guid]))

    async def test_refuses_another_members_bounty_or_identity_before_bank(self):
        bridge = SimpleNamespace(command=AsyncMock())
        for proof, scope in ((dict(self.bounty, permanent_count=0), self.scope),
                             (dict(self.bounty, override_count=1), self.scope),
                             (self.bounty, dict(self.scope, killer_character=self.first))):
            self.host.last_game = dict(self.initial, native_enemy_bounty=proof, native_enemy_loot_cqc=scope)
            self.host.stopping = self.host.native_checkpoint_unsafe = False
            # The same pawn cannot be reassigned to a different live owner.
            if scope["killer_character"] == self.first:
                self.host.native_recipients = lambda: {self.second: {"pawn": self.second_pawn, "connected": True}}
            with self.assertRaisesRegex(RuntimeError, "exact_killer_unavailable"):
                await native_enemy_loot.observe(self.host, bridge)
            self.assertTrue(self.host.stopping and self.host.native_checkpoint_unsafe)
        bridge.command.assert_not_awaited()

    async def test_two_pending_cqc_rolls_keep_their_separate_shooter_and_reservation(self):
        second_target = native_enemy_loot.CQC_ACTORS[1]
        other_ammo, other_slot, other_guid = native_enemy_loot.SHELL_ACTOR + "2147480955", "SFCoop_" + "d" * 32, "D" * 32
        sources = {self.target: self.source,
                   second_target: dict(self.source, bandit=second_target, killer_pawn=self.first_pawn, ammo_actor=other_ammo,
                                       ammo_quantity=5, bounty_active=False, bounty_instances=0)}
        scopes = {self.target: self.scope,
                  second_target: dict(self.scope, bandit=second_target, killer_character=self.first)}
        bounties = {self.target: self.bounty, second_target: dict(self.bounty, bandit=second_target, permanent_count=0)}
        drops = {self.target: self.captured,
                 second_target: dict(self.captured, actor=other_ammo, pawn=self.first_pawn, slot=other_slot, guid=other_guid, units=5)}

        def snapshot(receipt=None, retire_proof=None):
            return dict(self.initial, native_enemy_loot_by_actor=sources, native_enemy_bounty_by_actor=bounties,
                        native_enemy_loot_cqc_by_actor={actor: dict(scope) for actor, scope in scopes.items()},
                        native_enemy_loot_escrow=receipt, native_enemy_loot_retire_proof=retire_proof)

        async def command(action, *arguments, **_):
            if action == native_enemy_loot.BANK:
                target, slot, ammo = arguments
                proof = drops[target]
                self.assertEqual((slot, ammo), (proof["slot"], proof["actor"]))
                self.assertTrue(scopes[target]["reserved"])
                scopes[target]["captured"] = True
                return snapshot(proof)
            self.assertEqual(action, native_enemy_loot.RETIRE)
            ammo, slot, guid = arguments
            target = self.host.party[native_enemy_loot.STATE_KEY][guid]["bandit"]
            proof = drops[target]
            self.assertEqual((slot, ammo), (proof["slot"], proof["actor"]))
            if target == self.target:
                self.assertTrue(scopes[second_target]["reserved"])
            scopes[target]["reserved"] = False
            return snapshot(dict(proof, state="retired"), self.retire_proof(proof))

        self.host.last_game = snapshot()
        bridge = SimpleNamespace(command=AsyncMock(side_effect=command))
        with patch.object(native_host_save, "new_slot", side_effect=[self.slot, other_slot]), patch.object(
                native_host_save, "record", side_effect=lambda _, slot, area, **__: {"slot": slot, "map": area}):
            await native_enemy_loot.observe(self.host, bridge)
            await native_enemy_loot.observe(self.host, bridge)
        records = self.host.party[native_enemy_loot.STATE_KEY]
        self.assertEqual({guid: (record["bandit"], record["killer"], record["quantity"]) for guid, record in records.items()},
                         {self.guid: (self.target, self.second, 8), other_guid: (second_target, self.first, 5)})
        self.assertFalse(native_enemy_loot.pending(self.host))
        self.assertEqual(bridge.command.await_count, 4)
        self.assertEqual(self.host.checkpoint_native.await_count, 4)
        self.assertTrue(all(native_host_save.cqc_retired(record) for record in records.values()))

    async def test_claim_is_available_without_fixtures_to_another_member_once(self):
        roll = dict(map=DEAD_BRIDGE, epoch=self.host.zone_epoch, bandit=self.target, actor=self.ammo,
                    guid=self.guid, quantity=8, class_path=native_enemy_loot.SHELLS, position=[1, 2, 3],
                    killer=self.second, claimed_by=None, escrow=self.frame)
        self.party[native_enemy_loot.STATE_KEY] = {self.guid: roll}
        snapshot = dict(self.initial, native_enemy_loot_escrow=dict(self.captured, state="claimed", pawn=self.first_pawn))
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[
            RuntimeError("native_enemy_loot_claimant_unavailable"), snapshot,
        ]))
        with patch.object(native_host_save, "verify_record"):
            with self.assertRaisesRegex(party.RuleError, "native_enemy_loot_claimant_unavailable"):
                await native_enemy_loot.claim(self.host, bridge, self.first, self.guid, "b" * 32)
            self.assertFalse(self.host.stopping)
            self.assertFalse(self.host.native_checkpoint_unsafe)
            self.assertIsNone(roll["claimed_by"])
            result = await native_enemy_loot.claim(self.host, bridge, self.first, self.guid, "b" * 32)
            repeated = await native_enemy_loot.claim(self.host, bridge, self.first, self.guid, "b" * 32)
            with self.assertRaisesRegex(party.RuleError, "already_claimed"):
                await native_enemy_loot.claim(self.host, bridge, "host", self.guid, "a" * 32)
        self.assertEqual(result, repeated)
        self.assertEqual(result["native_enemy_loot_claim"]["claimed_by"], self.first)
        self.assertEqual(bridge.command.await_count, 2)
        self.assertEqual(self.host.checkpoint_native.await_args.kwargs["require_guest_frames"], frozenset({self.first}))
        with self.assertRaisesRegex(party.RuleError, "unavailable"):
            await native_enemy_loot.approach(self.host, bridge, f"host|{self.guid}")

    async def test_stack_merge_keeps_exact_shell_claim_identity_across_replay(self):
        roll = dict(map=DEAD_BRIDGE, epoch=self.host.zone_epoch, bandit=self.target, actor=self.ammo,
                    guid=self.guid, quantity=8, class_path=native_enemy_loot.SHELLS, position=[1, 2, 3],
                    killer=self.second, claimed_by=None, escrow=self.frame)
        self.party[native_enemy_loot.STATE_KEY] = {self.guid: roll}
        destination = "C" * 32
        merge = dict(guid=self.guid, inventory_guid=destination, amount=8, before=7, after=15)
        proof = dict(self.captured, state="merged", pawn=self.first_pawn, merge=merge)
        bridge = SimpleNamespace(command=AsyncMock(return_value=dict(self.initial, native_enemy_loot_escrow=proof)))
        with patch.object(native_host_save, "verify_record"):
            result = await native_enemy_loot.claim(self.host, bridge, self.first, self.guid, "b" * 32)
            self.host.party = party.load_party(self.host.party)
            repeated = await native_enemy_loot.claim(self.host, bridge, self.first, self.guid, "b" * 32)
            with self.assertRaisesRegex(party.RuleError, "already_claimed"):
                await native_enemy_loot.claim(self.host, bridge, "host", self.guid, "a" * 32)
        self.assertEqual(result, repeated)
        claim = result["native_enemy_loot_claim"]
        self.assertEqual((claim["guid"], claim["inventory_guid"], claim["claimed_by"]),
                         (self.guid, destination, self.first))
        self.assertEqual(claim["stack_merge"], dict(amount=8, before=7, after=15))
        self.assertNotIn("inventory_guid", self.host.party[native_enemy_loot.STATE_KEY][self.guid])
        bridge.command.assert_awaited_once()
        self.host.checkpoint_native.assert_awaited_once()
        self.assertEqual(self.host.checkpoint_native.await_args.kwargs,
                         dict(require_guest_frames=frozenset({self.first}), typed_inventory=True))

    async def test_stock_shell_view_waits_for_retirement_and_authoritative_focus_is_rechecked(self):
        roll = dict(map=DEAD_BRIDGE, epoch=self.host.zone_epoch, bandit=self.target, actor=self.ammo,
                    guid=self.guid, quantity=8, class_path=native_enemy_loot.SHELLS, position=[1, 2, 3],
                    killer=self.second, claimed_by=None, escrow=self.frame)
        self.party[native_enemy_loot.STATE_KEY] = {self.guid: roll}
        self.assertIsNone(native_enemy_loot.shell_frame(self.host))
        self.host.last_game = dict(self.initial, native_enemy_shell_view_version=2,
                                   native_enemy_loot_cqc=dict(self.scope, captured=True, reserved=False))
        self.assertIsNone(native_enemy_loot.shell_candidates(self.host))
        roll["retirement"] = native_host_save.cqc_retirement_record(roll, self.slot)
        self.host.zone_epoch = "c" * 32
        self.assertIsNone(native_enemy_loot.shell_frame(self.host))
        selected = f"enemy-shell:{self.host.zone_epoch}:{self.guid}"
        current = {"map": DEAD_BRIDGE, "epoch": self.host.zone_epoch, "shells": {self.guid: [8, 1, 2, 3]}}
        private = native_enemy_loot.shell_candidates(self.host)
        self.assertEqual(private, {**current, "shells": {self.guid: [8, 1, 2, 3, self.ammo]}})
        self.host.last_game[native_enemy_shells.AUTHORITY_KEY] = {
            "map": DEAD_BRIDGE, "pawn": self.host_pawn, "epoch": self.host.zone_epoch, "eligible": [self.guid],
        }
        self.assertNotEqual(roll["epoch"], self.host.zone_epoch)
        self.assertEqual(native_enemy_loot.shell_frame(self.host), current)
        view = native_enemy_shells.ShellView()
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.host.last_game))
        await view.sync(bridge, self.host.last_game, "hosting", self.host.zone_epoch, private)
        await view.sync(bridge, self.host.last_game, "hosting", self.host.zone_epoch, private)
        bridge.command.assert_awaited_once_with(native_enemy_shells.FRAME, self.host.zone_epoch,
                                               self.host_pawn, f"{self.guid}|8|1|2|3|{self.ammo}", timeout=2)
        await view.sync(bridge, self.host.last_game, "hosting", self.host.zone_epoch, private, present=False)
        bridge.command.assert_awaited_with(native_enemy_shells.AUTHORITY_FRAME, self.host.zone_epoch,
                                          self.host_pawn, f"{self.guid}|8|1|2|3|{self.ammo}", timeout=2)
        await view.sync(bridge, self.host.last_game, "hosting", self.host.zone_epoch, None)
        bridge.command.assert_awaited_with(native_enemy_shells.CLEAR, timeout=2)
        bridge.command.reset_mock()
        with self.assertRaisesRegex(party.RuleError, "stale_focus"):
            await native_enemy_loot.claim(self.host, bridge, self.first, f"enemy-shell:{'a' * 32}:{self.guid}", "b" * 32)
        bridge.command.assert_not_awaited()
        with patch.object(native_host_save, "verify_record"):
            with self.assertRaisesRegex(party.RuleError, "focus_obstructed"):
                bridge.command.side_effect = RuntimeError("native_enemy_loot_focus_obstructed")
                await native_enemy_loot.claim(self.host, bridge, self.first, selected, "b" * 32)
            bridge.command.assert_awaited_once_with(
                native_enemy_loot.CLAIM, self.first_pawn, self.slot, self.guid, "1", "2", "3", "stock-focus", self.ammo,
                timeout=7,
            )
            self.assertFalse(self.host.native_checkpoint_unsafe or self.host.stopping)
            self.assertEqual(native_enemy_loot.shell_frame(self.host), current)
            bridge.command.side_effect = None
            self.host.last_game[native_world_loot.CAPABILITY] = 1
            bridge.command.return_value = dict(
                self.host.last_game, native_enemy_loot_escrow=dict(self.captured, state="claimed", pawn=self.first_pawn),
            )
            claimed = await native_enemy_loot.claim(self.host, bridge, self.first, selected, "b" * 32)
            replay = await native_enemy_loot.claim(self.host, bridge, self.first, selected, "b" * 32)
        bridge.command.assert_awaited_with(
            native_enemy_loot.CLAIM, self.first_pawn, self.slot, self.guid, "1", "2", "3", "stock-world-focus", self.ammo,
            timeout=7,
        )
        self.assertEqual(claimed, replay)
        self.assertEqual(claimed["native_enemy_loot_claim"]["claimed_by"], self.first)
        self.assertIsNone(native_enemy_loot.shell_frame(self.host))
        self.assertEqual(bridge.command.await_count, 2)

    async def test_restart_after_interrupted_retirement_checkpoint_never_invents_receipt(self):
        roll = dict(map=DEAD_BRIDGE, epoch=self.host.zone_epoch, bandit=self.target, actor=self.ammo,
                    guid=self.guid, quantity=8, class_path=native_enemy_loot.SHELLS, position=[1, 2, 3],
                    killer="host", claimed_by=None, escrow=self.frame)
        self.party[native_enemy_loot.STATE_KEY] = {self.guid: roll}
        snapshot = dict(self.initial, native_enemy_loot_escrow=dict(self.captured, state="retired"),
                        native_enemy_loot_retire_proof=self.retire_proof(self.captured),
                        native_enemy_loot_cqc=dict(self.scope, captured=True, reserved=False))
        bridge = SimpleNamespace(command=AsyncMock(return_value=snapshot))
        with TemporaryDirectory() as directory:
            with CampaignStore(directory, verify_checkpoint=native_host_save._escrow_frames) as store:
                store.save_world({"map": DEAD_BRIDGE, "party": self.party})
                self.host.checkpoint_native.side_effect = asyncio.CancelledError()
                with self.assertRaises(asyncio.CancelledError):
                    await native_enemy_loot._retire(self.host, bridge, roll)
                self.assertTrue(self.host.stopping and self.host.native_checkpoint_unsafe)
            with CampaignStore(directory, verify_checkpoint=native_host_save._escrow_frames) as reopened:
                self.host.party = party.load_party(reopened.status()["world"]["party"])
                self.host.stopping = self.host.native_checkpoint_unsafe = False
                self.host.zone_epoch = "c" * 32
                self.assertEqual(self.host.party[native_enemy_loot.STATE_KEY][self.guid], roll)
                self.assertIsNone(native_enemy_loot.shell_candidates(self.host))

    async def test_retired_unclaimed_drop_survives_real_store_reopen_with_fresh_epoch(self):
        historical = self.host.zone_epoch
        roll = dict(map=DEAD_BRIDGE, epoch=historical, bandit=self.target, actor=self.ammo,
                    guid=self.guid, quantity=8, class_path=native_enemy_loot.SHELLS, position=[1, 2, 3],
                    killer="host", claimed_by=None, escrow=self.frame)
        self.party[native_enemy_loot.STATE_KEY] = {self.guid: roll}
        retired = dict(self.initial, native_enemy_loot_escrow=dict(self.captured, state="retired"),
                       native_enemy_loot_retire_proof=self.retire_proof(self.captured),
                       native_enemy_loot_cqc=dict(self.scope, captured=True, reserved=False))
        bridge = SimpleNamespace(command=AsyncMock(return_value=retired))
        with TemporaryDirectory() as directory:
            with CampaignStore(directory, verify_checkpoint=native_host_save._escrow_frames) as store:
                first = store.save_world({"map": DEAD_BRIDGE, "party": self.party})

                async def persist(_bridge, candidate, **_):
                    return store.save_world({"map": DEAD_BRIDGE, "party": candidate})

                self.host.checkpoint_native.side_effect = persist
                self.assertEqual(await native_enemy_loot._retire(self.host, bridge, roll), first + 1)
            with CampaignStore(directory, verify_checkpoint=native_host_save._escrow_frames) as reopened:
                self.host.party = party.load_party(reopened.status()["world"]["party"])
                stored = self.host.party[native_enemy_loot.STATE_KEY][self.guid]
                self.assertEqual(stored["epoch"], historical)
                self.assertTrue(native_host_save.cqc_retired(stored))
                self.host.zone_epoch = "c" * 32
                self.host.last_game = dict(self.initial, native_enemy_shell_view_version=2)
                self.assertIsNone(native_enemy_loot.shell_frame(self.host))
                self.host.last_game[native_enemy_shells.AUTHORITY_KEY] = native_enemy_shells.parse_authority(
                    DEAD_BRIDGE, self.host.zone_epoch, self.host_pawn, self.guid,
                )
                self.assertEqual(native_enemy_loot.shell_frame(self.host), {
                    "map": DEAD_BRIDGE, "epoch": self.host.zone_epoch, "shells": {self.guid: [8, 1, 2, 3]},
                })
                self.host.last_game["map"] = MAP
                self.assertIsNone(native_enemy_loot.shell_candidates(self.host))

    async def test_same_thread_travel_hold_banks_before_retiring_the_party(self):
        host = self.host
        host.transitioning = True
        host.last_game = dict(self.initial, native_enemy_loot_cqc=dict(self.scope, reserved=False))
        host.smg_holds = None
        host.travelling_guests = Mock(return_value={"member": {}})
        host.world_flush_supported = False
        host.enrollment = None
        host.begin_zone_guests = Mock()
        host.reopen_zone_guests = AsyncMock()
        host.internal_port = 12345
        destination = {"state": "hosting", "map": MAP}
        request = dict(self.initial, ack={"message": native_enemy_loot.PENDING})
        accepted = {"ack": {"message": "zone travel requested"}}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=(request, accepted)),
                                 wait_for=AsyncMock(return_value=destination), process=SimpleNamespace(pid=0))
        transport = SimpleNamespace(hold_game_routes=Mock(side_effect=nullcontext))

        async def bank_reserved(current, _):
            self.assertIs(current.last_game, request)
            host.begin_zone_guests.assert_not_called()
            self.assertEqual(bridge.command.await_count, 1)

        with patch.object(native_enemy_loot, "observe", side_effect=bank_reserved) as bank, patch(
                "tools.silverfish.service.verify_loopback_socket"):
            result = await Host.switch_zone(host, bridge, transport, MAP, None, host.roles())
        self.assertTrue(result["ok"])
        self.assertEqual([item.args for item in bridge.command.await_args_list], [("travel", MAP), ("travel", MAP)])
        bank.assert_awaited_once_with(host, bridge)
        host.begin_zone_guests.assert_called_once_with(transport)
        host.reopen_zone_guests.assert_awaited_once()


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
        self.records[self.guid]["bandit"] = native_enemy_loot.CQC.removesuffix("_0") + "_2"
        await native_enemy_loot.project_client(self.client)
        self.client.bridge.command.assert_not_awaited()

    def test_refuses_duplicate_or_fabricated_shell_roll(self):
        alternate = "C" * 32
        self.records[alternate] = dict(self.records[self.guid], guid=alternate)
        self.assertIsNone(native_enemy_loot.defeated_cqc(self.records, self.epoch))
        del self.records[alternate]
        self.records[self.guid]["actor"] = native_enemy_loot.CQC
        self.assertIsNone(native_enemy_loot.defeated_cqc(self.records, self.epoch))

    async def test_replica_projections_use_two_independent_actor_and_guid_pairs(self):
        first, second = native_enemy_loot.CQC_ACTORS
        other_guid = "D" * 32
        self.records[other_guid] = dict(self.records[self.guid], guid=other_guid, bandit=second,
                                        actor=native_enemy_loot.SHELL_ACTOR + "2147480955")
        table_key = native_enemy_loot.DEATH_VIEW + "_by_actor"
        view = self.client.last_game[native_enemy_loot.DEATH_VIEW]
        views = {first: view, second: dict(view, actor=second)}
        self.client.last_game[table_key] = views.copy()

        async def project(action, actor, epoch, guid, **_):
            self.assertEqual(action, "native-enemy-loot-project-death")
            self.assertEqual((epoch, guid), (self.epoch, self.guid if actor == first else other_guid))
            views[actor] = dict(views[actor], state="projected", epoch=epoch, guid=guid, hidden=True, collision=False)
            return dict(self.client.last_game, **{table_key: views.copy()})

        self.client.bridge.command.side_effect = project
        await native_enemy_loot.project_client(self.client)
        await native_enemy_loot.project_client(self.client)
        self.assertEqual(self.client.bridge.command.await_count, 2)
        self.assertEqual(self.client.last_game[table_key][second]["guid"], other_guid)

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
