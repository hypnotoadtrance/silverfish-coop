from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

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
        self.authority.bridge = SimpleNamespace(directory=path, command=AsyncMock(), wait_for=AsyncMock())
        self.authority.bridge.command.side_effect = lambda *_args, **_kw: {
            **base, carry.AUTHORITY: {"pawn": PAWN, "lease": _args[2], "sequence": int(_args[4]),
                                     "state": "held", "reason": "stock_core_grabbed"},
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

        def stock_released(condition, timeout):
            self.assertEqual(self.aim_path.read_bytes(), f"1\t{LEASE}\t3\tdrop\n".encode())
            frame = self.host.last_game
            self.assertFalse(condition(frame))
            receipt = dict(frame[carry.AUTHORITY], state="released", reason="stock_core_released", sequence=3)
            self.assertFalse(condition({**frame, carry.AUTHORITY: dict(receipt, lease="f" * 32)}))
            released = {**frame, carry.AUTHORITY: receipt}
            self.assertTrue(condition(released))
            return released

        self.authority.bridge.wait_for.side_effect = stock_released
        await self.authority.operate(CHARACTER, drop)
        self.assertIsNone(self.authority.holder)
        self.assertFalse(self.aim_path.exists())
        with self.assertRaises(party.RuleError):
            await self.authority.operate(CHARACTER, grab)
        self.authority.bridge.command.assert_awaited_once()

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


class GuestInputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.session = "c" * 32
        self.scope = (PAWN, self.session, EPOCH)
        self.player = {"character_id": CHARACTER, "downed": False}
        self.public = {"state": "held", "holder": CHARACTER}
        self.game = {"state": "connected", "map": core.MAP, "pawns": {"local": {"name": PAWN}}}
        self.client = SimpleNamespace(
            last_game=self.game, gameplay={carry.PUBLIC: self.public, "players": {"guest": self.player}},
            transport=SimpleNamespace(connected=True, game_access=True), gameplay_seen=100.0,
            invite={"character_id": CHARACTER}, viewer_role="guest", expected_zone=core.MAP,
            zone_epoch=EPOCH, host_session=self.session,
            bridge=SimpleNamespace(directory=self.directory, input_namespace=uuid.uuid4()),
            gameplay_action=AsyncMock(), control=Mock(),
        )
        self.input = carry.GuestInput(self.client)
        self.start_existing_hold()

    def start_existing_hold(self):
        self.input.scope, self.input.lease = self.scope, LEASE
        self.input.held_since = self.input.last_renewal_at = 100.0
        self.input.holder_seen = True
        self.input.sequence = 1

    def sample(self, sequence):
        self.game[carry.AIM] = dict(pawn=PAWN, session=self.session, epoch=EPOCH,
                                    sequence=sequence, pitch=3.0, yaw=0.0)

    async def poll_at(self, timestamp):
        with patch.object(carry.time, "monotonic", return_value=timestamp):
            await self.input.poll(True)

    async def test_stale_gameplay_pauses_hold_and_requires_a_new_exact_sample_to_resume(self):
        self.sample(16)
        await self.poll_at(100.5)
        self.assertEqual(self.client.gameplay_action.await_count, 1)
        heartbeat = self.directory / carry.VIEW_LEASE
        initial_write = heartbeat.stat().st_mtime_ns
        self.sample(17)
        await self.poll_at(102.01)
        self.assertEqual((self.input.scope, self.input.lease), (self.scope, LEASE))
        self.assertEqual(self.client.gameplay_action.await_count, 1)
        self.assertEqual(heartbeat.stat().st_mtime_ns, initial_write)

        self.client.gameplay_seen = 102.02
        await self.poll_at(102.03)
        self.assertEqual(self.client.gameplay_action.await_count, 1)
        self.sample(18)
        await self.poll_at(102.2)
        self.assertEqual(self.client.gameplay_action.await_count, 2)
        action, _, target = self.client.gameplay_action.await_args.args
        self.assertEqual(action, carry.ACTION)
        self.assertEqual(carry.Request.parse(target), carry.Request(EPOCH, LEASE, "aim", 3, (3, 0)))

    async def test_fresh_gameplay_after_two_seconds_cannot_renew_the_expired_lease(self):
        self.sample(16)
        await self.poll_at(100.5)
        self.sample(17)
        await self.poll_at(102.01)
        self.assertEqual(self.input.lease, LEASE)
        self.client.gameplay_seen = 102.51
        self.sample(18)
        await self.poll_at(102.52)
        self.assertIsNone(self.input.lease)
        self.assertEqual(self.client.gameplay_action.await_count, 1)

    async def test_known_scope_loss_revokes_immediately_while_gameplay_is_stale(self):
        changes = (
            patch.object(self.client.transport, "connected", False),
            patch.object(self.client, "host_session", "d" * 32),
            patch.dict(self.game, map=core.MAP + "_changed"),
            patch.dict(self.game["pawns"]["local"], name=PAWN + "7"),
            patch.dict(self.player, downed=True),
            patch.dict(self.client.gameplay, wipe={"state": "active"}),
        )
        for change in changes:
            with self.subTest(change=change), change:
                self.start_existing_hold()
                self.input.last_renewal_at = 101.0
                await self.poll_at(102.01)
                self.assertIsNone(self.input.lease)
        self.client.gameplay_action.assert_not_awaited()

    async def test_fresh_release_report_ends_the_original_hold(self):
        self.sample(16)
        self.public.update(state="ready", holder=None)
        await self.poll_at(100.1)
        self.assertIsNone(self.input.lease)
        self.client.gameplay_action.assert_not_awaited()

    async def test_stale_gameplay_cannot_queue_a_new_grab(self):
        self.input.lease = None
        self.public.update(state="ready", holder=None)
        edge = ("native-mindcore-carry-toggle", "1", core.MAP, PAWN, core.CORE, self.session, EPOCH,
                "grab", "interact", "3.000", "0.000")
        (self.directory / carry.INPUT).write_text("\t".join(edge) + "\n", encoding="ascii")
        await self.poll_at(102.01)
        self.assertIsNone(self.input.scope)
        self.client.gameplay_seen = 102.02
        await self.poll_at(102.03)
        self.assertEqual(self.input.scope, self.scope)
        self.client.gameplay_action.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
