import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tools.silverfish import link, native_guest_armor as armor, party


class NativeGuestArmorTests(unittest.IsolatedAsyncioTestCase):
    def host(self):
        players = party.new_party()
        party.add_player(players, "guest-id")
        return SimpleNamespace(
            native_guest=True, native_zones=True, native_profile=True, stopping=False, fixtures=False,
            transitioning=False, native_checkpoint_unsafe=False, party=players, last_seen=time.monotonic(),
            last_game={"state": "hosting", "map": "area"}, checkpoint_native=AsyncMock(),
            native_recipients=lambda: {"guest-id": {"connected": True, "pawn": "exact pawn"}},
        )

    def report(self, **changes):
        return dict({"map": "area", "pawn": "exact pawn", "guid": "AB" * 16, "action": "equip",
                     "ok": True, "status": "equipped", "before": 2, "after": 1,
                     "carry_before": 1.5, "carry_after": 1.25, "minimum": 25, "slash": .2, "acid": .2}, **changes)

    async def test_equip_requires_exact_frame_and_replays_without_second_mutation(self):
        host = self.host()
        proof = self.report()
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "area", "native_guest_armor": proof,
        }))
        args = (host, bridge, "guest-id", "a" * 32, armor.ACTION, proof["guid"])
        equipped = await armor.perform(*args)
        bridge.command.assert_awaited_once_with(armor.ACTION, "exact pawn", proof["guid"], timeout=5)
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({"guest-id"})})
        self.assertEqual(equipped["native_guest_armor"], dict(proof, class_path=armor.CLASS_PATH))
        self.assertEqual(await armor.perform(*args), equipped)
        self.assertEqual(bridge.command.await_count, 1)
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertGreaterEqual(link.checkpoint_action_timeout(
            {"action": armor.ACTION, "operation_id": "a" * 32, "target": proof["guid"]}
        ), 15)

    async def test_worn_slot_refusal_is_clean_and_projection_mismatch_is_unsafe(self):
        for proof in (self.report(ok=False, status="native_guest_armor_slot_occupied"),
                      self.report(carry_after=1.5), self.report(pawn="different pawn")):
            with self.subTest(proof=proof):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(return_value={
                    "state": "hosting", "map": "area", "native_guest_armor": proof,
                }))
                with self.assertRaises(party.RuleError if not proof["ok"] else RuntimeError):
                    await armor.perform(host, bridge, "guest-id", "b" * 32, armor.ACTION, proof["guid"])
                self.assertEqual(host.stopping, proof["ok"])
                self.assertEqual(host.native_checkpoint_unsafe, proof["ok"])
                self.assertEqual(host.party["receipts"], {})
                host.checkpoint_native.assert_not_awaited()

    async def test_no_receipt_is_exposed_if_detached_checkpoint_fails(self):
        host = self.host()
        host.checkpoint_native.side_effect = RuntimeError("native guest frame missing")
        proof = self.report()
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "area", "native_guest_armor": proof,
        }))
        with self.assertRaisesRegex(RuntimeError, "native guest frame missing"):
            await armor.perform(host, bridge, "guest-id", "a" * 32, armor.ACTION, proof["guid"])
        self.assertTrue(host.stopping)
        self.assertTrue(host.native_checkpoint_unsafe)
        self.assertEqual(host.party["receipts"], {})
