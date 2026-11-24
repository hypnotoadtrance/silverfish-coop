import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish.native_wipe_view import WipeView


class WipeViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = "01234567-89ab-4cde-8f01-23456789abcd"
        self.wipe_id = "a" * 32
        self.guid = "B" * 32
        self.area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
        self.pawn = "Player_FP_C Observatory"
        self.game = {"map": self.area, "pawns": {"local": {"name": self.pawn}}}
        self.draft = {"id": self.wipe_id, "native_inventory_ready": True, "roster": ["host", self.owner],
                      "choices": {}, "keep_slots": 1, "deadline": None}
        self.scope = self.wipe_id, self.area, self.pawn, self.owner, "0"
        self.command = AsyncMock(side_effect=self.bridge_command)
        self.view = WipeView(SimpleNamespace(command=self.command), Mock(), self.owner)
        self.view.read_events = Mock(return_value=[])
        self.submit = AsyncMock()
        self.fetch = AsyncMock(return_value={"native_wipe_items": {
            "wipe_id": self.wipe_id, "owner": self.owner, "count": 1, "forced_count": 0, "offset": 0,
            "items": [{"guid": self.guid, "source": "backpack", "index": 0, "quantity": 1,
                       "stackable": False, "cannot_keep": False,
                       "class_path": "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"}],
        }})

    async def bridge_command(self, action, *fields, timeout):
        self.assertEqual(fields[:4], self.scope[:4])
        if action == "native-wipe-view-render":
            return {"native_wipe_view": dict(zip(("id", "map", "pawn", "owner"), self.scope[:4]),
                                            offset=0, total=1, screen="stock")}
        return self.game

    async def sync(self, draft):
        await self.view.sync(self.game, draft, self.fetch, self.submit)
        calls = self.command.call_args_list[:]
        self.command.reset_mock()
        return calls

    async def test_first_confirmation_keeps_the_current_chooser_leased(self):
        first, = await self.sync(self.draft)
        self.assertEqual(first.args[10], "-")
        self.view.read_events.return_value = [
            ["native-wipe-view-submit", "1", *self.scope[:4], "1", self.guid],
        ]
        result, = await self.sync(self.draft)
        self.assertEqual(result.args, ("native-wipe-view-result", *self.scope[:4], "accepted"))
        self.assertEqual(self.submit.await_args.args[1], {self.guid: 1})

        self.view.read_events.return_value = [
            ["native-wipe-view-submit", "2", *self.scope[:4], "0"],
        ]
        self.draft["choices"][self.owner] = {self.guid: 1}
        self.view.next_lease = 0
        lease, = await self.sync(self.draft)
        self.assertEqual(lease.args, ("native-wipe-view-lease", *self.scope))
        self.assertEqual(self.submit.await_count, 1)
        self.assertEqual(self.fetch.await_count, 1)

        close, = await self.sync(None)
        self.assertEqual(close.args, ("native-wipe-view-clear", *self.scope[:4]))
        self.assertIsNone(self.view.scope)

    async def test_reopening_projects_the_authoritative_choice(self):
        self.draft["choices"][self.owner] = {self.guid: 1}
        render, = await self.sync(self.draft)
        self.assertEqual(render.args[10], self.guid)
        self.assertEqual(self.view.scope, self.scope)
        self.submit.assert_not_awaited()

    async def test_empty_confirmation_does_not_reopen_for_selection(self):
        self.draft["choices"][self.owner] = {}
        render, = await self.sync(self.draft)
        self.assertEqual(render.args[10], "0")
        self.assertEqual(self.view.confirmed, {})
