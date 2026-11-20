"""Verify the Unreal listener through the Windows UDP owner tables."""

import ctypes
from functools import lru_cache
import socket
import sys


_DWORD = ctypes.c_uint32
_HEADER_SIZE = ctypes.sizeof(_DWORD)
_UDP_TABLE_OWNER_PID = 1
_ERROR_INSUFFICIENT_BUFFER = 122
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x800
_LOOPBACK_V4 = socket.htonl(0x7F000001)
_REFUSAL = "The staged game did not bind its expected loopback-only UDP socket"


class _Udp4Owner(ctypes.Structure):
    _fields_ = [("address", _DWORD), ("port", _DWORD), ("pid", _DWORD)]


class _Udp6Owner(ctypes.Structure):
    _fields_ = [
        ("address", ctypes.c_ubyte * 16), ("scope", _DWORD), ("port", _DWORD), ("pid", _DWORD),
    ]


@lru_cache(maxsize=1)
def _get_extended_udp_table():
    if sys.platform != "win32":
        raise RuntimeError("Windows UDP ownership verification is unavailable")
    library = ctypes.WinDLL("iphlpapi.dll", winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
    table = library.GetExtendedUdpTable
    table.argtypes = (ctypes.c_void_p, ctypes.POINTER(_DWORD), ctypes.c_int32, _DWORD, ctypes.c_int, _DWORD)
    table.restype = _DWORD
    return table


def _owner_rows(api, family, row_type):
    size = _DWORD()
    table = None
    # The table can grow after the null-buffer size probe.
    for _ in range(4):
        result = api(table, ctypes.byref(size), False, family, _UDP_TABLE_OWNER_PID, 0)
        if result == 0 and table is not None:
            count = _DWORD.from_buffer(table).value
            if count <= (ctypes.sizeof(table) - _HEADER_SIZE) // ctypes.sizeof(row_type):
                return (row_type * count).from_buffer(table, _HEADER_SIZE)
            break
        if result == 0 or (result == _ERROR_INSUFFICIENT_BUFFER and size.value < _HEADER_SIZE):
            break
        if result != _ERROR_INSUFFICIENT_BUFFER:
            raise ctypes.WinError(result)
        table = ctypes.create_string_buffer(size.value)
    raise RuntimeError("Windows did not return a complete UDP owner table")


def verify_loopback_socket(pid: int, port: int) -> None:
    """Require an explicit IPv4 loopback bind for the requested PID and UDP port."""
    if type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF or type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeError(_REFUSAL)
    api = _get_extended_udp_table()
    network_port = socket.htons(port)
    ipv4 = {row.address for row in _owner_rows(api, socket.AF_INET, _Udp4Owner)
            if row.pid == pid and row.port & 0xFFFF == network_port}
    if ipv4 != {_LOOPBACK_V4} or any(
        row.pid == pid and row.port & 0xFFFF == network_port
        for row in _owner_rows(api, socket.AF_INET6, _Udp6Owner)
    ):
        raise RuntimeError(_REFUSAL)
