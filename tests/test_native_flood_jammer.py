import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_flood_jammer as jammer, native_rewards, party, runtime


class FloodJammerObjectiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_board_readback_requires_cooked_faction_ordinals(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pawn = "exact guest pawn"
        reader = runtime.Bridge(directory, None)
        host = SimpleNamespace()
        for spec, accepted, refused in ((jammer.FLOOD, "2", "8"), (jammer.BLACK_LAKE, "8", "2")):
            action = spec.command("preflight")
            stock = (
                ("schema", "1"), ("session", "host", "hosting", spec.board_map, "1", "0"),
                (spec.command(), action, spec.board_map, pawn, spec.board, spec.quest, "-", "-", "0", "0", "0", "0"),
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
            last_game={"state": "hosting", "map": spec.objective_map, f"{spec.key}_objective": proof},
            native_recipients=lambda: {"guest-character": {"pawn": "exact guest pawn"}},
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

    async def test_forced_stock_xp_is_queued_once_for_the_active_guest(self):
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
        host.checkpoint_native.assert_awaited_once()

    async def test_wrong_authored_target_cannot_record_progress(self):
        host = self.host(1)
        host.last_game["native_flood_jammer_objective"]["trigger"] = "other trigger"
        await jammer.observe(host, object())
        self.assertEqual(host.party["native_quests"][jammer.QUEST_ID]["step"], 1)
        host.checkpoint_native.assert_not_awaited()

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
        host.checkpoint_native.assert_awaited_once()

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
