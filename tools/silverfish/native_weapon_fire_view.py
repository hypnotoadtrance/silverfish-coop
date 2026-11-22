"""Relay short-lived cosmetic cues from the host's stock firearm shots."""

import re

from .native_guest_drop_record import valid_id, valid_position


KEY = "native_weapon_shots"
LIMIT = 36
_STOCK_CLASS = re.compile(r"/Game/Classes/Weapons/(?:[\w-]+/)*([\w-]+)\.\1_C", re.ASCII)


def valid_shot(cue):
    if type(cue) is not list or len(cue) != 11:
        return False
    serial, path, *pose = cue
    return (type(serial) is int and 0 < serial < 10**15
            and type(path) is str and len(path) <= 160 and _STOCK_CLASS.fullmatch(path)
            and valid_position(pose[:3])
            and all(type(angle) in (int, float) and -180 <= angle <= 180 for angle in pose[3:6])
            and all(type(value) in (int, float) and low <= value <= high
                    for value, (low, high) in zip(pose[6:], ((0, 10), (0, 10), (-26, 24)), strict=True)))


class Playback:
    def __init__(self):
        self.scope = None
        self.last_played = 0

    async def sync(self, bridge, control, game, frame, epoch):
        if (not valid_id(epoch) or not isinstance(frame, dict)
                or (frame.get("map"), frame.get("epoch")) != (game.get("map"), epoch)):
            return game
        cues = frame.get("cues")
        if type(cues) is not list or not 0 < len(cues) <= LIMIT:
            return game
        if epoch != self.scope:
            self.scope, self.last_played = epoch, 0

        selected = {cue[0]: cue[1:] for cue in cues if valid_shot(cue) and cue[0] > self.last_played}
        if not selected:
            return game
        arguments = ["|".join((str(serial), path, *(f"{value:.2f}" for value in pose)))
                     for serial, (path, *pose) in sorted(selected.items())]
        try:
            result = await bridge.command("native-weapon-play-shots", game["map"], epoch, *arguments, timeout=1.5)
        except (OSError, RuntimeError, TimeoutError) as error:
            control.event("native_weapon_shots_unavailable", reason=str(error))
            return game
        self.last_played = max(selected)
        for receipt in result.get("ack", {}).get("message", "").split(","):
            match = re.fullmatch(r"([0-9]{1,15}):([01]):([01]):([01])", receipt)
            if match and (serial := int(match[1])) in selected:
                control.event("native_weapon_shot_played", serial=serial, weapon=selected[serial][0],
                              muzzle_flash=match[2] == "1", audio_component=match[3] == "1", stock_sound=match[4] == "1")
        return result
