import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import party
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.multi_host import MultiHost
from tools.silverfish.service import PROTOCOL, Client, Host
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


class LegacyGuestWipeRosterTests(unittest.IsolatedAsyncioTestCase):
    async def test_entered_guest_stays_in_wipe_roster_without_a_stock_pawn(self):
        def frame(sequence, connection=None):
            pawns = {"local": {"name": "Host pawn", "health": 250, "location": [0, 0, 0]}}
            rows = []
            state = {"mode": "host", "state": "hosting", "map": CROSSROADS, "server_connection": False,
                     "sequence": sequence, "remote_clients": int(connection is not None), "pawns": pawns,
                     "native_roster": {"state": "qualified", "driver": "d1",
                                       "count": int(connection is not None), "connections": rows}}
            if connection:
                name, controller = f"Player_FP_C Guest_{connection}", f"PlayerControler_FP_C Guest_{connection}"
                rows.append({"state": "ready", "id": connection, "connection": f"IpConnection Guest_{connection}",
                             "pawn": name, "controller": controller})
                pawns["guest"] = {"name": name, "controller": controller, "health": 40, "location": [100, 0, 0]}
                state["native_guest_save"] = {"state": "bound", "pawn": name}
            return state

        directory = Path(self.enterContext(TemporaryDirectory()))
        store = self.enterContext(CampaignStore(directory))
        invite = dict(store.issue_invitation("Guest", "Scout"), protocol_version=PROTOCOL, game_build="build")
        guest, character = invite["guest_id"], invite["character_id"]
        host = Host(store, Mock(), "build", None, "127.0.0.1", 0, False, None,
                    native_backend="cpp", native_events=True, native_guest=True, authored_zones=True,
                    verify_native_roster=True)
        host.last_game = frame(1)
        host.checkpoint = Mock(return_value=1)
        host.checkpoint_native = AsyncMock(return_value=2)
        bridge = Mock()
        transport = Mock()
        self.assertTrue(host.admit(invite)["accepted"])
        self.assertEqual(host.roles(), {"host": "host"})

        bridge.snapshot.return_value = frame(2, "c1")
        await host.observe(bridge, transport)
        self.assertEqual(host.role_pawn("guest"), ("guest", None))
        self.assertEqual(host.act("host", uuid.uuid4().hex, "native-down", "host", 1000)["effect"], "downed")

        bridge.snapshot.return_value = frame(3)
        await host.observe(bridge, transport)
        self.assertIsNone(host.guest_pawn)
        roles = {"host": "host", "guest": character}
        self.assertEqual(host.roles(), roles)
        self.assertEqual(host.travel_refusal(OBSERVATORY, roles), {
            "ok": False, "error": "travel_blocked", "blockers": [{"id": character, "reason": "missing_pawn"}],
        })
        host.last_game = frame(3, "c2")
        self.assertNotIn(character, host.authoritative_pawns(roles))
        self.assertEqual(host.native_recipients(), {})
        host.last_game = frame(3)
        await host.settle_wipe(bridge, transport)
        self.assertTrue(host.party["players"]["host"]["downed"])
        self.assertIsNone(host.party["wipe"])
        host.checkpoint_native.assert_not_awaited()

        lease = host.guest["lease_id"]
        host.disconnected(guest)
        self.assertEqual(host.roles(), roles)
        self.assertTrue(host.admit(invite)["resumed"])
        self.assertEqual(host.guest["lease_id"], lease)
        self.assertEqual(host.roles(), roles)
        bridge.snapshot.return_value = frame(4, "c2")
        await host.observe(bridge, transport)
        self.assertEqual(host.authoritative_pawns(roles)[character]["health"], 40)
        self.assertIsNone(host.travel_refusal(OBSERVATORY, roles))

        host.confirmed_guest_leaves.add(guest)
        host.disconnected(guest)
        self.assertEqual(host.roles(), {"host": "host"})
        await host.settle_wipe(bridge, transport)
        host.checkpoint_native.assert_awaited_once()


class GuestReturnSnapshotTests(unittest.TestCase):
    def test_cleared_reservation_keeps_bound_guest_joining_during_attachment(self):
        host = object.__new__(MultiHost)
        first = SimpleNamespace(binding=None, disconnected_at=None, ready=False)
        second = SimpleNamespace(binding=None, disconnected_at=None, ready=False)
        host.members = {"first": first, "second": second}
        host.returning_guests = ["first", "second"]
        host.enrollment = SimpleNamespace(pending=SimpleNamespace(guest_id="first", id=7))
        host.return_waiting = 7
        host.return_census_floor = None
        host.transitioning = False
        host.party = party.new_party()
        with patch.object(Host, "snapshot_for_guest", side_effect=lambda *_: {"travel_in_progress": False}):
            self.assertEqual(host.snapshot_for_guest("first", "guest"), {
                "travel_in_progress": False, "native_return_reservation": 7, "native_return_retry_allowed": True,
            })
            self.assertEqual(host.snapshot_for_guest("second", "guest2"), {"travel_in_progress": True})

            first.binding = object()
            host.enrollment.pending = None
            self.assertEqual(host.snapshot_for_guest("first", "guest"), {"travel_in_progress": False})
            self.assertEqual(host.snapshot_for_guest("second", "guest2"), {"travel_in_progress": True})

            host.enrollment.pending = SimpleNamespace(guest_id="second", id=8)
            self.assertEqual(host.snapshot_for_guest("first", "guest"), {"travel_in_progress": False})
            self.assertEqual(host.snapshot_for_guest("second", "guest2"), {
                "travel_in_progress": False, "native_return_reservation": 8, "native_return_retry_allowed": False,
            })
            host.transitioning = True
            self.assertEqual(host.snapshot_for_guest("first", "guest"), {"travel_in_progress": True})


class GuestExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_attachment_failure_aborts_only_the_joining_guest(self):
        failed = SimpleNamespace(guest_id="peer1", character_id="scout", role="guest", ready=False, disconnected_at=None)
        ready = SimpleNamespace(guest_id="peer2", role="guest2", ready=True, disconnected_at=None)
        frame = {"sequence": 7}
        error = RuntimeError("The retained guest save is unavailable")
        host = SimpleNamespace(
            members=SimpleNamespace(members=(failed, ready), observe=Mock(return_value=SimpleNamespace(state="ready", reason=None)),
                                    match_pawns=Mock(return_value={"guest": object(), "guest2": object()})),
            last_game=frame, party=party.new_party(), control=Mock(), stopping=False,
            observe_host_frame=Mock(), recover_joining_pawn=AsyncMock(return_value=False), _refresh_pending_return=AsyncMock(),
            _expire_return_handshake=AsyncMock(), _retire_expired_guests=AsyncMock(),
            _attach_member=AsyncMock(side_effect=error), _reserve_next_return=Mock(),
        )
        bridge = Mock()
        bridge.snapshot.return_value = frame

        async def abort(guest_id):
            self.assertEqual(guest_id, failed.guest_id)
            failed.disconnected_at = 1

        transport = SimpleNamespace(abort_guest_route=AsyncMock(side_effect=abort))
        await MultiHost.observe(host, bridge, transport)
        await MultiHost.observe(host, bridge, transport)

        transport.abort_guest_route.assert_awaited_once_with(failed.guest_id)
        host.control.event.assert_called_once_with("native_guest_attachment_failed", character_id=failed.character_id,
                                                 reason=str(error))
        self.assertTrue(ready.ready)
        self.assertFalse(host.stopping)

    async def test_retirement_releases_only_guests_whose_native_connections_cleared(self):
        first = SimpleNamespace(guest_id="peer1")
        second = SimpleNamespace(guest_id="peer2")
        present = {first.guest_id: first, second.guest_id: second}

        def expire(_game):
            present.pop(second.guest_id, None)
            return first, second

        members = SimpleNamespace(expire=Mock(side_effect=expire), get=present.get)
        host = SimpleNamespace(members=members, last_game={}, report_guest_runtime_retired=Mock())
        transport = SimpleNamespace(release_guest=AsyncMock())
        await MultiHost._retire_expired_guests(host, transport)
        members.expire.assert_called_once_with(host.last_game)
        transport.release_guest.assert_awaited_once_with("peer2")
        host.report_guest_runtime_retired.assert_called_once_with("peer2")


class GuestInputIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_pawn_and_sequence_in_new_client_are_a_new_input(self):
        guid = "A" * 32
        shot = {"sequence": 1, "pawn": "exact pawn", "map": "zone", "guid": guid, "barrel": "primary"}
        game = {"map": "zone", "pawns": {"local": {"name": "exact pawn"}}, "native_guest_reload_intent": shot}

        def client():
            return SimpleNamespace(last_game=game, last_reload_intent=0, stopping=False,
                                   host_session="host", zone_epoch="zone", control=Mock(),
                                   gameplay={"native_equipped_weapon": {"guid": guid, "kind": "light-pistol"}},
                                   bridge=SimpleNamespace(input_namespace=uuid.uuid4()), gameplay_action=AsyncMock(return_value={}))

        first, second = client(), client()
        await Client.service_reload_input(first, True)
        await Client.service_reload_input(first, True)
        await Client.service_reload_input(second, True)
        self.assertEqual(first.gameplay_action.await_count, 1)
        self.assertNotEqual(first.gameplay_action.await_args.args[1], second.gameplay_action.await_args.args[1])


class NativeRespawnTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_wipe_participant_heals_after_joining_host_in_later_zone(self):
        saved = party.new_party()
        saved["pending_spawns"]["scout"] = {"map": "rest", "location": [9, 8, 7], "at_native_rest": True}
        current = {"map": "later", "pawns": {"guest": {"health": 0, "max_health": 250, "location": [1, 2, 3]}}}
        restored = {"map": "later", "pawns": {"guest": {**current["pawns"]["guest"], "health": 250}}}
        binding = object()
        host = SimpleNamespace(party=saved, last_game=current, native_guest=True,
                               roles=lambda: {"guest": "scout"}, role_pawn=lambda _role: ("guest", binding),
                               checkpoint=Mock(), checkpoint_native=AsyncMock())
        bridge = SimpleNamespace(restore=AsyncMock(side_effect=[None, restored]))

        await Host.apply_pending_spawns(host, bridge)
        self.assertIn("scout", host.party["pending_spawns"])
        host.checkpoint_native.assert_not_awaited()

        await Host.apply_pending_spawns(host, bridge)
        bridge.restore.assert_awaited_with("guest", {"location": [1, 2, 3], "health": 250}, binding=binding,
                                           respawning=True)
        self.assertIs(host.last_game, restored)
        self.assertEqual(host.party["pending_spawns"], {})
        host.checkpoint_native.assert_awaited_once_with(bridge, host.party, require_guest_frames=frozenset({"scout"}))
        host.checkpoint.assert_not_called()
