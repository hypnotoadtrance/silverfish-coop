import asyncio
from hashlib import sha256
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import Mock

from tools.silverfish import direct_ip, link, runtime, steam_session
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.native_session_exit import SESSION
from tools.silverfish.steam_transport import SteamTransport, _CALLBACK_INVITE

from tests.test_steam_transport import GUEST, HOST, LOBBY, Platform


class SteamSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.platform = Platform()

    async def start(self, peer):
        api = self.platform.client(peer)
        transport = SteamTransport(api)
        bridge = self.directory / str(peer)
        bridge.mkdir()
        pump = asyncio.create_task(transport.run())
        session = steam_session.SteamSession(transport, bridge, pump)
        inbox = asyncio.create_task(session.receive_invites())

        async def stop():
            inbox.cancel()
            pump.cancel()
            await asyncio.gather(inbox, pump, return_exceptions=True)

        self.addAsyncCleanup(stop)
        return api, session, bridge

    async def ready(self, predicate):
        async with asyncio.timeout(3):
            while not (value := predicate()):
                await asyncio.sleep(.02)
        return value

    async def test_pause_invite_runs_real_password_and_pinned_quic_enrollment_over_the_tunnel(self):
        host_api, host, host_bridge = await self.start(HOST)
        guest_api, guest, guest_bridge = await self.start(GUEST)
        identity = link.make_server_identity()
        fingerprint = sha256(identity.certificate_pem.encode("utf-8")).hexdigest()
        store = self.enterContext(CampaignStore(self.directory / "campaign"))
        enrollment = direct_ip.Enrollment(store, identity, "fixture-build", 2, {"native_zones": True}, "test only")
        server = await link.start_host("127.0.0.1", 0, "127.0.0.1", 32123, identity,
                                       lambda _: {"accepted": False}, lambda _: None, enroll=enrollment)
        self.addAsyncCleanup(server.close)
        pause_token = "a" * 32
        runtime.write_text(host_bridge / SESSION, f"1\t{pause_token}\thost\t{int(time.time()) + 5}\n", newline="\n")
        host_api.overlay = False
        async with host.playing(role="host"), host.host_panel(
            host_bridge, "127.0.0.1", server.address[1], identity.certificate_pem, Mock(),
        ):
            offer = host_bridge / steam_session.HOST_OFFER
            await self.ready(offer.exists)
            self.assertEqual(offer.read_text(), f"1\t{pause_token}\tready\n")
            runtime.write_text(host_bridge / steam_session.HOST_INVITE, f"1\t{'b' * 32}\tinvite\n", newline="\n")
            await self.ready(lambda: not (host_bridge / steam_session.HOST_INVITE).exists())
            self.assertIsNone(host.transport.lobby)
            runtime.write_text(host_bridge / steam_session.HOST_INVITE, f"1\t{pause_token}\tinvite\n", newline="\n")
            await self.ready(lambda: offer.exists() and offer.read_text().endswith("\tfriends\n"))
            self.assertEqual(host_api.presence, LOBBY)
            guest_api.callbacks.append((_CALLBACK_INVITE, struct.pack("<QQ", LOBBY, HOST), 0))
            join = guest_bridge / steam_session.JOIN_OFFER
            await self.ready(join.exists)
            _, nonce, port, _ = join.read_text().strip().split("\t")
            port = int(port)
            self.assertEqual(guest.fingerprint_for("127.0.0.1", port), fingerprint)
            self.assertIsNone(guest.fingerprint_for("127.0.0.1", port + 1))
            runtime.write_text(guest_bridge / steam_session.JOIN_OPENED, f"1\t{nonce}\topened\n", newline="\n")
            await self.ready(lambda: not join.exists())

            async def connect(password, pin=fingerprint):
                return await direct_ip.connect(guest_bridge, "127.0.0.1", port, password, "fixture-build", 2,
                                               verified_fingerprint=pin, require_trust=True)

            with self.assertRaisesRegex(direct_ip.Refused, "fingerprint"):
                await connect("test only", "0" * 64)
            with self.assertRaises(direct_ip.Refused) as refusal:
                await connect("")
            self.assertEqual(refusal.exception.reason, "direct_password_required")
            connection = await connect("test only")
            self.assertTrue(enrollment.authorized(connection.session()["guest_id"]))
            async with guest.playing("127.0.0.1", port, role="guest"):
                self.assertEqual(guest.transport.lobby, LOBBY)
            self.assertIsNone(guest.transport.lobby)
        self.assertFalse(offer.exists())
        self.assertIsNone(host_api.presence)

    async def test_cancel_releases_only_the_current_join_offer(self):
        _, host, _ = await self.start(HOST)
        guest_api, guest, bridge = await self.start(GUEST)
        await host.transport.host(32124, fingerprint="d" * 64)
        guest_api.callbacks.append((_CALLBACK_INVITE, struct.pack("<QQ", LOBBY, HOST), 0))
        offer = bridge / steam_session.JOIN_OFFER
        await self.ready(offer.exists)
        nonce = offer.read_text().split("\t")[1]
        cancel = bridge / steam_session.JOIN_CANCEL
        runtime.write_text(cancel, f"1\t{'c' * 32}\tcancel\n", newline="\n")
        await self.ready(lambda: not cancel.exists())
        self.assertEqual(guest.transport.lobby, LOBBY)
        runtime.write_text(cancel, f"1\t{nonce}\tcancel\n", newline="\n")
        await self.ready(lambda: guest.transport.lobby is None)
        self.assertFalse(offer.exists())
