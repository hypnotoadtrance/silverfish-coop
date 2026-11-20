"""Exercise the stock west landing after both players ride the Data Silo lift."""

import asyncio
import math

from tools.silverfish import native_datasilo_lift as lift, native_mindcore as core, runtime


# The cooked deck has a narrow west opening on its positive-Y side. At the
# capsule's center, the stock radius of 30 leaves roughly Y 56.4 through 72.4.
EXIT_Y = 64.0
ALIGN_TOLERANCE = 5.0
PARKED_ROOT = (-1780.0, 0.0, 2400.0)
POLL_SECONDS = 0.10


def _position(pawn):
    location = pawn.get("location")
    if (not isinstance(location, list) or len(location) != 3
            or any(type(axis) not in (float, int) or not math.isfinite(axis) for axis in location)):
        raise RuntimeError("Missing measured pawn position on the stock Data Silo lift")
    return location


def _safe_landing(position):
    x, y, z = position
    return -2300 <= x <= -1875 and 36 <= y <= 106 and 2470 <= z <= 2520


def _brief(frame):
    return {seat: {"position": [round(axis, 2) for axis in row["position"]],
                   "on_deck": row["on_deck"], "state": row["state"]}
            for seat, row in frame["pawns"].items()}


async def verify_disembark(run, guest_run, action, host_pawn, guest_pawn, character, zone_epoch) -> dict:
    """Keep the lift parked and use only bounded local walks to leave its deck."""
    if not all(isinstance(value, str) and value for value in (host_pawn, guest_pawn, character, zone_epoch)):
        raise ValueError("Stock lift disembark requires the original two seats and visit")
    guest_local_name = None
    study = {}
    deadline = asyncio.get_running_loop().time() + 90

    def observe():
        nonlocal guest_local_name
        host = runtime.read_json(run / "status.json")
        guest = runtime.read_json(guest_run / "status.json")
        authority, peer = host.get("game", {}), guest.get("game", {})
        if ((host.get("state"), guest.get("state"), guest.get("link_connected"),
             host.get("zone_epoch"), guest.get("zone_epoch"), guest.get("character_id"),
             guest.get("host_session")) != ("hosting", "connected", True, zone_epoch, zone_epoch,
                                             character, host.get("generation"))
                or (authority.get("map"), peer.get("map")) != (core.MAP, core.MAP)):
            raise RuntimeError("Stock lift disembark lost the original connected Data Silo visit")
        if (authority.get("native_guest_save", {}).get("pawn") != guest_pawn
                or authority.get("native_guest_save", {}).get("state") != "bound"
                or not any(row.get("character_id") == character and row.get("state") == "active"
                           for row in host.get("campaign", {}).get("leases", ()))):
            raise RuntimeError("Stock lift disembark lost the original enrolled guest pawn")
        for game, expected_role, expected_state in ((authority, 3, "active"), (peer, 1, "guarded")):
            car = game.get(lift.KEY, {})
            root = car.get("root_position")
            if ((car.get("actor"), car.get("net_role"), car.get("state"), car.get("stop"))
                    != (lift.ACTOR, expected_role, expected_state, "upper")
                    or car.get("replicate_movement") is not True or car.get("deck_collision") not in {1, 3, 5}
                    or not isinstance(root, list) or len(root) != 3 or math.dist(root, PARKED_ROOT) > 4):
                raise RuntimeError("Stock lift disembark lost the exact stationary upper car")
        peer_local = peer.get("pawns", {}).get("local", {}).get("name")
        if not peer_local or (guest_local_name and peer_local != guest_local_name):
            raise RuntimeError("Stock lift disembark lost the guest's original local pawn")
        guest_local_name = peer_local
        pawns = {}
        for seat, game, slot, identity in (("host", authority, "local", host_pawn),
                                           ("guest", peer, "local", guest_local_name),
                                           ("guest_authority", authority, "guest", guest_pawn)):
            pawn = game.get("pawns", {}).get(slot, {})
            if pawn.get("name") != identity:
                raise RuntimeError("Stock lift disembark found a different pawn")
            base = game.get(lift.RIDER_KEY, {}).get(slot, {})
            effects = game.get("player_effects", {}).get(slot, {})
            pawns[seat] = {"position": _position(pawn),
                           "on_deck": (base.get("pawn"), base.get("base")) == (identity, lift.DECK),
                           "state": effects.get("state"), "speed": effects.get("speed")}
        if any(pawn["state"] != "standing" for pawn in pawns.values()):
            raise RuntimeError(f"Stock lift disembark requires standing riders: {_brief({'pawns': pawns})}")
        sequence = authority.get("sequence"), peer.get("sequence")
        if not all(type(value) is int and value > 0 for value in sequence):
            raise RuntimeError("Stock lift disembark is missing live game frame sequences")
        return {"sequence": sequence, "pawns": pawns, "root": authority[lift.KEY]["root_position"]}

    async def settle(before, seat, *, confirmations=2):
        end = min(deadline, asyncio.get_running_loop().time() + 2.5)
        last, stable = before, 0
        seats = ("guest", "guest_authority") if seat == "guest" else ("host", "guest", "guest_authority")
        while asyncio.get_running_loop().time() < end:
            current = observe()
            if all(a > b for a, b in zip(current["sequence"], last["sequence"], strict=True)):
                if all(math.dist(last["pawns"][name]["position"], current["pawns"][name]["position"]) <= 5
                       and last["pawns"][name]["on_deck"] == current["pawns"][name]["on_deck"]
                       for name in seats):
                    stable += 1
                    if stable >= confirmations:
                        return current
                else:
                    stable = 0
                last = current
            await asyncio.sleep(POLL_SECONDS)
        raise RuntimeError(f"Stock lift {seat} did not settle after movement: {_brief(last)}")

    async def pulse(before, seat, axis, direction, milliseconds, pulses):
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(f"Stock lift disembark timed out for {seat}: {_brief(before)}")
        directory = guest_run if seat == "guest" else run
        result = await action(directory, "walk", x=direction if axis == "x" else 0,
                              y=direction if axis == "y" else 0, milliseconds=milliseconds)
        if result.get("movement_scheduled") is not True:
            raise RuntimeError(f"Stock lift {seat} could not schedule native movement")
        # The action acknowledges scheduling. Wait only out its bounded input
        # window before reading new paired game frames for actual movement.
        await asyncio.sleep(milliseconds / 1000)
        after = await settle(before, seat)
        pulses.append({"axis": axis, "input": direction, "milliseconds": milliseconds,
                       "before": _brief(before)[seat], "after": _brief(after)[seat]})
        return after

    frame = observe()
    initial = _brief(frame)
    if not all(pawn["on_deck"] for pawn in frame["pawns"].values()):
        raise RuntimeError(f"Stock lift disembark did not begin with three exact deck observations: {initial}")

    for seat in ("guest", "host"):
        pulses = []
        deadline = asyncio.get_running_loop().time() + 90
        for _ in range(16):
            pawn = frame["pawns"][seat]
            if not pawn["on_deck"]:
                raise RuntimeError(f"Stock lift {seat} lost the deck while lining up: {_brief(frame)}")
            lateral = EXIT_Y - pawn["position"][1]
            if abs(lateral) <= ALIGN_TOLERANCE:
                break
            duration = max(80, min(1000, round(27 * abs(lateral))))
            frame = await pulse(frame, seat, "y", math.copysign(1, lateral), duration, pulses)
        if abs(frame["pawns"][seat]["position"][1] - EXIT_Y) > ALIGN_TOLERANCE:
            raise RuntimeError(f"Stock lift {seat} could not align at the west opening: {_brief(frame)}; "
                               f"last inputs: {pulses[-2:]}")

        # Put the first rider farther onto the continuous west rim so their
        # capsule leaves room for the second rider at the QuarterPipe landing.
        landing_x = -1985 if seat == "guest" else -1875
        target_x = -2030 if seat == "guest" else -1910
        for _ in range(24):
            pawn = frame["pawns"][seat]
            if not pawn["on_deck"] and _safe_landing(pawn["position"]) and pawn["position"][0] <= landing_x:
                break
            if pawn["position"][2] < 2465 or abs(pawn["position"][1] - EXIT_Y) > 18:
                raise RuntimeError(f"Stock lift {seat} missed the west landing: {_brief(frame)}")
            distance = pawn["position"][0] - target_x
            duration = max(220, min(1000, round(12 * distance)))
            frame = await pulse(frame, seat, "x", -1, duration, pulses)
        frame = await settle(frame, seat, confirmations=3)
        offsets = frame["pawns"]
        observations = ("guest", "guest_authority") if seat == "guest" else ("host",)
        if any(offsets[name]["on_deck"] or not _safe_landing(offsets[name]["position"])
               or offsets[name]["position"][0] > landing_x for name in observations):
            raise RuntimeError(f"Stock lift {seat} did not reach the west landing: {_brief(frame)}; "
                               f"last inputs: {pulses[-2:]}")
        if seat == "guest":
            if (math.dist(offsets["guest"]["position"], offsets["guest_authority"]["position"]) > 18
                    or not offsets["host"]["on_deck"]):
                raise RuntimeError(f"Stock lift guest position did not agree while host stayed aboard: {_brief(frame)}")
        study[seat] = {"pulses": pulses, "landed": {name: _brief(frame)[name] for name in observations},
                       "stable_frames": 3}

    if any(pawn["on_deck"] or not _safe_landing(pawn["position"]) for pawn in frame["pawns"].values()):
        raise RuntimeError(f"Stock lift riders did not both remain on the west landing: {_brief(frame)}")
    if math.dist(frame["pawns"]["guest"]["position"], frame["pawns"]["guest_authority"]["position"]) > 18:
        raise RuntimeError(f"Stock lift guest observations separated after host disembarked: {_brief(frame)}")
    return {"character_id": character, "zone_epoch": zone_epoch, "steady_upper_car": frame["root"],
            "guest_local_pawn": guest_local_name, "guest_authority_pawn": guest_pawn, "host_pawn": host_pawn,
            "initial": initial, **study, "final": _brief(frame)}
