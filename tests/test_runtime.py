import asyncio
import ctypes
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import runtime, service
from tools.silverfish.local_bridge import INPUT_LIMIT, LocalBridge
from tools.silverfish.control import Control


class LocalBridgeInputTests(unittest.TestCase):
    def test_reads_only_the_requested_batch_and_preserves_record_boundaries(self):
        library = Mock()
        bridge = LocalBridge(library)
        first = b"wire-board\trow\n"
        second = b"interact\t1\n"
        third = b"next"
        records = [first, second, third]

        def read(buffer, capacity, lengths, limit):
            self.assertGreaterEqual(capacity, INPUT_LIMIT * limit)
            batch = records[:limit]
            del records[:limit]
            packed = b"".join(batch)
            ctypes.memmove(buffer, packed, len(packed))
            for index, line in enumerate(batch):
                lengths[index] = len(line)
            return len(batch)

        library.SilverfishBridgeReadInputBatchV1.side_effect = read
        self.assertEqual(bridge.read_input(0), [])
        self.assertEqual(bridge.read_input(2), [first[:-1], second[:-1]])
        self.assertEqual(bridge.read_input(), [third])
        self.assertEqual(bridge.read_input(), [])
        library.SilverfishBridgeReadInputV1.assert_not_called()

    def test_older_native_bridge_can_still_read_one_record_at_a_time(self):
        library = Mock()
        del library.SilverfishBridgeReadInputBatchV1
        bridge = LocalBridge(library)
        rows = iter([b"interact\t1\n", b""])

        def read(buffer, capacity):
            row = next(rows)
            ctypes.memmove(buffer, row, len(row))
            return len(row)

        library.SilverfishBridgeReadInputV1.side_effect = read
        self.assertEqual(bridge.read_input(), [b"interact\t1"])


class BridgeWalkTests(unittest.IsolatedAsyncioTestCase):
    async def test_continuous_walk_uses_the_native_three_second_limit(self):
        bridge = runtime.Bridge(Path("unused"), None)
        with patch.object(bridge, "command", new_callable=AsyncMock) as command:
            await bridge.walk(0, 1, 3000, continuous=True)
            command.assert_awaited_once_with("walk-continuous", "0", "1", "3000")
            with self.assertRaisesRegex(ValueError, "50 to 3000 ms"):
                await bridge.walk(0, 1, 3001, continuous=True)
            command.assert_awaited_once()

    async def test_discrete_walk_keeps_its_one_second_limit(self):
        bridge = runtime.Bridge(Path("unused"), None)
        with patch.object(bridge, "command", new_callable=AsyncMock) as command:
            await bridge.walk(0, 1, 1000)
            command.assert_awaited_once_with("walk", "0", "1", "1000")
            with self.assertRaisesRegex(ValueError, "50 to 1000 ms"):
                await bridge.walk(0, 1, 1001)
            command.assert_awaited_once()


class LocalCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_stock_loot_inspection_receipt_preserves_the_resident_world_frame(self):
        bridge = runtime.Bridge(Path("unused"), None)
        area = runtime.AUTHORED_CRATES[runtime.native_loot.CRATE]
        crate = runtime.native_loot.CRATE
        pawn = "Player_FP_C exact participant"
        published = {"sequence": 42, "state": "hosting", "map": area}
        bridge._local_status = published
        reply_pawn = pawn

        async def reply(payload, _timeout):
            nonce, *_ = payload.decode().split("\t")
            row = ("native-loot", "inspect", area, crate, reply_pawn, "1", "ready",
                   "0", "0", "", "0", "24", "0", "0", "-", "0", "0")
            return f"native-loot-inspection-reply\t1\nack\t{nonce}\taccepted\tinspected\n" + "\t".join(row) + "\n"

        bridge.local_bridge = Mock(command=AsyncMock(side_effect=reply))
        response = await bridge.command("native-loot-inspect", pawn, crate)
        self.assertEqual(response["native_loot"]["distance"], 24)
        self.assertGreater(response[runtime.native_loot.INSPECTION_BYTES], 0)
        self.assertIs(bridge._local_status, published)

        reply_pawn = "Player_FP_C stale participant"
        with self.assertRaisesRegex(RuntimeError, "invalid scope"):
            await bridge.command("native-loot-inspect", pawn, crate)
        self.assertIs(bridge._local_status, published)

    async def test_pickup_presentation_receipts_do_not_replace_resident_status(self):
        bridge = runtime.Bridge(Path("unused"), None)
        bridge.local_bridge = Mock(command=AsyncMock())
        bridge._local_status = {"sequence": 42, "state": "hosting"}

        async def reply(request, timeout):
            nonce, action, *_ = request.decode().split("\t")
            return f"pickup-presentation-ack\t{nonce}\taccepted\t{action}\n"

        bridge.local_bridge.command.side_effect = reply
        for action in (runtime.native_pickup_audio.COMMAND, runtime.native_world_loot.RETIRE,
                       runtime.native_world_loot.PRESENT_HOST_CLAIM):
            result = await bridge.command(action, "fixture")
            self.assertEqual(set(result), {"ack"})
            self.assertEqual(result["ack"]["message"], action)
        self.assertEqual(bridge._local_status, {"sequence": 42, "state": "hosting"})

        bridge.local_bridge.command.side_effect = None
        bridge.local_bridge.command.return_value = "pickup-presentation-ack\twrong\taccepted\tretired\n"
        with self.assertRaisesRegex(RuntimeError, "did not match"):
            await bridge.command(runtime.native_world_loot.RETIRE, "fixture")

    async def test_embedded_command_accepts_exact_receipt_without_file_polling(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)

            async def reply(payload, timeout):
                request, action, argument = payload.decode().rstrip("\n").split("\t")
                self.assertEqual((action, argument, timeout), ("walk", "20", 3))
                return (f"schema\t1\nseq\t42\nack\t{request}\taccepted\tmove\n"
                        "session\tclient\tconnected\tObservatory\t1\t0\n")

            bridge.local_bridge = AsyncMock()
            bridge.local_bridge.command.side_effect = reply
            bridge.local_bridge.read_status = Mock(return_value=None)
            result = await bridge.command("walk", "20", timeout=3)
            self.assertEqual(result["sequence"], 42)
            self.assertEqual(result["ack"]["message"], "move")
            self.assertEqual(list(bridge.directory.iterdir()), [])
            status_file = bridge.directory / "status.tsv"
            frame = "schema\t1\nseq\t{}\nsession\tclient\tconnected\tObservatory\t1\t0\n"
            status_file.write_text(frame.format(41), encoding="utf-8")
            self.assertEqual(bridge.snapshot()["sequence"], 42)
            status_file.write_text(frame.format(43), encoding="utf-8")
            self.assertEqual(bridge.snapshot()["sequence"], 43)

    async def test_memory_status_replaces_file_polls_and_preserves_newer_command_receipts(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), Mock(poll=Mock(return_value=None)))
            frame = "schema\t1\nseq\t{}\nsession\tclient\tconnected\tObservatory\t1\t0\n"
            (bridge.directory / "status.tsv").write_text(frame.format(10), encoding="utf-8")
            bridge.local_bridge = Mock()
            bridge.local_bridge.read_status.return_value = frame.format(11)
            bridge.local_bridge.input_ready.return_value = False
            bridge.local_bridge.status_ready.return_value = True
            self.assertEqual(bridge.snapshot()["sequence"], 11)
            self.assertEqual(bridge.status_source, "memory")
            await asyncio.wait_for(bridge.wait_for_interaction(1), .2)

            bridge._local_status = bridge._parse_status(frame.format(12))
            self.assertEqual(bridge.snapshot()["sequence"], 12)
            bridge.local_bridge.read_status.return_value = frame.format(13)
            self.assertEqual(bridge.snapshot()["sequence"], 13)
            self.assertIsNone(bridge._local_status)

            bridge.local_bridge.read_status.return_value = None
            self.assertEqual(bridge.snapshot()["sequence"], 10)
            self.assertEqual(bridge.status_source, "file")

    async def test_embedded_inputs_wake_worker_without_reading_stale_file_input(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)
            bridge.local_bridge = Mock()
            bridge.local_bridge.input_ready.return_value = True
            bridge.local_bridge.read_input.return_value = [
                b"interact\t8", b"interact\t10\tstock-critter:1", b"interact\t11\tstock-critter:65536",
            ]
            (bridge.directory / "input.tsv").write_bytes(b"interact\t9\n")
            await asyncio.wait_for(bridge.wait_for_interaction(1), .2)
            self.assertEqual(bridge.interactions(), [
                (uuid.uuid5(bridge.input_namespace, str(sequence)).hex, target)
                for sequence, target in ((8, None), (10, "stock-critter:1"))
            ])

    async def test_full_interaction_lane_keeps_new_press_in_the_native_queue(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)
            bridge.local_bridge = Mock()
            bridge.local_bridge.input_ready.return_value = True
            bridge.local_bridge.read_input.return_value = [b"interact\t65"]
            bridge._local_interaction_records.extend(
                f"interact\t{sequence}".encode() for sequence in range(1, 65)
            )
            self.assertEqual(bridge.native_crate_inputs(), [])
            bridge.local_bridge.read_input.assert_not_called()
            self.assertEqual(bridge.interactions(), [
                (uuid.uuid5(bridge.input_namespace, str(sequence)).hex, None) for sequence in range(1, 65)
            ])
            self.assertEqual(bridge.interactions(), [(uuid.uuid5(bridge.input_namespace, "65").hex, None)])
            bridge.local_bridge.read_input.assert_called_once_with(64)

    async def test_crate_ring_input_does_not_consume_a_loose_interaction(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)
            bridge.local_bridge = Mock()
            bridge.local_bridge.input_ready.side_effect = [True, False, False]
            crate = b"loot-crate-open\tmap\tcrate\tpawn\t7\n"
            bridge.local_bridge.read_input.return_value = [b"interact\t8", crate]
            self.assertEqual(bridge.native_crate_inputs(), [crate])
            self.assertEqual(bridge.interactions(), [(uuid.uuid5(bridge.input_namespace, "8").hex, None)])
            self.assertEqual(bridge.native_crate_inputs(), [])
            self.assertEqual(list(bridge.directory.iterdir()), [])

    async def test_death_ring_records_are_consumed_once_and_keep_other_inputs(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)
            bridge.local_bridge = Mock()
            bridge.local_bridge.input_ready.side_effect = [True, False, False]
            bridge.local_bridge.read_input.return_value = [
                b"downed\tguest\t21\tPlayer_FP_C guest\n", b"interact\t8",
                b"downed\tlocal\t22\tPlayer_FP_C host\n",
            ]
            self.assertEqual(bridge.interactions(), [(uuid.uuid5(bridge.input_namespace, "8").hex, None)])
            self.assertEqual(bridge.native_deaths(), [
                (slot, uuid.uuid5(bridge.input_namespace, f"death{sequence}").hex, f"Player_FP_C {slot_pawn}")
                for slot, sequence, slot_pawn in (("guest", 21, "guest"), ("local", 22, "host"))
            ])
            self.assertEqual(bridge.native_deaths(), [])
            self.assertEqual(list(bridge.directory.iterdir()), [])

    async def test_resident_stardust_drains_in_memory_and_keeps_other_inputs(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)
            bridge.local_bridge = Mock()
            bridge.local_bridge.input_ready.side_effect = [True, False, False, False]
            guid = "A" * 32
            suffix = f"\tCrossRoads\tPlayer_FP_C host\t{guid}\taction_use\t1\t2\t3\n".encode()
            bridge.local_bridge.read_input.return_value = [
                b"fruiting-stardust-request\t1" + suffix,
                b"interact\t8",
                b"stardust-request\t2" + suffix,
                b"stardust-request\tbroken" + suffix,
                b"stardust-request\t3" + suffix,
            ]
            self.assertEqual([r.sequence for r in bridge.native_cube_requests("stardust")], [2, 3])
            fruiting, = bridge.native_cube_requests("fruiting-stardust")
            self.assertEqual((fruiting.sequence, fruiting.guid, fruiting.location), (1, guid, (1, 2, 3)))
            self.assertEqual(bridge.native_cube_requests("stardust"), [])
            self.assertEqual(bridge.interactions(), [(uuid.uuid5(bridge.input_namespace, "8").hex, None)])
            self.assertEqual(list(bridge.directory.iterdir()), [])

    async def test_legacy_stardust_still_reads_the_file_bridge_once(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)
            guid = "B" * 32
            (bridge.directory / "native-stardust-events").write_text(
                f"stardust-request\t1\tCrossRoads\tPlayer_FP_C host\t{guid}\taction_use\t1\t2\t3\n")
            request, = bridge.native_cube_requests("stardust")
            self.assertEqual((request.sequence, request.guid, request.location), (1, guid, (1, 2, 3)))
            self.assertEqual(bridge.native_cube_requests("stardust"), [])

    async def test_file_command_keeps_its_reply_when_memory_publishes_a_new_frame(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), Mock(poll=Mock(return_value=None)))
            frame = "schema\t1\nseq\t{}\nack\t{}\taccepted\t{}\nsession\tclient\tconnected\tObservatory\t1\t0\n"

            def publish(path, request):
                tag = request.split("\t", 1)[0]
                path.with_name("command-reply.tsv").write_text(frame.format(40, tag, "stock action applied"))
                path.with_name("status.tsv").write_text(frame.format(41, "later-memory-ticket", ""))

            with patch.object(runtime, "write_text", side_effect=publish):
                reply = await bridge.command("stock-fixture")
            self.assertEqual((reply["sequence"], reply["ack"]["message"]), (40, "stock action applied"))
            self.assertEqual(bridge.snapshot()["sequence"], 41)

    async def test_bad_embedded_receipt_does_not_fall_back_to_replay(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), None)
            bridge.local_bridge = AsyncMock()
            bridge.local_bridge.command.return_value = (
                "schema\t1\nseq\t42\nack\tother-request\taccepted\n"
                "session\tclient\tconnected\tObservatory\t1\t0\n"
            )
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                await bridge.command("leave")
            bridge.local_bridge.command.assert_awaited_once()
            self.assertEqual(list(bridge.directory.iterdir()), [])


class GameplayStatusTests(unittest.TestCase):
    def test_guest_vitals_are_separate_from_pawn_identity_and_ignored_at_title(self):
        bridge = runtime.Bridge(Path("unused"), None)
        pawn = "Player_FP_C OwnedGuest"
        header = "schema\t1\nseq\t42\nsession\tclient\t{}\tObservatory\t1\t0\n"
        rows = f"pawn\tlocal\t{pawn}\t120\t1\t2\t3\tController\t250\n"
        rows += f"native-guest-vitals\t{pawn}\t54.2\t210.75\n"
        parsed = bridge._parse_status(header.format("connected") + rows)
        self.assertEqual(parsed["native_guest_vitals"], {pawn: {"food": 54.2, "regen": 210.75}})
        self.assertEqual(parsed["pawns"]["local"]["health"], 120)
        self.assertNotIn("native_guest_vitals", bridge._parse_status(header.format("ready") + rows))

        extended = f"native-guest-vitals\t{pawn}\t54.2\t210.75\t0\t0\t0\t3\t1.7\n"
        parsed = bridge._parse_status(header.format("connected") + extended)
        self.assertEqual(parsed["native_guest_vitals"][pawn]["sickness"], 3)
        self.assertEqual(parsed["native_guest_vitals"][pawn]["rads"], 1.7)
        overflow = extended.replace("\t3\t1.7\n", "\t2147483648\t-1\n")
        parsed = bridge._parse_status(header.format("connected") + overflow)
        self.assertNotIn("sickness", parsed["native_guest_vitals"][pawn])
        self.assertNotIn("rads", parsed["native_guest_vitals"][pawn])

    def test_explosive_drum_status_accepts_authored_alias_and_keeps_old_blast_counts(self):
        bridge = runtime.Bridge(Path("unused"), None)
        area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        prefix = f"{area}.CrossRoads_P:PersistentLevel."
        actual, retired = prefix + "Prop_FeulDrum_C_33", prefix + "Prop_FuelDrum_C_34"
        frame = f"schema\t1\nseq\t20\nsession\thost\thosting\t{area}\t1\t1\n"
        rows = (f"native-explosive-canister\t{actual}\t1\t0\t1\t40\t100\t200\t300\n"
                f"native-explosive-canister\t{retired}\t2\t0\t1\t-\t-\t-\t-\n"
                f"native-explosive-canister\t{prefix}Prop_Wrong_C_33\t1\t0\t1\t40\t100\t200\t300\n")
        status = bridge._parse_status(frame + rows + "native-anomaly-capture\t0\t0\t0\t0\t3\t1\n")
        self.assertEqual(status["native_explosive_canisters"], {
            actual: {"phase": 1, "particles_active": False, "changed": True, "health": 40, "location": [100, 200, 300]},
            retired: {"phase": 2, "particles_active": False, "changed": True, "health": None},
        })
        self.assertEqual((status["native_canister_capture"], status["native_fuel_drum_capture"]), (3, 1))
        legacy = bridge._parse_status(frame + "native-anomaly-capture\t0\t0\t0\t0\t3\n")
        self.assertEqual((legacy["native_canister_capture"], legacy["native_fuel_drum_capture"]), (3, 0))

    def test_transition_cover_and_cue_are_independent_bridge_receipts(self):
        bridge = runtime.Bridge(Path("unused"), None)
        destination = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        frame = ("schema\t1\nseq\t20\nsession\tclient\tconnected\tObservatory\t1\t1\n"
                 "native-zone-loading-support\t1\n"
                 f"native-zone-loading-cover\t{destination}\n"
                 f"native-zone-loading-cue\t{destination}\tguest\t0\n")
        status = bridge._parse_status(frame)
        self.assertTrue(status["native_zone_loading_support"])
        self.assertEqual(status["native_zone_loading_cover"], destination)
        self.assertEqual(status["native_zone_loading_cue"], {"destination": destination, "seat": "guest", "dispatched": False})

    def test_repeated_diagnostics_yield_to_gameplay_and_link_changes_publish_immediately(self):
        with TemporaryDirectory() as temporary, Control(Path(temporary)) as control:
            with patch("tools.silverfish.control.time.monotonic", side_effect=(1, 1.01, 1.02, 1.03, 1.30)):
                self.assertTrue(control.gameplay_status_due("connected", "zone-a"))
                self.assertFalse(control.gameplay_status_due("connected", "zone-a"))
                self.assertTrue(control.gameplay_status_due("reconnecting", "zone-a"))
                self.assertFalse(control.gameplay_status_due("reconnecting", "zone-a"))
                self.assertTrue(control.gameplay_status_due("reconnecting", "zone-a"))


class WorldWeatherTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_fog_density_survives_the_snapshot_and_guest_bridge(self):
        area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        actor = service.WORLD_VIEW_ACTORS[area]
        epoch = uuid.uuid4().hex
        # SetFogDensity stores the stock 1.2 value as a float.
        weather = ("1.2000000476837", "0.7", "2", "3", "0.1")

        def frame(mode, visit, verified):
            fields = ("native-world-view", mode, area, actor, visit, "3", "9000",
                      "20", "40", "0", "0", "0", "1", "1", "5",
                      "0.3", "0.4", "0.5", "02:30", "1", "1", *weather, verified)
            return "\t".join(fields) + "\n"

        bridge = runtime.Bridge(Path("unused"), None)
        source_s = "schema\t1\nseq\t8\nsession\thost\thosting\t" + area + "\t1\t0\n"
        source = bridge._parse_status(source_s + frame("sample", "-", "0"))["native_world_view"]
        self.assertEqual(source["fog_density"], float(weather[0]))
        self.assertEqual(source["cloud_density_offset"], float(weather[-1]))

        async def reply(request, timeout):
            command_id, action, visit, sequence, destination, day, *arguments = request.decode().rstrip("\n").split("\t")
            self.assertEqual((action, visit, sequence, destination, day, timeout),
                             ("native-world-view-apply", epoch, "3", area, actor, 3))
            self.assertEqual(tuple(map(float, arguments[-5:])), tuple(map(float, weather)))
            return ("schema\t1\nseq\t9\n"
                    f"ack\t{command_id}\taccepted\tworld weather applied\n"
                    f"session\tclient\tconnected\t{area}\t0\t1\n" + frame("applied", epoch, "1"))

        bridge.local_bridge = AsyncMock()
        bridge.local_bridge.command.side_effect = reply
        client = SimpleNamespace(bridge=bridge, zone_epoch=epoch, last_game={"state": "connected", "map": area},
                                 gameplay_seen=service.time.monotonic(), gameplay={"host_world": {"native_world_view": source}})
        await service.Client.project_world_view(client)
        applied = client.last_game["native_world_view"]
        self.assertTrue(applied["weather_verified"])
        self.assertTrue(applied["save_unchanged"])
        self.assertEqual(applied["fog_density"], source["fog_density"])


    async def test_wet_rain_keeps_raw_verification_distinct_from_the_local_nullrhi_skip(self):
        area = "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P"
        actor = service.WORLD_VIEW_ACTORS[area]
        epoch = uuid.uuid4().hex

        def status(role, state, mode, marker, intensity):
            phase_epoch = epoch if mode == "applied" else "-"
            fields = ("native-world-view", mode, area, actor, phase_epoch, "3", "27000",
                      "20", "40", "0", "0", "0", "1", "1", "5",
                      "0.3", "0.4", "0.5", "07:30", "1", "1",
                      "0.2", "0.7", "2", "3", "0.1", "1", intensity, "0", "0", "1", marker)
            return (f"schema\t1\nseq\t8\nsession\t{role}\t{state}\t{area}\t1\t1\n"
                    + "\t".join(fields) + "\n")

        bridge = runtime.Bridge(Path("unused"), None)
        source = bridge._parse_status(status("host", "hosting", "sample", "nullrhi", "0.55"))["native_world_view"]
        self.assertNotIn("rain_skipped_nullrhi", source)
        self.assertEqual(source["rain_intensity"], .55)
        for marker, wet, accepted in (("1", True, True), ("nullrhi", True, True), ("0", True, False),
                                      ("nullrhi", False, False), ("unexpected", True, False), ("1", False, True)):
            with self.subTest(marker=marker, wet=wet):
                intensity = .55 if wet else 0
                reply = bridge._parse_status(status("client", "connected", "applied", marker, str(intensity)))
                proof = reply["native_world_view"]
                self.assertEqual(proof["rain_verified"], marker == "1")
                self.assertEqual(proof.get("rain_skipped_nullrhi", False), wet and marker == "nullrhi")
                client = SimpleNamespace(bridge=SimpleNamespace(command=AsyncMock(return_value=reply)), zone_epoch=epoch,
                                         last_game={"state": "connected", "map": area}, gameplay_seen=service.time.monotonic(),
                                         gameplay={"host_world": {"native_world_view": {**source, "rain_intensity": intensity}}})
                if accepted:
                    await service.Client.project_world_view(client)
                    applied = client.last_game["native_world_view"]
                    self.assertTrue(applied["weather_verified"])
                    self.assertTrue(applied["save_unchanged"])
                    self.assertEqual(applied["rain_intensity"], intensity)
                else:
                    with self.assertRaisesRegex(RuntimeError, "did not verify"):
                        await service.Client.project_world_view(client)

    async def test_resident_world_view_merges_exact_readback_without_replacing_status(self):
        area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        actor = service.WORLD_VIEW_ACTORS[area]
        epoch = uuid.uuid4().hex
        pawn = f"Player_FP_C {area}.CrossRoads_P:PersistentLevel.Player_FP_C_1"
        world_view = (f"native-world-view\tapplied\t{area}\t{actor}\t{epoch}\t17\t27000\t20\t40\t0\t0\t0\t1\t1\t5\t"
                      "0.3\t0.4\t0.5\t07:30\t1\t1\t1.2\t0.7\t2\t3\t0.1\t1\t0.55\t0\t0\t1\t1\n")

        def full(sequence, *, readback="", who=pawn, ack="older"):
            return (f"schema\t1\nseq\t{sequence}\nack\t{ack}\taccepted\tfull\n"
                    f"session\tclient\tconnected\t{area}\t0\t1\npawn\tlocal\t{who}\t10\t1\t2\t3\tPC\n"
                    f"native-world-view-apply-local\t1\n{readback}")

        async def reply(request, timeout):
            nonce, action, visit, sequence, dest, target, *_ = request.decode().rstrip("\n").split("\t")
            self.assertEqual((action, visit, sequence, dest, target, timeout),
                             ("native-world-view-apply-local", epoch, "17", area, actor, 3))
            return f"native-world-view-reply\t1\t9\t{pawn}\nack\t{nonce}\taccepted\tapplied\n{world_view}"

        bridge = runtime.Bridge(Path("unused"), None)
        bridge.local_bridge = Mock(command=AsyncMock(side_effect=reply))
        bridge.local_bridge.read_status.return_value = full(9)
        initial = bridge.snapshot()
        source = dict(runtime._parse_world_view(world_view.rstrip("\n").split("\t"), "hosting"), mode="sample")
        client = SimpleNamespace(bridge=bridge, zone_epoch=epoch, last_game=initial,
                                 gameplay_seen=service.time.monotonic(), gameplay={"host_world": {"native_world_view": source}})
        await service.Client.project_world_view(client)
        proof = client.last_game["native_world_view"]
        self.assertTrue(proof["weather_verified"] and proof["rain_verified"] and proof["save_unchanged"])
        self.assertEqual(client.last_game["ack"], initial["ack"])
        self.assertEqual(client.last_game["pawns"], initial["pawns"])
        self.assertNotIn("native_world_view", initial)
        self.assertEqual(bridge.snapshot(), client.last_game)
        await service.Client.project_world_view(client)
        bridge.local_bridge.command.assert_awaited_once()

        # A full successor or a different local pawn must supersede the transient receipt.
        bridge.local_bridge.read_status.return_value = full(10, readback=world_view)
        self.assertEqual(bridge.snapshot()["native_world_view"], proof)
        self.assertIsNone(bridge._local_world_view)
        bridge.remember_local_world_view(initial, {"native_world_view": proof, "native_world_view_local_scope": (9, pawn)})
        bridge.local_bridge.read_status.return_value = full(9, who=pawn + "_replacement")
        self.assertNotIn("native_world_view", bridge.snapshot())
        with self.assertRaisesRegex(RuntimeError, "current guest"):
            bridge.remember_local_world_view(initial, {"native_world_view": proof, "native_world_view_local_scope": (9, "other")})

        # The same opt-in command accepts the full reply when ordinary publication is due.
        async def full_reply(request, timeout):
            nonce = request.decode().split("\t", 1)[0]
            return full(11, readback=world_view, ack=nonce)

        client.last_game = initial
        bridge.local_bridge.command.side_effect = full_reply
        await service.Client.project_world_view(client)
        self.assertEqual(client.last_game["sequence"], 11)
        self.assertEqual(client.last_game["native_world_view"], proof)

        for replacement, error in (("nonce", "match the request"), ("wrong epoch", "verify the requested frame"),
                                   ("refused", "weather restored"), ("bad rain", "did not verify")):
            with self.subTest(replacement=replacement):
                async def altered(request, timeout):
                    message = await reply(request, timeout)
                    if replacement == "nonce":
                        return message.replace("ack\t", "ack\twrong-")
                    if replacement == "wrong epoch":
                        return message.replace(epoch, uuid.uuid4().hex)
                    if replacement == "refused":
                        return "\n".join(message.splitlines()[:2]).replace("accepted\tapplied", "error\tweather restored") + "\n"
                    return message.removesuffix("1\n") + "0\n"

                client.last_game = initial
                bridge.local_bridge.command.side_effect = altered
                with self.assertRaisesRegex(RuntimeError, error):
                    await service.Client.project_world_view(client)
                self.assertIsNone(bridge._local_world_view)


class SharedStockStageTests(unittest.TestCase):
    def test_stage_links_verified_stock_data_from_a_private_cache(self):
        with TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary).resolve()
            source, loader, cache = (root / name for name in ("source", "loader", "cache"))
            radio = Path("SilverFish/Content/Music/Radio/sample.wav")
            metadata_paths = (runtime.SHIPPING, Path("SilverFish.exe"), radio)
            for relative in (*metadata_paths, *(runtime.PAKS / name for name in runtime.STOCK_PACKAGES)):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.as_posix().encode())
            (source / runtime.MANIFEST).write_text("\n".join(path.as_posix() for path in metadata_paths), encoding="utf-8")
            for relative in ("dwmapi.dll", "ue4ss/UE4SS.dll", "ue4ss/UE4SS-settings.ini"):
                path = loader / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("ConsoleEnabled = 1\n", encoding="utf-8")
            for relative, stock in ((Path(runtime.STOCK_ARCHIVE.name), runtime.STOCK_ARCHIVE), (radio, radio)):
                path = cache / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((source / stock).read_bytes())

            work = root / "linked"
            runtime.prepare_game(work, source, loader, None, stock_cache=cache)
            stage = work / "stage"
            self.assertTrue((stage / runtime.STOCK_ARCHIVE).samefile(cache / runtime.STOCK_ARCHIVE.name))
            self.assertTrue((stage / radio).samefile(cache / radio))
            self.assertFalse((stage / runtime.STOCK_ARCHIVE).samefile(source / runtime.STOCK_ARCHIVE))
            self.assertFalse((stage / runtime.SHIPPING).samefile(source / runtime.SHIPPING))
            self.assertIn("ConsoleEnabled = 0", (stage / runtime.SHIPPING.parent /
                                                 "ue4ss/UE4SS-settings.ini").read_text(encoding="utf-8"))

            bad_cache = root / "mismatched"
            bad_cache.mkdir()
            (bad_cache / runtime.STOCK_ARCHIVE.name).write_bytes(b"not the installed archive")
            with self.assertRaisesRegex(ValueError, "differs from the selected game build"):
                runtime.prepare_game(root / "rejected", source, loader, None, stock_cache=bad_cache)
            self.assertFalse((root / "rejected/stage").exists())


class StageBackendTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.stage = self.work / "stage"
        game = self.stage / runtime.SHIPPING
        game.parent.mkdir(parents=True)
        game.write_bytes(b"game fixture")
        self.binaries = game.parent
        self.scripts = self.binaries / runtime.MOD_SCRIPTS
        self.scripts.mkdir(parents=True)
        (self.scripts / "main.lua").write_bytes(b"return nil\n")
        self.manifest = self.stage / "silverfish-coop-stage.json"
        self.metadata = {"schema": 1, "game_build": "a" * 64}
        runtime.write_json(self.manifest, self.metadata)

    def install_cpp_fixture(self):
        hashes = {}
        for key, relative in (("runtime", runtime.CPP_RUNTIME_DLL), ("addon", runtime.CPP_ADDON_DLL)):
            path = self.binaries / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = key.encode("ascii")
            path.write_bytes(payload)
            hashes[key] = hashlib.sha256(payload).hexdigest()
        self.metadata["cpp_death_lab"] = hashes
        runtime.write_json(self.manifest, self.metadata)

    def test_stock_and_cooked_stage_have_distinct_fingerprints(self):
        stock = runtime.staged_build(self.work)
        self.assertRegex(stock, r"^[0-9a-f]{64}$")
        self.assertNotEqual(stock, self.metadata["game_build"])
        self.assertIsNone(runtime.native_death_backend(self.work))
        with patch.object(runtime, "has_native_death_patch", return_value=True):
            self.assertEqual(runtime.native_death_backend(self.work), "overlay")
            self.assertNotEqual(runtime.staged_build(self.work), stock)

    def test_lua_change_makes_old_invitation_incompatible(self):
        script = self.scripts / "commands.lua"
        script.write_bytes(b"return {}\n")
        build = runtime.staged_build(self.work)
        invite = self.work / "invite.json"
        runtime.write_json(invite, {"schema": 1, "game_build": build, "protocol_version": service.PROTOCOL,
                                   "host": "127.0.0.1", "port": 7777, "guest_id": "guest", "character_id": "scout",
                                   "certificate": "fixture", "credential": "fixture"})
        script.write_bytes(b"return {}\r\n")
        self.assertEqual(service.load_invite(invite, runtime.staged_build(self.work))["game_build"], build)

        script.write_bytes(b"return {'another command'}\n")
        with self.assertRaisesRegex(ValueError, "game/mod build are incompatible"):
            service.load_invite(invite, runtime.staged_build(self.work))

        script.unlink()
        self.assertNotEqual(runtime.staged_build(self.work), build)

    def test_portable_manifest_overrides_an_older_root_manifest(self):
        expected = runtime.staged_build(self.work)
        portable = self.stage / runtime.PORTABLE_STAGE_MANIFEST
        runtime.write_json(portable, self.metadata)
        runtime.write_json(self.manifest, {"schema": 1, "game_build": "b" * 64})
        self.assertEqual(runtime.stage_manifest(self.stage), portable)
        self.assertEqual(runtime.staged_build(self.work), expected)
        portable.unlink()
        self.assertEqual(runtime.stage_manifest(self.stage), self.manifest)
        self.assertNotEqual(runtime.staged_build(self.work), expected)

    def test_portable_update_fingerprints_only_its_current_lua_files(self):
        expected = runtime.staged_build(self.work)
        main = self.scripts / "main.lua"
        self.metadata["lua_files"] = {"main.lua": hashlib.sha256(main.read_bytes()).hexdigest()}
        runtime.write_json(self.manifest, self.metadata)
        (self.scripts / "old_release.lua").write_bytes(b"return {'no longer shipped'}\n")
        with runtime.installed_content(self.work, self.stage):
            runtime.verify_installed_lua(self.work)
            self.assertTrue(runtime.staged_script(self.work, "main.lua"))
            self.assertFalse(runtime.staged_script(self.work, "old_release.lua"))
            self.assertEqual(runtime.staged_build(self.work), expected)

            main.write_bytes(b"return nil\r\n")
            self.assertEqual(runtime.staged_build(self.work), expected)
            main.write_bytes(b"return {}\n")
            with self.assertRaisesRegex(ValueError, "differs from the release"):
                runtime.verify_installed_lua(self.work)
            main.unlink()
            with self.assertRaisesRegex(ValueError, "Missing installed co-op script"):
                runtime.verify_installed_lua(self.work)

    def test_portable_lua_inventory_refuses_redirected_file_names(self):
        self.metadata["lua_files"] = {"main.lua": "a" * 64, "../outside.lua": "a" * 64}
        runtime.write_json(self.manifest, self.metadata)
        with self.assertRaisesRegex(ValueError, "file list is invalid"):
            runtime.verify_installed_lua(self.work)

    def test_launcher_code_changes_the_peer_fingerprint(self):
        launcher = self.work / "tools/silverfish/service.py"
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(b"PROTOCOL = 1\n")
        with patch.object(runtime, "REPO", self.work):
            baseline = runtime.staged_build(self.work)
            launcher.write_bytes(b"PROTOCOL = 2\n")
            self.assertNotEqual(runtime.staged_build(self.work), baseline)

    def test_cpp_stage_is_distinct_and_rejects_changed_binaries(self):
        stock = runtime.staged_build(self.work)
        self.install_cpp_fixture()
        self.assertEqual(runtime.native_death_backend(self.work), "cpp")
        self.assertNotEqual(runtime.staged_build(self.work), stock)
        (self.binaries / runtime.CPP_ADDON_DLL).write_bytes(b"changed add-on")
        with self.assertRaisesRegex(ValueError, "add-on|addon"):
            runtime.staged_build(self.work)

    def test_cpp_stage_rejects_mixed_overlay_or_missing_manifest(self):
        self.install_cpp_fixture()
        with patch.object(runtime, "has_native_death_patch", return_value=True):
            with self.assertRaisesRegex(ValueError, "separate stage"):
                runtime.native_death_backend(self.work)
        self.metadata.pop("cpp_death_lab")
        runtime.write_json(self.manifest, self.metadata)
        with self.assertRaisesRegex(ValueError, "missing its stage manifest"):
            runtime.native_death_backend(self.work)

    @unittest.skipUnless(os.name == "nt", "Uses the Windows game launcher")
    def test_only_headless_launches_use_unreals_offscreen_application(self):
        paths = self.work, self.work / "profile", self.work / "bridge"
        with patch.dict(os.environ), patch("tools.silverfish.win_job.GameProcess") as game:
            os.environ.pop("SILVERFISH_COOP_RENDER_CAPTURE", None)
            for port in (17432, None):
                runtime.launch(*paths, headless=True, port=port)
                command = game.call_args.args[0]
                self.assertIn("-RenderOffScreen", command)
                self.assertIn("-nullrhi", command)

            runtime.launch(*paths, headless=False)
            self.assertNotIn("-RenderOffScreen", game.call_args.args[0])

    @unittest.skipUnless(os.name == "nt", "Uses the Windows game launcher")
    def test_headless_capture_enables_gpu_only_for_the_selected_peer(self):
        paths = self.work, self.work / "profile", self.work / "bridge"
        flag = "SILVERFISH_COOP_RENDER_CAPTURE"
        fixture = "SILVERFISH_COOP_FIXTURE_CAPTURE"
        with patch.dict(os.environ, {fixture: "inherited"}), patch("tools.silverfish.win_job.GameProcess") as game:
            for capture in ("host", "guest", "unknown"):
                os.environ[flag] = capture
                for role, port in (("host", 17432), ("guest", None)):
                    with self.subTest(capture=capture, role=role):
                        runtime.launch(*paths, headless=True, port=port)
                        command = game.call_args.args[0]
                        self.assertEqual("-nullrhi" in command, capture != role)
                        self.assertIn("-RenderOffScreen", command)
                        self.assertIn("DefaultViewportMouseCaptureMode=NoCapture", command)
                        self.assertIn("DefaultViewportMouseLockMode=DoNotLock", command)
                        self.assertNotIn(flag, game.call_args.kwargs["env"])
                        self.assertEqual(game.call_args.kwargs["env"].get(fixture), "1" if capture == role else None)
                        startup = game.call_args.kwargs["startupinfo"]
                        self.assertTrue(startup.dwFlags & runtime.subprocess.STARTF_USESHOWWINDOW)
                        self.assertEqual(startup.wShowWindow, runtime.subprocess.SW_HIDE)

    @unittest.skipUnless(os.name == "nt", "Uses the Windows game launcher")
    def test_title_and_foreground_do_not_use_the_guest_capture_setting(self):
        paths = self.work, self.work / "profile", self.work / "bridge"
        fixture = "SILVERFISH_COOP_FIXTURE_CAPTURE"
        with (patch.dict(os.environ, {"SILVERFISH_COOP_RENDER_CAPTURE": "guest", fixture: "1"}),
              patch("tools.silverfish.win_job.GameProcess") as game):
            runtime.launch(*paths, headless=True, title_fixture=True)
            self.assertIn("-nullrhi", game.call_args.args[0])
            self.assertNotIn(fixture, game.call_args.kwargs["env"])
            runtime.launch(*paths, headless=False)
            self.assertNotIn("-RenderOffScreen", game.call_args.args[0])
            self.assertNotIn(fixture, game.call_args.kwargs["env"])

    @unittest.skipUnless(os.name == "nt", "Uses the Windows game launcher")
    def test_reload_fixture_requires_headless_client_with_reload_input(self):
        self.install_cpp_fixture()
        fixture_flag = "SILVERFISH_COOP_NATIVE_GUEST_RELOAD_FIXTURES"
        input_flag = "SILVERFISH_COOP_NATIVE_GUEST_RELOAD_INPUT"
        paths = self.work, self.work / "profile", self.work / "bridge"
        with patch.dict(os.environ), patch("tools.silverfish.win_job.GameProcess") as game:
            os.environ.pop(fixture_flag, None)
            runtime.launch(*paths, headless=True, native_zones=True, guest_reload_input=True)
            self.assertEqual(game.call_args.kwargs["env"][fixture_flag], "1")
            self.assertEqual(game.call_args.kwargs["env"][input_flag], "1")

            os.environ[fixture_flag] = "1"
            runtime.launch(*paths, headless=False, native_zones=True, guest_reload_input=True)
            self.assertNotIn(fixture_flag, game.call_args.kwargs["env"])
            self.assertEqual(game.call_args.kwargs["env"][input_flag], "1")

            runtime.launch(*paths, headless=True, native_zones=True)
            self.assertNotIn(fixture_flag, game.call_args.kwargs["env"])

    @unittest.skipUnless(os.name == "nt", "Uses the Windows game launcher")
    def test_cqc_enemy_loot_is_explicit_and_independent_from_fixtures(self):
        self.install_cpp_fixture()
        flag = "SILVERFISH_COOP_NATIVE_CQC_LOOT"
        paths = self.work, self.work / "profile", self.work / "bridge"
        host = dict(headless=False, port=17432, native_events=True, native_guest=True, native_zones=True, native_loot_input=True)
        with patch.dict(os.environ, {flag: "1"}), patch("tools.silverfish.win_job.GameProcess") as game:
            runtime.launch(*paths, **host)
            self.assertNotIn(flag, game.call_args.kwargs["env"])
            runtime.launch(*paths, native_dead_bridge_enemy_loot=True, **host)
            self.assertEqual(game.call_args.kwargs["env"][flag], "1")
            self.assertNotIn("SILVERFISH_COOP_NATIVE_FIXTURES", game.call_args.kwargs["env"])
            with self.assertRaisesRegex(ValueError, "native campaign host"):
                runtime.launch(*paths, headless=True, native_zones=True, native_dead_bridge_enemy_loot=True)
