import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_guest_consumable, native_guest_weapon, party


class NativeGuestInventoryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def host(self):
        player_party = party.new_party()
        party.add_player(player_party, "character")
        return SimpleNamespace(
            native_guest=True, native_profile=True, fixtures=False, transitioning=False,
            stopping=False, native_checkpoint_unsafe=False, last_seen=time.monotonic(),
            last_game={"state": "hosting", "map": "zone"},
            native_recipients=lambda: {"character": {"connected": True, "pawn": "exact pawn"}},
            party=player_party, native_pickup=Mock(), control=Mock(),
            checkpoint_native=AsyncMock(),
        )

    async def test_stale_binding_denies_weapon_request_without_stopping_host(self):
        for action, target in (("fire", "1" * 32), ("equip", "1" * 32), ("reload", "equipped")):
            with self.subTest(action=action):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(side_effect=RuntimeError("guest_weapon_refused_detached_save")))
                with self.assertRaisesRegex(party.RuleError, "^guest_weapon_refused_detached_save$"):
                    await native_guest_weapon.perform(host, bridge, "character", "a" * 32,
                                                      f"native-guest-weapon-{action}", target)
                bridge.command.assert_awaited_once_with(f"native-guest-weapon-{action}", "exact pawn", target, timeout=5)
                self.assertFalse(host.stopping)
                self.assertFalse(host.native_checkpoint_unsafe)
                self.assertEqual(host.party["receipts"], {})
                host.checkpoint_native.assert_not_awaited()

    async def test_missing_acknowledgment_keeps_host_checkpoint_unsafe(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(side_effect=TimeoutError("missing acknowledgment")))
        with self.assertRaises(TimeoutError):
            await native_guest_weapon.perform(host, bridge, "character", "a" * 32,
                                              "native-guest-weapon-fire", "1" * 32)
        self.assertTrue(host.stopping)
        self.assertTrue(host.native_checkpoint_unsafe)
        host.checkpoint_native.assert_not_awaited()

    async def test_failed_native_shot_readback_keeps_host_checkpoint_unsafe(self):
        host = self.host()
        guid = "1" * 32
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "zone",
            "native_guest_weapon": {"map": "zone", "action": "fire", "pawn": "exact pawn", "guid": guid,
                                    "ok": False, "status": "mutation_unverified_guest_weapon_shot"},
        }))
        with self.assertRaisesRegex(RuntimeError, "mutation_unverified_guest_weapon_shot"):
            await native_guest_weapon.perform(host, bridge, "character", "a" * 32, "native-guest-weapon-fire", guid)
        self.assertTrue(host.stopping)
        self.assertTrue(host.native_checkpoint_unsafe)
        host.checkpoint_native.assert_not_awaited()

    async def test_verified_weapon_shot_requires_exact_guest_checkpoint(self):
        guid, operation = "1" * 32, "a" * 32
        proof = {"map": "zone", "action": "fire", "pawn": "exact pawn", "guid": guid, "ok": True,
                 "status": "equipped", "actor_ammo": 5, "stored_ammo": 5, "before": 6, "after": 5, "projectiles": 1}
        for missing in (False, True):
            with self.subTest(missing=missing):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(return_value={
                    "state": "hosting", "map": "zone", "native_guest_weapon": proof,
                }))
                if missing:
                    host.checkpoint_native.side_effect = RuntimeError("Required native guest frame is unavailable")
                    with self.assertRaisesRegex(RuntimeError, "Required native guest frame"):
                        await native_guest_weapon.perform(host, bridge, "character", operation,
                                                          "native-guest-weapon-fire", guid)
                    self.assertTrue(host.stopping)
                    self.assertTrue(host.native_checkpoint_unsafe)
                    self.assertNotIn("character", host.party["receipts"])
                else:
                    result = await native_guest_weapon.perform(host, bridge, "character", operation,
                                                               "native-guest-weapon-fire", guid)
                    self.assertEqual(result["native_guest_weapon"], proof)
                    self.assertEqual(host.party["receipts"]["character"][0]["id"], operation)
                    self.assertFalse(host.native_checkpoint_unsafe)
                self.assertEqual(host.checkpoint_native.await_args.kwargs,
                                 {"require_guest_frames": frozenset({"character"})})

    async def test_weapon_inspection_does_not_checkpoint(self):
        host = self.host()
        guid = "1" * 32
        proof = {"map": "zone", "action": "inspect", "pawn": "exact pawn", "guid": guid, "ok": True,
                 "status": "backpack", "actor_ammo": -1, "stored_ammo": 6}
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "zone", "native_guest_weapon": proof,
        }))
        self.assertEqual(await native_guest_weapon.perform(host, bridge, "character", "a" * 32,
                                                            "native-guest-weapon-inspect", guid),
                         {"native_guest_weapon": proof})
        host.checkpoint_native.assert_not_awaited()

    async def test_verified_bandage_consumption_requires_exact_guest_checkpoint(self):
        guid, operation = "1" * 32, "b" * 32
        proof = {"state": "used", "map": "zone", "pawn": "exact pawn", "guid": guid,
                 "quantity_before": 2, "quantity_after": 1, "health_before": 25, "health_after": 25,
                 "regen_before": 25, "regen_after": 50, "max_health": 100, "hemophilia": False}
        for missing in (False, True):
            with self.subTest(missing=missing):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(return_value={
                    "state": "hosting", "map": "zone", "native_guest_bandage": proof,
                }))
                if missing:
                    host.checkpoint_native.side_effect = (5, RuntimeError("Required native guest frame is unavailable"))
                    with self.assertRaisesRegex(RuntimeError, "Required native guest frame"):
                        await native_guest_consumable.use(host, bridge, "character", guid, operation)
                    self.assertTrue(host.stopping)
                    self.assertTrue(host.native_checkpoint_unsafe)
                    self.assertNotIn("character", host.party["receipts"])
                    host.native_pickup.forget_committed_row.assert_not_called()
                else:
                    result = await native_guest_consumable.use(host, bridge, "character", guid, operation)
                    self.assertEqual(result["native_guest_bandage"], proof)
                    self.assertEqual(host.party["receipts"]["character"][0]["id"], operation)
                    self.assertFalse(host.native_checkpoint_unsafe)
                    host.native_pickup.forget_committed_row.assert_called_once_with("character", guid)
                before, after = host.checkpoint_native.await_args_list
                self.assertEqual(before.args, (bridge,))
                self.assertEqual(before.kwargs, {})
                self.assertEqual(after.kwargs, {"require_guest_frames": frozenset({"character"})})
