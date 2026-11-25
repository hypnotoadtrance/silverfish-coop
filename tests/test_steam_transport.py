import asyncio
from hashlib import sha256
import secrets
import struct
import unittest

from tools.silverfish import link
from tools.silverfish.steam_transport import (
    SteamTransport,
    SteamTransportError,
    _CALLBACK_CREATED,
    _CALLBACK_ENTER,
    _CALLBACK_MATCH_LIST,
    _CALLBACK_FRIEND_JOIN,
    _CALLBACK_INVITE,
    _CALLBACK_SESSION_REQUEST,
    _HEADER,
    _MAGIC,
    _METADATA_KEY,
    parse_launch_lobby,
)


FINGERPRINT = "a7" * 32
HOST = 76561198000000001
GUEST = 76561198000000002
OUTSIDER = 76561198000000003
LOBBY = 109775240000000001


class Platform:
    def __init__(self):
        self.clients = {}
        self.owner = HOST
        self.members = set()
        self.metadata = {}

    def client(self, peer):
        client = FakeSteam(self, peer)
        self.clients[peer] = client
        return client


class FakeSteam:
    def __init__(self, platform, peer):
        self.platform, self.local_id = platform, peer
        self.callbacks, self.messages = [], []
        self.left, self.accepted, self.closed_channels = [], [], []
        self.presence = self.invited = None
        self.closed = False
        self.overlay = True
        self.lobby_access = None

    def create_lobby(self, *, access):
        self.lobby_access = access
        self.platform.members.add(self.local_id)
        self.callbacks.append((_CALLBACK_CREATED, struct.pack("<i4xQ", 1, LOBBY), 25))
        return 25

    def join_lobby(self, lobby):
        self.platform.members.add(self.local_id)
        self.callbacks.append((_CALLBACK_ENTER, struct.pack("<QIB3xI4x", lobby, 0, 0, 1), 26))
        return 26

    def leave_lobby(self, lobby):
        self.left.append(lobby)
        self.platform.members.discard(self.local_id)

    def lobby_owner(self, lobby):
        return self.platform.owner if self.local_id in self.platform.members else 0

    def lobby_members(self, lobby):
        return self.platform.members.copy()

    def lobby_data(self, lobby, key):
        return self.platform.metadata.get(key, "")

    def persona_name(self):
        return "Fixture Host"

    def request_lobbies(self, key, value, *, limit):
        count = int(self.platform.metadata.get(key) == value)
        self.callbacks.append((_CALLBACK_MATCH_LIST, struct.pack("<I", count), 27))
        return 27

    def list_lobbies(self, count):
        return (LOBBY,) if count else ()

    def set_lobby_data(self, lobby, key, value):
        self.platform.metadata[key] = value
        return True

    def set_join_presence(self, lobby):
        self.presence = lobby
        return True

    def show_invites(self, lobby):
        self.invited = lobby
        return self.overlay

    def callback_peer(self, data):
        return int.from_bytes(data, "little")

    def accept_messages(self, peer):
        self.accepted.append(peer)
        return True

    def send_datagram(self, peer, data):
        target = self.platform.clients[peer]
        if self.local_id not in target.accepted:
            target.callbacks.append((_CALLBACK_SESSION_REQUEST, self.local_id.to_bytes(8, "little"), 0))
        target.messages.append((self.local_id, data))
        return True

    def close_messages(self, peer):
        self.closed_channels.append(peer)

    def poll_callbacks(self):
        callbacks, self.callbacks = self.callbacks, []
        return callbacks

    def receive_datagrams(self):
        messages, self.messages = self.messages, []
        return messages

    def close(self):
        self.closed = True


class Inbox(asyncio.DatagramProtocol):
    def __init__(self):
        self.packets = asyncio.Queue()

    def datagram_received(self, data, address):
        self.packets.put_nowait((data, address))

    async def receive(self):
        return await asyncio.wait_for(self.packets.get(), 1)


class SteamTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_steam_lobby_access_controls_friend_join_presence_without_disabling_invites(self):
        api = Platform().client(HOST)
        host = await self.start(api)
        for access in ("invite", "friends", "public"):
            with self.subTest(access=access):
                await host.host(32124, fingerprint="d" * 64, access=access)
                self.assertEqual(api.lobby_access, access)
                self.assertEqual(api.presence, None if access == "invite" else LOBBY)
                self.assertTrue(host.show_invites())
                self.assertEqual(api.invited, LOBBY)
                host.leave()
                self.assertIsNone(api.presence)

    async def start(self, api):
        transport = SteamTransport(api)
        task = asyncio.create_task(transport.run())

        async def stop():
            transport.close()
            await asyncio.wait_for(task, 1)

        self.addAsyncCleanup(stop)
        return transport

    async def bind(self):
        socket, inbox = await asyncio.get_running_loop().create_datagram_endpoint(
            Inbox, local_addr=("127.0.0.1", 0),
        )
        self.addCleanup(socket.close)
        return socket, inbox, socket.get_extra_info("sockname")

    async def test_invited_guest_keeps_distinct_udp_sources_and_fingerprints(self):
        platform = Platform()
        host_api, guest_api = platform.client(HOST), platform.client(GUEST)
        host, guest = await self.start(host_api), await self.start(guest_api)
        game, game_inbox, host_address = await self.bind()
        self.assertEqual(await host.host(host_address[1], fingerprint=FINGERPRINT), LOBBY)
        self.assertEqual(platform.metadata[_METADATA_KEY], f"1:{HOST}:{FINGERPRINT}")
        self.assertEqual(host_api.presence, LOBBY)
        self.assertTrue(host.show_invites())
        self.assertEqual(host_api.invited, LOBBY)
        host_api.overlay = False
        self.assertFalse(host.show_invites())

        guest_api.callbacks.append((_CALLBACK_INVITE, struct.pack("<QQ", LOBBY, HOST), 0))
        invite = await asyncio.wait_for(guest.pending_invites.get(), 1)
        route = await guest.join(invite)
        self.assertEqual((route.lobby, route.peer_id, route.fingerprint), (LOBBY, HOST, FINGERPRINT))
        one, one_inbox, _ = await self.bind()
        two, two_inbox, _ = await self.bind()
        one.sendto(b"catalogue", (route.host, route.port))
        two.sendto(b"game packet", (route.host, route.port))
        first, second = await game_inbox.receive(), await game_inbox.receive()
        sources = dict((first, second))
        self.assertNotEqual(sources[b"catalogue"], sources[b"game packet"])
        game.sendto(b"game reply", sources[b"game packet"])
        game.sendto(b"catalogue reply", sources[b"catalogue"])
        self.assertEqual((await one_inbox.receive())[0], b"catalogue reply")
        self.assertEqual((await two_inbox.receive())[0], b"game reply")

        outsider = platform.client(OUTSIDER)
        outsider.send_datagram(HOST, _HEADER.pack(_MAGIC, LOBBY, 23) + b"outside lobby")
        guest_api.send_datagram(HOST, _HEADER.pack(_MAGIC, LOBBY + 1, 23) + b"wrong lobby")
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(game_inbox.packets.get(), .1)
        self.assertNotIn(OUTSIDER, host_api.accepted)
        guest.leave()
        host.leave()
        self.assertIn(GUEST, host_api.closed_channels)
        self.assertIn(HOST, guest_api.closed_channels)
        self.assertIsNone(host_api.presence)

    async def test_friend_join_accepts_only_the_lobby_argument(self):
        platform = Platform()
        api = platform.client(GUEST)
        guest = await self.start(api)
        api.callbacks.extend([
            (_CALLBACK_FRIEND_JOIN, struct.pack("<Q256s", HOST, b"exec anything"), 0),
            (_CALLBACK_FRIEND_JOIN, struct.pack("<Q256s", HOST, f"+connect_lobby {LOBBY}".encode()), 0),
        ])
        self.assertEqual(await asyncio.wait_for(guest.pending_invites.get(), 1), LOBBY)
        self.assertTrue(guest.pending_invites.empty())

    async def test_existing_authenticated_quic_carries_unreal_packets_over_the_tunnel(self):
        platform = Platform()
        host, guest = await self.start(platform.client(HOST)), await self.start(platform.client(GUEST))
        unreal_host, host_inbox, host_address = await self.bind()
        unreal_guest, guest_inbox, _ = await self.bind()
        identity = link.make_server_identity()
        fingerprint = sha256(identity.certificate_pem.encode("ascii")).hexdigest()
        request = {
            "guest_id": "steam-guest",
            "credential": secrets.token_urlsafe(24),
            "character_id": "steam-character",
            "protocol_version": 1,
            "game_build": "test-build",
        }
        catalog_request = dict(request, catalog=True, guest_id="steam-catalog")

        def admit(candidate):
            if candidate == catalog_request:
                return {"accepted": True, "game_access": False, "catalog_version": 1}
            if candidate == request:
                return {"accepted": True, "character_id": request["character_id"]}
            return {"accepted": False, "reason": "unexpected_guest"}

        relay = await link.start_host("127.0.0.1", 0, *host_address, identity, admit)
        self.addAsyncCleanup(relay.close)
        await host.host(relay.address[1], fingerprint=fingerprint)
        route = await guest.join(LOBBY)
        self.assertEqual(route.fingerprint, fingerprint)
        catalog, client = await link.start_client(catalog_only=True), await link.start_client()
        self.addAsyncCleanup(catalog.close)
        self.addAsyncCleanup(client.close)
        self.assertTrue((await catalog.connect(
            route.host, route.port, identity.certificate_pem, catalog_request,
        ))["accepted"])
        self.assertTrue((await client.connect(
            route.host, route.port, identity.certificate_pem, request,
        ))["accepted"])
        self.assertTrue(relay.connected(request["guest_id"]))
        data, reply = secrets.token_bytes(4097), secrets.token_bytes(4097)
        unreal_guest.sendto(data, client.address)
        forwarded, source = await host_inbox.receive()
        self.assertEqual(forwarded, data)
        unreal_host.sendto(reply, source)
        self.assertEqual(await guest_inbox.receive(), (reply, client.address))
        poses = asyncio.Queue()
        relay.set_prop_pose_handler(lambda guest_id, packet: poses.put_nowait((guest_id, packet)))
        pose = b"\xab" * 58
        self.assertTrue(client.send_prop_pose(pose))
        self.assertEqual(await asyncio.wait_for(poses.get(), 2), (request["guest_id"], pose))

    async def test_rejects_lobbies_with_the_wrong_owner_or_fingerprint(self):
        platform = Platform()
        platform.members.add(HOST)
        api = platform.client(GUEST)
        guest = await self.start(api)
        for marker in (f"1:{OUTSIDER}:{FINGERPRINT}", f"1:{HOST}:bad"):
            platform.metadata[_METADATA_KEY] = marker
            with self.assertRaisesRegex(SteamTransportError, "not a compatible Silverfish"):
                await guest.join(LOBBY)
            self.assertNotIn(GUEST, platform.members)

    async def test_owner_change_closes_the_existing_route(self):
        platform = Platform()
        platform.members = {HOST, OUTSIDER}
        platform.metadata[_METADATA_KEY] = f"1:{HOST}:{FINGERPRINT}"
        guest = await self.start(platform.client(GUEST))
        await guest.join(LOBBY)
        platform.owner = OUTSIDER
        async with asyncio.timeout(1):
            while guest.lobby is not None:
                await asyncio.sleep(.01)
        self.assertNotIn(GUEST, platform.members)

    async def test_brief_steam_roster_outage_keeps_the_same_loopback_route(self):
        platform = Platform()
        host, guest = await self.start(platform.client(HOST)), await self.start(platform.client(GUEST))
        game, game_inbox, host_address = await self.bind()
        await host.host(host_address[1], fingerprint=FINGERPRINT)
        route = await guest.join(LOBBY)
        client, client_inbox, _ = await self.bind()
        client.sendto(b"initial", (route.host, route.port))
        _, source = await game_inbox.receive()

        platform.owner = 0
        host._refresh_roster()
        guest._refresh_roster()
        client.sendto(b"while unavailable", (route.host, route.port))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(game_inbox.packets.get(), .1)
        self.assertEqual((host.lobby, guest.lobby), (LOBBY, LOBBY))

        platform.owner = HOST
        host._refresh_roster()
        guest._refresh_roster()
        client.sendto(b"resumed", (route.host, route.port))
        self.assertEqual(await game_inbox.receive(), (b"resumed", source))
        game.sendto(b"reply after reconnect", source)
        self.assertEqual((await client_inbox.receive())[0], b"reply after reconnect")

    async def test_late_success_after_cancel_releases_the_steam_lobby(self):
        platform = Platform()
        api = platform.client(HOST)
        owner = SteamTransport(api)
        self.addCleanup(owner.close)
        pending = asyncio.create_task(owner.host(9123, fingerprint=FINGERPRINT))
        await asyncio.sleep(0)
        owner.leave()
        with self.assertRaisesRegex(SteamTransportError, "cancelled"):
            await pending
        task = asyncio.create_task(owner.run())
        async with asyncio.timeout(1):
            while not api.left:
                await asyncio.sleep(.01)
        owner.close()
        await task
        self.assertEqual(api.left, [LOBBY])


class SteamLaunchTests(unittest.TestCase):
    def test_accepts_one_steam_lobby_argument(self):
        self.assertEqual(parse_launch_lobby(["game.exe", "+connect_lobby", str(LOBBY)]), LOBBY)
        self.assertIsNone(parse_launch_lobby(["game.exe", f"+connect_lobby {LOBBY}"]))
        self.assertIsNone(parse_launch_lobby(["+connect_lobby", str(LOBBY), "+connect_lobby", str(LOBBY)]))
        self.assertIsNone(parse_launch_lobby(["+connect_lobby", str(2**64)]))
