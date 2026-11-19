import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_dialogue as dialogue
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
            events.write_text(f"dialogue-input\t1\t{pawn}\t-\t-\ndialogue-input\t2\t{pawn}\t{'a' * 32}\t2\n"
                              f"dialogue-input\t3\t{pawn}\t{'a' * 32}\t3", encoding="utf-8")
            self.assertEqual(bridge.native_dialogue_requests(), [(1, pawn, "-", "-")])
            with events.open("ab") as stream:
                stream.write(b"\n")
            self.assertEqual(bridge.native_dialogue_requests(), [(3, pawn, "a" * 32, "3")])
            self.assertEqual(bridge.native_dialogue_requests(), [])


if __name__ == "__main__":
    unittest.main()
