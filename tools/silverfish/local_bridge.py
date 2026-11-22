"""Process-local command receipts for the embedded engine worker."""

import asyncio
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


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
        self._lock = asyncio.Lock()

    @classmethod
    def connect(cls, image: Path):
        if os.name != "nt":
            return None
        path = image.parent / "ue4ss" / "Mods" / "SilverfishCoopNative" / "dlls" / "main.dll"
        get_module = ctypes.WinDLL("kernel32").GetModuleHandleW
        get_module.argtypes, get_module.restype = [wintypes.LPCWSTR], wintypes.HMODULE
        handle = get_module(str(path))
        if not handle:
            return None
        try:
            library = ctypes.CDLL(str(path), handle=handle)
            bridge = cls(library)
        except (AttributeError, OSError):
            return None
        return bridge if bridge._open() else None

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
        self._close()
