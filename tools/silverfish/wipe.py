"""Pure decisions for a campaign's locked party wipe and checkpoint respawn."""

from __future__ import annotations

from copy import deepcopy
import json
import math
import uuid


class WipeError(ValueError):
    """A wipe or item-selection request was refused."""


def _finite(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _position(state: object) -> dict | None:
    if not isinstance(state, dict):
        return None
    zone = state.get("map")
    location = state.get("location")
    if not isinstance(zone, str) or not zone:
        return None
    if not isinstance(location, (list, tuple)) or len(location) != 3 or not all(map(_finite, location)):
        return None
    return {"map": zone, "location": list(location)}


def _inventory(items: object) -> dict[str, int]:
    if not isinstance(items, dict) or any(
        not isinstance(item, str) or not item or type(count) is not int or count < 1
        for item, count in items.items()
    ):
        raise WipeError("invalid_inventory")
    return items


def begin_wipe(
    players: dict,
    active_ids: tuple[str, ...],
    checkpoint: dict,
    now: float,
    *,
    keep_slots: int | None,
    drop_backpacks: bool,
    selection_seconds: float,
    pawns: dict | None = None,
) -> dict | None:
    """Freeze the active and in-grace roster once every character is downed.

    Persist the returned JSON-compatible draft as one campaign update. Pass the
    same wall-clock time base into selection and completion so the deadline
    survives launcher restarts. A slot covers one item kind with a chosen stack
    count. None leaves inventories intact. Zero makes all items ineligible.
    """
    roster = sorted(set(active_ids))
    if not roster or any(players.get(actor, {}).get("downed") is not True for actor in roster):
        return None
    rest = _position(checkpoint)
    if rest is None:
        raise WipeError("invalid_checkpoint")
    if keep_slots is not None and (type(keep_slots) is not int or keep_slots < 0):
        raise WipeError("invalid_keep_slots")
    if type(drop_backpacks) is not bool:
        raise WipeError("invalid_backpack_setting")
    if not _finite(now) or not _finite(selection_seconds) or selection_seconds < 0:
        raise WipeError("invalid_selection_time")
    deadline = now + selection_seconds if keep_slots else now
    if not _finite(deadline):
        raise WipeError("invalid_selection_time")

    snapshot = {}
    drop_positions = {}
    for actor in roster:
        player = players[actor]
        if not _finite(player.get("max_health")) or player["max_health"] <= 0:
            raise WipeError("invalid_max_health")
        _inventory(player.get("inventory"))
        snapshot[actor] = deepcopy(player)
        if pawns is not None:
            position = _position(pawns.get(actor))
            if position is not None:
                drop_positions[actor] = position

    seed = json.dumps([float(now), roster, rest], sort_keys=True, separators=(",", ":"))
    return {
        "schema": 1,
        "id": uuid.uuid5(uuid.NAMESPACE_OID, seed).hex,
        "roster": roster,
        "checkpoint": rest,
        "deadline": deadline,
        "keep_slots": keep_slots,
        "drop_backpacks": drop_backpacks,
        "players": snapshot,
        "drop_positions": drop_positions,
        "choices": {},
    }


def _validate_schema(wipe: dict) -> None:
    if not isinstance(wipe, dict) or wipe.get("schema") != 1:
        raise WipeError("unsupported_wipe_schema")


def _choice_complete(wipe: dict) -> bool:
    return all(actor in wipe["choices"] for actor in wipe["roster"])


def _validate_now(now: float) -> None:
    if not _finite(now):
        raise WipeError("invalid_selection_time")


def select_items(wipe: dict, actor_id: str, retained: dict[str, int], *, now: float | None = None) -> dict:
    """Return the updated selection draft. Pass the host clock to reject late input."""
    _validate_schema(wipe)
    if not wipe["keep_slots"]:
        raise WipeError("selection_unavailable")
    if now is not None:
        _validate_now(now)
    if _choice_complete(wipe) or (now is not None and now >= wipe["deadline"]):
        raise WipeError("selection_closed")
    if actor_id not in wipe["players"]:
        raise WipeError("actor_unavailable")
    selected = _inventory(retained)
    if len(selected) > wipe["keep_slots"]:
        raise WipeError("too_many_items")
    frozen = wipe["players"][actor_id]["inventory"]
    if any(count > frozen.get(item, 0) for item, count in selected.items()):
        raise WipeError("item_unavailable")
    candidate = deepcopy(wipe)
    candidate["choices"][actor_id] = dict(selected)
    return candidate


def finish_wipe(wipe: dict, now: float) -> dict | None:
    """Compute an idempotent respawn result after all submissions or the deadline.

    Restore the returned players and merge backpacks by ID in the same durable
    update that clears the draft. Use owner_id to authorize backpack access.
    If a death position was unavailable the record holds a null position.
    """
    _validate_schema(wipe)
    _validate_now(now)
    if wipe["keep_slots"] and now < wipe["deadline"] and not _choice_complete(wipe):
        return None
    players = {}
    backpacks = {}
    namespace = uuid.UUID(hex=wipe["id"])
    for actor in wipe["roster"]:
        player = deepcopy(wipe["players"][actor])
        inventory = player["inventory"]
        kept = dict(inventory if wipe["keep_slots"] is None else wipe["choices"].get(actor, {}))
        lost = {item: count - kept.get(item, 0) for item, count in inventory.items()
                if count > kept.get(item, 0)}
        if lost and wipe["drop_backpacks"]:
            backpack_id = uuid.uuid5(namespace, actor).hex
            backpacks[backpack_id] = {
                "id": backpack_id,
                "owner_id": actor,
                "inventory": lost,
                "position": deepcopy(wipe["drop_positions"].get(actor)),
            }
        player.update(health=player["max_health"], downed=False, inventory=kept)
        players[actor] = player

    return {
        "wipe_id": wipe["id"],
        "players": players,
        "backpacks": backpacks,
        "checkpoint": deepcopy(wipe["checkpoint"]),
    }
