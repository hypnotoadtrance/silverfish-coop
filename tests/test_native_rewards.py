import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_party_members import connection, snapshot
from tools.silverfish import native_rewards, party
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.multi_host import MultiHost
from tools.silverfish.party_members import PartyMembers
from tools.silverfish.travel import CROSSROADS


class NativeRewardDurabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.now = 1000.0
        self.store = CampaignStore(Path(scratch.name) / "campaign", clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.members = PartyMembers(self.store, max_guests=2, clock=lambda: self.now)
        self.invites = [self.store.issue_invitation(name, name) for name in ("Grace", "Active")]
        self.grace, self.active = (row["character_id"] for row in self.invites)
        self.disconnected = self.invites[0]["guest_id"]
        rows = [connection(1), connection(2)]
        self.pawns = {row["pawn"]: invite["character_id"] for row, invite in zip(rows, self.invites)}
        self.base = snapshot(3, *rows)
        self.base["pawns"]["local"] = {"name": "HostPawn", "health": 100, "location": [0, 0, 0]}
        self.base["native_guest_xp_ready"] = True
        self.base["native_guest_save_members"] = {pawn: {"state": "bound"} for pawn in self.pawns}

        host = self.host = object.__new__(MultiHost)
        host.store, host.members, host.last_game = self.store, self.members, self.base
        host.native_profile, host.native_guest, host.native_zones = object(), True, True
        host.native_checkpoint_unsafe = host.stopping = False
        host.allowed_maps, host.control = {CROSSROADS}, Mock()
        host.party = party.new_party()
        host.pending_host_save = None
        host.pending_guest_saves = {}

        self.live_xp = dict.fromkeys(self.pawns.values(), 10)
        self.slots = {}
        self.capture_guest = lambda character: None
        self.bridge = SimpleNamespace(command=self.stock_action)
        for index, invite in enumerate(self.invites):
            member = self.members.admit(game=snapshot(index + 1, *rows[:index]), **invite).member
            binding = self.members.observe(snapshot(index + 2, *rows[:index + 1])).assignment
            self.members.mark_ready(member.guest_id, binding)
            frame = {"character_id": member.character_id, "slot": f"old-{index}", "xp": 10, "map": CROSSROADS}
            self.store.save_character(member.lease_id, {"native_guest_save": frame})
            party.add_player(host.party, member.character_id)
            native_rewards.queue(host.party["players"][member.character_id], 1)
        self.store.save_world({"map": CROSSROADS, "party": host.party})
        self.enterContext(patch("tools.silverfish.native_host_save.record", side_effect=self.stock_record))
        self.enterContext(patch("tools.silverfish.native_host_save.prune"))

    def stock_record(self, profile, slot, area, *, flush):
        return {"slot": slot, "map": area, "xp": self.slots[slot]}

    async def stock_action(self, action, primary, secondary=None, *, timeout):
        state = deepcopy(self.base)
        if action == "native-guest-quest-xp":
            character = self.pawns[primary]
            before = self.live_xp[character]
            self.live_xp[character] += int(secondary)
            state["native_guest_xp"] = {"map": CROSSROADS, "pawn": primary, "before": before, "after": self.live_xp[character]}
        elif action == "native-guest-save-retain":
            self.assertEqual(self.pawns[primary], secondary)
        elif action == "native-host-save-capture":
            self.slots[primary] = None
            state["native_host_save"] = {"state": "captured", "map": CROSSROADS, "pawn": "HostPawn", "slot": primary}
        elif action == "native-guest-save-capture":
            character = self.pawns[primary]
            self.slots[secondary] = self.live_xp[character]
            state["native_guest_save_member_frames"] = {
                primary: {"state": "captured", "map": CROSSROADS, "pawn": primary, "slot": secondary},
            }
            self.capture_guest(character)
        else:
            self.fail(f"Unexpected stock action {action}")
        return state

    def saved_awards(self):
        saved = self.store.status()
        xp = {row["character_id"]: row["state"]["native_guest_save"]["xp"] for row in saved["characters"]}
        pending = {character: player.get(native_rewards.PENDING, 0)
                   for character, player in saved["world"]["party"]["players"].items()}
        return xp, pending

    async def test_grace_keeps_queued_xp_until_the_same_guest_reconnects(self):
        self.members.disconnect(self.disconnected)
        self.now += 89
        await native_rewards.settle(self.host, self.bridge)
        self.assertEqual(self.saved_awards(), ({self.grace: 10, self.active: 11}, {self.grace: 1, self.active: 0}))
        self.assertEqual(self.live_xp, {self.grace: 10, self.active: 11})

        self.assertTrue(self.members.admit(game=self.base, **self.invites[0]).resumed)
        await native_rewards.settle(self.host, self.bridge)
        await native_rewards.settle(self.host, self.bridge)
        self.assertEqual(self.saved_awards(), ({self.grace: 11, self.active: 11}, {self.grace: 0, self.active: 0}))
        self.assertEqual(self.live_xp, {self.grace: 11, self.active: 11})

    async def test_expired_lease_cannot_commit_pending_clear_even_after_its_frame_captured(self):
        def expire_first(character):
            if character == self.grace:
                self.members.disconnect(self.disconnected)
                self.now += 90

        self.capture_guest = expire_first
        revision = self.store.status()["revision"]
        with self.assertRaisesRegex(RuntimeError, "omit a newly modified detached guest save"):
            await native_rewards.settle(self.host, self.bridge)

        self.assertEqual(self.live_xp, {self.grace: 11, self.active: 11})
        self.assertEqual(self.saved_awards(), ({self.grace: 10, self.active: 10}, {self.grace: 1, self.active: 1}))
        self.assertEqual(self.store.status()["revision"], revision)
        self.assertTrue(self.host.native_checkpoint_unsafe)
        self.assertTrue(self.host.stopping)

    async def test_live_rewards_retain_detached_xp_and_leave_the_checkpoint_unchanged(self):
        self.base["native_guest_save_retention"] = True
        revision = self.store.status()["revision"]
        self.members.disconnect(self.disconnected)

        await native_rewards.settle(self.host, self.bridge)
        self.assertEqual(self.live_xp, {self.grace: 10, self.active: 11})
        self.assertEqual(set(self.host.pending_guest_saves), {self.active})
        self.assertEqual(self.host.party["players"][self.grace][native_rewards.PENDING], 1)

        self.assertTrue(self.members.admit(game=self.base, **self.invites[0]).resumed)
        await native_rewards.settle(self.host, self.bridge)
        await native_rewards.settle(self.host, self.bridge)

        self.assertEqual(self.live_xp, {self.grace: 11, self.active: 11})
        self.assertEqual(set(self.host.pending_guest_saves), {self.grace, self.active})
        self.assertEqual(self.saved_awards(), ({self.grace: 10, self.active: 10}, {self.grace: 1, self.active: 1}))
        self.assertEqual(self.store.status()["revision"], revision)
