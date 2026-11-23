import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_board_contract, native_flood_jammer as jammer, native_quest, native_rewards, party, runtime


class FloodJammerObjectiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_board_readback_requires_cooked_faction_ordinals(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pawn = "exact guest pawn"
        reader = runtime.Bridge(directory, None)
        cases = ((jammer.FLOOD, jammer.BOROUGH, "2", "8"), (jammer.BLACK_LAKE, jammer.BLACK_MARKET, "8", "2"),
                 (jammer.BLACK_LAKE, jammer.OBSERVATORY, "8", "2"), (jammer.CROSSROADS_JAMMER, jammer.OBSERVATORY, "8", "2"))
        for spec, area, accepted, refused in cases:
            host = SimpleNamespace(last_game={"map": area})
            action = spec.command("preflight")
            stock = (
                ("schema", "1"), ("session", "host", "hosting", area, "1", "0"),
                (spec.command(), action, area, pawn, spec.board_at(area), spec.quest, "-", "-", "0", "0", "0", "0"),
                (spec.command("reputation"), "5", "0", "0"),
            )
            for faction in (accepted, refused):
                rows = stock + ((spec.command("reputation"), faction, "0", "0"),)
                (directory / "status.tsv").write_text("\n".join("\t".join(row) for row in rows), encoding="utf-8")
                bridge = SimpleNamespace(command=AsyncMock(return_value=reader.snapshot()))
                if faction == accepted:
                    report = await jammer._command(host, bridge, action, pawn, spec)
                    self.assertEqual(set(report["reputation"]), spec.factions)
                else:
                    with self.assertRaisesRegex(RuntimeError, "did not report the exact authored contract"):
                        await jammer._command(host, bridge, action, pawn, spec)

    def host(self, step, *, forced=False, spec=jammer.FLOOD):
        saved = party.new_party()
        for actor in ("host", "guest-character", "offline-character"):
            party.add_player(saved, actor)
        if step is not None:
            saved["native_quests"] = {spec.quest: {"quest_id": spec.quest, "step": step, "actor": "host"}}
        proof = {
            "map": spec.objective_map, "jammer": spec.jammer, "trigger": spec.trigger,
            "state": "stock_force_complete_verified" if forced else "stock_objective_verified",
            "step_before": step, "step_after": -7 if forced else 2,
            "xp_before": 4, "xp_after": 5 if forced else 4,
            "shooter_role": "guest", "shooter": "exact guest pawn", "projectile": "stock projectile",
        }
        return SimpleNamespace(
            native_guest=True, native_zones=True, native_profile="isolated",
            stopping=False, transitioning=False, native_checkpoint_unsafe=False, party=saved,
            pending_host_save=None, last_seen=time.monotonic(),
            last_game={"state": "hosting", "map": spec.objective_map, f"{spec.key}_objective": proof},
            native_recipients=lambda: {"guest-character": {"pawn": "exact guest pawn", "connected": True}},
            roles=lambda: {"host": "host", "guest": "guest-character"},
            checkpoint_native=AsyncMock(return_value=2), control=Mock(),
        )

    async def test_stock_progress_keeps_exact_guest_owner_and_no_early_payout(self):
        host = self.host(1)
        await jammer.observe(host, object())
        updated = host.party["native_quests"][jammer.QUEST_ID]
        self.assertEqual((updated["step"], updated["actor"]), (2, "guest-character"))
        self.assertEqual(updated["objective"]["projectile"], "stock projectile")
        self.assertEqual(host.party["economy"]["wallet"], 0)
        self.assertNotIn(native_rewards.PENDING, host.party["players"]["guest-character"])

    async def test_forced_stock_xp_is_queued_once_for_the_active_guest_without_a_save(self):
        host = self.host(None, forced=True)
        bridge = object()
        await jammer.observe(host, bridge)
        await jammer.observe(host, bridge)
        updated = host.party["native_quests"][jammer.QUEST_ID]
        self.assertEqual(updated["step"], -7)
        self.assertEqual(updated["recipients"], ["guest-character", "host"])
        self.assertEqual(host.party["players"]["host"]["quest_xp"], 1)
        self.assertEqual(host.party["players"]["guest-character"][native_rewards.PENDING], 1)
        self.assertNotIn(native_rewards.PENDING, host.party["players"]["offline-character"])
        host.checkpoint_native.assert_not_awaited()
        host.control.event.assert_called_once_with(f"{jammer.FLOOD.key}_objective_committed", revision=None, **updated)
        self.assertIs(host.pending_host_save, False)

    async def test_wrong_authored_target_cannot_record_progress(self):
        host = self.host(1)
        host.last_game["native_flood_jammer_objective"]["trigger"] = "other trigger"
        await jammer.observe(host, object())
        self.assertEqual(host.party["native_quests"][jammer.QUEST_ID]["step"], 1)
        host.checkpoint_native.assert_not_awaited()

    async def test_stock_jammer_board_progress_and_payout_do_not_write(self):
        for spec in (jammer.CROSSROADS_JAMMER, jammer.BLACK_LAKE, jammer.FLOOD):
            with self.subTest(spec=spec.key):
                host = self.host(1, spec=spec)
                objective_snapshot = host.last_game
                host.party.pop("native_quests")
                host.last_game = {"state": "hosting", "map": spec.board_map}
                guest = "guest-character"

                def snapshot(verb, step_before, step_after, *, payout=0):
                    operation = spec.command(verb)
                    proof = dict(
                        operation=operation, map=spec.board_map, pawn="exact guest pawn", board=spec.board, quest=spec.quest,
                        step_before=step_before, step_after=step_after, cash_before=10, cash_after=10 + payout,
                        xp_before=2, xp_after=2 + bool(payout),
                        reputation={faction: {"before": 0, "after": 20 if payout else 0} for faction in spec.factions},
                    )
                    return {"state": "hosting", "map": spec.board_map, spec.key: proof}

                bridge = SimpleNamespace(command=AsyncMock(side_effect=(
                    snapshot("preflight", None, None), snapshot("start", None, 1),
                    snapshot("preflight", 2, 2), snapshot("turn-in", 2, -10, payout=spec.cash),
                )))
                started = await jammer.act(host, bridge, guest, spec.command("start"))
                self.assertEqual(started["step"], 1)
                host.last_game = objective_snapshot
                await jammer.observe(host, bridge)
                self.assertEqual(host.party["native_quests"][spec.quest]["step"], 2)
                host.last_game = {"state": "hosting", "map": spec.board_map}
                settled = await jammer.act(host, bridge, guest, spec.command("turn-in"))
                repeated = await jammer.act(host, bridge, guest, spec.command("turn-in"))

                self.assertEqual((settled["step"], repeated["effect"]), (-10, "native_quest_already_settled"))
                self.assertEqual(host.party["economy"]["wallet"], 10 + spec.cash)
                self.assertEqual(host.party["players"][guest][native_rewards.PENDING], 1)
                self.assertIs(host.pending_host_save, False)
                self.assertFalse(host.native_checkpoint_unsafe)
                self.assertEqual(bridge.command.await_count, 4)
                host.checkpoint_native.assert_not_awaited()

    async def test_observatory_selected_board_scopes_both_native_jammer_calls(self):
        spec, area, board = jammer.CROSSROADS_JAMMER, jammer.OBSERVATORY, jammer.OBSERVATORY_BOUNTY_BOARD
        host = self.host(None, spec=spec)
        host.last_game = {"state": "hosting", "map": area}
        host.allowed_maps = {area}
        host.zone_epoch = "d3" * 16
        pawn = "exact guest pawn"

        def stock(verb, step):
            return {"state": "hosting", "map": area, spec.key: {
                "operation": spec.command(verb), "map": area, "pawn": pawn, "board": board, "quest": spec.quest,
                "step_before": None, "step_after": step, "cash_before": 0, "cash_after": 0, "xp_before": 0, "xp_after": 0,
                "reputation": {faction: {"before": 0, "after": 0} for faction in spec.factions},
            }}

        bridge = SimpleNamespace(command=AsyncMock(side_effect=(stock("preflight", None), stock("start", 1))))
        stale = native_board_contract.menu_target("e3" * 16, board, spec.quest)
        with self.assertRaisesRegex(party.RuleError, "stale_visit"):
            await native_quest.action(host, bridge, "guest-character", "native-quest-start", stale)
        wrong_board = native_board_contract.menu_target(host.zone_epoch, board.removesuffix("_2") + "_9", spec.quest)
        with self.assertRaisesRegex(party.RuleError, "requires_authored_board"):
            await native_quest.action(host, bridge, "guest-character", "native-quest-start", wrong_board)
        bridge.command.assert_not_awaited()

        selected = native_board_contract.menu_target(host.zone_epoch, board, spec.quest)
        result = await native_quest.action(host, bridge, "guest-character", "native-quest-start", selected)
        self.assertEqual((result["step"], result[spec.key]["board"]), (1, board))
        self.assertEqual([call.args for call in bridge.command.await_args_list], [
            (spec.command("preflight"), pawn, board), (spec.command("start"), pawn, board),
        ])
        host.checkpoint_native.assert_not_awaited()
        self.assertIs(host.pending_host_save, False)

    async def test_crossroads_can_adopt_an_authored_objective_accepted_in_an_older_host_save(self):
        spec = jammer.CROSSROADS_JAMMER
        host = self.host(1, spec=spec)
        del host.party["native_quests"]
        await jammer.observe(host, object())
        await jammer.observe(host, object())
        record = host.party["native_quests"][spec.quest]
        self.assertEqual((record["step"], record["actor"]), (2, "guest-character"))
        self.assertEqual(host.party["economy"]["wallet"], 0)
        host.checkpoint_native.assert_not_awaited()

    async def test_live_pose_matches_the_current_exact_host_actor(self):
        for spec in (jammer.FLOOD, jammer.BLACK_LAKE, jammer.CROSSROADS_JAMMER):
            with self.subTest(spec=spec.key):
                epoch = "e" * 32
                original = {"position": [2, 3, 4], "rotation": [0, 90, 0], "scale": [1, 1, 1]}
                desired = {**original, "position": [17, 20, 24], "rotation": [0, -90, 10]}
                pose = dict(map=spec.objective_map, actor=spec.jammer, transform=desired)
                local = dict(map=spec.objective_map, actor=spec.jammer, state="present", audio="-")
                game = {"state": "connected", "map": spec.objective_map, f"{spec.key}_projection": local,
                        jammer.POSE: {**pose, "transform": original}}
                authority = {"state": "hosting", "map": spec.objective_map, jammer.POSE: pose}
                frame = jammer.pose_frame(authority, epoch)
                self.assertEqual(frame, {**pose, "epoch": epoch})
                after = {**game, jammer.POSE: pose}
                bridge = SimpleNamespace(command=AsyncMock(return_value=after))
                client = SimpleNamespace(last_game=game, gameplay={jammer.POSE: {**frame, "epoch": "f" * 32}},
                                         zone_epoch=epoch, bridge=bridge, control=Mock())
                await jammer.project_client(client)
                bridge.command.assert_not_awaited()
                client.gameplay[jammer.POSE] = frame
                await jammer.project_client(client)
                await jammer.project_client(client)
                self.assertEqual(client.last_game, after)
                bridge.command.assert_awaited_once_with(spec.command("project"), "pose", "17", "20", "24",
                                                       "0", "-90", "10", "1", "1", "1", timeout=3)

    async def test_guest_projection_requires_the_committed_exact_stock_objective(self):
        host = self.host(1)
        live = {"map": jammer.FLOOD_PLAINS, "actor": jammer.JAMMER, "state": "present", "audio": "-"}
        client = SimpleNamespace(
            last_game={"state": "connected", "map": jammer.FLOOD_PLAINS, "native_flood_jammer_projection": live},
            gameplay={"native_quests": host.party["native_quests"]}, control=Mock(),
            bridge=SimpleNamespace(command=AsyncMock()),
        )
        await jammer.project_client(client)
        client.bridge.command.assert_not_awaited()
        await jammer.observe(host, object())
        client.gameplay["native_quests"] = host.party["native_quests"]
        hidden = {**live, "state": "hidden", "audio": "muted"}
        client.bridge.command.return_value = {**client.last_game, "native_flood_jammer_projection": hidden}
        await jammer.project_client(client)
        client.bridge.command.assert_awaited_once_with("native-flood-jammer-project", "destroyed", timeout=3)
        self.assertEqual(client.last_game["native_flood_jammer_projection"], hidden)


    async def test_lake_objective_cannot_advance_flood_or_project_from_its_receipt(self):
        spec = jammer.BLACK_LAKE
        host = self.host(1, spec=spec)
        host.party["native_quests"][jammer.FLOOD.quest] = {"quest_id": jammer.FLOOD.quest, "step": 1}
        key = f"{spec.key}_objective"
        proof = host.last_game[key]
        host.last_game[key] = {**proof, "jammer": jammer.FLOOD.jammer}
        await jammer.observe(host, object())
        host.checkpoint_native.assert_not_awaited()
        host.last_game[key] = proof
        await jammer.observe(host, object())
        self.assertEqual(host.party["native_quests"][spec.quest]["step"], 2)
        self.assertEqual(host.party["native_quests"][jammer.FLOOD.quest]["step"], 1)
        self.assertEqual(host.party["native_quests"][spec.quest]["actor"], "guest-character")
        host.checkpoint_native.assert_not_awaited()

        projection = f"{spec.key}_projection"
        live = {"map": spec.objective_map, "actor": spec.jammer, "state": "present", "audio": "-"}
        client = SimpleNamespace(
            last_game={"state": "connected", "map": spec.objective_map, projection: live},
            gameplay={"native_quests": {jammer.FLOOD.quest: host.party["native_quests"][spec.quest]}}, control=Mock(),
            bridge=SimpleNamespace(command=AsyncMock()),
        )
        await jammer.project_client(client)
        client.bridge.command.assert_not_awaited()
        client.gameplay["native_quests"] = host.party["native_quests"]
        hidden = {**live, "state": "hidden", "audio": "muted"}
        client.bridge.command.return_value = {**client.last_game, projection: hidden}
        await jammer.project_client(client)
        client.bridge.command.assert_awaited_once_with(spec.command("project"), "destroyed", timeout=3)
        self.assertEqual(client.last_game[projection], hidden)
