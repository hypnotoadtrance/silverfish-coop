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
        stock = (
            ("schema", "1"), ("session", "host", "hosting", jammer.BOROUGH, "1", "0"),
            ("native-flood-jammer", jammer.PREFLIGHT, jammer.BOROUGH, pawn, jammer.native_wire_quest.BOARD,
             jammer.QUEST_ID, "-", "-", "0", "0", "0", "0"),
            ("native-flood-jammer-reputation", "5", "0", "0"),
        )
        reader = runtime.Bridge(directory, None)
        host = SimpleNamespace()
        for commune_id in ("2", "8"):
            rows = stock + (("native-flood-jammer-reputation", commune_id, "0", "0"),)
            (directory / "status.tsv").write_text("\n".join("\t".join(row) for row in rows), encoding="utf-8")
            bridge = SimpleNamespace(command=AsyncMock(return_value=reader.snapshot()))
            if commune_id == "2":
                report = await jammer._command(host, bridge, jammer.PREFLIGHT, pawn)
                self.assertEqual(set(report["reputation"]), {"2", "5"})
            else:
                with self.assertRaisesRegex(RuntimeError, "did not report the exact authored contract"):
                    await jammer._command(host, bridge, jammer.PREFLIGHT, pawn)

    def host(self, step, *, forced=False):
        saved = party.new_party()
        for actor in ("host", "guest-character", "offline-character"):
            party.add_player(saved, actor)
        if step is not None:
            saved["native_quests"] = {jammer.QUEST_ID: {"quest_id": jammer.QUEST_ID, "step": step, "actor": "host"}}
        proof = {
            "map": jammer.FLOOD_PLAINS, "jammer": jammer.JAMMER, "trigger": jammer.TRIGGER,
            "state": "stock_force_complete_verified" if forced else "stock_objective_verified",
            "step_before": step, "step_after": -7 if forced else 2,
            "xp_before": 4, "xp_after": 5 if forced else 4,
            "shooter_role": "guest", "shooter": "exact guest pawn", "projectile": "stock projectile",
        }
        return SimpleNamespace(
            native_guest=True, native_zones=True, native_profile="isolated",
            stopping=False, transitioning=False, native_checkpoint_unsafe=False, party=saved,
            last_game={"state": "hosting", "map": jammer.FLOOD_PLAINS, "native_flood_jammer_objective": proof},
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
