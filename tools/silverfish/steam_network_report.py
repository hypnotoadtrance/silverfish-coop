"""Opt-in, per-session Steam message measurements for playtests."""

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
import time

from .control import resident_status
from .native_map_preflight import valid_campaign_package
from .runtime import write_json


ENABLE_FILE = "capture-steam-network"
REPORT_FILE = "steam-network-report.json"
SAMPLE_SECONDS = 1.0
MAX_GROUPS = 31
LINK_COUNTERS = (
    "quic_datagrams_sent",
    "quic_datagrams_received",
    "quic_payload_bytes_sent",
    "quic_payload_bytes_received",
    "game_payload_bytes_queued",
    "game_wire_payload_bytes_queued",
    "game_wire_payload_bytes_received",
    "game_payload_bytes_delivered",
    "snapshot_plaintext_bytes",
    "snapshot_encoded_bytes",
    "snapshot_received_bytes",
    "snapshot_responses_encoded",
    "snapshot_responses_decoded",
    "snapshot_world_delta_responses",
    "snapshot_field_delta_responses",
    "relay_queue_departed_frames",
    "relay_queue_wait_total_ns",
    "relay_queue_wait_over_20ms_frames",
    "relay_queue_wait_over_50ms_frames",
    "relay_queue_evicted_frames",
    "relay_queue_expired_frames",
    "keepalive_probes_queued",
    "keepalive_probes_acked",
    "keepalive_ticks_congested",
)


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
    link_activity: dict[str, int] = field(default_factory=dict)
    steam_activity: dict[str, int] = field(default_factory=dict)
    peak_send_interval: dict | None = None
    peak_receive_interval: dict | None = None

    def add(self, seconds, counters, rtt, group, link_activity, steam_activity):
        sent, send_bytes, received, receive_bytes = counters
        self.seconds += seconds
        self.samples += 1
        self.sent_messages += sent
        self.sent_bytes += send_bytes
        self.received_messages += received
        self.received_bytes += receive_bytes
        # The last interval can be short when the player exits.
        if seconds >= SAMPLE_SECONDS:
            send_bps = round(send_bytes * 8 / seconds)
            receive_bps = round(receive_bytes * 8 / seconds)
            send_peak = self.peak_send_bps is None or send_bps > self.peak_send_bps
            receive_peak = self.peak_receive_bps is None or receive_bps > self.peak_receive_bps
            if send_peak or receive_peak:
                interval = {"seconds": round(seconds, 3), "map": group[0], "lobby_members": group[1],
                            "sent_bytes": send_bytes, "received_bytes": receive_bytes, "heartbeat_rtt_us": rtt or None,
                            "link_activity": link_activity.copy(), "steam_activity": steam_activity.copy()}
            if send_peak:
                self.peak_send_bps = send_bps
                self.peak_send_interval = interval
            if receive_peak:
                self.peak_receive_bps = receive_bps
                self.peak_receive_interval = interval
        self.peak_heartbeat_rtt_us = max(self.peak_heartbeat_rtt_us, rtt)
        for key, amount in link_activity.items():
            self.link_activity[key] = self.link_activity.get(key, 0) + amount
        for key, amount in steam_activity.items():
            self.steam_activity[key] = self.steam_activity.get(key, 0) + amount

    def result(self):
        departed = self.link_activity.get("relay_queue_departed_frames", 0)
        wait = self.link_activity.get("relay_queue_wait_total_ns", 0)
        return {
            **asdict(self),
            "seconds": round(self.seconds, 3),
            "mean_send_bps": round(self.sent_bytes * 8 / self.seconds),
            "mean_receive_bps": round(self.received_bytes * 8 / self.seconds),
            "peak_heartbeat_rtt_us": self.peak_heartbeat_rtt_us or None,
            "mean_relay_queue_wait_us": round(wait / departed / 1000) if departed else None,
        }


class _Report:
    def __init__(self):
        self.total = _Traffic()
        self.groups = {}
        self.previous = None

    def sample(self, now, frame, counters, pressure):
        packets, lobby_members = counters[:4], counters[4]
        area = frame.get("game", {}).get("map")
        if (not frame.get("link_connected") or frame.get("state") not in ("hosting", "connected")
                or frame.get("awaiting_zone_guest") or not valid_campaign_package(area)):
            area = None
        group = area, lobby_members
        transport = frame.get("transport", {})
        observation = frame.get("generation"), frame.get("zone_epoch")
        previous, self.previous = self.previous, (now, packets, group, observation, transport, pressure)
        if not previous or now <= previous[0]:
            return
        last_time, last_packets, last_group, last_observation, last_transport, last_pressure = previous
        elapsed = now - last_time
        delta = tuple(after - before for after, before in zip(packets, last_packets, strict=True))
        steam_activity = {key: after - before for key, before in last_pressure.items()
                          if (after := pressure.get(key, before)) > before}
        if not (lobby_members or last_group[1] or any(delta) or steam_activity):
            return
        activity = {}
        rtt = 0
        if observation[0] and observation[0] == last_observation[0]:
            for key in LINK_COUNTERS:
                before, after = last_transport.get(key), transport.get(key)
                if type(before) is int and type(after) is int and after >= before >= 0:
                    activity[key] = after - before
            if (area and group == last_group and observation == last_observation
                    and transport.get("keepalive_probes_acked", 0) > last_transport.get("keepalive_probes_acked", 0)):
                sample = transport.get("keepalive_rtt_us")
                if type(sample) is int and sample > 0:
                    rtt = sample
        if (group != last_group or observation != last_observation
                or (group not in self.groups and len(self.groups) >= MAX_GROUPS)):
            group = None, None
        if group not in self.groups:
            self.groups[group] = _Traffic()
        self.groups[group].add(elapsed, delta, rtt, group, activity, steam_activity)
        self.total.add(elapsed, delta, rtt, group, activity, steam_activity)

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
        report.sample(time.monotonic(), resident_status(run) or {}, transport.traffic_counters(),
                      transport.pressure_counters())

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
        if report.total.sent_messages or report.total.received_messages or report.total.steam_activity:
            # The opt-in report is written once after the session, outside the engine thread.
            with suppress(OSError):
                await asyncio.to_thread(write_json, work / REPORT_FILE, report.result(role, transport.version))
