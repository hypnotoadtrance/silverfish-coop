import asyncio
from hashlib import sha256
from pathlib import Path
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zlib

from tools.silverfish import (
    direct_ip, embedded_startup, link, native_title_entry, runtime, service, steam_browser, steam_network_report, steam_session,
)
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import Control, status as control_status
from tools.silverfish.steam_transport import (
    SteamTransport, _BUILD_KEY, _CALLBACK_ENTER, _CALLBACK_FRIEND_JOIN, _CALLBACK_INVITE, _CALLBACK_MATCH_LIST,
    _HEADER, _MAGIC,
)
from tools.silverfish.title_handoff import DESCRIPTOR_FILE, REQUEST_FILE
from tools.silverfish.title_session import STATUS_FILE, SessionPlan, TitleSession
from tools.silverfish.travel import OBSERVATORY

from tests.test_steam_transport import BUILD, GUEST, HOST, LOBBY, VERSION, Platform
from tests.test_title_handoff import ready as stock_title


class SteamSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.platform = Platform()

    def test_oversized_fast_avatar_encoding_fits_both_title_menus(self):
        pixels = bytes(range(256)) * 16
        compress = zlib.compress

        def compressed(data, *, level):
            if level == 1:
                return b"x" * 4351
            return compress(data, level=level)

        with patch.object(steam_session.zlib, "compress", side_effect=compressed):
            encoded = steam_session._avatar_hex(pixels)
        self.assertLessEqual(len(encoded), 8500)
        png = bytes.fromhex(encoded)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(png[37:41], b"IDAT")
        size, = struct.unpack_from("!I", png, 33)
        scanlines = zlib.decompress(png[41:41 + size])
        self.assertEqual(scanlines, b"".join(b"\0" + pixels[i:i + 128] for i in range(0, len(pixels), 128)))

    def test_network_report_groups_stable_samples_and_keeps_travel_traffic_in_totals(self):
        report = steam_network_report._Report()
        frame = {"state": "connected", "link_connected": True, "game": {"map": OBSERVATORY},
                 "generation": "session", "zone_epoch": "visit"}
        first = {"keepalive_probes_acked": 7, "keepalive_rtt_us": 95_000,
                 "game_payload_bytes_delivered": 300, "snapshot_received_bytes": 150, "relay_queue_expired_frames": 3}
        middle = {"keepalive_probes_acked": 8, "keepalive_rtt_us": 42_000,
                  "game_payload_bytes_delivered": 900, "snapshot_received_bytes": 330, "relay_queue_expired_frames": 4}
        last = {"keepalive_probes_acked": 9, "keepalive_rtt_us": 500_000,
                "game_payload_bytes_delivered": 1010, "snapshot_received_bytes": 385, "relay_queue_expired_frames": 4}
        report.sample(10, {**frame, "transport": first}, (10, 100, 5, 500, 3),
                      {"full_receive_batches": 3, "send_rejected_bytes": 0})
        report.sample(11, {**frame, "transport": middle}, (20, 300, 10, 1400, 3),
                      {"full_receive_batches": 7, "send_rejected_bytes": 0})
        report.sample(11.25, {**frame, "zone_epoch": "destination", "transport": last}, (21, 350, 13, 1650, 3),
                      {"full_receive_batches": 7, "send_rejected_bytes": 100})
        result = report.result("guest", VERSION)
        self.assertEqual(result["overall"]["sent_bytes"], 250)
        self.assertEqual(result["overall"]["received_bytes"], 1150)
        self.assertEqual(result["overall"]["mean_receive_bps"], 7360)
        self.assertEqual(result["overall"]["peak_receive_bps"], 7200)
        self.assertEqual(result["overall"]["peak_heartbeat_rtt_us"], 42_000)
        stable, travel = result["by_map_and_lobby_size"]
        self.assertEqual((stable["map"], stable["lobby_members"], stable["received_bytes"]), (OBSERVATORY, 3, 900))
        self.assertEqual((travel["map"], travel["lobby_members"], travel["received_bytes"]), (None, None, 250))
        self.assertIsNone(travel["peak_receive_bps"])
        self.assertIsNone(travel["peak_heartbeat_rtt_us"])
        self.assertEqual(stable["link_activity"], {"game_payload_bytes_delivered": 600,
                                                   "snapshot_received_bytes": 180, "relay_queue_expired_frames": 1,
                                                   "keepalive_probes_acked": 1})
        self.assertEqual(travel["link_activity"]["snapshot_received_bytes"], 55)
        self.assertEqual(result["overall"]["link_activity"]["game_payload_bytes_delivered"], 710)
        self.assertEqual(stable["steam_activity"], {"full_receive_batches": 4})
        self.assertEqual(travel["steam_activity"], {"send_rejected_bytes": 100})
        self.assertEqual(result["overall"]["steam_activity"], {"full_receive_batches": 4, "send_rejected_bytes": 100})
        peak = {"map": OBSERVATORY, "lobby_members": 3, "seconds": 1, "link_activity": stable["link_activity"],
                "steam_activity": stable["steam_activity"], "sent_bytes": 200, "received_bytes": 900,
                "heartbeat_rtt_us": 42_000}
        self.assertEqual(result["overall"]["peak_receive_interval"], peak)
        self.assertEqual(result["overall"]["peak_send_interval"], peak)

    def test_network_report_rebases_link_counters_without_changing_steam_totals(self):
        report = steam_network_report._Report()
        frame = {"state": "connected", "link_connected": True, "game": {"map": OBSERVATORY}, "zone_epoch": "visit"}
        report.sample(10, {**frame, "generation": "old", "transport": {"snapshot_received_bytes": 900}},
                      (8, 200, 4, 700, 2), {})
        report.sample(11, {**frame, "generation": "new", "transport": {"snapshot_received_bytes": 1100}},
                      (10, 300, 6, 1300, 2), {})
        report.sample(12, {**frame, "generation": "new", "transport": {"snapshot_received_bytes": 1280}},
                      (13, 530, 10, 1600, 2), {})
        result = report.result("guest", VERSION)
        self.assertEqual(result["overall"]["received_bytes"], 900)
        self.assertEqual(result["overall"]["link_activity"], {"snapshot_received_bytes": 180})
        self.assertEqual(result["overall"]["peak_receive_interval"]["map"], None)
        self.assertEqual(result["overall"]["peak_receive_interval"]["link_activity"], {})
        self.assertEqual(result["overall"]["peak_send_interval"]["link_activity"], {"snapshot_received_bytes": 180})
        self.assertEqual(result["by_map_and_lobby_size"][0]["link_activity"], {})

    def test_network_report_distinguishes_quic_payload_and_delayed_frames_at_the_peak(self):
        report = steam_network_report._Report()
        frame = {"state": "connected", "link_connected": True, "game": {"map": OBSERVATORY},
                 "generation": "session", "zone_epoch": "visit"}
        totals = {"quic_payload_bytes_received": 800_000, "game_wire_payload_bytes_received": 420_000,
                  "snapshot_received_bytes": 180_000, "snapshot_field_delta_responses": 7,
                  "relay_queue_departed_frames": 10, "relay_queue_wait_total_ns": 200_000_000,
                  "relay_queue_wait_over_20ms_frames": 4, "relay_queue_wait_over_50ms_frames": 1}
        report.sample(1, {**frame, "transport": dict.fromkeys(totals, 0)}, (0, 0, 0, 0, 4),
                      {"receive_unforwarded_bytes": 0})
        report.sample(2, {**frame, "transport": totals}, (8, 800, 750, 820_000, 4),
                      {"receive_unforwarded_bytes": 5_000})
        overall = report.result("guest", VERSION)["overall"]
        self.assertEqual(overall["mean_relay_queue_wait_us"], 20_000)
        peak = overall["peak_receive_interval"]
        self.assertEqual(peak["received_bytes"], 820_000)
        self.assertEqual(peak["link_activity"], totals)
        self.assertEqual(peak["steam_activity"], {"receive_unforwarded_bytes": 5_000})

    async def test_opt_in_steam_report_writes_once_at_session_exit_and_skips_direct_ip(self):
        _, session, _ = await self.start(HOST)
        _, client, _ = await self.start(GUEST)
        session.work = self.directory
        client.work = self.directory / "guest"
        client.work.mkdir()
        output = self.directory / steam_network_report.REPORT_FILE
        guest_output = client.work / steam_network_report.REPORT_FILE
        (self.directory / steam_network_report.ENABLE_FILE).touch()
        (client.work / steam_network_report.ENABLE_FILE).touch()
        game, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
        self.addCleanup(game.close)
        with Control(self.directory / "run", resident=True) as control, Control(client.work / "run", resident=True) as guest:
            control.status(active=True, state="hosting", game={"map": OBSERVATORY}, link_connected=True)
            guest.status(active=True, state="connected", game={"map": OBSERVATORY}, link_connected=True)
            async with session.playing(role="host", network_run=control.run):
                await session.transport.host(game.get_extra_info("sockname")[1], fingerprint="e" * 64)
                route = client._route = await client.transport.join(LOBBY)
                with patch.object(steam_network_report, "capture", wraps=steam_network_report.capture) as capture:
                    async with client.playing(route.host, route.port + 1, role="guest", network_run=guest.run):
                        self.assertIsNone(client.transport.lobby)
                    capture.assert_called_once_with(client.work, None, client.transport, "guest")
                route = client._route = await client.transport.join(LOBBY)
                payload = b"test packet"
                async with client.playing(route.host, route.port, role="guest", network_run=guest.run):
                    client.transport._guest_udp(payload, ("127.0.0.1", 32125))
                    with patch.object(client.transport.api, "send_datagram", return_value=False):
                        client.transport._guest_udp(payload, ("127.0.0.1", 32125))
                    await self.ready(lambda: session.transport.received_messages)
                    # A valid lobby message can reach a short-lived route that has already gone away.
                    stale = _HEADER.pack(_MAGIC, LOBBY, 0xF123456789ABCDEF) + b"late catalogue response"
                    session.transport.api.send_datagram(GUEST, stale)
                    await self.ready(lambda: client.transport.pressure_counters()["receive_unforwarded_messages"] == 1)
                    self.assertFalse(output.exists())
                    self.assertFalse(guest_output.exists())
        report = runtime.read_json(output)
        guest_report = runtime.read_json(guest_output)
        self.assertEqual(report["overall"]["received_messages"], 1)
        self.assertEqual(report["overall"]["received_bytes"], len(payload) + struct.calcsize("!4sQQ"))
        self.assertEqual(report["overall"]["received_bytes"], guest_report["overall"]["sent_bytes"])
        self.assertEqual(guest_report["overall"]["sent_messages"], 1)
        self.assertEqual(guest_report["overall"]["steam_activity"], {
            "send_rejected_messages": 1, "send_rejected_bytes": len(payload) + struct.calcsize("!4sQQ"),
            "receive_unforwarded_messages": 1, "receive_unforwarded_bytes": len(stale),
        })
        self.assertNotIn(str(HOST), output.read_text())
        self.assertNotIn(str(GUEST), output.read_text())

    async def start(self, peer):
        api = self.platform.client(peer)
        transport = SteamTransport(api, build=BUILD, version=VERSION)
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
            with self.assertRaises(direct_ip.Refused) as refusal:
                await direct_ip.connect(guest_bridge, "127.0.0.1", port, "test only", "other-build", 2,
                                        verified_fingerprint=fingerprint, require_trust=True)
            self.assertEqual(refusal.exception.reason, "incompatible_game_or_protocol")
            connection = await connect("test only")
            self.assertTrue(enrollment.authorized(connection.session()["guest_id"]))
            async with guest.playing("127.0.0.1", port, role="guest"):
                self.assertEqual(guest.transport.lobby, LOBBY)
            self.assertIsNone(guest.transport.lobby)
        self.assertFalse(offer.exists())
        self.assertIsNone(host_api.presence)

    async def test_invite_only_character_admission_reaches_resident_ready_and_resumes_on_same_route(self):
        self.enterContext(patch.object(link, "_IDLE_TIMEOUT", 1.5))
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
        enrollment = direct_ip.Enrollment(store, identity, build, service.PROTOCOL, {"native_zones": True}, "",
                                          steam_profile=host.transport.peer_profile)

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
            creator = {"name": "Fixture Guest", "steam_account": str(GUEST)}
            self.assertEqual(catalog, {"ok": True, "characters": [], "creator": creator})
            created = await service._character_catalog(connection.session(), "Invite Scout", "b" * 32)
            self.assertTrue(created["ok"], created)
            character = created["character"]["character_id"]
            self.assertEqual(await service._character_catalog(connection.session(), None, None),
                             {"ok": True, "characters": [created["character"]], "creator": creator})
            self.assertEqual(CampaignStore.read_existing(self.directory / "campaign")["guests"][connection.details["guest_id"]]
                             ["steam_account"], str(GUEST))
            avatar, = (await host.profile_avatars({str(GUEST)})).values()
            self.assertTrue(avatar.startswith("89504e470d0a1a0a0000000d494844520000002000000020"))
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
                            live_game = None
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
        guest_api, guest, bridge = await self.start(GUEST)
        fingerprint = "e" * 64
        await host.transport.host(32124, fingerprint=fingerprint, access="public")
        self.assertEqual(guest_api.lobby_owner(LOBBY), 0)
        browser = asyncio.create_task(steam_browser.serve(bridge, guest.transport, guest.open_lobby, guest.pump))
        self.addAsyncCleanup(self.stop_browser, browser)
        await asyncio.sleep(0)
        generation, token = "a" * 32, "b" * 32
        runtime.write_text(bridge / DESCRIPTOR_FILE, f"1\t{generation}\thost,join\t{int(time.time()) + 8}\n")
        request, result = bridge / steam_browser.REQUEST, bridge / steam_browser.RESPONSE
        prefix, response = f"1\t{generation}\t{token}\t", f"3\t{generation}\t{token}\t"
        selection = str(LOBBY)

        async def exchange(action, expected):
            runtime.write_text(request, prefix + action + "\n", newline="\n")
            await self.ready(lambda: result.exists() and result.read_text() == response + expected)

        del self.platform.metadata[_BUILD_KEY]
        await exchange("search", f"ready\t1\n{LOBBY}\tFixture Host\t-\t0\n")
        await exchange(selection, "incompatible\t0\n")
        self.platform.metadata[_BUILD_KEY] = f"rc-abc1234:{'2' * 64}"
        await exchange("search", f"ready\t1\n{LOBBY}\tFixture Host\trc-abc1234\t0\n")
        await exchange(selection, "incompatible\t0\n")
        self.platform.metadata[_BUILD_KEY] = f"{VERSION}:{BUILD}"
        await exchange("search", f"ready\t1\n{LOBBY}\tFixture Host\t{VERSION}\t1\n")
        with patch.object(guest_api, "join_lobby", wraps=guest_api.join_lobby) as join:
            await exchange(str(LOBBY + 1), "unavailable\t0\n")
            join.assert_not_called()
        self.assertEqual(guest_api.lobby_owner(LOBBY), 0)
        offer = bridge / steam_session.JOIN_OFFER
        self.platform.metadata[_BUILD_KEY] = f"rc-abc1234:{'2' * 64}"
        # A previously returned error frame can have the same contents.
        result.unlink()
        await exchange(selection, "unavailable\t0\n")
        notice, nonce, local, remote, expiry, source = offer.read_text().split()
        self.assertEqual((notice, nonce, local, remote, source), ("2", token, VERSION, "rc-abc1234", "browser"))
        self.assertGreater(int(expiry), time.time())
        self.assertIsNone(guest.transport.lobby)

        mismatch_notice = offer.read_bytes()
        self.platform.metadata[_BUILD_KEY] = f"{VERSION}:{BUILD}"
        discovery_time = [time.monotonic()]
        with patch.object(steam_browser, "time", SimpleNamespace(time=time.time, monotonic=lambda: discovery_time[0])):
            await exchange("search", f"ready\t1\n{LOBBY}\tFixture Host\t{VERSION}\t1\n")
            discovery_time[0] += steam_browser._LIST_LIFETIME
            with patch.object(guest_api, "join_lobby", wraps=guest_api.join_lobby) as join:
                await exchange(selection, "expired\t0\n")
                join.assert_not_called()
            self.assertEqual(offer.read_bytes(), mismatch_notice)

        fresh = "d" * 32
        prefix, response = f"1\t{generation}\t{fresh}\t", f"3\t{generation}\t{fresh}\t"
        await exchange("search", f"ready\t1\n{LOBBY}\tFixture Host\t{VERSION}\t1\n")
        await exchange(selection, "joined\t0\n")
        await self.ready(offer.exists)
        self.assertEqual(guest_api.lobby_owner(LOBBY), HOST)
        _, nonce, port, _, source = offer.read_text().split()
        self.assertEqual((nonce, source), (fresh, "browser"))
        self.assertEqual(guest.fingerprint_for("127.0.0.1", int(port)), fingerprint)

        cancel = bridge / steam_session.BROWSER_CANCEL
        runtime.write_text(cancel, f"1\t{'c' * 32}\tcancel\n", newline="\n")
        await self.ready(lambda: not cancel.exists())
        self.assertEqual(guest.transport.lobby, LOBBY)
        self.assertTrue(offer.exists())
        runtime.write_text(cancel, f"1\t{fresh}\tcancel\n", newline="\n")
        await self.ready(lambda: guest.transport.lobby is None)
        self.assertFalse(offer.exists())
        self.assertEqual(host.transport.lobby, LOBBY)

    @staticmethod
    async def stop_browser(browser):
        browser.cancel()
        await asyncio.gather(browser, return_exceptions=True)

    async def test_reopened_public_browser_supersedes_unanswered_search_and_uses_fresh_selection(self):
        _, host, _ = await self.start(HOST)
        guest_api, guest, bridge = await self.start(GUEST)
        await host.transport.host(32124, fingerprint="e" * 64, access="public")
        browser = asyncio.create_task(steam_browser.serve(bridge, guest.transport, guest.open_lobby, guest.pump))
        self.addAsyncCleanup(self.stop_browser, browser)
        await asyncio.sleep(0)

        generation, previous, latest = "a" * 32, "b" * 32, "c" * 32
        runtime.write_text(bridge / DESCRIPTOR_FILE, f"1\t{generation}\thost,join\t{int(time.time()) + 8}\n")
        request, result = bridge / steam_browser.REQUEST, bridge / steam_browser.RESPONSE

        def submit(token, action):
            runtime.write_text(request, f"1\t{generation}\t{token}\t{action}\n", newline="\n")

        with patch.object(guest_api, "request_lobbies", side_effect=(81, 82)) as searches:
            submit(previous, "search")
            await self.ready(lambda: searches.call_count == 1)
            submit(latest, "search")
            await self.ready(lambda: searches.call_count == 2)
            self.assertEqual(guest_api.discarded_searches, [81])
            self.assertFalse(result.exists())

            # A late old callback must have no effect on the new view or its selection.
            guest_api.callbacks.extend([
                (_CALLBACK_MATCH_LIST, struct.pack("<I", 0), 81),
                (_CALLBACK_MATCH_LIST, struct.pack("<I", 1), 82),
            ])
            listing = f"3\t{generation}\t{latest}\tready\t1\n{LOBBY}\tFixture Host\t{VERSION}\t1\n"
            await self.ready(lambda: result.exists() and result.read_text() == listing)
            submit(previous, str(LOBBY))
            await self.ready(lambda: not request.exists())
            self.assertEqual(result.read_text(), listing)
            self.assertIsNone(guest.transport.lobby)

            submit(latest, str(LOBBY))
            await self.ready(lambda: result.read_text() == f"3\t{generation}\t{latest}\tjoined\t0\n")
            self.assertEqual(guest.transport.lobby, LOBBY)

    async def test_public_join_back_discards_a_delayed_steam_callback_without_cancelling_invites(self):
        _, host, _ = await self.start(HOST)
        guest_api, guest, bridge = await self.start(GUEST)
        await host.transport.host(32124, fingerprint="e" * 64, access="public")
        browser = asyncio.create_task(steam_browser.serve(bridge, guest.transport, guest.open_lobby, guest.pump))
        self.addAsyncCleanup(self.stop_browser, browser)
        await asyncio.sleep(0)
        generation, token, wrong_token = "a" * 32, "b" * 32, "c" * 32
        runtime.write_text(bridge / DESCRIPTOR_FILE, f"1\t{generation}\thost,join\t{int(time.time()) + 8}\n")
        request, response = bridge / steam_browser.REQUEST, bridge / steam_browser.RESPONSE
        cancel, offer = bridge / steam_session.BROWSER_CANCEL, bridge / steam_session.JOIN_OFFER
        command, result = f"1\t{generation}\t{token}\t", f"3\t{generation}\t{token}\t"
        runtime.write_text(request, command + "search\n", newline="\n")
        await self.ready(lambda: response.exists() and response.read_text().startswith(result + "ready\t1\n"))

        handle = 81
        with patch.object(guest_api, "join_lobby", return_value=handle) as join:
            runtime.write_text(request, command + f"{LOBBY}\n", newline="\n")
            await self.ready(lambda: join.call_count == 1)

            runtime.write_text(cancel, f"1\t{wrong_token}\tcancel\n", newline="\n")
            await self.ready(lambda: not cancel.exists())
            self.assertFalse(guest.transport._calls[handle].result.done())
            self.assertFalse(offer.exists())

            runtime.write_text(cancel, f"1\t{token}\tcancel\n", newline="\n")
            await self.ready(lambda: response.read_text() == result + "unavailable\t0\n")
            self.assertFalse(offer.exists())
            self.assertIsNone(guest.transport.lobby)
            self.assertEqual(runtime.read_json(bridge / steam_session.STATUS)["state"], "ready")

        fresh_lobby = LOBBY + 1
        guest_api.callbacks.append((_CALLBACK_INVITE, struct.pack("<QQ", fresh_lobby, HOST), 0))
        await self.ready(lambda: not guest_api.callbacks and guest.transport.pending_invites.empty())
        self.assertFalse(offer.exists())
        self.assertEqual(runtime.read_json(bridge / steam_session.STATUS)["state"], "ready")

        self.platform.members.add(GUEST)
        guest_api.callbacks.append((_CALLBACK_ENTER, struct.pack("<QIB3xI4x", LOBBY, 0, 0, 1), handle))
        await self.ready(offer.exists)
        kind, invite_nonce, _, _ = offer.read_text().split()
        self.assertEqual(kind, "1")
        self.assertNotIn(invite_nonce, {token, wrong_token})
        self.assertEqual(guest_api.left, [LOBBY])
        self.assertEqual(guest.transport.lobby, fresh_lobby)
        self.assertEqual(host.transport.lobby, LOBBY)

    async def test_cancel_releases_only_the_current_join_offer_and_allows_a_fresh_invite(self):
        _, host, _ = await self.start(HOST)
        guest_api, guest, bridge = await self.start(GUEST)
        await host.transport.host(32124, fingerprint="d" * 64)
        guest_api.callbacks.append((_CALLBACK_INVITE, struct.pack("<QQ", LOBBY, HOST), 0))
        offer = bridge / steam_session.JOIN_OFFER
        await self.ready(offer.exists)
        nonce = offer.read_text().split("\t")[1]
        runtime.write_text(bridge / steam_session.JOIN_OPENED, f"1\t{nonce}\topened\n", newline="\n")
        await self.ready(lambda: not offer.exists())
        cancel = bridge / steam_session.JOIN_CANCEL
        runtime.write_text(cancel, f"1\t{'c' * 32}\tcancel\n", newline="\n")
        await self.ready(lambda: not cancel.exists())
        self.assertEqual(guest.transport.lobby, LOBBY)
        runtime.write_text(cancel, f"1\t{nonce}\tcancel\n", newline="\n")
        await self.ready(lambda: guest.transport.lobby is None)
        self.assertFalse(offer.exists())
        self.assertEqual(host.transport.lobby, LOBBY)
        guest_api.callbacks.append((_CALLBACK_INVITE, struct.pack("<QQ", LOBBY, HOST), 0))
        await self.ready(offer.exists)
        _, replacement, port, _ = offer.read_text().split()
        self.assertNotEqual(replacement, nonce)
        self.assertEqual(guest.fingerprint_for("127.0.0.1", int(port)), "d" * 64)
        runtime.write_text(cancel, f"1\t{nonce}\tcancel\n", newline="\n")
        await self.ready(lambda: not cancel.exists())
        self.assertTrue(offer.exists())
        self.assertEqual(guest.transport.lobby, LOBBY)

    async def test_failed_resident_title_observation_releases_the_claimed_steam_route(self):
        _, host, _ = await self.start(HOST)
        _, guest, bridge = await self.start(GUEST)
        await host.transport.host(32124, fingerprint="d" * 64)
        self.assertTrue(await guest.open_lobby(LOBBY))
        offer = bridge / steam_session.JOIN_OFFER
        _, _, port, _ = offer.read_text().split()
        connection = direct_ip.Connection({"host": "127.0.0.1", "port": int(port)})
        settings = Mock()
        settings.resolve.return_value = connection
        engine = Mock(observe_title=AsyncMock(side_effect=RuntimeError("Title changed before startup")))
        claim = SimpleNamespace(join_settings=("b" * 32, bridge.name, "character"), generation="c" * 32)
        with (patch("tools.silverfish.title_join_settings.TitleJoinSettings", return_value=settings),
              patch.object(embedded_startup, "EmbeddedEngine", return_value=engine)):
            plan, _ = embedded_startup._resident_direct_join_plan(self.directory, bridge, bridge, bridge, True, steam=guest)
            with self.assertRaisesRegex(RuntimeError, "Title changed"):
                await plan.resolve_claim(claim).start()
        self.assertFalse(offer.exists())
        self.assertIsNone(guest.transport.lobby)
        self.assertIsNone(guest.fingerprint_for("127.0.0.1", int(port)))
        self.assertEqual(host.transport.lobby, LOBBY)
