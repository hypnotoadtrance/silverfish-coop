"""Process-local commands, input, and status for the embedded engine worker."""

import asyncio
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import time

from .guest_request_scope import mark_effect

STATUS_LIMIT = 2 * 1024 * 1024
# Match the native FIFO and its largest accepted input record.
INPUT_LIMIT = 1536
CRATE_OUTCOME_INPUT_LIMIT = 4096
INPUT_CAPACITY = 64
ZONE_ROSTER_LIMIT = 8192
WEAPON_CUES_LIMIT = 16 * 1024
SPATIAL_CUE_RECEIPT_BYTES = "native_spatial_cue_receipt_bytes"
GUEST_INTERACTION_SHOP = 1
GUEST_INTERACTION_BROKER = 2
GUEST_INTERACTION_DIALOGUE = 4
GUEST_INTERACTION_MOSBY = 8


def spatial_cue_matches(game: dict, receipt: dict) -> bool:
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    return (receipt.get("state"), receipt.get("map"), receipt.get("pawn")) == (
        game.get("state"), game.get("map"), pawn)


class LocalCommandNotSubmitted(RuntimeError):
    """The command was refused or cancelled before the game claimed it."""


class LocalCommandDeferred(LocalCommandNotSubmitted):
    """A replaceable sample did not wait behind other local commands."""


class LocalCommandReplyUnavailable(RuntimeError):
    """The game could have executed the command before both replies were lost."""


COMBAT_REPLY_WAIT = 0.75
REPLY_RECOVERED = "bridge_reply_recovered"


async def combat_command(bridge, command, operation, *, timeout):
    if getattr(bridge, "local_bridge", None) is None:
        return await bridge.command(*command, timeout=timeout)
    operation = f"op:{operation}"
    try:
        return await bridge.command(*command, operation, timeout=min(timeout, COMBAT_REPLY_WAIT), update_status=False)
    except TimeoutError:
        try:
            receipt = await bridge.command(*command, operation, timeout=timeout, update_status=False)
        except (TimeoutError, LocalCommandNotSubmitted) as error:
            raise LocalCommandReplyUnavailable("The game did not return the combat result") from error
    return {**receipt, REPLY_RECOVERED: True}


class LocalBridge:
    def __init__(self, library):
        self._open = library.SilverfishBridgeOpenV1
        self._open.argtypes, self._open.restype = [], ctypes.c_int
        self._close = library.SilverfishBridgeCloseV1
        self._close.argtypes, self._close.restype = [], None
        self._submit = library.SilverfishBridgeSubmitV1
        self._submit.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
        self._submit.restype = ctypes.c_int64
        stash_body_limit = getattr(library, "SilverfishBridgeStashBodyLimitV1", None)
        self.stash_body_limit = 0
        if stash_body_limit is not None:
            stash_body_limit.argtypes, stash_body_limit.restype = [], ctypes.c_uint32
            self.stash_body_limit = stash_body_limit()
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
        self._read_input_batch = getattr(library, "SilverfishBridgeReadInputBatchV1", None)
        if self._read_input_batch is not None:
            self._read_input_batch.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                               ctypes.POINTER(ctypes.c_uint16), ctypes.c_uint32]
            self._read_input_batch.restype = ctypes.c_uint32
            self._input_buffer = ctypes.create_string_buffer(INPUT_LIMIT * INPUT_CAPACITY)
            self._input_lengths = (ctypes.c_uint16 * INPUT_CAPACITY)()
        else:
            self._input_buffer = ctypes.create_string_buffer(INPUT_LIMIT)
        self._enable_crate_outcome_input = getattr(library, "SilverfishBridgeEnableCrateOutcomeInputV1", None)
        if self._enable_crate_outcome_input is not None:
            self._enable_crate_outcome_input.argtypes = [ctypes.c_uint32]
            self._enable_crate_outcome_input.restype = ctypes.c_int
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
        self._pause_offer = getattr(library, "SilverfishPauseOfferV1", None)
        self._pause_take = getattr(library, "SilverfishPauseTakeV1", None)
        if self._pause_offer is not None and self._pause_take is not None:
            self._pause_offer.argtypes, self._pause_offer.restype = [ctypes.c_char_p, ctypes.c_uint], ctypes.c_int
            self._pause_take.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint64)]
            self._pause_take.restype = ctypes.c_int
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
        self._take_stock_loot_motion = getattr(library, "SilverfishBridgeTakeStockLootMotionV1", None)
        self._stock_loot_motion_buffer = None
        if self._take_stock_loot_motion is not None:
            self._take_stock_loot_motion.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            self._take_stock_loot_motion.restype = ctypes.c_uint32
            self._stock_loot_motion_buffer = ctypes.create_string_buffer(64 * 1024)
        self._publish_footsteps = getattr(library, "SilverfishBridgePublishFootstepsV1", None)
        if self._publish_footsteps is not None:
            self._publish_footsteps.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
            self._publish_footsteps.restype = ctypes.c_int
        self._take_local_weapon_cues = getattr(library, "SilverfishBridgeTakeLocalWeaponCuesV1", None)
        self._enable_local_weapon_cues = getattr(library, "SilverfishBridgeEnableLocalWeaponCuesV1", None)
        self._local_weapon_cues_buffer = None
        if self._take_local_weapon_cues is not None and self._enable_local_weapon_cues is not None:
            self._take_local_weapon_cues.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            self._take_local_weapon_cues.restype = ctypes.c_uint32
            self._enable_local_weapon_cues.argtypes, self._enable_local_weapon_cues.restype = [], ctypes.c_int
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
        self._command_count = 0
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
        bridge.enable_local_weapon_cues()
        bridge.enable_crate_outcome_input()
        return bridge

    def enable_crate_outcome_input(self) -> None:
        if self._enable_crate_outcome_input is None:
            return
        slots = INPUT_CAPACITY if self._read_input_batch is not None else 1
        buffer = ctypes.create_string_buffer(CRATE_OUTCOME_INPUT_LIMIT * slots)
        if self._enable_crate_outcome_input(CRATE_OUTCOME_INPUT_LIMIT):
            self._input_buffer = buffer

    def enable_status(self) -> None:
        if self._enable_status is None or self._latest_status is None or self._copy_status is None:
            return
        if self._enable_status():
            self._status_buffer = ctypes.create_string_buffer(STATUS_LIMIT)

    @property
    def has_pause_exit(self) -> bool:
        return self._pause_offer is not None and self._pause_take is not None

    def offer_pause_exit(self, token: bytes | None, role: int) -> bool:
        return bool(self.has_pause_exit and self._pause_offer(token, role))

    def take_pause_exit(self, token: bytes, role: int) -> tuple[int, str] | None:
        if not self.has_pause_exit:
            return None
        press = ctypes.c_uint64()
        reason = {1: "quit_to_menu", 2: "save_and_quit"}.get(self._pause_take(token, role, ctypes.byref(press)))
        return (press.value, reason) if reason and press.value else None

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

    async def command(self, payload: bytes, timeout: float, *, skip_if_busy: bool = False, timing=None) -> str:
        if timing is not None:
            timing["lane_enter_us"] = time.monotonic_ns() // 1000
        # Account for waiting commands even during the lock's unlocked handoff.
        if skip_if_busy and self._command_count:
            raise LocalCommandDeferred("The local co-op command lane is busy")
        self._command_count += 1
        try:
            async with self._lock:
                if timing is not None:
                    timing["lane_acquired_us"] = time.monotonic_ns() // 1000
                ticket = self._submit(payload, len(payload))
                if timing is not None:
                    timing["native_submit_return_us"] = time.monotonic_ns() // 1000
                if not ticket:
                    raise LocalCommandNotSubmitted("The game could not queue the local co-op command")
                mark_effect("game_command")
                try:
                    delay = max(1, min(120000, int(timeout * 1000)))
                    size = await asyncio.to_thread(self._wait, ticket, delay)
                    if size == -2:
                        raise LocalCommandNotSubmitted("The game did not claim the local co-op command before it expired")
                    if size == 0:
                        raise TimeoutError("The game did not acknowledge the local co-op command")
                    if size < 0:
                        raise RuntimeError("The local co-op command receipt is unavailable")
                    if timing is not None:
                        timing["native_reply_us"] = time.monotonic_ns() // 1000
                    buffer = ctypes.create_string_buffer(size)
                    if not self._copy(ticket, buffer, size):
                        raise RuntimeError("The local co-op command receipt changed")
                    return buffer.raw.decode("utf-8")
                finally:
                    self._cancel(ticket)
        finally:
            self._command_count -= 1

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

    def take_stock_loot_motion(self) -> bytes | None:
        buffer = self._stock_loot_motion_buffer
        if buffer is None:
            return None
        size = self._take_stock_loot_motion(buffer, len(buffer))
        return buffer[:size] if 0 < size <= len(buffer) else None

    def take_local_prop_pose(self) -> bytes | None:
        buffer = self._local_prop_pose_buffer
        if buffer is None:
            return None
        size = self._take_local_prop_pose(buffer, len(buffer))
        return buffer[:size] if size else None

    @property
    def local_weapon_cues_available(self) -> bool:
        return self._local_weapon_cues_buffer is not None

    def enable_local_weapon_cues(self) -> None:
        if (self._take_local_weapon_cues is not None and self._enable_local_weapon_cues is not None
                and self._enable_local_weapon_cues()):
            self._local_weapon_cues_buffer = ctypes.create_string_buffer(WEAPON_CUES_LIMIT)

    def take_local_weapon_cues(self) -> bytes | None:
        buffer = self._local_weapon_cues_buffer
        if buffer is None:
            return None
        size = self._take_local_weapon_cues(buffer, len(buffer))
        return buffer[:size] if 0 < size <= len(buffer) else None

    @property
    def footsteps_available(self) -> bool:
        return self.local_weapon_cues_available and self._publish_footsteps is not None

    def publish_footsteps(self, frame: bytes) -> bool:
        return self.footsteps_available and bool(self._publish_footsteps(frame, len(frame)))

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

    def read_input(self, limit: int = 64):
        buffer = self._input_buffer
        limit = min(limit, INPUT_CAPACITY)
        if limit <= 0:
            return []
        records = []
        if self._read_input_batch is not None:
            count = self._read_input_batch(buffer, len(buffer), self._input_lengths, limit)
            start = 0
            for length in self._input_lengths[:count]:
                end = start + length
                records.append(buffer[start:end].rstrip(b"\n"))
                start = end
            return records
        for _ in range(limit):
            size = self._read_input(buffer, len(buffer))
            if not size:
                break
            records.append(buffer[:size].rstrip(b"\n"))
        return records

    def close(self):
        self._native_events_heartbeat = self._stock_board_heartbeat = None
        self._host_dig_heartbeat = self._guest_interaction_lease = self._native_zone_lease = None
        self._local_weapon_cues_buffer = None
        self._close()
