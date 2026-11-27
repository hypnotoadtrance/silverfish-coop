from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_guest_stack_drop as drop, native_guest_drop_record as record, native_host_save, party
from tools.silverfish import native_guest_drop, native_host_stack_drop as menu_drop
from tools.silverfish.local_bridge import LocalCommandNotSubmitted
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
    async def test_host_menu_rejects_stale_epoch_and_dispatches_stock_intent_once(self):
        host, _ = self.make_host()
        scope = {"map": OBSERVATORY, "pawn": PAWN, "epoch": host.zone_epoch}
        host.smg_holds = None
        host.control.generation = "exact-test-host"
        host.last_game.update(pawns={"local": {"name": PAWN}}, **{menu_drop.CAPABILITY: 1, menu_drop.SCOPE: scope})
        fields = [menu_drop.INTENT_ROW, OBSERVATORY, PAWN, "e" * 32, "1", GUID, "9", "ctrl_click"]
        host.last_game[menu_drop.INTENT] = menu_drop.parse(fields, "hosting", OBSERVATORY)
        bridge = SimpleNamespace(command=AsyncMock())
        controller = menu_drop.HostDropInput()
        receipt = {native_guest_drop.RESULT: {"state": "dropped"}}
        with patch.object(native_guest_drop, "perform", AsyncMock(return_value=receipt)) as perform:
            await controller.poll(host, bridge)
            perform.assert_not_awaited()
            fields[3] = host.zone_epoch
            host.last_game[menu_drop.INTENT] = menu_drop.parse(fields, "hosting", OBSERVATORY)
            bridge.command.return_value = {**host.last_game, "ack": {"message": "refreshed"}}
            await controller.poll(host, bridge)
            await controller.poll(host, bridge)
            perform.assert_awaited_once()
            self.assertEqual(perform.await_args.args[:5], (host, bridge, "host", native_guest_drop.DROP, f"{GUID}|9"))
        bridge.command.assert_awaited_once_with(menu_drop.FINISH, host.zone_epoch, "1", timeout=2)
        self.assertEqual(host.control.event.call_args.kwargs["refresh"], "refreshed")

    async def test_host_split_drag_dispatches_only_the_staged_portion_once(self):
        host, _ = self.make_host()
        scope = {"map": OBSERVATORY, "pawn": PAWN, "epoch": host.zone_epoch}
        host.smg_holds = None
        host.control.generation = "split-drag-host"
        fields = [menu_drop.INTENT_ROW, OBSERVATORY, PAWN, host.zone_epoch, "7", NEW_GUID, "4", "split_drag_to_floor"]
        intent = menu_drop.parse(fields, "hosting", OBSERVATORY)
        host.last_game.update(pawns={"local": {"name": PAWN}}, **{
            menu_drop.CAPABILITY: 1, menu_drop.SCOPE: scope, menu_drop.INTENT: intent,
        })
        bridge = SimpleNamespace(command=AsyncMock(return_value={**host.last_game, "ack": {"message": "refreshed"}}))
        receipt = {native_guest_drop.RESULT: {"state": "dropped", "guid": NEW_GUID, "quantity": 4}}
        controller = menu_drop.HostDropInput()
        with patch.object(native_guest_drop, "perform", AsyncMock(return_value=receipt)) as perform:
            await controller.poll(host, bridge)
            await controller.poll(host, bridge)
            perform.assert_awaited_once()
            self.assertEqual(perform.await_args.args[:5], (host, bridge, "host", native_guest_drop.DROP, f"{NEW_GUID}|4"))
        bridge.command.assert_awaited_once_with(menu_drop.FINISH, host.zone_epoch, "7", timeout=2)
        self.assertEqual(host.control.event.call_args.kwargs["entry"], "split_drag_to_floor")

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
        magnum = snapshot([*row[:8], actor.replace("Ammo_Pistol_BP", "Ammo_Magnum_BP"), NEW_GUID])
        self.assertEqual(magnum[drop.KEY]["class_path"], "/Game/Classes/Items/Ammo_Magnum_BP.Ammo_Magnum_BP_C")
        ap = snapshot([*row[:8], actor.replace("Ammo_Pistol_BP", "Ammo_9mm_AP_BP"), NEW_GUID])
        self.assertEqual(ap[drop.KEY]["class_path"], "/Game/Classes/Items/Ammo_9mm_AP_BP.Ammo_9mm_AP_BP_C")
        self.assertIsNone(snapshot([*row[:8], actor.replace("Ammo_Pistol_BP", "Ammo_Unregistered_BP"), NEW_GUID]))

        stock_class = "/Game/Classes/Items/Item_PocketWatch.Item_PocketWatch_C"
        stock_actor = actor.replace("Ammo_Pistol_BP", "Item_PocketWatch")
        generic = [*row[:5], "1", *row[6:8], stock_actor, NEW_GUID, "1", "2", "3", stock_class]
        self.assertEqual(snapshot(generic)[drop.KEY]["class_path"], stock_class)
        self.assertIsNone(snapshot([*generic[:-1], stock_class.replace("PocketWatch", "CopperWire")]))

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

    async def test_memory_birth_can_retry_after_the_native_command_was_never_claimed(self):
        host, _ = self.make_host()
        host.last_game.update({drop.MEMORY_CAPABILITY: True, record.CAPABILITY: 1})
        status = host.last_game
        request = ("receipt-id", drop.BIRTH, PAWN, SLOT, GUID, "9", OBSERVATORY, "memory")
        header = "\t".join((drop.REPLY, "1", request[0], drop.BIRTH, "hosting", OBSERVATORY, "accepted", "reported"))
        row = [drop.BIRTH, *stock_row()[1:], "1", "2", "3"]
        raw = header + "\n" + "\t".join(row) + "\n"
        proof = drop.parse_memory_reply(raw, request)
        with self.assertRaisesRegex(RuntimeError, "another transaction"):
            drop.parse_memory_reply(header + "\n" + "\t".join([*row[:4], NEW_GUID, *row[5:]]) + "\n", request)
        command = AsyncMock(side_effect=[LocalCommandNotSubmitted("lane busy"), proof])
        bridge = SimpleNamespace(command=command, local_bridge=object())
        with self.assertRaisesRegex(party.RuleError, "bridge_busy"):
            await drop.perform(host, bridge, CHARACTER, GUID, 9, fixture=False, memory_slot=SLOT)
        self.assertFalse(host.native_checkpoint_unsafe or host.stopping)
        result = await drop.perform(host, bridge, CHARACTER, GUID, 9, fixture=False, memory_slot=SLOT)
        self.assertEqual(result[drop.KEY]["bridge_reply_bytes"], len(raw))
        self.assertIs(host.last_game, status)
        self.assertEqual(command.await_args_list[0], command.await_args_list[1])
        self.assertIs(command.await_args.kwargs["update_status"], False)
        host.checkpoint_native.assert_not_awaited()

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
