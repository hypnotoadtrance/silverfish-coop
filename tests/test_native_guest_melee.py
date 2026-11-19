import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_authored_pickup as pickup, native_guest_melee as melee, party
from tools.silverfish.service import Client
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE


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
            fields[13:] = ["0", "0", "-1", "-1", "-1"]
        key, report = melee.parse(fields)
        snapshot = {"state": "hosting", "map": DEAD_BRIDGE, key: report,
                    "native_guest_equipped_weapons": {"exact pawn": {"kind": melee.KIND, "guid": GUID}}}
        host = self.host()
        host.fixtures = False
        host.party.pop(pickup.STATE_KEY)
        host.last_game = snapshot
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
                self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({"guest"})})
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

    def test_plain_guid_and_previous_travel_epoch_cannot_be_dispatched(self):
        epoch, previous = "e" * 32, "d" * 32
        wire = melee.request_target(GUID, epoch)
        self.assertEqual(melee.current_guid(wire, epoch), GUID)
        for rejected in (GUID, melee.request_target(GUID, previous), None):
            with self.assertRaisesRegex(party.RuleError, "exact_epoch_and_guid_required"):
                melee.current_guid(rejected, epoch)

    async def test_client_primary_tap_routes_once_to_melee_action_with_scoped_guid(self):
        scope = (DEAD_BRIDGE, "local pawn", GUID, melee.KIND)
        pending = [(4, *scope), (4, *scope)]
        client = SimpleNamespace(guest_primary_input=True, viewer_role="guest", stopping=False, host_session="session",
                                 zone_epoch="epoch", gameplay_seen=time.monotonic(), invite={"character_id": "member"},
                                 gameplay={"players": {"guest": {"character_id": "member", "downed": False}},
                                           "native_equipped_weapon": {"map": DEAD_BRIDGE, "pawn": "host pawn", "guid": GUID,
                                                                      "kind": melee.KIND}},
                                 last_game={"map": DEAD_BRIDGE, "pawns": {"local": {"name": scope[1]}}},
                                 next_primary_renewal=time.monotonic() + 30, primary_scope=scope, last_primary_intent=0,
                                 bridge=Mock(input_namespace=uuid.uuid4(), native_primary_input_requests=Mock(return_value=pending)),
                                 control=Mock(), gameplay_action=AsyncMock(return_value={}))
        await Client.service_primary_input(client, True)
        operation_id = uuid.uuid5(client.bridge.input_namespace, "session:epoch:local pawn:primary:4").hex
        client.gameplay_action.assert_awaited_once_with(melee.SWING, operation_id, GUID)
        client.control.event.assert_called_once_with("native_guest_melee_input", operation_id=operation_id, sequence=4)
