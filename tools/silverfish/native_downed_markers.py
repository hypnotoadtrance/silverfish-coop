"""Project the exact downed party's positions without relying on replica names."""

import re
import time


KEY = "native_downed_markers"
COMMAND = "player-marker-frame"
MAX_MARKERS = 4
MAX_COORDINATE = 10_000_000
CHARACTER = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
EPOCH = re.compile(r"[0-9a-f]{32}")


def character_id(value) -> bool:
    return value == "host" or isinstance(value, str) and CHARACTER.fullmatch(value) is not None


def location(value) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 3
            and all(type(axis) in (int, float) and abs(axis) <= MAX_COORDINATE for axis in value))


def frame(known, positions, area, epoch, viewer) -> dict:
    markers = {}
    for player in known.get("players", {}).values():
        character = player["character_id"]
        if character == viewer or player["downed"] is not True or character in known.get("pending_spawns", {}):
            continue
        pawn = positions.get(character, {})
        point = pawn.get("location")
        if pawn.get("map") == area and character_id(character) and location(point):
            markers[character] = point
    return {"epoch": epoch, "map": area, "markers": markers}


class MarkerView:
    def __init__(self) -> None:
        self.next_update = 0.0

    async def sync(self, client) -> None:
        now = time.monotonic()
        host = client.gameplay.get(KEY)
        area, epoch = client.last_game.get("map"), client.zone_epoch
        if (now < self.next_update or now - client.gameplay_seen >= 2
                or not client.transport.game_access or not isinstance(host, dict)
                or not isinstance(epoch, str) or not EPOCH.fullmatch(epoch)
                or not isinstance(area, str) or area != client.expected_zone
                or (host.get("map"), host.get("epoch")) != (area, epoch)):
            return
        markers = host.get("markers")
        viewer = client.invite["character_id"]
        downed = {p.get("character_id") for p in client.gameplay.get("players", {}).values() if p.get("downed") is True}
        if (not isinstance(markers, dict) or len(markers) > MAX_MARKERS or viewer in markers or markers.keys() - downed
                or any(not character_id(character) or not location(point) for character, point in markers.items())):
            return
        self.next_update = now + .8
        fields = tuple(field for character, point in sorted(markers.items()) for field in (character, *map(str, point)))
        await client.bridge.command(COMMAND, epoch, area, *fields, timeout=2)


def parse(frame_row, marker_rows, area) -> dict:
    _, epoch, map_name, count = frame_row
    if not EPOCH.fullmatch(epoch) or map_name != area or not count.isascii() or not count.isdecimal():
        raise ValueError("Invalid downed marker frame")
    if not 0 <= int(count) == len(marker_rows) <= MAX_MARKERS:
        raise ValueError("Invalid downed marker count")
    markers = {}
    for _, mark_epoch, mark_map, character, *point in marker_rows:
        if (mark_epoch, mark_map) != (epoch, area) or not character_id(character) or character in markers:
            raise ValueError("Invalid downed marker identity")
        point = [float(axis) for axis in point]
        if not location(point):
            raise ValueError("Invalid downed marker position")
        markers[character] = point
    return {"epoch": epoch, "map": area, "count": len(markers), "markers": markers}
