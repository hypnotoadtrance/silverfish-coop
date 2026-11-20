"""Check stock Missing Contact's canonical dug-package custody."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_computer_board as board, native_host_dig as dig
from tools.silverfish import native_quest, native_rewards, native_wire_board, party
from tools.silverfish.travel import BLACK_MARKET, CROSSROADS, OBSERVATORY


SPEC = board.MISSING_CONTACT
HOST, GUEST = "Player_FP_C host", "Player_FP_C guest"
GUID = "9A" * 16


def snapshot(verb, *, pawn=HOST, guid=GUID, owner="host", completed=False):
    return {"state": "hosting", "map": BLACK_MARKET, "pawns": {"local": {"name": HOST}}, SPEC.key: {
        "operation": SPEC.command(verb), "map": BLACK_MARKET, "pawn": pawn, "board": board.BOARDS[BLACK_MARKET],
        "quest": SPEC.quest_id, "can_turn_in": True, "step_before": 1, "step_after": -10 if completed else 1,
        "cash_before": 0, "cash_after": 450 if completed else 0, "xp_before": 0, "xp_after": int(completed),
        "objective_guid": guid, "objective_owner": owner,
        "reputation": {"8": {"before": 0, "after": 40 if completed else 0},
                       "4": {"before": -600, "after": -605 if completed else -600}},
    }}


class MissingContactTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_canonical_exact_sealed_package_can_settle_once(self):
        self.assertEqual(native_wire_board.fixture_selection(BLACK_MARKET, SPEC.quest_id), (SPEC.quest_id,))
        with self.assertRaises(party.RuleError):
            native_wire_board.fixture_selection(OBSERVATORY, SPEC.quest_id)
        self.assertIsNone(SPEC.fixture_xp)
        data = party.new_party()
        for actor in ("host", "guest"):
            party.add_player(data, actor)
        host = SimpleNamespace(
            party=data, native_profile=True, native_guest=True, native_zones=True,
            native_checkpoint_unsafe=False, transitioning=False, stopping=False,
            last_game=snapshot("preflight"), last_seen=time.monotonic(),
            roles=lambda: {"host": "host", "guest": "guest"},
            native_recipients=lambda: {"guest": {"pawn": GUEST, "connected": True}},
            checkpoint_native=AsyncMock(return_value=21), control=Mock(),
        )
        bridge = SimpleNamespace(command=AsyncMock(return_value=snapshot("preflight", guid="-", owner="-")))

        async def redeem(actor="host"):
            return await native_quest.action(host, bridge, actor, "native-quest-turnin", SPEC.quest_id)

        with self.assertRaisesRegex(party.RuleError, "stock_missing_contact_objective_required"):
            await redeem()
        bridge.command.return_value = snapshot("preflight")
        with self.assertRaisesRegex(party.RuleError, "stock_missing_contact_objective_required"):
            await redeem()
        dig_record = {"state": "collected", "map": CROSSROADS, "source": dig.SOURCE, "flag": dig.FLAG, "flag_value": 1,
                      "package_guid": "82" * 16}
        data[dig.KEY] = dig_record
        with self.assertRaisesRegex(party.RuleError, "stock_missing_contact_objective_required"):
            await redeem()
        dig_record["package_guid"] = GUID
        bridge.command.return_value = snapshot("preflight", pawn=GUEST, owner="guest")
        with self.assertRaisesRegex(party.RuleError, "stock_missing_contact_objective_required"):
            await redeem("guest")
        host.checkpoint_native.assert_not_awaited()

        bridge.command.side_effect = [snapshot("preflight"), snapshot("turn-in", completed=True)]
        receipt = await redeem()
        self.assertEqual((receipt["step"], receipt[SPEC.consumed_field], receipt["objective_owner"]), (-10, GUID, "host"))
        self.assertEqual((receipt["wallet"], receipt["xp"], receipt["recipients"]), (450, 1, ["guest", "host"]))
        self.assertEqual(receipt["reputation"], {"8": 40, "4": -605})
        self.assertEqual(host.party["players"]["guest"][native_rewards.PENDING], 1)
        bridge.command.assert_awaited_with(SPEC.command("turn-in"), HOST, GUID, "host", timeout=8)
        self.assertEqual((await redeem("guest"))["effect"], "native_quest_already_settled")
        self.assertEqual(host.checkpoint_native.await_count, 2)
