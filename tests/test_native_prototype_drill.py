from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_authored_pickup, native_host_save, native_prototype_drill as drill, native_rewards, party, runtime
from tools.silverfish import link, native_loot, native_quest, native_wire_board
from tools.silverfish.campaign import CampaignFormatError
from tools.silverfish.travel import CROSSROADS


GUEST, OFFLINE, LATE = "current-guest", "reserved-guest", "later-admission"
HOST_PAWN, GUEST_PAWN, OFFLINE_PAWN = "Player_FP_C Host", "Player_FP_C Guest", "Player_FP_C Returning"
OBJECTIVE, ARMOR = "A" * 32, "B" * 32
ESCROW_BYTES = b"GVAS" + b"typed stock escrow fixture" * 3


def redemption(operation=drill.PREFLIGHT, *, complete=False, pawn=GUEST_PAWN):
    turning_in = operation == drill.TURN_IN
    old = ("-10", "10700", "13", "1", "1", "1") if complete else ("1", "700", "10", "0", "0", "0")
    new = ("-10", "10700", "13", "1", "1", "1") if turning_in else old
    fields = [drill.TAG, operation, drill.SCIENTIST_SHOP, pawn, drill.BOARD, drill.QUEST_ID,
              "-" if complete else OBJECTIVE, "-" if complete else "guest" if pawn == GUEST_PAWN else "host",
              ARMOR if turning_in else "-", *(value for pair in zip(old[:5], new[:5], strict=True) for value in pair),
              drill.STATION, old[-1], new[-1], "0" if complete else "1"]
    rows = []
    for index, before, after, old_found in ((2, 10, 610, "1"), (3, 0, -200, "0"), (4, -100, -500, "1")):
        old_value = str(after if complete else before)
        old_present = "1" if complete else old_found
        advantage, present = ("40", "1") if index == 2 else ("0", "0")
        rows.append([drill.REP_TAG, str(index), old_value, old_present, str(after) if turning_in else old_value,
                     "1" if turning_in else old_present, advantage, present, advantage, present])
    return fields, rows


class PrototypeProofTests(unittest.TestCase):
    def test_exact_board_callback_and_faction_rows(self):
        fields, rows = redemption(drill.TURN_IN)
        proof = drill.parse_redemption(fields, rows)
        self.assertEqual((proof["step_after"], proof["flag_after"], proof["drill_tested_after"], proof["tape_after"]),
                         (-10, 1, True, True))
        self.assertEqual(proof["reputation"]["commune"], {
            "before": 10, "before_found": True, "after": 610, "after_found": True,
            "advantage": 40, "advantage_found": True,
        })
        for index, wrong in ((4, "other-board"), (8, OBJECTIVE), (12, "10800"), (15, "1"), (21, "0"), (22, "0")):
            altered = fields.copy()
            altered[index] = wrong
            with self.subTest(index=index), self.assertRaises(ValueError):
                drill.parse_redemption(altered, rows)
        for index, col, wrong in ((1, 1, "5"), (0, 8, "340"), (1, 4, "-400"), (2, 4, "-300")):
            altered = deepcopy(rows)
            altered[index][col] = wrong
            with self.subTest(faction=index, column=col), self.assertRaises(ValueError):
                drill.parse_redemption(fields, altered)
        with self.assertRaises(ValueError):
            drill.parse_redemption(fields, rows[:2])

    def test_reward_reports_exact_distinct_item_and_scientist_capture(self):
        slot = "SFCoop_" + "e" * 32
        self.assertEqual(drill.parse_reward([drill.REWARD_TAG, drill.SCIENTIST_SHOP, HOST_PAWN, "captured", slot, ARMOR, ARMOR])
                         ["class_path"], drill.ARMOR_CLASS)
        with self.assertRaises(ValueError):
            drill.parse_reward([drill.REWARD_TAG, CROSSROADS, HOST_PAWN, "captured", slot, ARMOR, ARMOR])
        with self.assertRaises(ValueError):
            drill.parse_reward([drill.REWARD_TAG, drill.SCIENTIST_SHOP, GUEST_PAWN, "granted", slot, ARMOR, ARMOR])

    def test_bridge_requires_one_complete_prototype_board_frame_in_the_scientist_world(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        bridge = runtime.Bridge(directory, object())
        frame, reputation = redemption()

        def snapshot(rows, area=drill.SCIENTIST_SHOP):
            lines = ["schema\t1", "seq\t1", f"session\thost\thosting\t{area}\t1\t0"]
            lines.extend("\t".join(row) for row in rows)
            (directory / "status.tsv").write_text("\n".join(lines), encoding="utf-8")
            return bridge.snapshot()

        self.assertEqual(snapshot([frame, *reputation])[drill.KEY], drill.parse_redemption(frame, reputation))
        self.assertIsNone(snapshot([frame, *reputation], CROSSROADS))
        self.assertIsNone(snapshot([frame, frame, *reputation]))
        self.assertIsNone(snapshot(reputation))


class PrototypeTurnInTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = Path(self.enterContext(TemporaryDirectory()))
        source_map, actor, item = native_authored_pickup.SOURCES[drill.SOURCE]
        state = party.new_party()
        for character in ("host", GUEST, OFFLINE, LATE):
            party.add_player(state, character)
        state["economy"]["wallet"] = 700
        state["native_quests"] = {drill.QUEST_ID: {"quest_id": drill.QUEST_ID, "step": 1, "actor": GUEST}}
        self.claim = dict(source=drill.SOURCE, map=source_map, actor=actor, class_path=item, owner=GUEST,
                          guid=OBJECTIVE, topic_micro_drill=1)
        state[native_authored_pickup.STATE_KEY] = {drill.SOURCE: self.claim}
        self.members = {GUEST: dict(connected=True, pawn=GUEST_PAWN), OFFLINE: dict(connected=False, pawn=OFFLINE_PAWN)}
        self.roles = {"host": "host", "guest": GUEST, "guest2": OFFLINE}
        self.area = drill.SCIENTIST_SHOP
        self.completed = self.disconnect_after_grant = False
        self.defer = False
        self.refusal = None
        self.native_xp = {GUEST_PAWN: 20, OFFLINE_PAWN: 7}
        self.commits, self.actions, self.grants = [], [], []
        self.memory_escrows = {}
        self.host = SimpleNamespace(native_profile=self.profile, native_guest=True, native_zones=True,
                                    native_loot_input=True, pending_host_save=None, pending_guest_saves={},
                                    queue_guest_save=lambda actor, pawn: self.host.pending_guest_saves.update({actor: (pawn, {})}),
                                    transitioning=False, stopping=False, native_checkpoint_unsafe=False,
                                    native_recipients=lambda: self.members, roles=lambda: self.roles,
                                    allowed_maps={self.area, CROSSROADS}, party=state, last_seen=time.monotonic(),
                                    last_game=self.game(), control=SimpleNamespace(event=Mock()),
                                    checkpoint_native=AsyncMock(side_effect=self.checkpoint))
        self.bridge = SimpleNamespace(command=AsyncMock(side_effect=self.command))

    def game(self, **proof):
        return dict(state="hosting", map=self.area, pawns={"local": {"name": HOST_PAWN}},
                    native_guest_xp_ready=True, native_guest_save_retention=self.defer, **proof)

    def persist_escrow(self, slot):
        frame = native_host_save.slot_path(self.profile, slot)
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(ESCROW_BYTES)

    async def checkpoint(self, _bridge, proposed=None, *, require_guest_frames=frozenset()):
        self.commits.append((deepcopy(proposed), require_guest_frames))
        return len(self.commits)

    async def command(self, action, *args, timeout=None):
        self.actions.append(action)
        if action in (drill.PREFLIGHT, drill.TURN_IN):
            if action == drill.TURN_IN:
                self.assertEqual(args[1], OBJECTIVE)
                self.assertTrue(self.host.transitioning and self.host.native_checkpoint_unsafe)
                if self.defer:
                    self.assertFalse(self.commits)
                    self.assertEqual(self.host.pending_guest_saves[GUEST][0], GUEST_PAWN)
                else:
                    self.assertTrue(self.commits)
                if self.refusal:
                    raise RuntimeError(self.refusal)
                self.assertFalse(self.completed)
            proof = drill.parse_redemption(*redemption(action, complete=self.completed, pawn=args[0]))
            if action == drill.TURN_IN:
                self.completed = True
                self.roles["guest3"] = LATE
            return self.game(**{drill.KEY: proof})
        if action == drill.CAPTURE:
            slot, template, *options = args
            self.assertEqual(template, ARMOR)
            self.assertEqual(options, ["memory"] if self.defer else [])
            if options:
                self.memory_escrows[slot] = template
            else:
                self.persist_escrow(slot)
            report = [drill.REWARD_TAG, self.area, HOST_PAWN, "captured", slot, template, template]
        elif action == drill.GRANT:
            slot, template, pawn, assigned, *options = args
            self.assertEqual(options, ["memory"] if slot in self.memory_escrows else [])
            if options:
                self.assertEqual(self.memory_escrows[slot], template)
            else:
                self.assertTrue(native_host_save.slot_path(self.profile, slot).is_file())
            self.grants.append((pawn, assigned))
            if self.disconnect_after_grant:
                self.members[GUEST]["connected"] = False
            report = [drill.REWARD_TAG, self.area, pawn, "granted", slot, template, assigned]
        elif action == "native-guest-save-retain":
            pawn, actor = args
            self.assertEqual(self.members[actor], {"connected": True, "pawn": pawn})
            return self.game()
        elif action == "native-loot-flush-escrows":
            for slot in args:
                self.assertEqual(self.memory_escrows[slot], ARMOR)
                self.persist_escrow(slot)
            return self.game()
        elif action == "native-loot-release-escrows":
            for slot in args:
                self.assertEqual(self.memory_escrows.pop(slot), ARMOR)
            return self.game()
        else:
            self.assertEqual(action, "native-guest-quest-xp")
            pawn, amount = args
            previous = self.native_xp[pawn]
            self.native_xp[pawn] += int(amount)
            return self.game(native_guest_xp=dict(pawn=pawn, map=self.area, before=previous, after=self.native_xp[pawn]))
        return self.game(**{drill.REWARD_KEY: drill.parse_reward(report)})

    async def test_grace_recipient_gets_exact_award_on_return_without_replaying_stock(self):
        offered = await drill.preflight(self.host, self.bridge, GUEST)
        self.assertTrue(offered[drill.KEY]["objective_qualified"])
        result = await drill.turn_in(self.host, self.bridge, GUEST)
        self.assertEqual(result["consumed_drill_head"], OBJECTIVE)
        self.assertEqual(result["recipients"], sorted(["host", GUEST, OFFLINE]))
        self.assertEqual(result["test_results"], {"station": drill.STATION, "class_path": drill.TAPE_CLASS})
        self.assertEqual(self.host.party["economy"]["wallet"], 10700)
        self.assertNotIn("quest_xp", self.host.party["players"][LATE])
        self.assertEqual(self.native_xp, {GUEST_PAWN: 23, OFFLINE_PAWN: 7})
        self.assertEqual(self.grants, [(GUEST_PAWN, result["armor"][GUEST])])
        self.assertEqual(self.commits[-1][1], {GUEST})
        self.assertEqual(self.host.party[drill.REWARDS]["pending"], {OFFLINE: result["armor"][OFFLINE]})

        durable = self.commits[-1][0]
        raw = {"world": {"party": durable}, "characters": {actor: {"state": {}} for actor in (GUEST, OFFLINE, LATE)}}
        frames = drill.escrow_frames(raw)
        self.assertEqual(frames, [durable[drill.REWARDS]["escrow"]])
        native_host_save.verify_record(self.profile, frames[0])
        native_host_save.verify_checkpoint(self.profile, raw)
        wrong_owner = deepcopy(raw)
        wrong_owner["world"]["party"][drill.REWARDS]["pending"][OFFLINE] = result["armor"][GUEST]
        with self.assertRaises(CampaignFormatError):
            native_host_save.verify_checkpoint(self.profile, wrong_owner)

        self.host.party = party.load_party(durable)
        reused = await drill.turn_in(self.host, self.bridge, GUEST)
        self.assertEqual(reused["effect"], "native_quest_already_settled")
        self.assertEqual(self.actions.count(drill.TURN_IN), 1)
        self.assertEqual(len(self.commits), 2)
        await drill.settle(self.host, self.bridge)
        self.assertEqual(len(self.commits), 2)
        self.area = CROSSROADS
        self.host.last_game = self.game()
        self.members[OFFLINE]["connected"] = True
        await drill.settle(self.host, self.bridge)
        self.assertEqual(self.grants[-1], (OFFLINE_PAWN, result["armor"][OFFLINE]))
        self.assertEqual(self.native_xp[OFFLINE_PAWN], 10)
        self.assertEqual(self.commits[-1][1], {OFFLINE})
        self.assertNotIn(drill.REWARDS, self.host.party)
        self.assertNotIn(native_rewards.PENDING, self.host.party["players"][OFFLINE])
        await drill.settle(self.host, self.bridge)
        self.assertEqual(len(self.commits), 3)

    async def test_deferred_escrow_survives_explicit_checkpoint_and_travel_for_an_offline_guest(self):
        self.defer = True
        self.host.last_game = self.game()
        completed = await drill.turn_in(self.host, self.bridge, GUEST)
        self.assertTrue(self.host.pending_host_save)
        self.assertEqual(self.actions[:3], [drill.PREFLIGHT, "native-guest-save-retain", drill.TURN_IN])
        reward = self.host.party[drill.REWARDS]
        slot = reward["escrow"]["slot"]
        self.assertEqual(reward["escrow"], {"map": drill.SCIENTIST_SHOP, "slot": slot, "pending": True})
        self.assertEqual(reward["pending"], {OFFLINE: completed["armor"][OFFLINE]})
        self.assertEqual(self.grants, [(GUEST_PAWN, completed["armor"][GUEST])])
        self.assertEqual(self.native_xp[GUEST_PAWN], 23)
        replay = await drill.turn_in(self.host, self.bridge, GUEST)
        self.assertEqual((replay["effect"], replay["armor"]), ("native_quest_already_settled", completed["armor"]))
        await drill.settle(self.host, self.bridge)
        self.assertFalse(self.commits or tuple(self.profile.rglob("*.sav")))

        characters = {actor: {"state": {}} for actor in (GUEST, OFFLINE, LATE)}
        with self.assertRaises(CampaignFormatError):
            drill.escrow_frames({"world": {"party": self.host.party}, "characters": characters})
        saved, frames = await native_loot.flush_pending_escrows(self.host, self.bridge, self.host.party)
        frame = frames[(drill.REWARDS, None)]
        self.assertEqual(frame, saved[drill.REWARDS]["escrow"])
        self.assertIs(self.host.party[drill.REWARDS]["escrow"]["pending"], True)
        native_host_save.verify_checkpoint(self.profile, {"world": {"party": saved}, "characters": characters})
        await self.bridge.command("native-loot-release-escrows", slot, timeout=3)

        self.area = CROSSROADS
        self.host.last_game = self.game()
        self.host.party = party.load_party(saved)
        self.host.pending_guest_saves.clear()
        self.members[OFFLINE]["connected"] = True
        self.host.pending_host_save = None
        await drill.settle(self.host, self.bridge)
        await drill.settle(self.host, self.bridge)
        self.assertEqual(self.grants[-1], (OFFLINE_PAWN, completed["armor"][OFFLINE]))
        self.assertEqual(len(self.grants), 2)
        self.assertEqual(self.native_xp[OFFLINE_PAWN], 10)
        self.assertEqual(self.host.pending_guest_saves, {OFFLINE: (OFFLINE_PAWN, {})})
        self.assertIsNone(self.host.pending_host_save)
        self.assertNotIn(drill.REWARDS, self.host.party)
        self.assertFalse(self.commits)
        self.assertEqual(list(self.profile.rglob("*.sav")), [native_host_save.slot_path(self.profile, slot)])

    async def test_deferred_in_map_delivery_releases_template_without_creating_a_save(self):
        self.defer = True
        self.host.last_game = self.game()
        completed = await drill.turn_in(self.host, self.bridge, GUEST)
        self.members[OFFLINE]["connected"] = True
        self.host.last_game["native_guest_save_retention"] = False
        await drill.settle(self.host, self.bridge)
        self.assertIn(OFFLINE, self.host.party[drill.REWARDS]["pending"])
        self.assertFalse(self.commits)
        self.host.last_game = self.game()
        await drill.settle(self.host, self.bridge)
        self.assertEqual(self.actions[-3:], [drill.GRANT, "native-guest-quest-xp", "native-loot-release-escrows"])
        self.assertEqual(self.grants[-1], (OFFLINE_PAWN, completed["armor"][OFFLINE]))
        self.assertFalse(self.memory_escrows or self.commits or tuple(self.profile.rglob("*.sav")))
        self.assertNotIn(drill.REWARDS, self.host.party)

    async def test_unverified_deferred_award_cannot_publish_the_turn_in(self):
        self.defer = self.disconnect_after_grant = True
        self.host.last_game = self.game()
        before = deepcopy(self.host.party)
        with self.assertRaisesRegex(RuntimeError, "recipient changed before their stock XP"):
            await drill.turn_in(self.host, self.bridge, GUEST)
        self.assertEqual(self.host.party, before)
        self.assertTrue(self.host.native_checkpoint_unsafe and self.host.stopping)
        self.assertFalse(self.host.transitioning or self.commits or tuple(self.profile.rglob("*.sav")))

    async def test_cooked_class_alone_does_not_redeem_a_different_or_spent_authored_identity(self):
        self.claim["guid"] = "C" * 32
        self.assertFalse((await drill.preflight(self.host, self.bridge, GUEST))[drill.KEY]["objective_qualified"])
        with self.assertRaisesRegex(party.RuleError, "exact_authored_objective_required"):
            await drill.turn_in(self.host, self.bridge, GUEST)
        self.claim["guid"] = OBJECTIVE
        self.host.party["native_quests"]["Clt_DrillHead_Brkr"] = {"consumed_guid": OBJECTIVE}
        with self.assertRaisesRegex(party.RuleError, "exact_authored_objective_required"):
            await drill.turn_in(self.host, self.bridge, GUEST)
        self.assertFalse(self.commits)
        self.assertNotIn(drill.TURN_IN, self.actions)

    async def test_missing_stock_completion_receipt_refuses_replay_without_mutation(self):
        self.host.party["native_quests"][drill.QUEST_ID]["step"] = -10
        with self.assertRaisesRegex(RuntimeError, "no completed stock quest"):
            await drill.turn_in(self.host, self.bridge, GUEST)
        self.assertEqual(self.actions, [drill.PREFLIGHT])
        self.assertFalse(self.commits or self.host.stopping)

    async def test_safe_native_refusal_preserves_last_paired_checkpoint_and_play(self):
        self.refusal = "prototype_drill_board_exact_listener_unqualified"
        with self.assertRaisesRegex(party.RuleError, self.refusal):
            await drill.turn_in(self.host, self.bridge, GUEST)
        self.assertEqual(self.commits, [(None, frozenset())])
        self.assertFalse(self.host.native_checkpoint_unsafe or self.host.stopping or self.host.transitioning)

    async def test_unverified_mutation_or_custody_change_cannot_publish_a_final_checkpoint(self):
        self.disconnect_after_grant = True
        prior = deepcopy(self.host.party)
        with self.assertRaisesRegex(RuntimeError, "recipient changed before their stock XP"):
            await drill.turn_in(self.host, self.bridge, GUEST)
        self.assertTrue(self.host.native_checkpoint_unsafe and self.host.stopping)
        self.assertFalse(self.host.transitioning)
        self.assertEqual(self.host.party, prior)
        self.assertEqual(self.commits, [(None, frozenset())])

    async def test_missing_detached_save_frame_keeps_prior_state_after_stock_reward(self):
        before = deepcopy(self.host.party)

        async def reject_final(_bridge, proposed=None, *, require_guest_frames=frozenset()):
            if proposed is None:
                return await self.checkpoint(_bridge, proposed, require_guest_frames=require_guest_frames)
            self.assertEqual(require_guest_frames, {GUEST})
            self.assertEqual(proposed["native_quests"][drill.QUEST_ID]["step"], -10)
            raise RuntimeError("The exact guest frame did not verify")

        self.host.checkpoint_native.side_effect = reject_final
        with self.assertRaisesRegex(RuntimeError, "exact guest frame"):
            await drill.turn_in(self.host, self.bridge, GUEST)
        self.assertEqual(self.native_xp[GUEST_PAWN], 23)
        self.assertEqual(len(self.grants), 1)
        self.assertEqual(self.host.party, before)
        self.assertTrue(self.host.native_checkpoint_unsafe and self.host.stopping)
        self.assertEqual(self.commits, [(None, frozenset())])

    async def test_scientist_board_routes_issued_contract_only_and_extends_turn_in_budget(self):
        view = native_wire_board.WireBoardView(self.bridge, self.host.control)
        self.assertEqual(native_wire_board.fixture_selection(drill.SCIENTIST_SHOP, drill.QUEST_ID), (drill.QUEST_ID,))
        with self.assertRaises(party.RuleError):
            native_wire_board.fixture_selection(CROSSROADS, drill.QUEST_ID)
        with self.assertRaisesRegex(party.RuleError, "uses_authored_mosby_dialogue"):
            await native_quest.action(self.host, self.bridge, GUEST, "native-quest-start", drill.QUEST_ID)
        async def invoke(verb, _operation, target):
            return await drill.preflight(self.host, self.bridge, GUEST, verb, target)

        query = (drill.SCIENTIST_SHOP, drill.BOARD, drill.QUEST_ID, "a" * 32, invoke)
        self.assertEqual(await view._inspect(*query), "ready")
        self.claim["owner"] = OFFLINE
        self.assertEqual(await view._inspect(*query), "missing")
        self.completed = True
        self.assertEqual(await view._inspect(*query), "completed")
        self.assertEqual(link.checkpoint_action_timeout({
            "action": "native-quest-turnin", "target": drill.QUEST_ID, "operation_id": "a" * 32,
        }), 28)
