import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_host_save, native_stash, party, runtime
from tools.silverfish.travel import OBSERVATORY


SITE = "A" * 32
GUEST = "guest-character"


class NativeStashTests(unittest.IsolatedAsyncioTestCase):
    async def test_resident_stash_render_is_confined_to_the_scoped_ticket(self):
        bridge = runtime.Bridge(Path("unused"), None)
        bridge.local_bridge = SimpleNamespace(command=AsyncMock())
        body = f"{'D' * 32}\t/Game/Classes/Items/Test.Test_C\t3\t0.25\t15\t0\n".encode("ascii")
        response = (f"schema\t1\nseq\t1\nsession\tclient\tconnected\t{OBSERVATORY}\t1\t1\n"
                    "native-stash-view-memory\t1\nnative-stash-view-memory-large\t1\n")

        async def command(request, timeout):
            header, _, raw = request.partition(b"\n")
            nonce, operation, sequence, area, site, count = header.decode("ascii").split("\t")
            self.assertEqual((operation, sequence, area, site, count, raw, timeout),
                             (native_stash.RENDER_MEMORY, "42", OBSERVATORY, SITE, "1", body, 5))
            return response + f"ack\t{nonce}\taccepted\t{native_stash.MEMORY_VIEW_ACK}\n"

        bridge.local_bridge.command.side_effect = command
        received = await bridge.command(native_stash.RENDER_MEMORY, "42", OBSERVATORY, SITE, "1",
                                        timeout=5, stash_payload=body)
        self.assertTrue(received[native_stash.MEMORY_VIEW])
        self.assertTrue(received[native_stash.LARGE_MEMORY_VIEW])
        bridge.local_bridge.command.reset_mock()
        for invalid in (None, "text", b"truncated", b"\r\n", b"x" * native_stash.MEMORY_VIEW_MAX_BYTES + b"\n"):
            with self.assertRaisesRegex(ValueError, "Invalid resident stash view payload"):
                await bridge.command(native_stash.RENDER_MEMORY, "42", OBSERVATORY, SITE, "1", stash_payload=invalid)
        with self.assertRaisesRegex(ValueError, "requires the resident stash view command"):
            await bridge.command("ordinary-command", stash_payload=body)
        bridge.local_bridge.command.assert_not_awaited()

        bridge.local_bridge.command.side_effect = lambda request, timeout: response + "ack\twrong\taccepted\t-\n"
        with self.assertRaisesRegex(RuntimeError, "did not match"):
            await bridge.command(native_stash.RENDER_MEMORY, "42", OBSERVATORY, SITE, "0", stash_payload=b"")
        self.assertIs(bridge._local_status, received)
        bridge.local_bridge.command.side_effect = lambda request, timeout: response + (
            f"ack\t{request.partition(b'\t')[0].decode()}\taccepted\t-\n"
            "native-guest-ui-body\t1\n\n")
        with self.assertRaisesRegex(RuntimeError, "unexpected body"):
            await bridge.command(native_stash.RENDER_MEMORY, "42", OBSERVATORY, SITE, "0", stash_payload=b"")
        self.assertIs(bridge._local_status, received)

    async def test_large_resident_ticket_binds_payload_and_prefix_only_with_native_support(self):
        bridge = runtime.Bridge(Path("unused"), None)
        limit = native_stash.LARGE_MEMORY_VIEW_MAX_BYTES
        bridge.local_bridge = SimpleNamespace(command=AsyncMock(), stash_body_limit=limit)
        body = b"x" * (limit - 1) + b"\n"

        async def command(request, _timeout):
            header, separator, raw = request.partition(b"\n")
            self.assertEqual(raw, body)
            self.assertTrue(separator)
            nonce = header.split(b"\t", 1)[0].decode()
            return (f"schema\t1\nseq\t1\nsession\tclient\tconnected\t{OBSERVATORY}\t1\t1\n"
                    f"ack\t{nonce}\taccepted\t{native_stash.MEMORY_VIEW_ACK}\n")

        bridge.local_bridge.command.side_effect = command
        command_args = native_stash.RENDER_MEMORY, "1", OBSERVATORY, SITE, "1"
        await bridge.command(*command_args, stash_payload=body)
        for args, invalid in ((command_args, body + b"\n"),
                              ((native_stash.RENDER_MEMORY, "1", "x" * 1024, SITE, "1"), body),
                              (("ordinary-command",), body)):
            with self.assertRaises(ValueError):
                await bridge.command(*args, stash_payload=invalid)
        bridge.local_bridge.stash_body_limit = 0
        with self.assertRaisesRegex(ValueError, "Invalid resident stash view payload"):
            await bridge.command(*command_args, stash_payload=body)
        bridge.local_bridge.command.assert_awaited_once()

    async def test_stash_view_uses_memory_for_split_and_empty_pages_with_legacy_fallback(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        item, created = "D" * 32, "E" * 32
        rows = [f"{created}\t/Game/Classes/Items/Test.Test_C\t3\t0.25\t15\t0\n"]
        large_page = [rows[0].replace(created, f"{index:032X}", 1) for index in range(1, 512)]
        self.assertGreater(len("".join(large_page)), 32 * 1024)
        view = dict(state="pending", sequence=1, map=OBSERVATORY, pawn="guest-pawn", site=SITE,
                    mode="stash", offset=0, item=None)
        game = {"map": OBSERVATORY, "pawns": {"local": {"name": view["pawn"]}}}
        bridge = SimpleNamespace(directory=directory, local_bridge=object(), input_namespace=uuid.uuid4())
        client = SimpleNamespace(bridge=bridge, invite={"character_id": GUEST},
                                 transport=SimpleNamespace(game_access=True), stopping=False,
                                 gameplay={native_stash.SITES: {SITE: {"map": OBSERVATORY}}},
                                 host_session="session", zone_epoch="epoch",
                                 guest_ui=SimpleNamespace(refresh=AsyncMock()), control=Mock(),
                                 gameplay_action=AsyncMock())
        for scenario in ("resident", "empty", "old-lua", "external", "large", "large-old-lua", "large-old-native",
                         "bad-receipt"):
            with self.subTest(scenario=scenario):
                page = [] if scenario == "empty" else large_page if scenario.startswith("large") else rows
                in_memory = scenario in {"resident", "empty", "large", "bad-receipt"}
                pending = ({**view, "item": item, "split_amount": 3, "expected": 7}
                           if scenario == "resident" else view)
                client.last_game = {**game, native_stash.VIEW: pending, native_stash.MEMORY_VIEW: scenario != "old-lua",
                                    native_stash.LARGE_MEMORY_VIEW: scenario in {"large", "large-old-native"}}
                client.gameplay_seen = time.monotonic()
                body_limit = native_stash.LARGE_MEMORY_VIEW_MAX_BYTES if scenario in {"large", "large-old-lua"} else 0
                bridge.local_bridge = None if scenario == "external" else SimpleNamespace(stash_body_limit=body_limit)
                client.gameplay_action.reset_mock()
                client.gameplay_action.return_value = {native_stash.MOVED: dict(owner=GUEST, site=SITE,
                    direction="split-withdraw", guid=item, quantity=3, remaining=4, created_guid=created)}
                rendered = {**client.last_game, native_stash.VIEW: {**pending, "state": "rendered"},
                            "ack": {"message": native_stash.MEMORY_VIEW_ACK if scenario != "bad-receipt" else "wrong"}}

                async def command(action, sequence, area, site, *extra, **kwargs):
                    self.assertEqual((sequence, area, site), ("1", OBSERVATORY, SITE))
                    if action == "native-stash-view-unavailable":
                        self.assertEqual(scenario, "bad-receipt")
                        return game
                    if in_memory:
                        self.assertEqual((action, extra, kwargs), (native_stash.RENDER_MEMORY, (str(len(page)),),
                            {"timeout": 5, "stash_payload": "".join(page).encode("ascii")}))
                        self.assertEqual(list(directory.iterdir()), [])
                    else:
                        nonce, count = extra
                        self.assertEqual((action, count, kwargs), ("native-stash-view-render", str(len(page)), {"timeout": 5}))
                        self.assertEqual((directory / f"native-stash-screen-{nonce}.tsv").read_bytes(),
                                         "".join(page).encode("ascii"))
                    return rendered

                bridge.command = AsyncMock(side_effect=command)
                with patch.object(native_stash, "_stock_rows", AsyncMock(return_value=page)), \
                        patch.object(runtime, "write_text", wraps=runtime.write_text) as write:
                    await native_stash.service_view(client, True)
                    self.assertEqual(write.call_count, 0 if in_memory else 1)
                self.assertEqual(list(directory.iterdir()), [])
                self.assertEqual(bridge.command.await_count, 2 if scenario == "bad-receipt" else 1)
                self.assertEqual(client.last_game, game if scenario == "bad-receipt" else rendered)
                if scenario == "resident":
                    action, _, target = client.gameplay_action.await_args.args
                    self.assertEqual((action, target), (native_stash.MOVE, f"{SITE}|split-withdraw|{item}|3|7"))
                    client.gameplay_action.assert_awaited_once()
                else:
                    client.gameplay_action.assert_not_awaited()

    async def test_split_rejects_stale_or_merge_target_before_a_checkpoint(self):
        state = party.new_party()
        party.add_player(state, "host")
        party.add_player(state, GUEST)
        game = {"state": "hosting", "map": OBSERVATORY, native_stash.SETTING: False,
                native_stash.SITES: {SITE: {"map": OBSERVATORY}}, native_stash.LIVE_MOVE: True,
                native_stash.LIVE_SPLIT: True, "native_guest_save_retention": True}
        host = SimpleNamespace(last_game=game, zone_epoch="zone", native_zones=True, native_profile=Mock(),
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                               party=state, smg_holds=None, native_recipients=lambda: {GUEST: {
                                   "pawn": "guest-pawn", "connected": True}},
                               pending_guest_saves={GUEST: ("guest-pawn", {})}, checkpoint_native=AsyncMock(), control=Mock())
        item, operation = "D" * 32, uuid.uuid4().hex
        bridge = SimpleNamespace(command=AsyncMock())
        for invalid in (f"{SITE}|split-deposit|{item}|7|7", f"{SITE}|split-withdraw|{item}|1|2147483648"):
            with self.assertRaisesRegex(party.RuleError, "exact_site_and_item"):
                await native_stash.move(host, bridge, GUEST, invalid, operation)
        bridge.command.assert_not_awaited()

        target = f"{SITE}|split-deposit|{item}|3|7"
        for status in ("native_stash_move_split_stale_amount", "native_stash_move_split_destination_stack"):
            bridge.command.side_effect = RuntimeError(status)
            with self.assertRaisesRegex(party.RuleError, status):
                await native_stash.move(host, bridge, GUEST, target, operation)
            self.assertEqual(host.party["receipts"], {})
            self.assertFalse(host.native_checkpoint_unsafe)
            self.assertFalse(host.stopping)

        async def stock_split(action, pawn, slot, site, guid, direction, combined, created, amount, expected, *, timeout):
            self.assertEqual((action, pawn, slot, site, guid, direction, combined, amount, expected),
                             (native_stash.MOVE, "guest-pawn", "-", SITE, item, "split-deposit", "0", "3", "7"))
            return {**game, native_stash.MOVED: dict(map=OBSERVATORY, pawn=pawn, slot=slot, site=site, guid=guid,
                direction=direction, combined=False, class_path="/Game/Classes/Items/Test.Test_C", quantity=3,
                remaining=4, created_guid=created)}

        bridge.command.reset_mock()
        bridge.command.side_effect = stock_split
        response = await native_stash.move(host, bridge, GUEST, target, operation)
        portion = response[native_stash.MOVED]
        self.assertNotEqual(portion["created_guid"], item)
        self.assertEqual((portion["owner"], portion["quantity"], portion["remaining"]), (GUEST, 3, 4))
        self.assertEqual(await native_stash.move(host, bridge, GUEST, target, operation), response)
        self.assertEqual(bridge.command.await_count, 1)
        host.checkpoint_native.assert_not_awaited()

    async def test_split_status_requires_both_verified_quantities_and_distinct_id(self):
        bridge = runtime.Bridge(Path("unused"), None)
        item, created = "D" * 32, "E" * 32
        base = (f"schema\t1\nseq\t1\nsession\thost\thosting\t{OBSERVATORY}\t1\t0\n"
                "native-stash-live-move\t1\nnative-stash-live-split\t1\n")
        move = (f"native-stash-move\t{OBSERVATORY}\tguest-pawn\t-\t{SITE}\t{item}\tsplit-withdraw"
                "\t0\t/Game/Classes/Items/Test.Test_C\t3")
        result = bridge._parse_status(base + move + f"\t4\t{created}\n")[native_stash.MOVED]
        self.assertEqual((result["quantity"], result["remaining"], result["created_guid"]), (3, 4, created))
        for extra in (f"\t0\t{created}", f"\t4\t{item}", ""):
            self.assertIsNone(bridge._parse_status(base + move + extra + "\n"))

        fields = ["native-stash-view", "pending", "1", OBSERVATORY, "guest-pawn", SITE, "stash", "0", item]
        view = native_stash.parse_view(fields + ["3", "7"], OBSERVATORY, "connected")
        self.assertEqual((view["split_amount"], view["expected"]), (3, 7))
        with self.assertRaisesRegex(ValueError, "stash split"):
            native_stash.parse_view(fields + ["7", "7"], OBSERVATORY, "connected")

    async def test_split_merge_receipt_keeps_the_existing_destination_and_is_idempotent(self):
        state = party.new_party()
        party.add_player(state, "host")
        party.add_player(state, GUEST)
        source, destination = "D" * 32, "E" * 32
        operation = uuid.uuid4().hex
        target = f"{SITE}|split-withdraw|{source}|3|7"
        game = {"state": "hosting", "map": OBSERVATORY, native_stash.SETTING: True,
                native_stash.SITES: {SITE: {"map": OBSERVATORY}}, native_stash.LIVE_MOVE: True,
                native_stash.LIVE_SPLIT: True, native_stash.LIVE_SPLIT_MERGE: True, "native_guest_save_retention": True}
        host = SimpleNamespace(last_game=game, zone_epoch="zone", native_zones=True, native_profile=Mock(),
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                               party=state, smg_holds=None,
                               native_recipients=lambda: {GUEST: {"pawn": "guest-pawn", "connected": True}},
                               pending_guest_saves={GUEST: ("guest-pawn", {})}, checkpoint_native=AsyncMock(),
                               control=Mock())
        merge = dict(guid=destination, before=8, after=11)
        bridge = SimpleNamespace(command=AsyncMock(return_value={**game, native_stash.MOVED: dict(
            map=OBSERVATORY, pawn="guest-pawn", slot="-", site=SITE, guid=source, direction="split-withdraw",
            combined=True, class_path="/Game/Classes/Items/Test.Test_C", quantity=3, remaining=4, stack_merge=merge)}))
        response = await native_stash.move(host, bridge, GUEST, target, operation)
        receipt = response[native_stash.MOVED]
        self.assertEqual((receipt["guid"], receipt["remaining"], receipt["stack_merge"]), (source, 4, merge))
        self.assertNotIn("created_guid", receipt)
        self.assertEqual(await native_stash.move(host, bridge, GUEST, target, operation), response)
        bridge.command.assert_awaited_once()
        host.checkpoint_native.assert_not_awaited()
        self.assertFalse(host.native_checkpoint_unsafe)

        status = runtime.Bridge(Path("unused"), None)
        base = (f"schema\t1\nseq\t1\nsession\thost\thosting\t{OBSERVATORY}\t1\t0\n"
                "native-stash-live-move\t1\nnative-stash-live-split\t1\n")
        capability = "native-stash-live-split-merge\t1\n"
        row = (f"native-stash-move\t{OBSERVATORY}\tguest-pawn\t-\t{SITE}\t{source}\tsplit-withdraw"
               "\t1\t/Game/Classes/Items/Test.Test_C\t3\t4")
        after = f"\t{destination}\t8\t11\n"
        self.assertIsNone(status._parse_status(base + row + after))
        self.assertEqual(status._parse_status(base + capability + row + after)[native_stash.MOVED]["stack_merge"], merge)
        for invalid in ((source, 8, 11), (destination, 0, 3), (destination, 8, 12),
                        (destination, 2**31 - 3, 2**31)):
            invalid_row = "\t" + "\t".join(map(str, invalid)) + "\n"
            self.assertIsNone(status._parse_status(base + capability + row + invalid_row))

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
                      ammo=7, value=29, weight=0.25, never_drop=False) for index in range(34)]

        def page(action, operation, target):
            site, raw_offset, *token = target.split("|")
            assert action == native_stash.LIST and site == SITE
            offset = int(raw_offset)
            size = native_stash.SNAPSHOT_PAGE_SIZE if token else 16
            end = offset + size
            result = dict(site=SITE, owner=GUEST, combined=False, offset=offset, total=len(stock),
                          items=stock[offset:end], next_offset=end if end < len(stock) else None)
            if token:
                result["snapshot"], = token
            return {native_stash.RESULT: result}

        client = SimpleNamespace(invite={"character_id": GUEST}, gameplay={},
                                 gameplay_action=AsyncMock(side_effect=page))
        rows = await native_stash._stock_rows(client, SITE, uuid.uuid4())
        self.assertEqual(len(rows), 34)
        self.assertEqual(rows[-1].split("\t"), [stock[-1]["guid"], stock[-1]["class_path"], "7", "0.25", "29", "0\n"])

        client.gameplay[native_stash.CENSUS_SNAPSHOT] = True
        client.gameplay_action.reset_mock()
        generation = uuid.uuid4()
        self.assertEqual(await native_stash._stock_rows(client, SITE, generation), rows)
        self.assertEqual([call.args[2] for call in client.gameplay_action.await_args_list],
                         [f"{SITE}|{offset}|{generation.hex}" for offset in (0, 32)])

        def wrong_token(*args):
            result = page(*args)
            if result[native_stash.RESULT]["offset"]:
                result[native_stash.RESULT]["snapshot"] = uuid.uuid4().hex
            return result

        client.gameplay_action.side_effect = wrong_token
        with self.assertRaisesRegex(ValueError, "exact stash"):
            await native_stash._stock_rows(client, SITE, generation)

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

        generation = uuid.uuid4().hex
        with self.assertRaisesRegex(party.RuleError, "snapshot_unavailable"):
            await native_stash.inspect(host, bridge, GUEST, f"{SITE}|0|{generation}")
        game[native_stash.CENSUS_SNAPSHOT] = True
        scoped = (await native_stash.inspect(host, bridge, GUEST, f"{SITE}|0|{generation}"))[native_stash.RESULT]
        self.assertEqual(scoped, {**live_view, "snapshot": generation})
        bridge.command.assert_awaited_with(native_stash.CENSUS, "guest-pawn", "-", SITE, "0", generation, timeout=5)

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
