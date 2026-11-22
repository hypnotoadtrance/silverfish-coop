import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_authored_pickup, native_computer_board as collection, native_host_save
from tools.silverfish import link, native_quest, native_rewards, native_wire_board, party, runtime
from tools.silverfish.travel import BOROUGH, SCIENTIST_SHOP


CONTRACT = collection.COUP_REPORT
SOURCE = native_authored_pickup.COUP_REPORT
DEAD_END, NOTE, NOTE_CLASS = native_authored_pickup.SOURCES[SOURCE]
GUID = "AB" * 16
GUEST, PAWN = "scout", "exact scout pawn"


class NorthernPassageInputTests(unittest.TestCase):
    def test_ordinary_interact_accepts_only_registered_placed_pickups_and_bag_ids(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        targets = (*native_authored_pickup.SOURCES, "a" * 32, "out-of-scope-note")
        (path / "input.tsv").write_text("".join(f"interact\t{index}\t{target}\n" for index, target in enumerate(targets, 1)),
                                      encoding="ascii")
        reader = runtime.Bridge(path, Mock())
        self.assertEqual(tuple(target for _, target in reader.interactions()), targets[:-1])
        self.assertEqual(reader.interactions(), [])

    def test_transaction_has_time_for_both_native_checkpoints(self):
        for action, target in (("native-quest-turnin", CONTRACT.quest_id), (native_authored_pickup.ACTION, SOURCE)):
            with self.subTest(action=action):
                payload = {"action": action, "target": target, "operation_id": "a" * 32}
                self.assertEqual(link.checkpoint_action_timeout(payload), 20.0)

    def test_snapshot_keeps_the_two_native_reputation_rows(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        operation = CONTRACT.command("turn-in")
        rows = (
            ("schema", "1"), ("session", "host", "hosting", BOROUGH, "1", "0"),
            (CONTRACT.prefix, operation, BOROUGH, PAWN, collection.BOARDS[BOROUGH], CONTRACT.quest_id,
             "1", "-10", "75", "1275", "3", "4", GUID, "guest", "1"),
            (CONTRACT.prefix + "-reputation", "2", "20", "80"),
            (CONTRACT.prefix + "-reputation", "5", "-300", "-400"),
        )
        (path / "status.tsv").write_text("\n".join("\t".join(row) for row in rows), encoding="utf-8")
        proof = runtime.Bridge(path, Mock()).snapshot()[CONTRACT.key]
        self.assertEqual((proof["operation"], proof["objective_guid"], proof["step_after"]), (operation, GUID, -10))
        self.assertEqual(proof["reputation"], {"2": {"before": 20, "after": 80}, "5": {"before": -300, "after": -400}})


class NorthernPassageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        profile = Path(self.enterContext(tempfile.TemporaryDirectory()))
        state = party.new_party()
        party.add_player(state, "host")
        party.add_player(state, GUEST)
        self.host = SimpleNamespace(
            party=state, native_profile=profile, fixtures=False, native_zones=True, native_guest=True,
            transitioning=False, stopping=False, native_checkpoint_unsafe=False, pending_host_save=None,
            last_seen=time.monotonic(),
            last_game={"state": "hosting", "map": DEAD_END, native_authored_pickup.SOURCES_KEY: {SOURCE: {
                "source": SOURCE, "map": DEAD_END, "actor": NOTE, "class_path": NOTE_CLASS, "state": "available",
            }}},
            roles=lambda: {"host": "host", "guest": GUEST},
            native_recipients=lambda: {GUEST: {"pawn": PAWN, "connected": True}},
            checkpoint_native=AsyncMock(return_value=5), control=Mock(),
        )
        self.bridge = Mock(command=AsyncMock(side_effect=self.command))
        self.step = None
        self.cash = 75
        self.xp = 3
        self.holds_note = False
        self.reputation = {"2": 20, "5": -300}

    async def command(self, operation, pawn, *arguments, timeout):
        self.assertEqual(pawn, PAWN)
        snapshot = dict(self.host.last_game)
        area = snapshot["map"]
        if operation == native_authored_pickup.ACTION:
            self.assertEqual(area, DEAD_END)
            slot, source = arguments
            self.assertEqual(source, SOURCE)
            path = native_host_save.slot_path(self.host.native_profile, slot)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"GVAS exact Dead End note")
            snapshot["native_authored_pickup_claim"] = {
                "source": SOURCE, "map": area, "pawn": pawn, "slot": slot,
                "ok": True, "applied": True, "status": "claimed", "guid": GUID,
            }
            snapshot[native_authored_pickup.EFFECT_KEY] = "officer_reaction"
            self.holds_note = True
            return snapshot
        self.assertTrue(operation.startswith(CONTRACT.prefix + "-"))
        action = operation.removeprefix(CONTRACT.prefix + "-")
        before_step, before_cash, before_xp = self.step, self.cash, self.xp
        before_rep = dict(self.reputation)
        objective = (GUID, "guest") if self.step in (1, 2) and self.holds_note else ("-", "-")
        if action == "start":
            self.step = 1
        elif action == "turn-in":
            self.assertEqual(arguments, objective)
            self.step, self.holds_note = -10, False
            self.cash += 1200
            self.xp += 1
            self.reputation = {"2": 80, "5": -400}
        elif self.xp < 3:
            raise RuntimeError("stock_coup_report_quest_not_offered")
        snapshot[CONTRACT.key] = {
            "map": area, "pawn": pawn, "board": collection.BOARDS[area], "quest": CONTRACT.quest_id,
            "operation": operation, "can_turn_in": area == BOROUGH,
            "step_before": before_step, "step_after": self.step, "cash_before": before_cash,
            "cash_after": self.cash, "xp_before": before_xp, "xp_after": self.xp,
            "objective_guid": objective[0], "objective_owner": objective[1],
            "reputation": {key: {"before": before_rep[key], "after": value} for key, value in self.reputation.items()},
        }
        return snapshot

    async def quest(self, action):
        return await native_quest.action(self.host, self.bridge, GUEST, action, CONTRACT.quest_id)

    async def test_exact_placed_note_is_consumed_with_coherent_party_payout(self):
        picked_up = await native_authored_pickup.claim(self.host, self.bridge, GUEST, SOURCE, "a" * 32)
        self.assertEqual(picked_up["native_authored_pickup_claim"], {
            "source": SOURCE, "map": DEAD_END, "actor": NOTE, "class_path": NOTE_CLASS, "guid": GUID, "owner": GUEST,
            "effect": "officer_reaction",
        })
        self.assertEqual(self.host.checkpoint_native.call_args.kwargs["require_guest_frames"], {GUEST})
        self.host.last_game["map"] = BOROUGH
        self.host.checkpoint_native.reset_mock()
        self.assertEqual((await self.quest("native-quest-start"))["step"], 1)
        self.host.checkpoint_native.assert_not_awaited()
        before = await collection.preflight(self.host, self.bridge, GUEST, spec=CONTRACT)
        self.assertTrue(before[CONTRACT.key]["objective_qualified"])
        receipt = await self.quest("native-quest-turnin")
        self.assertEqual((receipt["step"], receipt["cash"], receipt["xp"], receipt["consumed_note"], self.holds_note),
                         (-10, 1200, 1, GUID, False))
        self.assertEqual((receipt["reputation"], self.host.party["economy"]["wallet"]), ({"2": 80, "5": -400}, 1275))
        self.assertEqual(self.host.party["players"][GUEST][native_rewards.PENDING], 1)
        self.assertEqual(self.host.checkpoint_native.call_args.kwargs["require_guest_frames"], {GUEST})
        self.assertEqual(self.host.party["native_quests"][CONTRACT.quest_id]["objective_owner"], GUEST)
        calls = self.bridge.command.call_count
        self.assertEqual((await self.quest("native-quest-turnin"))["effect"], "native_quest_already_settled")
        self.assertEqual(self.bridge.command.call_count, calls)

    async def test_board_offer_and_exact_objective_are_required_before_mutation(self):
        self.host.last_game["map"] = BOROUGH
        self.xp = 2
        with self.assertRaisesRegex(party.RuleError, "quest_not_offered"):
            await self.quest("native-quest-start")
        self.xp, self.step, self.holds_note = 3, 1, True
        proof = await collection.preflight(self.host, self.bridge, GUEST, spec=CONTRACT)
        self.assertFalse(proof[CONTRACT.key]["objective_qualified"])
        with self.assertRaisesRegex(party.RuleError, "objective_required"):
            await self.quest("native-quest-turnin")
        self.host.last_game["map"] = SCIENTIST_SHOP
        with self.assertRaisesRegex(party.RuleError, "borough_redemption_required"):
            await self.quest("native-quest-turnin")
        self.host.checkpoint_native.assert_not_awaited()
        self.assertFalse(self.host.native_checkpoint_unsafe)
        self.assertFalse(self.host.stopping)

    async def test_board_overlay_uses_authoritative_note_custody(self):
        self.host.last_game["map"] = BOROUGH
        self.step, self.holds_note = 1, True
        panel = native_wire_board.WireBoardView(self.bridge, self.host.control)

        async def check(verb, operation, target):
            self.assertEqual(verb, CONTRACT.command("preflight"))
            return await collection.preflight(self.host, self.bridge, GUEST, spec=CONTRACT)

        board = collection.BOARDS[BOROUGH]
        self.assertEqual(await panel._inspect(BOROUGH, board, CONTRACT.quest_id, "1" * 32, check), "missing")
        self.host.party[native_authored_pickup.STATE_KEY] = {
            SOURCE: {"source": SOURCE, "map": DEAD_END, "actor": "different placed note",
                     "class_path": NOTE_CLASS, "guid": GUID, "owner": GUEST},
        }
        self.assertEqual(await panel._inspect(BOROUGH, board, CONTRACT.quest_id, "2" * 32, check), "missing")
        self.host.party[native_authored_pickup.STATE_KEY][SOURCE]["actor"] = NOTE
        self.assertEqual(await panel._inspect(BOROUGH, board, CONTRACT.quest_id, "3" * 32, check), "ready")
