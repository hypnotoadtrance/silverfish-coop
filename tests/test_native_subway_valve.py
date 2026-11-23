import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from tools.silverfish import native_host_save, native_loot, native_quest, native_subway_valve as valve, native_wire_board, party
from tools.silverfish.campaign import CampaignFormatError
from tools.silverfish.runtime import Bridge
from tools.silverfish.travel import BOROUGH, CITY


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
                           native_checkpoint_unsafe=False, pending_host_save=None, last_game=snapshot(),
                           last_seen=time.monotonic(), zone_epoch=EPOCH,
                           party=candidate, control=Mock(), checkpoint_native=AsyncMock(return_value=3),
                           native_recipients=lambda: {CHARACTER: {"pawn": PAWN, "connected": True}})


class FloodControlTests(unittest.IsolatedAsyncioTestCase):
    def test_bridge_consumes_the_exact_stock_interact_target_once(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            inputs = directory / "input.tsv"
            inputs.write_bytes(b"interact\t1\tsubway-valve\ninteract\t2\tsubway-valve-extra\ninteract\t3\tsubway-valve")
            bridge = Bridge(directory, None)
            self.assertEqual([target for _, target in bridge.interactions()], [valve.INPUT])
            self.assertEqual(bridge.interactions(), [])
            with inputs.open("ab") as output:
                output.write(b"\n")
            self.assertEqual([target for _, target in bridge.interactions()], [valve.INPUT])
            self.assertEqual(bridge.interactions(), [])

    def test_board_and_objective_reject_wrong_actor_or_stock_delta(self):
        self.assertEqual(native_wire_board.fixture_selection(BOROUGH, valve.QUEST), (valve.QUEST,))
        with self.assertRaises(party.RuleError):
            native_wire_board.fixture_selection(valve.SUBWAY, valve.QUEST)
        self.assertEqual((objective()["epoch"], objective()["flag"]), (EPOCH, 1))
        self.assertEqual(valve.FACTIONS, {"2", "4", "5"})
        self.assertEqual(objective()["commune_after"] - objective()["commune_before"], 20)
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

    def test_scene_reports_exact_components_without_calling_live_sludge_closed(self):
        fields = ["native-subway-valve-scene", valve.SUBWAY, "settling", "host", "-", "absent", "present", "retired", "1"]
        proof = valve.parse_scene(fields, "hosting", valve.SUBWAY)
        self.assertEqual((proof["box"], proof["sludge"], proof["audio"], proof["powered"]), ("absent", "present", "retired", True))
        self.assertEqual(valve.parse_scene([*fields, "component_muted"], "hosting", valve.SUBWAY)["audio_check"], "component_muted")
        with self.assertRaises(ValueError):
            valve.parse_scene([*fields[:2], "closed", *fields[3:]], "hosting", valve.SUBWAY)

    async def test_board_acceptance_defers_save_and_preserves_earlier_typed_changes(self):
        host = authority()
        del host.party["native_quests"][valve.QUEST]
        host.last_game = {"state": "hosting", "map": BOROUGH, "pawns": {"local": {"name": BOROUGH_PAWN}}}
        host.pending_host_save = True
        proof = valve.parse_board(["native-subway-valve-board", "start", BOROUGH, BOROUGH_PAWN, valve.BOARD,
                                  "-", "1", "0", "9", "9", "7", "7", "-", "-"], "hosting", BOROUGH)
        proof["reputation"] = dict.fromkeys(valve.FACTIONS, 0)
        bridge = SimpleNamespace(command=AsyncMock(return_value={**host.last_game, valve.BOARD_KEY: proof}))

        receipt = await native_quest.action(host, bridge, "host", "native-quest-start", valve.QUEST)
        record = {"quest_id": valve.QUEST, "step": 1, "actor": "host"}
        self.assertEqual(receipt, {"effect": "native_quest_changed", **record})
        self.assertEqual(host.party["native_quests"][valve.QUEST], record)
        self.assertIs(host.pending_host_save, True)
        self.assertFalse(host.native_checkpoint_unsafe)
        host.control.event.assert_called_once_with("native_subway_valve_board_committed",
                                                   operation=valve.START, revision=None, **record)
        reply = await native_quest.action(host, bridge, "host", "native-quest-start", valve.QUEST)
        self.assertEqual(reply, {"effect": "native_quest_present", **record})
        bridge.command.assert_awaited_once_with(valve.START, BOROUGH_PAWN, timeout=8)
        host.checkpoint_native.assert_not_awaited()

    async def test_cancelled_board_acceptance_fences_the_current_save(self):
        host = authority()
        del host.party["native_quests"][valve.QUEST]
        host.last_game = {"state": "hosting", "map": BOROUGH, "pawns": {"local": {"name": BOROUGH_PAWN}}}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=asyncio.CancelledError))
        with self.assertRaises(asyncio.CancelledError):
            await native_quest.action(host, bridge, "host", "native-quest-start", valve.QUEST)
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
        self.assertNotIn(valve.QUEST, host.party["native_quests"])
        self.assertIsNone(host.pending_host_save)
        host.checkpoint_native.assert_not_awaited()

    async def test_only_exact_current_visit_commits_the_stock_activation_once(self):
        host = authority()
        before = host.party
        proof = objective()
        closed = snapshot("closed", proof)
        settling = snapshot("settling", proof)
        finalized = dict(settling, ack={"message": "exact Flood Control authority presentation requested"})
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[settling, finalized]), wait_for=AsyncMock(return_value=closed))
        with self.assertRaisesRegex(party.RuleError, "stale_visit"):
            await valve.activate(host, bridge, CHARACTER, OTHER)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()
        with patch.object(valve.asyncio, "sleep", new_callable=AsyncMock) as fade:
            receipt = await valve.activate(host, bridge, CHARACTER, EPOCH)
        fade.assert_awaited_once_with(1)
        self.assertEqual((receipt["quest_id"], receipt["actor"], receipt["step"], receipt["objective"]),
                         (valve.QUEST, CHARACTER, 2, proof))
        self.assertEqual(host.party["native_quests"][valve.QUEST]["objective"], proof)
        self.assertEqual(before["native_quests"][valve.QUEST]["step"], 1)
        self.assertEqual(bridge.command.await_args_list, [call(valve.ACTIVATE, PAWN, EPOCH, timeout=8),
                                                         call(valve.FINALIZE, PAWN, EPOCH, timeout=8)])
        self.assertTrue(bridge.wait_for.await_args.args[0](closed))
        self.assertIs(host.pending_host_save, False)
        self.assertFalse(host.native_checkpoint_unsafe)
        host.checkpoint_native.assert_not_awaited()
        self.assertEqual((await valve.activate(host, bridge, CHARACTER, EPOCH))["effect"], "native_subway_valve_already_closed")
        self.assertEqual(bridge.command.await_count, 2)

    async def test_unknown_post_dispatch_result_fences_the_current_save(self):
        host = authority()
        settling = dict(snapshot("settling", objective()), ack={"message": "exact Flood Control authority presentation requested"})
        bridge = SimpleNamespace(command=AsyncMock(return_value=settling),
                                 wait_for=AsyncMock(side_effect=asyncio.CancelledError))
        with patch.object(valve.asyncio, "sleep", new_callable=AsyncMock), self.assertRaises(asyncio.CancelledError):
            await valve.activate(host, bridge, "host", EPOCH)
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
        self.assertEqual(host.party["native_quests"][valve.QUEST]["step"], 1)
        self.assertIsNone(host.pending_host_save)
        host.checkpoint_native.assert_not_awaited()

    async def test_exact_stock_lock_mismatch_fences_scene_and_keeps_previous_gamerule_state(self):
        host = authority()
        before = host.party
        proof = objective()
        settling = snapshot("settling", proof)
        settling[valve.SCENE].update(box="present", sludge="present", audio="retired")
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[settling, RuntimeError("stock_subway_valve_scene_stock_box_still_present")]),
                                 snapshot=Mock(return_value=settling))
        with patch.object(valve.asyncio, "sleep", new_callable=AsyncMock), self.assertRaisesRegex(RuntimeError, "box=present"):
            await valve.activate(host, bridge, "host", EPOCH)
        self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
        self.assertIs(host.party, before)
        self.assertIsNone(host.pending_host_save)
        host.checkpoint_native.assert_not_awaited()

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


class FloodControlDeferredRewardsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host = authority()
        self.host.native_profile = Path(self.enterContext(TemporaryDirectory()))
        self.host.native_loot_input = True
        self.host.allowed_maps = {BOROUGH, CITY}
        self.host.pending_guest_saves = {}
        self.host.queue_guest_save = self.queue_guest_save
        self.host.roles = lambda: {"host": "host", "guest": CHARACTER}
        self.host.party["native_quests"][valve.QUEST].update(step=2, objective=objective())
        self.host.last_game = {
            "state": "hosting", "map": BOROUGH, "native_guest_save_retention": True,
            "native_guest_xp_ready": True, "pawns": {"local": {"name": BOROUGH_PAWN}},
        }
        self.connected = False
        self.guest_pawn = BOROUGH_PAWN.replace("_20", "_23")
        self.host.native_recipients = lambda: {CHARACTER: {"pawn": self.guest_pawn, "connected": self.connected}}
        self.pools = set()
        self.items = []
        self.xp = 0
        self.xp_delta = 1
        self.refusal = None
        self.bridge = SimpleNamespace(command=AsyncMock(side_effect=self.command))

    def queue_guest_save(self, character, pawn):
        self.host.pending_guest_saves[character] = (pawn, {})

    def write_template(self, slot):
        path = native_host_save.slot_path(self.host.native_profile, slot)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"GVAS exact artifact and helmet")

    async def command(self, verb, *arguments, timeout=None):
        frame = dict(self.host.last_game)
        area = frame["map"]
        if verb == valve.TURN_IN:
            self.assertEqual(arguments, (BOROUGH_PAWN,))
            fields = ["native-subway-valve-board", "turn-in", BOROUGH, BOROUGH_PAWN, valve.BOARD, "2", "-10", "1",
                      "9", "9", "7", "8", *TEMPLATE]
            frame[valve.BOARD_KEY] = valve.parse_board(fields, "hosting", BOROUGH)
            frame[valve.BOARD_KEY]["reputation"] = dict.fromkeys(valve.FACTIONS, 0)
        elif verb == valve.CAPTURE:
            slot, art, mask, *mode = arguments
            self.assertEqual([art, mask], TEMPLATE)
            if mode == ["memory"]:
                self.pools.add(slot)
            else:
                self.assertFalse(mode)
                self.write_template(slot)
            fields = ["native-subway-valve-reward", area, BOROUGH_PAWN, "captured", slot, *TEMPLATE, *TEMPLATE]
            frame[valve.REWARD] = valve.parse_reward(fields, "hosting", area)
        elif verb == valve.GRANT:
            slot, art, mask, pawn, personal_art, personal_mask, *mode = arguments
            self.assertEqual([art, mask], TEMPLATE)
            self.assertEqual(pawn, self.guest_pawn)
            self.assertEqual(mode, ["memory"] if slot in self.pools else [])
            if self.host.native_loot_input:
                self.assertEqual(self.host.pending_guest_saves[CHARACTER][0], pawn)
            if self.refusal:
                raise RuntimeError(self.refusal)
            ids = [personal_art, personal_mask]
            self.assertFalse(self.items)
            self.items.extend(ids)
            fields = ["native-subway-valve-reward", area, pawn, "granted", slot, *TEMPLATE, *ids]
            frame[valve.REWARD] = valve.parse_reward(fields, "hosting", area)
        elif verb == "native-guest-save-retain":
            self.assertEqual(arguments, (self.guest_pawn, CHARACTER))
            self.assertTrue(self.connected)
        elif verb == "native-guest-quest-xp":
            self.assertEqual(arguments, (self.guest_pawn, "1"))
            before = self.xp
            self.xp += self.xp_delta
            frame["native_guest_xp"] = {"map": area, "pawn": self.guest_pawn, "before": before, "after": self.xp}
        elif verb == "native-loot-flush-escrows":
            for slot in arguments:
                self.assertIn(slot, self.pools)
                self.write_template(slot)
        elif verb == "native-loot-release-escrows":
            self.assertEqual(self.xp, 1)
            for slot in arguments:
                self.pools.remove(slot)
        else:
            self.fail(f"Unexpected Flood Control command {verb}")
        return frame

    async def redeem(self):
        return await native_quest.action(self.host, self.bridge, "host", "native-quest-turnin", valve.QUEST)

    async def test_connected_guest_receives_both_stock_rows_and_xp_without_a_save(self):
        self.connected = True
        receipt = await self.redeem()
        self.assertListEqual(receipt["rewards"][CHARACTER], self.items)
        self.assertEqual((len(set(self.items + TEMPLATE)), self.xp), (4, 1))
        self.assertListEqual([c.args[0] for c in self.bridge.command.await_args_list],
                             [valve.TURN_IN, valve.CAPTURE, "native-guest-save-retain", valve.GRANT,
                              "native-guest-quest-xp", "native-loot-release-escrows"])
        self.assertTrue(self.host.pending_host_save)
        self.assertFalse(self.pools)
        self.assertNotIn(valve.PENDING, self.host.party)
        self.host.checkpoint_native.assert_not_awaited()
        self.assertEqual((await self.redeem())["effect"], "native_quest_already_settled")
        self.assertEqual(len(self.items), 2)

    async def test_offline_bundle_promotes_into_the_existing_schema_and_settles_after_travel(self):
        receipt = await self.redeem()
        pending = self.host.party[valve.PENDING]
        slot = pending["escrow"]["slot"]
        self.assertEqual(pending["pending"][CHARACTER], receipt["rewards"][CHARACTER])
        self.assertTrue(pending["escrow"]["pending"])
        self.assertFalse(native_host_save.slot_path(self.host.native_profile, slot).exists())
        updated, records = await native_loot.flush_pending_escrows(self.host, self.bridge, self.host.party)
        checkpoint = {"world": {"party": updated}, "characters": {CHARACTER: {"state": {}}}}
        native_host_save.verify_checkpoint(self.host.native_profile, checkpoint)
        self.assertEqual(native_host_save.references(checkpoint), [records[(valve.PENDING, None)]])

        # The normal save leaves a disk-backed template for the next world or process.
        self.pools.remove(slot)
        self.host.party = party.load_party(updated)
        self.host.last_game["map"] = CITY
        self.guest_pawn = f"Player_FP_C {CITY}.City_01_P:PersistentLevel.Player_FP_C_23"
        self.connected = True
        await valve.settle(self.host, self.bridge)
        self.assertEqual((self.items, self.xp), (receipt["rewards"][CHARACTER], 1))
        self.assertNotIn(valve.PENDING, self.host.party)
        self.host.checkpoint_native.assert_not_awaited()

    async def test_missing_guest_leaves_bundle_pending_but_template_failure_fences_campaign(self):
        await self.redeem()
        pending = self.host.party[valve.PENDING]
        self.connected = True
        self.refusal = "stock_subway_valve_reward_recipient_unavailable"
        await valve.settle(self.host, self.bridge)
        self.assertFalse(self.host.stopping or self.host.native_checkpoint_unsafe)
        self.assertIs(self.host.party[valve.PENDING], pending)
        self.assertEqual((self.items, self.xp), ([], 0))

        self.refusal = "mutation_unverified_stock_subway_valve_reward_template"
        with self.assertRaisesRegex(RuntimeError, "reward_template"):
            await valve.settle(self.host, self.bridge)
        self.assertTrue(self.host.stopping and self.host.native_checkpoint_unsafe)
        self.assertIs(self.host.party[valve.PENDING], pending)
        self.host.checkpoint_native.assert_not_awaited()

    async def test_no_deferred_commit_or_pool_release_after_unverified_bundle_mutation(self):
        await self.redeem()
        pending = self.host.party[valve.PENDING]
        self.connected = True
        self.refusal = "mutation_unverified_subway_valve_reward_delivery"
        with self.assertRaisesRegex(RuntimeError, "reward_delivery"):
            await valve.settle(self.host, self.bridge)
        self.assertTrue(self.host.stopping and self.host.native_checkpoint_unsafe)
        self.assertIs(self.host.party[valve.PENDING], pending)
        self.assertIn(pending["escrow"]["slot"], self.pools)
        self.assertEqual(self.xp, 0)
        self.host.checkpoint_native.assert_not_awaited()

    async def test_verified_bundle_still_requires_the_exact_xp_readback(self):
        await self.redeem()
        pending = self.host.party[valve.PENDING]
        self.connected = True
        self.xp_delta = 0
        with self.assertRaisesRegex(RuntimeError, "quest XP did not survive native readback"):
            await valve.settle(self.host, self.bridge)
        self.assertEqual(self.items, pending["pending"][CHARACTER])
        self.assertTrue(self.host.stopping and self.host.native_checkpoint_unsafe)
        self.assertIn(pending["escrow"]["slot"], self.pools)
        self.assertIs(self.host.party[valve.PENDING], pending)
        self.host.checkpoint_native.assert_not_awaited()

    async def test_older_host_uses_durable_capture_and_checkpoint(self):
        self.host.native_loot_input = False
        self.connected = True
        receipt = await self.redeem()
        self.assertEqual((self.items, self.xp), (receipt["rewards"][CHARACTER], 1))
        self.assertEqual(self.host.checkpoint_native.await_count, 2)
        self.assertEqual(self.host.checkpoint_native.await_args.kwargs["require_guest_frames"], {CHARACTER})
        self.assertFalse(self.pools or self.host.pending_guest_saves)
