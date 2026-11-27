import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from tools.silverfish import native_flare
from tools.silverfish.travel import OBSERVATORY


class StockFlareReceiptTests(unittest.TestCase):
    def test_discovered_projectile_receipt_accepts_a_mounted_content_class(self):
        item = "/ModContent/Grenades/CustomFlash.CustomFlash_C"
        path = f"{OBSERVATORY}.Observitory_P:PersistentLevel.CustomProjectile_C_25"
        row = [native_flare.ACTION, OBSERVATORY, "guest", "A" * 32, "0", "2", "1", item, path, "12000"]
        proof = native_flare.parse(row, "hosting", OBSERVATORY)
        self.assertEqual((proof["class_path"], proof["projectile"], proof["fuse_ms"]), (item, path, 12000))
        row[8] = path.replace("PersistentLevel", "DifferentLevel")
        with self.assertRaises(ValueError):
            native_flare.parse(row, "hosting", OBSERVATORY)

    def test_each_stock_class_verifies_only_its_own_projectile(self):
        for item, projectile in native_flare.CLASSES.items():
            with self.subTest(item=item):
                path = f"{OBSERVATORY}.Observitory_P:PersistentLevel.{projectile}_25"
                row = [native_flare.ACTION, OBSERVATORY, "guest", "A" * 32, "0", "2", "1", item, path]
                if item == native_flare.PIPE_BOMB:
                    row.append("1250")
                proof = native_flare.parse(row, "hosting", OBSERVATORY)
                self.assertEqual((proof["before"], proof["after"], proof["projectile"]), (2, 1, path))
                row[8] = path.replace(projectile, "Unrecognized_Projectile_C")
                with self.assertRaises(ValueError):
                    native_flare.parse(row, "hosting", OBSERVATORY)

    def test_pipe_bomb_requires_its_stock_remaining_fuse(self):
        row = [native_flare.INPUT, OBSERVATORY, "guest", "77", "C" * 32, "0", native_flare.PIPE_BOMB]
        self.assertEqual(native_flare.parse([*row, "0"], "connected", OBSERVATORY)["fuse_ms"], 0)
        for extra in ([], ["-1"], ["0001"], ["5001"]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                native_flare.parse(row + extra, "connected", OBSERVATORY)
        row[-1] = "/Game/Classes/Equipment/Eqp_Bolt.Eqp_Bolt_C"
        with self.assertRaises(ValueError):
            native_flare.parse([*row, "400"], "connected", OBSERVATORY)

    def test_bolt_uses_the_stack_throw_path_without_accepting_a_flare_receipt(self):
        bolt = "/Game/Classes/Equipment/Eqp_Bolt.Eqp_Bolt_C"
        row = [native_flare.INPUT, OBSERVATORY, "guest", "77", "C" * 32, "1", bolt]
        self.assertEqual(native_flare.parse(row, "connected", OBSERVATORY),
                         {"map": OBSERVATORY, "pawn": "guest", "sequence": 77,
                          "guid": "C" * 32, "toss": "1", "class_path": bolt})
        wrong_projectile = f"{OBSERVATORY}.Observitory_P:PersistentLevel.Flare_Projectile_BP_C_25"
        with self.assertRaisesRegex(ValueError, "Invalid stock flare throw receipt"):
            native_flare.parse([native_flare.ACTION, OBSERVATORY, "guest", "C" * 32, "1", "1", "0",
                                bolt, wrong_projectile], "hosting", OBSERVATORY)

    def test_receipt_requires_exactly_one_consumed_unit_and_current_map(self):
        item, projectile = next(iter(native_flare.CLASSES.items()))
        path = f"{OBSERVATORY}.Observitory_P:PersistentLevel.{projectile}_25"
        row = [native_flare.ACTION, OBSERVATORY, "guest", "B" * 32, "0", "2", "0", item, path]
        with self.assertRaises(ValueError):
            native_flare.parse(row, "hosting", OBSERVATORY)
        row[6] = "1"
        with self.assertRaises(ValueError):
            native_flare.parse(row, "hosting", "/Game/AnotherMap")


class StockFlareRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_committed_retry_reuses_projectile_and_does_not_throw_again(self):
        operation, epoch, guid = "a" * 32, "b" * 32, "C" * 32
        selection = f"{epoch}@{guid}@0"
        result = {native_flare.PROOF: {"before": 2, "after": 1, "projectile": "original"}}
        host = SimpleNamespace(native_guest=True, native_profile=True, stopping=False, transitioning=False,
            native_checkpoint_unsafe=False, zone_epoch=epoch, last_seen=time.monotonic(), last_game={"state": "hosting"},
            native_recipients=lambda: {"guest": {"connected": True, "pawn": "stock_pawn"}},
            party={"players": {"guest": {"downed": False}}, "pending_spawns": {}, "receipts": {"guest": [
                {"id": operation, "request": [native_flare.ACTION, selection, None], "result": result},
            ]}})
        bridge = SimpleNamespace(command=AsyncMock())
        self.assertEqual(await native_flare.perform(host, bridge, "guest", selection, operation), result)
        bridge.command.assert_not_awaited()
