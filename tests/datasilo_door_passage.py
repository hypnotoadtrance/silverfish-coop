"""Verify stock collision and passage at the same live upper Data Silo door."""

import asyncio
import math

from live_ai_smoke import status, wait
from tools.silverfish import native_datasilo_door as door, native_mindcore as core, runtime
from tools.silverfish import native_mindcore_carry as carry, native_mindcore_fixture as fixture


CRADLE_CAPSULE_XY = (0.0, 6662.06)


async def verify_held_cradle_passage(run, guest_run, action, authority, proof):
    """Bring the held Core through the keyed catwalk door and into the real cradle capsule."""
    host_name = authority["game"]["pawns"]["local"]["name"]
    guest_name = authority["game"]["native_guest_save"]["pawn"]
    generation, epoch = authority["generation"], authority["zone_epoch"]
    for role, name in (("host", host_name), ("guest", guest_name)):
        assert (await action(run, door.STAGE_PAWN, target=role))["pawn"] == name
    proof["loose_core_at_catwalk"] = await action(run, fixture.DECK_LOAD, target="cradle-door")

    def member(state):
        game = state.get("game", {})
        guest = game.get("pawns", {}).get("guest", {})
        assert (state["generation"], state["zone_epoch"], game["map"], guest["name"]) == (
            generation, epoch, core.MAP, guest_name), state
        return guest

    for pose in ("closed", "open"):
        pressed = await action(run, door.FIXTURE, target="button")
        assert (pressed["input"], pressed["source_pose"]) == ("button", "closed"), pressed
        observed = await wait(run, lambda state: (panel := state.get("game", {}).get(door.KEY, {}))
                              and panel.get("pose") == pose and not panel.get("locked"))
        member(observed)
        proof[f"stock_door_{pose}_unlocked"] = observed["game"][door.KEY]
        if pose == "closed":
            await asyncio.sleep(.5)

    await action(guest_run, carry.FIXTURE, target="grab")
    held_state = await wait(run, lambda state: (held := state.get("game", {}).get(carry.AUTHORITY, {}))
                            and (held.get("state"), held.get("pawn"), held.get("core")) == ("held", guest_name, core.CORE))
    held = proof["stock_grip"] = held_state["game"][carry.AUTHORITY]
    lease = held["lease"]
    await action(guest_run, carry.FIXTURE, target="turn@8@90")
    await wait(run, lambda state: (row := state.get("game", {}).get(carry.AUTHORITY, {}))
               and row.get("lease") == lease and abs(row["yaw"] - 90) < .5 and abs(row["pitch"] - 8) < .5
               and row.get("grip_position"))

    def carrier_frame():
        state = status(run)
        guest = member(state)
        game = state["game"]
        peer = status(guest_run)["game"]
        return {"pawn": guest["location"], "health": guest["health"], "hold": game[carry.AUTHORITY],
                "prop": game.get(core.REPLICATION, {}), "scene": game[core.SCENE],
                "guest_pawn": peer.get("pawns", {}).get("local", {}).get("location"),
                "guest_aim": peer.get(carry.AIM), "guest_scope": peer.get(carry.VIEW_SCOPE),
                "sequences": [game["sequence"], peer["sequence"]]}

    async def turn_trailing_core_toward_cradle(before):
        # A stock grip can leave the Core behind its carrier once the cradle blocks
        # the catwalk. Turning in place swings the still-held Core around that grip.
        x, y, _ = before["pawn"]
        core_x, core_y, _ = before["prop"]["position"]
        target_x, target_y = CRADLE_CAPSULE_XY
        delta = math.degrees(math.atan2(target_y - y, target_x - x) - math.atan2(core_y - y, core_x - x))
        yaw = (before["hold"]["yaw"] + delta + 180) % 360 - 180
        turn = proof["cradle_turn"] = {"before": before, "yaw": yaw, "samples": []}
        await action(guest_run, carry.FIXTURE, target=f"turn@8@{yaw:.3f}")
        deadline = asyncio.get_running_loop().time() + 4
        while asyncio.get_running_loop().time() < deadline:
            sample = carrier_frame()
            rows = turn["samples"]
            if not rows or sample["sequences"] != rows[-1]["sequences"]:
                rows.append(sample)
            row = sample["hold"]
            assert (row["lease"], row["core"]) == (lease, core.CORE), row
            if row["state"] == "mount_released" or sample["scene"]["installed"]:
                return
            if row["state"] != "held":
                raise RuntimeError(f"The Core hold ended during the final turn: {sample}")
            await asyncio.sleep(.055)
        raise RuntimeError(f"The final turn did not bring the Core into the stock cradle: {turn['samples'][-1]}")

    frames = proof["carrier_walk"] = []
    stalled = 0
    for _ in range(60):
        before = carrier_frame()
        hold, scene = before["hold"], before["scene"]
        x, y, z = before["pawn"]
        assert hold["lease"] == lease and hold["core"] == core.CORE, hold
        if hold["state"] == "mount_released" or scene["installed"]:
            break
        if hold["state"] != "held" or before["health"] <= 0 or y >= 6650 or not 6660 < z < 6740:
            raise RuntimeError(f"The exact carrier did not reach the stock cradle while holding the Core: {before}")
        duration = 320 if y < 5400 else 400 if y < 6200 else 180
        direction = {"x": max(-.3, min(.3, -x / 140)), "y": .8, "milliseconds": duration, "continuous": True}
        step = {"before": before, "input": direction, "samples": []}
        frames.append(step)
        result = await action(guest_run, "walk", **direction)
        assert result["movement_scheduled"], result
        pulse_end = asyncio.get_running_loop().time() + duration / 1000
        deadline = pulse_end + 2
        while asyncio.get_running_loop().time() < deadline:
            sample = carrier_frame()
            if not step["samples"] or sample["sequences"] != step["samples"][-1]["sequences"]:
                step["samples"].append(sample)
            row = sample["hold"]
            assert row["lease"] == lease and row["core"] == core.CORE, row
            if row["state"] == "mount_released" or sample["scene"]["installed"]:
                break
            if row["state"] != "held":
                raise RuntimeError(f"The Core hold ended during a catwalk step: {sample}")
            grip = row.get("grip_position")
            if (asyncio.get_running_loop().time() >= pulse_end and grip and row["sequence"] > hold["sequence"]
                    and sample["pawn"][1] > y + 4 and grip[1] > hold["grip_position"][1] + 4):
                stalled = 0
                break
            await asyncio.sleep(.055)
        else:
            body = sample["prop"].get("position")
            if (6570 < y < 6650 and body and sample["pawn"][1] <= y + 4 and body[1] < sample["pawn"][1] - 25
                    and abs(row["yaw"] - 90) < .5):
                await turn_trailing_core_toward_cradle(sample)
                break
            # A stock shock can stop movement for the entire input pulse.
            stalled += 1
            if stalled > 2 or row["sequence"] <= hold["sequence"] or sample["pawn"][1] > y + 4:
                raise RuntimeError(f"The carrier's grip did not follow a catwalk step: {step['samples'][-1]}")
    else:
        raise RuntimeError(f"The held Core did not reach the cradle: {frames[-1]}")

    mounted = await wait(run, lambda state: (scene := state.get("game", {}).get(core.SCENE, {}))
                         and (scene["status"], scene["installed"]) == (1, True))
    member(mounted)
    release = mounted["game"][carry.AUTHORITY]
    assert (release["lease"], release["pawn"], release["state"], release["reason"]) == (
        lease, guest_name, "mount_released", "stock_core_capsule_release"), release
    assert any(frame["before"]["pawn"][1] > 5140 and frame["before"]["hold"]["state"] == "held" for frame in frames), frames
    proof["capsule_release"] = release
    return mounted["game"][core.SCENE]


async def verify_passage(run, guest_run, action, restart_guest, character, host_generation, zone_epoch, *, keycard=False):
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

    async def settled_door(pose, *, locked=None):
        deadline = asyncio.get_running_loop().time() + 14
        result = {}
        while asyncio.get_running_loop().time() < deadline:
            _, _, a, b, valid_visit = observe()
            result = {"host": a.get(door.KEY, {}), "guest": b.get(door.KEY, {})}
            if (valid_visit and pose_matches(result["host"], result["guest"], pose)
                    and (locked is None or result["host"]["locked"] is locked)):
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
    proof["guest_power_refused"] = await settled_door("closed", locked=True)
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

    if keycard:
        first = await action(run, door.FIXTURE, target="button")
        assert (first["input"], first["source_pose"]) == ("button", "closed"), first
        unlocked = await settled_door("closed", locked=False)
        assert unlocked["host"]["open"] is False
        proof["stock_keycard_unlock"] = {"input": first, "unlocked": unlocked}
        await asyncio.sleep(.5)
        second = await action(run, door.FIXTURE, target="button")
        assert (second["input"], second["source_pose"]) == ("button", "closed"), second
        proof["stock_keycard_open"] = second
    else:
        assert (await action(run, door.FIXTURE, target="power"))["source_pose"] == "closed"
    proof["opened"] = await settled_door("open")
    assert proof["opened"]["host"]["open"] and not proof["opened"]["host"]["locked"]
    moved = math.dist(proof["closed"]["host"]["panel_location"], proof["opened"]["host"]["panel_location"])
    assert 150 < moved < 160
    proof["live_passage"] = await cross(guest_pawn)

    old_generation = observe()[1]["generation"]
    proof["guest_departure"] = await restart_guest()
    proof["fresh_peer_open"] = await settled_door("open")
    assert observe()[1]["generation"] != old_generation
    fresh_pawn = (await action(run, door.STAGE_PAWN, target="guest"))["pawn"]
    proof["fresh_peer_passage"] = await cross(fresh_pawn)
    proof["panel_displacement_uu"] = round(moved, 3)
    return proof
