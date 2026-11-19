import asyncio
import secrets
import unittest
from unittest.mock import patch

from aioquic.quic.events import ConnectionTerminated
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


if __name__ == "__main__":
    unittest.main()
