"""Opt-in, per-session Steam message measurements for playtests."""

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass
import time

from .control import resident_status
from .native_map_preflight import valid_campaign_package
from .runtime import write_json


ENABLE_FILE = "capture-steam-network"
REPORT_FILE = "steam-network-report.json"
SAMPLE_SECONDS = 1.0
MAX_GROUPS = 31


@dataclass(slots=True)
class _Traffic:
    seconds: float = 0
    samples: int = 0
    sent_messages: int = 0
    sent_bytes: int = 0
    received_messages: int = 0
    received_bytes: int = 0
    peak_send_bps: int | None = None
    peak_receive_bps: int | None = None
    peak_heartbeat_rtt_us: int = 0

    def add(self, seconds, counters, rtt):
        sent, send_bytes, received, receive_bytes = counters
        self.seconds += seconds
        self.samples += 1
        self.sent_messages += sent
        self.sent_bytes += send_bytes
        self.received_messages += received
        self.received_bytes += receive_bytes
        # The last interval can be short when the player exits.
        if seconds >= SAMPLE_SECONDS:
            self.peak_send_bps = max(self.peak_send_bps or 0, round(send_bytes * 8 / seconds))
            self.peak_receive_bps = max(self.peak_receive_bps or 0, round(receive_bytes * 8 / seconds))
        self.peak_heartbeat_rtt_us = max(self.peak_heartbeat_rtt_us, rtt)

    def result(self):
        return {
            **asdict(self),
            "seconds": round(self.seconds, 3),
            "mean_send_bps": round(self.sent_bytes * 8 / self.seconds),
            "mean_receive_bps": round(self.received_bytes * 8 / self.seconds),
            "peak_heartbeat_rtt_us": self.peak_heartbeat_rtt_us or None,
        }


class _Report:
    def __init__(self):
        self.total = _Traffic()
        self.groups = {}
        self.previous = None
        self.last_acks = 0

    def sample(self, now, frame, counters):
        packets, lobby_members = counters[:4], counters[4]
        area = frame.get("game", {}).get("map")
        if not frame.get("link_connected") or not valid_campaign_package(area):
            area = None
        group = area, lobby_members
        previous, self.previous = self.previous, (now, packets, group)
        if not previous or now <= previous[0]:
            return
        elapsed = now - previous[0]
        delta = tuple(after - before for after, before in zip(packets, previous[1], strict=True))
        if not (lobby_members or previous[2][1] or any(delta)):
            return
        transport = frame.get("transport", {})
        acks = transport.get("keepalive_probes_acked", 0)
        rtt = transport.get("keepalive_rtt_us", 0) if area and acks > self.last_acks else 0
        self.last_acks = max(acks, self.last_acks)
        if group != previous[2] or (group not in self.groups and len(self.groups) >= MAX_GROUPS):
            group = None, None
        if group not in self.groups:
            self.groups[group] = _Traffic()
        self.groups[group].add(elapsed, delta, rtt)
        self.total.add(elapsed, delta, rtt)

    def result(self, role, version):
        return {
            "schema": 1,
            "role": role,
            "version": version,
            "ended_unix": int(time.time()),
            "measurement": "silverfish_steam_message_bytes",
            "overall": self.total.result(),
            "by_map_and_lobby_size": [{"map": area, "lobby_members": members, **traffic.result()}
                                     for (area, members), traffic in self.groups.items()],
        }


@asynccontextmanager
async def capture(work, run, transport, role):
    if work is None or run is None or not (work / ENABLE_FILE).is_file():
        yield
        return
    report = _Report()

    def sample():
        report.sample(time.monotonic(), resident_status(run) or {}, transport.traffic_counters())

    async def watch():
        while True:
            await asyncio.sleep(SAMPLE_SECONDS)
            sample()

    sample()
    watcher = asyncio.create_task(watch())
    try:
        yield
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        sample()
        if report.total.sent_messages or report.total.received_messages:
            # The opt-in report is written once after the session, outside the engine thread.
            with suppress(OSError):
                await asyncio.to_thread(write_json, work / REPORT_FILE, report.result(role, transport.version))
