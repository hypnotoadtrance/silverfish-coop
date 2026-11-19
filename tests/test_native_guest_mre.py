import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_guest_consumable as consume, party, runtime
from tools.silverfish.travel import BOROUGH


GUID = "ABCD" * 8
PAWN = f"Player_FP_C {BOROUGH}.Borough:PersistentLevel.Player_FP_C_1"
OPERATION = "1234" * 8


def report():
    return [consume.MRE, "used", BOROUGH, PAWN, GUID, "2", "1", "193", "200",
            "20", "26", "20", "250", "40", "37", "4", "1"]


class NativeMreTests(unittest.IsolatedAsyncioTestCase):
    def test_headless_fixture_authenticates_nonlethal_stock_damage(self):
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            bridge = runtime.Bridge(directory, None)
            prefix = f"schema\t1\nsession\thost\thosting\t{BOROUGH}\t1\t0\n"
            fixture = f"native-guest-mre-fixture\t{BOROUGH}\t{PAWN}\t17\t42\t0"
            status = directory / "status.tsv"
            status.write_text(prefix + fixture + "\t40\t250\t210\t250\t230\n", encoding="utf-8")
            self.assertEqual(bridge.snapshot()["native_guest_mre_fixture"]["damage"], {
                "amount": 40, "hp_before": 250, "hp_after": 210, "regen_before": 250, "regen_after": 230,
            })
            status.write_text(prefix + fixture + "\t40\t250\t210\t250\t250\n", encoding="utf-8")
            self.assertIsNone(bridge.snapshot())

    def test_stock_food_clamp_healing_and_guest_detox(self):
        proof = consume.parse_meal(report())
        self.assertEqual(proof["class_path"], consume.MRE_CLASS)
        self.assertEqual((proof["food_before"], proof["food_after"], proof["regen_after"]), (193, 200, 26))
        self.assertEqual((proof["intox_before"], proof["intox_after"], proof["host_intox_after"]), (40, 37, 4))
        for column, value in [(3, "Player_FP_C other"), (6, "0"), (8, "223"), (10, "35"), (11, "nan"),
                              (14, "36"), (15, "-1"), (16, "false")]:
            with self.subTest(column=column), self.assertRaises(ValueError):
                frame = report()
                frame[column] = value
                consume.parse_meal(frame)

    def test_canned_peaches_have_a_distinct_stock_proof(self):
        peaches = [consume.CANNED_PEACHES, "used", BOROUGH, PAWN, GUID, "2", "1", "17", "29",
                   "230", "230", "210", "250", "42", "41", "4", "0"]
        expected = consume.parse_meal(peaches)
        self.assertEqual(expected["class_path"], consume.CANNED_PEACHES_CLASS)
        self.assertEqual((expected["quantity_after"], expected["food_after"], expected["regen_after"],
                          expected["intox_after"]), (1, 29, 230, 41))
        for column, change in [(0, consume.MRE), (0, "native-guest-canned-tuna"), (8, "47"), (10, "240"), (14, "39")]:
            with self.subTest(column=column, change=change), self.assertRaises(ValueError):
                invalid = peaches.copy()
                invalid[column] = change
                consume.parse_meal(invalid)
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            bridge = runtime.Bridge(directory, None)
            status = directory / "status.tsv"
            prefix = f"schema\t1\nsession\thost\thosting\t{BOROUGH}\t1\t0\n"
            status.write_text(prefix + "\t".join(peaches) + "\n", encoding="utf-8")
            self.assertEqual(bridge.snapshot()["native_guest_canned_peaches"], expected)

    async def test_opt_in_transaction_requires_exact_written_guest_frame_and_accepts_replay(self):
        state = {"players": {"guest": {"downed": False, "native_inventory": {GUID: {"quantity": 2}}}},
                 "wipe": None, "pending_spawns": {}, "receipts": {}}
        host = SimpleNamespace(native_guest=True, guest_mre=False, native_profile=True, stopping=False, transitioning=False,
                               native_checkpoint_unsafe=False, last_seen=time.monotonic(), last_game={"state": "hosting", "map": BOROUGH},
                               native_recipients=lambda: {"guest": {"connected": True, "pawn": PAWN}},
                               party=state, checkpoint_native=AsyncMock(return_value=5), control=SimpleNamespace(event=Mock()),
                               native_pickup=SimpleNamespace(forget_committed_row=Mock()))
        snapshot = {"state": "hosting", "map": BOROUGH, "native_guest_mre": consume.parse_meal(report())}
        bridge = SimpleNamespace(command=AsyncMock(return_value=snapshot))
        with self.assertRaisesRegex(party.RuleError, "native_guest_mre_unavailable"):
            await consume.use(host, bridge, "guest", GUID, OPERATION, action=consume.MRE)
        bridge.command.assert_not_awaited()
        host.guest_mre = True
        with patch.object(party, "load_party", return_value=state):
            result = await consume.use(host, bridge, "guest", GUID, OPERATION, action=consume.MRE)
            self.assertEqual(await consume.use(host, bridge, "guest", GUID, OPERATION, action=consume.MRE), result)
        bridge.command.assert_awaited_once_with(consume.MRE, PAWN, GUID, timeout=6)
        self.assertEqual(host.checkpoint_native.await_args.kwargs["require_guest_frames"], frozenset({"guest"}))
        host.native_pickup.forget_committed_row.assert_not_called()
        self.assertFalse(host.native_checkpoint_unsafe)
