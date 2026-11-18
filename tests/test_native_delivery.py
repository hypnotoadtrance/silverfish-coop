import time
import unittest
from unittest.mock import Mock

from tools.silverfish.service import Host


class NativeDeathDeliveryTests(unittest.TestCase):
    def setUp(self):
        store = Mock()
        store.status.return_value = {"world": {}}
        self.host = Host(store, Mock(), "staged-build", None, "127.0.0.1", 0, True, None,
                         native_backend="cpp", native_events=True, native_guest=True)
        self.host.guest = {"character_id": "scout", "disconnected": None}
        self.host.guest_pawn = "JoinedPawn"
        self.host.last_game = {"pawns": {"guest": {"name": "JoinedPawn"}}}
        self.host.last_seen = time.monotonic()
        self.bridge = Mock()
        self.event = ("guest", "event", "JoinedPawn")
        self.bridge.native_deaths.side_effect = [[self.event], []]

    def test_save_error_retries_the_same_operation(self):
        self.host.act = Mock(side_effect=[OSError("save temporarily locked"), None])
        self.host.deliver_native_deaths(self.bridge)
        self.assertEqual(self.host.pending_native_deaths, [self.event])

        self.host.deliver_native_deaths(self.bridge)
        self.assertEqual(self.host.pending_native_deaths, [])
        self.assertEqual(self.host.act.call_count, 2)
        self.host.act.assert_called_with("host", "event", "native-down", "scout", 1000)

    def test_pawn_replacement_rejects_the_pending_event(self):
        self.host.act = Mock(side_effect=OSError("save temporarily locked"))
        self.host.deliver_native_deaths(self.bridge)
        self.host.guest_pawn = "ReplacementPawn"
        self.host.last_game["pawns"]["guest"]["name"] = "ReplacementPawn"

        self.host.deliver_native_deaths(self.bridge)
        self.assertEqual(self.host.pending_native_deaths, [])
        self.host.act.assert_called_once()
        self.host.control.event.assert_called_once_with(
            "native_death_rejected", operation_id="event", reason="pawn_identity_changed",
        )
