import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish.runtime import MAP
from tools.silverfish.service import Client, open_admission


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    invite = {"host": "127.0.0.1", "port": 7777, "certificate": "unused test certificate"}

    async def test_catalog_handoff_retries_an_in_flight_release(self):
        transport = Mock()
        transport.connect = AsyncMock(side_effect=[
            {"accepted": False, "reason": "guest_already_connected"}, {"accepted": True},
        ])
        result = await open_admission(transport, self.invite, {}, timeout=1)
        self.assertTrue(result["accepted"])
        self.assertEqual(transport.connect.await_count, 2)

    async def test_initial_join_waits_for_travel_and_publishes_wait_reason(self):
        transport = Mock()
        transport.connect = AsyncMock(side_effect=[
            {"accepted": False, "reason": "travel_in_progress"}, {"accepted": True},
        ])
        waiting = Mock()
        result = await open_admission(transport, self.invite, {}, timeout=1, queue=True, on_wait=waiting)
        self.assertTrue(result["accepted"])
        waiting.assert_called_once_with("travel_in_progress")

    async def test_credentials_or_character_refusal_is_not_retried(self):
        transport = Mock()
        transport.connect = AsyncMock(return_value={"accepted": False, "reason": "guest_or_character_unavailable"})
        result = await open_admission(transport, self.invite, {}, timeout=1, queue=True)
        self.assertFalse(result["accepted"])
        self.assertEqual(transport.connect.await_count, 1)

    async def test_client_disables_routes_when_admission_does_not_confirm_selected_character(self):
        invite = dict(self.invite, guest_id="guest", credential="credential", character_id="scout", game_build="build")
        for character in (None, "different-character"):
            with self.subTest(character=character):
                response = {"accepted": True, "session_id": "a" * 32, "zone": MAP, "zone_epoch": "first"}
                if character is not None:
                    response["character_id"] = character
                transport = Mock(address=("127.0.0.1", 7778), game_access=True,
                                 connect=AsyncMock(return_value=response), disconnect=AsyncMock())
                control = Mock()
                client = Client(control, transport, invite, Mock())

                with self.assertRaisesRegex(RuntimeError, "did not confirm the selected character"):
                    await client.connect()

                transport.disconnect.assert_awaited_once_with()
                control.event.assert_not_called()
                self.assertIsNone(client.host_session)
