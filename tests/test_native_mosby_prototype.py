import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_dialogue as dialogue, party


class MosbyPrototypeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = dialogue.PROFILES[dialogue.SCIENTIST_SHOP]
        self.game = {"state": "hosting", "map": self.profile.map}
        self.progress = dict(zip((*dialogue.PROGRESS_KEYS, *dialogue.PROTOTYPE_KEYS),
                                 (1, 1, 0, 2, 7500, 3, 0, 0, 0, None)))
        members = {name: {"connected": True, "pawn": f"Player_FP_C {name}"} for name in ("first", "second")}
        state = party.new_party()
        for name in members:
            party.add_player(state, name)
        self.host = SimpleNamespace(native_recipients=lambda: members, party=state,
                                    control=SimpleNamespace(generation="generation", event=Mock()), zone_epoch="zone",
                                    last_game=self.game, last_seen=time.monotonic(), native_profile=True, native_guest=True,
                                    native_zones=True, transitioning=False, stopping=False, native_checkpoint_unsafe=False,
                                    pending_host_save=None, checkpoint_native=AsyncMock(side_effect=self.checkpoint))
        self.bridge = SimpleNamespace(command=AsyncMock(side_effect=self.stock))
        self.sessions = dialogue.DialogueSessions()
        self.writes, self.checkpoints = [], []

    async def checkpoint(self, bridge, candidate=None):
        self.assertIs(bridge, self.bridge)
        self.checkpoints.append((self.progress.copy(), party.load_party(candidate or self.host.party)))
        return len(self.checkpoints)

    def stock_fields(self, pawn, connection, effect):
        fields = ["native-dialogue-stock", self.profile.map, self.profile.npc, self.profile.controller, pawn, connection]
        progress = ["native-dialogue-progress", self.profile.map, pawn,
                    *("-" if value is None else str(value) for value in self.progress.values()), effect]
        lines = [["native-dialogue-line", row, str(index), f"line {index}"] for row, (count, _) in self.profile.rows.items()
                 for index in range(1, count + 1)]
        choices = [["native-dialogue-choice", row, str(index), f"choice {index}"] for row in self.profile.rows
                   for index in dialogue.available_ordinals(self.profile, row, self.progress)]
        return fields, lines, choices, progress

    async def stock(self, command, pawn, *args, timeout):
        connection = f"NetConnection {pawn}@1"
        effect = "read"
        if command != dialogue.READ:
            self.assertTrue(self.host.transitioning and self.host.native_checkpoint_unsafe)
            _, _, _, before = self.stock_fields(pawn, connection, effect)
            if command == dialogue.READINGS_OPEN:
                self.assertEqual(args, (connection, *before[3:-1]))
                self.assertEqual((self.progress["quest_step"], self.progress["stone_maze_readings"]), (-10, 1))
                self.progress.update(stone_maze_readings=2, topic_micro_drill=1)
                effect = "readings_opened"
            else:
                self.assertEqual(command, dialogue.COMMIT)
                row, ordinal, *values = args
                self.assertEqual(tuple(values), (connection, *before[3:-1]))
                self.assertEqual((row, self.progress["topic_micro_drill"], self.progress["mosby_drill_test"]),
                                 (dialogue.EQUIPMENT, 1, 0))
                effect = dialogue.EFFECTS[(row, int(ordinal))]
                self.progress["prototype_step" if ordinal == "2" else "topic_drill_lab"] = 1
            self.writes.append((pawn, effect))
        return {**self.game, "native_dialogue_stock": dialogue.parse_stock(*self.stock_fields(pawn, connection, effect))}

    async def act(self, character, reply=None, ordinal=None, operation=None):
        action, target = (dialogue.OPEN, dialogue.MOSBY) if reply is None else (dialogue.CHOOSE, f"{reply['token']}@{ordinal}")
        result = await self.sessions.act(self.host, self.bridge, character, action, target, operation or uuid.uuid4().hex)
        return result["native_dialogue"]

    async def test_readings_phase_and_issue_apply_once_for_two_guests(self):
        before = await self.act("first", await self.act("first"), 4)
        with self.assertRaisesRegex(ValueError, "choice_unavailable"):
            await self.act("first", before, 7)
        self.assertFalse(self.writes)
        self.progress.update(quest_step=-10, stone_maze_readings=1)

        readings = await self.act("first")
        self.assertEqual((readings["row"], readings["effect"], readings["progress"]["stone_maze_readings"]),
                         (dialogue.READINGS, "readings_opened", 2))
        first_offer = await self.act("first", readings, 1)
        second_questions = await self.act("second", await self.act("second"), 4)
        second_offer = await self.act("second", second_questions, 7)
        self.assertEqual(len(dialogue.render_text(first_offer, self.profile, dialogue.EQUIPMENT)), 8)
        self.assertNotEqual(first_offer["token"], second_offer["token"])
        with self.assertRaisesRegex(ValueError, "choice_expired"):
            await self.act("second", first_offer, 2)

        operation = uuid.uuid4().hex
        accepted = await self.act("first", first_offer, 2, operation)
        self.assertEqual(accepted["effect"], "prototype_drill_issued")
        self.assertEqual(await self.act("first", first_offer, 2, operation), accepted)
        self.assertEqual((await self.act("second", second_offer, 2))["effect"], "prototype_drill_already_present")
        self.assertIs(self.host.pending_host_save, False)
        self.host.checkpoint_native.assert_not_awaited()
        self.assertEqual(await self.host.checkpoint_native(self.bridge), 1)
        progress, durable = self.checkpoints[-1]
        self.assertEqual(self.writes, [("Player_FP_C first", "readings_opened"), ("Player_FP_C first", "prototype_drill_issued")])
        self.assertEqual((progress["prototype_step"], progress["cash"], progress["xp"]), (1, 7500, 3))
        self.assertEqual(durable["native_quests"][dialogue.PROTOTYPE_QUEST],
                         {"quest_id": dialogue.PROTOTYPE_QUEST, "step": 1, "actor": "first"})
        self.sessions = dialogue.DialogueSessions()
        self.host.control.generation = "restarted"
        self.assertEqual((await self.act("first"))["row"], dialogue.GREET)
        self.assertEqual(len(self.checkpoints), 1)

    async def test_source_earned_microdrill_topic_allows_stock_branch_without_stone_maze(self):
        self.progress.update(topic_micro_drill=1, quest_step=None)
        opened = await self.act("first")
        self.assertEqual(opened["row"], dialogue.GREET)
        offer = await self.act("first", await self.act("first", opened, 4), 7)
        later = await self.act("first", offer, 1)
        self.assertEqual((later["row"], later["effect"], later["progress"]["topic_drill_lab"]),
                         (dialogue.QUESTIONS, "topic_drill_lab", 1))
        self.assertEqual((self.progress["stone_maze_readings"], self.progress["prototype_step"]), (0, None))
        self.progress["mosby_drill_test"] = 1
        with self.assertRaisesRegex(ValueError, "choice_unavailable"):
            await self.act("first", later, 7)

    async def test_unverified_open_recovery_stops_without_checkpointing_partial_flags(self):
        self.progress.update(quest_step=-10, stone_maze_readings=1)

        async def partial(command, pawn, *args, timeout):
            if command == dialogue.READINGS_OPEN:
                self.progress["stone_maze_readings"] = 2
                raise RuntimeError("mutation_unverified_native_dialogue_mosby_readings:Topic_MicroDrill")
            return await self.stock(command, pawn, *args, timeout=timeout)

        self.bridge.command.side_effect = partial
        with self.assertRaisesRegex(RuntimeError, "mutation_unverified_native_dialogue"):
            await self.act("first")
        self.assertTrue(self.host.stopping and self.host.native_checkpoint_unsafe)
        self.host.checkpoint_native.assert_not_awaited()
        self.assertIsNone(self.host.pending_host_save)
