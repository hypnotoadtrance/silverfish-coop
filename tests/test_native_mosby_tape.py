import time
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_host_save, native_loot, native_mosby_tape as tape, party


PAWN = "Player_FP_C exact guest"
BLANK = "A" * 32
FILLED = "B" * 32
ARMOR = "C" * 32
OTHER = "D" * 32
OPERATION = "e" * 32
CHARACTER = "guest-character"


def offer(verb=tape.PREFLIGHT, state="offer", blank=BLANK, filled="-"):
    return ["native-mosby-tape", verb, tape.STONE_MAZE, PAWN, tape.STATION, state, blank, filled, "1", "100", "2", "1", "1", "0", "0"]


class MosbyTapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_tape_is_traded_once_and_commit_requires_guest_frame(self):
        game = {"state": "hosting", "map": tape.STONE_MAZE}
        host = SimpleNamespace(native_profile=True, native_loot_input=False, native_zones=True, native_guest=True, transitioning=False, stopping=False,
                               native_checkpoint_unsafe=False, party=party.new_party(), last_game=game, last_seen=time.monotonic(),
                               control=SimpleNamespace(event=Mock()))
        party.add_player(host.party, CHARACTER)
        host.native_recipients = lambda: {CHARACTER: {"pawn": PAWN, "connected": True}}
        commits = []

        async def checkpoint(_bridge, proposed=None, *, require_guest_frames=frozenset()):
            commits.append((deepcopy(proposed), require_guest_frames))
            return len(commits)

        host.checkpoint_native = AsyncMock(side_effect=checkpoint)
        mutations = []

        async def invoke(action, pawn, *args, timeout):
            self.assertEqual(pawn, PAWN)
            if action == tape.CONVERT:
                self.assertTrue(host.transitioning and host.native_checkpoint_unsafe)
                blank, product = args
                self.assertEqual(blank, BLANK)
                self.assertNotEqual(product, blank)
                mutations.append(product)
                proof = tape.parse_exchange(offer(action, "exchanged", BLANK, product))
            else:
                self.assertEqual(action, tape.PREFLIGHT)
                proof = tape.parse_exchange(offer(blank=BLANK if not mutations else "-"))
            return {**game, "native_mosby_tape": proof}

        bridge = SimpleNamespace(command=AsyncMock(side_effect=invoke))
        first = await tape.convert(host, bridge, CHARACTER, BLANK, OPERATION)
        self.assertEqual(first["effect"], "stone_maze_tape_exchanged")
        self.assertEqual(mutations, [first["filled_guid"]])
        self.assertEqual([frames for _, frames in commits], [frozenset(), frozenset({CHARACTER})])
        self.assertEqual(commits[-1][0][tape.EXCHANGES][BLANK], host.party[tape.EXCHANGES][BLANK])
        self.assertFalse(host.transitioning or host.native_checkpoint_unsafe or host.stopping)

        host.party = party.load_party(commits[-1][0])
        again = await tape.convert(host, bridge, CHARACTER, BLANK, OPERATION)
        self.assertEqual((again["effect"], again["filled_guid"]), ("stone_maze_tape_already_exchanged", first["filled_guid"]))
        with self.assertRaisesRegex(ValueError, "operation_id_conflict"):
            await tape.convert(host, bridge, CHARACTER, OTHER, OPERATION)
        self.assertEqual((len(commits), len(mutations)), (2, 1))

    async def test_deferred_gear_and_xp_awarded_only_to_exact_bound_owned_saves(self):
        slot = native_host_save.new_slot()
        frame = {"slot": slot, "map": tape.SCIENTIST_SHOP}
        host = SimpleNamespace(native_profile=True, native_guest=True, last_game={"map": tape.STONE_MAZE, "native_guest_xp_ready": True})
        host.native_recipients = lambda: {CHARACTER: {"pawn": PAWN, "connected": True}, "sibling": {"connected": False}}
        state = party.new_party()
        for owner in (CHARACTER, "sibling"):
            party.add_player(state, owner)
        state[tape.REWARDS] = {"escrow": frame, "template_guid": ARMOR, "pending": {CHARACTER: FILLED, "sibling": OTHER}}

        with (patch.object(tape, "_reward", new_callable=AsyncMock) as grant,
              patch.object(tape.native_rewards, "project", new_callable=AsyncMock) as project,
              patch.object(native_host_save, "verify_record") as verify):
            granted = await tape._deliver(host, None, state)
        self.assertEqual(granted, {CHARACTER})
        grant.assert_awaited_once_with(host, None, tape.GRANT, PAWN, slot, ARMOR, FILLED, memory=False)
        project.assert_awaited_once_with(host, None, state, [(CHARACTER, PAWN, 3)])
        verify.assert_called_once_with(host.native_profile, frame)
        self.assertEqual(state[tape.REWARDS]["pending"], {"sibling": OTHER})
        self.assertNotIn(tape.native_rewards.PENDING, state["players"]["sibling"])

    async def test_pending_quest_gear_stays_in_memory_until_a_real_checkpoint(self):
        slot = native_host_save.new_slot()
        frame = {"slot": slot, "map": tape.SCIENTIST_SHOP, "pending": True}
        game = {"state": "hosting", "map": tape.STONE_MAZE, "native_guest_xp_ready": True}
        host = SimpleNamespace(native_profile=True, native_guest=True, last_game=game)
        members = {CHARACTER: {"pawn": PAWN, "connected": True}, "other": {"connected": False}}
        host.native_recipients = lambda: members
        state = party.new_party()
        for owner in members:
            party.add_player(state, owner)
        state[tape.REWARDS] = {"escrow": frame, "template_guid": ARMOR, "pending": {CHARACTER: FILLED, "other": OTHER}}
        bridge = SimpleNamespace(command=AsyncMock(return_value=game))
        with (patch.object(tape, "_reward", new_callable=AsyncMock) as grant,
              patch.object(tape.native_wire_quest, "_retain_reward_guest", AsyncMock(return_value=True)) as retain,
              patch.object(tape.native_rewards, "project", new_callable=AsyncMock),
              patch.object(native_host_save, "verify_record") as verify,
              patch.object(native_host_save, "record", side_effect=AssertionError("unexpected disk access"))):
            delivered = await tape._deliver(host, bridge, state, defer_save=True)
        self.assertEqual(delivered, {CHARACTER})
        self.assertEqual(state[tape.REWARDS]["pending"], {"other": OTHER})
        self.assertEqual(state[tape.REWARDS]["escrow"], frame)
        retain.assert_awaited_once_with(host, bridge, CHARACTER, PAWN)
        grant.assert_awaited_once_with(host, bridge, tape.GRANT, PAWN, slot, ARMOR, FILLED, memory=True)
        verify.assert_not_called()
        bridge.command.assert_not_awaited()

        persisted = {"slot": slot, "map": tape.SCIENTIST_SHOP, "sha256": "f" * 64}
        with patch.object(native_host_save, "record", return_value=persisted) as record:
            updated, frames = await native_loot.flush_pending_escrows(host, bridge, state)
        bridge.command.assert_awaited_once_with("native-loot-flush-escrows", slot)
        record.assert_called_once_with(host.native_profile, slot, tape.SCIENTIST_SHOP, flush=True)
        self.assertEqual(updated[tape.REWARDS]["escrow"], persisted)
        self.assertEqual(frames, {(tape.REWARDS, None): persisted})
        self.assertEqual(state[tape.REWARDS]["escrow"], frame)


    def test_wire_proofs_bind_world_actor_op_and_recipe(self):
        self.assertEqual(tape.parse_exchange(offer())["give"], {"class_path": tape.BLANK_CLASS, "quantity": 1, "cash": 0})
        unqualified = offer()
        unqualified[-1] = "1"
        with self.assertRaisesRegex(ValueError, "station exchange proof"):
            tape.parse_exchange(unqualified)
        with self.assertRaisesRegex(ValueError, "station exchange proof"):
            tape.parse_exchange(offer(tape.CONVERT, "exchanged", BLANK, BLANK))
        template = ["native-mosby-readings", tape.READINGS_PREFLIGHT, tape.SCIENTIST_SHOP, PAWN, tape.BOARD, tape.MOSBY_QUEST,
                    "-", "-", "-", "1", "1", "100", "100", "2", "2", "0", "0"]
        reputations = [["native-mosby-readings-rep", str(index), "0", "0", "0", "0", "0", "0"] for index in tape.FACTIONS]
        self.assertIsNone(tape.parse_readings(template, reputations)["objective_guid"])
        with self.assertRaisesRegex(ValueError, "Missing Stone Maze faction"):
            tape.parse_readings(template, reputations[:2])
        with self.assertRaisesRegex(ValueError, "Scientist Shop Stone Maze contract proof"):
            tape.parse_readings([*template[:12], "7600", *template[13:]], reputations)


if __name__ == "__main__":
    unittest.main()
