import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_guest_consumable as consumable, native_guest_drop_record, native_inventory, party
from tools.silverfish.travel import OBSERVATORY


EPOCH = "12" * 16
OPERATION = "34" * 16
GUID = "56" * 16
CHARACTER = "a1e57cdc-94b2-41aa-9f47-c3890eb691dd"
PAWN = f"Player_FP_C {OBSERVATORY}.Observitory_P:PersistentLevel.Player_FP_C_3"
MODDED = "/ExamplePack/Items/Item_Meal.Item_Meal_C"


def use_preview(item=MODDED, before="2", after="1", food="4.25"):
    return [consumable.PERSONAL_PREVIEW, OBSERVATORY, PAWN, GUID, item, before, after, food, "6", "-1", "2"]


class PersonalConsumableTests(unittest.IsolatedAsyncioTestCase):
    def test_preview_requires_one_unrecognized_typed_use_and_bounded_vitals(self):
        preview = consumable.parse_personal(use_preview(), preview=True)
        self.assertEqual(consumable.personal_target(EPOCH, preview), f"{EPOCH}@{GUID}|{MODDED}|2|4.25,6,-1,2")
        for malformed in (use_preview(item=consumable.MRE_CLASS), use_preview(after="0"),
                          use_preview(food="nan"), use_preview(food="200.01")):
            with self.assertRaises(ValueError):
                consumable.parse_personal(malformed, preview=True)
        game = {"state": "connected", "map": OBSERVATORY, "pawns": {"local": {"name": PAWN}}}
        request = [native_guest_drop_record.STOCK_ACTION_ROW, OBSERVATORY, PAWN, EPOCH, "1",
                   consumable.PERSONAL_ENTRY, GUID, "1", "0"]
        self.assertEqual(native_guest_drop_record.parse_stock_action(request, game)["mode"], "use")

    async def test_guest_forwards_only_a_current_preview_of_the_selected_guid(self):
        game = {"state": "connected", "map": OBSERVATORY, "pawns": {"local": {"name": PAWN}}}
        preview = consumable.parse_personal(use_preview(), preview=True)
        receipt = dict(preview, state="used", owner=CHARACTER, epoch=EPOCH)
        client = SimpleNamespace(last_game=game, host_session="host", zone_epoch=EPOCH, suspended=False,
                                 needs_travel=False, server_transitioning=False, invite={"character_id": CHARACTER},
                                 transport=SimpleNamespace(game_access=True),
                                 bridge=SimpleNamespace(command=AsyncMock(return_value={
                                     **game, consumable.PERSONAL_PREVIEW_KEY: preview})),
                                 gameplay_action=AsyncMock(return_value={consumable.PERSONAL_KEY: receipt}))
        self.assertEqual(await consumable.settle_personal_preview(client, OBSERVATORY, EPOCH, OPERATION, GUID, "1"), receipt)
        client.gameplay_action.assert_awaited_once_with(
            consumable.PERSONAL, OPERATION, consumable.personal_target(EPOCH, preview))
        client.gameplay_action.reset_mock()
        client.zone_epoch = "78" * 16
        with self.assertRaisesRegex(ValueError, "personal consumable preview is no longer current"):
            await consumable.settle_personal_preview(client, OBSERVATORY, EPOCH, OPERATION, GUID, "1")
        client.gameplay_action.assert_not_awaited()

    def host(self):
        state = party.new_party()
        party.add_player(state, CHARACTER)
        return SimpleNamespace(zone_epoch=EPOCH, native_guest=True, native_profile=True,
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                               last_game={"state": "hosting", "map": OBSERVATORY}, last_seen=time.monotonic(),
                               native_recipients=lambda: {CHARACTER: {"connected": True, "pawn": PAWN}},
                               party=state, native_pickup=Mock(), control=Mock(), checkpoint_native=AsyncMock())

    async def test_receipt_replay_applies_only_once_and_keeps_checkpoint_deferred(self):
        host = self.host()
        preview = consumable.parse_personal(use_preview(), preview=True)
        target = consumable.personal_target(EPOCH, preview)
        proof = consumable.parse_personal([consumable.PERSONAL, "used", OBSERVATORY, PAWN, GUID, MODDED,
                                          "2", "1", "41.25", "206", "219", "2"])
        bridge = SimpleNamespace(command=AsyncMock(return_value={**host.last_game, consumable.PERSONAL_KEY: proof}))
        with patch.object(native_inventory, "retain_guest_save", new_callable=AsyncMock, return_value=True) as retain:
            accepted = await consumable.use_personal(host, bridge, CHARACTER, target, OPERATION)
            replay = await consumable.use_personal(host, bridge, CHARACTER, target, OPERATION)
            with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
                await consumable.use_personal(host, bridge, CHARACTER, target.replace("4.25", "5"), OPERATION)
        self.assertEqual(accepted, replay)
        self.assertEqual(accepted[consumable.PERSONAL_KEY], dict(proof, owner=CHARACTER, epoch=EPOCH))
        retain.assert_awaited_once_with(host, bridge, CHARACTER, PAWN)
        bridge.command.assert_awaited_once_with(consumable.PERSONAL, PAWN, GUID, MODDED, "2", "4.25", "6", "-1", "2", timeout=6)
        host.checkpoint_native.assert_not_awaited()

    async def test_refuses_a_stale_visit_or_unretained_save_without_a_mutation(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock())
        preview = consumable.parse_personal(use_preview(), preview=True)
        with patch.object(native_inventory, "retain_guest_save", new_callable=AsyncMock, return_value=False) as retain:
            with self.assertRaisesRegex(party.RuleError, "personal_consumable_visit_changed"):
                await consumable.use_personal(host, bridge, CHARACTER, consumable.personal_target("78" * 16, preview), OPERATION)
            retain.assert_not_awaited()
            with self.assertRaisesRegex(party.RuleError, "guest_personal_consumable_refused_working_save_unavailable"):
                await consumable.use_personal(host, bridge, CHARACTER, consumable.personal_target(EPOCH, preview), OPERATION)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()
