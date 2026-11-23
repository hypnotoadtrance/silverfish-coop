"""Process-local commands, input, and status for the embedded engine worker."""

import asyncio
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

STATUS_LIMIT = 2 * 1024 * 1024
ZONE_ROSTER_LIMIT = 8192
GUEST_INTERACTION_SHOP = 1
GUEST_INTERACTION_BROKER = 2
GUEST_INTERACTION_DIALOGUE = 4
GUEST_INTERACTION_MOSBY = 8


class LocalBridge:
    def __init__(self, library):
        self._open = library.SilverfishBridgeOpenV1
        self._open.argtypes, self._open.restype = [], ctypes.c_int
        self._close = library.SilverfishBridgeCloseV1
        self._close.argtypes, self._close.restype = [], None
        self._submit = library.SilverfishBridgeSubmitV1
        self._submit.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
        self._submit.restype = ctypes.c_int64
        self._wait = library.SilverfishBridgeWaitV1
        self._wait.argtypes = [ctypes.c_int64, ctypes.c_uint32]
        self._wait.restype = ctypes.c_int32
        self._copy = library.SilverfishBridgeCopyV1
        self._copy.argtypes, self._copy.restype = [ctypes.c_int64, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int
        self._cancel = library.SilverfishBridgeCancelV1
        self._cancel.argtypes, self._cancel.restype = [ctypes.c_int64], None
        self._input_ready = library.SilverfishBridgeInputReadyV1
        self._input_ready.argtypes, self._input_ready.restype = [], ctypes.c_int
        self._read_input = library.SilverfishBridgeReadInputV1
        self._read_input.argtypes, self._read_input.restype = [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_uint32
        self._native_events_heartbeat = getattr(library, "SilverfishBridgePublishNativeEventsHeartbeatV1", None)
        if self._native_events_heartbeat is not None:
            self._native_events_heartbeat.argtypes = [ctypes.c_int64]
            self._native_events_heartbeat.restype = ctypes.c_int
        self._stock_board_heartbeat = getattr(library, "SilverfishBridgePublishStockBoardHeartbeatV1", None)
        if self._stock_board_heartbeat is not None:
            self._stock_board_heartbeat.argtypes = [ctypes.c_int64]
            self._stock_board_heartbeat.restype = ctypes.c_int
        self._host_dig_heartbeat = getattr(library, "SilverfishBridgePublishHostDigHeartbeatV1", None)
        if self._host_dig_heartbeat is not None:
            self._host_dig_heartbeat.argtypes = [ctypes.c_int64]
            self._host_dig_heartbeat.restype = ctypes.c_int
        self._native_zone_lease = getattr(library, "SilverfishBridgePublishNativeZoneLeaseV1", None)
        if self._native_zone_lease is not None:
            self._native_zone_lease.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_uint32]
            self._native_zone_lease.restype = ctypes.c_int
        self._guest_interaction_lease = getattr(library, "SilverfishBridgePublishGuestInteractionLeaseV1", None)
        if self._guest_interaction_lease is not None:
            self._guest_interaction_lease.argtypes = [ctypes.c_int64, ctypes.c_uint32,
                                                      ctypes.c_char_p, ctypes.c_uint32,
                                                      ctypes.c_char_p, ctypes.c_uint32]
            self._guest_interaction_lease.restype = ctypes.c_int
        self._prop_poses = getattr(library, "SilverfishBridgePublishPropPosesV1", None)
        if self._prop_poses is not None:
            self._prop_poses.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
            self._prop_poses.restype = ctypes.c_int
        self._take_local_prop_pose = getattr(library, "SilverfishBridgeTakeLocalPropPoseV1", None)
        self._local_prop_pose_buffer = None
        if self._take_local_prop_pose is not None:
            self._take_local_prop_pose.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            self._take_local_prop_pose.restype = ctypes.c_uint32
            self._local_prop_pose_buffer = ctypes.create_string_buffer(768)
        self._take_avatar_pose = getattr(library, "SilverfishBridgeTakeAvatarPoseV1", None)
        self._publish_avatar_poses = getattr(library, "SilverfishBridgePublishAvatarPosesV1", None)
        self._avatar_pose_buffer = None
        if self._take_avatar_pose is not None and self._publish_avatar_poses is not None:
            self._take_avatar_pose.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            self._take_avatar_pose.restype = ctypes.c_uint32
            self._publish_avatar_poses.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
            self._publish_avatar_poses.restype = ctypes.c_int
            self._avatar_pose_buffer = ctypes.create_string_buffer(512)
        self._lock = asyncio.Lock()
        self._status_buffer = None
        self._status_revision = 0
        self._status_frame = None
        self._enable_status = getattr(library, "SilverfishBridgeEnableStatusV1", None)
        self._latest_status = getattr(library, "SilverfishBridgeStatusRevisionV1", None)
        self._copy_status = getattr(library, "SilverfishBridgeCopyStatusV1", None)
        if self._enable_status is not None and self._latest_status is not None and self._copy_status is not None:
            self._enable_status.argtypes, self._enable_status.restype = [], ctypes.c_int
            self._latest_status.argtypes, self._latest_status.restype = [], ctypes.c_uint64
            self._copy_status.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.POINTER(ctypes.c_uint64)]
            self._copy_status.restype = ctypes.c_uint32

    @classmethod
    def connect(cls, image: Path):
        if os.name != "nt":
            return None
        path = image.parent / "ue4ss" / "Mods" / "SilverfishCoopNative" / "dlls" / "main.dll"
        get_module = ctypes.WinDLL("kernel32").GetModuleHandleExW
        get_module.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, ctypes.POINTER(wintypes.HMODULE)]
        get_module.restype = wintypes.BOOL
        handle = wintypes.HMODULE()
        pin_until_exit = 1
        if not get_module(pin_until_exit, str(path), ctypes.byref(handle)):
            return None
        try:
            library = ctypes.CDLL(str(path), handle=handle.value)
            bridge = cls(library)
        except (AttributeError, OSError):
            return None
        if not bridge._open():
            return None
        bridge.enable_status()
        return bridge

    def enable_status(self) -> None:
        if self._enable_status is None or self._latest_status is None or self._copy_status is None:
            return
        if self._enable_status():
            self._status_buffer = ctypes.create_string_buffer(STATUS_LIMIT)

    def status_ready(self) -> bool:
        return self._status_buffer is not None and self._latest_status() != self._status_revision

    def read_status(self) -> str | None:
        if self._status_buffer is None:
            return None
        latest = self._latest_status()
        if not latest:
            self._status_revision = 0
            self._status_frame = None
        elif latest != self._status_revision:
            revision = ctypes.c_uint64()
            size = self._copy_status(self._status_revision, self._status_buffer, STATUS_LIMIT, ctypes.byref(revision))
            if not revision.value or not 0 < size <= STATUS_LIMIT:
                return None
            self._status_revision = revision.value
            try:
                self._status_frame = self._status_buffer[:size].decode("utf-8")
            except UnicodeError:
                self._status_frame = None
        return self._status_frame

    async def command(self, payload: bytes, timeout: float) -> str:
        async with self._lock:
            ticket = self._submit(payload, len(payload))
            if not ticket:
                raise RuntimeError("The game could not queue the local co-op command")
            try:
                delay = max(1, min(120000, int(timeout * 1000)))
                size = await asyncio.to_thread(self._wait, ticket, delay)
                if size == 0:
                    raise TimeoutError("The game did not acknowledge the local co-op command")
                if size < 0:
                    raise RuntimeError("The local co-op command receipt is unavailable")
                buffer = ctypes.create_string_buffer(size)
                if not self._copy(ticket, buffer, size):
                    raise RuntimeError("The local co-op command receipt changed")
                return buffer.raw.decode("utf-8")
            finally:
                self._cancel(ticket)

    def input_ready(self):
        return bool(self._input_ready())

    def publish_native_events_heartbeat(self, stamp: int) -> bool:
        return self._native_events_heartbeat is not None and bool(self._native_events_heartbeat(stamp))

    def publish_stock_board_heartbeat(self, stamp: int) -> bool:
        return self._stock_board_heartbeat is not None and bool(self._stock_board_heartbeat(stamp))

    @property
    def host_dig_heartbeat_available(self) -> bool:
        return self._host_dig_heartbeat is not None

    def publish_host_dig_heartbeat(self, stamp: int) -> bool:
        return self._host_dig_heartbeat is not None and bool(self._host_dig_heartbeat(stamp))

    @property
    def native_zone_lease_available(self) -> bool:
        return self._native_zone_lease is not None

    def publish_native_zone_lease(self, stamp: int, roster: str | None = None) -> bool:
        if self._native_zone_lease is None:
            return False
        payload = roster.encode("utf-8") if roster is not None else None
        if payload is not None and len(payload) >= ZONE_ROSTER_LIMIT:
            # The stock reader rejects oversized rosters. Clear the labels while keeping the lease fresh.
            payload = b""
        return bool(self._native_zone_lease(stamp, payload, len(payload) if payload else 0))

    @property
    def guest_interaction_lease_available(self) -> bool:
        return self._guest_interaction_lease is not None

    def publish_guest_interaction_lease(self, stamp: int, features: int, area: str = "", pawn: str = "") -> bool:
        if self._guest_interaction_lease is None:
            return False
        area_bytes, pawn_bytes = area.encode("utf-8"), pawn.encode("utf-8")
        return bool(self._guest_interaction_lease(stamp, features, area_bytes, len(area_bytes),
                                                  pawn_bytes, len(pawn_bytes)))

    def publish_prop_poses(self, frame: str) -> bool:
        if self._prop_poses is None:
            return False
        payload = frame.encode("ascii")
        return bool(self._prop_poses(payload, len(payload)))

    @property
    def local_prop_pose_available(self) -> bool:
        return self._local_prop_pose_buffer is not None

    def take_local_prop_pose(self) -> bytes | None:
        buffer = self._local_prop_pose_buffer
        if buffer is None:
            return None
        size = self._take_local_prop_pose(buffer, len(buffer))
        return buffer[:size] if size else None

    @property
    def avatar_pose_available(self) -> bool:
        return self._avatar_pose_buffer is not None

    def take_avatar_pose(self) -> bytes | None:
        buffer = self._avatar_pose_buffer
        if buffer is None:
            return None
        size = self._take_avatar_pose(buffer, len(buffer))
        return buffer[:size] if size else None

    def publish_avatar_poses(self, frame: bytes) -> bool:
        return self._avatar_pose_buffer is not None and bool(self._publish_avatar_poses(frame, len(frame)))

    def read_input(self):
        buffer = ctypes.create_string_buffer(1024)
        records = []
        for _ in range(64):
            size = self._read_input(buffer, len(buffer))
            if not size:
                break
            records.append(buffer[:size].rstrip(b"\n"))
        return records

    def close(self):
        self._native_events_heartbeat = self._stock_board_heartbeat = None
        self._host_dig_heartbeat = self._guest_interaction_lease = self._native_zone_lease = None
        self._close()
