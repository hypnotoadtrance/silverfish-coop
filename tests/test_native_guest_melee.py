import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_authored_pickup as pickup, native_guest_melee as melee, party
from tools.silverfish.runtime import Bridge
from tools.silverfish.service import Client
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, OBSERVATORY, REFINERY


GUID = "AB" * 16
OPERATION = "a" * 32


class NativeGuestMeleeTests(unittest.IsolatedAsyncioTestCase):
    def host(self):
        state = party.new_party()
        party.add_player(state, "guest")
        state[pickup.STATE_KEY] = {pickup.OLD_PIPE: {"owner": "guest", "guid": GUID}}
        return SimpleNamespace(native_guest=True, fixtures=True, native_profile=True, native_guest_controls=True,
                               stopping=False, transitioning=False, smg_holds=None,
                               native_checkpoint_unsafe=False, party=state, last_seen=time.monotonic(),
                               last_game={"state": "hosting", "map": CROSSROADS}, checkpoint_native=AsyncMock(),
                               native_recipients=lambda: {"guest": {"connected": True, "pawn": "exact pawn"}})

    def swing(self, status, **changes):
        settled = status == "surface_verified"
        return {"state": "hosting", "map": CROSSROADS, melee.CANARY_KEY: dict(
            {"map": CROSSROADS, "pawn": "exact pawn", "guid": GUID, "serial": 3, "status": status,
             "presses": 1, "releases": 1, "contacts": int(settled), "qualified": int(settled),
             "settled": settled, "passive_free": True}, **changes)}

    async def test_pending_swing_holds_checkpoint_and_replays_exact_receipt(self):
        host = self.host()
        complete = self.swing("surface_verified")

        async def observe(terminal, timeout):
            self.assertTrue(host.native_checkpoint_unsafe)
            self.assertEqual(host.checkpoint_native.await_count, 1)
            self.assertFalse(terminal(self.swing("surface_verified", serial=4)))
            self.assertFalse(terminal(self.swing("surface_verified", pawn="another pawn")))
            self.assertTrue(terminal(complete))
            return complete

        bridge = SimpleNamespace(command=AsyncMock(return_value=self.swing("pending")), wait_for=AsyncMock(side_effect=observe))
        args = host, bridge, melee.CANARY, "guest", GUID, OPERATION
        self.assertEqual(await melee.perform(*args), {melee.CANARY_KEY: complete[melee.CANARY_KEY]})
        self.assertEqual(await melee.perform(*args), {melee.CANARY_KEY: complete[melee.CANARY_KEY]})
        bridge.command.assert_awaited_once_with(melee.CANARY + "-start", "exact pawn", GUID, timeout=5)
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({"guest"})})
        self.assertFalse(host.native_checkpoint_unsafe)

    async def test_uncertain_contact_never_commits_and_clean_refusal_releases_fence(self):
        for error, dirty in ((RuntimeError("native_guest_melee_stock_swing_unqualified"), False), (None, True)):
            with self.subTest(dirty=dirty):
                host = self.host()
                frame = self.swing("mutation_unverified_guest_melee_contact_path", contacts=1)
                bridge = SimpleNamespace(command=AsyncMock(return_value=frame, side_effect=error))
                with self.assertRaises(RuntimeError if dirty else party.RuleError):
                    await melee.perform(host, bridge, melee.CANARY, "guest", GUID, OPERATION)
                self.assertEqual(host.checkpoint_native.await_count, 1)
                self.assertEqual(host.stopping, dirty)
                self.assertEqual(host.native_checkpoint_unsafe, dirty)
                self.assertEqual(host.party["receipts"], {})

    async def test_equipment_needs_exact_placed_claim_before_native_access(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock())
        host.party[pickup.STATE_KEY][pickup.OLD_PIPE]["owner"] = "other guest"
        with self.assertRaisesRegex(party.RuleError, "exact_authored_claim"):
            await melee.perform(host, bridge, melee.EQUIP, "guest", GUID, OPERATION)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()

    def test_stock_animation_tsv_and_pocket_watch_fallback(self):
        fields = [melee.CANARY, CROSSROADS, "exact pawn", GUID, "3", "surface_verified", "1", "1", "1", "1", "1", "1"]
        self.assertEqual(melee.parse(fields), (melee.CANARY_KEY, self.swing("surface_verified")[melee.CANARY_KEY]))
        self.assertEqual(pickup.select_source(CROSSROADS, None), pickup.POCKET_WATCH)
        self.assertEqual(pickup.select_source(CROSSROADS, pickup.OLD_PIPE), pickup.OLD_PIPE)

    def damage_fields(self):
        return [melee.CANARY, DEAD_BRIDGE, "exact pawn", GUID, "4", "stock_damage_verified", "1", "1", "1", "1", "1", "1",
                melee.TARGET, "1", "1", "160", "137.5", "22.5"]

    def test_damage_receipt_requires_exact_live_target_and_stock_health_delta(self):
        fields = self.damage_fields()
        key, result = melee.parse(fields)
        self.assertEqual(key, melee.CANARY_KEY)
        self.assertTrue(melee.verified_damage(result))
        for index, wrong in ((1, CROSSROADS), (12, "other actor"), (13, "2"), (14, "0"), (15, "nan"),
                             (16, "0"), (17, "25")):
            self.assertIsNone(melee.parse([*fields[:index], wrong, *fields[index+1:]]))
        self.assertIsNone(melee.parse(fields[:12]))

    def test_staged_contact_trace_records_movement_and_rejects_unregistered_target(self):
        target = melee.BY_MAP[DEAD_BRIDGE].actors[1]
        pawn = f"Player_FP_C {DEAD_BRIDGE}.DeadBridge_P:PersistentLevel.Player_FP_C_42"
        fields = [melee.CONTACT_FIXTURE, DEAD_BRIDGE, pawn, GUID, "12", target, "sphere_empty", "-",
                  "160", "145", "-1", "2.5", "0.3", "161"]
        key, proof = melee.parse(fields)
        self.assertEqual(key, melee.CONTACT_KEY)
        self.assertEqual((proof["target"], proof["serial"], proof["stage"]), (target, 12, "sphere_empty"))
        self.assertEqual((proof["initial_health"], proof["contact_health"], proof["selected_health"]), (160, 145, None))
        self.assertEqual((proof["camera_displacement"], proof["camera_direction_dot"], proof["target_displacement"]),
                         (2.5, 0.3, 161))
        self.assertIsNone(proof["camera_scope"])
        self.assertEqual(melee.parse([*fields, "restored"])[1]["camera_scope"], "restored")
        self.assertIsNone(melee.parse([*fields, "unknown_camera_mutation"]))
        for index, wrong in ((1, REFINERY), (2, "other pawn"), (4, "0"), (5, "foreign target"), (6, "fake_stage"),
                             (8, "99"), (9, "x"), (10, "nan"), (12, "1.5"), (13, "-3")):
            self.assertIsNone(melee.parse([*fields[:index], wrong, *fields[index+1:]]))

    async def test_damage_action_is_fixed_to_authentic_crossroads_claim_in_dead_bridge(self):
        host = self.host()
        key, result = melee.parse(self.damage_fields())
        bridge = SimpleNamespace(command=AsyncMock(return_value={"state": "hosting", "map": DEAD_BRIDGE, key: result}))
        args = host, bridge, melee.DAMAGE, "guest", GUID, OPERATION
        with self.assertRaisesRegex(party.RuleError, "fixture_unavailable"):
            await melee.perform(*args)

        host.last_game["map"] = DEAD_BRIDGE
        with self.assertRaisesRegex(party.RuleError, "exact_authored_claim"):
            await melee.perform(*args)
        bridge.command.assert_not_awaited()
        claimed = host.party[pickup.STATE_KEY][pickup.OLD_PIPE]
        area, actor, class_path = pickup.SOURCES[pickup.OLD_PIPE]
        claimed.update(source=pickup.OLD_PIPE, map=area, actor=actor, class_path=class_path)
        self.assertEqual(await melee.perform(*args), {key: result})
        self.assertEqual(await melee.perform(*args), {key: result})
        bridge.command.assert_awaited_once_with(melee.DAMAGE + "-start", "exact pawn", GUID, timeout=5)
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({"guest"})})

    def ordinary_fixture(self, miss):
        fields = self.damage_fields()
        fields[0] = melee.SWING
        if miss:
            fields[5] = "stock_miss_verified"
            fields[12] = "-"
            fields[13:] = ["0", "0", "-1", "-1", "-1"]
        key, report = melee.parse(fields)
        snapshot = {"state": "hosting", "map": DEAD_BRIDGE, key: report,
                    "native_guest_save_retention": True,
                    "native_guest_equipped_weapons": {"exact pawn": {"kind": "melee", "guid": GUID}}}
        host = self.host()
        host.fixtures = False
        host.party.pop(pickup.STATE_KEY)
        host.last_game = snapshot
        host.pending_guest_saves = {"guest": ("exact pawn",)}
        return host, snapshot

    async def test_ordinary_exact_equipment_hit_and_miss_replay_without_fixture(self):
        for miss in (False, True):
            with self.subTest(miss=miss):
                host, snapshot = self.ordinary_fixture(miss)
                bridge = SimpleNamespace(command=AsyncMock(return_value=snapshot))
                args = host, bridge, melee.SWING, "guest", GUID, OPERATION
                self.assertEqual(await melee.perform(*args), {melee.SWING_KEY: snapshot[melee.SWING_KEY]})
                self.assertEqual(await melee.perform(*args), {melee.SWING_KEY: snapshot[melee.SWING_KEY]})
                bridge.command.assert_awaited_once_with(melee.SWING + "-start", "exact pawn", GUID, timeout=5)
                host.checkpoint_native.assert_not_awaited()
                self.assertFalse(host.native_checkpoint_unsafe)

    async def test_ordinary_refuses_other_equipped_guid_and_open_smg_scope(self):
        host, snapshot = self.ordinary_fixture(False)
        bridge = SimpleNamespace(command=AsyncMock())
        args = host, bridge, melee.SWING, "guest", GUID, OPERATION
        snapshot["native_guest_equipped_weapons"]["exact pawn"]["guid"] = "F" * 32
        with self.assertRaisesRegex(party.RuleError, "exact_equipment_required"):
            await melee.perform(*args)
        snapshot["native_guest_equipped_weapons"]["exact pawn"]["guid"] = GUID
        host.smg_holds = SimpleNamespace(strokes={"someone else": True})
        with self.assertRaisesRegex(party.RuleError, "combat_busy"):
            await melee.perform(*args)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()

    def test_only_ordinary_actual_contact_accepts_each_registered_actor(self):
        for area, profile in melee.BY_MAP.items():
            for target in profile.actors:
                with self.subTest(target=target):
                    fields = self.damage_fields()
                    fields[0], fields[1], fields[12] = melee.SWING, area, target
                    key, row = melee.parse(fields)
                    self.assertEqual((key, row["target"]), (melee.SWING_KEY, target))
                    self.assertTrue(melee.verified_damage(row))
                    fields[1] = REFINERY if area == DEAD_BRIDGE else DEAD_BRIDGE
                    self.assertIsNone(melee.parse(fields))
        fields = self.damage_fields()
        fields[12] = melee.BY_MAP[DEAD_BRIDGE].actors[1]
        self.assertIsNone(melee.parse(fields))

    def test_ordinary_swing_accepts_lethal_damage_to_an_unregistered_campaign_actor(self):
        fields = self.damage_fields()
        fields[0], fields[1] = melee.SWING, OBSERVATORY
        level = OBSERVATORY.rsplit("/", 1)[1]
        fields[12] = f"NPC_State_Conscript_C {OBSERVATORY}.{level}:PersistentLevel.NPC_State_Conscript_C_184"
        fields[15:18] = ["8", "0", "22.5"]
        key, row = melee.parse(fields)
        self.assertEqual((key, row["target"]), (melee.SWING_KEY, fields[12]))
        self.assertTrue(melee.verified_damage(row))
        fields[17] = "7"
        self.assertIsNone(melee.parse(fields))

    async def test_headless_aim_can_name_second_registered_actor_but_swing_cannot(self):
        host, snapshot = self.ordinary_fixture(False)
        host.fixtures = True
        target = melee.BY_MAP[DEAD_BRIDGE].actors[1]
        aim = {"map": DEAD_BRIDGE, "pawn": "exact pawn", "guid": GUID, "target": target}
        bridge = SimpleNamespace(command=AsyncMock(return_value={**snapshot, melee.AIM_KEY: aim}))
        requested = host, bridge, melee.AIM_FIXTURE, "guest", GUID, OPERATION
        self.assertEqual(await melee.perform(*requested, fixture_lane=2), {melee.AIM_KEY: aim})
        bridge.command.assert_awaited_once_with(melee.AIM_FIXTURE, "exact pawn", GUID, target, timeout=5)
        with self.assertRaisesRegex(party.RuleError, "registry_target_required"):
            await melee.perform(*requested, fixture_lane=3)
        with self.assertRaisesRegex(party.RuleError, "registry_target_required"):
            await melee.perform(host, bridge, melee.SWING, "guest", GUID, OPERATION, fixture_lane=2)
        host.checkpoint_native.assert_not_awaited()

    def test_plain_guid_and_previous_travel_epoch_cannot_be_dispatched(self):
        epoch, previous = "e" * 32, "d" * 32
        wire = melee.request_target(GUID, epoch)
        self.assertEqual(melee.current_guid(wire, epoch), GUID)
        for rejected in (GUID, melee.request_target(GUID, previous), None):
            with self.assertRaisesRegex(party.RuleError, "exact_epoch_and_guid_required"):
                melee.current_guid(rejected, epoch)

    async def test_client_primary_tap_routes_once_to_melee_action_with_scoped_guid(self):
        scope = (DEAD_BRIDGE, "local pawn", GUID, "melee")
        pending = [(4, *scope), (4, *scope)]
        client = SimpleNamespace(guest_primary_input=True, viewer_role="guest", stopping=False, host_session="session",
                                 zone_epoch="epoch", gameplay_seen=time.monotonic(), invite={"character_id": "member"},
                                 gameplay={"players": {"guest": {"character_id": "member", "downed": False}},
                                           "native_equipped_weapon": {"map": DEAD_BRIDGE, "pawn": "host pawn", "guid": GUID,
                                                                      "kind": scope[-1]}},
                                 last_game={"map": DEAD_BRIDGE, "pawns": {"local": {"name": scope[1]}}},
                                 next_primary_renewal=time.monotonic() + 30, primary_scope=scope, last_primary_intent=0,
                                 melee_input=None,
                                 bridge=Mock(input_namespace=uuid.uuid4(), native_primary_input_requests=Mock(return_value=pending)),
                                 control=Mock(), gameplay_action=AsyncMock(return_value={}))
        await Client.service_primary_input(client, True)
        operation_id = uuid.uuid5(client.bridge.input_namespace, "session:epoch:local pawn:primary:4").hex
        client.gameplay_action.assert_awaited_once_with(melee.SWING, operation_id, GUID)
        client.control.event.assert_called_once_with("native_guest_melee_input", operation_id=operation_id, sequence=4)

    def test_stock_edges_are_consumed_once_from_the_resident_memory_queue(self):
        pawn = f"Player_FP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Player_FP_C_42"
        valid_rows = [
            ("12", "press", "12", "0"),
            ("13", "release", "12", "80"),
            ("18", "press", "18", "0"),
            ("19", "cancel", "18", "340"),
        ]
        rows = valid_rows + [("20", "release", "20", "0"), ("21", "press", "20", "0"), ("9", "release", "14", "0"),
                             ("21", "press", "21", "10"), ("25", "release", "22", "9001")]
        records = ["\t".join((melee.INPUT_RECORD, sequence, CROSSROADS, pawn, GUID, "old-pipe", edge, stroke, ms)).encode()
                   for sequence, edge, stroke, ms in rows]
        bridge = Bridge(None, Mock())
        bridge.local_bridge = Mock(input_ready=Mock(side_effect=[True, False]), read_input=Mock(return_value=records))
        self.assertEqual(bridge.native_melee_input_requests(),
                         [(int(sequence), CROSSROADS, pawn, GUID, "old-pipe", edge, int(stroke), int(ms))
                          for sequence, edge, stroke, ms in valid_rows])
        self.assertEqual(bridge.native_melee_input_requests(), [])

    async def test_only_adjacent_short_edges_in_the_same_drain_become_a_stock_tap(self):
        scope = CROSSROADS, "local pawn", GUID, "old-pipe"
        batches = [
            [(1, *scope, "press", 1, 0), (2, *scope, "release", 1, 48)],
            [(3, *scope, "press", 3, 0), (4, *scope, "release", 3, 610)],
            [(5, *scope, "press", 5, 0)],
            [(6, *scope, "release", 5, 49)],
            [(7, *scope, "press", 7, 0), (8, *scope, "cancel", 7, 2)],
        ]
        bridge = Mock(input_namespace=uuid.uuid4(), native_melee_input_requests=Mock(side_effect=batches))
        client = SimpleNamespace(host_session="session", zone_epoch="epoch", bridge=bridge,
                                 gameplay_action=AsyncMock(return_value={}), control=Mock())
        controller = melee.GuestInput(client)
        for _ in batches:
            await controller.service(scope)
        edges = [call.args[2].rsplit("@", 1)[1] for call in client.gameplay_action.await_args_list]
        self.assertEqual(edges, ["tap", "press", "release", "press", "release", "press", "cancel"])
        events = [(call.kwargs["sequence"], call.kwargs["edge"]) for call in client.control.event.call_args_list]
        self.assertEqual(events, list(zip((2, 3, 4, 5, 6, 7, 8), edges)))

    async def test_atomic_native_taps_use_both_edges_and_do_not_reuse_one_stroke(self):
        host = self.host()
        host.zone_epoch, host.control = "epoch", Mock()
        host.last_game["native_guest_equipped_weapons"] = {"exact pawn": {"kind": "old-pipe", "guid": GUID}}
        handler = melee.AuthorityInput(host)

        def snapshot(edges):
            return dict(host.last_game, **{melee.INPUT_KEY: dict(
                map=CROSSROADS, pawn="exact pawn", guid=GUID, serial=3, status="pending", presses=edges,
                releases=edges, contacts=0, hits=0, charged=0, combo=0)})

        bridge = SimpleNamespace(command=AsyncMock(side_effect=[snapshot(1), snapshot(2)]))
        first = f"epoch@{GUID}@{'1' * 32}"
        second = f"epoch@{GUID}@{'2' * 32}"
        with patch.object(melee.native_inventory, "retain_guest_save", new=AsyncMock(return_value=True)):
            receipt = await handler.request(bridge, "guest", first + "@tap", OPERATION)
            self.assertEqual(receipt, await handler.request(bridge, "guest", first + "@tap", OPERATION))
            with self.assertRaisesRegex(party.RuleError, "combat_busy"):
                await handler.request(bridge, "guest", first + "@press", OPERATION)
            with self.assertRaisesRegex(party.RuleError, "exact_edge_required"):
                await handler.request(bridge, "guest", first + "@release", OPERATION)
            combo = await handler.request(bridge, "guest", second + "@tap", OPERATION)
        self.assertEqual(combo[melee.INPUT_KEY]["releases"], 2)
        self.assertIsNone(handler.scope.held)
        self.assertEqual([call.args for call in bridge.command.await_args_list], [
            (melee.INPUT + "-start", "exact pawn", GUID, "tap"),
            (melee.INPUT + "-edge", "3", "tap"),
        ])
        host.checkpoint_native.assert_not_awaited()

    async def test_exact_stroke_remains_bound_across_stock_combo_edges_without_checkpoint(self):
        host = self.host()
        area, pawn = CROSSROADS, "exact pawn"
        host.zone_epoch, host.control = "epoch", Mock()
        equipped = {pawn: {"kind": "old-pipe", "guid": GUID}}
        host.last_game["native_guest_equipped_weapons"] = equipped
        handler = melee.AuthorityInput(host)

        def frame(presses, releases, contacts=0, hits=0, charged=0, combo=0, status="pending"):
            return dict(map=area, pawn=pawn, guid=GUID, serial=3, status=status, presses=presses, releases=releases,
                        contacts=contacts, hits=hits, charged=charged, combo=combo)

        resolved = [frame(1, 0), frame(1, 1), frame(2, 1, 1, 1), frame(2, 2, 1, 1)]
        game = dict(host.last_game)
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[dict(game, **{melee.INPUT_KEY: item}) for item in resolved]))

        async def edge(token, action, *, character="guest", epoch="epoch"):
            return await handler.request(bridge, character, f"{epoch}@{GUID}@{token}@{action}", OPERATION)

        old, new, stranger = "b" * 32, "c" * 32, "d" * 32
        with patch.object(melee.native_inventory, "retain_guest_save", new=AsyncMock(return_value=True)) as retain:
            first = await edge(old, "press")
            self.assertEqual(await edge(old, "press"), first)
            with self.assertRaisesRegex(party.RuleError, "exact_edge_required"):
                await edge(stranger, "release")
            with self.assertRaisesRegex(party.RuleError, "exact_edge_required"):
                await edge(old, "release", epoch="superseded")
            with self.assertRaisesRegex(party.RuleError, "combat_busy"):
                await edge(old, "release", character="stranger")
            await edge(old, "release")
            await edge(new, "press")
            await edge(new, "release")
        self.assertEqual([call.args for call in bridge.command.await_args_list], [
            (melee.INPUT + "-start", pawn, GUID), (melee.INPUT + "-edge", "3", "release"),
            (melee.INPUT + "-edge", "3", "press"), (melee.INPUT + "-edge", "3", "release"),
        ])
        retain.assert_awaited_once_with(host, bridge, "guest", pawn)
        settled = frame(2, 2, 2, 2, combo=1, status="settled")
        host.last_game[melee.INPUT_KEY] = settled
        handler.observe()
        self.assertIsNone(handler.scope)
        host.control.event.assert_called_once_with("native_guest_melee_input_settled", character_id="guest", **settled)
        host.checkpoint_native.assert_not_awaited()

    def test_stock_edge_state_rejects_impossible_combo_or_charge_evidence(self):
        pawn = f"Player_FP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Player_FP_C_42"
        fields = [melee.INPUT_STATE, CROSSROADS, pawn, GUID, "3", "settled", "2", "2", "2", "1", "1", "1"]
        self.assertEqual(melee.parse_input_state(fields)["combo"], 1)
        for index, invalid in ((7, "1"), (8, "3"), (9, "3"), (10, "2"), (11, "-1")):
            with self.subTest(index=index):
                bad = list(fields)
                bad[index] = invalid
                self.assertIsNone(melee.parse_input_state(bad))
