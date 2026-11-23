"""Project qualified stock handheld flashlights to other co-op players."""

import time

from . import native_player_avatar
from .party import REQUEST_ID


SOURCE_TAG = "native-flashlight-source"
VIEW_TAG = "native-flashlight-view"
KEY = "native_flashlight"
VIEW = "native_flashlight_view"
GUEST_SOURCE_TAG = "native-guest-flashlight-source"
GUEST_AUTHORITY_TAG = "native-guest-flashlight-authority"
GUEST_VIEW_TAG = "native-guest-flashlight-view"
GUEST_SOURCE = "native_guest_flashlight_source"
GUEST_AUTHORITY = "native_guest_flashlight_authority"
GUEST_VIEW = "native_guest_flashlight_view"
GUEST_BEAMS = "native_guest_flashlights"
GUEST_SUPPORT = "native_guest_flashlight_support"
MAX_GUEST_SOURCES = 16
HEAVY = "heavy"
_LEASE = 1.5
_BOUNDS = ((-250, 250),) * 3 + ((-180, 180),) * 3 + ((0, 1),) * 3


def _identity(player, instance, switch):
    return all(type(value) is int and low <= value <= 2147483647
               for value, low in ((player, 0), (instance, 1), (switch, 0)))


def _values(values):
    return isinstance(values, list) and len(values) == len(_BOUNDS) and all(
        type(value) in (int, float) and low <= value <= high
        for value, (low, high) in zip(values, _BOUNDS, strict=True))


def _identifier(epoch):
    return isinstance(epoch, str) and REQUEST_ID.fullmatch(epoch) is not None


def _power_profile(token):
    if token in ("0", "1"):
        return token == "1", None
    if token in ("0h", "1h"):
        return token == "1h", HEAVY
    raise ValueError("Invalid stock flashlight power/profile")


def _power_token(power, profile):
    return str(int(power)) + ("h" if profile == HEAVY else "")


def parse_source(fields, phase):
    _, player, instance, switch, token, *axes = fields
    power, profile = _power_profile(token)
    identity = tuple(map(int, (player, instance, switch)))
    values = list(map(float, axes))
    if phase != "hosting" or not _identity(*identity) or not _values(values):
        raise ValueError("Invalid authoritative flashlight source")
    result = dict(player=identity[0], instance=identity[1], switch=identity[2], power=power, pose=values)
    if profile:
        result["profile"] = profile
    return result


def parse_view(fields, phase):
    _, epoch, player, instance, switch, token, visible, heard = fields
    power, profile = _power_profile(token)
    identity = tuple(map(int, (player, instance, switch)))
    heard = None if heard == "-" else int(heard)
    permitted = ("connected", "hosting") if fields[0] == GUEST_VIEW_TAG else ("connected",)
    if (phase not in permitted or not _identifier(epoch) or visible not in ("0", "1")
            or not _identity(*identity) or heard is not None and not 0 <= heard <= identity[2]):
        raise ValueError("Invalid local flashlight view receipt")
    result = dict(epoch=epoch, player=identity[0], instance=identity[1], switch=identity[2],
                  power=power, visible=visible == "1", heard=heard)
    if profile:
        result["profile"] = profile
    return result


def _source_sample(values):
    return (isinstance(values, (list, tuple))
            and (len(values) == 3 or len(values) == 4 and values[3] == HEAVY)
            and _identity(0, values[0], values[1]) and type(values[2]) is bool)


def _sample_profile(sample):
    return HEAVY if len(sample) == 4 else None


def parse_guest_source(fields, phase):
    _, pawn, instance, switch, token = fields
    power, profile = _power_profile(token)
    sample = [int(instance), int(switch), power]
    if profile:
        sample.append(profile)
    if (phase != "connected" or not pawn.startswith("Player_FP_C ") or len(pawn) > 480
            or not _source_sample(sample)):
        raise ValueError("Invalid local guest flashlight source")
    return {"pawn": pawn, "sample": sample}


def parse_guest_authority(fields, phase):
    _, player, pawn, *profile = fields
    player = int(player)
    if (phase != "hosting" or not _identity(player, 1, 0) or not pawn.startswith("Player_FP_C ") or len(pawn) > 480
            or profile not in ([], [HEAVY])):
        raise ValueError("Invalid guest flashlight authority")
    return player, {"pawn": pawn, "profile": profile[0] if profile else None}


def guest_update(game, epoch):
    source = game.get(GUEST_SOURCE, {})
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    sample = source.get("sample")
    if _identifier(epoch) and pawn and source.get("pawn") == pawn and _source_sample(sample):
        return epoch, tuple(sample)


def receive_guest(host, character, pawn, sample):
    if sample is None:
        host.guest_flashlights.pop(character, None)
        return
    if (not _source_sample(sample) or host.party.get("wipe")
            or host.party.get("players", {}).get(character, {}).get("downed") is not False
            or not pawn or not any(row["pawn"] == pawn and row["profile"] == _sample_profile(sample)
                                   for row in host.last_game.get(GUEST_AUTHORITY, {}).values())):
        return
    scope = host.zone_epoch, pawn
    sample = tuple(sample)
    prior = host.guest_flashlights.get(character)
    if prior and prior[0] == scope:
        old = prior[1]
        if (sample[:2] < old[:2] or sample[:2] == old[:2] and sample[2] != old[2]
                or sample[0] == old[0] and _sample_profile(sample) != _sample_profile(old)):
            return
    host.guest_flashlights[character] = scope, sample, time.monotonic()


def peer_frame(host, exclude=None):
    game, epoch, now = host.last_game, host.zone_epoch, time.monotonic()
    if (game.get("state") != "hosting" or not isinstance(game.get("map"), str) or not _identifier(epoch)
            or host.transitioning or host.party.get("wipe") or now - host.last_seen > _LEASE):
        host.guest_flashlights.clear()
        return
    if not host.guest_flashlights:
        return
    players = host.party.get("players", {})
    members = host.native_recipients()
    ids = {row["pawn"]: (int(player), row["profile"]) for player, row in game.get(GUEST_AUTHORITY, {}).items()}
    rows, heavy_rows = [], []
    for character, (scope, state, seen) in tuple(host.guest_flashlights.items()):
        member = members.get(character, {})
        pawn = member.get("pawn")
        player, profile = ids.get(pawn, (None, None))
        if (scope != (epoch, pawn) or not member.get("connected") or now - seen > _LEASE
                or players.get(character, {}).get("downed") is not False or player is None or _sample_profile(state) != profile):
            del host.guest_flashlights[character]
        elif character != exclude:
            (heavy_rows if profile == HEAVY else rows).append([player, *state[:3]])
    if 0 < len(rows) + len(heavy_rows) <= MAX_GUEST_SOURCES:
        result = {"map": game["map"], "epoch": epoch, "sources": sorted(rows)}
        if heavy_rows:
            result["heavy_sources"] = sorted(heavy_rows)
        return result


async def project_peers(bridge, game, incoming, epoch):
    area, applied = game.get("map"), game.get(GUEST_VIEW, {})
    if game.get("state") not in ("hosting", "connected") or not isinstance(area, str) or not _identifier(epoch):
        return game
    if incoming is None:
        if not applied:
            return game
        expected = {}
    else:
        if not isinstance(incoming, dict) or (incoming.get("map"), incoming.get("epoch")) != (area, epoch):
            return game
        expected = {}
        rows, heavy_rows = incoming.get("sources"), incoming.get("heavy_sources", [])
        if (not isinstance(rows, list) or not isinstance(heavy_rows, list)
                or not 0 < len(rows) + len(heavy_rows) <= MAX_GUEST_SOURCES):
            return game
        for group, profile in ((rows, None), (heavy_rows, HEAVY)):
            for row in group:
                if (not isinstance(row, list) or len(row) != 4 or not _identity(*row[:3])
                        or type(row[3]) is not bool or str(row[0]) in expected):
                    return game
                expected[str(row[0])] = epoch, *row[1:], profile

    def assigned(value):
        return {key: (row["epoch"], row["instance"], row["switch"], row["power"], row.get("profile"))
                for key, row in value.items()}

    if assigned(applied) == expected:
        return game
    fields = [str(value) for player, (_, instance, switch, power, profile) in expected.items()
              for value in (player, instance, switch, _power_token(power, profile))]
    result = await bridge.command(GUEST_VIEW_TAG, area, epoch, *fields, timeout=2)
    if assigned(result.get(GUEST_VIEW, {})) != expected:
        raise RuntimeError("The observer did not accept the guest flashlight views")
    return result


def frame(host):
    game = host.last_game
    source = game.get(KEY)
    if (game.get("state") == "hosting" and isinstance(game.get("map"), str) and _identifier(host.zone_epoch) and source
            and not host.transitioning and not host.party.get("wipe") and not host.party["players"]["host"].get("downed")):
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        mapped = game.get(native_player_avatar.KEY, {}).get(str(source["player"]), {}).get("pawn")
        if pawn and mapped == pawn:
            return dict(source, map=game["map"], epoch=host.zone_epoch)


async def project(bridge, game, incoming, epoch):
    area = game.get("map")
    applied = game.get(VIEW)
    if game.get("state") != "connected" or not isinstance(area, str):
        return game
    if incoming is None:
        if not applied:
            return game
        result = await bridge.command(VIEW_TAG, area, applied["epoch"], timeout=2)
        if VIEW in result:
            raise RuntimeError("The guest did not clear the stock flashlight view")
        return result
    if (not isinstance(incoming, dict) or (incoming.get("map"), incoming.get("epoch")) != (area, epoch)
            or not _identifier(epoch) or not _identity(incoming.get("player"), incoming.get("instance"), incoming.get("switch"))
            or type(incoming.get("power")) is not bool or incoming.get("profile") not in (None, HEAVY)
            or not _values(incoming.get("pose"))):
        return game
    fields = ("epoch", "player", "instance", "switch", "power", "profile")
    expected = tuple(incoming.get(field) for field in fields)
    if applied and tuple(applied.get(field) for field in fields) == expected:
        return game
    result = await bridge.command(VIEW_TAG, area, epoch, str(incoming["player"]), str(incoming["instance"]),
                                  str(incoming["switch"]), _power_token(incoming["power"], incoming.get("profile")),
                                  *(f"{value:.5f}" for value in incoming["pose"]), timeout=2)
    local = result.get(VIEW, {})
    if tuple(local.get(field) for field in fields) != expected:
        raise RuntimeError("The guest did not accept the stock flashlight view")
    return result
