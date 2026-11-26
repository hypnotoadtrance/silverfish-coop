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
