from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock
import uuid

from tools.silverfish import native_guest_drop as drop, native_guest_drop_record as record, native_inventory


MAP = record.OBSERVATORY
PAWN = f"Player_FP_C {MAP}.Observitory_P:PersistentLevel.Player_FP_C_3"
GUID = "A" * 32


class GuestInventoryDropTests(unittest.IsolatedAsyncioTestCase):
    def client(self, *, snapshot_count=9, phase="dropping", rebind_during_read=False):
        view = record.parse_view(
            [record.VIEW_ROW, "5", MAP, PAWN, GUID, record.ITEM, phase, "9"], "connected", MAP,
        )
        game = {"state": "connected", "map": MAP, "pawns": {"local": {"name": PAWN}}, record.VIEW: view,
                "native_inventory_view": {"state": "rendered", "sequence": 5, "map": MAP, "pawn": PAWN, "offset": 16}}
        stack = SimpleNamespace(admission="a" * 32)
        client = SimpleNamespace(
            last_game=game, zone_epoch="b" * 32, host_session="c" * 32, stack_drop_projection=stack,
            last_inventory_drop=0, stopping=False, suspended=False, transport=SimpleNamespace(game_access=True),
            needs_travel=False, server_transitioning=False, gameplay_seen=time.monotonic(),
            gameplay={record.CAPABILITY: 1}, invite={"character_id": "exact-owner"}, control=Mock(),
            bridge=SimpleNamespace(command=AsyncMock(return_value=game.copy()), snapshot=Mock(return_value=game.copy()),
                                   input_namespace=uuid.NAMESPACE_URL),
        )
        client.inventory_drop_scope = drop.view_identity(client, 5, MAP, PAWN)

        async def gameplay_action(action, operation, target):
            if action == "native-inventory-list":
                if rebind_during_read:
                    stack.admission = "d" * 32
                return {"native_inventory": {
                    "owner": "exact-owner", "offset": 16, "total": 17, "items": [
                        dict(guid=GUID, class_path=record.ITEM, quantity=snapshot_count, source="backpack", stackable=True,
                             important=False),
                    ],
                }}
            return {drop.RESULT: dict(state="dropped", drop_id=operation, owner="exact-owner", guid=GUID,
                                      quantity=9, class_path=record.ITEM, map=MAP, location=[5., 20., 30.])}
        client.gameplay_action = AsyncMock(side_effect=gameplay_action)
        return client

    async def test_delete_only_prepares_and_home_settles_exact_complete_row_once(self):
        client = self.client(phase="confirming")
        await drop.service_view(client, True)
        client.gameplay_action.assert_not_awaited()
        client.last_game[record.VIEW]["state"] = "dropping"
        await drop.service_view(client, True)
        preflight, mutation = client.gameplay_action.await_args_list
        self.assertEqual(preflight.args, ("native-inventory-list", mutation.args[1], "16"))
        self.assertEqual((mutation.args[0], mutation.args[-1]), (drop.DROP, f"{GUID}|9"))
        client.bridge.command.assert_awaited_once_with(record.VIEW_RESULT, "5", MAP, GUID, "dropped", timeout=2)
        await drop.service_view(client, True)
        self.assertEqual(client.gameplay_action.await_count, 2)

    async def test_changed_count_and_reauthenticated_scope_refuse_before_bank(self):
        for options in ({"snapshot_count": 8}, {"rebind_during_read": True}):
            with self.subTest(options=options):
                client = self.client(**options)
                await drop.service_view(client, True)
                client.gameplay_action.assert_awaited_once()
                self.assertEqual(client.gameplay_action.await_args.args[0], "native-inventory-list")
                client.bridge.command.assert_awaited_once_with(record.VIEW_RESULT, "5", MAP, GUID, "refused", timeout=2)

    async def test_previous_render_admission_cannot_read_or_drop(self):
        client = self.client()
        client.stack_drop_projection.admission = "d" * 32
        await drop.service_view(client, True)
        client.gameplay_action.assert_not_awaited()
        client.bridge.command.assert_awaited_once_with(record.VIEW_RESULT, "5", MAP, GUID, "refused", timeout=2)

    def test_stack_and_view_limits(self):
        row = dict(guid=GUID, class_path=record.ITEM, quantity=1, source="backpack", stackable=True, important=False)
        self.assertTrue(drop.droppable(row))
        for replacement in ({"quantity": 201}, {"important": True}, {"source": "equipment"}, {"stackable": False}):
            self.assertFalse(drop.droppable({**row, **replacement}))
        pistol = dict(row, class_path=record.LIGHT_PISTOL, stackable=False)
        self.assertTrue(drop.droppable(pistol))
        self.assertFalse(drop.droppable(dict(pistol, quantity=7)))
        self.assertFalse(drop.droppable(dict(pistol, important=True)))
        report = [record.VIEW_ROW, "5", MAP, PAWN, GUID, record.ITEM, "dropping", "201"]
        with self.assertRaises(ValueError):
            record.parse_view(report, "connected", MAP)

    def test_native_eligibility_replaces_the_list_for_stock_backpack_items(self):
        stock = "/Game/Classes/Items/Item_PocketWatch.Item_PocketWatch_C"
        row = dict(guid=GUID, class_path=stock, quantity=1, source="backpack",
                   stackable=False, important=False, droppable=True)
        self.assertFalse(drop.droppable(row))
        self.assertTrue(drop.droppable(row, generic=True))
        for changes in ({"droppable": False}, {"important": True}, {"quantity": 2}, {"source": "armor"},
                        {"class_path": "/Game/Classes/Items/Item_PocketWatch.Different_C"}):
            self.assertFalse(drop.droppable({**row, **changes}, generic=True))

    def test_take_ammo_requires_the_entire_expected_magazine(self):
        game = {"state": "connected", "map": MAP, "pawns": {"local": {"name": PAWN}}}
        envelope = [record.STOCK_ACTION_ROW, MAP, PAWN, "b" * 32, "5", "take-ammo", GUID]
        for rounds in ("1", "12"):
            intent = record.parse_stock_action([*envelope, rounds, rounds], game)
            self.assertEqual((intent["mode"], intent["count"], intent["expected"]), ("unload", int(rounds), int(rounds)))
            self.assertEqual(native_inventory.take_ammo_target(f"{GUID}|{rounds}"), (GUID, int(rounds), int(rounds)))
        for amount, expected in (("0", "0"), ("1", "7"), ("13", "13"), ("2", "0")):
            with self.assertRaises(ValueError):
                record.parse_stock_action([*envelope, amount, expected], game)
        for target in (f"{GUID}|0", f"{GUID}|13", f"{GUID}|1|2", f"{'0' * 32}|1"):
            self.assertIsNone(native_inventory.take_ammo_target(target))
