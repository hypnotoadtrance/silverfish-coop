from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_mindcore as core, native_mindcore_carry as carry, party


CHARACTER, OTHER = "guest", "other"
PAWN = "Player_FP_C " + core.LEVEL + "Player_FP_C_66"
EPOCH, LEASE = "a" * 32, "b" * 32


class AuthorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = Path(self.enterContext(TemporaryDirectory()))
        base = {"state": "hosting", "map": core.MAP, carry.CAPABILITY: core.CORE,
                core.SCENE: {"status": 0, "installed": False}, core.REPLICATION: {"state": "active"}}
        self.host = SimpleNamespace(last_game=base, last_seen=time.monotonic(), stopping=False, transitioning=False,
                                    native_checkpoint_unsafe=False, zone_epoch=EPOCH, control=Mock(),
                                    party={"players": {CHARACTER: {"downed": False}}})
        self.native = {CHARACTER: {"pawn": PAWN, "connected": True}}
        self.host.native_recipients = lambda: self.native
        self.authority = carry.Authority(self.host)
        self.authority.bridge = SimpleNamespace(directory=path, command=AsyncMock())
        self.authority.bridge.command.side_effect = lambda *_args, **_kw: {
            **base, carry.AUTHORITY: {"pawn": PAWN, "lease": _args[2], "sequence": int(_args[4]),
                                     "state": "held" if _args[3] == "grab" else "released",
                                     "reason": "stock_core_grabbed" if _args[3] == "grab" else "stock_core_released"},
        }
        self.aim_path = path / carry.AUTHORITY_AIM

    async def test_hold_aim_and_drop_are_bound_to_one_authenticated_guest_and_epoch(self):
        grab = carry.Request(EPOCH, LEASE, "grab", 1, (3, 24))
        await self.authority.operate(CHARACTER, grab)
        aim = carry.Request(EPOCH, LEASE, "aim", 2, (2.5, -15.1))
        self.assertFalse(self.authority.aim(OTHER, aim)["ok"])
        self.assertFalse(self.aim_path.exists())
        self.assertTrue(self.authority.aim(CHARACTER, aim)["ok"])
        self.assertFalse(self.authority.aim(CHARACTER, aim)["ok"])
        self.assertEqual(self.aim_path.read_bytes(), f"1\t{LEASE}\t2\t2.500\t-15.100\n".encode())
        drop = carry.Request(EPOCH, LEASE, "drop", 3)
        with self.assertRaises(party.RuleError):
            await self.authority.operate(OTHER, drop)
        await self.authority.operate(CHARACTER, drop)
        self.assertIsNone(self.authority.holder)
        self.assertFalse(self.aim_path.exists())
        with self.assertRaises(party.RuleError):
            await self.authority.operate(CHARACTER, grab)
        self.assertEqual(self.authority.bridge.command.await_count, 2)

    async def test_loss_of_identity_revokes_aim_before_the_native_watchdog_finishes(self):
        await self.authority.operate(CHARACTER, carry.Request(EPOCH, LEASE, "grab", 1, (3, 24)))
        self.assertTrue(self.authority.aim(CHARACTER, carry.Request(EPOCH, LEASE, "aim", 2, (3, 24)))["ok"])
        self.native[CHARACTER]["connected"] = False
        self.authority.maintain()
        self.assertFalse(self.aim_path.exists())
        self.native[CHARACTER]["connected"] = True
        self.assertFalse(self.authority.aim(CHARACTER, carry.Request(EPOCH, LEASE, "aim", 3, (3, 24)))["ok"])
        self.host.last_game[carry.AUTHORITY]["state"] = "released"
        self.authority.maintain()
        self.assertIsNone(self.authority.holder)

    async def test_unverified_stock_effect_on_initial_grab_stops_checkpointing(self):
        self.authority.bridge.command.side_effect = RuntimeError("mutation_unverified_stock_core_grab")
        with self.assertRaisesRegex(RuntimeError, "mutation_unverified"):
            await self.authority.operate(CHARACTER, carry.Request(EPOCH, LEASE, "grab", 1, (3, 24)))
        self.assertTrue(self.host.native_checkpoint_unsafe and self.host.stopping)

    def test_wire_request_has_exact_shape_and_rejects_unbounded_or_stale_fields(self):
        valid = f"{EPOCH}@{LEASE}@aim@2@1.250@-125.960"
        self.assertEqual(carry.Request.parse(valid).aim, (1.25, -125.96))
        for target in (valid.replace("2@1.250", "0@1.250"), valid.replace("1.250", "nan"),
                       valid.replace("-125.960", "-189"), valid + "@714", valid.replace("@aim@", "@drop@")):
            with self.subTest(target=target), self.assertRaises(ValueError):
                carry.Request.parse(target)


if __name__ == "__main__":
    unittest.main()
