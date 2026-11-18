import asyncio
import secrets
import unittest

from cryptography.hazmat.primitives import serialization

from tools.silverfish.link import (
    LinkError,
    load_server_identity,
    make_server_identity,
    start_client,
    start_host,
)


class DatagramInbox(asyncio.DatagramProtocol):
    def __init__(self):
        self.messages = asyncio.Queue()

    def datagram_received(self, data, address):
        self.messages.put_nowait((data, address))

    async def next(self):
        return await asyncio.wait_for(self.messages.get(), 3)


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

        def admit(request):
            if request == self.request:
                return {"accepted": True, "character_id": request["character_id"]}
            return {"accepted": False, "reason": "unexpected_guest"}

        self.host = await start_host(
            "127.0.0.1", 0, *self.game_host.get_extra_info("sockname"),
            self.identity, admit, self.disconnected.put_nowait,
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
        for size in (1, 1024, 1200, 4097, 16_384):
            await self.exchange(size)

    async def test_reconnect_preserves_both_unreal_facing_endpoints(self):
        guest = self.request["guest_id"]
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
        self.assertEqual(self.client.address, client_endpoint)
        self.assertTrue(self.disconnected.empty())
        await self.exchange(1200)

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

    async def test_wrong_certificate_blocks_admission(self):
        with self.assertRaisesRegex(LinkError, "handshake failed"):
            await self.connect_guest(identity=make_server_identity())
        self.assertFalse(self.client.connected)
        self.assertIsNone(self.host.guest_endpoint(self.request["guest_id"]))

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


if __name__ == "__main__":
    unittest.main()
