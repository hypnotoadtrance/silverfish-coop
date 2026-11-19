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

    async def test_stock_hazop_equip_and_distinct_armor_swap_keep_exact_replacement(self):
        old_guid = "FE" * 16
        equip = self.report(profile="hazop", carry_before=6.0, carry_after=5.0, minimum=32.0)
        swap = dict(equip, status="swapped", before=2, after=2, carry_after=5.25,
                    replaced_profile="bomber", replaced_guid=old_guid)
        reverse = self.report(guid=old_guid, profile="bomber", status="swapped", before=2, after=2,
                              carry_before=5.25, carry_after=6.0, minimum=25.0, replaced_profile="hazop", replaced_guid=equip["guid"])
        for proof in (equip, swap, reverse):
            with self.subTest(proof=proof):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(return_value={"state": "hosting", "map": "area", "native_guest_armor": proof}))
                args = (host, bridge, "guest-id", "c" * 32, armor.ACTION, proof["guid"])
                result = await armor.perform(*args)
                self.assertTrue(armor.effects(result["native_guest_armor"]))
                self.assertEqual(result["native_guest_armor"]["class_path"], armor.PROFILES[proof["profile"]].class_path)
                self.assertEqual(result["native_guest_armor"].get("replaced_guid"), proof.get("replaced_guid"))
                self.assertEqual(await armor.perform(*args), result)
                self.assertEqual(bridge.command.await_count, 1)
        for replacement in (dict(replaced_profile="hazop"), dict(replaced_guid=equip["guid"]), dict(carry_after=5.0),
                            dict(replaced_class_path=armor.HAZOP_CLASS_PATH)):
            self.assertFalse(armor.effects({**swap, **replacement}))

    def test_legacy_and_exact_profile_status_frames(self):
        old = ["native-guest-armor", "area", "pawn", "AB" * 16, "equip", "1", "equipped", "2", "1", "6", "5.75", "25", ".2", ".2"]
        self.assertTrue(armor.effects(armor.parse(old)))
        new = [*old[:6], "swapped", "2", "2", "6", "5.25", "32", ".2", ".2", "hazop", "bomber", "FE" * 16]
        proof = armor.parse(new)
        self.assertEqual((proof["class_path"], proof["replaced_class_path"]), (armor.HAZOP_CLASS_PATH, armor.CLASS_PATH))
        self.assertTrue(armor.effects(proof))
        with self.assertRaisesRegex(ValueError, "Invalid exact stock armor profile"):
            armor.parse([*new[:-2], "bomber", "-"])
