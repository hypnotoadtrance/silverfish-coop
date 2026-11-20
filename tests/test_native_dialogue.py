import tempfile
import time
import unittest
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_dialogue as dialogue, party
from tools.silverfish.runtime import Bridge


ROOT_LINES = [["native-dialogue-line", dialogue.ROOT, "1", "Looking for something?"]]
STORY_LINES = [["native-dialogue-line", dialogue.STORY, str(index), f"Story {index}"] for index in (1, 2, 3)]
CHOICES = [["native-dialogue-choice", row, str(index), label] for row, index, label in (
    (dialogue.ROOT, 1, "Not right now.."), (dialogue.ROOT, 3, "What's your story?"), (dialogue.STORY, 1, "I see..."))]


def stock_frame(pawn, connection):
    return ["native-dialogue-stock", dialogue.OBSERVATORY, dialogue.NPC, dialogue.CONTROLLER, pawn, connection]


class CastawayDialogueTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_authenticated_pawns_have_independent_and_live_bound_choices(self):
        members = {guest: {"connected": True, "pawn": f"Player_FP_C {guest}"} for guest in ("first", "second")}
        connections = {entry["pawn"]: f"NetConnection staged-{guest}@1" for guest, entry in members.items()}
        game = {"state": "hosting", "map": dialogue.OBSERVATORY}
        party = {"players": {guest: {"downed": False} for guest in members}, "pending_spawns": {}, "wipe": None}
        host = SimpleNamespace(native_recipients=lambda: members, party=party, control=SimpleNamespace(generation="host-generation"),
                               zone_epoch="first-epoch", last_game=game, last_seen=time.monotonic(), native_profile=True,
                               native_zones=True, native_guest=True, transitioning=False, stopping=False, native_checkpoint_unsafe=False)

        async def read(command, pawn, *, timeout):
            self.assertEqual(command, dialogue.READ)
            return {**game, "native_dialogue_stock": dialogue.parse_stock(stock_frame(pawn, connections[pawn]),
                                                                          ROOT_LINES + STORY_LINES, CHOICES)}

        bridge = SimpleNamespace(command=AsyncMock(side_effect=read))
        sessions = dialogue.DialogueSessions()

        async def request(guest, action, target, operation=None):
            return (await sessions.act(host, bridge, guest, action, target, operation or uuid.uuid4().hex))["native_dialogue"]

        first = await request("first", dialogue.OPEN, dialogue.PROFILE)
        second = await request("second", dialogue.OPEN, dialogue.PROFILE)
        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual(first["lines"], ["Looking for something?"])
        self.assertEqual([choice["ordinal"] for choice in first["choices"]], [1, 3])
        with self.assertRaisesRegex(ValueError, "native_dialogue_choice_expired"):
            await request("second", dialogue.CHOOSE, first["token"] + "@3")
        with self.assertRaisesRegex(ValueError, "native_dialogue_choice_unavailable"):
            await request("first", dialogue.CHOOSE, first["token"] + "@2")

        operation = uuid.uuid4().hex
        story = await request("first", dialogue.CHOOSE, first["token"] + "@3", operation)
        self.assertEqual((story["row"], story["lines"]), (dialogue.STORY, ["Story 1", "Story 2", "Story 3"]))
        self.assertNotEqual(story["token"], first["token"])
        self.assertEqual(await request("first", dialogue.CHOOSE, first["token"] + "@3", operation), story)
        with self.assertRaisesRegex(ValueError, "operation_id_conflict"):
            await request("first", dialogue.CHOOSE, first["token"] + "@1", operation)
        self.assertEqual((await request("second", dialogue.CHOOSE, second["token"] + "@1"))["state"], "closed")
        self.assertEqual(sessions.sessions["first"].row, dialogue.STORY)

        connections[members["first"]["pawn"]] = "NetConnection staged-first@2"
        with self.assertRaisesRegex(ValueError, "native_dialogue_choice_expired"):
            await request("first", dialogue.CHOOSE, story["token"] + "@1")
        host.transitioning = True
        with self.assertRaisesRegex(ValueError, "native_dialogue_guest_unavailable"):
            await request("first", dialogue.OPEN, dialogue.PROFILE)
        self.assertEqual(host.party, {"players": {"first": {"downed": False}, "second": {"downed": False}},
                                     "pending_spawns": {}, "wipe": None})

    async def test_guest_uses_current_pending_input_and_renders_host_stock_text(self):
        pawn, serial, epoch = "Player_FP_C local", 5, "correct-epoch"
        snapshot = {"map": dialogue.OBSERVATORY, "pawns": {"local": {"name": pawn}},
                    "native_dialogue_view": dict(state="pending", pawn=pawn, sequence=serial, row="-", token="-")}
        first = {"row": dialogue.ROOT, "token": "a" * 32, "state": "open", "profile": dialogue.PROFILE,
                 "npc": dialogue.NPC, "map": dialogue.OBSERVATORY, "lines": ["Verified live text"],
                 "choices": [{"ordinal": 1, "text": "Close"}, {"ordinal": 3, "text": "Story"}]}
        opened = {**snapshot, "native_dialogue_view": dict(state="open", pawn=pawn, sequence=serial,
                                                          row=dialogue.ROOT, token=first["token"])}
        bridge = SimpleNamespace(native_dialogue_requests=Mock(return_value=[(serial, pawn + "_sibling", "-", "-")]),
                                 command=AsyncMock(return_value=opened), input_namespace=uuid.uuid4())
        action = AsyncMock(return_value={"native_dialogue": first})
        view = dialogue.DialogueView(bridge, SimpleNamespace(event=Mock()))
        await view.sync(snapshot, epoch, True, action)
        action.assert_not_awaited()
        bridge.native_dialogue_requests.return_value = [(serial, pawn, "-", "-")]
        result = await view.sync(snapshot, epoch, True, action)
        self.assertEqual(result, opened)
        bridge.command.assert_awaited_once_with(dialogue.RENDER, str(serial), dialogue.ROOT, first["token"],
                                               "Verified live text", "Close", "Story", timeout=2)
        self.assertEqual(action.await_args.args[0::2], (dialogue.OPEN, dialogue.PROFILE))
        await view.sync(snapshot, "next-epoch", True, action)
        self.assertEqual(action.await_count, 1)

    def test_bridge_accepts_exact_stock_rows_and_only_complete_authorized_intents(self):
        pawn = "Player_FP_C test"
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            bridge = Bridge(directory, None)
            standard = [["schema", "1"], ["session", "host", "hosting", dialogue.OBSERVATORY, "1", "0"]]
            frame = stock_frame(pawn, "NetConnection exact@1")
            rows = standard + [frame] + ROOT_LINES + STORY_LINES + CHOICES
            status = directory / "status.tsv"
            status.write_text("\n".join("\t".join(row) for row in rows), encoding="utf-8")
            self.assertEqual(bridge.snapshot()["native_dialogue_stock"]["pawn"], pawn)
            rows.append(["native-dialogue-choice", dialogue.ROOT, "2", "Trade"])
            status.write_text("\n".join("\t".join(row) for row in rows), encoding="utf-8")
            self.assertIsNone(bridge.snapshot())

            events = directory / "native-dialogue-events"
            events.write_text(f"dialogue-input\t1\t{pawn}\t-\t-\ndialogue-input\t2\t{pawn}\t{'a' * 32}\t8\n"
                              f"dialogue-input\t3\t{pawn}\t{'a' * 32}\t3", encoding="utf-8")
            self.assertEqual(bridge.native_dialogue_requests(), [(1, pawn, "-", "-")])
            with events.open("ab") as stream:
                stream.write(b"\n")
            self.assertEqual(bridge.native_dialogue_requests(), [(3, pawn, "a" * 32, "3")])
            self.assertEqual(bridge.native_dialogue_requests(), [])


class MosbyDialogueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = dialogue.PROFILES[dialogue.SCIENTIST_SHOP]
        self.game = {"state": "hosting", "map": self.profile.map}
        self.members = {guest: {"connected": True, "pawn": f"Player_FP_C {guest}"} for guest in ("first", "second")}
        state = party.new_party()
        for guest in self.members:
            party.add_player(state, guest)
        self.host = SimpleNamespace(native_recipients=lambda: self.members, party=state, control=SimpleNamespace(generation="host-generation", event=Mock()),
                                    zone_epoch="first-epoch", last_game=self.game, last_seen=time.monotonic(), native_profile=True,
                                    native_zones=True, native_guest=True, transitioning=False, stopping=False, native_checkpoint_unsafe=False)
        self.progress = dict(zip(dialogue.PROGRESS_KEYS, (0, 0, 0, None, 250, 2)))
        self.checkpoints = []
        self.writes = []
        self.host.checkpoint_native = AsyncMock(side_effect=self.checkpoint)
        self.bridge = SimpleNamespace(command=AsyncMock(side_effect=self.stock))
        self.sessions = dialogue.DialogueSessions()

    async def checkpoint(self, _bridge, proposed=None):
        self.checkpoints.append((self.progress.copy(), deepcopy(proposed if proposed else self.host.party)))
        return len(self.checkpoints)

    async def stock(self, command, pawn, *arguments, timeout):
        effect = "read"
        connection = f"NetConnection {pawn}@1"
        if command == dialogue.COMMIT:
            row, ordinal, identity, *before = arguments
            self.assertTrue(self.host.transitioning and self.host.native_checkpoint_unsafe)
            self.assertEqual((identity, before), (connection, ["-" if self.progress[key] is None else str(self.progress[key])
                                                              for key in dialogue.PROGRESS_KEYS]))
            effect = dialogue.EFFECTS[(row, int(ordinal))]
            key = "quest_step" if effect == "stone_maze_issued" else effect
            self.assertIn(self.progress[key], (0, None))
            self.progress[key] = 1
            self.writes.append(effect)
        else:
            self.assertEqual(command, dialogue.READ)
        frame = ["native-dialogue-stock", self.profile.map, self.profile.npc, self.profile.controller, pawn, connection]
        progress = ["native-dialogue-progress", self.profile.map, pawn,
                    *("-" if self.progress[key] is None else str(self.progress[key]) for key in dialogue.PROGRESS_KEYS), effect]
        rows = dialogue.stock_rows(self.profile, self.progress)
        lines = [["native-dialogue-line", row, str(index), f"line {index}"]
                 for row, (count, _) in rows.items() for index in range(1, count + 1)]
        choices = [["native-dialogue-choice", row, str(index), f"choice {index}"] for row in rows
                   for index in dialogue.available_ordinals(self.profile, row, self.progress)]
        return {**self.game, "native_dialogue_stock": dialogue.parse_stock(frame, lines, choices, progress)}

    async def act(self, guest, reply=None, ordinal=None, operation=None):
        action, target = (dialogue.OPEN, dialogue.MOSBY) if reply is None else (dialogue.CHOOSE, f"{reply['token']}@{ordinal}")
        return (await self.sessions.act(self.host, self.bridge, guest, action, target, operation or uuid.uuid4().hex))["native_dialogue"]

    async def test_qualified_topics_and_shared_issue_checkpoint_only_once(self):
        first = await self.act("first", await self.act("first"), 4)
        second = await self.act("second", await self.act("second"), 4)
        self.assertEqual([choice["ordinal"] for choice in first["choices"]], [1, 3])
        with self.assertRaisesRegex(ValueError, "native_dialogue_choice_unavailable"):
            await self.act("first", first, 6)
        anomaly = await self.act("first", first, 3)
        self.assertEqual((anomaly["row"], anomaly["effect"], anomaly["revision"]), (dialogue.ANOMALIES, "topic_eather_drill", 2))
        self.assertEqual((self.checkpoints[0][0]["topic_eather_drill"], self.checkpoints[1][0]["topic_eather_drill"]), (0, 1))
        with self.assertRaisesRegex(ValueError, "native_dialogue_choice_unavailable"):
            await self.act("second", second, 5)
        other_anomaly = await self.act("second", second, 3)
        self.assertEqual(other_anomaly["effect"], "topic_already_known")
        first = await self.act("first", anomaly, 1)
        second = await self.act("second", other_anomaly, 1)
        self.assertEqual([choice["ordinal"] for choice in first["choices"]], [1, 3, 5])
        drill = await self.act("first", first, 5)
        other_drill = await self.act("second", second, 5)
        first = await self.act("first", await self.act("first", drill, 1), 6)
        second = await self.act("second", await self.act("second", other_drill, 1), 6)
        self.assertEqual((first["row"], second["row"]), (dialogue.OFFER, dialogue.OFFER))
        operation = uuid.uuid4().hex
        issued = await self.act("first", first, 2, operation)
        self.assertEqual((issued["state"], issued["effect"], issued["progress"]["quest_step"]), ("closed", "stone_maze_issued", 1))
        self.assertEqual(await self.act("first", first, 2, operation), issued)
        self.assertEqual((await self.act("second", second, 2))["effect"], "stone_maze_already_present")
        record = {"quest_id": dialogue.MOSBY_QUEST, "step": 1, "actor": "first"}
        self.assertEqual(self.checkpoints[-1][1]["native_quests"][dialogue.MOSBY_QUEST], record)
        self.assertEqual(self.writes, ["topic_eather_drill", "topic_drill_data", "stone_maze_issued"])
        self.assertEqual(len(self.checkpoints), 6)

        saved_progress, saved_party = self.checkpoints[-1]
        self.progress = saved_progress.copy()
        self.host.party, self.host.control.generation = party.load_party(saved_party), "restarted-generation"
        self.sessions = dialogue.DialogueSessions()
        with self.assertRaisesRegex(ValueError, "native_dialogue_choice_expired"):
            await self.act("first", first, 2, operation)
        restarted = await self.act("second")
        self.assertEqual(restarted["progress"], dict(zip(dialogue.PROGRESS_KEYS, (1, 1, 0, 1, 250, 2))))
        self.assertEqual(self.host.party["native_quests"][dialogue.MOSBY_QUEST], record)

    async def test_unverified_mosby_mutation_stops_before_a_second_checkpoint(self):
        questions = await self.act("first", await self.act("first"), 4)

        async def failed(command, pawn, *arguments, timeout):
            if command == dialogue.COMMIT:
                raise RuntimeError("mutation_unverified_native_dialogue_mosby:Topic_EatherDrill")
            return await self.stock(command, pawn, *arguments, timeout=timeout)

        self.bridge.command.side_effect = failed
        with self.assertRaisesRegex(RuntimeError, "mutation_unverified_native_dialogue_mosby"):
            await self.act("first", questions, 3)
        self.assertEqual(len(self.checkpoints), 1)
        self.assertTrue(self.host.stopping and self.host.native_checkpoint_unsafe)
        self.assertFalse(self.host.transitioning)


if __name__ == "__main__":
    unittest.main()
