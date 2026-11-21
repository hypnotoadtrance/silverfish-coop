"""Project banked shotgun shells as local, noncolliding interaction cues."""

import re
import time

from .native_downed_markers import location
from .native_gold import valid_guid
from .enemy_loot_profiles import BY_MAP


KEY = "native_enemy_shell_view"
FRAME_KEY = "native_enemy_shell_frame"
AUTHORITY_KEY = "native_enemy_shell_authority"
FRAME = "native-enemy-shell-frame"
AUTHORITY_FRAME = "native-enemy-shell-authority-frame"
CLEAR = "native-enemy-shell-clear"
FIXTURE = "native-enemy-shell-input-fixture"
PREFIX = "enemy-shell:"
LIMIT = 2
EPOCH = re.compile(r"[0-9a-f]{32}")


def valid_epoch(value):
    return isinstance(value, str) and EPOCH.fullmatch(value) is not None and value != "0" * 32


def target_parts(value):
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return None
    epoch, _, guid = value[len(PREFIX):].partition(":")
    if valid_epoch(epoch) and valid_guid(guid):
        return epoch, guid
    return None


def parse_view(area, epoch, markers, focused):
    guids = [] if markers == "-" else markers.split(",")
    if (area in BY_MAP and valid_epoch(epoch) and 0 < len(guids) <= LIMIT
            and len(set(guids)) == len(guids) and all(valid_guid(guid) for guid in guids)
            and (focused == "-" or focused in guids)):
        return {"map": area, "epoch": epoch, "markers": guids, "focused": None if focused == "-" else focused}
    return None


def parse_authority(area, epoch, pawn, eligible):
    guids = [] if eligible == "-" else eligible.split(",")
    if (area in BY_MAP and valid_epoch(epoch) and 0 < len(pawn) <= 512 and pawn.startswith("Player_FP_C ")
            and len(guids) <= LIMIT and len(set(guids)) == len(guids) and all(valid_guid(guid) for guid in guids)):
        return {"map": area, "epoch": epoch, "pawn": pawn, "eligible": guids}
    return None


def _fields(frame, profile, epoch, *, hosting):
    if not isinstance(frame, dict) or (frame.get("map"), frame.get("epoch")) != (profile.map, epoch):
        return ()
    rows = frame.get("shells")
    if not isinstance(rows, dict) or not 0 < len(rows) <= LIMIT:
        return ()
    encoded = []
    for guid, row in rows.items():
        if (not valid_guid(guid) or not isinstance(row, (list, tuple)) or len(row) != (5 if hosting else 4)
                or type(row[0]) is not int or not 3 <= row[0] <= 9 or not location(row[1:4])):
            return ()
        if hosting and not profile.owns_shell(row[4]):
            return ()
        encoded.append("|".join(map(str, (guid, *row))))
    return tuple(sorted(encoded))


async def fixture(bridge, game, epoch, target):
    selected = target_parts(target)
    view = game.get(KEY, {})
    area = game.get("map")
    if (area not in BY_MAP or not selected or selected[0] != epoch or (view.get("map"), view.get("epoch")) != (area, epoch)
            or selected[1] not in view.get("markers", ())):
        raise ValueError("Choose an exact active shell cue in the current encounter map")
    snapshot = await bridge.command(FIXTURE, target, timeout=5)
    return snapshot, {"map": area, "epoch": epoch, "guid": selected[1]}


class ShellView:
    def __init__(self):
        self.binding = None
        self.next_update = 0.0

    async def sync(self, bridge, game, phase, epoch, frame, *, present=True):
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        version = game.get("native_enemy_shell_view_version")
        profile = BY_MAP.get(game.get("map"))
        current = (game.get("state") == phase and profile
                   and version in ((2,) if phase == "hosting" else (1, 2)) and pawn and valid_epoch(epoch))
        hosting = phase == "hosting"
        fields = _fields(frame, profile, epoch, hosting=hosting) if current and (hosting or present) else ()
        if not fields:
            old, self.binding = self.binding, None
            if old and current:
                return await bridge.command(CLEAR, timeout=2)
            return game
        command = FRAME if present else AUTHORITY_FRAME
        binding = (command, profile.id, epoch, pawn, fields)
        now = time.monotonic()
        if binding == self.binding and now < self.next_update:
            return game
        self.next_update = now + .8
        self.binding = binding
        return await bridge.command(command, epoch, pawn, *fields, timeout=2)
