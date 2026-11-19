import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import link, native_authored_pickup, native_computer_board as collection, native_quest
from tools.silverfish import native_rewards, native_turret_photo, native_wire_board, party, runtime
from tools.silverfish.travel import BOROUGH, SCAV_BAR


CONTRACT = collection.DEPOT_DOCUMENTS
BOARD = collection.BOARDS[SCAV_BAR]
SOURCE = native_authored_pickup.DEPOT_DOCUMENTS
GUEST, PAWN, GUID = "courier", "Player_FP_C detached courier", "AC" * 16


def proof(verb, *, before, after, guid="-", owner="-", earned=False):
    return {"operation": CONTRACT.command(verb), "map": SCAV_BAR, "pawn": PAWN, "board": BOARD,
            "quest": CONTRACT.quest_id, "can_turn_in": True, "step_before": before, "step_after": after,
            "cash_before": 70, "cash_after": 1370 if earned else 70, "xp_before": 3, "xp_after": 4 if earned else 3,
            "objective_guid": guid, "objective_owner": owner,
            "reputation": {"8": {"before": 5, "after": 30 if earned else 5}}}


class DepotDocumentsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state = party.new_party()
        for actor in ("host", GUEST):
            party.add_player(state, actor)
        self.host = SimpleNamespace(
            party=state, native_profile=True, native_zones=True, native_guest=True, native_checkpoint_unsafe=False,
            transitioning=False, stopping=False, last_seen=time.monotonic(),
            last_game={"state": "hosting", "map": SCAV_BAR}, roles=lambda: {"host": "host", "guest": GUEST},
            native_recipients=lambda: {GUEST: {"pawn": PAWN, "connected": True}},
            checkpoint_native=AsyncMock(return_value=4), control=Mock(),
        )
        self.bridge = SimpleNamespace(command=AsyncMock())

    @staticmethod
    def snapshot(report):
        return {"state": "hosting", "map": SCAV_BAR, CONTRACT.key: report}

    async def act(self, verb):
        return await native_quest.action(self.host, self.bridge, GUEST, verb, CONTRACT.quest_id)

    async def test_authored_scope_and_stock_offer_refusal_leave_no_checkpoint(self):
        self.host.last_game["map"] = BOROUGH
        self.assertRaises(party.RuleError, native_wire_board.fixture_selection, BOROUGH, CONTRACT.quest_id)
        self.assertEqual(native_wire_board.fixture_selection(SCAV_BAR, CONTRACT.quest_id), (CONTRACT.quest_id,))
        with self.assertRaisesRegex(party.RuleError, "scav_bar_redemption_required"):
            await self.act("native-quest-turnin")
        self.bridge.command.assert_not_called()

        self.host.last_game["map"] = SCAV_BAR
        self.bridge.command.side_effect = RuntimeError("stock_depot_documents_quest_not_offered")
        with self.assertRaisesRegex(party.RuleError, "quest_not_offered"):
            await self.act("native-quest-start")
        self.host.checkpoint_native.assert_not_called()
        self.assertFalse(self.host.native_checkpoint_unsafe)

    async def test_exact_guest_documents_survive_checkpoint_and_consume_once(self):
        self.bridge.command.side_effect = [self.snapshot(proof("preflight", before=None, after=None)),
                                           self.snapshot(proof("start", before=None, after=1))]
        self.assertEqual((await self.act("native-quest-start"))["step"], 1)

        active = self.snapshot(proof("preflight", before=1, after=1, guid=GUID, owner="guest"))
        self.bridge.command.side_effect = [active]
        with self.assertRaisesRegex(party.RuleError, "objective_required"):
            await self.act("native-quest-turnin")
        self.assertEqual(self.host.checkpoint_native.await_count, 2)

        area, actor, path = native_authored_pickup.SOURCES[SOURCE]
        self.host.party[native_authored_pickup.STATE_KEY] = {
            SOURCE: {"source": SOURCE, "map": area, "actor": actor, "class_path": path, "guid": GUID, "owner": GUEST},
        }
        done = self.snapshot(proof("turn-in", before=1, after=-10, guid=GUID, owner="guest", earned=True))
        self.bridge.command.side_effect = [active, done]
        receipt = await self.act("native-quest-turnin")
        self.assertEqual((receipt["consumed_documents"], receipt["objective_owner"], receipt["cash"], receipt["xp"]), (GUID, GUEST, 1300, 1))
        self.assertEqual(receipt["reputation"], {"8": 30})
        self.assertEqual(self.host.party["players"][GUEST][native_rewards.PENDING], 1)
        self.assertEqual(self.host.checkpoint_native.call_args.kwargs["require_guest_frames"], {GUEST})
        self.bridge.command.assert_awaited_with(CONTRACT.command("turn-in"), PAWN, GUID, "guest", timeout=8)

        self.host.party = party.load_party(json.loads(json.dumps(self.host.party)))
        self.assertEqual((await self.act("native-quest-turnin"))["effect"], "native_quest_already_settled")
        self.assertEqual(self.host.party["economy"]["wallet"], 1370)

    async def test_scav_stock_catalog_identifies_the_experimental_offer(self):
        self.bridge.command.return_value = {
            "state": "hosting", "map": SCAV_BAR, "sequence": 11,
            "native_bounty_board": {"map": SCAV_BAR, "pawn": PAWN, "board": BOARD},
            "native_bounty_offers": {"sequence": 11, "board": {"state": "ready", "total": 1, "emitted": 1,
                                      "reason": "", "entries": {CONTRACT.quest_id: {"state": "offer"}}}},
        }
        result = await native_turret_photo.offers(self.host, self.bridge, GUEST, None)
        self.assertEqual(result["native_bounty_board"]["quests"], [{"id": CONTRACT.quest_id, "state": "offer", "co_op": "experimental"}])
        with self.assertRaisesRegex(party.RuleError, "native_turret_photo_board_unavailable"):
            await native_turret_photo.preflight(self.host, self.bridge, GUEST)

    def test_status_and_checkpoint_budget_for_the_exact_stock_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            rows = (("schema", "1"), ("session", "host", "hosting", SCAV_BAR, "1", "0"),
                    (CONTRACT.prefix, CONTRACT.command("turn-in"), SCAV_BAR, PAWN, BOARD, CONTRACT.quest_id,
                     "1", "-10", "70", "1370", "3", "4", GUID, "guest", "1"),
                    (CONTRACT.prefix + "-reputation", "8", "5", "30"))
            (path / "status.tsv").write_text("\n".join("\t".join(row) for row in rows), encoding="utf-8")
            self.assertEqual(runtime.Bridge(path, Mock()).snapshot()[CONTRACT.key],
                             proof("turn-in", before=1, after=-10, guid=GUID, owner="guest", earned=True))
        payload = {"action": "native-quest-turnin", "target": CONTRACT.quest_id, "operation_id": "b" * 32}
        self.assertEqual(link.checkpoint_action_timeout(payload), 20.0)
