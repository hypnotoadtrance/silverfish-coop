import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_mindcore as mindcore, native_mindcore_fixture as fixture, party
from tools.silverfish.native_tape_profiles import DRILL, DRILL_QUEST


CHARACTER = "75a8b566-e13c-46d6-b542-313ec5163c3f"
OTHER = "dc39fe04-5626-4220-ba91-fbf229188bbc"
GUID = "A" * 32
OPERATION = "a" * 32
PAWN = f"Player_FP_C {mindcore.LEVEL}Player_FP_C_0"


def scene(stage, *, installed=True):
    return mindcore.parse_scene([
        "native-mindcore-scene", mindcore.MAP, mindcore.CRADLE, mindcore.UPLOADER, mindcore.CORE, mindcore.FLAG,
        str(stage), str(int(installed)),
    ])


def report(stage, verb=None, proof="owned"):
    frame = {"state": "hosting", "map": mindcore.MAP, mindcore.SCENE: scene(stage)}
    if verb:
        frame[mindcore.PROOF] = mindcore.parse_proof([
            "native-mindcore-tape", mindcore.MAP, verb, PAWN, GUID, proof, str(stage),
        ])
    return frame


def host(stage=1):
    state = party.new_party()
    for owner in ("host", CHARACTER, OTHER):
        party.add_player(state, owner)
    state["native_quests"] = {
        DRILL_QUEST: {"quest_id": DRILL_QUEST, "step": -10, "mosby_drill_test": 1,
                      "test_results": {"station": DRILL.station, "class_path": DRILL.product}},
    }
    state[DRILL.ledger] = {
        "B" * 32: dict(owner=CHARACTER, filled_guid=GUID, map=DRILL.map, station=DRILL.station, class_path=DRILL.product),
    }
    recipients = Mock(return_value={
        CHARACTER: {"connected": True, "pawn": PAWN},
        OTHER: {"connected": True, "pawn": PAWN + "7"},
    })
    return SimpleNamespace(
        party=state, last_game=report(stage), last_seen=time.monotonic(), native_profile=True,
        native_zones=True, native_guest=True, transitioning=False, stopping=False, native_checkpoint_unsafe=False,
        native_recipients=recipients, checkpoint_native=AsyncMock(side_effect=(5, 6)), control=Mock(),
    )


def bridge():
    value = SimpleNamespace(command=AsyncMock(side_effect=[
        report(1, mindcore.PREFLIGHT), report(1, mindcore.UPLOAD, "dispatched"), report(2, mindcore.VERIFY),
    ]), wait_for=AsyncMock())

    async def finish(predicate, timeout):
        if timeout != 18 or predicate(report(1)) or not predicate(report(2)):
            raise AssertionError("Expected the actual mounted console to reach stock stage two")
        return report(2)

    value.wait_for.side_effect = finish
    return value


class MindCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_owned_tape_compiles_once_after_stock_seal_and_survives_replay(self):
        owner, game = host(), bridge()
        proof = await mindcore.upload(owner, game, CHARACTER, GUID, OPERATION)
        self.assertEqual(proof["effect"], "native_mindcore_results_compiled")
        row = owner.party[mindcore.LEDGER]
        self.assertEqual(row, {key: value for key, value in proof.items() if key != "effect"})
        self.assertTrue(mindcore.valid_receipt(owner.party, {CHARACTER: {}}))
        self.assertFalse(mindcore.valid_receipt(owner.party, {OTHER: {}}))
        self.assertEqual(owner.party[DRILL.ledger]["B" * 32]["filled_guid"], GUID)
        owner.control.event.assert_called_once_with("native_mindcore_results_committed", revision=6, **row)
        self.assertEqual([call.args for call in game.command.await_args_list], [
            (mindcore.PREFLIGHT, PAWN, GUID), (mindcore.UPLOAD, PAWN, GUID), (mindcore.VERIFY, PAWN, GUID),
        ])
        self.assertEqual([call.kwargs["require_guest_frames"] for call in owner.checkpoint_native.await_args_list],
                         [frozenset({CHARACTER}), frozenset({CHARACTER})])
        self.assertFalse(owner.native_checkpoint_unsafe or owner.transitioning or owner.stopping)

        game.command.side_effect = None
        game.command.return_value = report(2, mindcore.VERIFY)
        self.assertEqual(await mindcore.upload(owner, game, CHARACTER, GUID, OPERATION),
                         {"effect": "native_mindcore_already_compiled", **row})
        game.command.assert_awaited_with(mindcore.VERIFY, PAWN, GUID, timeout=7)
        self.assertEqual(owner.checkpoint_native.await_count, 2)

    async def test_wrong_owner_and_unmounted_core_refuse_without_a_stock_call(self):
        owner, game = host(), bridge()
        with self.assertRaisesRegex(party.RuleError, "durable_results_owner"):
            await mindcore.upload(owner, game, OTHER, GUID, OPERATION)
        owner.last_game = {"state": "hosting", "map": mindcore.MAP, mindcore.SCENE: scene(1, installed=False)}
        with self.assertRaisesRegex(party.RuleError, "console_not_installed"):
            await mindcore.upload(owner, game, CHARACTER, GUID, OPERATION)
        game.command.assert_not_awaited()
        owner.checkpoint_native.assert_not_awaited()

    async def test_stock_compiled_without_receipt_does_not_mint_one(self):
        owner, game = host(2), bridge()
        with self.assertRaisesRegex(party.RuleError, "already_compiled"):
            await mindcore.upload(owner, game, CHARACTER, GUID, OPERATION)
        self.assertNotIn(mindcore.LEDGER, owner.party)
        game.command.assert_not_awaited()

    async def test_uncertain_dispatched_stock_signal_stops_before_durable_publication(self):
        owner, game = host(), bridge()
        game.wait_for.side_effect = TimeoutError("stock console did not seal")
        with self.assertRaises(TimeoutError):
            await mindcore.upload(owner, game, CHARACTER, GUID, OPERATION)
        self.assertTrue(owner.native_checkpoint_unsafe and owner.stopping)
        self.assertFalse(owner.transitioning)
        self.assertNotIn(mindcore.LEDGER, owner.party)
        owner.checkpoint_native.assert_awaited_once()
        owner.control.event.assert_not_called()

    async def test_known_pre_dispatch_native_refusal_releases_checkpoint_fence(self):
        owner, game = host(), bridge()
        game.command.side_effect = [
            report(1, mindcore.PREFLIGHT), RuntimeError("native_mindcore_refused_stock_upload_unqualified"),
        ]
        with self.assertRaisesRegex(party.RuleError, "stock_upload_unqualified"):
            await mindcore.upload(owner, game, CHARACTER, GUID, OPERATION)
        self.assertFalse(owner.native_checkpoint_unsafe or owner.transitioning or owner.stopping)
        self.assertNotIn(mindcore.LEDGER, owner.party)

    def test_exact_stock_scene_and_proof_parser_rejects_wrong_map_or_unsealed_values(self):
        good = [
            "native-mindcore-scene", mindcore.MAP, mindcore.CRADLE, mindcore.UPLOADER, mindcore.CORE, mindcore.FLAG, "1", "1",
        ]
        for index, value in ((3, "OtherUploader"), (4, "MindCore_BP_C_2"), (5, "Mosby_DrillTest"), (6, "3"), (7, "true")):
            altered = good[:]
            altered[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                mindcore.parse_scene(altered)
        with self.assertRaises(ValueError):
            mindcore.parse_proof(["native-mindcore-tape", mindcore.MAP, mindcore.UPLOAD, PAWN, GUID, "dispatched", "2"])

    def test_replication_report_keeps_mount_authority_separate_from_peer_movement(self):
        row = ["native-mindcore-replication", mindcore.MAP, "guest", "guarded", mindcore.CORE,
               "0", "1", "0", "0", "0.0", "6682.0", "6719.0", "load_map"]
        view = mindcore.parse_replication(row, "guest")
        self.assertTrue(view["replicate_movement"])
        self.assertFalse(view["capsule_overlap"])
        for index, value in ((3, "active"), (4, mindcore.CORE.removesuffix("_1") + "_2"), (7, "1"), (8, "1")):
            changed = row.copy()
            changed[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                mindcore.parse_replication(changed, "guest")


class MindCoreFixtureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host = host()
        self.local = f"Player_FP_C {mindcore.LEVEL}Player_FP_C_1"
        self.host.fixtures = True
        self.host.native_recipients.return_value = {CHARACTER: {"connected": True, "pawn": PAWN}}
        self.host.last_game = self.frame(0)

    def frame(self, stage):
        return {**report(stage), mindcore.SCENE: scene(stage, installed=stage != 0),
                "pawns": {"local": {"name": self.local}}}

    async def test_dormant_core_moves_once_and_only_stock_mount_readback_allows_checkpoint(self):
        game = SimpleNamespace(command=AsyncMock(return_value=self.frame(0)), wait_for=AsyncMock())

        async def mounted(predicate, timeout):
            self.assertTrue(self.host.native_checkpoint_unsafe and self.host.transitioning)
            self.assertEqual(timeout, 12)
            self.assertFalse(predicate(self.frame(0)))
            self.assertFalse(predicate(self.frame(2)))
            self.assertTrue(predicate(self.frame(1)))
            return self.frame(1)

        game.wait_for.side_effect = mounted
        state = await fixture.perform(self.host, game, fixture.INSTALL, None)
        game.command.assert_awaited_once_with(fixture.INSTALL, self.local, timeout=7)
        self.assertEqual([call.kwargs["require_guest_frames"] for call in self.host.checkpoint_native.await_args_list],
                         [frozenset({CHARACTER})] * 2)
        self.assertEqual(state[mindcore.SCENE], scene(1))
        self.assertFalse(self.host.native_checkpoint_unsafe or self.host.transitioning)
        self.assertNotIn(mindcore.LEDGER, self.host.party)

    async def test_incomplete_stock_mount_fails_closed_and_never_publishes_after_move(self):
        game = SimpleNamespace(command=AsyncMock(return_value=self.frame(0)), wait_for=AsyncMock(side_effect=TimeoutError))
        with self.assertRaises(TimeoutError):
            await fixture.perform(self.host, game, fixture.INSTALL, None)
        self.host.checkpoint_native.assert_awaited_once()
        self.host.control.event.assert_not_called()
        self.assertTrue(self.host.native_checkpoint_unsafe and self.host.stopping)
        self.assertFalse(self.host.transitioning)

    async def test_fixture_selectors_are_local_and_require_staged_opt_in_and_exact_guest(self):
        game = SimpleNamespace(command=AsyncMock())
        for enabled, guest, target in ((False, True, None), (True, False, None), (True, True, "guest")):
            self.host.fixtures = enabled
            self.host.native_recipients.return_value = {CHARACTER: {"connected": True, "pawn": PAWN}} if guest else {}
            with self.subTest(enabled=enabled, guest=guest, target=target), self.assertRaises(party.RuleError):
                await fixture.perform(self.host, game, fixture.INSTALL, target)
        game.command.assert_not_awaited()

    async def test_carry_step_requires_a_fresh_exact_pawn_and_stock_handle_report(self):
        def carrying(state, sequence, pawn=self.local):
            row = fixture.parse_carry([fixture.CARRY_TAG, mindcore.MAP, pawn, mindcore.CORE, state, "72.0", "62.0"])
            return {**self.frame(0), "sequence": sequence, fixture.CARRY_KEY: row}

        game = SimpleNamespace(command=AsyncMock(return_value=carrying("carrying", 19)), wait_for=AsyncMock())

        async def advanced(predicate, *, timeout):
            self.assertEqual(timeout, 9)
            self.assertFalse(predicate(carrying("carried", 19)))
            self.assertFalse(predicate(carrying("carried", 20, PAWN)))
            observed = carrying("carried", 20)
            self.assertTrue(predicate(observed))
            return observed

        game.wait_for.side_effect = advanced
        observed = await fixture.perform(self.host, game, fixture.CARRY, "step")
        self.assertEqual(observed[fixture.CARRY_KEY]["core_movement_uu"], 62)
        game.command.assert_awaited_once_with(fixture.CARRY, self.local, "step", timeout=7)
        self.host.checkpoint_native.assert_not_awaited()

    def test_pose_reports_keep_the_exact_authored_core_and_local_role(self):
        row = [fixture.POSE_TAG, mindcore.MAP, "guest", mindcore.CORE, "714.39", "-1686.95", "118.37",
               "1", "1", "0", "0", "3"]
        pose = fixture.parse_pose(row, "guest")
        self.assertEqual(pose["position"], [714.39, -1686.95, 118.37])
        self.assertFalse(pose["replicate_movement"])
        with self.assertRaises(ValueError):
            fixture.parse_pose(row, "host")
        row[3] = row[3].removesuffix("_1") + "_2"
        with self.assertRaises(ValueError):
            fixture.parse_pose(row, "guest")
