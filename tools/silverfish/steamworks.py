"""Steamworks flat API used by the in-process Steam co-op worker."""

from __future__ import annotations

import ctypes as C
from pathlib import Path
import struct
import sys
from threading import get_ident


APP_ID = 2941710
LOBBY_CREATED = 513
LOBBY_ENTER = 504
LOBBY_MATCH_LIST = 510
LOBBY_JOIN_REQUESTED = 333
RICH_PRESENCE_JOIN_REQUESTED = 337
SESSION_REQUEST = 1251
SESSION_FAILED = 1252
MAX_DATAGRAM = 4096

_STEAM_API_CALL_COMPLETED = 703
_CALL_RESULT = struct.Struct("<QiI")
_CALL_RESULT_SIZE = {LOBBY_CREATED: 16, LOBBY_ENTER: 24, LOBBY_MATCH_LIST: 4}
_EVENTS = {LOBBY_ENTER, LOBBY_JOIN_REQUESTED, RICH_PRESENCE_JOIN_REQUESTED, SESSION_REQUEST, SESSION_FAILED}
_MAX_CALLBACKS = 128
_MAX_DATAGRAMS = 64
_MAX_PENDING_CALLS = 32
LOBBY_TYPES = {"invite": 0, "friends": 1, "public": 2}
_COOP_CHANNEL = 83
_SEND_UNRELIABLE_NO_NAGLE = 1
_SEND_AUTO_RESTART_BROKEN_SESSION = 32
_RESULT_OK = 1


class SteamError(RuntimeError):
    """A Steam operation failed, leaving Direct IP available."""


# SDK 1.62, Windows x64. Reference declarations are in ValveSoftware/Proton,
# lsteamclient/steamworks_sdk_162/{steam_api_flat.h,steam_api_internal.h,steamnetworkingtypes.h}.
class _Identity(C.Structure):
    _fields_ = [("kind", C.c_int32), ("size", C.c_int32), ("data", C.c_uint8 * 128)]


class _Callback(C.Structure):
    _fields_ = [("user", C.c_int32), ("id", C.c_int32), ("data", C.c_void_p), ("size", C.c_int32)]


class _MessagePrefix(C.Structure):
    _fields_ = [("data", C.c_void_p), ("size", C.c_int32), ("connection", C.c_uint32), ("peer", _Identity)]


_Message = C.POINTER(_MessagePrefix)
_MessageBatch = _Message * _MAX_DATAGRAMS


def _binding(dll, name, result, *args):
    try:
        function = getattr(dll, "SteamAPI_" + name)
    except AttributeError:
        raise SteamError(f"The Steamworks runtime is missing {name}") from None
    function.restype = result
    function.argtypes = args
    return function


def _steam_id(value: int) -> int:
    if type(value) is not int or not 0 < value < (1 << 64):
        raise SteamError("Invalid Steam identifier")
    return value


def _lobby_text(value: str, limit: int) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) > limit or b"\0" in encoded:
        raise SteamError("Invalid Steam lobby metadata")
    return encoded


class Steamworks:
    """Access the Steam client on the single thread that owns manual dispatch."""

    def __init__(self, dll):
        self._dll = dll
        self._thread = get_ident()
        self._pending: dict[int, int] = {}
        self._join_presence: bytes | None = None
        self._closed = True
        pointer, integer, number64, boolean, char = C.c_void_p, C.c_int32, C.c_uint64, C.c_bool, C.c_char_p
        identity = C.POINTER(_Identity)

        self._shutdown = _binding(dll, "Shutdown", None)
        init = _binding(dll, "InitFlat", integer, C.POINTER(C.c_char))
        get_pipe = _binding(dll, "GetHSteamPipe", integer)
        dispatch_init = _binding(dll, "ManualDispatch_Init", None)
        self._dispatch_frame = _binding(dll, "ManualDispatch_RunFrame", None, integer)
        self._next_callback = _binding(dll, "ManualDispatch_GetNextCallback", boolean, integer, C.POINTER(_Callback))
        self._free_callback = _binding(dll, "ManualDispatch_FreeLastCallback", None, integer)
        self._get_call = _binding(dll, "ManualDispatch_GetAPICallResult", boolean, integer, number64,
                                  pointer, integer, integer, C.POINTER(boolean))

        get_apps = _binding(dll, "SteamApps_v008", pointer)
        get_friends = _binding(dll, "SteamFriends_v018", pointer)
        get_matchmaking = _binding(dll, "SteamMatchmaking_v009", pointer)
        get_messages = _binding(dll, "SteamNetworkingMessages_SteamAPI_v002", pointer)
        get_net_utils = _binding(dll, "SteamNetworkingUtils_SteamAPI_v004", pointer)
        get_user = _binding(dll, "SteamUser_v023", pointer)
        get_utils = _binding(dll, "SteamUtils_v010", pointer)
        get_app_id = _binding(dll, "ISteamUtils_GetAppID", C.c_uint32, pointer)
        get_user_id = _binding(dll, "ISteamUser_GetSteamID", number64, pointer)
        logged_on = _binding(dll, "ISteamUser_BLoggedOn", boolean, pointer)
        relay_init = _binding(dll, "ISteamNetworkingUtils_InitRelayNetworkAccess", None, pointer)

        self._create_lobby = _binding(dll, "ISteamMatchmaking_CreateLobby", number64, pointer, integer, integer)
        self._join_lobby = _binding(dll, "ISteamMatchmaking_JoinLobby", number64, pointer, number64)
        self._leave_lobby = _binding(dll, "ISteamMatchmaking_LeaveLobby", None, pointer, number64)
        self._lobby_owner = _binding(dll, "ISteamMatchmaking_GetLobbyOwner", number64, pointer, number64)
        self._members = _binding(dll, "ISteamMatchmaking_GetNumLobbyMembers", integer, pointer, number64)
        self._member = _binding(dll, "ISteamMatchmaking_GetLobbyMemberByIndex", number64, pointer, number64, integer)
        self._lobby_data = _binding(dll, "ISteamMatchmaking_GetLobbyData", char, pointer, number64, char)
        self._set_lobby_data = _binding(dll, "ISteamMatchmaking_SetLobbyData", boolean, pointer, number64, char, char)
        self._lobby_filter = _binding(dll, "ISteamMatchmaking_AddRequestLobbyListStringFilter", None, pointer,
                                      char, char, integer)
        self._lobby_result_limit = _binding(dll, "ISteamMatchmaking_AddRequestLobbyListResultCountFilter", None,
                                            pointer, integer)
        self._lobby_open_slots = _binding(dll, "ISteamMatchmaking_AddRequestLobbyListFilterSlotsAvailable", None,
                                          pointer, integer)
        self._lobby_list = _binding(dll, "ISteamMatchmaking_RequestLobbyList", number64, pointer)
        self._lobby_by_index = _binding(dll, "ISteamMatchmaking_GetLobbyByIndex", number64, pointer, integer)
        self._persona = _binding(dll, "ISteamFriends_GetPersonaName", char, pointer)
        self._friend_name = _binding(dll, "ISteamFriends_GetFriendPersonaName", char, pointer, number64)
        self._request_user = _binding(dll, "ISteamFriends_RequestUserInformation", boolean, pointer, number64, boolean)
        self._small_avatar = _binding(dll, "ISteamFriends_GetSmallFriendAvatar", integer, pointer, number64)
        self._image_size = _binding(dll, "ISteamUtils_GetImageSize", boolean, pointer, integer,
                                    C.POINTER(C.c_uint32), C.POINTER(C.c_uint32))
        self._image_rgba = _binding(dll, "ISteamUtils_GetImageRGBA", boolean, pointer, integer, pointer, integer)
        self._avatars = {}
        self._overlay_enabled = _binding(dll, "ISteamUtils_IsOverlayEnabled", boolean, pointer)
        self._open_invites = _binding(dll, "ISteamFriends_ActivateGameOverlayInviteDialog", None, pointer, number64)
        self._get_presence = _binding(dll, "ISteamFriends_GetFriendRichPresence", char, pointer, number64, char)
        self._set_presence = _binding(dll, "ISteamFriends_SetRichPresence", boolean, pointer, char, char)
        self._launch_command_line = _binding(dll, "ISteamApps_GetLaunchCommandLine", integer,
                                             pointer, C.POINTER(C.c_char), integer)

        self._get_peer = _binding(dll, "SteamNetworkingIdentity_GetSteamID64", number64, identity)
        self._set_peer = _binding(dll, "SteamNetworkingIdentity_SetSteamID64", None, identity, number64)
        self._accept_messages = _binding(dll, "ISteamNetworkingMessages_AcceptSessionWithUser", boolean, pointer, identity)
        self._close_messages = _binding(dll, "ISteamNetworkingMessages_CloseChannelWithUser", boolean, pointer, identity, integer)
        self._send = _binding(dll, "ISteamNetworkingMessages_SendMessageToUser", integer, pointer, identity,
                              pointer, C.c_uint32, integer, integer)
        self._receive = _binding(dll, "ISteamNetworkingMessages_ReceiveMessagesOnChannel", integer,
                                 pointer, integer, C.POINTER(_Message), integer)
        self._release = _binding(dll, "SteamNetworkingMessage_t_Release", None, _Message)

        error = C.create_string_buffer(1024)
        status = init(error)
        if status:
            raise SteamError(f"Steamworks initialization failed ({status}). Launch Project Silverfish through Steam.")
        self._closed = False
        try:
            self._pipe = get_pipe()
            self._utils, user = get_utils(), get_user()
            if not self._pipe or not self._utils or not user or get_app_id(self._utils) != APP_ID:
                raise SteamError("Steamworks did not start in the Project Silverfish app context")
            if not logged_on(user):
                raise SteamError("Sign in to Steam before using Steam multiplayer")
            self.local_id = _steam_id(get_user_id(user))
            self._apps = get_apps()
            self._friends = get_friends()
            self._matchmaking = get_matchmaking()
            self._messages = get_messages()
            net_utils = get_net_utils()
            if not all((self._apps, self._friends, self._matchmaking, self._messages, net_utils)):
                raise SteamError("The required Steamworks interfaces are unavailable")
            dispatch_init()
            relay_init(net_utils)
        except BaseException:
            self.close()
            raise

    @classmethod
    def open(cls, dll: Path) -> Steamworks:
        if sys.platform != "win32" or C.sizeof(C.c_void_p) != 8:
            raise SteamError("Steam multiplayer requires the Windows x64 runtime")
        try:
            library = C.CDLL(str(dll.resolve(strict=True)), winmode=0x1100)
        except OSError:
            raise SteamError("The Steamworks runtime could not be loaded") from None
        return cls(library)

    def _require_open(self) -> None:
        if self._closed or get_ident() != self._thread:
            raise SteamError("Steamworks is unavailable on this worker")

    def _remember(self, callback: int, request: int) -> int:
        if not request:
            raise SteamError("Steam could not start the lobby request")
        if len(self._pending) >= _MAX_PENDING_CALLS:
            self._pending.pop(next(iter(self._pending)))
        self._pending[request] = callback
        return request

    def _call_result(self, payload: bytes) -> tuple[int, bytes, int] | None:
        if len(payload) != _CALL_RESULT.size:
            return None
        request, callback, size = _CALL_RESULT.unpack(payload)
        expected = self._pending.pop(request, None)
        if expected is None:
            return None
        if expected != callback or size != _CALL_RESULT_SIZE[expected]:
            return expected, b"", request
        buffer = C.create_string_buffer(size)
        failed = C.c_bool()
        ok = self._get_call(self._pipe, request, buffer, size, callback, C.byref(failed))
        return callback, buffer.raw if ok and not failed.value else b"", request

    def poll_callbacks(self) -> list[tuple[int, bytes, int]]:
        """Get (callback ID, payload, call handle). Notifications use handle zero."""
        self._require_open()
        self._dispatch_frame(self._pipe)
        callback = _Callback()
        events = []
        for _ in range(_MAX_CALLBACKS):
            if not self._next_callback(self._pipe, C.byref(callback)):
                break
            try:
                if not callback.data or not 0 < callback.size <= 4096:
                    continue
                if callback.id == _STEAM_API_CALL_COMPLETED:
                    result = self._call_result(C.string_at(callback.data, callback.size))
                    if result:
                        events.append(result)
                elif callback.id in _EVENTS:
                    events.append((callback.id, C.string_at(callback.data, callback.size), 0))
            finally:
                self._free_callback(self._pipe)
        return events

    def create_lobby(self, *, access: str = "invite") -> int:
        self._require_open()
        return self._remember(LOBBY_CREATED, self._create_lobby(self._matchmaking, LOBBY_TYPES[access], 5))

    def join_lobby(self, lobby_id: int) -> int:
        self._require_open()
        return self._remember(LOBBY_ENTER, self._join_lobby(self._matchmaking, _steam_id(lobby_id)))

    def request_lobbies(self, key: str, value: str, *, limit: int) -> int:
        self._require_open()
        self._lobby_filter(self._matchmaking, _lobby_text(key, 64), _lobby_text(value, 64), 0)
        self._lobby_result_limit(self._matchmaking, limit)
        self._lobby_open_slots(self._matchmaking, 1)
        return self._remember(LOBBY_MATCH_LIST, self._lobby_list(self._matchmaking))

    def discard_lobby_search(self, handle: int) -> None:
        if self._pending.get(handle) == LOBBY_MATCH_LIST:
            del self._pending[handle]

    def list_lobbies(self, count: int) -> tuple[int, ...]:
        self._require_open()
        return tuple(self._lobby_by_index(self._matchmaking, index) for index in range(count))

    def persona_name(self) -> str:
        self._require_open()
        return (self._persona(self._friends) or b"")[:128].decode("utf-8", errors="ignore")

    def friend_name(self, peer: int) -> str:
        self._require_open()
        name = (self._friend_name(self._friends, _steam_id(peer)) or b"")[:256].decode("utf-8", errors="ignore")
        return "" if name == "[unknown]" else name

    def small_avatar_rgba(self, peer: int) -> bytes | None:
        """Read Steam's 32px profile image, or None while Steam fetches it."""
        self._require_open()
        peer = _steam_id(peer)
        if peer != self.local_id and self._request_user(self._friends, peer, False):
            return None
        image = self._small_avatar(self._friends, peer)
        if image < 0:
            return None
        if image == 0:
            return b""
        if image not in self._avatars:
            width, height = C.c_uint32(), C.c_uint32()
            if not self._image_size(self._utils, image, C.byref(width), C.byref(height)) or (width.value, height.value) != (32, 32):
                return b""
            pixels = C.create_string_buffer(32 * 32 * 4)
            if not self._image_rgba(self._utils, image, pixels, len(pixels)):
                return b""
            if len(self._avatars) >= 256:
                self._avatars.pop(next(iter(self._avatars)))
            self._avatars[image] = pixels.raw
        return self._avatars[image]

    def leave_lobby(self, lobby_id: int) -> None:
        self._require_open()
        self._leave_lobby(self._matchmaking, _steam_id(lobby_id))

    def lobby_owner(self, lobby_id: int) -> int:
        self._require_open()
        return self._lobby_owner(self._matchmaking, _steam_id(lobby_id))

    def lobby_members(self, lobby_id: int) -> set[int]:
        self._require_open()
        lobby_id = _steam_id(lobby_id)
        count = self._members(self._matchmaking, lobby_id)
        if not 0 < count <= 250:
            return set()
        return {member for index in range(count) if (member := self._member(self._matchmaking, lobby_id, index))}

    def lobby_data(self, lobby_id: int, key: str) -> str:
        self._require_open()
        value = self._lobby_data(self._matchmaking, _steam_id(lobby_id), _lobby_text(key, 64))
        if not value or len(value) > 1024:
            return ""
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    def set_lobby_data(self, lobby_id: int, key: str, value: str) -> bool:
        self._require_open()
        return self._set_lobby_data(self._matchmaking, _steam_id(lobby_id), _lobby_text(key, 64), _lobby_text(value, 1024))

    def show_invites(self, lobby_id: int) -> bool:
        self._require_open()
        if not self._overlay_enabled(self._utils):
            return False
        self._open_invites(self._friends, _steam_id(lobby_id))
        return True

    def set_join_presence(self, lobby_id: int | None) -> bool:
        """Publish a Join Game action, or withdraw the exact value we published."""
        self._require_open()
        key = b"connect"
        current = self._get_presence(self._friends, self.local_id, key)
        if lobby_id is None:
            if self._join_presence is None:
                return True
            if current == self._join_presence and not self._set_presence(self._friends, key, None):
                return False
            self._join_presence = None
            return True
        value = f"+connect_lobby {_steam_id(lobby_id)}".encode("ascii")
        if current and current != self._join_presence:
            return False
        if not self._set_presence(self._friends, key, value):
            return False
        self._join_presence = value
        return True

    def launch_command_line(self) -> str:
        """Get optional Steam-delivered startup arguments for the game."""
        self._require_open()
        buffer = C.create_string_buffer(1024)
        size = self._launch_command_line(self._apps, buffer, len(buffer))
        if not 0 < size < len(buffer) - 1:
            return ""
        try:
            return buffer.value.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    def callback_peer(self, payload: bytes) -> int:
        """Decode the peer at the beginning of a SteamNetworkingMessages callback."""
        self._require_open()
        if len(payload) < C.sizeof(_Identity):
            return 0
        return self._get_peer(C.byref(_Identity.from_buffer_copy(payload)))

    def _identity(self, peer: int) -> _Identity:
        identity = _Identity()
        self._set_peer(C.byref(identity), _steam_id(peer))
        return identity

    def accept_messages(self, peer: int) -> bool:
        self._require_open()
        return self._accept_messages(self._messages, C.byref(self._identity(peer)))

    def close_messages(self, peer: int) -> None:
        self._require_open()
        self._close_messages(self._messages, C.byref(self._identity(peer)), _COOP_CHANNEL)

    def send_datagram(self, peer: int, data: bytes) -> bool:
        self._require_open()
        if not 0 < len(data) <= MAX_DATAGRAM:
            return False
        payload = C.create_string_buffer(data, len(data))
        flags = _SEND_UNRELIABLE_NO_NAGLE | _SEND_AUTO_RESTART_BROKEN_SESSION
        return self._send(self._messages, C.byref(self._identity(peer)), payload, len(data), flags, _COOP_CHANNEL) == _RESULT_OK

    def receive_datagrams(self) -> list[tuple[int, bytes]]:
        self._require_open()
        batch = _MessageBatch()
        count = self._receive(self._messages, _COOP_CHANNEL, batch, _MAX_DATAGRAMS)
        if not 0 < count <= _MAX_DATAGRAMS:
            return []
        try:
            received = []
            for message in batch[:count]:
                if not message:
                    continue
                frame = message.contents
                if frame.data and 0 < frame.size <= MAX_DATAGRAM and (peer := self._get_peer(C.byref(frame.peer))):
                    received.append((peer, C.string_at(frame.data, frame.size)))
            return received
        finally:
            for message in batch[:count]:
                if message:
                    self._release(message)

    def close(self) -> None:
        if self._closed:
            return
        self._require_open()
        try:
            if self._join_presence is not None:
                self.set_join_presence(None)
        finally:
            self._closed = True
            self._pending.clear()
            self._shutdown()
