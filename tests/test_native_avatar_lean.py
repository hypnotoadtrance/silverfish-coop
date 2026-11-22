import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.silverfish import native_avatar_lean as lean, native_player_avatar as avatar


class AvatarLeanSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_relay_keeps_pose_sequence_when_the_same_host_visit_recovers(self):
        pawn = "Player_FP_C Host"
        visit = "a" * 32
        host = SimpleNamespace(
            zone_epoch=visit, transitioning=False, stopping=False, last_seen=10,
            last_game={"state": "hosting", "pawns": {"local": {"name": pawn}},
                       avatar.KEY: {"1": {"pawn": pawn}}},
            party={"players": {"host": {"downed": False}}}, native_recipients=lambda: {},
        )
        mailbox = Mock()
        mailbox.take_avatar_pose.side_effect = (
            None, b"Player_FP_C Host\t1000", None, None, b"Player_FP_C Host\t-2000",
        )
        transport = Mock()
        steps = iter((None, 8, 10, None, None))

        async def next_tick(_):
            if (last_seen := next(steps)) is not None:
                host.last_seen = last_seen
            if mailbox.take_avatar_pose.call_count == 5:
                host.stopping = True

        with patch.object(lean.time, "monotonic", return_value=10), patch.object(lean.asyncio, "sleep", next_tick):
            await lean.relay(host, SimpleNamespace(local_bridge=mailbox), transport)

        frames = [args[0] for args, _ in transport.send_avatar_pose.call_args_list]
        self.assertEqual([lean._HEADER.unpack_from(frame) for frame in frames],
                         [(bytes.fromhex(visit), 1), (bytes.fromhex(visit), 2)])
        self.assertEqual([lean._entries(frame) for frame in frames], [{1: 1000}, {1: -2000}])

    async def test_resumed_view_does_not_apply_an_older_packet_from_the_same_visit(self):
        pawn, visit = "Player_FP_C Guest", "c" * 32
        area = "/Game/Test"
        mailbox, transport = Mock(), Mock()
        mailbox.take_avatar_pose.return_value = None

        def frame(number, angle):
            return lean._HEADER.pack(bytes.fromhex(visit), number) + lean._ENTRY.pack(1, angle)

        transport.game_access = True
        transport.take_avatar_pose.side_effect = (frame(4, 1000), None, frame(3, 2000), frame(5, -1000))
        client = SimpleNamespace(
            zone_epoch=visit, expected_zone=area, needs_travel=False, server_transitioning=False, stopping=False,
            last_game={"state": "connected", "map": area, "pawns": {"local": {"name": pawn}}},
            bridge=SimpleNamespace(local_bridge=mailbox), transport=transport,
        )

        async def next_tick(_):
            transport.game_access = transport.take_avatar_pose.call_count != 1
            if transport.take_avatar_pose.call_count == 4:
                client.stopping = True

        with patch.object(lean.asyncio, "sleep", next_tick):
            await lean.follow(client)

        self.assertEqual([args[0] for args, _ in mailbox.publish_avatar_poses.call_args_list],
                         [b"Player_FP_C Guest\n1\t1000\n", b"Player_FP_C Guest\n1\t-1000\n"])
