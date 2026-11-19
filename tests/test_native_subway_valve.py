import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_host_save, native_subway_valve as valve, native_wire_board, party
from tools.silverfish.campaign import CampaignFormatError
from tools.silverfish.travel import BOROUGH


EPOCH = "12" * 16
OTHER = "34" * 16
PAWN = f"Player_FP_C {valve.SUBWAY}.Subway_01_P:PersistentLevel.Player_FP_C_10"
BOROUGH_PAWN = f"Player_FP_C {BOROUGH}.Borough:PersistentLevel.Player_FP_C_20"
CHARACTER = "2ab60fd6-873f-4e67-88cf-c51e9577098f"
TEMPLATE = ["AA" * 16, "BB" * 16]
ASSIGNED = ["CC" * 16, "DD" * 16]


def objective(epoch=EPOCH):
    return valve.parse_objective(["native-subway-valve-objective", valve.SUBWAY, valve.VALVE, valve.TRIGGER,
        PAWN, epoch, *map(str, (1, 2, 0, 1, 300, 300, 7, 7, 0, 20, 0, 200))], "hosting", valve.SUBWAY)


def snapshot(scene="present", verified=None):
    state = {"map": valve.SUBWAY, "state": "hosting", valve.SCENE: {"state": scene},
             "pawns": {"local": {"name": PAWN}}}
    if verified:
        state[valve.OBJECTIVE] = verified
    return state


def authority():
    candidate = party.new_party()
    party.add_player(candidate, "host")
    party.add_player(candidate, CHARACTER)
    candidate["native_quests"] = {valve.QUEST: {"quest_id": valve.QUEST, "step": 1}}
    return SimpleNamespace(native_guest=True, native_zones=True, native_profile=True, stopping=False, transitioning=False,
                           native_checkpoint_unsafe=False, last_game=snapshot(), last_seen=time.monotonic(), zone_epoch=EPOCH,
                           party=candidate, control=Mock(), checkpoint_native=AsyncMock(return_value=3),
                           native_recipients=lambda: {CHARACTER: {"pawn": PAWN, "connected": True}})


class FloodControlTests(unittest.IsolatedAsyncioTestCase):
    def test_board_and_objective_reject_wrong_actor_or_stock_delta(self):
        self.assertEqual(native_wire_board.fixture_selection(BOROUGH, valve.QUEST), (valve.QUEST,))
        with self.assertRaises(party.RuleError):
            native_wire_board.fixture_selection(valve.SUBWAY, valve.QUEST)
        self.assertEqual((objective()["epoch"], objective()["flag"]), (EPOCH, 1))
        fields = ["native-subway-valve-board", "turn-in", BOROUGH, BOROUGH_PAWN, valve.BOARD, "2", "-10", "1", "9", "9",
                  "7", "8", *TEMPLATE]
        self.assertEqual(valve.parse_board(fields, "hosting", BOROUGH)["bundle"], TEMPLATE)
        for index, invalid in ((1, "activate"), (2, valve.SUBWAY), (3, PAWN), (4, valve.BOARD + "0"), (13, TEMPLATE[0])):
            with self.subTest(index=index), self.assertRaises(ValueError):
                valve.parse_board([*fields[:index], invalid, *fields[index + 1:]], "hosting", BOROUGH)
        bad = ["native-subway-valve-objective", valve.SUBWAY, valve.VALVE, valve.TRIGGER, PAWN, EPOCH,
               *map(str, (1, 2, 0, 1, 300, 301, 7, 7, 0, 20, 0, 200))]
        with self.assertRaises(ValueError):
            valve.parse_objective(bad, "hosting", valve.SUBWAY)

    async def test_only_exact_current_visit_commits_the_stock_activation_once(self):
        host = authority()
        before = host.party
        proof = objective()
        closed = snapshot("closed", proof)
        bridge = SimpleNamespace(command=AsyncMock(return_value=snapshot("settling", proof)), wait_for=AsyncMock(return_value=closed))
        with self.assertRaisesRegex(party.RuleError, "stale_visit"):
            await valve.activate(host, bridge, CHARACTER, OTHER)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()
        receipt = await valve.activate(host, bridge, CHARACTER, EPOCH)
        self.assertEqual((receipt["quest_id"], receipt["actor"], receipt["step"], receipt["objective"]),
                         (valve.QUEST, CHARACTER, 2, proof))
        self.assertEqual(host.party["native_quests"][valve.QUEST]["objective"], proof)
        self.assertEqual(before["native_quests"][valve.QUEST]["step"], 1)
        bridge.command.assert_awaited_once_with(valve.ACTIVATE, PAWN, EPOCH, timeout=8)
        self.assertTrue(bridge.wait_for.await_args.args[0](closed))
        self.assertEqual(host.checkpoint_native.await_count, 2)
        self.assertEqual((await valve.activate(host, bridge, CHARACTER, EPOCH))["effect"], "native_subway_valve_already_closed")
        self.assertEqual(bridge.command.await_count, 1)

    async def test_unknown_post_dispatch_result_fences_the_current_save(self):
        host = authority()
        bridge = SimpleNamespace(command=AsyncMock(return_value=snapshot("settling", objective())),
                                 wait_for=AsyncMock(side_effect=asyncio.CancelledError))
        with self.assertRaises(asyncio.CancelledError):
            await valve.activate(host, bridge, "host", EPOCH)
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
        self.assertEqual(host.party["native_quests"][valve.QUEST]["step"], 1)
        self.assertEqual(host.checkpoint_native.await_count, 1)

    async def test_client_renders_only_verified_stock_completion_in_its_current_visit(self):
        local = {"state": "connected", "map": valve.SUBWAY, valve.SCENE: {"state": "present", "pawn": PAWN, "epoch": None},
                 "pawns": {"local": {"name": PAWN}}}
        journal = {"step": 2, "objective": objective()}
        after = {**local, valve.SCENE: {"state": "closed", "pawn": PAWN, "epoch": OTHER}}
        client = SimpleNamespace(last_game=local, zone_epoch=OTHER, host_session="", needs_travel=False,
                                 server_transitioning=False, gameplay_seen=time.monotonic(), bridge=SimpleNamespace(
                                     command=AsyncMock(return_value=after)), gameplay={"native_quests": {valve.QUEST: journal}})
        await valve.project_client(client)
        client.host_session = "verified"
        journal["objective"] = dict(objective(), valve=valve.VALVE + "_2")
        await valve.project_client(client)
        journal["objective"] = objective()
        client.gameplay_seen -= 4
        await valve.project_client(client)
        client.bridge.command.assert_not_awaited()
        client.gameplay_seen = time.monotonic()
        await valve.project_client(client)
        client.bridge.command.assert_awaited_once_with(valve.PROJECT, OTHER, PAWN, timeout=3)
        self.assertIs(client.last_game, after)
        await valve.project_client(client)
        self.assertEqual(client.bridge.command.await_count, 1)

    def test_pending_bundle_pins_the_escrow_and_rejects_shared_or_foreign_guids(self):
        frame = {"map": BOROUGH, "slot": "SFCoop_" + "1" * 32, "sha256": "2" * 64, "size": 500}
        reward = {"escrow": frame, "template": TEMPLATE, "pending": {CHARACTER: ASSIGNED}}
        checkpoint = {"world": {"party": {valve.PENDING: reward}}, "characters": {CHARACTER: {}}}
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [frame])
        for invalid in ({CHARACTER: [TEMPLATE[0], ASSIGNED[1]]}, {CHARACTER: ASSIGNED[:1]}, {"intruder": ASSIGNED}):
            reward["pending"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(CampaignFormatError):
                native_host_save._escrow_frames(checkpoint)
