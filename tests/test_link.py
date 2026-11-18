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
        self.assertTrue(self.disconnected.empty())
        self.assertTrue((await self.connect_guest())["accepted"])
        self.assertEqual(self.host.guest_endpoint(guest_id), game_endpoint)
        await self.exchange(1024)

    async def test_catalog_flag_requires_a_boolean(self):
        with self.assertRaisesRegex(LinkError, "invalid catalog flag"):
            await self.connect_guest(dict(self.request, catalog="yes"))
        self.assertTrue((await self.connect_guest())["accepted"])

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
