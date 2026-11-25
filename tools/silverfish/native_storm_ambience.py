"""Carry a bounded stock storm presentation between matching co-op visits."""

import time

from .native_guest_drop_record import valid_id


KEY = "native_weather_ambience"
GUEST = "native_weather_ambience_guest"
GUARD = "native_weather_storm_guard"
HOST_TAG = "native-weather-ambience"
GUEST_TAG = "native-weather-ambience-guest"
HOST_FOG_TAG = "native-weather-ambience-fog"
GUEST_FOG_TAG = "native-weather-ambience-guest-fog"
COMMAND = "native-weather-ambience-apply"
REPLY = "native-weather-ambience-reply"
GUARD_TAG = "native-weather-storm-guard"

_STORM = "/Game/Classes/Weather/Storms/"
_PFX = "/Game/Assets/PFX/"
_WAV = "/Game/Assets/Sounds/WAVs/"
_MATERIAL = "/Game/Assets/Materials/Mat_Functions/"
_WIND = "Ambiant_Wav/Exterior_Amb/Wind_Strong_01"
_FIELDS = ("phase", "variant", "level", "blend", "fade", "particle", "sound", "volume")
_FOG_FIELDS = ("material", "density", "start_distance", "r", "g", "b", "a")
_COMPONENTS = ("particle_registered", "audio_registered", "fog_registered")


def _asset(base, name):
    return f"{base}{name}.{name.rsplit('/', 1)[-1]}" if name else None


_VARIANTS = {
    _asset(_STORM, variant) + "_C": (_asset(_PFX, particle), _asset(_WAV, sound), _asset(_MATERIAL, material))
    for variant, particle, sound, material in (
        ("StormMaster_BP", "NI_AnomStorm_01", _WIND, "Mat_CustomFog"),
        ("LightningStorm_BP", "NI_LightningStorm", _WIND, None),
        ("TheFog_BP", None, None, None),
        ("EatherFlare_BP", None, _WIND, "Mat_FlareFog"),
        ("NightLightStorm_BP", None, "Ambiant_Wav/Ambience_Psychedelic_Loop_002", None),
        ("PestilenceStorm_BP", None, "Anomaly_Wav/578267__brunoauzet__black-bees-swarm", "Mat_PestilenceFog"),
    )
}


def _bounded(value, top):
    return type(value) in (int, float) and 0 <= value <= top


def _valid_fog(variant, fog):
    return (variant in _VARIANTS and isinstance(fog, dict) and _VARIANTS[variant][2] is not None
            and fog.get("material") == _VARIANTS[variant][2]
            and _bounded(fog.get("density"), 1) and _bounded(fog.get("start_distance"), 100_000)
            and all(_bounded(fog.get(field), 1) for field in ("r", "g", "b", "a")))


def valid(frame):
    if not isinstance(frame, dict):
        return False
    phase = frame.get("phase")
    if phase == "idle":
        return all(field not in frame for field in (*_FIELDS[1:], "fog"))
    variant = frame.get("variant")
    if (phase not in ("active", "fading") or not isinstance(variant, str) or variant not in _VARIANTS
            or any(field not in frame for field in _FIELDS)):
        return False
    particle, sound = frame.get("particle"), frame.get("sound")
    return (type(frame.get("level")) is int and 0 <= frame["level"] <= 3
            and _bounded(frame.get("blend"), 1) and _bounded(frame.get("fade"), 60)
            and particle in (None, _VARIANTS[variant][0]) and sound in (None, _VARIANTS[variant][1])
            and _bounded(frame.get("volume"), 2) and (sound is not None or frame["volume"] == 0)
            and ("fog" not in frame or _valid_fog(variant, frame["fog"])))


def _optional(path):
    return None if path == "-" else path


def parse_host(fields):
    if fields == [HOST_TAG, "idle"]:
        return {"phase": "idle"}
    _, phase, variant, level, blend, fade, particle, sound, volume = fields
    frame = dict(phase=phase, variant=variant, level=int(level), blend=float(blend), fade=float(fade),
                 particle=_optional(particle), sound=_optional(sound), volume=float(volume))
    if not valid(frame):
        raise ValueError("Unqualified stock storm ambience")
    return frame


def parse_guest(fields):
    _, epoch, phase, *report = fields
    if not valid_id(epoch):
        raise ValueError("Unqualified stock storm visit")
    if phase == "idle" and not report:
        return {"phase": "idle", "epoch": epoch}
    variant, level, blend, particle, sound, volume, *flags = report
    frame = parse_host([HOST_TAG, phase, variant, level, blend, "0", particle, sound, volume])
    del frame["fade"]
    if len(flags) != len(_COMPONENTS) or any(flag not in ("0", "1") for flag in flags):
        raise ValueError("Unqualified stock storm playback")
    return {**frame, "epoch": epoch, **dict(zip(_COMPONENTS, (flag == "1" for flag in flags), strict=True))}


def add_fog(frame, fields, *, guest=False):
    row = fields[1:]
    if guest and row.pop(0) != frame.get("epoch"):
        raise ValueError("Storm fog playback belongs to another visit")
    variant, material, *values = row
    fog = dict(zip(_FOG_FIELDS, (material, *map(float, values)), strict=True))
    if (variant != frame.get("variant") or "fog" in frame or frame.get("phase") not in ("active", "fading")
            or not _valid_fog(variant, fog)):
        raise ValueError("Unqualified stock storm fog")
    frame["fog"] = fog


def _args(frame):
    if frame["phase"] == "idle":
        return ("idle",)
    values = [frame[field] for field in _FIELDS]
    if "fog" in frame:
        values.extend(frame["fog"][field] for field in _FOG_FIELDS)
    return tuple("-" if value is None else str(value) for value in values)


class Projection:
    def __init__(self):
        self.scope = None
        self.args = None
        self.retry_at = 0.0

    async def sync(self, bridge, control, game, frame, epoch, snapshot_age):
        area, view = game.get("map"), game.get("native_world_view", {})
        if (game.get("state") != "connected" or not valid_id(epoch) or not valid(frame) or snapshot_age >= 2
                or (frame.get("epoch"), frame.get("map")) != (epoch, area)
                or (view.get("mode"), view.get("epoch"), view.get("map")) != ("applied", epoch, area)):
            return game
        scope = area, epoch
        if scope != self.scope:
            self.scope, self.args, self.retry_at = scope, None, 0.0
        arguments = _args(frame)
        now = time.monotonic()
        if now < self.retry_at and (self.args == arguments or self.args is None):
            return game

        try:
            result = await bridge.command(COMMAND, epoch, *arguments, timeout=1.5)
            local_scope = result.get("native_weather_ambience_local_scope")
            if local_scope is not None:
                sequence, pawn = local_scope
                if (result.get("map") != area or game.get("sequence", 0) > sequence
                        or game.get("pawns", {}).get("local", {}).get("name") != pawn):
                    raise RuntimeError("The storm ambience reply belongs to another guest or visit")
        except (OSError, RuntimeError) as error:
            self.args, self.retry_at = None, now + 1
            control.event("native_weather_ambience_unavailable", reason=str(error))
            return game
        if (result.get("state"), result.get("map")) != ("connected", area):
            return result
        proof = result.get(GUEST, {})
        expected = "cleared" if frame["phase"] == "idle" else "projected"
        if (result.get("ack", {}).get("message") != expected
                or (proof.get("epoch"), proof.get("phase"), proof.get("variant")) != (
                    epoch, frame["phase"], frame.get("variant"))):
            self.args, self.retry_at = None, now + 1
            control.event("native_weather_ambience_unavailable", reason="stock ambience readback did not match")
            return game if local_scope is not None else result
        if self.args is None or self.args[:2] != arguments[:2]:
            control.event("native_weather_ambience_applied", epoch=epoch, phase=frame["phase"],
                          variant=frame.get("variant"))
        self.args, self.retry_at = arguments, now + 1
        if local_scope is not None:
            return {**game, GUEST: proof}
        return result
