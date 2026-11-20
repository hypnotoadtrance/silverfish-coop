from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_guest_stack_drop as drop, native_guest_drop_record as record, native_host_save, party
from tools.silverfish.runtime import Bridge
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


LEVEL = OBSERVATORY + ".Observitory_P:PersistentLevel."
PAWN = f"Player_FP_C {LEVEL}Player_FP_C_200"
ACTOR = f"Ammo_Pistol_BP_C {LEVEL}Ammo_Pistol_BP_C_202"
SLOT, GUID, NEW_GUID = "SFCoop_" + "a" * 32, "B" * 32, "C" * 32
CHARACTER = "authenticated-guest"


def stock_row():
    return [drop.ACTION, OBSERVATORY, PAWN, SLOT, GUID, "9", "1", drop.SUCCESS, ACTOR, NEW_GUID]


class GuestStackDropTests(unittest.IsolatedAsyncioTestCase):
    def test_bridge_accepts_one_exact_host_only_report(self):
        path = Path(self.enterContext(TemporaryDirectory()))
        bridge = Bridge(path, Mock())

        def snapshot(*reports, state="hosting"):
            rows = ("schema\t1", f"session\tnative\t{state}\t{OBSERVATORY}\t1\t0",
                    drop.ACTION + "-capability\t1", *("\t".join(row) for row in reports))
            (path / "status.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
            return bridge.snapshot()

        report = snapshot(stock_row())
        self.assertEqual(report[drop.CAPABILITY], 1)
        self.assertEqual(report[drop.KEY]["actor"], ACTOR)
        self.assertNotIn(drop.CAPABILITY, snapshot(state="connected"))
        self.assertIsNone(snapshot(stock_row(), state="connected"))
        self.assertIsNone(snapshot(stock_row(), stock_row()))
        for index, wrong in ((2, "different pawn"), (3, "vanilla"), (5, "09"), (5, "201"),
                             (7, "forged_success"), (8, "different actor"), (9, GUID)):
            row = stock_row()
            row[index] = wrong
            self.assertIsNone(snapshot(row))

    def test_crossroads_birth_capability_requires_the_exact_map_and_class(self):
        path = Path(self.enterContext(TemporaryDirectory()))
        bridge = Bridge(path, Mock())
        area = CROSSROADS
        level = area + ".CrossRoads_P:PersistentLevel."
        pawn, actor = f"Player_FP_C {level}Player_FP_C_200", f"Ammo_Pistol_BP_C {level}Ammo_Pistol_BP_C_202"
        row = [drop.BIRTH, area, pawn, SLOT, GUID, "9", "1", drop.SUCCESS, actor, NEW_GUID]

        def snapshot(report):
            rows = ("schema\t1", f"session\tnative\thosting\t{area}\t1\t0", drop.BIRTH + "-capability\t1",
                    drop.ACTION + "-capability\t1", "\t".join(report))
            (path / "status.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
            return bridge.snapshot()

        report = snapshot(row)
        self.assertEqual(report[record.CAPABILITY], 1)
        self.assertNotIn(drop.CAPABILITY, report)
        self.assertEqual(report[drop.KEY]["actor"], actor)
        self.assertIsNone(snapshot([drop.ACTION, *row[1:]]))
        self.assertIsNone(snapshot([*row[:8], ACTOR, NEW_GUID]))
        self.assertIsNone(snapshot([*row[:8], actor.replace("Ammo_Pistol_BP", "Ammo_Magnum_BP"), NEW_GUID]))

    def make_host(self):
        profile = Path(self.enterContext(TemporaryDirectory()))
        file = native_host_save.slot_path(profile, SLOT)
        file.parent.mkdir(parents=True)
        file.write_bytes(b"GVAS" + bytes(30))
        frame = dict(native_host_save.record(profile, SLOT, OBSERVATORY), character_id=CHARACTER)
        campaign = {"revision": 5, "characters": [{"character_id": CHARACTER, "state": {"native_guest_save": frame}}]}
        state = party.new_party()
        party.add_player(state, CHARACTER)
        host = SimpleNamespace(zone_epoch="f" * 32, fixtures=True, native_guest=True, native_zones=True, native_profile=profile,
                               stopping=False, transitioning=False, last_seen=time.monotonic(), native_checkpoint_unsafe=False,
                               last_game={"state": "hosting", "map": OBSERVATORY, drop.CAPABILITY: 1},
                               native_recipients=lambda: {CHARACTER: {"connected": True, "pawn": PAWN}},
                               party=state, store=Mock(status=Mock(return_value=campaign)),
                               checkpoint_native=AsyncMock(return_value=5), control=Mock())
        return host, file

    async def test_success_reads_exact_paired_frame_and_returns_transient_receipt(self):
        host, file = self.make_host()
        original = file.read_bytes()
        proof = drop.parse(stock_row(), "hosting", OBSERVATORY)
        bridge = SimpleNamespace(command=AsyncMock(return_value={**host.last_game, drop.KEY: proof}))
        result = (await drop.perform(host, bridge, CHARACTER, GUID, 9))[drop.KEY]
        self.assertEqual((result["owner"], result["revision"], result["new_guid"]), (CHARACTER, 5, NEW_GUID))
        host.checkpoint_native.assert_awaited_once_with(bridge, require_guest_frames=frozenset({CHARACTER}))
        bridge.command.assert_awaited_once_with(drop.ACTION, PAWN, SLOT, GUID, "9", timeout=6)
        host.control.event.assert_called_once_with("native_guest_stack_drop_probed", **result)
        self.assertFalse(host.native_checkpoint_unsafe or host.stopping)
        self.assertEqual(file.read_bytes(), original)
        self.assertEqual(host.party["receipts"], {})

    async def test_safe_refusal_allows_gameplay_and_unverified_retirement_stops_checkpointing(self):
        for status, stopping in (("native_guest_stack_drop_stock_9mm_row_unqualified", False),
                                 ("mutation_unverified_native_guest_stack_drop_retirement", True)):
            with self.subTest(status=status):
                host, _ = self.make_host()
                row = stock_row()
                row[6:] = ["0", status, "-", "-"]
                proof = drop.parse(row, "hosting", OBSERVATORY)
                bridge = SimpleNamespace(command=AsyncMock(return_value={**host.last_game, drop.KEY: proof}))
                with self.assertRaisesRegex(RuntimeError if stopping else party.RuleError, status):
                    await drop.perform(host, bridge, CHARACTER, GUID, 9)
                self.assertEqual((host.stopping, host.native_checkpoint_unsafe), (stopping, stopping))
                host.control.event.assert_not_called()

    async def test_downed_guest_and_wrong_hash_do_not_reach_the_native_spawn(self):
        host, file = self.make_host()
        bridge = SimpleNamespace(command=AsyncMock())
        host.party["players"][CHARACTER]["downed"] = True
        with self.assertRaisesRegex(party.RuleError, "exact_standing_guest"):
            await drop.perform(host, bridge, CHARACTER, GUID, 9)
        host.checkpoint_native.assert_not_awaited()
        host.party["players"][CHARACTER]["downed"] = False
        file.write_bytes(file.read_bytes() + b"tampered")
        with self.assertRaisesRegex(RuntimeError, "does not match its campaign checkpoint"):
            await drop.perform(host, bridge, CHARACTER, GUID, 9)
        bridge.command.assert_not_awaited()
        self.assertFalse(host.native_checkpoint_unsafe or host.stopping)
