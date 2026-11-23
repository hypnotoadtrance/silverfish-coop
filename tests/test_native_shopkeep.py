import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_dialogue as dialogue, native_finite_shop
from tools.silverfish.travel import BOROUGH


PAWN = "Player_FP_C exact"
CONNECTION = "NetConnection exact@1"


def parse(meet, effect, pawn=PAWN, connection=CONNECTION):
    frame = ["native-dialogue-stock", dialogue.SHOPKEEP, dialogue.SHOPKEEP_NPC, dialogue.SHOPKEEP_CONTROLLER, pawn, connection]
    lines, choices = [], []
    for row, (count, allowed) in dialogue.SHOPKEEP_ROWS.items():
        lines.extend(["native-dialogue-line", row, str(index), f"Stock line {index}"] for index in range(1, count + 1))
        choices.extend(["native-dialogue-choice", row, str(index), "View wares"] for index in allowed)
    progress = ["native-dialogue-progress", dialogue.SHOPKEEP, pawn, str(meet), "400", "2", effect]
    return dialogue.parse_stock(frame, lines, choices, progress)


class ShopKeepReceptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_exact_per_character_stock_response_grants_cash(self):
        game = {"state": "hosting", "map": dialogue.SHOPKEEP}
        members = {name: {"connected": True, "pawn": "Player_FP_C " + name} for name in ("first", "second")}
        host = SimpleNamespace(
            native_recipients=lambda: members, party={"players": {name: {"downed": False} for name in members},
                                                      "wipe": None, "pending_spawns": {}},
            control=SimpleNamespace(generation="boot", event=Mock()), zone_epoch="zone", native_zones=True,
            native_guest=True, native_profile=True, transitioning=False, stopping=False, native_checkpoint_unsafe=False,
            last_game=game, last_seen=time.monotonic(), pending_host_save=None, checkpoint_native=AsyncMock(),
        )
        meet, granted = 0, []
        connections = {value["pawn"]: f"NetConnection {name}@1" for name, value in members.items()}

        async def read(command, pawn, *arguments, timeout):
            nonlocal meet
            connection = connections[pawn]
            effect = "read"
            if command == dialogue.SHOPKEEP_OPEN:
                self.assertEqual(arguments, (connection, str(meet), "400", "2"))
                effect = "opened-repeat" if meet else "opened-first"
                meet = max(meet, 1)
            if command == dialogue.SHOPKEEP_CASH:
                self.assertEqual(arguments, (dialogue.SHOPKEEP_FIRST, "1", connection))
                effect = "cash-granted"
                granted.append(pawn)
            return {**game, "native_dialogue_stock": parse(meet, effect, pawn, connection)}

        bridge = SimpleNamespace(command=AsyncMock(side_effect=read))
        sessions = dialogue.DialogueSessions(shopkeep=True)

        async def send(name, verb, target, operation=None):
            return (await sessions.act(host, bridge, name, verb, target, operation or uuid.uuid4().hex))["native_dialogue"]

        operation = uuid.uuid4().hex
        first = await send("first", dialogue.OPEN, dialogue.SHOPKEEP_PROFILE, operation)
        self.assertEqual((first["row"], first["effect"], first["revision"]), (dialogue.SHOPKEEP_FIRST, "opened-first", None))
        self.assertIs(host.pending_host_save, False)
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertEqual(await send("first", dialogue.OPEN, dialogue.SHOPKEEP_PROFILE, operation), first)
        self.assertEqual(sum(call.args[0] == dialogue.SHOPKEEP_OPEN for call in bridge.command.await_args_list), 1)
        host.pending_host_save = None  # A normal checkpoint can clear the staged flag before the next visit.
        second = await send("second", dialogue.OPEN, dialogue.SHOPKEEP_PROFILE)
        self.assertEqual((second["row"], second["effect"], second["progress"]["meet"]), (dialogue.SHOPKEEP_ROOT, "opened-repeat", 1))
        self.assertIsNone(second["revision"])
        self.assertIsNone(host.pending_host_save)
        with self.assertRaisesRegex(ValueError, "choice_expired"):
            await send("second", dialogue.CHOOSE, first["token"] + "@1")
        with self.assertRaisesRegex(ValueError, "choice_unavailable"):
            await send("first", dialogue.CHOOSE, first["token"] + "@2")
        accepted = await send("first", dialogue.CHOOSE, first["token"] + "@1")
        self.assertEqual((accepted["row"], accepted["effect"]), (dialogue.SHOPKEEP_ROOT, "cash-granted"))
        self.assertEqual(granted, [members["first"]["pawn"]])
        host.checkpoint_native.assert_not_awaited()
        self.assertEqual(meet, 1)

    async def test_unverified_stock_open_stops_without_marking_a_save(self):
        game = {"state": "hosting", "map": dialogue.SHOPKEEP}
        host = SimpleNamespace(
            native_recipients=lambda: {"guest": {"connected": True, "pawn": PAWN}},
            party={"players": {"guest": {"downed": False}}, "wipe": None, "pending_spawns": {}},
            control=SimpleNamespace(generation="boot", event=Mock()), zone_epoch="zone", native_zones=True,
            native_guest=True, native_profile=True, transitioning=False, stopping=False, native_checkpoint_unsafe=False,
            last_game=game, last_seen=time.monotonic(), pending_host_save=None, checkpoint_native=AsyncMock(),
        )
        unverified = parse(1, "opened-first")
        unverified["progress"]["cash"] += 1
        bridge = SimpleNamespace(command=AsyncMock(side_effect=(
            {**game, "native_dialogue_stock": parse(0, "read")},
            {**game, "native_dialogue_stock": unverified},
        )))
        sessions = dialogue.DialogueSessions(shopkeep=True)
        with self.assertRaisesRegex(RuntimeError, "first-meet flag did not verify"):
            await sessions.act(host, bridge, "guest", dialogue.OPEN, dialogue.SHOPKEEP_PROFILE, uuid.uuid4().hex)
        self.assertTrue(host.native_checkpoint_unsafe)
        self.assertTrue(host.stopping)
        self.assertFalse(host.transitioning)
        self.assertIsNone(host.pending_host_save)
        host.checkpoint_native.assert_not_awaited()
        host.control.event.assert_not_called()

    async def test_merchant_handoff_requires_native_response_and_verified_view(self):
        number, token = 6, "1" * 32
        snapshot = {"map": dialogue.SHOPKEEP, "pawns": {"local": {"name": PAWN}}, "native_dialogue_view": {
            "state": "pending", "pawn": PAWN, "sequence": number, "token": token}}
        reported = {**snapshot, "native_shop_view": {
            "pawn": PAWN, "sequence": number, "map": dialogue.SHOPKEEP, "actor": dialogue.SHOPKEEP_COUNTER, "state": "pending"}}
        bridge = SimpleNamespace(native_dialogue_requests=Mock(return_value=[(number, PAWN, token, "1")]),
                                 input_namespace=uuid.uuid4(), command=AsyncMock(return_value=reported))
        view = dialogue.DialogueView(bridge, Mock(), shopkeep=True, shopkeep_view=True)
        reply = {"profile": dialogue.SHOPKEEP_PROFILE, "map": dialogue.SHOPKEEP, "npc": dialogue.SHOPKEEP_NPC,
                 "row": dialogue.SHOPKEEP_ROOT, "state": "open", "token": "2" * 32, "effect": "cash-granted",
                 "lines": ["Stock line"], "choices": [{"ordinal": 2, "text": "View wares"}]}
        action = AsyncMock(return_value={"native_dialogue": reply})
        self.assertEqual(await view.sync(snapshot, "zone", True, action), reported)
        bridge.command.assert_awaited_once_with(dialogue.SHOPKEEP_VIEW, str(number), token, timeout=3)
        self.assertEqual(action.await_args.args[0::2], (dialogue.CHOOSE, token + "@1"))

    def test_section_requires_capability_and_rejects_malformed_meet(self):
        self.assertNotIn(dialogue.SHOPKEEP, dialogue.DialogueSessions().maps)
        self.assertNotIn(BOROUGH, native_finite_shop.MERCHANTS)
        self.assertEqual(parse(1, "opened-first")["progress"]["meet"], 1)
        with self.assertRaises(ValueError):
            parse(0, "opened-first")
