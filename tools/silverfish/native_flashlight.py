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
OFFHAND = "offhand"
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


def _power_profile(token, *, offhand=False):
    if token in ("0", "1"):
        return token == "1", None
    if token in ("0h", "1h"):
        return token == "1h", HEAVY
    if offhand and token in ("0o", "1o"):
        return token == "1o", OFFHAND
    raise ValueError("Invalid stock flashlight power/profile")


def _power_token(power, profile):
    return str(int(power)) + {HEAVY: "h", OFFHAND: "o"}.get(profile, "")


def parse_source(fields, phase):
    _, player, instance, switch, token, *axes = fields
    power, profile = _power_profile(token, offhand=True)
    identity = tuple(map(int, (player, instance, switch)))
    values = list(map(float, axes))
    if phase != "hosting" or not _identity(*identity) or not _values(values):
        raise ValueError("Invalid authoritative flashlight source")
    result = dict(player=identity[0], instance=identity[1], switch=identity[2], power=power, pose=values)
    if profile:
        result["profile"] = profile
    return result


def parse_view(fields, phase):
    tag, epoch, player, instance, switch, token, visible, heard, *direction = fields
    power, profile = _power_profile(token, offhand=tag in (VIEW_TAG, GUEST_VIEW_TAG))
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
    if profile == OFFHAND:
        aim = list(map(float, direction))
        if len(aim) != 3 or any(not -180 <= axis <= 180 for axis in aim):
            raise ValueError("Invalid offhand flashlight direction receipt")
        result["aim"] = aim
    elif direction:
        raise ValueError("Unexpected handheld flashlight direction receipt")
    return result


def _source_sample(values):
    if (not isinstance(values, (list, tuple)) or len(values) < 3
            or not _identity(0, values[0], values[1]) or type(values[2]) is not bool):
        return False
    return (len(values) == 3 or len(values) == 4 and values[3] == HEAVY
            or len(values) == 7 and values[3] == OFFHAND and _directions(values[4:]))


def _directions(angles):
    return len(angles) == 3 and all(type(axis) in (int, float) and -180 <= axis <= 180 for axis in angles)


def _sample_profile(sample):
    return sample[3] if len(sample) > 3 else None


def parse_guest_source(fields, phase):
    _, pawn, instance, switch, token, *direction = fields
    power, profile = _power_profile(token, offhand=True)
    sample = [int(instance), int(switch), power]
    if profile:
        sample.append(profile)
    if profile == OFFHAND:
        sample.extend(map(float, direction))
    elif direction:
        raise ValueError("Unexpected guest handheld flashlight direction")
    if (phase != "connected" or not pawn.startswith("Player_FP_C ") or len(pawn) > 480
            or not _source_sample(sample)):
        raise ValueError("Invalid local guest flashlight source")
    return {"pawn": pawn, "sample": sample}


def parse_guest_authority(fields, phase):
    _, player, pawn, *profile = fields
    player = int(player)
    if (phase != "hosting" or not _identity(player, 1, 0) or not pawn.startswith("Player_FP_C ") or len(pawn) > 480
            or profile not in ([], [HEAVY], [OFFHAND])):
        raise ValueError("Invalid guest flashlight authority")
    return player, {"pawn": pawn, "profile": profile[0] if profile else None}


def guest_update(game, epoch):
    source = game.get(GUEST_SOURCE, {})
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    sample = source.get("sample")
    if _identifier(epoch) and pawn and source.get("pawn") == pawn and _source_sample(sample):
        return epoch, tuple(sample)


def guest_edge(update):
    if update is not None:
        epoch, sample = update
        return epoch, sample[:4]


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
    rows = {None: [], HEAVY: [], OFFHAND: []}
    for character, (scope, state, seen) in tuple(host.guest_flashlights.items()):
        member = members.get(character, {})
        pawn = member.get("pawn")
        player, profile = ids.get(pawn, (None, None))
        if (scope != (epoch, pawn) or not member.get("connected") or now - seen > _LEASE
                or players.get(character, {}).get("downed") is not False or player is None or _sample_profile(state) != profile):
            del host.guest_flashlights[character]
        elif character != exclude:
            rows[profile].append([player, *state[:3], *state[4:]])
    if 0 < sum(map(len, rows.values())) <= MAX_GUEST_SOURCES:
        result = {"map": game["map"], "epoch": epoch, "sources": sorted(rows[None])}
        if rows[HEAVY]:
            result["heavy_sources"] = sorted(rows[HEAVY])
        if rows[OFFHAND]:
            result["offhand_sources"] = sorted(rows[OFFHAND])
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
        groups = {None: incoming.get("sources"), HEAVY: incoming.get("heavy_sources", []),
                  OFFHAND: incoming.get("offhand_sources", [])}
        if (any(not isinstance(rows, list) for rows in groups.values())
                or not 0 < sum(map(len, groups.values())) <= MAX_GUEST_SOURCES):
            return game
        for profile, rows in groups.items():
            for row in rows:
                if (not isinstance(row, list) or len(row) != (7 if profile == OFFHAND else 4)
                        or not _identity(*row[:3]) or type(row[3]) is not bool or str(row[0]) in expected
                        or profile == OFFHAND and not _directions(row[4:])):
                    return game
                expected[str(row[0])] = epoch, *row[1:4], profile, row[4:] if profile == OFFHAND else None

    def assigned(value):
        return {key: (row["epoch"], row["instance"], row["switch"], row["power"], row.get("profile"), row.get("aim"))
                for key, row in value.items()}

    if assigned(applied) == expected:
        return game
    fields = [str(value) for player, (_, instance, switch, power, profile, aim) in expected.items()
              for value in (player, instance, switch, _power_token(power, profile), *(aim or ()))]
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
            or type(incoming.get("power")) is not bool or incoming.get("profile") not in (None, HEAVY, OFFHAND)
            or not _values(incoming.get("pose"))):
        return game
    fields = ("epoch", "player", "instance", "switch", "power", "profile")
    aim = incoming["pose"][3:6] if incoming.get("profile") == OFFHAND else None
    expected = tuple(incoming.get(field) for field in fields) + (aim,)

    def accepted(state):
        return tuple(state.get(field) for field in fields) + (state.get("aim"),) == expected

    if applied and accepted(applied):
        return game
    result = await bridge.command(VIEW_TAG, area, epoch, str(incoming["player"]), str(incoming["instance"]),
                                  str(incoming["switch"]), _power_token(incoming["power"], incoming.get("profile")),
                                  *(f"{value:.5f}" for value in incoming["pose"]), timeout=2)
    local = result.get(VIEW, {})
    if not accepted(local):
        raise RuntimeError("The guest did not accept the stock flashlight view")
    return result
