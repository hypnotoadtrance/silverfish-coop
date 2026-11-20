import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_authored_pickup as pickup, party, runtime


SOURCE = pickup.PROTOTYPE_DRILL
AREA, ACTOR, CLASS = pickup.SOURCES[SOURCE]
PAWN, OWNER = "Player_FP_C exact enrolled pawn", "guest"
SLOT, GUID, EPOCH = "SFCoop_" + "a" * 32, "AB" * 16, "e" * 32


class PrototypeDrillPickupTests(unittest.IsolatedAsyncioTestCase):
    def snapshot(self, *, pawn=PAWN, guid=GUID, quest="Clt_DrillHead", step=1, before=2, after=2):
        bridge = runtime.Bridge(Path(self.enterContext(tempfile.TemporaryDirectory())), None)
        rows = (
            ("schema", 1),
            ("session", "host", "hosting", AREA, 1, 0),
            (pickup.PROTOTYPE_TAG, SOURCE, AREA, pawn, SLOT, guid, quest, step, before, after),
        )
        claim = ("native-authored-pickup-claim", SOURCE, AREA, PAWN, 1, 1, "claimed", GUID, SLOT)
        (bridge.directory / "status.tsv").write_text(
            "".join("\t".join(map(str, row)) + "\n" for row in (*rows[:2], claim, rows[2])), encoding="utf-8")
        return bridge.snapshot()

    def test_progress_requires_exact_claim_identity_active_quest_and_unchanged_positive_topic(self):
        proof = self.snapshot()[pickup.PROTOTYPE_KEY]
        self.assertEqual((proof["quest"], proof["step"], proof["topic_before"], proof["topic_after"]),
                         ("Clt_DrillHead", 1, 2, 2))
        for change in (dict(pawn="Player_FP_C rival"), dict(guid="CD" * 16), dict(quest="Clt_DrillHead_Brkr"),
                       dict(step=2), dict(before=0, after=0), dict(after=1)):
            with self.subTest(change=change):
                self.assertIsNone(self.snapshot(**change))

    def host(self):
        state = party.new_party()
        for name in ("host", OWNER):
            party.add_player(state, name)
        return SimpleNamespace(party=state, native_zones=True, native_profile=Path("staged-profile"), stopping=False,
                               transitioning=False, native_checkpoint_unsafe=False, zone_epoch=EPOCH,
                               last_seen=time.monotonic(), native_recipients=lambda: {OWNER: {"connected": True, "pawn": PAWN}},
                               last_game={"state": "hosting", "map": AREA, "pawns": {"local": {"name": "host pawn"}},
                                          pickup.SOURCES_KEY: {SOURCE: dict(source=SOURCE, map=AREA, actor=ACTOR,
                                                                            class_path=CLASS, state="available")}},
                               checkpoint_native=AsyncMock(return_value=1), control=Mock())

    async def test_one_guest_custody_and_repeat_without_another_stock_action(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.snapshot()))
        request = pickup.request_target(SOURCE, EPOCH)
        with (patch.object(pickup.native_host_save, "new_slot", return_value=SLOT),
              patch.object(pickup.native_host_save, "record")):
            result = await pickup.claim(host, bridge, OWNER, request, "a" * 32)
            self.assertEqual(await pickup.claim(host, bridge, OWNER, request, "a" * 32), result)
            with self.assertRaisesRegex(party.RuleError, "already_claimed"):
                await pickup.claim(host, bridge, "host", SOURCE, "b" * 32)
            with self.assertRaisesRegex(party.RuleError, "stale_zone"):
                await pickup.claim(host, bridge, OWNER, pickup.request_target(SOURCE, "f" * 32), "c" * 32)
        claim = result["native_authored_pickup_claim"]
        self.assertEqual((claim["guid"], claim["owner"], claim["topic_micro_drill"]), (GUID, OWNER, 2))
        self.assertEqual(host.party[pickup.STATE_KEY][SOURCE], claim)
        bridge.command.assert_awaited_once_with(pickup.ACTION, PAWN, SLOT, SOURCE, timeout=7)
        self.assertEqual(host.checkpoint_native.await_args.kwargs["require_guest_frames"], frozenset({OWNER}))

    async def test_missing_progress_proof_blocks_checkpoint_after_typed_mutation(self):
        host = self.host()
        changed = self.snapshot()
        del changed[pickup.PROTOTYPE_KEY]
        bridge = SimpleNamespace(command=AsyncMock(return_value=changed))
        with patch.object(pickup.native_host_save, "new_slot", return_value=SLOT):
            with self.assertRaisesRegex(RuntimeError, "preserved stock topic"):
                await pickup.claim(host, bridge, OWNER, pickup.request_target(SOURCE, EPOCH), "a" * 32)
        self.assertTrue(host.stopping and host.native_checkpoint_unsafe)
        self.assertEqual(host.checkpoint_native.await_count, 1)
        self.assertNotIn(pickup.STATE_KEY, host.party)
