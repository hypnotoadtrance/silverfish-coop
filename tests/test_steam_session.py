import asyncio
from hashlib import sha256
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from tools.silverfish import direct_ip, link, native_title_entry, runtime, service, steam_browser, steam_session
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import Control, status as control_status
from tools.silverfish.steam_transport import SteamTransport, _CALLBACK_FRIEND_JOIN, _CALLBACK_INVITE
from tools.silverfish.title_handoff import DESCRIPTOR_FILE, REQUEST_FILE
from tools.silverfish.title_session import STATUS_FILE, SessionPlan, TitleSession
from tools.silverfish.travel import OBSERVATORY

from tests.test_steam_transport import GUEST, HOST, LOBBY, Platform
from tests.test_title_handoff import ready as stock_title


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
        host_api.overlay = False
        async with host.playing(role="host"), host.host_panel(
            host_bridge, "127.0.0.1", server.address[1], identity.certificate_pem, Mock(), access="friends",
            pause_token=lambda: pause_token,
        ):
            offer = host_bridge / steam_session.HOST_OFFER
            await self.ready(offer.exists)
            self.assertEqual(offer.read_text(), f"1\t{pause_token}\tready\n")
            runtime.write_text(host_bridge / steam_session.HOST_INVITE, f"1\t{'b' * 32}\tinvite\n", newline="\n")
            await self.ready(lambda: not (host_bridge / steam_session.HOST_INVITE).exists())
            self.assertEqual(host.transport.lobby, LOBBY)
            self.assertIsNone(host_api.invited)
            self.assertEqual(host_api.lobby_access, "friends")
            self.assertEqual(host_api.presence, LOBBY)
            runtime.write_text(host_bridge / steam_session.HOST_INVITE, f"1\t{pause_token}\tinvite\n", newline="\n")
            await self.ready(lambda: offer.exists() and offer.read_text().endswith("\tfriends\n"))
            self.assertEqual(host_api.presence, LOBBY)
            connect = f"+connect_lobby {LOBBY}".encode("ascii").ljust(256, b"\x00")
            guest_api.callbacks.append((_CALLBACK_FRIEND_JOIN, struct.pack("<Q", HOST) + connect, 0))
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

    async def test_invite_only_character_admission_reaches_resident_ready_and_resumes_on_same_route(self):
        self.enterContext(patch.multiple(link, _IDLE_TIMEOUT=1.5, _KEEPALIVE_INTERVAL=.2))
        host_api, host, host_mailbox = await self.start(HOST)
        guest_api, guest, guest_mailbox = await self.start(GUEST)
        build = "fixture-build"
        identity = link.make_server_identity()
        store = self.enterContext(CampaignStore(self.directory / "campaign"))
        host_control = self.enterContext(Control(self.directory / "host-run", resident=True))
        authority = service.Host(store, host_control, build, identity, "127.0.0.1", 0, False, None,
                                 native_backend=None, native_events=False, native_guest=False)
        authority.last_game = dict(state="hosting", map=OBSERVATORY)
        authority.last_seen = time.monotonic()
        enrollment = direct_ip.Enrollment(store, identity, build, service.PROTOCOL, {"native_zones": True}, "")

        def admit(request):
            if not enrollment.authorized(request["guest_id"]):
                return {"accepted": False, "reason": "guest_unavailable"}
            return authority.admit(request)

        server = await link.start_host("127.0.0.1", 0, "127.0.0.1", 32123, identity,
                                       admit, authority.disconnected, on_request=authority.guest_request, enroll=enrollment)
        self.addAsyncCleanup(server.close)
        token = "a" * 32
        async with host.playing(role="host"), host.host_panel(
            host_mailbox, "127.0.0.1", server.address[1], identity.certificate_pem, host_control, access="invite",
            pause_token=lambda: token,
        ):
            await self.ready((host_mailbox / steam_session.HOST_OFFER).exists)
            runtime.write_text(host_mailbox / steam_session.HOST_INVITE, f"1\t{token}\tinvite\n", newline="\n")
            await self.ready(lambda: host_api.invited == LOBBY)
            self.assertEqual(host_api.lobby_access, "invite")
            self.assertIsNone(host_api.presence)
            guest_api.callbacks.append((_CALLBACK_INVITE, struct.pack("<QQ", LOBBY, HOST), 0))
            offer = guest_mailbox / steam_session.JOIN_OFFER
            await self.ready(offer.exists)
            _, nonce, port_text, _ = offer.read_text().split()
            address, port = "127.0.0.1", int(port_text)
            runtime.write_text(guest_mailbox / steam_session.JOIN_OPENED, f"1\t{nonce}\topened\n", newline="\n")
            await self.ready(lambda: not offer.exists())
            pin = guest.fingerprint_for(address, port)
            self.assertEqual(pin, sha256(identity.certificate_pem.encode("utf-8")).hexdigest())

            connection = await direct_ip.connect(guest_mailbox, address, port, "", build, service.PROTOCOL,
                                                 verified_fingerprint=pin, require_trust=True)
            catalog = await service._character_catalog(connection.session(), None, None)
            self.assertEqual(catalog, {"ok": True, "characters": []})
            created = await service._character_catalog(connection.session(), "Invite Scout", "b" * 32)
            self.assertTrue(created["ok"], created)
            character = created["character"]["character_id"]
            invite = connection.session(character)
            guest_id = invite["guest_id"]

            # The installed title's file stays at the solo menu while its child has the resident game frame.
            saved = guest_mailbox / "Saved"
            saved.mkdir()
            bridge = runtime.Bridge(guest_mailbox, Mock(poll=Mock(return_value=None)))
            sequence = 1
            live_game = None
            finish = asyncio.Event()
            guest_run = self.directory / "guest-run"
            transport = await link.start_client()
            self.addAsyncCleanup(transport.close)
            client = None

            def file_snapshot():
                nonlocal sequence
                if live_game is None:
                    sequence += 1
                data = stock_title(sequence)
                data["save_scope"] = None
                data[native_title_entry.PANEL_KEY] = dict(state="ready", mounted=True, actions=["join"])
                data[native_title_entry.STARTUP_KEY] = dict(map=data["map"], sequence=sequence, saved_directory=str(saved))
                return data

            async def join():
                nonlocal client, live_game
                async with guest.playing(address, port, role="guest"):
                    with Control(guest_run, resident=True) as control:
                        client = service.Client(control, transport, invite, bridge, allowed_maps=frozenset({OBSERVATORY}))
                        result = await client.connect()
                        self.assertEqual((result["character_id"], result["resumed"]), (character, False))
                        live_game = dict(mode="client", state="connected", map=OBSERVATORY, sequence=sequence + 1,
                                         save_scope=character, server_connection=True)
                        control.status(role="client", active=True, state="connected", character_id=character,
                                       gameplay_fresh=True, link_connected=transport.connected, game=live_game)
                        try:
                            await finish.wait()
                        finally:
                            control.status(role="client", active=False, state="stopped")

            bridge.snapshot = file_snapshot
            read_game = Mock(return_value=None)
            plan = SessionPlan(join, lambda: control_status(guest_run), lambda frame: frame.get("character_id") == character
                               and frame.get("gameplay_fresh") is True, read_game=read_game)
            title = TitleSession(bridge, {"join": plan}, resident=True, title_saved_directory=saved,
                                 on_title_ready=Mock(), poll_interval=.01)
            task = asyncio.create_task(title.run())
            self.addAsyncCleanup(self.stop_browser, task)

            def title_state():
                path = guest_mailbox / STATUS_FILE
                return runtime.read_json(path) if path.exists() else {}

            await self.ready(lambda: title_state().get("state") == "offered")
            generation = (guest_mailbox / DESCRIPTOR_FILE).read_text().split("\t")[1]
            runtime.write_text(guest_mailbox / REQUEST_FILE, f"1\t{generation}\t{'c' * 32}\tjoin\n", newline="\n")
            await self.ready(lambda: read_game.called or title_state().get("last_failure"))
            self.assertTrue(read_game.called, title_state())
            self.assertEqual(title_state()["state"], "starting")
            read_game.return_value = live_game
            await self.ready(lambda: title_state().get("state") == "ready" or title_state().get("last_failure"))
            status = title_state()
            self.assertEqual((status["state"], status.get("progress")), ("ready", "connected"), status)
            self.assertEqual(bridge.snapshot()["map"], native_title_entry.MAP)
            self.assertTrue(server.connected(guest_id))
            lease_id = authority.guest["lease_id"]
            host_endpoint, guest_endpoint = server.guest_endpoint(guest_id), transport.address

            self.platform.owner = 0
            host.transport._refresh_roster()
            guest.transport._refresh_roster()
            await self.ready(lambda: authority.guest["disconnected"] is not None and not transport.connected)
            reserved, = store.status()["leases"]
            self.assertEqual((reserved["state"], reserved["character_id"]), ("reserved", character))
            self.assertEqual((host.transport.lobby, guest.transport.lobby), (LOBBY, LOBBY))
            self.assertEqual(title_state()["state"], "ready")

            self.platform.owner = HOST
            host.transport._refresh_roster()
            guest.transport._refresh_roster()
            resumed = await client.connect()
            self.assertEqual((resumed["resumed"], resumed["character_id"]), (True, character))
            self.assertEqual(authority.guest["lease_id"], lease_id)
            self.assertEqual((server.guest_endpoint(guest_id), transport.address), (host_endpoint, guest_endpoint))
            self.assertEqual(guest.fingerprint_for(address, port), pin)
            self.assertTrue(server.connected(guest_id))
            self.assertIsNone(title_state()["last_failure"])
            finish.set()
            await self.ready(lambda: title_state().get("state") == "offered")
            self.assertIsNone(title_state()["last_failure"])

    async def test_public_lobby_browser_search_and_selection_use_the_steam_join_route(self):
        _, host, _ = await self.start(HOST)
        _, guest, bridge = await self.start(GUEST)
        fingerprint = "e" * 64
        await host.transport.host(32124, fingerprint=fingerprint, access="public")
        browser = asyncio.create_task(steam_browser.serve(bridge, guest.transport, guest.open_lobby, guest.pump))
        self.addAsyncCleanup(self.stop_browser, browser)
        await asyncio.sleep(0)
        generation, token = "a" * 32, "b" * 32
        runtime.write_text(bridge / DESCRIPTOR_FILE, f"1\t{generation}\thost,join\t{int(time.time()) + 8}\n")
        request, result = bridge / steam_browser.REQUEST, bridge / steam_browser.RESPONSE
        prefix = f"1\t{generation}\t{token}\t"
        runtime.write_text(request, prefix + "search\n", newline="\n")
        await self.ready(result.exists)
        self.assertEqual(result.read_text(), prefix + "ready\t1\nFixture Host\n")
        runtime.write_text(request, prefix + "1\n", newline="\n")
        offer = bridge / steam_session.JOIN_OFFER
        await self.ready(offer.exists)
        _, _, port, _ = offer.read_text().split()
        self.assertEqual(guest.fingerprint_for("127.0.0.1", int(port)), fingerprint)

    @staticmethod
    async def stop_browser(browser):
        browser.cancel()
        await asyncio.gather(browser, return_exceptions=True)

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
