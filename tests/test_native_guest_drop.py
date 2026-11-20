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
    def make_world(self, area=MAP):
        profile = Path(self.enterContext(TemporaryDirectory()))
        native_host_save.directory(profile).mkdir(parents=True)

        def generation(letter):
            slot = "SFCoop_" + letter * 32
            native_host_save.slot_path(profile, slot).write_bytes(b"GVAS" + letter.encode() * 32)
            return native_host_save.record(profile, slot, area)

        source = {**generation("a"), "character_id": OWNER}
        escrow, replacement = generation("b"), generation("c")
        state = party.new_party()
        for owner in ("host", OWNER, OTHER):
            party.add_player(state, owner)
        frozen = {"revision": 42, "characters": [{"character_id": OWNER, "state": {"native_guest_save": source}}]}
        prefix = f"Player_FP_C {area}.{record.MAPS[area]}:PersistentLevel.Player_FP_C_"
        pawns = {"host": prefix + "1", OWNER: prefix + "82", OTHER: prefix + "83"}
        host = SimpleNamespace(zone_epoch="f" * 32, fixtures=True, native_guest=True, native_zones=True, native_profile=profile, party=state,
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False, smg_holds=None,
                               last_seen=time.monotonic(), last_game={"state": "hosting", "map": area, record.CAPABILITY: 1,
                                   "pawns": {"local": {"name": pawns["host"], "health": 100}}},
                               native_recipients=lambda: {character: {"connected": True, "pawn": pawns[character]}
                                                          for character in (OWNER, OTHER)},
                               roles=lambda: {"host": "host", "guest": OWNER, "guest2": OTHER},
                               authoritative_pawns=Mock(return_value={name: {"map": area, "location": POS} for name in pawns}),
                               checkpoint_native=AsyncMock(return_value=43), control=Mock(),
                               store=Mock(status=Mock(return_value=frozen)))
        item = {"guid": GUID, "quantity": 9, "class_path": record.ITEM, "source": "backpack", "stackable": True, "important": False}
        row = dict(owner=OWNER, guid=GUID, quantity=9, class_path=record.ITEM, map=area,
                   location=POS, escrow=escrow, claim_id=None)
        return host, source, escrow, replacement, item, row

    def reply(self, host, verb, source, replacement=None, *, recipient=OWNER):
        status = "applied" if verb == "debit" else "recovered"
        area = host.last_game["map"]
        pawn = (host.last_game["pawns"]["local"]["name"] if recipient == "host"
                else host.native_recipients()[recipient]["pawn"])
        row = [probe.SETTLE, area, verb, pawn, DROP, source["slot"], replacement["slot"] if replacement else "-", "1", status, "1"]
        return {**host.last_game, probe.SETTLE_KEY: probe.parse(row, "hosting", area)}

    async def test_debit_publishes_one_required_guest_frame_and_exact_escrow(self):
        host, source, escrow, replacement, item, expected = self.make_world()
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.reply(host, "debit", source, replacement)))
        birth = {probe.KEY: {"map": MAP, "pawn": PAWN, "slot": source["slot"], "revision": 42, "location": POS}}
        prepared = {"bag": escrow, "replacement": replacement}
        with (patch.object(probe, "perform", AsyncMock(return_value=birth)),
              patch.object(drop.native_inventory, "read_complete", AsyncMock(return_value={"items": [item]})),
              patch.object(drop.native_inventory_death, "prepare", AsyncMock(return_value=prepared)) as prepare):
            result = await drop.perform(host, bridge, OWNER, drop.DROP, GUID + "|9", DROP)
        self.assertEqual(host.party[record.STATE][DROP], expected)
        self.assertEqual(prepare.await_args.args[2]["dropped"], [GUID])
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({OWNER})})
        self.assertEqual(record.active_frames(host.party, {OWNER}), [escrow])
        self.assertNotIn("escrow", drop.visible_drops(host.party, MAP)[DROP])
        host.party["receipts"].clear()
        self.assertEqual(await drop.perform(host, bridge, OWNER, drop.DROP, GUID + "|9", DROP), result)
        bridge.command.assert_awaited_once()

    async def test_reclaim_refuses_inactive_guest_wrong_map_and_distance_before_bridge(self):
        host, _, escrow, _, _, row = self.make_world()
        host.party[record.STATE] = {DROP: row}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.reply(host, "reclaim", escrow)))
        host.party["players"][OTHER]["downed"] = True
        with self.assertRaisesRegex(party.RuleError, "exact_standing_guest_required"):
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

    async def test_teammate_claim_replays_only_for_the_claimant_and_preserves_drop_owner(self):
        host, _, escrow, _, item, row = self.make_world()
        host.party[record.STATE] = {DROP: row}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.reply(host, "reclaim", escrow, recipient=OTHER)))
        with patch.object(drop.native_inventory, "read_complete", AsyncMock(return_value={"items": [item]})):
            result = await drop.perform(host, bridge, OTHER, drop.RECLAIM, DROP, CLAIM)
        receipt = result[drop.RESULT]
        self.assertEqual((receipt["guid"], receipt["owner"], receipt["claimed_by"]), (GUID, OWNER, OTHER))
        self.assertEqual(record.active_frames(host.party, {OWNER, OTHER}), [])
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({OTHER})})
        self.assertEqual(await drop.perform(host, bridge, OTHER, drop.RECLAIM, DROP, CLAIM), result)
        host.party["receipts"].clear()
        self.assertEqual(await drop.perform(host, bridge, OTHER, drop.RECLAIM, DROP, CLAIM), result)
        with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
            await drop.perform(host, bridge, OTHER, drop.RECLAIM, "d" * 32, CLAIM)
        with self.assertRaisesRegex(party.RuleError, "already_reclaimed"):
            await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM)
        bridge.command.assert_awaited_once()

    async def test_host_claim_uses_exact_local_pawn_and_canonical_checkpoint(self):
        host, _, escrow, _, item, row = self.make_world()
        host.party[record.STATE] = {DROP: row}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.reply(host, "reclaim", escrow, recipient="host")))
        host.last_game["pawns"]["local"]["health"] = 0
        with self.assertRaisesRegex(party.RuleError, "exact_standing_host_required"):
            await drop.perform(host, bridge, "host", drop.RECLAIM, DROP, CLAIM)
        host.last_game["pawns"]["local"]["health"] = 100
        bridge.command.assert_not_awaited()
        with patch.object(drop.native_inventory, "read_complete", AsyncMock(return_value={"items": [item]})):
            result = await drop.perform(host, bridge, "host", drop.RECLAIM, DROP, CLAIM)
        self.assertEqual(result[drop.RESULT]["claimed_by"], "host")
        self.assertEqual(bridge.command.await_args.args[2], host.last_game["pawns"]["local"]["name"])
        self.assertTrue(all(not call.kwargs["require_guest_frames"] for call in host.checkpoint_native.await_args_list))
        self.assertEqual(record.active_frames(host.party, {OWNER}), [])

    async def test_host_debit_publishes_canonical_escrow_and_replays(self):
        host, source, escrow, replacement, item, expected = self.make_world()
        source = {key: value for key, value in source.items() if key != "character_id"}
        host.store.status.return_value["world"] = {"native_host_save": source}
        pawn = host.last_game["pawns"]["local"]["name"]
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.reply(host, "debit", source, replacement, recipient="host")))
        birth = {probe.KEY: {"map": MAP, "pawn": pawn, "slot": source["slot"], "revision": 42, "location": POS}}
        with (patch.object(probe, "perform", AsyncMock(return_value=birth)),
              patch.object(drop.native_inventory, "read_complete", AsyncMock(return_value={"items": [item]})),
              patch.object(drop.native_inventory_death, "prepare", AsyncMock(return_value={"bag": escrow, "replacement": replacement}))):
            result = await drop.perform(host, bridge, "host", drop.DROP, GUID + "|9", DROP)
        self.assertEqual(host.party[record.STATE][DROP], dict(expected, owner="host"))
        host.checkpoint_native.assert_awaited_once()
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset()})
        self.assertEqual(record.active_frames(host.party, {OWNER}), [escrow])
        host.party["receipts"].clear()
        self.assertEqual(await drop.perform(host, bridge, "host", drop.DROP, GUID + "|9", DROP), result)
        bridge.command.assert_awaited_once()

    async def test_legacy_durable_and_recent_claim_replays_fill_original_claimant(self):
        host, _, _, _, _, row = self.make_world()
        row.pop("escrow")
        row["claim_id"] = CLAIM
        host.party[record.STATE] = {DROP: row}
        old = {drop.RESULT: {**record.visible(row, DROP), "state": "reclaimed"}}
        host.party["receipts"][OWNER] = [{"id": CLAIM, "request": [drop.RECLAIM, DROP, None], "result": old}]
        bridge = SimpleNamespace(command=AsyncMock())
        self.assertEqual(record.active_frames(host.party, {OWNER}), [])
        expected = {drop.RESULT: {**old[drop.RESULT], "claimed_by": OWNER}}
        self.assertEqual(await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM), expected)
        host.party["receipts"].clear()
        self.assertEqual(await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM), expected)
        bridge.command.assert_not_awaited()

    def test_manifest_covers_active_escrow_and_rejects_reused_generation(self):
        host, source, escrow, _, _, row = self.make_world()
        host.party[record.STATE] = {DROP: row}
        checkpoint = {"world": {"map": MAP, "party": host.party}, "characters": {OWNER: {"state": {"native_guest_save": source}}}}
        self.assertEqual(native_host_save.references(checkpoint), [source, escrow])
        native_host_save.verify_checkpoint(host.native_profile, checkpoint)
        row["escrow"] = {key: value for key, value in source.items() if key != "character_id"}
        with self.assertRaisesRegex(RuntimeError, "shares another Unreal save generation"):
            native_host_save.verify_checkpoint(host.native_profile, checkpoint)

    def test_shared_claim_ledger_rejects_unknown_claimants_and_conflicting_operations(self):
        host, _, _, _, _, row = self.make_world()
        row.pop("escrow")
        row.update(claim_id=CLAIM, claimed_by=OTHER)
        host.party[record.STATE] = {DROP: row}
        with self.assertRaisesRegex(ValueError, "Invalid stock stack drop claimant"):
            record.active_frames(host.party, {OWNER})
        colliding = {**row, "owner": OTHER, "claimed_by": OWNER, "claim_id": "d" * 32}
        for records in ({DROP: row, CLAIM: colliding}, {CLAIM: colliding, DROP: row}):
            host.party[record.STATE] = records
            with self.assertRaisesRegex(ValueError, "stock stack drop (payload|operation identity)"):
                record.active_frames(host.party, {OWNER, OTHER})


    async def test_crossroads_reclaim_requires_the_exact_source_map_and_unchanged_epoch(self):
        host, _, escrow, _, item, row = self.make_world(record.CROSSROADS)
        host.party[record.STATE] = {DROP: row}
        bridge = SimpleNamespace(command=AsyncMock(return_value=self.reply(host, "reclaim", escrow)))
        origin = host.authoritative_pawns.return_value[OWNER]
        origin["map"] = MAP
        with self.assertRaisesRegex(party.RuleError, "out_of_range"):
            await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM)
        host.checkpoint_native.assert_not_awaited()
        bridge.command.assert_not_awaited()

        origin["map"] = record.CROSSROADS

        async def read_during_epoch_change(*args):
            host.zone_epoch = "d" * 32
            return {"items": [item]}

        with patch.object(drop.native_inventory, "read_complete", side_effect=read_during_epoch_change):
            with self.assertRaisesRegex(party.RuleError, "scope_changed"):
                await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM)
        bridge.command.assert_not_awaited()
        self.assertEqual(host.party[record.STATE][DROP], row)

        with patch.object(drop.native_inventory, "read_complete", AsyncMock(return_value={"items": [item]})):
            result = await drop.perform(host, bridge, OWNER, drop.RECLAIM, DROP, CLAIM)
        self.assertEqual(result[drop.RESULT]["map"], record.CROSSROADS)
        self.assertEqual(bridge.command.await_args.args[-1], record.CROSSROADS)

    def test_legacy_and_crossroads_drops_share_a_ledger_without_aliasing_frames(self):
        host, source, escrow, replacement, _, legacy = self.make_world()
        crossing = native_host_save.record(host.native_profile, replacement["slot"], record.CROSSROADS)
        other_id = "d" * 32
        newer = {**legacy, "owner": OTHER, "guid": "B" * 32, "map": record.CROSSROADS, "escrow": crossing}
        host.party[record.STATE] = {DROP: legacy, other_id: newer}
        checkpoint = {"world": {"map": record.CROSSROADS, "party": host.party},
                      "characters": {OWNER: {"state": {"native_guest_save": source}}, OTHER: {"state": {}}}}
        native_host_save.verify_checkpoint(host.native_profile, checkpoint)
        self.assertEqual(record.active_frames(host.party, {OWNER, OTHER}), [escrow, crossing])
        self.assertEqual(list(drop.visible_drops(host.party, record.CROSSROADS)), [other_id])
        self.assertEqual(list(drop.visible_drops(host.party, MAP)), [DROP])

        newer["escrow"] = {**crossing, "map": MAP}
        with self.assertRaisesRegex(ValueError, "Invalid or duplicate stock stack drop escrow"):
            record.active_frames(host.party, {OWNER, OTHER})
        newer["escrow"] = native_host_save.record(host.native_profile, escrow["slot"], record.CROSSROADS)
        with self.assertRaisesRegex(RuntimeError, "Invalid or duplicate stock stack drop escrow"):
            native_host_save.verify_checkpoint(host.native_profile, checkpoint)
