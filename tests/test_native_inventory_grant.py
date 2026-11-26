import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_inventory_grant, party
from tools.silverfish.travel import OBSERVATORY


CHARACTER, GUID = "guest-character", "A" * 32
EPOCH, OPERATION = "b" * 32, "c" * 32
CLASS = "/Game/Classes/Weapons/Wep_Pistol.Wep_Pistol_C"
PAWN = "exact enrolled pawn"


class NativeInventoryGrantTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_settles_grant_once_and_rejects_conflicting_replay(self):
        frame = {"map": OBSERVATORY, "state": "hosting", "ack": {"message": "added"}}
        host = SimpleNamespace(
            party=party.new_party(), control=Mock(), guest_inventory_grants={},
            zone_epoch=EPOCH, last_game=frame.copy(), native_guest=True, native_profile=True,
            native_recipients=lambda: {CHARACTER: {"pawn": PAWN, "connected": True}},
            stopping=False, transitioning=False, native_checkpoint_unsafe=False, checkpoint_native=AsyncMock(),
        )
        bridge = SimpleNamespace(local_bridge=object(), command=AsyncMock(return_value=frame))
        target = f"{EPOCH}@{GUID}@{CLASS}@(Ammo=7)"
        with patch("tools.silverfish.native_inventory.retain_guest_save", AsyncMock(return_value=True)):
            first = await native_inventory_grant.accept(host, bridge, CHARACTER, target, OPERATION)
            self.assertEqual(first, await native_inventory_grant.accept(host, bridge, CHARACTER, target, "d" * 32))
            with self.assertRaisesRegex(party.RuleError, "refused_guid"):
                await native_inventory_grant.accept(host, bridge, CHARACTER, target.replace("Ammo=7", "Ammo=8"), "e" * 32)
            host.zone_epoch = "f" * 32
            with self.assertRaisesRegex(party.RuleError, "scope_changed"):
                await native_inventory_grant.accept(host, bridge, CHARACTER, target, OPERATION)
        bridge.command.assert_awaited_once_with(native_inventory_grant.APPLY, PAWN, GUID, CLASS, "(Ammo=7)", timeout=3)
        host.checkpoint_native.assert_not_awaited()

    async def test_stack_grants_on_same_guid_settle_by_operation(self):
        frame = {"map": OBSERVATORY, "state": "hosting", "ack": {"message": "increased"}}
        host = SimpleNamespace(
            party=party.new_party(), control=Mock(), guest_inventory_grants={},
            zone_epoch=EPOCH, last_game=frame.copy(), native_guest=True, native_profile=True,
            native_recipients=lambda: {CHARACTER: {"pawn": PAWN, "connected": True}},
            stopping=False, transitioning=False, native_checkpoint_unsafe=False, checkpoint_native=AsyncMock(),
        )
        bridge = SimpleNamespace(local_bridge=object(), command=AsyncMock(return_value=frame))
        stack = f"{EPOCH}@{GUID}@{CLASS}@stack:"
        next_operation = "e" * 32
        with patch("tools.silverfish.native_inventory.retain_guest_save", AsyncMock(return_value=True)):
            first = await native_inventory_grant.accept(host, bridge, CHARACTER, stack + "4", OPERATION)
            second = await native_inventory_grant.accept(host, bridge, CHARACTER, stack + "2", next_operation)
            self.assertEqual(first[native_inventory_grant.RESULT]["units"], 4)
            self.assertEqual(second[native_inventory_grant.RESULT]["units"], 2)
            self.assertEqual(first, await native_inventory_grant.accept(host, bridge, CHARACTER, stack + "4", OPERATION))
            with self.assertRaisesRegex(party.RuleError, "refused_guid"):
                await native_inventory_grant.accept(host, bridge, CHARACTER, stack + "2", OPERATION)
            with self.assertRaisesRegex(party.RuleError, "invalid_native_inventory_grant"):
                await native_inventory_grant.accept(host, bridge, CHARACTER, stack + "2147483648", "f" * 32)
        self.assertEqual([call.args for call in bridge.command.await_args_list], [
            (native_inventory_grant.APPLY, PAWN, GUID, CLASS, "stack:4", OPERATION),
            (native_inventory_grant.APPLY, PAWN, GUID, CLASS, "stack:2", next_operation),
        ])
        host.checkpoint_native.assert_not_awaited()
