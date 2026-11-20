import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_package_handoff_journal as journal, party
from tools.silverfish.native_computer_board import MISSING_CONTACT
from tools.silverfish.native_host_dig_live import CUSTODY
from tools.silverfish.travel import BLACK_MARKET


HOST = f"Player_FP_C {BLACK_MARKET}.Shop_BlackMarket_01:PersistentLevel.Player_FP_C_1"
FIRST = "11aa22bb-33cc-44dd-88ee-9900aabbccdd"
SECOND = "22bb33cc-44dd-55ee-88ff-0011aabbccdd"
GUID = "A" * 32


class PackageJournalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.journal = journal.Journal()
        self.members = {FIRST: {"alias": "guest", "pawn": "guest pawn", "connected": True},
                        SECOND: {"alias": "guest2", "pawn": "second pawn", "connected": True}}
        squad = party.new_party()
        for character in ("host", FIRST, SECOND):
            party.add_player(squad, character)
        squad[CUSTODY] = {"state": "owned", "package_guid": GUID}
        squad["native_quests"] = {MISSING_CONTACT.quest_id: {"step": 1}}
        self.game = dict(mode="host", state="hosting", map=BLACK_MARKET, pawns={
            "local": {"name": HOST, "location": [0, 0, 0]},
            "guest": {"name": "guest pawn", "location": [10, 0, 0]},
            "guest2": {"name": "second pawn", "location": [20, 0, 0]},
        })
        self.host = SimpleNamespace(last_game=self.game, last_seen=time.monotonic(), stopping=False,
                                    transitioning=False, native_checkpoint_unsafe=False, party=squad,
                                    zone_epoch="a" * 32, control=SimpleNamespace(generation="b" * 32, event=Mock()),
                                    store=SimpleNamespace(character_names=lambda ids: dict.fromkeys(ids, "Courier")),
                                    roles=lambda: {"host": "host", "guest": FIRST, "guest2": SECOND},
                                    native_recipients=lambda: self.members)
        self.view("idle")
        self.bridge = SimpleNamespace(command=AsyncMock(return_value=self.game))

    def view(self, state, token="-", recipient=None):
        self.game.pop(journal.JOURNAL, None)
        self.game.pop(journal.INTENT, None)
        journal.parse([journal.JOURNAL_ROW, BLACK_MARKET, HOST, state, token], self.game)
        if recipient is not None:
            journal.parse([journal.INTENT_ROW, BLACK_MARKET, HOST, token, recipient], self.game)

    async def test_rebound_recipient_cannot_retarget_the_other_guest(self):
        await self.journal.poll(self.host, self.bridge)
        token = self.journal.offer.token
        self.bridge.command.assert_awaited_once_with("native-package-handoff-offer", token, GUID,
                                                     FIRST, "Courier (Guest 1)", SECOND, "Courier (Guest 2)", timeout=3)
        self.view("submitting", token, FIRST)
        self.members[FIRST]["pawn"] = "replacement pawn"
        self.game["pawns"]["guest"]["name"] = "replacement pawn"
        with patch.object(journal.dig, "handoff", new_callable=AsyncMock) as transfer:
            await self.journal.poll(self.host, self.bridge)
            transfer.assert_not_awaited()
        self.bridge.command.assert_awaited_with("native-package-handoff-result", token, "refused", timeout=3)

    async def test_retries_delivery_without_transferring_twice(self):
        await self.journal.poll(self.host, self.bridge)
        token = self.journal.offer.token
        self.view("submitting", token, SECOND)
        self.bridge.command.side_effect = [TimeoutError, self.game]
        with patch.object(journal.dig, "handoff", new_callable=AsyncMock) as transfer:
            await self.journal.poll(self.host, self.bridge)
            await self.journal.poll(self.host, self.bridge)
            transfer.assert_awaited_once_with(self.host, self.bridge, SECOND, journal_intent=self.game[journal.INTENT])
        self.bridge.command.assert_awaited_with("native-package-handoff-result", token, "transferred", timeout=3)

    async def test_reopened_journal_requires_a_fresh_offer(self):
        await self.journal.poll(self.host, self.bridge)
        previous = self.journal.offer.token
        self.view("idle")
        await self.journal.poll(self.host, self.bridge)
        self.assertNotEqual(previous, self.journal.offer.token)
        with self.assertRaises(ValueError):
            journal.parse([journal.INTENT_ROW, BLACK_MARKET, HOST, previous, FIRST], self.game)
