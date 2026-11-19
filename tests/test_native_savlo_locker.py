import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

from tools.silverfish import native_authored_pickup as pickup, native_savlo_locker as locker, party


PAWN = f"Player_FP_C {locker.TRENCH}.Trench_01_P:PersistentLevel.Player_FP_C_1"
EPOCH = "1a" * 16


def game(phase, state, *, locked=False, actor=locker.ACTOR, epoch="-"):
    fields = [locker.TAG, state, locker.TRENCH, actor, PAWN, epoch, str(int(locked))]
    return {"state": phase, "map": locker.TRENCH, "pawns": {"local": {"name": PAWN}},
            locker.KEY: locker.parse(fields, phase, locker.TRENCH)}


def host(game):
    return SimpleNamespace(native_zones=True, fixtures=True, stopping=False, transitioning=False, native_profile=True,
                           native_checkpoint_unsafe=False, last_game=game, last_seen=time.monotonic(), zone_epoch=EPOCH,
                           party={"receipts": {}, "players": {}}, control=Mock())


class SecureLockerTests(unittest.IsolatedAsyncioTestCase):
    def test_reports_cannot_replace_actor_pawn_or_epoch(self):
        fields = [locker.TAG, "projected", locker.TRENCH, locker.ACTOR, PAWN, EPOCH, "1"]
        proof = locker.parse(fields, "connected", locker.TRENCH)
        self.assertEqual((proof["locked"], proof["epoch"]), (True, EPOCH))
        for index, wrong in ((1, "opened"), (2, pickup.FORT_LETO), (3, locker.ACTOR[:-1] + "7"), (4, "foreign pawn"), (5, "-")):
            with self.subTest(index=index), self.assertRaises(ValueError):
                locker.parse([*fields[:index], wrong, *fields[index + 1:]], "connected", locker.TRENCH)
        self.assertEqual(pickup.select_source(pickup.FORT_LETO, None), pickup.FORT_LETO_LOCKER_KEY)
        self.assertEqual(pickup.select_source(locker.TRENCH, None), locker.SOURCE)

    async def test_first_press_unlocks_and_only_second_opens(self):
        locked, unlocked = game("hosting", "locked", locked=True), game("hosting", "unlocked")
        opening, opened = game("hosting", "opening"), game("hosting", "opened")
        authority = host(locked)
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[locked, unlocked, opening]), wait_for=AsyncMock(return_value=opened))
        with self.assertRaisesRegex(ValueError, "stock_savlo_locker_key_required"):
            await locker.press_fixture(authority, bridge)
        self.assertEqual(locker.frame(authority), {})
        self.assertEqual((await locker.press_fixture(authority, bridge))[locker.KEY]["state"], "unlocked")
        self.assertEqual(locker.frame(authority), {})
        self.assertEqual((await locker.press_fixture(authority, bridge))[locker.KEY]["state"], "opened")
        self.assertEqual(bridge.command.await_args_list, [call(locker.FIXTURE, PAWN, "locked", timeout=6),
                                                         call(locker.FIXTURE, PAWN, "locked", timeout=6),
                                                         call(locker.FIXTURE, PAWN, "unlocked", timeout=6)])
        self.assertTrue(bridge.wait_for.await_args.args[0](opened))
        authority.fixtures = False
        self.assertEqual(locker.frame(authority), {"map": locker.TRENCH, "actor": locker.ACTOR, "epoch": EPOCH, "state": "opened"})

    async def test_projection_requires_authenticated_fresh_exact_epoch_and_keeps_local_lock_state(self):
        authority = host(game("hosting", "opened"))
        replica = game("connected", "projected", locked=True, epoch=EPOCH)
        client = SimpleNamespace(last_game=game("connected", "locked", locked=True), zone_epoch=EPOCH, host_session="",
                                 needs_travel=False, server_transitioning=False, gameplay_seen=time.monotonic(),
                                 gameplay={locker.FRAME: locker.frame(authority)}, bridge=SimpleNamespace(command=AsyncMock(return_value=replica)))
        await locker.project_client(client)
        client.host_session = "authority"
        client.zone_epoch = "2b" * 16
        await locker.project_client(client)
        client.zone_epoch = EPOCH
        client.gameplay_seen -= 3
        await locker.project_client(client)
        client.bridge.command.assert_not_awaited()
        client.gameplay_seen = time.monotonic()
        await locker.project_client(client)
        client.bridge.command.assert_awaited_once_with("native-savlo-locker-project", EPOCH, locker.ACTOR, PAWN, timeout=3)
        self.assertIs(client.last_game, replica)
        await locker.project_client(client)
        self.assertEqual(client.bridge.command.await_count, 1)

    async def test_structured_keycard_claim_never_reaches_bridge_before_exact_stock_open(self):
        authority = host(game("hosting", "unlocked"))
        bridge = SimpleNamespace(command=AsyncMock())
        for row in (authority.last_game[locker.KEY], {"map": locker.TRENCH, "state": "opened", "pawn": PAWN,
                                                        "actor": locker.ACTOR[:-1] + "7"}, {}):
            authority.last_game[locker.KEY] = row
            with self.assertRaisesRegex(party.RuleError, "native_authored_pickup_savlo_locker_closed"):
                await pickup.claim(authority, bridge, "host", locker.SOURCE, "b" * 32)
        bridge.command.assert_not_awaited()
