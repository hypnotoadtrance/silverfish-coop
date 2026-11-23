import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_host_save, native_stash, party, runtime
from tools.silverfish.travel import OBSERVATORY


SITE = "A" * 32
GUEST = "guest-character"


class NativeStashTests(unittest.IsolatedAsyncioTestCase):
    async def test_reorder_requires_live_retention_and_returns_exact_anchor(self):
        state = party.new_party()
        party.add_player(state, "host")
        party.add_player(state, GUEST)
        game = {"state": "hosting", "map": OBSERVATORY, native_stash.SETTING: False,
                native_stash.SITES: {SITE: {"map": OBSERVATORY}}, native_stash.LIVE_MOVE: True,
                native_stash.LIVE_REORDER: True}
        host = SimpleNamespace(last_game=game, zone_epoch="zone", native_zones=True, native_profile=Mock(),
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                               party=state, smg_holds=None, native_recipients=lambda: {GUEST: {
                                   "pawn": "guest-pawn", "connected": True}}, pending_guest_saves={},
                               checkpoint_native=AsyncMock(), control=Mock())
        item, before, operation = "D" * 32, "E" * 32, uuid.uuid4().hex
        target = f"{SITE}|reorder|{item}|{before}"
        bridge = SimpleNamespace(command=AsyncMock())
        for invalid in (f"{SITE}|reorder|{item}|{item}", f"{SITE}|deposit|{item}|{before}", target.rsplit("|", 1)[0]):
            with self.assertRaisesRegex(party.RuleError, "exact_site_and_item"):
                await native_stash.move(host, bridge, GUEST, invalid, operation)
        with self.assertRaisesRegex(party.RuleError, "native_guest_save_retention_unavailable"):
            await native_stash.move(host, bridge, GUEST, target, operation)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()

        game["native_guest_save_retention"] = True
        host.pending_guest_saves[GUEST] = ("guest-pawn", {})
        bridge.command.side_effect = RuntimeError("native_stash_move_reorder_anchor_unavailable")
        with self.assertRaisesRegex(party.RuleError, "reorder_anchor_unavailable"):
            await native_stash.move(host, bridge, GUEST, target, operation)
        self.assertEqual(host.party["receipts"], {})
        self.assertFalse(host.stopping)
        self.assertFalse(host.native_checkpoint_unsafe)
        host.checkpoint_native.assert_not_awaited()

        bridge.command.reset_mock()
        bridge.command.side_effect = None
        bridge.command.return_value = {**game, native_stash.MOVED: dict(map=OBSERVATORY, pawn="guest-pawn",
            slot="-", site=SITE, guid=item, direction="reorder", combined=False, before=before,
            class_path="/Game/Classes/Items/Test.Test_C", quantity=1)}
        result = await native_stash.move(host, bridge, GUEST, target, operation)
        self.assertEqual((result[native_stash.MOVED]["owner"], result[native_stash.MOVED]["before"]), (GUEST, before))
        bridge.command.assert_awaited_once_with(native_stash.MOVE, "guest-pawn", "-", SITE, item,
                                                "reorder", "0", before, timeout=6)
        self.assertEqual(await native_stash.move(host, bridge, GUEST, target, operation), result)
        self.assertEqual(bridge.command.await_count, 1)
        host.checkpoint_native.assert_not_awaited()

    async def test_reorder_status_requires_exact_anchor_and_preserves_legacy_transfer(self):
        bridge = runtime.Bridge(Path("unused"), None)
        item, before = "D" * 32, "E" * 32
        base = f"schema\t1\nseq\t1\nack\t-\tok\t-\nsession\thost\thosting\t{OBSERVATORY}\t1\t0\n"
        capability = "native-stash-live-move\t1\nnative-stash-live-reorder\t1\n"
        move = f"native-stash-move\t{OBSERVATORY}\tguest-pawn\t-\t{SITE}\t{item}\t"
        after = "\t0\t/Game/Classes/Items/Test.Test_C\t1"
        snapshot = bridge._parse_status(base + capability + move + "reorder" + after + f"\t{before}\n")
        self.assertEqual(snapshot[native_stash.MOVED]["before"], before)
        snapshot = bridge._parse_status(base + capability + move + "deposit" + after + "\n")
        self.assertNotIn("before", snapshot[native_stash.MOVED])
        self.assertIsNone(bridge._parse_status(base + capability + move + "reorder" + after + f"\t{item}\n"))

        fields = ["native-stash-view", "pending", "1", OBSERVATORY, "guest-pawn", SITE, "stash", "0", item]
        self.assertEqual(native_stash.parse_view(fields + [before], OBSERVATORY, "connected")["before"], before)
        self.assertEqual(native_stash.parse_view(fields + ["-"], OBSERVATORY, "connected")["before"], "-")
        self.assertNotIn("before", native_stash.parse_view(fields, OBSERVATORY, "connected"))
        with self.assertRaisesRegex(ValueError, "reorder"):
            native_stash.parse_view(fields + [item], OBSERVATORY, "connected")

    async def test_stock_screen_reads_every_page_for_the_same_character(self):
        stock = [dict(guid=f"{index + 1:032X}", source="stash", class_path="/Game/Classes/Weapons/Test.Test_C",
                      ammo=7, value=29, weight=0.25, never_drop=False) for index in range(17)]

        def page(action, operation, target):
            assert action == native_stash.LIST and target.startswith(f"{SITE}|")
            offset = int(target.rsplit("|", 1)[1])
            return {native_stash.RESULT: dict(site=SITE, owner=GUEST, combined=False, offset=offset,
                    total=len(stock), items=stock[offset:offset + 16], next_offset=16 if offset == 0 else None)}

        client = SimpleNamespace(invite={"character_id": GUEST}, gameplay_action=AsyncMock(side_effect=page))
        rows = await native_stash._stock_rows(client, SITE, uuid.uuid4())
        self.assertEqual(len(rows), 17)
        self.assertEqual(rows[-1].split("\t"), [stock[-1]["guid"], stock[-1]["class_path"], "7", "0.25", "29", "0\n"])

        def wrong_owner(*args):
            result = page(*args)
            if result[native_stash.RESULT]["offset"]:
                result[native_stash.RESULT]["owner"] = "host"
            return result

        client.gameplay_action.side_effect = wrong_owner
        with self.assertRaisesRegex(ValueError, "exact stash"):
            await native_stash._stock_rows(client, SITE, uuid.uuid4())

    async def test_stash_census_reads_only_the_authenticated_characters_frame(self):
        profile = Path(self.enterContext(tempfile.TemporaryDirectory()))
        native_host_save.directory(profile).mkdir(parents=True)

        def saved(letter):
            slot = f"SFCoop_{letter * 32}"
            native_host_save.slot_path(profile, slot).write_bytes(b"GVAS" + letter.encode() * 32)
            return native_host_save.record(profile, slot, OBSERVATORY)

        owner, visitor = saved("a"), {**saved("b"), "character_id": GUEST}
        campaign = dict(revision=5, world={"native_host_save": owner},
                        characters=[dict(character_id=GUEST, state={"native_guest_save": visitor})])
        state = party.new_party()
        party.add_player(state, "host")
        party.add_player(state, GUEST)
        game = {"state": "hosting", "map": OBSERVATORY, "pawns": {"local": {"name": "host-pawn"}},
                native_stash.SETTING: True, native_stash.SITES: {SITE: {"map": OBSERVATORY}}}
        host = SimpleNamespace(last_game=game, zone_epoch="zone", native_zones=True, native_profile=profile,
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                               party=state, store=Mock(status=Mock(return_value=campaign)),
                               native_recipients=lambda: {GUEST: {"pawn": "guest-pawn", "connected": True}},
                               checkpoint_native=AsyncMock(return_value=5))

        async def census(action, pawn, slot, site, offset, *, timeout):
            value = "C" if slot == owner["slot"] else "D"
            assert action == native_stash.CENSUS and site == SITE
            return {**game, "native_stash_census": dict(map=OBSERVATORY, pawn=pawn, slot=slot, site=site,
                combined=game[native_stash.SETTING], total=1, offset=int(offset), count=1, items=[
                    dict(guid=value * 32, source="stash", index=0, quantity=1, class_path="/Game/Classes/Items/Test.Test_C")])}

        bridge = SimpleNamespace(command=AsyncMock(side_effect=census))
        guest_view = (await native_stash.inspect(host, bridge, GUEST, SITE))[native_stash.RESULT]
        self.assertEqual((guest_view["owner"], guest_view["combined"], guest_view["items"][0]["guid"]),
                         (GUEST, True, "D" * 32))
        self.assertEqual(bridge.command.await_args.args[1:3], ("guest-pawn", visitor["slot"]))
        host.checkpoint_native.assert_awaited_with(bridge, require_guest_frames=frozenset({GUEST}))
        game[native_stash.SETTING] = False
        host_view = (await native_stash.inspect(host, bridge, "host", SITE))[native_stash.RESULT]
        self.assertEqual((host_view["owner"], host_view["combined"], host_view["items"][0]["guid"]),
                         ("host", False, "C" * 32))
        self.assertEqual(bridge.command.await_args.args[1:3], ("host-pawn", owner["slot"]))
        with self.assertRaisesRegex(party.RuleError, "exact_site"):
            await native_stash.inspect(host, bridge, GUEST, SITE + "|4097")
        with self.assertRaisesRegex(party.RuleError, "scope_unavailable"):
            await native_stash.inspect(host, bridge, "foreign-character", SITE)
        self.assertEqual(bridge.command.await_count, 2)

        game[native_stash.LIVE_CENSUS] = True
        host.checkpoint_native.reset_mock()
        row = dict(guid="D" * 32, source="stash", index=0, quantity=1, class_path="/Game/Classes/Items/Test.Test_C",
                   stackable=False, ammo=7)
        bridge.command.side_effect = None
        bridge.command.return_value = {**game, "native_stash_census": dict(map=OBSERVATORY, pawn="guest-pawn",
            slot="-", site=SITE, combined=False, total=1, offset=0, count=1, items=[row])}
        live_view = (await native_stash.inspect(host, bridge, GUEST, SITE))[native_stash.RESULT]
        self.assertEqual((live_view["owner"], live_view["items"]), (GUEST, [row]))
        bridge.command.assert_awaited_with(native_stash.CENSUS, "guest-pawn", "-", SITE, "0", timeout=5)
        host.checkpoint_native.assert_not_awaited()

        bridge.command.return_value["native_stash_census"]["pawn"] = "host-pawn"
        with self.assertRaisesRegex(RuntimeError, "exact character"):
            await native_stash.inspect(host, bridge, GUEST, SITE)
        host.checkpoint_native.assert_not_awaited()

    async def test_live_guest_transfer_requires_retention_and_current_scope(self):
        state = party.new_party()
        party.add_player(state, "host")
        party.add_player(state, GUEST)
        game = {"state": "hosting", "map": OBSERVATORY, native_stash.SETTING: True,
                native_stash.SITES: {SITE: {"map": OBSERVATORY}}, native_stash.LIVE_MOVE: True}
        recipient = {"pawn": "guest-pawn", "connected": True}
        host = SimpleNamespace(last_game=game, zone_epoch="zone", native_zones=True, native_profile=Mock(),
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                               party=state, smg_holds=None, native_recipients=lambda: {GUEST: recipient},
                               pending_guest_saves={}, queue_guest_save=Mock(),
                               checkpoint_native=AsyncMock(), control=Mock())
        bridge = SimpleNamespace(command=AsyncMock())
        item, operation = "D" * 32, uuid.uuid4().hex
        target = f"{SITE}|deposit|{item}"

        with self.assertRaisesRegex(party.RuleError, "native_guest_save_retention_unavailable"):
            await native_stash.move(host, bridge, GUEST, target, operation)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()

        game["native_guest_save_retention"] = True

        async def retain_during_reconnect(action, pawn, character, *, timeout):
            self.assertEqual((action, pawn, character), ("native-guest-save-retain", "guest-pawn", GUEST))
            recipient["pawn"] = "rejoined-pawn"
            return game.copy()

        bridge.command.side_effect = retain_during_reconnect
        with self.assertRaisesRegex(party.RuleError, "native_stash_scope_changed"):
            await native_stash.move(host, bridge, GUEST, target, operation)
        self.assertEqual(bridge.command.await_count, 1)
        host.checkpoint_native.assert_not_awaited()
        host.control.event.assert_not_called()
        self.assertEqual(host.party["receipts"], {})
        self.assertFalse(host.stopping)
        self.assertFalse(host.native_checkpoint_unsafe)

        bridge.command.reset_mock()
        bridge.command.side_effect = [
            game.copy(),
            {**game, native_stash.MOVED: dict(map=OBSERVATORY, pawn="rejoined-pawn", slot="-", site=SITE,
                guid=item, direction="deposit", combined=True, class_path="/Game/Classes/Items/Test.Test_C", quantity=1)},
        ]
        moved = await native_stash.move(host, bridge, GUEST, target, operation)
        self.assertEqual((moved[native_stash.MOVED]["owner"], moved[native_stash.MOVED]["guid"]), (GUEST, item))
        self.assertEqual(bridge.command.await_count, 2)
        bridge.command.assert_awaited_with(native_stash.MOVE, "rejoined-pawn", "-", SITE, item, "deposit", "1", timeout=6)
        self.assertEqual(await native_stash.move(host, bridge, GUEST, target, operation), moved)
        self.assertEqual(bridge.command.await_count, 2)
        host.checkpoint_native.assert_not_awaited()
