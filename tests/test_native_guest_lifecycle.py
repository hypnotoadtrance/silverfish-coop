import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import party
from tools.silverfish.multi_host import MultiHost
from tools.silverfish.service import Client, Host


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
        expired = (first, second)
        members = SimpleNamespace(
            expired=lambda: expired, match_pawns=lambda *_args, **_kwargs: {member.role: member for member in expired},
            expire=Mock(side_effect=lambda _game, awaiting_checkpoint: tuple(
                member for member in expired if member.lease_id not in awaiting_checkpoint)),
        )
        host = SimpleNamespace(members=members, last_game={}, retirement_processed=set(), expiry_capture_attempts={},
                               checkpoint_departing_guests=AsyncMock(side_effect=({"char2"}, {"char1"})))
        bridge = SimpleNamespace(command=AsyncMock())
        transport = SimpleNamespace(release_guest=AsyncMock())
        await MultiHost._retire_expired_guests(host, bridge, transport)
        transport.release_guest.assert_awaited_once_with("peer2")
        self.assertEqual(host.expiry_capture_attempts, {"lease1": 1})
        expired = (first,)
        await MultiHost._retire_expired_guests(host, bridge, transport)
        transport.release_guest.assert_awaited_with("peer1")
        host.checkpoint_departing_guests.assert_awaited_with(bridge, {"char1": "pawn1"})
        self.assertEqual(host.expiry_capture_attempts, {})


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
