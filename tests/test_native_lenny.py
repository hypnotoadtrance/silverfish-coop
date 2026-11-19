import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_lenny as lenny, party, runtime


TARGET = f"NPC_Lenny_C {lenny.LEVEL}NPC_Lenny_C_2147483491"


class LennyObjectiveTests(unittest.IsolatedAsyncioTestCase):
    def host(self, proof):
        gameplay = party.new_party()
        for actor in ("host", "guest-character"):
            party.add_player(gameplay, actor)
        gameplay["native_quests"] = {lenny.SPEC.quest: {"quest_id": lenny.SPEC.quest, "step": 1}}
        return SimpleNamespace(
            native_profile="private", native_guest=True, native_zones=True, stopping=False,
            transitioning=False, native_checkpoint_unsafe=False, party=gameplay,
            last_game={"state": "hosting", "map": lenny.AREA, "native_lenny_objective": proof},
            native_recipients=lambda: {"guest-character": {"pawn": "exact guest"}},
            checkpoint_native=AsyncMock(return_value=33), control=Mock(),
        )

    def proof(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        rows = (
            ("schema", "1"), ("session", "host", "hosting", lenny.AREA, "1", "0"),
            ("native-lenny-objective", lenny.AREA, lenny.SPAWNER, TARGET, "stock_objective_verified",
             "1", "1", "2", "0", "1", "4", "4", "guest", "exact guest", "authority projectile", "0"),
        )
        (directory / "status.tsv").write_text("\n".join("\t".join(row) for row in rows), encoding="utf-8")
        return runtime.Bridge(directory, None).snapshot()["native_lenny_objective"]

    async def test_verified_stock_death_commits_once_and_corpse_retirement_does_not_change_receipt(self):
        proof = self.proof()
        host = self.host(proof)
        await lenny.observe(host, object())
        record = host.party["native_quests"][lenny.SPEC.quest]
        self.assertEqual((record["step"], record["actor"]), (2, "guest-character"))
        self.assertEqual(record["objective"]["flag_after"], 1)
        self.assertEqual(record["objective"]["projectile"], "authority projectile")
        self.assertEqual(host.party["economy"]["wallet"], 0)
        proof["health"] = None
        await lenny.observe(host, object())
        host.checkpoint_native.assert_awaited_once()
        self.assertFalse(host.native_checkpoint_unsafe)

    async def test_target_alias_and_wrong_death_flag_do_not_commit(self):
        proof = self.proof()
        host = self.host(proof)
        proof["target"] = TARGET.replace("NPC_Lenny_C_", "NPC_Bandit_C_")
        await lenny.observe(host, object())
        host.checkpoint_native.assert_not_awaited()
        proof.update(target=TARGET, flag_after=0)
        with self.assertRaisesRegex(RuntimeError, "disagreed"):
            await lenny.observe(host, object())
        self.assertTrue(host.stopping and host.native_checkpoint_unsafe)
        host.checkpoint_native.assert_not_awaited()
