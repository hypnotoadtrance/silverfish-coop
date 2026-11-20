"""Verify stock collision and passage at the same live upper Data Silo door."""

import asyncio
import math

from tools.silverfish import native_datasilo_door as door, native_mindcore as core, runtime


async def verify_passage(run, guest_run, action, restart_guest, character, host_generation, zone_epoch):
    proof = {}

    def observe():
        host = runtime.read_json(run / "status.json")
        guest = runtime.read_json(guest_run / "status.json")
        host_game, guest_game = host.get("game", {}), guest.get("game", {})
        valid_visit = (
            (host.get("state"), host.get("generation"), host.get("zone_epoch"), host_game.get("map"))
            == ("hosting", host_generation, zone_epoch, core.MAP)
            and (guest.get("state"), guest.get("host_session"), guest.get("zone_epoch"),
                 guest.get("character_id"), guest_game.get("map"), guest.get("link_connected"))
            == ("connected", host_generation, zone_epoch, character, core.MAP, True)
        )
        return host, guest, host_game, guest_game, valid_visit

    def pose_matches(host, guest, pose):
        if (host.get("actor"), host.get("state"), host.get("net_role"), host.get("pose"),
                host.get("replicates"), host.get("arrow_replicates")) != (door.ACTOR, "active", 3, pose, True, True):
            return False
        if (guest.get("actor"), guest.get("state"), guest.get("net_role"), guest.get("pose"),
                guest.get("knob_collision")) != (door.ACTOR, "guarded", 1, pose, 0):
            return False
        if host["panel_collision"] not in {1, 3, 5} or guest["panel_collision"] != host["panel_collision"]:
            return False
        return all(math.dist(host[key], guest[key]) < 8 for key in ("arrow_relative", "arrow_location", "panel_location"))

    async def settled_door(pose):
        deadline = asyncio.get_running_loop().time() + 14
        result = {}
        while asyncio.get_running_loop().time() < deadline:
            _, _, a, b, valid_visit = observe()
            result = {"host": a.get(door.KEY, {}), "guest": b.get(door.KEY, {})}
            if valid_visit and pose_matches(result["host"], result["guest"], pose):
                return result
            await asyncio.sleep(.12)
        raise RuntimeError(f"Upper stock door did not settle at {pose}: {result}")

    def on_catwalk(game, slot, point):
        return (isinstance(point, list) and len(point) == 3 and abs(point[0]) <= 65 and 6660 < point[2] < 6740
                and game.get("player_effects", {}).get(slot, {}).get("state") == "standing")

    async def guest_pose(expected_pawn, *, after_sequence=None):
        deadline = asyncio.get_running_loop().time() + 5
        locations = []
        while asyncio.get_running_loop().time() < deadline:
            _, _, a, b, valid_visit = observe()
            if (valid_visit and a.get("native_guest_save", {}).get("pawn") == expected_pawn
                    and a.get("native_guest_save", {}).get("state") == "bound"
                    and b.get("pawns", {}).get("local", {}).get("name")
                    and (after_sequence is None or a.get("sequence", 0) > after_sequence[0]
                         and b.get("sequence", 0) > after_sequence[1])):
                locations = [a.get("pawns", {}).get("guest", {}).get("location"),
                             b.get("pawns", {}).get("local", {}).get("location")]
                if (on_catwalk(a, "guest", locations[0]) and on_catwalk(b, "local", locations[1])
                        and math.dist(*locations) <= 18):
                    return {"host": locations[0], "guest": locations[1], "sequences": [a["sequence"], b["sequence"]]}
            await asyncio.sleep(.12)
        raise RuntimeError(f"The same guest did not reach a synchronized upper catwalk pose: {locations}")

    async def walk_guest(pawn, duration):
        previous = await guest_pose(pawn)
        result = await action(guest_run, "walk", x=0, y=1, milliseconds=duration, continuous=True)
        assert result["movement_scheduled"]
        await asyncio.sleep(duration / 1000)
        return await guest_pose(pawn, after_sequence=previous["sequences"])

    async def cross(pawn):
        before = await guest_pose(pawn)
        assert before["guest"][1] < 5000
        arrived = before
        for _ in range(10):
            arrived = await walk_guest(pawn, 500)
            if min(arrived["host"][1], arrived["guest"][1]) >= 5140:
                return {"before": before, "after": arrived}
        raise RuntimeError(f"The enrolled guest did not walk onto the north catwalk: {arrived}")

    proof["closed"] = await settled_door("closed")
    assert proof["closed"]["host"]["locked"] and not proof["closed"]["host"]["open"]
    guarded = await action(guest_run, door.FIXTURE, target="guard")
    assert guarded[door.KEY]["pose"] == "closed"
    await asyncio.sleep(.55)
    proof["guest_power_refused"] = await settled_door("closed")
    await action(run, door.STAGE_PAWN, target="host")
    guest_pawn = (await action(run, door.STAGE_PAWN, target="guest"))["pawn"]
    before = await guest_pose(guest_pawn)
    assert before["guest"][1] < 4920
    blocked = before
    for _ in range(8):
        blocked = await walk_guest(guest_pawn, 600)
        if min(blocked["host"][1], blocked["guest"][1]) >= 4940:
            break
    still_blocked = await walk_guest(guest_pawn, 650)
    if (min(blocked["host"][1], blocked["guest"][1]) < 4940
            or max(still_blocked["host"][1], still_blocked["guest"][1]) >= 4980
            or math.dist(blocked["guest"], still_blocked["guest"]) > 5):
        raise RuntimeError(f"The closed stock panel did not physically stop the same guest: {blocked} -> {still_blocked}")
    proof["closed_blocked"] = {"before": before, "blocked": still_blocked}

    assert (await action(run, door.FIXTURE, target="power"))["source_pose"] == "closed"
    proof["opened"] = await settled_door("open")
    assert proof["opened"]["host"]["open"] and not proof["opened"]["host"]["locked"]
    moved = math.dist(proof["closed"]["host"]["panel_location"], proof["opened"]["host"]["panel_location"])
    assert 150 < moved < 160
    proof["live_passage"] = await cross(guest_pawn)

    old_generation = observe()[1]["generation"]
    await restart_guest()
    proof["fresh_peer_open"] = await settled_door("open")
    assert observe()[1]["generation"] != old_generation
    fresh_pawn = (await action(run, door.STAGE_PAWN, target="guest"))["pawn"]
    proof["fresh_peer_passage"] = await cross(fresh_pawn)
    proof["panel_displacement_uu"] = round(moved, 3)
    return proof
