import ctypes as C
import struct
import unittest
from unittest.mock import Mock

from tools.silverfish import steamworks


class _Dll:
    """Expose fake flat exports with Win64-compatible callback and message pointers."""

    def __init__(self, *, app=steamworks.APP_ID):
        self.callbacks = []
        self.callback_memory = None
        self.results = {}
        self.exports = {}
        self.functions = {
            "InitFlat": 0,
            "GetHSteamPipe": 1,
            "SteamApps_v008": 2,
            "SteamFriends_v018": 2,
            "SteamMatchmaking_v009": 2,
            "SteamNetworkingMessages_SteamAPI_v002": 2,
            "SteamNetworkingUtils_SteamAPI_v004": 2,
            "SteamUser_v023": 2,
            "SteamUtils_v010": 2,
            "ISteamUtils_GetAppID": app,
            "ISteamUser_BLoggedOn": True,
            "ISteamUser_GetSteamID": 76561198000000001,
            "ISteamMatchmaking_CreateLobby": 24,
            "ISteamMatchmaking_JoinLobby": 25,
            "SteamNetworkingIdentity_GetSteamID64": self.peer,
            "ManualDispatch_GetNextCallback": self.next_callback,
            "ManualDispatch_GetAPICallResult": self.result,
            "ISteamFriends_SetRichPresence": True,
        }

    def __getattr__(self, name):
        if not name.startswith("SteamAPI_"):
            raise AttributeError(name)
        return self.exports.setdefault(name, Mock(side_effect=self.functions.get(name.removeprefix("SteamAPI_"))
                                                  if callable(self.functions.get(name.removeprefix("SteamAPI_"))) else None,
                                                  return_value=self.functions.get(name.removeprefix("SteamAPI_"))))

    @staticmethod
    def peer(out):
        address = C.cast(out, C.POINTER(steamworks._Identity)).contents
        return int.from_bytes(bytes(address.data[:8]), "little")

    def next_callback(self, pipe, out):
        if not self.callbacks:
            return False
        kind, payload = self.callbacks.pop(0)
        self.callback_memory = C.create_string_buffer(payload)
        record = C.cast(out, C.POINTER(steamworks._Callback)).contents
        record.id = kind
        record.data = C.addressof(self.callback_memory)
        record.size = len(payload)
        return True

    def result(self, pipe, handle, out, size, kind, failed):
        payload = self.results[handle]
        if payload is None:
            C.cast(failed, C.POINTER(C.c_bool)).contents.value = True
            return True
        C.memmove(out, payload, size)
        return True


class SteamworksTests(unittest.TestCase):
    def test_wrong_app_refuses_before_manual_dispatch(self):
        dll = _Dll(app=480)
        with self.assertRaisesRegex(steamworks.SteamError, "Project Silverfish app context"):
            steamworks.Steamworks(dll)
        dll.SteamAPI_Shutdown.assert_called_once_with()
        dll.SteamAPI_ManualDispatch_Init.assert_not_called()

    def test_dispatch_correlates_calls_releases_frames_and_ignores_unrelated_events(self):
        dll = _Dll()
        api = steamworks.Steamworks(dll)
        self.addCleanup(api.close)
        self.assertEqual(api.create_lobby(), 24)
        dll.SteamAPI_ISteamMatchmaking_CreateLobby.assert_called_once_with(api._matchmaking, 0, 5)
        self.assertEqual(api.join_lobby(17), 25)
        created = struct.pack("<i4xQ", 1, 42)
        invited = struct.pack("<QQ", 43, 44)
        dll.results.update({24: created, 25: None})
        dll.callbacks.extend([(703, struct.pack("<QiI", 999, 513, 16)),
                              (703, struct.pack("<QiI", 24, 513, 16)), (333, invited),
                              (703, struct.pack("<QiI", 25, 504, 24)), (500, b"other")])
        self.assertEqual(api.poll_callbacks(), [(513, created, 24), (333, invited, 0), (504, b"", 25)])
        self.assertEqual(dll.SteamAPI_ManualDispatch_FreeLastCallback.call_count, 5)
        self.assertEqual(dll.SteamAPI_ManualDispatch_GetAPICallResult.call_count, 2)

    def test_creates_each_steam_lobby_type_explicitly(self):
        dll = _Dll()
        api = steamworks.Steamworks(dll)
        self.addCleanup(api.close)
        for access, kind in (("invite", 0), ("friends", 1), ("public", 2)):
            api.create_lobby(access=access)
            dll.SteamAPI_ISteamMatchmaking_CreateLobby.assert_called_with(api._matchmaking, kind, 5)

    def test_received_data_is_copied_and_each_message_is_released(self):
        dll = _Dll()
        api = steamworks.Steamworks(dll)
        self.addCleanup(api.close)
        payload = C.create_string_buffer(b"abc\x00def")
        peer = 76561198000000003
        one = steamworks._MessagePrefix(data=C.addressof(payload), size=7)
        one.peer.data[:8] = peer.to_bytes(8, "little")
        oversize = steamworks._MessagePrefix(data=C.addressof(payload), size=steamworks.MAX_DATAGRAM + 1)

        def receive(messages, channel, out, capacity):
            out[0] = C.pointer(one)
            out[1] = C.pointer(oversize)
            return 2

        dll.SteamAPI_ISteamNetworkingMessages_ReceiveMessagesOnChannel.side_effect = receive
        self.assertEqual(api.receive_datagrams(), [(peer, b"abc\x00def")])
        self.assertEqual(dll.SteamAPI_SteamNetworkingMessage_t_Release.call_count, 2)

    def test_presence_cleanup_does_not_modify_another_connect_key(self):
        dll = _Dll()
        dll.SteamAPI_ISteamFriends_GetFriendRichPresence.return_value = b"some-other-mode"
        api = steamworks.Steamworks(dll)
        self.addCleanup(api.close)
        self.assertFalse(api.set_join_presence(17))
        dll.SteamAPI_ISteamFriends_SetRichPresence.assert_not_called()
        dll.SteamAPI_ISteamFriends_GetFriendRichPresence.return_value = b""
        self.assertTrue(api.set_join_presence(17))
        dll.SteamAPI_ISteamFriends_SetRichPresence.assert_called_once_with(2, b"connect", b"+connect_lobby 17")
        dll.SteamAPI_ISteamFriends_GetFriendRichPresence.return_value = b"a-replacement"
        self.assertTrue(api.set_join_presence(None))
        self.assertEqual(dll.SteamAPI_ISteamFriends_SetRichPresence.call_count, 1)
