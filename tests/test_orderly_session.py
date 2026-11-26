import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import link, party
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.runtime import MAP
from tools.silverfish.service import Client, Host


SESSION, EPOCH, OPERATION = "a" * 32, "b" * 32, "c" * 32


class OrderlySessionTests(unittest.IsolatedAsyncioTestCase):
    def host(self, store, invitation):
        host = Host.__new__(Host)
        host.store = store
        host.control = Mock(generation=SESSION)
        host.zone_epoch = EPOCH
        host.last_game = {"state": "hosting", "map": MAP}
        host.native_profile = Path("isolated-profile")
        host.persist_native_guest = True
        host.native_checkpoint_unsafe = False
        host.stopping = host.transitioning = False
        host.session_stop_reason = None
        host.expired_characters = set()
        host.smg_holds = None
        host.pickup_audio = Mock(frame=Mock(return_value=None))
        host.party = party.new_party()
        host.native_requests = asyncio.Queue(maxsize=8)
        host.native_request_ready = asyncio.Event()
        host.prepared_guest_leaves = {}
        host.confirmed_guest_leaves = set()
        guest = store.activate(invitation["guest_id"], invitation["credential"], invitation["character_id"])
        host.guest = {"guest_id": invitation["guest_id"], "character_id": invitation["character_id"],
                      "lease_id": guest["lease_id"], "disconnected": None}
        host.native_recipients = lambda: {invitation["character_id"]: {
            "pawn": "exact authority pawn", "connected": host.guest["disconnected"] is None,
        }}
        host.checkpoint = Mock()
        host.checkpoint_native = AsyncMock(return_value=17)
        return host

    async def guest_request(self, host, guest_id, action, target=EPOCH):
        reply = host.guest_request(guest_id, {"action": action, "operation_id": OPERATION, "target": target})
        if isinstance(reply, dict):
            return reply
        task = asyncio.create_task(reply)
        await asyncio.sleep(0)
        await host.service_guest_native_batch("bridge")
        return await task

    async def test_guest_leave_requires_own_paired_frame_and_exact_confirmation(self):
        with CampaignStore(Path(self.enterContext(tempfile.TemporaryDirectory())) / "save") as store:
            invite = store.issue_invitation("Alice", "Scout")
            other = store.issue_invitation("Bob", "Cook")
            host = self.host(store, invite)
            guest_id, character = invite["guest_id"], invite["character_id"]
            foreign = await self.guest_request(host, other["guest_id"], link.GUEST_LEAVE)
            self.assertEqual(foreign, {"ok": False, "error": "guest_not_active"})
            stale = await self.guest_request(host, guest_id, link.GUEST_LEAVE, target="old-epoch")
            self.assertEqual(stale, {"ok": False, "error": "native_session_leave_invalid"})
            premature = await self.guest_request(host, guest_id, link.GUEST_LEAVE_CONFIRM)
            self.assertEqual(premature, {"ok": False, "error": "native_session_leave_checkpoint_required"})
            host.checkpoint_native.side_effect = RuntimeError("Missing exact guest frame")
            failed = await self.guest_request(host, guest_id, link.GUEST_LEAVE)
            self.assertEqual(failed, {"ok": False, "error": "Missing exact guest frame"})
            self.assertEqual(host.authenticated_guest(guest_id), ("guest", character))
            self.assertFalse(host.guest_leave_pending(guest_id))

            host.checkpoint_native.side_effect = None
            prepared = await self.guest_request(host, guest_id, link.GUEST_LEAVE)
            host.checkpoint_native.assert_awaited_with("bridge", require_guest_frames=frozenset({character}))
            self.assertEqual(store.status()["leases"][0]["state"], "active")
            receipt = {"ok": True, "guest_leave": {"session_id": SESSION, "zone_epoch": EPOCH,
                       "character_id": character, "operation_id": OPERATION, "revision": 17}}
            self.assertEqual(prepared, receipt)
            self.assertEqual(await self.guest_request(host, guest_id, link.GUEST_LEAVE_CONFIRM), receipt)
            self.assertEqual(await self.guest_request(host, guest_id, link.GUEST_LEAVE_CONFIRM), receipt)
            self.assertEqual(host.checkpoint_native.await_count, 2)
            host.disconnected(guest_id)
            self.assertEqual(store.status()["leases"], [])
            self.assertIsNone(host.authenticated_guest(guest_id))
            self.assertIn(character, host.expired_characters)
            host.checkpoint.assert_not_called()

    async def test_link_loss_during_final_capture_preserves_normal_reconnect_grace(self):
        with CampaignStore(Path(self.enterContext(tempfile.TemporaryDirectory())) / "save") as store:
            invite = store.issue_invitation("Alice", "Scout")
            host = self.host(store, invite)
            guest_id, character = invite["guest_id"], invite["character_id"]

            async def race(bridge, require_guest_frames):
                host.disconnected(guest_id)
                self.assertEqual(require_guest_frames, {character})
                return 17

            host.checkpoint_native.side_effect = race
            reply = await self.guest_request(host, guest_id, link.GUEST_LEAVE)
            self.assertEqual(reply, {"ok": False, "error": "native_session_leave_connection_changed"})
            self.assertFalse(host.guest_leave_pending(guest_id))
            self.assertFalse(host.transitioning)
            self.assertLess(time.monotonic() - host.guest["disconnected"], 1)
            resumed = store.activate(guest_id, invite["credential"], character)
            self.assertTrue(resumed["resumed"])

    async def test_host_end_announces_only_after_exact_paired_commit(self):
        with CampaignStore(Path(self.enterContext(tempfile.TemporaryDirectory())) / "save") as store:
            invite = store.issue_invitation("Alice", "Scout")
            second = store.issue_invitation("Kellan", "Runner")
            third = store.issue_invitation("Nora", "Cook")
            guests = [entry["character_id"] for entry in (invite, second, third)]
            host = self.host(store, invite)
            host.roles = lambda: {"host": "host", **dict(zip(("guest", "guest2", "guest3"), guests, strict=True))}
            relay = SimpleNamespace(announce_session_end=AsyncMock(return_value=3))
            host.checkpoint_native.side_effect = RuntimeError("Missing exact guest frame")
            with self.assertRaisesRegex(RuntimeError, "Missing exact guest frame"):
                await host.end_session("bridge", relay)
            relay.announce_session_end.assert_not_awaited()
            self.assertFalse(host.stopping or host.transitioning)
            host.checkpoint_native.side_effect = None
            self.assertEqual(await host.end_session("bridge", relay), {"revision": 17, "peers_acknowledged": 3})
            host.checkpoint_native.assert_awaited_with("bridge", require_guest_frames=frozenset(guests))
            relay.announce_session_end.assert_awaited_once_with(SESSION, 17)
            self.assertTrue(host.stopping)
            self.assertEqual(host.session_stop_reason, "host_session_ended")

    async def test_guest_client_stops_only_after_its_own_durable_receipt(self):
        exact = {"session_id": SESSION, "zone_epoch": EPOCH, "operation_id": OPERATION,
                 "character_id": "scout", "revision": 17}
        for initial in ({"ok": False, "error": "missing_exact_frame"},
                        {"ok": True, "guest_leave": dict(exact, character_id="other")},
                        {"ok": True, "guest_leave": exact}):
            with self.subTest(initial=initial):
                transport = SimpleNamespace(game_access=True, session_end=None, address=("127.0.0.1", 51000),
                                            request=AsyncMock(side_effect=[initial, {"ok": True, "guest_leave": exact}]))
                client = Client(Mock(), transport, {"character_id": "scout"}, Mock())
                client.host_session, client.zone_epoch = SESSION, EPOCH
                client.last_game = {"state": "connected"}
                if initial.get("guest_leave") != exact:
                    with self.assertRaises((RuntimeError, ValueError)):
                        await client.leave_session(OPERATION)
                    self.assertFalse(client.stopping)
                    self.assertEqual(transport.request.await_count, 1)
                else:
                    self.assertEqual(await client.leave_session(OPERATION), {"revision": 17, "retirement_confirmed": True})
                    first, second = transport.request.await_args_list
                    self.assertEqual((first.args[0]["action"], second.args[0]["action"]),
                                     (link.GUEST_LEAVE, link.GUEST_LEAVE_CONFIRM))
                    self.assertTrue(client.stopping)

    async def test_quic_orderly_end_is_generation_bound_and_survives_transport_close(self):
        identity = link.make_server_identity()
        request = {"protocol_version": 1, "game_build": "test", "guest_id": "guest",
                   "character_id": "scout", "credential": "authority"}

        def admit(value):
            if value == request:
                return {"accepted": True, "character_id": "scout", "session_id": SESSION}
            return {"accepted": False, "reason": "unexpected_guest"}

        relay = await link.start_host("127.0.0.1", 0, "127.0.0.1", 9, identity, admit)
        self.addAsyncCleanup(relay.close)
        transport = await link.start_client()
        self.addAsyncCleanup(transport.close)
        reply = await transport.connect(*relay.address, identity.certificate_pem, request)
        self.assertTrue(reply["accepted"])
        self.assertIsNone(transport.session_end)
        self.assertEqual(await relay.announce_session_end(SESSION, 17), 1)
        await relay.close()
        client = Client.__new__(Client)
        client.transport, client.host_session, client.control = transport, SESSION, Mock()
        client.stopping, client.session_stop_reason = False, None
        self.assertTrue(client.check_host_end())
        self.assertTrue(client.stopping)
        client.control.event.assert_called_once_with("host_session_ended", revision=17)
