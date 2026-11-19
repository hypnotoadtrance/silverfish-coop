import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_authored_pickup, native_dust_cabinet as cabinet, party
from tools.silverfish.travel import RAIL_YARD


PAWN = f"Player_FP_C {RAIL_YARD}.RailYard_P:PersistentLevel.Player_FP_C_1"
EPOCH = "01" * 16


def snapshot(phase, state, *, pawn=PAWN, epoch="-"):
    fields = ["native-dust-cabinet", state, RAIL_YARD, cabinet.ACTOR, pawn, epoch]
    return {"state": phase, "map": RAIL_YARD, "pawns": {"local": {"name": pawn}},
            cabinet.KEY: cabinet.parse(fields, phase, RAIL_YARD)}


def host_stub(game):
    return SimpleNamespace(
        fixtures=True, native_zones=True, stopping=False, transitioning=False, native_checkpoint_unsafe=False,
        last_game=game, last_seen=time.monotonic(), zone_epoch=EPOCH, control=SimpleNamespace(event=Mock()),
        native_profile=True, party={"receipts": {}, "players": {}},
    )


class NativeDustCabinetTests(unittest.IsolatedAsyncioTestCase):
    def test_raw_status_has_exact_actor_phase_and_epoch(self):
        fields = ["native-dust-cabinet", "projected", RAIL_YARD, cabinet.ACTOR, PAWN, EPOCH]
        self.assertEqual(cabinet.parse(fields, "connected", RAIL_YARD)["epoch"], EPOCH)
        with self.assertRaises(ValueError):
            cabinet.parse(fields, "hosting", RAIL_YARD)
        for index, value in [(2, "OtherMap"), (3, cabinet.ACTOR[:-1] + "7"), (4, "Player_FP_C Fake"), (5, "-" * 32)]:
            with self.subTest(index=index), self.assertRaises(ValueError):
                cabinet.parse([*fields[:index], value, *fields[index + 1:]], "connected", RAIL_YARD)

    async def test_only_verified_open_host_publishes_and_actual_projection_matches_epoch(self):
        closed, opened = snapshot("hosting", "closed"), snapshot("hosting", "opened")
        host = host_stub(closed)
        bridge = SimpleNamespace(command=AsyncMock(return_value=closed), wait_for=AsyncMock(return_value=opened))
        self.assertEqual(cabinet.frame(host), {})
        self.assertEqual(await cabinet.open_fixture(host, bridge), {cabinet.KEY: opened[cabinet.KEY]})
        bridge.command.assert_awaited_once_with(cabinet.FIXTURE, PAWN, timeout=5)
        self.assertTrue(bridge.wait_for.call_args.args[0](opened))
        frame = cabinet.frame(host)
        self.assertEqual(frame, {"map": RAIL_YARD, "actor": cabinet.ACTOR, "epoch": EPOCH, "state": "opened"})
        host.fixtures = False
        self.assertEqual(cabinet.frame(host), {})

        projected = snapshot("connected", "projecting", epoch=EPOCH)
        client = SimpleNamespace(last_game=snapshot("connected", "closed"), zone_epoch=EPOCH, host_session="host-session",
                                 gameplay={cabinet.FRAME_KEY: frame, "test_fixtures": False}, server_transitioning=False,
                                 needs_travel=False, gameplay_seen=time.monotonic(), bridge=SimpleNamespace(command=AsyncMock(
                                     return_value=projected)))
        await cabinet.project_client(client)
        client.gameplay["test_fixtures"] = True
        client.gameplay[cabinet.FRAME_KEY]["epoch"] = "02" * 16
        await cabinet.project_client(client)
        client.gameplay[cabinet.FRAME_KEY]["epoch"] = EPOCH
        client.gameplay_seen -= 3
        await cabinet.project_client(client)
        client.bridge.command.assert_not_awaited()
        client.gameplay_seen = time.monotonic()
        await cabinet.project_client(client)
        client.bridge.command.assert_awaited_once_with(cabinet.PROJECT, EPOCH, cabinet.ACTOR, PAWN, timeout=3)
        self.assertIs(client.last_game, projected)

    async def test_direct_claim_refuses_closed_cabinet_before_native_work(self):
        host = host_stub(snapshot("hosting", "closed"))
        bridge = SimpleNamespace(command=AsyncMock())
        with self.assertRaisesRegex(party.RuleError, "native_authored_pickup_dust_cabinet_closed"):
            await native_authored_pickup.claim(host, bridge, "host", native_authored_pickup.DUST_FILTER, "12" * 16)
        bridge.command.assert_not_awaited()
