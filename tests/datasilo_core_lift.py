"""Follow the enrolled guest's stock Core hold from the floor onto the upper lift landing."""

import asyncio
import math
import uuid

from live_ai_smoke import status
from tools.silverfish import native_datasilo_lift as lift, native_guest_armor as armor
from tools.silverfish import native_mindcore as core, native_mindcore_carry as carry


EXIT_Y = 64.0
# Stock HoldObject uses the camera's horizontal heading above level pitch.
CARRY_PITCH = 3


async def equip_stock_protection(run, guest_run, action):
    # Put stock gear into the cloned fixture save before entering the hazard.
    torso_guid, head_guid = (uuid.uuid4().hex.upper() for _ in range(2))
    await action(run, armor.FIXTURE, target="guest", item_guid=torso_guid, kind="hazard-suit")
    torso = (await action(guest_run, armor.ACTION, target=torso_guid))["native_guest_armor"]
    await action(run, armor.FIXTURE, target="guest", item_guid=head_guid, kind="basic-helmet")
    helmet = (await action(guest_run, armor.HELMET_ACTION, target=head_guid))["native_guest_helmet"]
    assert (torso["guid"], torso["profile"], helmet["guid"]) == (torso_guid, "hazard-suit", head_guid)
    assert math.isclose(helmet["combined_acid"], 0.6, abs_tol=1e-9)
    return {"torso": torso, "helmet": helmet}


async def verify_guest_hazard_authority(run, guest_run, action, authority, character, proof):
    proof["stock_fixture_gear"] = await equip_stock_protection(run, guest_run, action)
    guest_pawn = authority["game"]["native_guest_save"]["pawn"]
    initial = status(run)["game"]["pawns"]["guest"]["health"]
    await action(run, carry.APPROACH_FIXTURE, target=character)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 40
    while loop.time() < deadline:
        host, peer = status(run), status(guest_run)
        game, guest_game = host.get("game", {}), peer.get("game", {})
        pawn = game.get("pawns", {}).get("guest", {})
        client = guest_game.get("pawns", {}).get("local", {})
        guard = guest_game.get("native_client_damage", {})
        authority_effect = game.get("player_effects", {}).get("guest", {}).get("state")
        guest_effect = guest_game.get("player_effects", {}).get("local", {}).get("state")
        row = {"health": pawn.get("health"), "scope": guard, "authority_effect": authority_effect,
               "guest_effect": guest_effect, "authority_pawn": pawn.get("name"), "client_pawn": client.get("name")}
        proof["last_observation"] = row
        assert (host.get("generation"), host.get("zone_epoch"), game.get("map"), pawn.get("name"),
                peer.get("state"), peer.get("link_connected"), peer.get("host_session"), peer.get("zone_epoch"),
                peer.get("character_id"), guest_game.get("map")) == (
                    authority["generation"], authority["zone_epoch"], core.MAP, guest_pawn,
                    "connected", True, authority["generation"], authority["zone_epoch"], character, core.MAP), row
        if guard.get("pawn") == client.get("name") and guard.get("blocked", 0) >= 3:
            assert guard.get("dead") is False and guard.get("output_faults") == 0, row
            hp = row["health"]
            if type(hp) in (int, float) and 0 < hp <= initial - 60 and authority_effect == guest_effect == "standing":
                proof.setdefault("healthy_under_hazard", row)
            if type(hp) in (int, float) and hp <= 0 and authority_effect == guest_effect == "downed":
                assert "healthy_under_hazard" in proof, row
                proof["host_downed_guest"] = row
                return
        await asyncio.sleep(.12)
    raise RuntimeError("Did not observe stock hazard damage on the host followed by the authoritative down")


async def verify_core_lift(run, guest_run, action, authority, character, proof, *, lift_pose_matches, lift_riders_match):
    session, epoch = authority["generation"], authority["zone_epoch"]
    host_pawn = authority["game"]["pawns"]["local"]["name"]
    guest_pawn = authority["game"]["native_guest_save"]["pawn"]
    proof["walks"] = []
    lease = guest_local_pawn = None

    def sample(label):
        host, peer = status(run), status(guest_run)
        host_game, peer_game = host.get("game", {}), peer.get("game", {})
        lead = host_game.get("pawns", {}).get("local", {})
        guest = host_game.get("pawns", {}).get("guest", {})
        local = peer_game.get("pawns", {}).get("local", {})
        held = host_game.get(carry.AUTHORITY, {})
        replica = peer_game.get(carry.VIEW, {})
        root = host_game.get(lift.KEY, {})
        prop = host_game.get(core.REPLICATION, {})
        remote_prop = peer_game.get(core.REPLICATION, {})
        host_bases, guest_bases = host_game.get(lift.RIDER_KEY, {}), peer_game.get(lift.RIDER_KEY, {})
        positions = [body.get("location") for body in (lead, guest, local)]
        sequences = [game.get("sequence", 0) for game in (host_game, peer_game)]
        effects = [game.get("player_effects", {}).get(seat, {}).get("state")
                   for game, seat in ((host_game, "local"), (host_game, "guest"), (peer_game, "local"))]
        witness = {"phase": label, "sequences": sequences, "lead": lead, "carrier": guest, "guest_local": local,
                   "effects": effects, "hold": held, "guest_hold": replica,
                   "guest_scope": peer_game.get(carry.VIEW_SCOPE), "guest_aim": peer_game.get(carry.AIM),
                   "lift": root, "bases": [host_bases, guest_bases],
                   "prop": prop, "guest_prop": remote_prop}
        proof["last_observation"] = witness
        paired = ((host.get("generation"), host.get("zone_epoch"), host_game.get("map"), host.get("state"))
                  == (session, epoch, core.MAP, "hosting")
                  and (peer.get("host_session"), peer.get("zone_epoch"), peer.get("character_id"),
                       peer_game.get("map"), peer.get("state"), peer.get("link_connected"))
                  == (session, epoch, character, core.MAP, "connected", True)
                  and (lead.get("name"), guest.get("name"), host_game.get("native_guest_save", {}).get("pawn"))
                  == (host_pawn, guest_pawn, guest_pawn)
                  and host_game.get("native_guest_save", {}).get("state") == "bound"
                  and local.get("name") and (not guest_local_pawn or local["name"] == guest_local_pawn))
        if not paired:
            return None
        if "downed" in effects or any(body.get("health") == 0 for body in (lead, guest, local)):
            raise RuntimeError(f"Stock lower-route damage downed a rider during {label}")
        if lease and held.get("lease") == lease and held.get("state") in {"released", "mount_released", "failure"}:
            raise RuntimeError(f"The exact Core hold ended during {label}: {held.get('reason')}")
        if lease and ((held.get("lease"), held.get("pawn"), held.get("core"), held.get("state"))
                      != (lease, guest_pawn, core.CORE, "held") or replica.get("state") != "held"):
            return None
        moving = root.get("stop") == "moving"
        if (effects != ["standing"] * 3
                or not all(isinstance(point, list) and len(point) == 3
                           and all(type(axis) in {int, float} and math.isfinite(axis) for axis in point)
                           for point in (*positions, prop.get("position"), remote_prop.get("position")))
                or math.dist(positions[1], positions[2]) > (220 if moving else 34)
                or math.dist(prop["position"], remote_prop["position"]) > (240 if moving else 130)
                or (prop.get("state"), prop.get("stage"), prop.get("core")) != ("active", 0, core.CORE)
                or (remote_prop.get("state"), remote_prop.get("stage"), remote_prop.get("core")) != ("guarded", 0, core.CORE)):
            return None
        return witness, host_game, peer_game

    async def observe(label, accept, *, timeout=4):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            observed = sample(label)
            if observed and accept(*observed):
                return observed[0]
            await asyncio.sleep(.06)
        raise RuntimeError(f"Did not observe the exact paired stock carry during {label}")

    async def at_stop(label, stop, *, riders=False):
        return await observe(label, lambda _, host, peer: (
            lift_pose_matches(host.get(lift.KEY, {}), peer.get(lift.KEY, {}), stop)
            and (not riders or lift_riders_match(host, peer, host_pawn, guest_pawn))), timeout=16)

    async def turn(label, yaw):
        before = await observe(label, lambda *_: True)
        await action(guest_run, carry.FIXTURE, target=f"turn@{CARRY_PITCH}@{yaw}")
        return await observe(label, lambda row, *_: (
            row["hold"]["sequence"] > before["hold"]["sequence"] and abs(row["hold"]["pitch"] - CARRY_PITCH) < .35
            and abs((row["hold"]["yaw"] - yaw + 180) % 360 - 180) < .35))

    async def walk_to(label, point, tolerance, *, seat="carrier", reached=None):
        directory = guest_run if seat == "carrier" else run
        last = await observe(label, lambda *_: True)
        stalled = 0
        for _ in range(18):
            position = last[seat]["location"][:2]
            distance = math.dist(point, position)
            if distance <= tolerance and (not reached or reached(last)):
                return last
            if distance <= 1:
                break
            dx, dy = ((target - origin) / distance for target, origin in zip(point, position, strict=True))
            strength = min(1, max(.16, distance / 180))
            duration = max(50, min(2200, round(.7 * 1000 * distance / (500 * strength))))
            request = {"x": strength * dx, "y": strength * dy, "milliseconds": duration, "continuous": True}
            step = {"phase": label, "seat": seat, "target": point, "before": position, "input": request}
            proof["walks"].append(step)
            result = await action(directory, "walk", **request)
            assert result["movement_scheduled"] is True, result
            step["frames"] = []
            previous = last
            deadline = asyncio.get_running_loop().time() + duration / 1000
            while asyncio.get_running_loop().time() < deadline:
                observed = sample(label)
                if observed:
                    previous = observed[0]
                frame = proof["last_observation"]
                scans = step["frames"]
                if not scans or scans[-1]["sequences"] != frame["sequences"]:
                    scans.append({key: frame[key] for key in (
                        "sequences", "carrier", "prop", "hold", "guest_scope", "guest_aim")})
                await asyncio.sleep(.055)
            # The last published pose during a pulse can predate both its end and stock braking.
            # Wait for two fresh paired poses before deciding the direction of the next pulse.
            def stopped(row, *_):
                nonlocal previous
                if not all(a > b for a, b in zip(row["sequences"], previous["sequences"], strict=True)):
                    return False
                settled = math.dist(previous[seat]["location"], row[seat]["location"]) < 4
                previous = row
                return settled

            after = await observe(label, stopped)
            end = after[seat]["location"][:2]
            step.update(after=end, prop=after["prop"]["position"], health=after[seat]["health"])
            progressed = distance - math.dist(point, end)
            stalled = stalled + 1 if progressed < min(4, distance / 4) else 0
            if stalled >= 3:
                raise RuntimeError(f"Three stock walks did not approach {label} at {point}")
            last = after
        raise RuntimeError(f"Stock walking did not reach {label} at {point}")

    def on_deck(row):
        for base, role, name in ((row["bases"][0], "guest", guest_pawn),
                                 (row["bases"][1], "local", guest_local_pawn)):
            rider = base.get(role, {})
            if (rider.get("pawn"), rider.get("base")) != (name, lift.DECK):
                return False
        return True

    proof["stock_fixture_gear"] = await equip_stock_protection(run, guest_run, action)

    proof["lower"] = await at_stop("lower stock lift", "lower")
    guest_local_pawn = proof["lower"]["guest_local"]["name"]
    await action(run, carry.APPROACH_FIXTURE, target=character)
    proof["approach"] = await observe("checked stock Core approach", lambda row, *_: (
        row["guest_hold"].get("state") == "ready"
        and math.dist(row["guest_local"]["location"], [714.39, -1467, 134.85]) <= 20), timeout=8)
    await action(guest_run, carry.FIXTURE, target="grab")
    proof["grab"] = await observe("stock Interact grab", lambda row, *_: (
        (row["hold"].get("state"), row["hold"].get("pawn"), row["hold"].get("core"))
        == ("held", guest_pawn, core.CORE) and row["guest_hold"].get("state") == "held"), timeout=9)
    lease = proof["grab"]["hold"]["lease"]

    await turn("keep the starting Core behind the northbound carrier", -90)
    await walk_to("clear the loose Core's starting floor", (714, -1110), 36)
    await walk_to("clear the east corner of the scrap pile", (310, -900), 32)
    await walk_to("carry the Core north through the gap between the scrap piles", (50, 125), 26)
    await turn("trail the Core while walking west", 0)
    await walk_to("cross north of the scrap pile", (-560, 215), 30)
    await walk_to("reach the shelf north of the pile face", (-650, 250), 12)
    await walk_to("round the pile face on its shallow shelf", (-710, 300), 12)
    await walk_to("cross the shallow shelf", (-770, 300), 12)
    await walk_to("clear the second pile face", (-805, 245), 12)
    await walk_to("reach the west side of the lower lift", (-1900, 170), 17)
    await turn("trail the Core on the west approach", 90)
    await walk_to("west approach to the lower lift", (-1900, EXIT_Y), 9)
    await walk_to("align with the lower lift opening", (-1900, EXIT_Y), 3.5)
    await turn("align the trailing Core with the west opening", 180)
    boarded = await walk_to("walk the held Core onto the lower deck", (-1672, EXIT_Y), 13, reached=on_deck)
    proof["carrier_boarded"] = boarded

    staged = await action(run, lift.STAGE_RIDER, target="host")
    assert staged["pawn"] == host_pawn, staged
    ready = await at_stop("both exact riders on the lower deck", "lower", riders=True)
    assert math.dist(ready["carrier"]["location"], boarded["carrier"]["location"]) < 20
    assert math.dist(ready["prop"]["position"], boarded["prop"]["position"]) < 35
    proof["both_boarded"] = ready
    pressed = await action(run, lift.FIXTURE, target="button")
    assert (pressed["input"], pressed["source_stop"]) == ("button", "lower"), pressed
    proof["stock_lift_button"] = pressed
    proof["ascending"] = await at_stop("moving stock lift with the Core", "moving", riders=True)
    upper = await at_stop("upper stock lift with the Core", "upper", riders=True)
    assert upper["prop"]["position"][2] - boarded["prop"]["position"][2] > 2250
    assert upper["carrier"]["location"][2] - boarded["carrier"]["location"][2] > 2250
    proof["upper"] = upper

    await walk_to("align with the upper lift opening", (-1672, EXIT_Y), 3.5)
    await turn("trail the Core toward the west landing", 0)
    landed = await walk_to("carry the Core onto the west landing", (-2010, EXIT_Y), 19, reached=lambda row: not on_deck(row))
    assert "guest" not in landed["bases"][0] and "local" not in landed["bases"][1]
    assert 2470 < landed["carrier"]["location"][2] < 2520
    assert landed["prop"]["position"][0] < -1820
    assert landed["hold"]["sequence"] > proof["grab"]["hold"]["sequence"]
    proof["landing"] = landed
