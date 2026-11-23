"""Project the listen host's qualified standard flashlight to nearby peers."""

from . import native_player_avatar
from .party import REQUEST_ID


SOURCE_TAG = "native-flashlight-source"
VIEW_TAG = "native-flashlight-view"
KEY = "native_flashlight"
VIEW = "native_flashlight_view"
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


def parse_source(fields, phase):
    _, player, instance, switch, power, *axes = fields
    identity = tuple(map(int, (player, instance, switch)))
    values = list(map(float, axes))
    if phase != "hosting" or power not in ("0", "1") or not _identity(*identity) or not _values(values):
        raise ValueError("Invalid authoritative flashlight source")
    return dict(player=identity[0], instance=identity[1], switch=identity[2], power=power == "1", pose=values)


def parse_view(fields, phase):
    _, epoch, player, instance, switch, power, visible, heard = fields
    identity = tuple(map(int, (player, instance, switch)))
    heard = None if heard == "-" else int(heard)
    if (phase != "connected" or not _identifier(epoch) or power not in ("0", "1") or visible not in ("0", "1")
            or not _identity(*identity) or heard is not None and not 0 <= heard <= identity[2]):
        raise ValueError("Invalid local flashlight view receipt")
    return dict(epoch=epoch, player=identity[0], instance=identity[1], switch=identity[2],
                power=power == "1", visible=visible == "1", heard=heard)


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
            or type(incoming.get("power")) is not bool or not _values(incoming.get("pose"))):
        return game
    fields = ("epoch", "player", "instance", "switch", "power")
    expected = tuple(incoming[field] for field in fields)
    if applied and tuple(applied.get(field) for field in fields) == expected:
        return game
    result = await bridge.command(VIEW_TAG, area, epoch, str(incoming["player"]), str(incoming["instance"]),
                                  str(incoming["switch"]), str(int(incoming["power"])),
                                  *(f"{value:.5f}" for value in incoming["pose"]), timeout=2)
    local = result.get(VIEW, {})
    if tuple(local.get(field) for field in fields) != expected:
        raise RuntimeError("The guest did not accept the stock flashlight view")
    return result
