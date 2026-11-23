import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_quest, native_wire_quest as wire, party
from tools.silverfish.travel import BOROUGH


class CopperStopAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        players = party.new_party()
        for character in ("host", "scout"):
            party.add_player(players, character)
        self.host = SimpleNamespace(
            native_profile=True, native_zones=True, native_guest=True, party=players,
            control=Mock(), stopping=False, transitioning=False, native_checkpoint_unsafe=False,
            pending_host_save=None, last_seen=time.monotonic(), checkpoint_native=AsyncMock(),
            last_game={"state": "hosting", "map": BOROUGH, "pawns": {"local": {"name": "host pawn"}}},
            native_recipients=lambda: {"scout": {"connected": True, "pawn": "guest pawn"}},
        )
        self.step = None
        self.xp = 3
        self.start_xp_change = 0
        self.bridge = Mock(command=AsyncMock(side_effect=self.command))

    async def command(self, verb, pawn, *, timeout):
        self.assertTrue(self.host.transitioning)
        if verb == wire.START:
            self.assertTrue(self.host.native_checkpoint_unsafe)
            self.step = 1
            self.xp += self.start_xp_change
        else:
            self.assertEqual(verb, wire.PREFLIGHT)
        proof = {
            "operation": verb, "map": BOROUGH, "pawn": pawn, "board": wire.BOARD, "id": wire.QUEST_ID,
            "state": "active" if self.step == 1 else "missing", "step": self.step, "changed": verb == wire.START,
            "final_step": 2, "cash_reward": 0, "xp_reward": 1, "commune_base_reward": 20,
            "native_cash": 30, "native_xp": self.xp, "native_commune_rep": -6,
        }
        return {**self.host.last_game, "native_wire_quest": proof}

    async def accept(self, actor):
        return await native_quest.action(self.host, self.bridge, actor, "native-quest-start", wire.QUEST_ID)

    async def test_guest_acceptance_is_shared_in_memory_and_retry_is_idempotent(self):
        result = await self.accept("scout")
        record = {"quest_id": wire.QUEST_ID, "step": 1, "actor": "scout"}
        self.assertEqual(result, {"effect": "native_quest_changed", **record})
        self.assertEqual(self.host.party["native_quests"][wire.QUEST_ID], record)
        self.assertIs(self.host.pending_host_save, False)
        self.assertFalse(self.host.transitioning or self.host.native_checkpoint_unsafe)
        self.host.control.event.assert_called_once_with("native_wire_quest_started", revision=None, **record)
        self.assertEqual((await self.accept("host"))["effect"], "native_quest_present")
        self.assertEqual([call.args for call in self.bridge.command.await_args_list],
                         [(wire.PREFLIGHT, "guest pawn"), (wire.START, "guest pawn"), (wire.PREFLIGHT, "host pawn")])
        self.host.checkpoint_native.assert_not_awaited()

    async def test_host_acceptance_preserves_an_earlier_typed_inventory_save(self):
        self.host.pending_host_save = True
        self.assertEqual((await self.accept("host"))["actor"], "host")
        self.assertIs(self.host.pending_host_save, True)
        self.host.checkpoint_native.assert_not_awaited()

    async def test_unverified_stock_mutation_cannot_become_a_checkpoint(self):
        self.start_xp_change = 1
        with self.assertRaisesRegex(RuntimeError, "changed balances"):
            await self.accept("scout")
        self.assertNotIn(wire.QUEST_ID, self.host.party.get("native_quests", {}))
        self.assertIsNone(self.host.pending_host_save)
        self.assertTrue(self.host.native_checkpoint_unsafe and self.host.stopping)
        self.assertFalse(self.host.transitioning)
        self.host.checkpoint_native.assert_not_awaited()

    async def test_cancelled_stock_start_cannot_become_a_checkpoint(self):
        async def cancelled_start(verb, pawn, *, timeout):
            proof = await self.command(verb, pawn, timeout=timeout)
            if verb == wire.START:
                raise asyncio.CancelledError
            return proof

        self.bridge.command.side_effect = cancelled_start
        with self.assertRaises(asyncio.CancelledError):
            await self.accept("scout")

        self.assertEqual(self.step, 1)
        self.assertNotIn(wire.QUEST_ID, self.host.party.get("native_quests", {}))
        self.assertIsNone(self.host.pending_host_save)
        self.assertTrue(self.host.native_checkpoint_unsafe and self.host.stopping)
        self.assertFalse(self.host.transitioning)
        self.host.checkpoint_native.assert_not_awaited()

    async def test_turn_in_retains_the_guest_inventory_before_consuming_the_exact_wire(self):
        host = self.host
        host.native_loot_input = host.last_game["native_guest_save_retention"] = True
        host.pending_guest_saves = {}
        host.queue_guest_save = lambda character, pawn: host.pending_guest_saves.update({character: (pawn, {})})
        host.roles = lambda: {"host": "host", "guest": "scout"}
        self.step = 1
        objective, weapon = "A1" * 16, "B2" * 16

        async def turn_in_command(verb, pawn, *arguments, timeout):
            if verb == wire.PREFLIGHT:
                frame = await self.command(verb, pawn, timeout=timeout)
                frame["native_wire_quest"].update(objective_guid=objective, objective_owner="guest")
                return frame
            if verb == "native-guest-save-retain":
                self.assertEqual((pawn, arguments), ("guest pawn", ("scout",)))
                return host.last_game
            self.assertEqual((verb, pawn, arguments), (wire.TURN_IN, "guest pawn", (objective, "guest")))
            self.assertEqual(host.pending_guest_saves["scout"][0], pawn)
            return {**host.last_game, "native_wire_quest_redemption": {
                "map": BOROUGH, "pawn": pawn, "board": wire.BOARD, "id": wire.QUEST_ID, "weapon_guid": weapon,
                "ammo_units": 14, "native_cash": 30, "native_xp": self.xp + 1, "rep_before": -6, "rep_after": 14,
                "objective_guid": objective, "objective_owner": "guest",
            }}

        self.bridge.command.side_effect = turn_in_command
        with patch.object(wire, "stage_party_rewards", new=AsyncMock(return_value=[])) as stage:
            receipt = await native_quest.action(host, self.bridge, "scout", "native-quest-turnin", wire.QUEST_ID)
        self.assertEqual([call.args[0] for call in self.bridge.command.await_args_list],
                         [wire.PREFLIGHT, "native-guest-save-retain", wire.TURN_IN])
        self.assertTrue(stage.await_args.kwargs["defer_save"])
        self.assertEqual((receipt["step"], receipt["consumed_wire"], receipt["objective_owner"]), (-10, objective, "scout"))
        self.assertIs(host.pending_host_save, True)
        host.checkpoint_native.assert_not_awaited()
