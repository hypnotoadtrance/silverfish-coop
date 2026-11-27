import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish.campaign import CampaignStore
from tools.silverfish.multi_host import MultiHost
from tools.silverfish.runtime import MAP
from tools.silverfish.service import NATIVE_RUNTIME_RETIRING, PROTOCOL, Client, Host, open_admission
from test_party_members import connection, snapshot


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    invite = {"host": "127.0.0.1", "port": 7777, "certificate": "unused test certificate"}

    async def test_catalog_handoff_retries_an_in_flight_release(self):
        transport = Mock()
        transport.connect = AsyncMock(side_effect=[
            {"accepted": False, "reason": "guest_already_connected"}, {"accepted": True},
        ])
        result = await open_admission(transport, self.invite, {}, timeout=1)
        self.assertTrue(result["accepted"])
        self.assertEqual(transport.connect.await_count, 2)

    async def test_initial_join_waits_for_travel_and_publishes_wait_reason(self):
        transport = Mock()
        transport.connect = AsyncMock(side_effect=[
            {"accepted": False, "reason": "travel_in_progress"}, {"accepted": True},
        ])
        waiting = Mock()
        result = await open_admission(transport, self.invite, {}, timeout=1, queue=True, on_wait=waiting)
        self.assertTrue(result["accepted"])
        waiting.assert_called_once_with("travel_in_progress")

    async def test_credentials_or_character_refusal_is_not_retried(self):
        transport = Mock()
        transport.connect = AsyncMock(return_value={"accepted": False, "reason": "guest_or_character_unavailable"})
        result = await open_admission(transport, self.invite, {}, timeout=1, queue=True)
        self.assertFalse(result["accepted"])
        self.assertEqual(transport.connect.await_count, 1)

    async def test_initial_join_waits_for_native_runtime_retirement(self):
        transport = Mock()
        transport.connect = AsyncMock(side_effect=[
            {"accepted": False, "reason": NATIVE_RUNTIME_RETIRING}, {"accepted": True},
        ])
        waiting = Mock()
        result = await open_admission(transport, self.invite, {}, timeout=1, queue=True, on_wait=waiting)
        self.assertTrue(result["accepted"])
        self.assertEqual(transport.connect.await_count, 2)
        waiting.assert_called_once_with(NATIVE_RUNTIME_RETIRING)

    async def test_disconnected_guest_retention_timeout_is_logged_once_and_keeps_host_running(self):
        control = Mock()
        store = Mock(status=Mock(return_value={"world": {}}))
        host = Host(store, control, "build", None, "127.0.0.1", 0, False, None,
                    native_backend=None, native_events=False, native_guest=True)
        host.last_game = {"state": "hosting", "map": MAP, "native_guest_save_retention": True}
        bridge = Mock(command=AsyncMock(side_effect=TimeoutError("guest pawn already retired")))

        await host.retain_disconnected_guest(bridge, "guest", "scout", "old pawn")
        await host.retain_disconnected_guest(bridge, "guest", "scout", "old pawn")

        bridge.command.assert_awaited_once_with("native-guest-save-retain", "old pawn", "scout", timeout=1)
        control.event.assert_called_once_with("guest_disconnect_retention_unavailable", character_id="scout",
                                              reason="guest pawn already retired")
        self.assertFalse(host.stopping)
        self.assertFalse(host.native_checkpoint_unsafe)

    async def test_client_disables_routes_when_admission_does_not_confirm_selected_character(self):
        invite = dict(self.invite, guest_id="guest", credential="credential", character_id="scout", game_build="build")
        for character in (None, "different-character"):
            with self.subTest(character=character):
                response = {"accepted": True, "session_id": "a" * 32, "zone": MAP, "zone_epoch": "first"}
                if character is not None:
                    response["character_id"] = character
                transport = Mock(address=("127.0.0.1", 7778), game_access=True,
                                 connect=AsyncMock(return_value=response), disconnect=AsyncMock())
                control = Mock()
                client = Client(control, transport, invite, Mock())

                with self.assertRaisesRegex(RuntimeError, "did not confirm the selected character"):
                    await client.connect()

                transport.disconnect.assert_awaited_once_with()
                control.event.assert_not_called()
                self.assertIsNone(client.host_session)


class RuntimeAdmissionTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.store = self.enterContext(CampaignStore(directory, clock=lambda: 1_000))
        self.control = Mock(generation="1" * 32)
        self.host = Host(self.store, self.control, "build", None, "127.0.0.1", 0, False, None,
                         native_backend=None, native_events=False, native_guest=False)
        self.host.last_game = {"state": "hosting", "map": MAP, "remote_clients": 1}
        self.host.checkpoint = Mock()
        invite = self.store.issue_invitation("Guest", "Scout")
        self.guest_id = invite["guest_id"]
        self.character_id = invite["character_id"]
        self.owned_other = self.store.create_character(self.guest_id, invite["credential"], "Another Scout")["character_id"]
        self.old_request = dict(invite, game_build="build", protocol_version=PROTOCOL, runtime_id="a" * 32)
        self.fresh_request = dict(self.old_request, runtime_id="b" * 32)
        self.assertTrue(self.host.admit(self.old_request)["accepted"])
        self.host.relay = Mock()
        self.host.relay.guest_runtime_id.return_value = self.old_request["runtime_id"]
        self.host.enrollment = Mock(assignments={self.guest_id: SimpleNamespace(connection_id="c1")})
        self.host.enrollment.reserve.return_value = SimpleNamespace(state="bound")
        self.host.guest_pawn = "old native pawn"
        self.host.disconnected(self.guest_id)
        self.old_guest = self.host.guest.copy()
        self.old_status = self.store.status()
        self.control.event.reset_mock()

    def assert_old_state(self):
        self.assertEqual(self.host.guest, self.old_guest)
        self.assertEqual(self.host.guest_pawn, "old native pawn")
        self.assertEqual(self.store.status(), self.old_status)
        self.host.enrollment.reserve.assert_not_called()

    def test_fresh_runtime_is_parked_until_the_old_native_seat_retires(self):
        refusal = {"accepted": False, "reason": NATIVE_RUNTIME_RETIRING}
        self.assertEqual(self.host.admit(self.fresh_request), refusal)
        self.assertEqual(self.host.admit(self.fresh_request), refusal)
        self.assert_old_state()
        proof = dict(guest_id=self.guest_id, character_id=self.character_id, zone_epoch=self.host.zone_epoch,
                     old_connection_id="c1")
        self.assertEqual(self.host.guest_runtime_replacements, {self.guest_id: proof})
        self.control.event.assert_called_once_with("guest_runtime_replacement_pending", **proof)

        self.store.release(self.old_guest["lease_id"])
        self.host.guest = None
        self.host.last_game["remote_clients"] = 0
        self.host.enrollment.assignments.clear()
        self.host.report_guest_runtime_retired(self.guest_id)
        result = self.host.admit(self.fresh_request)

        self.assertTrue(result["accepted"])
        self.assertFalse(result["resumed"])
        self.assertTrue(result["rotate_game_route"])
        self.assertNotEqual(self.host.guest["lease_id"], self.old_guest["lease_id"])
        self.assertIsNone(self.host.guest_pawn)
        self.control.event.assert_any_call("guest_runtime_retired", **proof)

    def test_wrong_credential_and_wrong_character_do_not_affect_old_lease(self):
        self.host.last_game["remote_clients"] = 0
        for request in (dict(self.fresh_request, credential="incorrect"),
                        dict(self.fresh_request, character_id=self.owned_other)):
            with self.subTest(character_id=request["character_id"]):
                self.assertEqual(self.host.admit(request), {
                    "accepted": False, "reason": "guest_or_character_unavailable",
                })
                self.assert_old_state()
                self.assertFalse(self.host.guest_runtime_replacements)
                self.control.event.assert_not_called()

    def test_warm_runtime_can_resume_the_original_lease_while_fresh_runtime_is_parked(self):
        self.assertEqual(self.host.admit(self.fresh_request)["reason"], NATIVE_RUNTIME_RETIRING)

        result = self.host.admit(self.old_request)

        self.assertTrue(result["accepted"])
        self.assertTrue(result["resumed"])
        self.assertNotIn("rotate_game_route", result)
        self.assertEqual(self.host.guest["lease_id"], self.old_guest["lease_id"])
        self.assertEqual(self.host.guest_pawn, "old native pawn")
        self.assertFalse(self.host.guest_runtime_replacements)


class QualifiedRuntimeDepartureTests(unittest.TestCase):
    def test_replacement_resumes_before_grace_only_after_qualified_departure(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        store = self.enterContext(CampaignStore(directory))
        control = Mock()
        host = MultiHost(store, control, "build", None, "127.0.0.1", 0, False, None, max_guests=3,
                         native_backend="cpp", native_events=True, native_guest=True, verify_native_roster=True,
                         persist_native_guest=True, native_profile=object())
        guest = dict(store.issue_invitation("Guest", "Scout"), game_build="build", protocol_version=PROTOCOL,
                     runtime_id="a" * 32)
        observer = dict(store.issue_invitation("Observer", "Pilot"), game_build="build", protocol_version=PROTOCOL,
                        runtime_id="b" * 32)
        replacement = dict(guest, runtime_id="c" * 32)
        first, second, fresh = connection(1), connection(2), connection(3)
        host.last_game = snapshot(1)
        self.assertTrue(host.admit(guest)["accepted"])
        host.last_game = snapshot(2, first)
        original = host.members.observe(host.last_game).assignment
        host.members.mark_ready(guest["guest_id"], original)
        member = host.members.get(guest["guest_id"])
        lease = member.lease_id

        self.assertTrue(host.admit(observer)["accepted"])
        host.last_game = snapshot(3, first, second)
        observer_binding = host.members.observe(host.last_game).assignment
        host.members.mark_ready(observer["guest_id"], observer_binding)
        host.last_seen = time.monotonic()

        host.disconnected(guest["guest_id"])
        parked = {"accepted": False, "reason": NATIVE_RUNTIME_RETIRING}
        self.assertEqual(host.admit(replacement), parked)
        warm = host.admit(guest)
        self.assertTrue(warm["resumed"])
        self.assertNotIn("rotate_game_route", warm)
        self.assertEqual(member.binding, original)

        host.disconnected(guest["guest_id"])
        self.assertEqual(host.admit(replacement), parked)
        host.last_game = snapshot(4, second)
        self.assertEqual(host.members.observe(host.last_game).reason, "known_connection_missing")
        self.assertEqual(host.members.forget_departed(guest["guest_id"], host.last_game).state, "released")
        self.assertIsNone(member.binding)
        self.assertTrue(member.in_grace(time.monotonic()))

        host.last_seen = time.monotonic() - 10
        self.assertEqual(host.admit(replacement), parked)
        host.last_seen = time.monotonic()
        reservation_until = member.grace_until
        member.grace_until = time.monotonic() - 1
        self.assertEqual(host.admit(replacement), parked)
        member.grace_until = reservation_until
        self.assertFalse(host.admit(dict(replacement, credential="incorrect"))["accepted"])
        self.assertIsNone(host.enrollment.pending)
        self.assertEqual(member.lease_id, lease)

        replaced = host.admit(replacement)
        self.assertTrue(replaced["accepted"])
        self.assertTrue(replaced["resumed"])
        self.assertTrue(replaced["rotate_game_route"])
        self.assertEqual(member.lease_id, lease)
        self.assertEqual(member.runtime_id, replacement["runtime_id"])
        self.assertTrue(member.spawn_near_host)
        self.assertFalse(member.ready)
        self.assertEqual(host.enrollment.pending.guest_id, guest["guest_id"])
        host.last_game = snapshot(5, second, fresh)
        self.assertEqual(host.members.observe(host.last_game).assignment.connection_id, "c3")
        self.assertEqual(host.members.get(observer["guest_id"]).binding, observer_binding)
        self.assertFalse(host.admit(guest)["accepted"])
