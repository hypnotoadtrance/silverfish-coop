import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_computer_board, native_host_save, native_mosby_station_view as station
from tools.silverfish import native_mosby_tape as tape, native_wire_board, party
from tools.silverfish.campaign import CampaignFormatError
from tools.silverfish.native_tape_profiles import PUMP, STONE


OWNER = "guest-owner"
PAWN = "Player_FP_C exact Utility Tunnels guest"
BLANK = "7B" * 16
OPERATION = "7a" * 16
PRODUCT = uuid.uuid5(uuid.NAMESPACE_URL, f"{PUMP.namespace}:{OWNER}:{BLANK}").hex.upper()


def exchange_row(profile=PUMP, action="preflight", state="offer", blank=BLANK, product="-"):
    return [profile.prefix, profile.verb(action), profile.map, PAWN, profile.station, state, blank, product,
            "1" if profile.requires_active_quest else "-", "100", "0", "1", "1", "0", "0"]


def pump_receipt():
    return {"owner": OWNER, "blank_guid": BLANK, "filled_guid": PRODUCT, "operation_id": OPERATION,
            "map": PUMP.map, "station": PUMP.station, "class_path": PUMP.product}


class PumpTapeTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_pump_wire_proof_allows_stock_download_before_quest_acceptance(self):
        proof = tape.parse_exchange(exchange_row())
        self.assertIsNone(proof["quest_step"])
        self.assertEqual(proof["receive"], {"class_path": PUMP.product, "quantity": 1, "cash": 0})
        wrong = exchange_row()
        wrong[4] = STONE.station
        with self.assertRaisesRegex(ValueError, "station exchange proof"):
            tape.parse_exchange(wrong)
        old = exchange_row(STONE)
        old[8] = "-"
        with self.assertRaisesRegex(ValueError, "station exchange proof"):
            tape.parse_exchange(old)

    async def test_exact_pump_trade_checkpoints_detached_owner_and_replays_only_once(self):
        game = {"state": "hosting", "map": PUMP.map}
        state = party.new_party()
        party.add_player(state, OWNER)
        host = SimpleNamespace(
            party=state, native_profile=True, native_loot_input=False, native_guest=True, native_zones=True, last_seen=time.monotonic(),
            last_game=game, transitioning=False, stopping=False, native_checkpoint_unsafe=False,
            native_recipients=lambda: {OWNER: {"pawn": PAWN, "connected": True}}, checkpoint_native=AsyncMock(return_value=7),
            control=Mock(),
        )
        before = {**game, PUMP.key: tape.parse_exchange(exchange_row())}
        after = {**game, PUMP.key: tape.parse_exchange(exchange_row(action="convert", state="exchanged", product=PRODUCT))}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=(before, after, before)))
        first = await tape.convert(host, bridge, OWNER, BLANK, OPERATION, profile=PUMP)
        self.assertEqual(first, {"effect": "eather_pump_tape_exchanged", **pump_receipt()})
        self.assertEqual(host.party[PUMP.ledger][BLANK], pump_receipt())
        self.assertEqual(host.checkpoint_native.await_args.kwargs["require_guest_frames"], {OWNER})
        replay = await tape.convert(host, bridge, OWNER, BLANK, OPERATION, profile=PUMP)
        self.assertEqual(replay, dict(first, effect="eather_pump_tape_already_exchanged"))
        self.assertEqual(host.checkpoint_native.await_count, 2)
        host.last_game = dict(game, map=STONE.map)
        with self.assertRaisesRegex(ValueError, "blank_already_exchanged"):
            await tape.convert(host, bridge, OWNER, BLANK, "4" * 32, profile=STONE)

    def test_receipt_is_profile_bound_in_saved_state_and_stock_redemption(self):
        row = pump_receipt()
        state = party.new_party()
        state[PUMP.ledger] = {BLANK: row}
        checkpoint = {"world": {"party": state}, "characters": {OWNER: {}}}
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [])
        stock = {"objective_guid": PRODUCT, "objective_owner": "guest"}
        host = SimpleNamespace(party=state)
        spec = native_computer_board.PUMP_ROOM
        self.assertTrue(native_computer_board._objective_owned(host, OWNER, spec, stock))
        self.assertFalse(native_computer_board._objective_owned(host, "another", spec, stock))
        row["station"] = STONE.station
        self.assertFalse(native_computer_board._objective_owned(host, OWNER, spec, stock))
        with self.assertRaisesRegex(CampaignFormatError, "exact product and station"):
            native_host_save._escrow_frames(checkpoint)
        row["station"] = PUMP.station
        state[STONE.ledger] = {BLANK: {"owner": OWNER, "blank_guid": BLANK,
                                     "filled_guid": "FD" * 16, "operation_id": "d" * 32}}
        with self.assertRaisesRegex(CampaignFormatError, "invalid owner or tape identity"):
            native_host_save._escrow_frames(checkpoint)

    async def test_stock_station_ui_rejects_the_right_sequence_from_the_wrong_station(self):
        scope = {"map": PUMP.map, "pawn": PAWN, "station": PUMP.station, "sequence": 1, "open": 1,
                 "state": "checking", "blank_guid": None, "filled_guid": None}
        snapshot = {"map": PUMP.map, "pawns": {"local": {"name": PAWN}}, station.KEY: scope}
        request = (1, 1, "view", PAWN, None, STONE.station)
        bridge = SimpleNamespace(native_mosby_station_requests=Mock(return_value=[request]),
                                 command=AsyncMock(), input_namespace=uuid.uuid4())
        view = station.StationView(bridge, Mock())
        action = AsyncMock(return_value={PUMP.key: tape.parse_exchange(exchange_row())})
        self.assertIs(await view.sync(snapshot, "epoch", True, action), snapshot)
        action.assert_not_awaited()
        bridge.native_mosby_station_requests.return_value = [(*request[:-1], PUMP.station)]
        bridge.command.return_value = {**snapshot, station.KEY: dict(scope, state="offer", blank_guid=BLANK)}
        self.assertEqual((await view.sync(snapshot, "epoch", True, action))[station.KEY]["state"], "offer")
        self.assertEqual(action.await_args.args[0], PUMP.verb("preflight"))
        spec = native_computer_board.PUMP_ROOM
        self.assertEqual(native_wire_board.fixture_selection(spec.redeem_map, spec.quest_id), (spec.quest_id,))
