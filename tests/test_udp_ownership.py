import ctypes
import os
import socket
import struct
import unittest
from unittest.mock import patch

from tools.silverfish import runtime, udp_ownership


class UdpOwnerTableTests(unittest.TestCase):
    def test_size_race_retries_and_port_uses_low_network_order_word(self):
        pid, port = 1234, 23456
        row = struct.pack("=IIII", 1, socket.htonl(0x7F000001),
                          0xFFFF0000 | socket.htons(port), pid)
        calls = []

        def table(buffer, size_pointer, order, family, table_class, reserved):
            self.assertEqual((order, table_class, reserved), (False, 1, 0))
            size = ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_uint32)).contents
            calls.append((family, size.value))
            if family == socket.AF_INET:
                if size.value < len(row):
                    size.value = 4 if buffer is None else len(row)
                    return 122
                ctypes.memmove(buffer, row, len(row))
                return 0
            if buffer is None:
                size.value = 4
                return 122
            return 0

        with patch.object(udp_ownership, "_get_extended_udp_table", return_value=table):
            runtime.verify_loopback_socket(pid, port)
        self.assertEqual(calls, [
            (socket.AF_INET, 0), (socket.AF_INET, 4), (socket.AF_INET, len(row)),
            (socket.AF_INET6, 0), (socket.AF_INET6, 4),
        ])

    def test_truncated_ipv6_table_cannot_verify_an_ipv4_bind(self):
        pid, port = 1234, 23456
        ipv4 = struct.pack("=IIII", 1, socket.htonl(0x7F000001), socket.htons(port), pid)
        ipv6 = struct.pack("=I", 1)

        def table(buffer, size_pointer, _order, family, _table_class, _reserved):
            data = ipv4 if family == socket.AF_INET else ipv6
            if buffer is None:
                ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_uint32)).contents.value = len(data)
                return 122
            ctypes.memmove(buffer, data, len(data))
            return 0

        with patch.object(udp_ownership, "_get_extended_udp_table", return_value=table):
            with self.assertRaisesRegex(RuntimeError, "complete UDP owner table"):
                runtime.verify_loopback_socket(pid, port)


@unittest.skipUnless(os.name == "nt", "Uses the Windows IP Helper UDP tables")
class WindowsUdpOwnerTests(unittest.TestCase):
    def test_bound_loopback_requires_the_exact_pid_and_port(self):
        pid = os.getpid()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            runtime.verify_loopback_socket(pid, port)
            self.assertTrue(udp_ownership.owns_udp_port(pid, port))
            with self.assertRaisesRegex(RuntimeError, "loopback-only UDP socket"):
                runtime.verify_loopback_socket(pid + 1, port)
        self.assertFalse(udp_ownership.owns_udp_port(pid, port))
        with self.assertRaisesRegex(RuntimeError, "loopback-only UDP socket"):
            runtime.verify_loopback_socket(pid, port)

    def test_ipv4_wildcard_is_rejected_at_the_requested_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener, socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM,
        ) as wildcard:
            listener.bind(("127.0.0.1", 0))
            wildcard.bind(("0.0.0.0", 0))
            runtime.verify_loopback_socket(os.getpid(), listener.getsockname()[1])
            with self.assertRaisesRegex(RuntimeError, "loopback-only UDP socket"):
                runtime.verify_loopback_socket(os.getpid(), wildcard.getsockname()[1])

    def test_ipv6_bindings_at_the_same_pid_and_port_are_rejected(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            for address in ("::", "::1"):
                with self.subTest(address=address), socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as other:
                    other.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    other.bind((address, port))
                    with self.assertRaisesRegex(RuntimeError, "loopback-only UDP socket"):
                        runtime.verify_loopback_socket(os.getpid(), port)
            runtime.verify_loopback_socket(os.getpid(), port)
