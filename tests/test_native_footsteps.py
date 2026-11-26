from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.silverfish import native_footsteps as feet


VISIT = "1" * 32
VISIT_BYTES = bytes.fromhex(VISIT)
AREA = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"


class HostFootstepsTests(unittest.TestCase):
    def setUp(self):
        self.host = SimpleNamespace(
            zone_epoch=VISIT, transitioning=False, stopping=False, last_seen=10,
            last_game={"state": "hosting", "map": AREA, "pawns": {"local": {"name": "host pawn"}},
                       feet.OUTFITS: {"3": {"pawn": "host pawn"}, "8": {"pawn": "guest pawn"}}},
            authenticated_guest=lambda guest: (guest, guest) if guest in {"sender", "listener"} else None,
            guest_leave_pending=lambda guest: False,
            native_recipients=lambda: {"sender": {"pawn": "guest pawn", "connected": True},
                                       "listener": {"pawn": "another pawn", "connected": True}},
        )
        self.bridge = SimpleNamespace(local_bridge=Mock())
        self.transport = Mock()
        self.steps = feet.HostFootsteps(self.host, self.bridge, self.transport)
        clock = patch.object(feet.time, "monotonic", return_value=10)
        self.time = clock.start()
        self.addCleanup(clock.stop)

    def upload(self, serial=1, cue=b"Stone", *, volume=800, epoch=VISIT_BYTES):
        return feet._UPLOAD.pack(epoch, serial, volume, 940) + cue

    def test_exact_guest_identity_fanout_and_host_memory_presentation(self):
        self.steps.receive("sender", self.upload())
        data, allowed = self.transport.send_footstep.call_args.args
        self.assertEqual(feet._decode(data, feet._DOWNLOAD), ((VISIT_BYTES, 8, 1, 800, 940), "Stone"))
        self.assertFalse(allowed("sender"))
        self.assertTrue(allowed("listener"))
        self.assertFalse(allowed("unknown"))
        self.steps.flush()
        self.bridge.local_bridge.publish_footsteps.assert_called_once_with(
            f"{AREA}\nhost pawn\n8|Stone|800|940\n".encode("ascii"))
        self.steps.flush()
        self.bridge.local_bridge.publish_footsteps.assert_called_once()

    def test_travel_stale_duplicate_rate_and_unqualified_surfaces_are_discarded(self):
        for sender, packet in (("unknown", self.upload()), ("sender", self.upload(epoch=b"0" * 16)),
                               ("sender", self.upload(cue=b"NotStock")), ("sender", self.upload(volume=9999))):
            self.steps.receive(sender, packet)
        self.transport.send_footstep.assert_not_called()
        self.steps.receive("sender", self.upload())
        self.steps.receive("sender", self.upload(serial=2))
        self.time.return_value += .1
        self.steps.receive("sender", self.upload())
        self.transport.send_footstep.assert_called_once()
        self.host.transitioning = True
        self.steps.receive("sender", self.upload(serial=3))
        self.steps.flush()
        self.bridge.local_bridge.publish_footsteps.assert_not_called()

    def test_host_publication_shares_local_stock_mailbox_without_taking_weapon_cues(self):
        self.steps.local(f"{AREA}\n1|cycle|3|Rifle|Clip\nstep|2|Trash|250|900|other pawn\n"
                         "step|3|Water_Shallow|300|870|host pawn\n")
        data, allowed = self.transport.send_footstep.call_args.args
        self.assertEqual(feet._decode(data, feet._DOWNLOAD), ((VISIT_BYTES, 3, 3, 300, 870), "Water_Shallow"))
        self.assertTrue(allowed("sender"))
        self.transport.send_footstep.assert_called_once()

    def test_shutter_and_footstep_relay_in_same_tick_without_echo_or_replay(self):
        self.steps.receive("sender", self.upload())
        shutter = self.upload(serial=2, cue=b"CameraShutter")
        self.steps.receive("sender", shutter)
        self.steps.receive("sender", shutter)
        self.assertEqual(self.transport.send_footstep.call_count, 2)
        data, allowed = self.transport.send_footstep.call_args.args
        self.assertEqual(feet._decode(data, feet._DOWNLOAD), ((VISIT_BYTES, 8, 2, 800, 940), "CameraShutter"))
        self.assertFalse(allowed("sender"))
        self.assertTrue(allowed("listener"))
        self.steps.flush()
        self.bridge.local_bridge.publish_footsteps.assert_called_once_with(
            f"{AREA}\nhost pawn\n8|Stone|800|940\n8|CameraShutter|800|940\n".encode("ascii"))


class GuestFootstepsTests(unittest.IsolatedAsyncioTestCase):
    async def test_guest_upload_and_authenticated_host_projection_use_separate_scopes(self):
        local_pawn = "guest local"
        game = {"state": "connected", "map": AREA, "pawns": {"local": {"name": local_pawn}},
                feet.OUTFITS: {"8": {"pawn": local_pawn}}}
        mailbox = Mock()
        transport = Mock(game_access=True)
        client = SimpleNamespace(bridge=SimpleNamespace(local_bridge=mailbox), transport=transport, stopping=False,
                                 last_game=game, zone_epoch=VISIT, expected_zone=AREA, needs_travel=False,
                                 server_transitioning=False)
        frame = f"{AREA}\nstep|6|Dirt|600|900|{local_pawn}\n".encode("ascii")
        pitch = 930
        from_host = feet._DOWNLOAD.pack(VISIT_BYTES, 3, 9, 420, pitch) + b"Stone"
        own = feet._DOWNLOAD.pack(VISIT_BYTES, 8, 5, 420, pitch) + b"Sand"
        another_visit = feet._DOWNLOAD.pack(b"x" * 16, 3, 10, 420, pitch) + b"Wood"
        mailbox.take_local_weapon_cues.side_effect = [frame, frame]
        transport.take_footsteps.side_effect = [[from_host], [from_host, own, another_visit, from_host]]
        turns = 0

        async def step(_):
            nonlocal turns
            turns += 1
            client.stopping = turns == 2

        with patch.object(feet.asyncio, "sleep", side_effect=step):
            await feet.follow(client)
        transport.send_footstep.assert_called_once_with(feet._UPLOAD.pack(VISIT_BYTES, 6, 600, 900) + b"Dirt")
        mailbox.publish_footsteps.assert_called_once_with(f"{AREA}\n{local_pawn}\n3|Stone|420|930\n".encode("ascii"))
