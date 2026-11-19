import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_host_save, native_meet_broker as broker, native_quest, native_wire_quest, party
from tools.silverfish.travel import BLACK_MARKET, CITY


STOCK_WEAPON = "AB" * 16
TEMPLATE_AMMO = "CD" * 16


class MeetBrokerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = Path(self.enterContext(tempfile.TemporaryDirectory()))
        characters = party.new_party()
        for name in ("host", "scout", "reserve"):
            party.add_player(characters, name)
        self.members = {
            "scout": {"connected": True, "pawn": "exact scout pawn"},
            "reserve": {"connected": False, "pawn": "exact reserve pawn"},
        }
        self.host = SimpleNamespace(
            native_profile=self.profile, native_zones=True, native_guest=True, party=characters, control=Mock(),
            stopping=False, transitioning=False, native_checkpoint_unsafe=False, last_seen=time.monotonic(),
            allowed_maps={BLACK_MARKET, CITY}, roles=lambda: {"host": "host", "guest": "scout", "guest2": "reserve"},
            native_recipients=lambda: self.members, checkpoint_native=AsyncMock(return_value=1),
            last_game={"state": "hosting", "map": BLACK_MARKET, "native_guest_xp_ready": True,
                       "pawns": {"local": {"name": "exact host pawn"}}},
        )
        self.step = None
        self.xp = 3
        self.reputation = -6
        self.guest_xp = {"exact scout pawn": 2, "exact reserve pawn": 1}
        self.created = {}
        self.bridge = Mock(command=AsyncMock(side_effect=self.command))

    async def command(self, verb, *arguments, timeout):
        snapshot = dict(self.host.last_game)
        area = snapshot["map"]
        if verb in (broker.PREFLIGHT, broker.START, broker.TURN_IN):
            before, xp, reputation = self.step, self.xp, self.reputation
            if verb == broker.START:
                self.step = 1
            if verb == broker.TURN_IN:
                self.step, self.xp = -10, self.xp + 1
                self.reputation += 2
            self.assertEqual(arguments, ("exact scout pawn",))
            snapshot["native_meet_broker"] = {
                "operation": verb, "map": area, "pawn": arguments[0], "board": broker.BOARD, "quest": broker.QUEST_ID,
                "step_before": before, "step_after": self.step, "cash_before": 30, "cash_after": 30,
                "xp_before": xp, "xp_after": self.xp, "weapon_guid": STOCK_WEAPON if verb == broker.TURN_IN else "-",
                "reputation_before": reputation, "reputation_after": self.reputation,
            }
        elif verb == "native-meet-broker-capture":
            slot, weapon = arguments
            self.assertEqual((area, weapon), (BLACK_MARKET, STOCK_WEAPON))
            path = native_host_save.slot_path(self.profile, slot)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"GVAS typed quest reward fixture")
            snapshot[broker.REWARDS.key] = self.reward_readback("captured", "exact host pawn", slot, weapon, TEMPLATE_AMMO)
        elif verb == "native-meet-broker-grant":
            pawn, slot, weapon, ammo, personal_weapon, personal_ammo = arguments
            self.assertEqual((weapon, ammo), (STOCK_WEAPON, TEMPLATE_AMMO))
            self.assertNotIn(pawn, self.created)
            self.created[pawn] = personal_weapon, personal_ammo
            snapshot[broker.REWARDS.key] = self.reward_readback("granted", pawn, slot, personal_weapon, personal_ammo)
        elif verb == "native-guest-quest-xp":
            pawn, amount = arguments
            before = self.guest_xp[pawn]
            self.guest_xp[pawn] += int(amount)
            snapshot["native_guest_xp"] = {"map": area, "pawn": pawn, "before": before, "after": self.guest_xp[pawn]}
        else:
            self.fail(f"Unexpected bridge command {verb}")
        return snapshot

    def reward_readback(self, state, pawn, slot, weapon, ammo):
        return {"state": state, "map": self.host.last_game["map"], "pawn": pawn, "slot": slot,
                "weapon_guid": weapon, "ammo_guid": ammo, "weapon_mag": 6, "ammo_units": 9}

    async def test_starts_settles_once_and_keeps_the_reserved_guest_reward_for_reconnect(self):
        async def stock_action(verb):
            return await native_quest.action(self.host, self.bridge, "scout", verb, broker.QUEST_ID)

        started = await stock_action("native-quest-start")
        self.assertEqual((started["step"], self.xp), (1, 3))
        self.assertEqual((await stock_action("native-quest-start"))["effect"], "native_quest_present")
        receipt = await stock_action("native-quest-turnin")
        self.assertEqual((receipt["step"], receipt["cash"], receipt["xp"], receipt["ammo_units"]), (-10, 0, 1, 9))
        self.assertEqual(receipt["freelancer_rep"], -4)
        self.assertEqual(receipt["recipients"], ["host", "reserve", "scout"])
        self.assertEqual(self.host.checkpoint_native.call_args.kwargs["require_guest_frames"], {"scout"})
        pending = self.host.party[broker.REWARDS.key]
        self.assertEqual(set(pending["pending"]), {"reserve"})
        checkpoint = {"world": {"party": self.host.party}, "characters": {key: {"state": {}} for key in self.members}}
        self.assertEqual(native_host_save.references(checkpoint), [pending["escrow"]])
        native_host_save.verify_checkpoint(self.profile, checkpoint)
        self.assertEqual(self.guest_xp, {"exact scout pawn": 3, "exact reserve pawn": 1})

        bridge_calls = self.bridge.command.call_count
        self.assertEqual((await stock_action("native-quest-turnin"))["effect"], "native_quest_already_settled")
        self.assertEqual(self.bridge.command.call_count, bridge_calls)
        self.host.party = party.load_party(self.host.party)
        self.host.last_game["map"] = CITY
        self.members["reserve"]["connected"] = True
        await native_wire_quest.settle(self.host, self.bridge, broker.REWARDS)
        self.assertNotIn(broker.REWARDS.key, self.host.party)
        self.assertEqual(self.host.checkpoint_native.call_args.kwargs["require_guest_frames"], {"reserve"})
        self.assertEqual(self.guest_xp, {"exact scout pawn": 3, "exact reserve pawn": 2})
        all_guids = {STOCK_WEAPON, TEMPLATE_AMMO, *self.created["exact scout pawn"], *self.created["exact reserve pawn"]}
        self.assertEqual(len(all_guids), 6)
        bridge_calls = self.bridge.command.call_count
        await native_wire_quest.settle(self.host, self.bridge, broker.REWARDS)
        self.assertEqual(self.bridge.command.call_count, bridge_calls)

    async def test_refuses_missing_quest_wrong_board_and_unbound_guest(self):
        with self.assertRaisesRegex(party.RuleError, "quest_not_ready"):
            await broker.commit(self.host, self.bridge, "scout", turning_in=True)
        self.host.checkpoint_native.assert_not_called()
        self.bridge.command.reset_mock()
        with self.assertRaisesRegex(party.RuleError, "interactor_unavailable"):
            await broker.commit(self.host, self.bridge, "reserve", turning_in=False)
        self.host.last_game["map"] = CITY
        with self.assertRaisesRegex(party.RuleError, "native_meet_broker_unavailable"):
            await broker.commit(self.host, self.bridge, "scout", turning_in=False)
        self.bridge.command.assert_not_called()

    async def test_mismatched_stock_turn_in_cannot_commit_or_issue_guest_rewards(self):
        self.step = 1

        async def inconsistent_readback(verb, *arguments, timeout):
            snapshot = await self.command(verb, *arguments, timeout=timeout)
            if verb == broker.TURN_IN:
                snapshot["native_meet_broker"]["cash_after"] = 31
            return snapshot

        self.bridge.command.side_effect = inconsistent_readback
        with self.assertRaisesRegex(RuntimeError, "authoritative preflight"):
            await broker.commit(self.host, self.bridge, "scout", turning_in=True)
        self.host.checkpoint_native.assert_awaited_once_with(self.bridge)
        self.assertTrue(self.host.native_checkpoint_unsafe)
        self.assertTrue(self.host.stopping)
        self.assertNotIn("native_quests", self.host.party)
        self.assertEqual(self.created, {})

    async def test_stock_hidden_offer_never_issues_a_quest(self):
        detail = "stock_meet_broker_stock_catalog_hidden:global_board=false,show_all=false,board=1,xp=0"
        self.bridge.command.side_effect = RuntimeError(detail)
        with self.assertRaisesRegex(party.RuleError, "stock_catalog_hidden"):
            await broker.commit(self.host, self.bridge, "scout", turning_in=False)
        self.bridge.command.assert_awaited_once_with(broker.PREFLIGHT, "exact scout pawn", timeout=5)
        self.host.checkpoint_native.assert_not_awaited()
        self.assertFalse(self.host.stopping)
        self.assertFalse(self.host.native_checkpoint_unsafe)
        self.assertNotIn(broker.QUEST_ID, self.host.party.get("native_quests", {}))
