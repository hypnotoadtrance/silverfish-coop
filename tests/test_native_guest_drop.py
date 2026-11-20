from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_guest_drop as drop, native_guest_drop_record as record
from tools.silverfish import native_guest_stack_drop as probe, native_host_save, party


MAP = record.OBSERVATORY
OWNER, OTHER = "guest-owner", "other-guest"
GUID, DROP, CLAIM = "A" * 32, "b" * 32, "c" * 32
PAWN = f"Player_FP_C {MAP}.Observitory_P:PersistentLevel.Player_FP_C_82"
POS = [10., 20., 30.]


class GuestDropTests(unittest.IsolatedAsyncioTestCase):
    def make_world(self):
        profile = Path(self.enterContext(TemporaryDirectory()))
        native_host_save.directory(profile).mkdir(parents=True)

        def generation(letter):
            slot = "SFCoop_" + letter * 32
            native_host_save.slot_path(profile, slot).write_bytes(b"GVAS" + letter.encode() * 32)
            return native_host_save.record(profile, slot, MAP)

        source = {**generation("a"), "character_id": OWNER}
        escrow, replacement = generation("b"), generation("c")
        state = party.new_party()
        for owner in (OWNER, OTHER):
            party.add_player(state, owner)
        frozen = {"revision": 42, "characters": [{"character_id": OWNER, "state": {"native_guest_save": source}}]}
        host = SimpleNamespace(fixtures=True, native_guest=True, native_zones=True, native_profile=profile, party=state,
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False, smg_holds=None,
                               last_seen=time.monotonic(), last_game={"state": "hosting", "map": MAP, probe.CAPABILITY: 1},
                               native_recipients=lambda: {character: {"connected": True, "pawn": PAWN} for character in (OWNER, OTHER)},
                               roles=lambda: {"guest": OWNER, "guest2": OTHER},
                               authoritative_pawns=Mock(return_value={OWNER: {"map": MAP, "location": POS}}),
                               checkpoint_native=AsyncMock(return_value=43), control=Mock(),
                               store=Mock(status=Mock(return_value=frozen)))
        item = {"guid": GUID, "quantity": 9, "class_path": record.ITEM, "source": "backpack", "stackable": True, "important": False}
        row = dict(owner=OWNER, guid=GUID, quantity=9, class_path=record.ITEM, map=MAP,
                   location=POS, escrow=escrow, claim_id=None)
        return host, source, escrow, replacement, item, row

    def reply(self, host, verb, source, replacement=None):
        status = "applied" if verb == "debit" else "recovered"
        row = [probe.SETTLE, MAP, verb, PAWN, DROP, source["slot"], replacement["slot"] if replacement else "-", "1", status, "1"]
        return {**host.last_game, probe.SETTLE_KEY: probe.parse(row, "hosting", MAP)}

    async def test_debit_publishes_one_required_guest_frame_and_exact_escrow(self):
        host, source, escrow, replacement, item, expected = self.make_world()
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.reply(host, "debit", source, replacement)))
        birth = {probe.KEY: {"slot": source["slot"], "revision": 42, "location": POS}}
        prepared = {"bag": escrow, "replacement": replacement}
        with (patch.object(probe, "perform", AsyncMock(return_value=birth)),
              patch.object(drop.native_inventory, "read_complete", AsyncMock(return_value={"items": [item]})),
              patch.object(drop.native_inventory_death, "prepare", AsyncMock(return_value=prepared)) as prepare):
            result = await drop.perform(host, bridge, OWNER, drop.DROP, GUID + "|9", DROP)
        self.assertEqual(host.party[record.STATE][DROP], expected)
        self.assertEqual(prepare.await_args.args[2]["dropped"], [GUID])
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({OWNER})})
        self.assertEqual(record.active_frames(host.party, {OWNER}), [escrow])
        self.assertEqual(drop.visible_drops(host.party, OTHER, MAP), {})
        self.assertNotIn("escrow", drop.visible_drops(host.party, OWNER, MAP)[DROP])
        host.party["receipts"].clear()
        self.assertEqual(await drop.perform(host, bridge, OWNER, drop.DROP, GUID + "|9", DROP), result)
        bridge.command.assert_awaited_once()

    async def test_reclaim_refuses_wrong_guest_map_and_distance_before_bridge(self):
        host, _, escrow, _, item, row = self.make_world()
        host.party[record.STATE] = {DROP: row}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.reply(host, "reclaim", escrow)))
        with self.assertRaisesRegex(party.RuleError, "not_owned"):
            await drop.perform(host, bridge, OTHER, drop.RECLAIM, DROP, CLAIM)
        host.authoritative_pawns.return_value[OWNER] = {"map": MAP, "location": [300., 20., 30.]}
        with self.assertRaisesRegex(party.RuleError, "out_of_range"):
            await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM)
        host.last_game["map"] = "/Game/Classes/Maps/Other"
        with self.assertRaisesRegex(party.RuleError, "fixture_unavailable"):
            await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()

    async def test_checkpoint_failure_preserves_durable_reclaim_and_fences_native_followup(self):
        host, _, escrow, _, item, row = self.make_world()
        host.party[record.STATE] = {DROP: row}
        host.checkpoint_native.side_effect = (44, OSError("paired commit interrupted"))
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.reply(host, "reclaim", escrow)))
        with patch.object(drop.native_inventory, "read_complete", AsyncMock(return_value={"items": [item]})):
            with self.assertRaisesRegex(OSError, "paired commit interrupted"):
                await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM)
        candidate = host.checkpoint_native.await_args.args[1]
        self.assertEqual(candidate[record.STATE][DROP]["claim_id"], CLAIM)
        self.assertEqual(record.active_frames(candidate, {OWNER}), [])
        self.assertEqual(record.active_frames(host.party, {OWNER}), [escrow])
        self.assertEqual((host.native_checkpoint_unsafe, host.stopping), (True, True))

    async def test_reclaim_keeps_exact_tombstone_and_replays_with_same_operation(self):
        host, _, escrow, _, item, row = self.make_world()
        host.party[record.STATE] = {DROP: row}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.reply(host, "reclaim", escrow)))
        with patch.object(drop.native_inventory, "read_complete", AsyncMock(return_value={"items": [item]})):
            result = await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM)
        self.assertEqual(result[drop.RESULT]["guid"], GUID)
        self.assertEqual(record.active_frames(host.party, {OWNER}), [])
        host.party["receipts"].clear()
        self.assertEqual(await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM), result)
        with self.assertRaisesRegex(party.RuleError, "already_reclaimed"):
            await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, "d" * 32)
        bridge.command.assert_awaited_once()

    def test_manifest_covers_active_escrow_and_rejects_reused_generation(self):
        host, source, escrow, _, _, row = self.make_world()
        host.party[record.STATE] = {DROP: row}
        checkpoint = {"world": {"map": MAP, "party": host.party}, "characters": {OWNER: {"state": {"native_guest_save": source}}}}
        self.assertEqual(native_host_save.references(checkpoint), [source, escrow])
        native_host_save.verify_checkpoint(host.native_profile, checkpoint)
        row["escrow"] = {key: value for key, value in source.items() if key != "character_id"}
        with self.assertRaisesRegex(RuntimeError, "shares another Unreal save generation"):
            native_host_save.verify_checkpoint(host.native_profile, checkpoint)
