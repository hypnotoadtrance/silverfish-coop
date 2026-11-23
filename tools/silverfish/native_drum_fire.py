"""Carry the current stock drum fire presentation over the authenticated snapshot."""

import re

from .native_guest_drop_record import valid_id


KEY = "native_drum_fire"
VIEW = "native_drum_fire_view"
MAX_FIELDS = 4
MAX_PARTICLES = 64
MAX_FIELD_BYTES = 6400
_CHUNK_BYTES = 960
_MAX_REVISION = 2**31 - 1
_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]*)?")
_TRANSFORM_LIMITS = ((-1e7, 1e7),) * 3 + ((-360, 360),) * 3 + ((.001, 10),) * 3


def _values(text, limits):
    values = text.split(",")
    return len(values) == len(limits) and all(
        len(value) <= 15 and _NUMBER.fullmatch(value) and low <= float(value) <= high
        for value, (low, high) in zip(values, limits, strict=True)
    )


def _serial(value, maximum):
    return (type(value) is str and len(value) <= 10 and value.isascii() and value.isdecimal()
            and 0 < int(value) <= maximum)


def _fields(fields):
    if type(fields) is not list or len(fields) > MAX_FIELDS:
        return False
    seen_fields = set()
    for encoded in fields:
        if type(encoded) is not str or len(encoded) > MAX_FIELD_BYTES:
            return False
        chunks = encoded.split("|")
        if len(chunks) != 3:
            return False
        identity, center, content = chunks
        if not _serial(identity, _MAX_REVISION) or int(identity) in seen_fields or not _values(center, ((-1e7, 1e7),) * 2):
            return False
        seen_fields.add(int(identity))
        particles = content.split(";")
        if len(particles) > MAX_PARTICLES:
            return False
        seen_particles = set()
        for particle in particles:
            number, separator, transform = particle.partition(",")
            if (not separator or not _serial(number, MAX_PARTICLES) or int(number) in seen_particles
                    or not _values(transform, _TRANSFORM_LIMITS)):
                return False
            seen_particles.add(int(number))
    return True


def parse_host(revision, fields):
    if _serial(revision, _MAX_REVISION) and _fields(fields):
        return {"revision": int(revision), "fields": fields}


def host_frame(game, epoch):
    view = game.get(KEY)
    if game.get("state") == "hosting" and view and valid_id(epoch):
        return {"map": game["map"], "epoch": epoch, **view}


async def project(bridge, game, frame, epoch):
    area = game.get("map")
    if (game.get("state") != "connected" or type(frame) is not dict or not valid_id(epoch)
            or (frame.get("map"), frame.get("epoch")) != (area, epoch)):
        return game
    revision = frame.get("revision")
    if type(revision) is not int or not 0 < revision <= _MAX_REVISION:
        return game
    local = game.get(VIEW, {})
    if local.get("epoch") == epoch and local.get("revision", 0) >= revision:
        return game
    fields = frame.get("fields")
    if not _fields(fields):
        return game
    # Keep each argument within Bridge.command's limit while applying the view as one ticket.
    payload = "~".join(fields)
    chunks = [payload[offset:offset + _CHUNK_BYTES] for offset in range(0, len(payload), _CHUNK_BYTES)]
    return await bridge.command("native-drum-fire-view", area, epoch, str(revision), *chunks, timeout=1.5)
