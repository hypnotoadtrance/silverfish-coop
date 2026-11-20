import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_flood_jammer as stock, native_rewards, native_spratt, native_wire_board, party
from tools.silverfish.travel import BLACK_MARKET, OBSERVATORY


SPEC = stock.SPRATT
GUEST = "guest"
PAWN = "exact enrolled guest pawn"
HOST_PAWN = "exact listen host pawn"


def report(verb, pawn, *, before, after, cash=20, xp=7, paid=False, fixture=False):
    return {"state": "hosting", "map": BLACK_MARKET, SPEC.key: {
        "operation": SPEC.command(verb), "map": BLACK_MARKET, "pawn": pawn, "board": SPEC.board,
        "quest": SPEC.quest, "step_before": before, "step_after": after,
        "xp_before": xp, "xp_after": 7 if fixture else xp + int(paid),
        "cash_before": cash, "cash_after": cash + (5000 if paid else 0),
        "reputation": {"5": {"before": -300, "after": -250 if paid else -300},
                       "8": {"before": 0, "after": 30 if paid else 0}},
    }}


def stock_death():
    return {
        "quest_id": SPEC.quest, "step": 2, "actor": GUEST,
        "objective": {
            "map": native_spratt.AREA, "spawner": native_spratt.SPAWNER,
            "target": f"NPC_Spratt_C {native_spratt.LEVEL}NPC_Spratt_C_2147483491",
            "state": native_spratt.VERIFIED, "step_before": 1, "step_after": 2,
            "cash_before": 20, "cash_after": 20, "xp_before": 7, "xp_after": 7, "callback_count": 1,
        },
    }


class SprattBoardTests(unittest.IsolatedAsyncioTestCase):
    def host(self, area=BLACK_MARKET):
        state = party.new_party()
        party.add_player(state, "host")
        party.add_player(state, GUEST)
        return SimpleNamespace(
            party=state, last_game={"map": area, "state": "hosting", "pawns": {"local": {"name": HOST_PAWN}}},
            last_seen=time.monotonic(), native_profile=True, native_guest=True, native_zones=True,
            stopping=False, transitioning=False, native_checkpoint_unsafe=False, fixtures=False,
            roles=lambda: {"host": "host", "guest": GUEST}, native_recipients=lambda: {GUEST: {"pawn": PAWN, "connected": True}},
            checkpoint_native=AsyncMock(return_value=5), control=Mock(),
        )

    async def test_exact_board_and_xp_gate_use_stock_offer(self):
        self.assertEqual(native_wire_board.fixture_selection(BLACK_MARKET, SPEC.quest), (SPEC.quest,))
        with self.assertRaises(party.RuleError):
            native_wire_board.fixture_selection(OBSERVATORY, SPEC.quest)
        bridge = SimpleNamespace(command=AsyncMock(return_value=report("preflight", PAWN, before=None, after=None, xp=6)))
        host = self.host()
        start = SPEC.command("start")
        with self.assertRaisesRegex(party.RuleError, "stock_spratt_quest_not_offered"):
            await stock.act(host, bridge, GUEST, start)
        with self.assertRaisesRegex(party.RuleError, "board_unavailable"):
            await stock.act(self.host(OBSERVATORY), bridge, GUEST, start)
        host.checkpoint_native.assert_not_awaited()
        bridge.command.side_effect = [report("preflight", PAWN, before=None, after=None), report("start", PAWN, before=None, after=1)]
        accepted = await stock.act(host, bridge, GUEST, start)
        self.assertEqual((accepted["quest_id"], accepted["step"], accepted["actor"]), (SPEC.quest, 1, GUEST))
        self.assertEqual(host.party["economy"]["wallet"], 0)

    async def test_only_verified_stock_death_can_redeem_and_pay_once(self):
        host = self.host()
        record = stock_death()
        host.party["native_quests"] = {SPEC.quest: record}
        bridge = SimpleNamespace(command=AsyncMock())
        turn_in = SPEC.command("turn-in")
        record["objective"]["callback_count"] = 0
        with self.assertRaisesRegex(party.RuleError, "objective_incomplete"):
            await stock.act(host, bridge, GUEST, turn_in)
        host.checkpoint_native.assert_not_awaited()
        bridge.command.assert_not_awaited()
        record["objective"]["callback_count"] = 1
        bridge.command.side_effect = [report("preflight", PAWN, before=2, after=2),
                                      report("turn-in", PAWN, before=2, after=-10, paid=True)]
        paid = await stock.act(host, bridge, GUEST, turn_in)
        self.assertEqual((paid["step"], paid["cash"], paid["wallet"], paid["xp"]), (-10, 5000, 5020, 1))
        self.assertEqual(set(paid["recipients"]), {"host", GUEST})
        self.assertEqual(host.party["players"][GUEST][native_rewards.PENDING], 1)
        replay = await stock.act(host, bridge, GUEST, turn_in)
        self.assertEqual((replay["effect"], replay["wallet"]), ("native_quest_already_settled", 5020))
        self.assertEqual(host.checkpoint_native.await_count, 2)

    async def test_xp_setup_is_fixture_only_and_quest_absent(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(return_value=report(
            "xp-fixture", HOST_PAWN, before=None, after=None, xp=0, fixture=True)))
        with self.assertRaisesRegex(party.RuleError, "xp_fixture_unavailable"):
            await stock.xp_fixture(host, bridge, SPEC)
        bridge.command.assert_not_awaited()
        host.fixtures = True
        result = (await stock.xp_fixture(host, bridge, SPEC))[SPEC.key]
        self.assertEqual((result["xp_before"], result["xp_after"], result["step_after"]), (0, 7, None))
        self.assertFalse(host.party.get("native_quests"))
