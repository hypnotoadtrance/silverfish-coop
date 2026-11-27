import asyncio
import os
import secrets
import time
import unittest
import zlib
from contextlib import suppress
from pathlib import Path
from random import Random
from string import ascii_letters, digits
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aioquic.quic.events import ConnectionTerminated, DatagramFrameReceived, StreamDataReceived
from cryptography.hazmat.primitives import serialization

from tools.silverfish import link, party
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.link import (
    LinkError,
    load_server_identity,
    make_server_identity,
    start_client,
    start_host,
)
from tools.silverfish.runtime import Bridge, MAP
from tools.silverfish.service import Client, Host
from tools.silverfish.udp_ownership import verify_loopback_socket


class NetworkTickTests(unittest.IsolatedAsyncioTestCase):
    async def test_monotonic_slots_skip_worker_stalls_without_replaying_ticks(self):
        now = 0.0

        async def sleep(delay):
            nonlocal now
            now += delay
            if abs(now - .15) < .001:
                now += .18

        loop = SimpleNamespace(time=lambda: now)
        ticks = link.network_ticks()
        try:
            with patch.object(link, "asyncio", SimpleNamespace(get_running_loop=lambda: loop, sleep=sleep)):
                await anext(ticks)
                self.assertAlmostEqual(now, .05)
                now += .012
                await anext(ticks)
                self.assertAlmostEqual(now, .10)
                await anext(ticks)
                self.assertAlmostEqual(now, .33)
                await anext(ticks)
                self.assertAlmostEqual(now, .38)
                now += .105
                await anext(ticks)
                self.assertAlmostEqual(now, .53)
        finally:
            await ticks.aclose()


class DatagramInbox(asyncio.DatagramProtocol):
    def __init__(self):
        self.messages = asyncio.Queue()

    def datagram_received(self, data, address):
        self.messages.put_nowait((data, address))

    async def next(self):
        return await asyncio.wait_for(self.messages.get(), 3)


class PacketBlackhole(asyncio.DatagramProtocol):
    def __init__(self, remote):
        self.remote = remote
        self.peer = None
        self.blocked = False
        self.discarded = 0

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if addr != self.remote:
            self.peer = addr
        destination = self.peer if addr == self.remote else self.remote
        if self.blocked:
            self.discarded += 1
        elif destination is not None:
            self.transport.sendto(data, destination)


class LinkTests(unittest.IsolatedAsyncioTestCase):
    async def udp(self):
        loop = asyncio.get_running_loop()
        transport, inbox = await loop.create_datagram_endpoint(
            DatagramInbox, local_addr=("127.0.0.1", 0),
        )
        self.addCleanup(transport.close)
        return transport, inbox

    async def asyncSetUp(self):
        self.game_host, self.host_inbox = await self.udp()
        self.game_client, self.client_inbox = await self.udp()
        self.identity = make_server_identity()
        self.request = {
            "guest_id": "guest",
            "credential": secrets.token_urlsafe(24),
            "character_id": "guest-character",
            "protocol_version": 1,
            "game_build": "test-build",
        }
        self.disconnected = asyncio.Queue()
        self.application_handler = None

        async def on_request(guest_id, payload):
            if self.application_handler is None:
                raise ValueError("No handler installed for this test")
            return await self.application_handler(guest_id, payload)

        def admit(request):
            if request == self.request:
                return {"accepted": True, "character_id": request["character_id"]}
            if request == dict(self.request, catalog=True):
                return {"accepted": True, "game_access": False, "catalog_version": 1}
            return {"accepted": False, "reason": "unexpected_guest"}

        self.host = await start_host(
            "127.0.0.1", 0, *self.game_host.get_extra_info("sockname"),
            self.identity, admit, self.disconnected.put_nowait, on_request=on_request,
        )
        self.addAsyncCleanup(self.host.close)
        self.client = await start_client()
        self.addAsyncCleanup(self.client.close)

    async def connect_guest(self, request=None, identity=None, client=None):
        return await (client or self.client).connect(
            *self.host.address,
            (identity or self.identity).certificate_pem,
            request or self.request,
        )

    async def wait_for_host_release(self):
        async with asyncio.timeout(3):
            while self.request["guest_id"] in self.host._protocols:
                await asyncio.sleep(0.01)

    async def exchange(self, size):
        # Random data exposes fragment ordering and truncation in both directions.
        original = secrets.token_bytes(size)
        self.game_client.sendto(original, self.client.address)
        forwarded, endpoint = await self.host_inbox.next()
        self.assertEqual(forwarded, original)
        self.assertEqual(endpoint, self.host.guest_endpoint(self.request["guest_id"]))
        reply = secrets.token_bytes(size)
        self.game_host.sendto(reply, endpoint)
        returned, endpoint = await self.client_inbox.next()
        self.assertEqual(returned, reply)
        self.assertEqual(endpoint, self.client.address)

    async def test_link_exchanges_unreal_sized_and_fragmented_packets(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertTrue(self.client.connected)
        self.assertTrue(self.host.connected(self.request["guest_id"]))
        if os.name == "nt":
            address = self.client._protocol._transport.get_extra_info("sockname")
            verify_loopback_socket(os.getpid(), address[1])
        sizes = (1, 1024, 1200, 4097, 16_384)
        for size in sizes:
            await self.exchange(size)
        fragments = sum((size + link._FRAGMENT_SIZE - 1) // link._FRAGMENT_SIZE for size in sizes)
        for relay in (self.host, self.client):
            stats = relay.statistics()
            self.assertEqual(stats["schema_version"], 1)
            self.assertEqual(stats["game_packets_queued"], len(sizes))
            self.assertEqual(stats["game_packets_reassembled"], len(sizes))
            self.assertEqual(stats["game_packets_delivered"], len(sizes))
            self.assertEqual(stats["game_payload_bytes_queued"], sum(sizes))
            self.assertEqual(stats["game_payload_bytes_delivered"], sum(sizes))
            self.assertEqual(stats["game_fragments_queued"], fragments)
            self.assertEqual(stats["game_fragments_received"], fragments)
            self.assertGreater(stats["quic_datagrams_sent"], 0)
            self.assertGreater(stats["quic_datagrams_received"], 0)
            self.assertGreater(stats["quic_payload_bytes_sent"], sum(sizes))
            self.assertGreater(stats["quic_payload_bytes_received"], sum(sizes))
            self.assertEqual(stats["relay_queue_departed_frames"], fragments)
            self.assertGreaterEqual(stats["relay_queue_wait_total_ns"], stats["relay_queue_wait_max_ns"])
            self.assertEqual(stats["relay_queue_expired_frames"], 0)
            stats["game_packets_queued"] = 0
            self.assertEqual(relay.statistics()["game_packets_queued"], len(sizes))

    async def test_admitted_idle_link_has_a_20_hz_worker_heartbeat_without_unreal_frames(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        client_before = self.client.statistics()
        host_before = self.host.statistics()
        await asyncio.sleep(.62)
        client_after = self.client.statistics()
        host_after = self.host.statistics()
        queued = client_after["keepalive_probes_queued"] - client_before["keepalive_probes_queued"]
        acked = client_after["keepalive_probes_acked"] - client_before["keepalive_probes_acked"]
        self.assertGreaterEqual(queued, 10)
        self.assertLessEqual(queued, 14)
        self.assertGreaterEqual(acked, queued - 2)
        self.assertEqual(client_after["keepalive_ticks_congested"], 0)
        self.assertGreaterEqual(host_after["quic_datagrams_received"] - host_before["quic_datagrams_received"], acked)
        self.assertGreater(client_after["keepalive_rtt_us"], 0)
        self.assertEqual(client_after["game_packets_queued"], 0)
        self.assertEqual(host_after["game_packets_queued"], 0)

    async def test_game_udp_burst_reuses_the_next_turn_flush_and_keeps_order(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        protocol = self.client._protocol
        before = self.client.statistics()
        packets = [bytes([index + 1]) + bytes(47) for index in range(24)]
        first = None
        for packet in packets:
            protocol.forward_game_packet(packet)
            if first is None:
                first = protocol._transmit_task
            self.assertIs(protocol._transmit_task, first)
        self.assertIsNotNone(first)
        self.assertEqual(self.client.statistics()["quic_datagrams_sent"], before["quic_datagrams_sent"])

        await asyncio.sleep(0)
        self.assertIsNone(protocol._transmit_task)
        self.assertGreater(self.client.statistics()["quic_datagrams_sent"], before["quic_datagrams_sent"])
        self.assertEqual([(await self.host_inbox.next())[0] for _ in packets], packets)
        self.assertLess(self.client.statistics()["quic_datagrams_sent"] - before["quic_datagrams_sent"], len(packets))

        for packet in packets:
            self.game_client.sendto(packet[::-1], self.client.address)
        self.assertEqual([(await self.host_inbox.next())[0] for _ in packets], [packet[::-1] for packet in packets])
        after = self.client.statistics()
        self.assertEqual(after["relay_queue_evicted_frames"], before["relay_queue_evicted_frames"])
        self.assertEqual(after["relay_queue_expired_frames"], before["relay_queue_expired_frames"])

    async def test_relay_statistics_count_overflow_and_expiry(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        protocol = self.client._protocol
        payload = secrets.token_bytes(2 * link._FRAGMENT_SIZE)
        now = protocol._loop.time()
        with patch.object(protocol, "transmit"), patch.object(protocol._loop, "time", return_value=now):
            for _ in range(link._MAX_QUEUED_FRAMES // 2 + 1):
                protocol.forward_game_packet(payload)
            protocol._forward_presentation(link._WEAPON_SHOT_HEADER, b"full queue")
            stats = self.client.statistics()
            self.assertEqual(stats["game_fragments_queued"], link._MAX_QUEUED_FRAMES + 2)
            self.assertEqual(stats["relay_queue_max_frames"], link._MAX_QUEUED_FRAMES)
            self.assertEqual(stats["relay_queue_evicted_frames"], 2)
            self.assertEqual(stats["weapon_cues_rejected_full_queue"], 1)
            protocol._expire_game_queue(now + link._GAME_QUEUE_AGE + .001)
            self.assertEqual(self.client.statistics()["relay_queue_expired_frames"], link._MAX_QUEUED_FRAMES)
            self.assertFalse(protocol._quic._datagrams_pending)

    async def test_prop_pose_replaces_pending_pose_without_growing_the_queue(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        protocol = self.client._protocol
        pending = protocol._quic._datagrams_pending
        now = protocol._loop.time()
        with patch.object(protocol, "transmit"), patch.object(protocol._loop, "time", return_value=now):
            self.assertTrue(self.client.send_prop_pose(b"old"))
            for _ in range(link._MAX_QUEUED_FRAMES - 1):
                protocol.forward_game_packet(b"Unreal")
            unreal_frames = list(pending)[1:]
            self.assertTrue(self.client.send_prop_pose(b"latest"))
            self.assertEqual(list(pending), [link._PROP_POSE_HEADER + b"latest", *unreal_frames])
            protocol._expire_game_queue(now + link._GAME_QUEUE_AGE + .001)
            self.assertFalse(pending)
            for _ in range(link._MAX_QUEUED_FRAMES):
                protocol.forward_game_packet(b"Unreal")
            self.assertFalse(self.client.send_prop_pose(b"full queue"))
            self.assertEqual(len(pending), link._MAX_QUEUED_FRAMES)
            stats = self.client.statistics()
            self.assertEqual(stats["prop_pose_datagrams_queued"], 1)
            self.assertEqual(stats["prop_pose_datagrams_replaced"], 1)

    async def test_relay_expiry_keeps_new_packets_and_replacement_poses(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        protocol = self.client._protocol
        pending = protocol._quic._datagrams_pending
        now = protocol._loop.time()
        with patch.object(protocol, "transmit"), patch.object(protocol._loop, "time", return_value=now) as clock:
            self.assertTrue(self.client.send_prop_pose(b"old"))
            protocol.forward_game_packet(secrets.token_bytes(link._FRAGMENT_SIZE + 1))
            clock.return_value = now + link._GAME_QUEUE_AGE / 2
            self.assertTrue(self.client.send_prop_pose(b"fresh"))
            packet_id = protocol._next_packet_id
            update = b"new Unreal packet"
            protocol.forward_game_packet(update)

            protocol._expire_game_queue(now + link._GAME_QUEUE_AGE + .001)
            self.assertEqual(list(pending), [
                link._PROP_POSE_HEADER + b"fresh",
                link._FRAGMENT_HEADER.pack(packet_id, len(update), 0) + update,
            ])
            self.assertEqual(self.client.statistics()["relay_queue_expired_frames"], 2)
            protocol._expire_game_queue(clock.return_value + link._GAME_QUEUE_AGE + .001)
            self.assertFalse(pending)
            self.assertEqual(self.client.statistics()["relay_queue_expired_frames"], 4)

    async def test_relay_wait_tracks_frames_that_depart_in_separate_batches(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        protocol = self.client._protocol
        pending = protocol._quic._datagrams_pending
        now = protocol._loop.time()
        with patch.object(protocol, "transmit"), patch.object(protocol._loop, "time", return_value=now):
            protocol.forward_game_packet(b"old")
            protocol.forward_game_packet(b"new")

        with patch.object(link.QuicConnectionProtocol, "transmit", side_effect=pending.popleft), \
                patch.object(protocol._loop, "time", side_effect=(now + .03, now + .07)):
            protocol.transmit()
            self.assertEqual(len(pending), 1)
            protocol.transmit()

        stats = self.client.statistics()
        self.assertFalse(pending)
        self.assertEqual(stats["relay_queue_departed_frames"], 2)
        self.assertAlmostEqual(stats["relay_queue_wait_total_ns"], 100_000_000, delta=10)
        self.assertAlmostEqual(stats["relay_queue_wait_max_ns"], 70_000_000, delta=10)

    async def test_avatar_poses_coalesce_independently_from_prop_and_unreal_frames(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        guest_id = self.request["guest_id"]
        upstream, downstream = self.client._protocol, self.host._protocols[guest_id]
        peers = ((upstream, self.client.send_avatar_pose),
                 (downstream, lambda packet: self.host.send_avatar_pose(packet, lambda current: current == guest_id)))
        with patch.object(upstream._loop, "time", return_value=upstream._loop.time()):
            for protocol, send in peers:
                with self.subTest(seat=type(protocol).__name__), patch.object(protocol, "transmit"):
                    send(b"old body pose")
                    protocol._forward_presentation(link._PROP_POSE_HEADER, b"grip pose")
                    for _ in range(link._MAX_QUEUED_FRAMES - 2):
                        protocol.forward_game_packet(b"Unreal")
                    pending = protocol._quic._datagrams_pending
                    unrelated = list(pending)[1:]
                    send(b"latest body pose")
                    self.assertEqual(list(pending), [link._AVATAR_POSE_HEADER + b"latest body pose", *unrelated])
        for seat in (self.client, self.host):
            stats = seat.statistics()
            self.assertEqual(stats["avatar_pose_datagrams_queued"], 1)
            self.assertEqual(stats["avatar_pose_datagrams_replaced"], 1)

    def test_reassembly_expires_older_partial_packet_and_preserves_live_packet(self):
        stats = link._TransportStatistics()
        assembler = link._Reassembler(stats)
        head = b"x" * link._FRAGMENT_SIZE
        size = len(head) + 1
        self.assertIsNone(assembler.receive(link._FRAGMENT_HEADER.pack(10, size, 0) + head, 0.0))
        self.assertIsNone(assembler.receive(link._FRAGMENT_HEADER.pack(11, size, 0) + head, 0.5))
        self.assertEqual(assembler.receive(link._FRAGMENT_HEADER.pack(11, size, 1) + b"y", 1.25), head + b"y")
        self.assertIsNone(assembler.receive(link._FRAGMENT_HEADER.pack(12, size, 1) + b"yz", 1.25))
        self.assertEqual(stats.reassembly_expired_packets, 1)
        self.assertEqual(stats.reassembly_max_pending_packets, 2)
        self.assertEqual(stats.game_packets_reassembled, 1)
        self.assertEqual(stats.game_fragments_received, 4)
        self.assertEqual(stats.game_fragments_rejected, 1)

    async def test_compressible_unreal_datagrams_cross_the_relay_without_changing_payload(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        payload = b"repeated Unreal actor state" * 100 + secrets.token_bytes(1300)
        self.game_client.sendto(payload, self.client.address)
        received, address = await self.host_inbox.next()
        self.assertEqual(received, payload)
        self.game_host.sendto(payload, address)
        self.assertEqual((await self.client_inbox.next())[0], payload)
        for relay in (self.host, self.client):
            stats = relay.statistics()
            self.assertEqual(stats["game_packets_compressed"], 1)
            self.assertEqual(stats["game_payload_bytes_queued"], len(payload))
            self.assertLess(stats["game_wire_payload_bytes_queued"], len(payload) // 2)

    def test_compressed_game_datagrams_require_one_bounded_complete_stream(self):
        stats = link._TransportStatistics()
        receiver = link._Reassembler(stats)
        compressed = zlib.compress(b"a" * (link._MAX_GAME_PACKET + 1))
        for data in (compressed, zlib.compress(b"valid") + b"extra", b"not compressed"):
            frame = link._FRAGMENT_HEADER.pack(1, len(data), link._COMPRESSED_FRAGMENT) + data
            self.assertIsNone(receiver.receive(frame, time.monotonic()))
        self.assertEqual(stats.game_packets_reassembled, 0)
        self.assertEqual(stats.game_fragments_rejected, 3)

    @unittest.skipUnless(os.name == "nt", "Windows UDP ownership tables are required")
    async def test_resident_client_refuses_wildcard_game_socket_before_pinning_peer(self):
        client = await start_client(game_pid=os.getpid())
        self.addAsyncCleanup(client.close)
        self.assertTrue((await self.connect_guest(client=client))["accepted"])
        wildcard, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            DatagramInbox, local_addr=("0.0.0.0", 0))
        self.addCleanup(wildcard.close)
        wildcard.sendto(b"wildcard socket", client.address)
        async with asyncio.timeout(3):
            while client.game_local_datagrams == 0:
                await asyncio.sleep(.01)
        self.assertIsNone(client.unreal_local_port)
        self.assertEqual(client.game_packets_sent, 0)

        self.game_client.sendto(b"qualified socket", client.address)
        received, _ = await self.host_inbox.next()
        self.assertEqual(received, b"qualified socket")
        self.assertEqual(client.unreal_local_port, self.game_client.get_extra_info("sockname")[1])

    async def test_reconnect_preserves_both_unreal_facing_endpoints(self):
        guest = self.request["guest_id"]
        self.request["runtime_id"] = self.client.runtime_id
        self.assertTrue((await self.connect_guest())["accepted"])
        host_endpoint = self.host.guest_endpoint(guest)
        client_endpoint = self.client.address
        await self.exchange(1200)

        await self.client.disconnect()
        self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), guest)
        self.assertFalse(self.client.connected)
        self.assertFalse(self.host.connected(guest))
        self.game_client.sendto(b"detached", self.client.address)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.host_inbox.messages.get(), 0.15)

        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertEqual(self.host.guest_endpoint(guest), host_endpoint)
        self.assertEqual(self.host.guest_runtime_id(guest), self.client.runtime_id)
        self.assertEqual(self.client.address, client_endpoint)
        self.assertTrue(self.disconnected.empty())
        await self.exchange(1200)

    async def test_fresh_runtime_cannot_use_old_relay_and_rotates_only_on_explicit_admission(self):
        guest = self.request["guest_id"]
        original_request = dict(self.request, runtime_id=self.client.runtime_id)
        fresh = await start_client()
        self.addAsyncCleanup(fresh.close)
        fresh_request = dict(self.request, runtime_id=fresh.runtime_id)
        self.assertNotEqual(fresh.runtime_id, self.client.runtime_id)
        retired = False

        def admit(request):
            if request == original_request:
                return {"accepted": True}
            if request == fresh_request:
                return {"accepted": True, "rotate_game_route": retired}
            return {"accepted": False, "reason": "unexpected_guest"}

        with patch.object(self.host, "_admit_callback", admit):
            self.assertTrue((await self.connect_guest(original_request))["accepted"])
            old_endpoint = self.host.guest_endpoint(guest)
            await self.exchange(1200)
            await self.client.disconnect()
            self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), guest)

            self.assertEqual(await self.connect_guest(fresh_request, client=fresh), {
                "accepted": False, "reason": "native_runtime_retiring",
            })
            self.assertFalse(fresh.connected)
            self.assertFalse(self.host.connected(guest))
            self.assertEqual(self.host.guest_endpoint(guest), old_endpoint)
            self.assertEqual(self.host.guest_runtime_id(guest), self.client.runtime_id)
            self.game_client.sendto(b"parked guest packet", fresh.address)
            self.game_host.sendto(b"parked host packet", old_endpoint)
            for inbox in (self.host_inbox, self.client_inbox):
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(inbox.messages.get(), 0.15)

            retired = True
            self.assertEqual(await self.connect_guest(fresh_request, client=fresh), {"accepted": True})
            new_endpoint = self.host.guest_endpoint(guest)
            self.assertNotEqual(new_endpoint, old_endpoint)
            self.assertEqual(self.host.guest_runtime_id(guest), fresh.runtime_id)
            self.game_client.sendto(b"new runtime packet", fresh.address)
            self.assertEqual(await self.host_inbox.next(), (b"new runtime packet", new_endpoint))
            self.game_host.sendto(b"stale old route packet", old_endpoint)
            self.game_host.sendto(b"new route reply", new_endpoint)
            self.assertEqual(await self.client_inbox.next(), (b"new route reply", fresh.address))
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(self.client_inbox.messages.get(), 0.15)

    async def test_failed_authorized_rotation_retires_old_route_and_allows_retry(self):
        guest = self.request["guest_id"]
        original_request = dict(self.request, runtime_id=self.client.runtime_id)
        fresh = await start_client()
        self.addAsyncCleanup(fresh.close)
        fresh_request = dict(self.request, runtime_id=fresh.runtime_id)

        def admit(request):
            if request == original_request:
                return {"accepted": True}
            if request == fresh_request:
                return {"accepted": True, "rotate_game_route": True}
            return {"accepted": False, "reason": "unexpected_guest"}

        with patch.object(self.host, "_admit_callback", admit):
            self.assertTrue((await self.connect_guest(original_request))["accepted"])
            prior = self.host._guest_routes[guest]
            await self.client.disconnect()
            self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), guest)
            self.assertFalse(prior.transport.is_closing())

            with (patch.object(self.host, "_create_game_route", side_effect=OSError("relay bind failed")) as opener,
                  self.assertLogs(link.__name__, level="WARNING")):
                self.assertEqual(await self.connect_guest(fresh_request, client=fresh), {
                    "accepted": False, "reason": "admission_failed",
                })
            opener.assert_awaited_once_with(guest)
            self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), guest)
            self.assertFalse(fresh.connected)
            self.assertFalse(self.host.connected(guest))
            self.assertTrue(prior.transport.is_closing())
            self.assertIsNone(self.host.guest_endpoint(guest))
            self.assertIsNone(self.host.guest_runtime_id(guest))

            self.assertEqual(await self.connect_guest(fresh_request, client=fresh), {"accepted": True})
            self.assertEqual(self.host.guest_runtime_id(guest), fresh.runtime_id)
            self.game_client.sendto(b"retry after relay bind failure", fresh.address)
            self.assertEqual(await self.host_inbox.next(), (
                b"retry after relay bind failure", self.host.guest_endpoint(guest),
            ))

    async def test_travel_hold_drops_game_packets_keeps_control_and_releases_same_route(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        guest = self.request["guest_id"]
        await self.exchange(1024)
        host_endpoint, client_endpoint = self.host.guest_endpoint(guest), self.client.address
        self.host.restrict_game_routes({guest})

        async def identify(guest_id, payload):
            return {"guest": guest_id, "echo": payload["echo"]}

        self.application_handler = identify
        with self.host.hold_game_routes():
            self.game_client.sendto(b"old client world", client_endpoint)
            self.game_host.sendto(b"old host world", host_endpoint)
            self.assertEqual(await self.client.request({"echo": "travelling"}), {"guest": guest, "echo": "travelling"})
            for inbox in (self.host_inbox, self.client_inbox):
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(inbox.messages.get(), .15)
        self.assertTrue(self.host.connected(guest))
        self.assertEqual((self.host.guest_endpoint(guest), self.client.address), (host_endpoint, client_endpoint))
        await self.exchange(4097)

    async def test_client_pins_game_socket_until_explicit_reset(self):
        alternate, alternate_inbox = await self.udp()
        self.assertTrue((await self.connect_guest())["accepted"])
        await self.exchange(1200)
        alternate.sendto(b"wrong sender", self.client.address)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.host_inbox.messages.get(), 0.15)

        self.client.reset_game_endpoint()
        alternate.sendto(b"new socket", self.client.address)
        packet, endpoint = await self.host_inbox.next()
        self.assertEqual(packet, b"new socket")
        self.game_host.sendto(b"reply", endpoint)
        self.assertEqual(await alternate_inbox.next(), (b"reply", self.client.address))

    async def test_host_releases_only_disconnected_guest_endpoint(self):
        guest = self.request["guest_id"]
        self.assertTrue((await self.connect_guest())["accepted"])
        endpoint = self.host.guest_endpoint(guest)
        self.assertFalse(await self.host.release_guest(guest))
        self.assertEqual(self.host.guest_endpoint(guest), endpoint)

        await self.client.disconnect()
        self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), guest)
        self.assertTrue(await self.host.release_guest(guest))
        self.assertIsNone(self.host.guest_endpoint(guest))
        self.assertFalse(await self.host.release_guest(guest))
        self.assertTrue((await self.connect_guest())["accepted"])
        await self.exchange(1200)

    async def test_denied_guest_cannot_relay_and_does_not_take_slot(self):
        denied = dict(self.request, credential=secrets.token_urlsafe(24))
        self.assertEqual(await self.connect_guest(denied), {
            "accepted": False, "reason": "unexpected_guest",
        })
        self.assertFalse(self.client.connected)
        self.assertIsNone(self.host.guest_endpoint(self.request["guest_id"]))
        self.game_client.sendto(b"unadmitted", self.client.address)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.host_inbox.messages.get(), 0.15)
        self.assertTrue((await self.connect_guest())["accepted"])

    async def test_second_link_cannot_replace_a_live_guest(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        duplicate = await start_client()
        self.addAsyncCleanup(duplicate.close)
        self.assertEqual(await self.connect_guest(client=duplicate), {
            "accepted": False, "reason": "guest_already_connected",
        })
        self.assertFalse(duplicate.connected)
        self.assertTrue(self.client.connected)
        self.assertTrue(self.disconnected.empty())

    async def host_for_guests(self, *requests, max_game_guests=2):
        credentials = {request["guest_id"]: request for request in requests}

        def admit(request):
            expected = credentials.get(request["guest_id"])
            if request == expected:
                return {"accepted": True, "character_id": request["character_id"]}
            if expected and request == dict(expected, catalog=True):
                return {"accepted": True, "game_access": False, "catalog_version": 1}
            return {"accepted": False, "reason": "unexpected_guest"}

        async def identify(guest_id, payload):
            return {"authenticated_guest": guest_id, "claimed_guest": payload["guest_id"]}

        self.host = await start_host(
            "127.0.0.1", 0, *self.game_host.get_extra_info("sockname"),
            self.identity, admit, self.disconnected.put_nowait, on_request=identify,
            max_game_guests=max_game_guests,
        )
        self.addAsyncCleanup(self.host.close)

    def guest_request(self, guest_id):
        return dict(self.request, guest_id=guest_id, character_id=f"{guest_id}-character",
                    credential=secrets.token_urlsafe(24))

    async def test_opt_in_guests_have_independent_routes_actions_and_reconnect(self):
        second_request, third_request = self.guest_request("second"), self.guest_request("third")
        first, second, third = (request["guest_id"] for request in (self.request, second_request, third_request))
        await self.host_for_guests(self.request, second_request, third_request)
        second_client, third_client = await start_client(), await start_client()
        self.addAsyncCleanup(second_client.close)
        self.addAsyncCleanup(third_client.close)
        second_socket, second_inbox = await self.udp()

        responses = await asyncio.gather(self.connect_guest(), self.connect_guest(second_request, client=second_client))
        self.assertTrue(all(response["accepted"] for response in responses))
        self.assertEqual(await self.connect_guest(client=third_client), {
            "accepted": False, "reason": "guest_already_connected",
        })
        self.assertEqual(await self.connect_guest(third_request, client=third_client), {
            "accepted": False, "reason": "party_full",
        })
        self.assertIsNone(self.host.guest_endpoint(third))

        async def exchange_both():
            self.game_client.sendto(b"first-to-host", self.client.address)
            second_socket.sendto(b"second-to-host", second_client.address)
            self.assertEqual(dict([await self.host_inbox.next(), await self.host_inbox.next()]), {
                b"first-to-host": self.host.guest_endpoint(first),
                b"second-to-host": self.host.guest_endpoint(second),
            })
            self.game_host.sendto(b"host-to-first", self.host.guest_endpoint(first))
            self.game_host.sendto(b"host-to-second", self.host.guest_endpoint(second))
            self.assertEqual(await self.client_inbox.next(), (b"host-to-first", self.client.address))
            self.assertEqual(await second_inbox.next(), (b"host-to-second", second_client.address))

        await exchange_both()
        self.assertNotEqual(self.host.guest_endpoint(first), self.host.guest_endpoint(second))
        self.assertEqual(await asyncio.gather(self.client.request({"guest_id": second}),
                                              second_client.request({"guest_id": first})), [
            {"authenticated_guest": first, "claimed_guest": second},
            {"authenticated_guest": second, "claimed_guest": first},
        ])

        previous_routes = {guest: self.host.guest_endpoint(guest) for guest in (first, second)}
        create_route = self.host._create_game_route
        attempted_routes = []

        async def fail_after_one(guest):
            if attempted_routes:
                raise OSError("fixture could not allocate a second relay")
            route = await create_route(guest)
            attempted_routes.append(route)
            return route

        with patch.object(self.host, "_create_game_route", new=fail_after_one):
            with self.assertRaisesRegex(LinkError, "could not refresh the Unreal relay route"):
                await self.host.refresh_game_routes()
        self.assertEqual(len(attempted_routes), 1)
        self.assertTrue(attempted_routes[0].is_closing())
        self.assertEqual({guest: self.host.guest_endpoint(guest) for guest in (first, second)}, previous_routes)
        await exchange_both()

        await self.host.refresh_game_routes()
        refreshed_routes = {guest: self.host.guest_endpoint(guest) for guest in (first, second)}
        self.assertFalse(set(refreshed_routes.values()) & set(previous_routes.values()))
        await exchange_both()
        await self.client.disconnect()
        self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), first)
        self.assertTrue(self.host.connected(second))
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertEqual(self.host.guest_endpoint(first), refreshed_routes[first])
        await exchange_both()

        await self.client.disconnect()
        self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), first)
        self.assertTrue(await self.host.release_guest(first))
        self.assertTrue((await self.connect_guest(third_request, client=third_client))["accepted"])
        self.assertTrue(self.host.connected(second))
        self.assertTrue(self.host.connected(third))
        self.assertEqual(self.host.guest_endpoint(second), refreshed_routes[second])
        self.assertTrue(self.disconnected.empty())

    async def test_catalog_capacity_is_separate_from_default_game_capacity(self):
        second_request, third_request = self.guest_request("second"), self.guest_request("third")
        await self.host_for_guests(self.request, second_request, third_request, max_game_guests=1)
        second_client, third_client = await start_client(), await start_client()
        self.addAsyncCleanup(second_client.close)
        self.addAsyncCleanup(third_client.close)
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertEqual(await self.connect_guest(second_request, client=second_client), {
            "accepted": False, "reason": "guest_already_connected",
        })

        with patch("tools.silverfish.link._MAX_CATALOG_LINKS", 1):
            self.assertEqual(await self.connect_guest(dict(second_request, catalog=True), client=second_client), {
                "accepted": True, "game_access": False, "catalog_version": 1,
            })
            self.assertEqual(await self.connect_guest(dict(third_request, catalog=True), client=third_client), {
                "accepted": False, "reason": "catalog_capacity",
            })
        self.assertIsNone(self.host.guest_endpoint(second_request["guest_id"]))
        self.assertIsNone(self.host.guest_endpoint(third_request["guest_id"]))
        await self.exchange(1200)
        self.assertTrue(self.disconnected.empty())

    async def test_wrong_certificate_blocks_admission(self):
        with self.assertRaisesRegex(LinkError, "handshake failed"):
            await self.connect_guest(identity=make_server_identity())
        self.assertFalse(self.client.connected)
        self.assertIsNone(self.host.guest_endpoint(self.request["guest_id"]))

    async def test_application_requests_use_admitted_identity_and_preserve_datagrams(self):
        guest_id = self.request["guest_id"]
        actions = []

        async def on_request(admitted_id, payload):
            actions.append((admitted_id, payload))
            return {"ok": True, "body": payload["body"]}

        self.application_handler = on_request
        with self.assertRaisesRegex(LinkError, "not admitted"):
            await self.client.request({"body": "pre-admission"})
        self.assertEqual(actions, [])
        self.assertTrue((await self.connect_guest())["accepted"])

        payload = {"guest_id": "misreported", "body": "x" * 30_000}
        response, _ = await asyncio.gather(self.client.request(payload), self.exchange(1200))
        self.assertEqual(response, {"ok": True, "body": payload["body"]})
        self.assertEqual(actions, [(guest_id, payload)])

        await self.client.disconnect()
        self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), guest_id)
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertEqual(await self.client.request({"body": "rejoined"}), {
            "ok": True, "body": "rejoined",
        })

    async def test_small_interaction_prioritizes_both_directions_and_drains_queued_game_packets(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        await self.exchange(64)
        guest_protocol = self.client._protocol
        host_protocol = self.host._protocols[self.request["guest_id"]]
        guest_packets = [secrets.token_bytes(link._FRAGMENT_SIZE) for _ in range(4)]
        host_packets = [secrets.token_bytes(link._FRAGMENT_SIZE) for _ in range(4)]

        def queue_without_sending(protocol, packets):
            with patch.object(protocol, "transmit"):
                for packet in packets:
                    protocol.forward_game_packet(packet)

        async def open_door(_, payload):
            self.assertEqual(payload, {"action": "open-door"})
            queue_without_sending(host_protocol, host_packets)
            return {"ok": True, "opened": True}

        self.application_handler = open_door
        queue_without_sending(guest_protocol, guest_packets)

        def immediate_send(protocol):
            send = protocol._send_action_stream

            def assert_immediate(*args, **kwargs):
                sent = protocol._stats.quic_datagrams_sent
                send(*args, **kwargs)
                self.assertGreater(protocol._stats.quic_datagrams_sent, sent)

            return assert_immediate

        with (patch.object(guest_protocol, "_send_action_stream", side_effect=immediate_send(guest_protocol)),
              patch.object(host_protocol, "_send_action_stream", side_effect=immediate_send(host_protocol))):
            self.assertEqual(await self.client.request({"action": "open-door"}), {"ok": True, "opened": True})
        self.assertGreater(self.client.statistics()["interaction_priority_passes"], 0)
        self.assertGreater(self.host.statistics()["interaction_priority_passes"], 0)
        self.assertCountEqual([(await self.host_inbox.next())[0] for _ in guest_packets], guest_packets)
        self.assertCountEqual([(await self.client_inbox.next())[0] for _ in host_packets], host_packets)
        for protocol in (guest_protocol, host_protocol):
            self.assertFalse(protocol._quic._datagrams_pending)
            self.assertFalse(protocol._game_enqueued)
            self.assertEqual(protocol._stats.relay_queue_expired_frames, 0)
            self.assertEqual(protocol._stats.relay_queue_evicted_frames, 0)

    async def test_small_snapshot_responses_leave_queued_game_packets_eligible(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        await self.exchange(64)
        host_protocol = self.host._protocols[self.request["guest_id"]]
        packets = [b"Unreal movement " + bytes([index]) for index in range(4)]

        for world, compressed in (("short", False), ("repeated world snapshot state " * 150, True)):
            with self.subTest(compressed=compressed):
                result = {"ok": True, "gameplay": {"world": world}}
                wire = link._encode_response(result, snapshot=True)
                self.assertLessEqual(len(wire), link._FRAGMENT_SIZE)
                self.assertEqual(wire.startswith(link._COMPRESSED_SNAPSHOT), compressed)

                async def reply(_, request):
                    self.assertEqual(request, {"action": "snapshot"})
                    for packet in packets:
                        host_protocol.forward_game_packet(packet)
                    return result

                self.application_handler = reply
                before = self.host.statistics()["interaction_priority_passes"]
                self.assertEqual(await self.client.request({"action": "snapshot"}), result)
                self.assertCountEqual([(await self.client_inbox.next())[0] for _ in packets], packets)
                self.assertEqual(self.host.statistics()["interaction_priority_passes"], before)
                self.assertFalse(host_protocol._quic._datagrams_pending)

    async def test_flow_blocked_interaction_leaves_relay_datagrams_eligible(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        protocol = self.client._protocol
        quic = protocol._quic
        stream_id = quic.get_next_available_stream_id()
        with patch.object(protocol, "transmit"):
            protocol.forward_game_packet(b"pending game packet")
            protocol._send_action_stream(stream_id, b"pending interaction")

        pending = quic._datagrams_pending
        self.assertEqual(protocol._ready_interactions(), {stream_id})
        with patch.object(quic, "_remote_max_data", quic._remote_max_data_used), \
                patch.object(link.QuicConnectionProtocol, "transmit", side_effect=pending.popleft):
            protocol.transmit()
        self.assertFalse(pending)
        self.assertFalse(protocol._game_enqueued)
        self.assertEqual(protocol._stats.interaction_priority_passes, 0)

    async def test_large_world_snapshot_is_compressed_and_game_datagrams_stay_independent(self):
        world = {"ok": True, "gameplay": {"loot_sources": [
            f"/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P:PersistentLevel.LootCrate_Small_BP_C_{index}"
            for index in range(700)
        ]}}

        async def snapshot(_, request):
            self.assertEqual(request, {"action": "snapshot"})
            return world

        self.application_handler = snapshot
        self.assertGreater(len(link._encode_action(world, limit=100_000)), link._MAX_ACTION_BODY)
        self.assertLess(len(link._encode_response(world, snapshot=True)), 4_096)
        self.assertTrue((await self.connect_guest())["accepted"])
        response, _ = await asyncio.gather(self.client.request({"action": "snapshot"}), self.exchange(1200))
        self.assertEqual(response, world)
        host_stats = self.host.statistics()
        client_stats = self.client.statistics()
        self.assertEqual(host_stats["snapshot_responses_encoded"], 1)
        self.assertEqual(host_stats["snapshot_responses_compressed"], 1)
        self.assertGreater(host_stats["snapshot_plaintext_bytes"], link._MAX_ACTION_BODY)
        self.assertLess(host_stats["snapshot_encoded_bytes"], 4_096)
        self.assertEqual(client_stats["snapshot_responses_decoded"], 1)
        self.assertEqual(client_stats["snapshot_received_bytes"], host_stats["snapshot_encoded_bytes"])
        self.assertGreater(host_stats["snapshot_encode_total_ns"], 0)
        self.assertGreater(client_stats["snapshot_decode_total_ns"], 0)

    async def test_large_mounted_world_baseline_uses_the_guest_window_and_keeps_other_actions_working(self):
        older_request = self.guest_request("older")
        await self.host_for_guests(self.request, older_request)
        self.assertTrue((await self.connect_guest())["accepted"])
        older = await start_client()
        self.addAsyncCleanup(older.close)
        configuration = link._configuration

        def legacy_window(is_client):
            options = configuration(is_client)
            if is_client:
                options.max_stream_data = link._MAX_ACTION_FRAME
                options.max_data = link._MAX_ADMISSION + link._MAX_ACTION_FRAME * link._MAX_ACTIONS
            return options

        with patch.object(link, "_configuration", legacy_window):
            self.assertTrue((await self.connect_guest(older_request, client=older))["accepted"])

        rng = Random(80)

        def slug(length):
            return "".join(rng.choices(ascii_letters + digits, k=length))

        items = {}
        for index in range(96):
            asset = "Item_" + slug(31)
            actor = "/Game/Maps/Depot_P.Depot_P:PersistentLevel.Placed_" + slug(113) + f"_C_{index}"
            items[actor] = {"quest": "Quest_" + slug(88), "class_path": f"/Mounted/{slug(78)}/{slug(80)}/{asset}.{asset}_C",
                            "transform": {"position": [rng.uniform(-25000, 25000) for _ in range(3)],
                                          "rotation": [0, index * 11 % 360, 0], "scale": [1, 1, 1]}}
        world = {"ok": True, "gameplay": {"native_authored_quest_placements": {"items": items}}}
        size = len(link._encode_response(world, snapshot=True))
        self.assertGreater(size, link._MAX_ACTION_FRAME)
        self.assertLess(size, link._MAX_SNAPSHOT_FRAME)

        self.host._request_callback = lambda guest, payload: (
            world if payload["action"] == "snapshot" else {"ok": True, "guest": guest})
        replies = await asyncio.gather(
            self.client.request({"action": "snapshot"}),
            older.request({"action": "snapshot"}),
            self.client.request({"action": "interact"}),
            older.request({"action": "interact"}),
            self.exchange(1200),
            return_exceptions=True,
        )
        self.assertEqual(replies[0], world)
        self.assertIsInstance(replies[1], LinkError)
        self.assertEqual(replies[2:5], [{"ok": True, "guest": self.request["guest_id"]},
                                        {"ok": True, "guest": older_request["guest_id"]}, None])
        self.assertEqual(self.client.statistics()["snapshot_received_bytes"], size)
        self.assertEqual(self.host.statistics()["snapshot_responses_oversized"], 1)
        self.assertTrue(self.client.connected and older.connected)

    async def test_repeated_empty_fin_does_not_redispatch_a_completed_stream(self):
        self.application_handler = AsyncMock(return_value={"ok": True})
        self.assertTrue((await self.connect_guest())["accepted"])
        server = self.host._protocols[self.request["guest_id"]]
        client = self.client._protocol
        stream = client._quic.get_next_available_stream_id()
        self.assertEqual(await self.client.request({"action": "snapshot"}), {"ok": True})
        self.assertNotIn(stream, server._actions)

        repeat = StreamDataReceived(data=b"", end_stream=True, stream_id=stream)
        with patch.object(self.host, "_spawn", wraps=self.host._spawn) as spawn:
            server.quic_event_received(repeat)
            spawn.assert_not_called()
        self.application_handler.assert_awaited_once_with(self.request["guest_id"], {"action": "snapshot"})

    async def test_cancelled_and_timed_out_snapshots_stop_their_response_streams(self):
        snapshots = asyncio.Queue()
        world = {"gameplay": {"rows": [f"actor-{index}" for index in range(3500)]}}

        async def reply(_, payload):
            if payload == {"action": "snapshot"}:
                release = asyncio.Event()
                snapshots.put_nowait(release)
                await release.wait()
                return world
            return {"ok": True}

        self.application_handler = reply
        self.assertTrue((await self.connect_guest())["accepted"])
        server = self.host._protocols[self.request["guest_id"]]
        client = self.client._protocol
        for cancel in (True, False):
            with self.subTest(cancel=cancel), self.assertNoLogs("tools.silverfish.link", level="WARNING"):
                stream = client._quic.get_next_available_stream_id()
                request = asyncio.create_task(self.client.request({"action": "snapshot"}, timeout=.25))
                release = await asyncio.wait_for(snapshots.get(), 1)
                try:
                    if cancel:
                        request.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await request
                    else:
                        with self.assertRaisesRegex(LinkError, "application request timed out"):
                            await request
                    async with asyncio.timeout(1):
                        while not server._actions[stream].abandoned:
                            await asyncio.sleep(.01)
                finally:
                    release.set()
                async with asyncio.timeout(1):
                    while stream in server._actions:
                        await asyncio.sleep(.01)
                self.assertEqual(self.host.statistics()["snapshot_responses_encoded"], 0)
                self.assertEqual(await self.client.request({"action": "interact"}), {"ok": True})
                await self.exchange(1200)

        # aioquic can emit STOP_SENDING before the initial STREAM frame when
        # cancellation happens while both are waiting for their first send.
        with patch.object(client, "transmit"):
            request = asyncio.create_task(self.client.request({"action": "snapshot"}, timeout=1))
            await asyncio.sleep(0)
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request
        self.assertEqual(await self.client.request({"action": "interact"}), {"ok": True})
        self.assertEqual(self.host.statistics()["snapshot_responses_encoded"], 0)
        self.assertTrue(snapshots.empty())

    async def test_repeated_admission_fin_leaves_both_seats_connected(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        server = self.host._protocols[self.request["guest_id"]]
        client = self.client._protocol
        repeat = StreamDataReceived(data=b"", end_stream=True, stream_id=link._ADMISSION_STREAM)
        for protocol in (server, client):
            with patch.object(protocol, "close", wraps=protocol.close) as close:
                protocol.quic_event_received(repeat)
                close.assert_not_called()
        await self.exchange(1200)

    async def test_gameplay_change_notice_follows_the_authorized_route_and_current_visit(self):
        guest_id = self.request["guest_id"]
        self.assertTrue((await self.connect_guest())["accepted"])
        visit = "a" * 32
        arrived = asyncio.Event()
        self.client.set_gameplay_change_handler(arrived.set)
        with self.host.hold_game_routes():
            self.host.send_gameplay_change(visit)
        self.assertFalse(self.client.gameplay_changed(visit))
        self.assertFalse(arrived.is_set())

        self.host.restrict_game_routes({guest_id})
        self.host.send_gameplay_change(visit)
        await asyncio.wait_for(arrived.wait(), 1)
        self.assertFalse(self.client.gameplay_changed("b" * 32))
        self.assertTrue(self.client.gameplay_changed(visit))
        self.client.consume_gameplay_change()
        self.assertFalse(self.client.gameplay_changed(visit))
        await self.exchange(1200)

    async def test_prop_pose_reaches_only_the_current_authenticated_game_route(self):
        guest_id = self.request["guest_id"]
        received = asyncio.Queue()
        self.host.set_prop_pose_handler(lambda guest, data: received.put_nowait((guest, data)))
        self.assertFalse(self.client.send_prop_pose(b"pre-admission"))
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertFalse(self.client.send_prop_pose(b""))
        self.assertFalse(self.client.send_prop_pose(bytes(link._FRAGMENT_SIZE + 1)))
        self.assertFalse(self.client.send_prop_pose("not bytes"))
        self.assertTrue(self.client.send_prop_pose(b"first grip"))
        self.assertEqual(await asyncio.wait_for(received.get(), 3), (guest_id, b"first grip"))
        self.assertTrue(self.host_inbox.messages.empty())

        dispatched = asyncio.Queue()
        route_pose = self.host._receive_prop_pose

        def observe(protocol, data):
            route_pose(protocol, data)
            dispatched.put_nowait(data)

        with patch.object(self.host, "_receive_prop_pose", side_effect=observe):
            with self.host.hold_game_routes():
                self.assertTrue(self.client.send_prop_pose(b"during travel"))
                self.assertEqual(await asyncio.wait_for(dispatched.get(), 3), b"during travel")
            self.host.restrict_game_routes(set())
            self.assertTrue(self.client.send_prop_pose(b"restricted"))
            self.assertEqual(await asyncio.wait_for(dispatched.get(), 3), b"restricted")
            self.host.restrict_game_routes(None)
        self.assertTrue(received.empty())
        self.assertTrue(self.host_inbox.messages.empty())

        old = self.host._protocols[guest_id]
        await self.client.disconnect()
        await asyncio.wait_for(self.disconnected.get(), 3)
        self.assertTrue((await self.connect_guest())["accepted"])
        with (patch.object(old, "authorized", True), patch.object(old, "game_access", True),
              patch.object(old, "terminated", False)):
            old.quic_event_received(DatagramFrameReceived(link._PROP_POSE_HEADER + b"stale connection"))
        current = self.host._protocols[guest_id]
        for bad_data in (b"", bytes(link._FRAGMENT_SIZE + 1)):
            current.quic_event_received(DatagramFrameReceived(link._PROP_POSE_HEADER + bad_data))
        self.assertTrue(received.empty())
        self.assertTrue(self.client.send_prop_pose(b"next grip"))
        self.assertEqual(await asyncio.wait_for(received.get(), 3), (guest_id, b"next grip"))
        self.assertTrue(received.empty())
        self.assertTrue(self.host_inbox.messages.empty())
        self.assertEqual(self.host.statistics()["prop_pose_datagrams_received"], 2)
        await self.exchange(1200)

    async def test_avatar_poses_reach_only_fresh_authenticated_game_connections(self):
        guest_id = self.request["guest_id"]
        inbound = asyncio.Queue()
        self.host.set_avatar_pose_handler(lambda guest, data: inbound.put_nowait((guest, data)))
        self.assertFalse(self.client.send_avatar_pose(b"pre-admission"))
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertFalse(self.client.send_avatar_pose(b""))
        self.assertFalse(self.client.send_avatar_pose(bytes(link._FRAGMENT_SIZE + 1)))
        self.assertFalse(self.client.send_avatar_pose("not bytes"))
        self.assertTrue(self.client.send_avatar_pose(b"guest pose"))
        self.assertEqual(await asyncio.wait_for(inbound.get(), 3), (guest_id, b"guest pose"))

        with self.host.hold_game_routes():
            with patch.object(self.host, "_receive_avatar_pose", wraps=self.host._receive_avatar_pose) as receive:
                self.assertTrue(self.client.send_avatar_pose(b"travel pose"))
                async with asyncio.timeout(1):
                    while not receive.called:
                        await asyncio.sleep(.01)
            self.host.send_avatar_pose(b"host travel pose", lambda _: True)
        self.assertTrue(inbound.empty())
        self.host.send_avatar_pose(b"wrong guest", lambda _: False)
        self.host.send_avatar_pose(b"", lambda _: True)
        self.host.send_avatar_pose(bytes(link._FRAGMENT_SIZE + 1), lambda _: True)
        async def received_host_poses(count):
            async with asyncio.timeout(1):
                while self.client.statistics()["avatar_pose_datagrams_received"] < count:
                    await asyncio.sleep(.01)

        self.host.send_avatar_pose(b"host previous pose", lambda _: True)
        await received_host_poses(1)
        self.host.send_avatar_pose(b"host latest pose", lambda admitted: admitted == guest_id)
        await received_host_poses(2)
        self.assertEqual(self.client.take_avatar_pose(), b"host latest pose")
        self.assertIsNone(self.client.take_avatar_pose())
        self.assertTrue(self.host_inbox.messages.empty())
        self.assertTrue(self.client_inbox.messages.empty())

        old_host, old_client = self.host._protocols[guest_id], self.client._protocol

        def avatar_frame(data):
            return DatagramFrameReceived(link._AVATAR_POSE_HEADER + data)

        for data in (b"", bytes(link._FRAGMENT_SIZE + 1)):
            old_host.quic_event_received(avatar_frame(data))
            old_client.quic_event_received(avatar_frame(data))
        self.assertTrue(inbound.empty())
        self.assertIsNone(self.client.take_avatar_pose())
        old_client.quic_event_received(avatar_frame(b"stale pose"))
        later = old_client._loop.time() + link._AVATAR_POSE_MAX_AGE + .01
        with patch.object(old_client._loop, "time", return_value=later):
            self.assertIsNone(self.client.take_avatar_pose())
        old_client.quic_event_received(avatar_frame(b"before reconnect"))
        await self.client.disconnect()
        await asyncio.wait_for(self.disconnected.get(), 3)
        self.assertTrue((await self.connect_guest())["accepted"])
        with (patch.object(old_host, "authorized", True), patch.object(old_host, "game_access", True),
              patch.object(old_host, "terminated", False), patch.object(old_client, "authorized", True),
              patch.object(old_client, "game_access", True), patch.object(old_client, "terminated", False)):
            old_host.quic_event_received(avatar_frame(b"stale client"))
            old_client.quic_event_received(avatar_frame(b"stale host"))
        self.assertTrue(inbound.empty())
        self.assertIsNone(self.client.take_avatar_pose())
        self.assertTrue(self.client.send_avatar_pose(b"rejoined guest"))
        self.assertEqual(await asyncio.wait_for(inbound.get(), 3), (guest_id, b"rejoined guest"))
        self.assertEqual(self.host.statistics()["avatar_pose_datagrams_received"], 2)
        await self.exchange(1200)

    async def test_catalog_admission_cannot_send_latest_poses(self):
        guest_id = self.request["guest_id"]
        recipient = Mock()
        self.host.set_prop_pose_handler(recipient)
        self.host.set_avatar_pose_handler(recipient)
        self.assertTrue((await self.connect_guest(dict(self.request, catalog=True)))["accepted"])
        self.assertFalse(self.client.send_prop_pose(b"catalog grip"))
        self.assertFalse(self.client.send_avatar_pose(b"catalog body"))
        protocol = self.host._protocols[guest_id]
        protocol.quic_event_received(DatagramFrameReceived(link._PROP_POSE_HEADER + b"catalog grip"))
        protocol.quic_event_received(DatagramFrameReceived(link._AVATAR_POSE_HEADER + b"catalog body"))
        self.host.send_avatar_pose(b"catalog host", lambda _: True)
        self.assertIsNone(self.client.take_avatar_pose())
        recipient.assert_not_called()

    async def test_footsteps_preserve_queued_cues_on_admitted_game_links(self):
        guest_id = self.request["guest_id"]
        inbound = asyncio.Queue()
        self.host.set_footstep_handler(lambda guest, data: inbound.put_nowait((guest, data)))
        self.assertFalse(self.client.send_footstep(b"pre-admission"))
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertTrue(self.client.send_footstep(b"guest step"))
        self.assertFalse(self.client.send_footstep(bytes(link._FOOTSTEP_MAX_SIZE + 1)))
        self.assertEqual(await asyncio.wait_for(inbound.get(), 1), (guest_id, b"guest step"))

        self.host.send_footstep(b"excluded", lambda _: False)
        with self.host.hold_game_routes():
            self.host.send_footstep(b"during travel", lambda _: True)
            protocol = self.host._protocols[guest_id]
            protocol.quic_event_received(DatagramFrameReceived(link._FOOTSTEP_HEADER + b"during travel"))
        self.assertTrue(inbound.empty())

        self.host.send_footstep(b"step 1", lambda target: target == guest_id)
        self.host.send_footstep(b"step 2", lambda target: target == guest_id)
        async with asyncio.timeout(1):
            while len(self.client._footsteps) < 2:
                await asyncio.sleep(.005)
        self.assertEqual(self.client.take_footsteps(), [b"step 1", b"step 2"])
        self.assertTrue(self.host_inbox.messages.empty())
        self.assertTrue(self.client_inbox.messages.empty())

        protocol = self.client._protocol
        protocol.quic_event_received(DatagramFrameReceived(link._FOOTSTEP_HEADER + b"expired"))
        later = protocol._loop.time() + link._FOOTSTEP_MAX_AGE + .01
        with patch.object(protocol._loop, "time", return_value=later):
            self.assertEqual(self.client.take_footsteps(), [])

    async def test_weapon_cues_reach_current_game_guest_without_entering_unreal(self):
        guest_id = self.request["guest_id"]
        self.assertTrue((await self.connect_guest())["accepted"])
        self.host.send_weapon_shots((b"wrong guest",), lambda _: False)
        self.host.send_loot_motion(b"wrong guest", lambda _: False)
        self.host.restrict_game_routes(set())
        self.host.send_weapon_shots((b"during travel",), lambda _: True)
        self.host.send_loot_motion(b"during travel", lambda _: True)
        self.host.restrict_game_routes(None)
        self.host.send_weapon_shots((b"live cue",), lambda admitted: admitted == guest_id)
        self.host.send_loot_motion(b"falling item", lambda admitted: admitted == guest_id)
        await self.exchange(1200)
        self.assertEqual(self.client.take_weapon_shots(), [b"live cue"])
        self.assertEqual(self.client.take_weapon_shots(), [])
        self.assertEqual([packet for _, packet in self.client.take_loot_motion()], [b"falling item"])

        self.host.send_weapon_shots((b"queued cue",), lambda _: True)
        await asyncio.wait_for(self.client.wait_weapon_shots(), 1)
        await asyncio.sleep(.3)
        self.assertEqual(self.client.take_weapon_shots(), [])

        self.host.send_weapon_shots((b"before disconnect",), lambda _: True)
        self.host.send_loot_motion(b"before disconnect", lambda _: True)
        async with asyncio.timeout(1):
            while not self.client.weapon_shots_ready() or not self.client.loot_motion_ready():
                await asyncio.sleep(.01)
        await self.client.disconnect()
        self.assertFalse(self.client.weapon_shots_ready())
        self.assertFalse(self.client.loot_motion_ready())
        await asyncio.wait_for(self.disconnected.get(), 3)
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertEqual(self.client.take_weapon_shots(), [])

    async def test_weapon_cue_burst_shares_quic_packets_without_changing_cue_frames(self):
        self.assertTrue((await self.connect_guest())["accepted"])
        cues = [bytes([index]) * 128 for index in range(1, 5)]

        async def receive_cues():
            async with asyncio.timeout(1):
                while len(self.client._weapon_shots) < len(cues):
                    await asyncio.sleep(.005)
            self.assertEqual(self.client.take_weapon_shots(), cues)

        before = self.host.statistics()
        for cue in cues:
            self.host.send_weapon_shots((cue,), lambda _: True)
        separate = self.host.statistics()
        self.assertEqual(separate["quic_datagrams_sent"] - before["quic_datagrams_sent"], len(cues))
        separate_bytes = separate["quic_payload_bytes_sent"] - before["quic_payload_bytes_sent"]
        await receive_cues()

        before = self.host.statistics()
        self.host.send_weapon_shots(cues, lambda _: True)
        after = self.host.statistics()
        self.assertEqual(after["relay_queue_departed_frames"] - before["relay_queue_departed_frames"], len(cues))
        self.assertEqual(after["quic_datagrams_sent"] - before["quic_datagrams_sent"], 1)
        self.assertLess(after["quic_payload_bytes_sent"] - before["quic_payload_bytes_sent"], separate_bytes)
        await receive_cues()
        self.assertTrue(self.client_inbox.messages.empty())

    async def test_application_invalid_body_and_handler_error_keep_link_usable(self):
        async def on_request(_, payload):
            if payload.get("fail"):
                raise ValueError("private diagnostic")
            if payload.get("oversized"):
                return {"body": "x" * 32_768}
            return {"ok": True}

        self.application_handler = on_request
        self.assertTrue((await self.connect_guest())["accepted"])
        with self.assertRaisesRegex(LinkError, "message too large"):
            await self.client.request({"body": "x" * 32_768})
        with self.assertRaisesRegex(LinkError, "invalid application message"):
            await self.client.request({"body": float("nan")})
        with self.assertRaisesRegex(LinkError, "object"):
            await self.client.request(["not", "an", "object"])
        with self.assertLogs("tools.silverfish.link", level="WARNING") as log:
            with self.assertRaisesRegex(LinkError, "^host application request failed$"):
                await self.client.request({"fail": True})
            with self.assertRaisesRegex(LinkError, "^host application request failed$"):
                await self.client.request({"oversized": True})
        self.assertNotIn("private diagnostic", " ".join(log.output))
        self.assertEqual(await self.client.request({}), {"ok": True})

    async def test_application_timeout_keeps_link_usable_and_does_not_claim_rollback(self):
        finished = asyncio.Event()

        async def on_request(_, payload):
            if payload.get("slow"):
                await asyncio.sleep(0.3)
                finished.set()
            return {"ok": True}

        self.application_handler = on_request
        self.assertTrue((await self.connect_guest())["accepted"])
        with self.assertRaisesRegex(LinkError, "application request timed out"):
            await self.client.request({"slow": True}, timeout=0.05)
        self.assertTrue(self.client.connected)
        self.assertEqual(await self.client.request({}), {"ok": True})
        await asyncio.wait_for(finished.wait(), 3)

    async def test_stock_purchase_preserves_the_slow_receipt_and_ordinary_deadline(self):
        native_started = asyncio.Event()
        finish_checkout = asyncio.Event()
        queued = asyncio.get_running_loop().create_future()
        receipts = {}
        fingerprint = {"action": "native-finite-shop-buy", "operation_id": "a" * 32, "target": "B" * 32 + "@100"}

        async def on_request(guest_id, payload):
            if payload == {"action": "ordinary"}:
                return await queued
            self.assertEqual(payload, fingerprint)
            transaction = (guest_id, payload["operation_id"])
            if transaction not in receipts:
                native_started.set()
                await finish_checkout.wait()
                receipts[transaction] = {"ok": True, "revision": 1, "wallet_after": 900}
            return receipts[transaction]

        self.application_handler = on_request
        self.assertTrue((await self.connect_guest())["accepted"])
        with patch("tools.silverfish.link._ACTION_TIMEOUT", 0.06):
            checkout = asyncio.create_task(self.client.request(fingerprint, timeout=2))
            try:
                await asyncio.wait_for(native_started.wait(), 2)
                with self.assertRaisesRegex(LinkError, "^host application request timed out$"):
                    await self.client.request({"action": "ordinary"}, timeout=2)
                self.assertTrue(queued.cancelled())
            finally:
                finish_checkout.set()
            settled = await checkout

        self.assertEqual(settled, {"ok": True, "revision": 1, "wallet_after": 900})
        await self.client.disconnect()
        self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), self.request["guest_id"])
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertEqual(await self.client.request(fingerprint), settled)
        self.assertEqual(len(receipts), 1)

    async def test_application_requests_have_a_bounded_flight_count(self):
        opened = asyncio.Queue()
        release = asyncio.Event()

        async def on_request(_, payload):
            opened.put_nowait(payload["index"])
            await release.wait()
            return payload

        self.application_handler = on_request
        self.assertTrue((await self.connect_guest())["accepted"])
        running = [asyncio.create_task(self.client.request({"index": i})) for i in range(8)]
        try:
            for _ in running:
                await asyncio.wait_for(opened.get(), 3)
            with self.assertRaisesRegex(LinkError, "too many application requests"):
                await self.client.request({"index": len(running)})
        finally:
            release.set()
            results = await asyncio.gather(*running)
        self.assertEqual(results, [{"index": i} for i in range(8)])

    async def test_hard_travel_refreshes_raw_route_without_interrupting_application(self):
        async def on_request(guest_id, payload):
            return {"guest": guest_id, **payload}

        self.application_handler = on_request
        guest_id = self.request["guest_id"]
        self.assertTrue((await self.connect_guest())["accepted"])
        await self.exchange(1200)
        old_host_route = self.host.guest_endpoint(guest_id)
        client_route = self.client.address

        await self.host.refresh_game_routes()

        self.assertTrue(self.host.connected(guest_id))
        self.assertEqual(self.client.address, client_route)
        self.assertNotEqual(self.host.guest_endpoint(guest_id), old_host_route)
        self.assertEqual(await self.client.request({"zone": "destination"}), {
            "guest": guest_id, "zone": "destination",
        })
        self.assertTrue(self.disconnected.empty())
        self.game_host.sendto(b"stale old world reply", old_host_route)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.client_inbox.messages.get(), 0.1)
        await self.exchange(1200)

        await self.client.disconnect()
        self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), guest_id)
        await self.host.refresh_game_routes()
        self.assertIsNone(self.host.guest_endpoint(guest_id))
        self.assertTrue((await self.connect_guest())["accepted"])
        await self.exchange(1200)

    async def test_native_return_retry_refreshes_only_the_selected_guest(self):
        first = self.request["guest_id"]
        second_request = self.guest_request("second")
        second = second_request["guest_id"]
        await self.host_for_guests(self.request, second_request)
        second_client = await start_client()
        self.addAsyncCleanup(second_client.close)
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertTrue((await self.connect_guest(second_request, client=second_client))["accepted"])
        await self.exchange(1200)
        prior_host = self.host.guest_endpoint(first)
        prior_local = self.client.address
        untouched_host = self.host.guest_endpoint(second)

        self.host.restrict_game_routes({second})
        with self.assertRaises(LinkError):
            await self.host.refresh_guest_game_route(first)
        self.assertEqual(self.host.guest_endpoint(first), prior_host)
        self.host.restrict_game_routes({first, second})
        await self.host.refresh_guest_game_route(first)
        await self.client.renew_game_endpoint()
        self.assertNotEqual(self.host.guest_endpoint(first), prior_host)
        self.assertNotEqual(self.client.address, prior_local)
        self.assertEqual(self.host.guest_endpoint(second), untouched_host)
        self.assertEqual((self.client.game_packets_sent, self.client.game_packets_received), (0, 0))

        self.game_client.sendto(b"stale client packet", prior_local)
        self.game_host.sendto(b"stale server packet", prior_host)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.host_inbox.next(), 0.1)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.client_inbox.next(), 0.1)
        await self.exchange(1200)
        self.assertEqual((self.client.game_packets_sent, self.client.game_packets_received), (1, 1))
        self.assertEqual(await self.client.request({"guest_id": second}), {
            "authenticated_guest": first, "claimed_guest": second,
        })
        self.assertTrue(second_client.connected)
        self.assertTrue(self.disconnected.empty())

    async def test_catalog_only_admission_opens_no_game_endpoint_and_serves_requests(self):
        async def on_catalog(guest_id, payload):
            return {"guest": guest_id, "action": payload["action"]}

        self.application_handler = on_catalog
        catalog = await start_client(catalog_only=True)
        self.addAsyncCleanup(catalog.close)
        guest_id = self.request["guest_id"]
        with self.assertRaisesRegex(LinkError, "no local Unreal endpoint"):
            _ = catalog.address
        with self.assertRaisesRegex(LinkError, "requires catalog"):
            await self.connect_guest(client=catalog)
        response = await self.connect_guest(dict(self.request, catalog=True), client=catalog)
        self.assertEqual(response, {"accepted": True, "game_access": False, "catalog_version": 1})
        self.assertTrue(catalog.connected)
        self.assertFalse(catalog.game_access)
        self.assertIsNone(self.host.guest_endpoint(guest_id))
        self.assertFalse(self.host.connected(guest_id))
        self.assertEqual(await catalog.request({"action": "list"}), {"guest": guest_id, "action": "list"})
        self.assertEqual((await self.connect_guest())["reason"], "guest_already_connected")

        await catalog.disconnect()
        await self.wait_for_host_release()
        self.assertTrue(self.disconnected.empty())
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertTrue(self.client.game_access)
        await self.exchange(1200)

    async def test_catalog_admission_drops_datagrams_with_preexisting_game_endpoints(self):
        guest_id = self.request["guest_id"]
        self.assertTrue((await self.connect_guest())["accepted"])
        await self.exchange(1024)
        game_endpoint = self.host.guest_endpoint(guest_id)
        await self.client.disconnect()
        self.assertEqual(await asyncio.wait_for(self.disconnected.get(), 3), guest_id)

        reply = await self.connect_guest(dict(self.request, catalog=True))
        self.assertFalse(reply["game_access"])
        self.assertFalse(self.client.game_access)
        self.assertFalse(self.host.connected(guest_id))
        self.assertEqual(self.host.guest_endpoint(guest_id), game_endpoint)
        self.game_client.sendto(b"no request to game", self.client.address)
        self.game_host.sendto(b"no reply from game", game_endpoint)
        for inbox in (self.host_inbox, self.client_inbox):
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(inbox.messages.get(), 0.1)

        await self.client.disconnect()
        await self.wait_for_host_release()
        self.assertTrue(self.disconnected.empty())
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertEqual(self.host.guest_endpoint(guest_id), game_endpoint)
        await self.exchange(1024)

    async def test_catalog_flag_requires_a_boolean(self):
        with self.assertRaisesRegex(LinkError, "invalid catalog flag"):
            await self.connect_guest(dict(self.request, catalog="yes"))
        self.assertTrue((await self.connect_guest())["accepted"])

    async def test_delayed_termination_does_not_spawn_work_after_host_close(self):
        guest_id = self.request["guest_id"]
        self.assertTrue((await self.connect_guest())["accepted"])
        protocol = self.host._protocols[guest_id]
        self.assertIsNotNone(protocol)

        await self.host.close()
        self.assertEqual(self.disconnected.get_nowait(), guest_id)
        protocol.quic_event_received(ConnectionTerminated(0, None, ""))

        self.assertFalse(self.host._tasks)
        self.assertTrue(self.disconnected.empty())

    async def test_identity_round_trip_preserves_certificate_pin(self):
        key = self.identity.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        restored = load_server_identity(self.identity.certificate_pem, key)
        self.assertEqual(restored.certificate_pem, self.identity.certificate_pem)
        with self.assertRaisesRegex(LinkError, "private key"):
            load_server_identity(make_server_identity().certificate_pem, key)
        self.assertTrue((await self.connect_guest(identity=restored))["accepted"])


class ClientReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def udp(self, protocol_factory):
        transport, protocol = await asyncio.get_running_loop().create_datagram_endpoint(
            protocol_factory, local_addr=("127.0.0.1", 0),
        )
        self.addCleanup(transport.close)
        return transport, protocol

    @staticmethod
    async def wait_until(condition, timeout=8):
        async with asyncio.timeout(timeout):
            while not condition():
                await asyncio.sleep(0.025)

    async def test_production_client_reauthenticates_after_bidirectional_packet_blackhole(self):
        self.enterContext(patch.object(link, "_IDLE_TIMEOUT", 1.5))
        directory = Path(self.enterContext(TemporaryDirectory()))
        store = self.enterContext(CampaignStore(directory))
        game_host, host_inbox = await self.udp(DatagramInbox)
        game_guest, guest_inbox = await self.udp(DatagramInbox)
        identity = make_server_identity()
        host_control = Mock(generation="1" * 32)
        host = Host(store, host_control, "test-build", identity, "127.0.0.1", 0, False, None,
                    native_backend=None, native_events=False, native_guest=False)
        host.last_game = {"state": "hosting", "map": MAP, "pawns": {
            "local": {"name": "host pawn", "health": 100, "location": [0, 0, 0]},
            "guest": {"name": "guest pawn", "health": 100, "location": [50, 0, 0]},
        }}
        host.last_seen = time.monotonic()
        guest = store.issue_invitation("Guest", "Scout")
        guest_id, character_id = guest["guest_id"], guest["character_id"]
        party.add_player(host.party, "host")
        party.add_player(host.party, character_id)
        server = await start_host("127.0.0.1", 0, *game_host.get_extra_info("sockname"), identity,
                                  host.admit, host.disconnected, on_request=host.guest_request)
        self.addAsyncCleanup(server.close)
        proxy_udp, proxy = await self.udp(lambda: PacketBlackhole(server.address))
        transport = await start_client()
        self.addAsyncCleanup(transport.close)
        address, port = proxy_udp.get_extra_info("sockname")
        invite = dict(guest, host=address, port=port, certificate=identity.certificate_pem, game_build=host.game_build)
        client_control = Mock()
        client_control.requests.return_value = ()

        game = {"state": "ready", "map": MAP}
        join_commands = []

        async def command(action, endpoint):
            self.assertEqual(action, "join")
            self.assertEqual(endpoint, f"127.0.0.1:{transport.address[1]}")
            join_commands.append(action)
            game["state"] = "connected"
            return dict(game)

        async def wait_for(predicate, *, timeout):
            snapshot = dict(game)
            self.assertTrue(predicate(snapshot))
            return snapshot

        bridge = Bridge(directory, SimpleNamespace(poll=lambda: None))
        bridge.snapshot = lambda: dict(game)
        bridge.command, bridge.wait_for = command, wait_for
        client = Client(client_control, transport, invite, bridge, auto_delay=0.05)

        async def exchange(payload, host_endpoint, local_endpoint):
            game_guest.sendto(payload, local_endpoint)
            self.assertEqual(await host_inbox.next(), (payload, host_endpoint))
            game_host.sendto(payload, host_endpoint)
            self.assertEqual(await guest_inbox.next(), (payload, local_endpoint))

        initial = await client.connect()
        self.assertEqual((initial["character_id"], initial["resumed"]), (character_id, False))
        host.guest_pawn = "guest pawn"
        lease_id = host.guest["lease_id"]
        await client.open_game_link()
        monitor = asyncio.create_task(client.run())

        async def stop_monitor():
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor

        self.addAsyncCleanup(stop_monitor)
        host_endpoint, local_endpoint = server.guest_endpoint(guest_id), transport.address
        await self.wait_until(lambda: client.gameplay.get("players", {}).get("guest", {}).get("character_id") == character_id)
        await exchange(b"before the outage", host_endpoint, local_endpoint)
        await self.wait_until(lambda: transport.statistics()["keepalive_rtt_us"] > 0, timeout=1)

        proxy.blocked = True
        try:
            await self.wait_until(lambda: not server.connected(guest_id) and not transport.connected
                                  and host.guest["disconnected"] is not None)
            self.assertEqual(host.guest["lease_id"], lease_id)
            self.assertEqual(transport.statistics()["keepalive_rtt_us"], -1)
            reserved, = store.status()["leases"]
            self.assertEqual((reserved["character_id"], reserved["state"]), (character_id, "reserved"))
            self.assertGreater(proxy.discarded, 0)
            self.assertGreater(transport.statistics()["keepalive_ticks_congested"], 0)
            self.assertFalse(client.suspended)
            last_gameplay_before_resume = client.gameplay_seen
        finally:
            proxy.blocked = False

        await self.wait_until(lambda: transport.connected and host.guest["disconnected"] is None
                              and client.gameplay_seen > last_gameplay_before_resume)
        self.assertTrue(server.connected(guest_id))
        authenticated = [call.kwargs for call in client_control.event.call_args_list
                         if call.args == ("client_link_authenticated",)]
        self.assertEqual(authenticated, [
            {"character_id": character_id, "resumed": False},
            {"character_id": character_id, "resumed": True},
        ])
        active, = store.status()["leases"]
        self.assertEqual((active["character_id"], active["state"], host.guest["lease_id"]),
                         (character_id, "active", lease_id))
        self.assertEqual(host.guest_pawn, "guest pawn")
        self.assertEqual(client.gameplay["players"]["guest"]["character_id"], character_id)
        self.assertEqual((client.host_session, client.zone_epoch), (initial["session_id"], initial["zone_epoch"]))
        self.assertEqual(join_commands, ["join"])
        self.assertEqual((server.guest_endpoint(guest_id), transport.address), (host_endpoint, local_endpoint))
        await exchange(b"after reauthentication", host_endpoint, local_endpoint)


if __name__ == "__main__":
    unittest.main()
