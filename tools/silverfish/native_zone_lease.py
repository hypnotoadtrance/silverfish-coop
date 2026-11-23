"""Keep stock zone, rest, and transport hooks armed while the host is current."""

import time

from .runtime import write_text


HEARTBEAT = "native-zone-heartbeat"
ROSTER = "native-zone-roster"


def memory_bridge(bridge):
    inbox = getattr(bridge, "local_bridge", None)
    return inbox if getattr(inbox, "native_zone_lease_available", False) else None


def renew(bridge, roster: str | None = None) -> None:
    timestamp = int(time.time())
    inbox = memory_bridge(bridge)
    if inbox is not None:
        if not inbox.publish_native_zone_lease(timestamp, roster):
            raise RuntimeError("The in-process stock zone lease is unavailable")
        return
    if roster is not None:
        write_text(bridge.directory / ROSTER, roster)
    write_text(bridge.directory / HEARTBEAT, f"{timestamp}\n")
