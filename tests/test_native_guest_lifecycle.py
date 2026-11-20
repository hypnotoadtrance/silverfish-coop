import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


class GuestExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_commit_does_not_confirm_a_retiring_guest(self):
        frame = {"slot": "character-save"}
        observation = {"name": "guest-pawn", "health": 19, "location": [1, 2, 3]}
        host = SimpleNamespace(
            persist_native_guest=True, native_profile=True, native_checkpoint_unsafe=False,
            transitioning=True, last_game={"map": "zone", "pawns": {"guest": observation}},
            store=SimpleNamespace(status=lambda: {"leases": []}), control=Mock(),
            capture_guest_generation=AsyncMock(return_value=frame),
            checkpoint_native=AsyncMock(side_effect=(OSError("sharing violation"), 9)),
        )
        bridge = object()
        departing = {"character": "guest-pawn"}
        self.assertEqual(await Host.checkpoint_departing_guests(host, bridge, departing), set())
        self.assertTrue(host.transitioning)
        self.assertEqual(await Host.checkpoint_departing_guests(host, bridge, departing), {"character"})
        captured = host.checkpoint_native.await_args.kwargs["unleased_states"]["character"]
        self.assertEqual((captured["health"], captured["native_guest_save"]), (19, frame))

    async def test_retry_holds_only_the_guest_whose_checkpoint_failed(self):
        first = SimpleNamespace(role="guest", lease_id="lease1", guest_id="peer1", character_id="char1", ready=True,
                                binding=SimpleNamespace(pawn="pawn1"))
        second = SimpleNamespace(role="guest2", lease_id="lease2", guest_id="peer2", character_id="char2", ready=True,
                                 binding=SimpleNamespace(pawn="pawn2"))
        old_stock_gone = {"peer2"}
        members_by_id = {member.guest_id: member for member in (first, second)}

        def expire(_game, awaiting_checkpoint):
            eligible = tuple(member for member in members_by_id.values() if member.lease_id not in awaiting_checkpoint)
            for member in eligible:
                if member.guest_id in old_stock_gone:
                    del members_by_id[member.guest_id]
            return eligible

        members = SimpleNamespace(
            expired=lambda: tuple(members_by_id.values()), get=members_by_id.get,
            match_pawns=lambda *_args, **_kwargs: {member.role: member for member in members_by_id.values()},
            expire=expire,
        )
        host = SimpleNamespace(members=members, last_game={}, retirement_processed=set(), expiry_capture_attempts={},
                               checkpoint_departing_guests=AsyncMock(side_effect=({"char2"}, {"char1"}, set())),
                               report_guest_runtime_retired=Mock())
        bridge = SimpleNamespace(command=AsyncMock())
        transport = SimpleNamespace(release_guest=AsyncMock())
        await MultiHost._retire_expired_guests(host, bridge, transport)
        transport.release_guest.assert_awaited_once_with("peer2")
        self.assertEqual(host.expiry_capture_attempts, {"lease1": 1})
        await MultiHost._retire_expired_guests(host, bridge, transport)
        host.checkpoint_departing_guests.assert_awaited_with(bridge, {"char1": "pawn1"})
        transport.release_guest.assert_awaited_once_with("peer2")
        host.report_guest_runtime_retired.assert_called_once_with("peer2")
        self.assertEqual(host.expiry_capture_attempts, {})

        old_stock_gone.add("peer1")
        await MultiHost._retire_expired_guests(host, bridge, transport)
        transport.release_guest.assert_awaited_with("peer1")
        host.report_guest_runtime_retired.assert_called_with("peer1")


class GuestInputIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_pawn_and_sequence_in_new_client_are_a_new_input(self):
        shot = {"sequence": 1, "pawn": "exact pawn", "map": "zone"}
        game = {"map": "zone", "pawns": {"local": {"name": "exact pawn"}}, "native_guest_reload_intent": shot}

        def client():
            return SimpleNamespace(last_game=game, last_reload_intent=0, stopping=False,
                                   host_session="host", zone_epoch="zone", control=Mock(),
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
