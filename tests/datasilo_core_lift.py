"""Follow the exact stock Core with its enrolled guest from the floor to the upper lift landing."""

import asyncio
import math
import uuid

from live_ai_smoke import status
from tools.silverfish import native_datasilo_lift as lift, native_guest_armor as armor
from tools.silverfish import native_mindcore as core, native_mindcore_carry as carry
from tools.silverfish import native_mindcore_fixture as fixture


EXIT_Y = 64.0
UPPER_LANDING_Z = 2400.0
DECK_WEST_EDGE_X = -1732.3
# Stock HoldObject uses the camera's horizontal heading above level pitch.
CARRY_PITCH = 3


class StockCoreReleased(RuntimeError):
    def __init__(self, label, hold):
        super().__init__(f"The exact Core hold ended during {label}: {hold['reason']}")
        self.hold = hold


async def equip_stock_protection(run, guest_run, action, *, helmet_profile="basic-helmet"):
    # Put stock gear into the cloned fixture save before entering the hazard.
    torso_guid, head_guid = (uuid.uuid4().hex.upper() for _ in range(2))
    await action(run, armor.FIXTURE, target="guest", item_guid=torso_guid, kind="hazard-suit")
    torso = (await action(guest_run, armor.ACTION, target=torso_guid))["native_guest_armor"]
    await action(run, armor.FIXTURE, target="guest", item_guid=head_guid, kind=helmet_profile)
    helmet = (await action(guest_run, armor.HELMET_ACTION, target=head_guid))["native_guest_helmet"]
    assert (torso["guid"], torso["profile"], helmet["guid"], helmet["profile"]) == (
        torso_guid, "hazard-suit", head_guid, helmet_profile)
    body, head = armor.PROFILES["hazard-suit"], armor.HEAD_PROFILES[helmet_profile]
    assert math.isclose(helmet["combined_acid"], body.acid + head.acid, abs_tol=1e-9)
    assert math.isclose(helmet["combined_poison"], body.poison + head.poison, abs_tol=1e-9)
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


async def verify_core_lift(run, guest_run, action, authority, character, proof, *, lift_pose_matches, lift_riders_match,
                           raise_before_ascent=False, park_on_deck=False, fixture_loose_cargo=False,
                           fixture_passengers=False, west_walkway=False, east_stairs=False, upper_approach=False,
                           stage_east=False):
    session, epoch = authority["generation"], authority["zone_epoch"]
    host_pawn = authority["game"]["pawns"]["local"]["name"]
    guest_pawn = authority["game"]["native_guest_save"]["pawn"]
    proof["walks"] = []
    moving_samples = {}
    if fixture_loose_cargo:
        proof["moving_cargo_samples"] = moving_samples
    lease = guest_local_pawn = None
    carrier_can_be_downed = False

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
        if fixture_loose_cargo and root.get("stop") == "moving":
            height = root["root_position"][2]
            peer_lift = peer_game.get(lift.KEY, {})
            moving_samples.setdefault(int(height // 300), dict(lift=root, guest_lift=peer_lift,
                                                               prop=prop, guest_prop=remote_prop))
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
        parked_above = root.get("stop") == "upper"
        if carrier_can_be_downed:
            if effects[1:] not in (["standing", "standing"], ["downed", "downed"]):
                return None
            required_seats, required_effects = (lead,), effects[:1]
        else:
            required_seats = (guest, local) if parked_above else (lead, guest, local)
            required_effects = effects[1:] if parked_above else effects
        if "downed" in required_effects or any(type(body.get("health")) in (int, float) and body["health"] <= 0
                                               for body in required_seats):
            health = f"host {lead.get('health')}, guest {guest.get('health')}"
            raise RuntimeError(f"Stock damage downed a required rider during {label} ({health})")
        if lease and held.get("lease") == lease and held.get("state") in {"released", "mount_released", "failure"}:
            if ((held.get("core"), held.get("pawn"), held.get("state")) == (core.CORE, guest_pawn, "released")
                    and held.get("reason") in {"stock_core_released", "stock_core_released_by_engine"}):
                raise StockCoreReleased(label, held)
            raise RuntimeError(f"The exact Core hold ended during {label}: {held.get('reason')}")
        if lease and ((held.get("lease"), held.get("pawn"), held.get("core"), held.get("state"))
                      != (lease, guest_pawn, core.CORE, "held") or replica.get("state") != "held"):
            return None
        moving = root.get("stop") == "moving"
        if (required_effects != ["standing"] * len(required_effects)
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
            and (not riders or lift_riders_match(host, peer, host_pawn, guest_pawn,
                                                 allow_downed_guest=carrier_can_be_downed,
                                                 allow_rail=park_on_deck or fixture_passengers))), timeout=16)

    async def turn(label, yaw, *, pitch=CARRY_PITCH):
        before = await observe(label, lambda *_: True)
        await action(guest_run, carry.FIXTURE, target=f"turn@{pitch:.3f}@{yaw:.3f}")
        return await observe(label, lambda row, *_: (
            row["hold"]["sequence"] > before["hold"]["sequence"] and abs(row["hold"]["pitch"] - pitch) < .35
            and abs((row["hold"]["yaw"] - yaw + 180) % 360 - 180) < .35))

    async def walk_to(label, point, tolerance, *, seat="carrier", reached=None, pulse_limit=2200):
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
            duration = max(50, min(pulse_limit, round(.7 * 1000 * distance / (500 * strength))))
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
            if math.dist(point, end) <= tolerance and (not reached or reached(after)):
                return after
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

    async def set_down(label):
        nonlocal lease
        released_lease, lease = lease, None
        await action(guest_run, carry.FIXTURE, target="release")

        def released(row, *_):
            hold = row["hold"]
            if (hold.get("lease"), hold.get("core"), hold.get("state")) != (released_lease, core.CORE, "released"):
                return False
            if carrier_can_be_downed and row["effects"][1:] == ["downed", "downed"]:
                return hold.get("reason") in {"stock_core_released", "stock_core_holder_incapacitated"}
            return hold.get("reason") == "stock_core_released" and row["guest_hold"].get("state") == "ready"

        return await observe(label, released, timeout=8)

    async def regrip(label, released):
        nonlocal lease
        previous_lease = released["hold"]["lease"] if released else None
        await action(guest_run, carry.FIXTURE, target="grab")
        held = await observe(label, lambda row, *_: (
            (row["hold"].get("state"), row["hold"].get("pawn"), row["hold"].get("core")) == ("held", guest_pawn, core.CORE)
            and row["hold"].get("lease") not in (None, previous_lease)
            and row["guest_hold"].get("state") == "held"), timeout=8)
        lease = held["hold"]["lease"]
        return held

    async def recover_stock_drop(released):
        nonlocal lease
        lease = None
        # The previous movement pulse is bounded to 2.2 seconds.
        await asyncio.sleep(2.25)
        loose = await observe("find the released original Core on the same floor", lambda row, *_: (
            row["guest_hold"].get("state") == "ready"
            and abs(row["prop"]["position"][2] - row["carrier"]["location"][2]) < 135), timeout=5)
        body, prop = loose["carrier"]["location"][:2], loose["prop"]["position"][:2]
        distance = math.dist(body, prop)
        if distance > 110:
            approach = [b + (a - b) * 85 / distance for a, b in zip(body, prop, strict=True)]
            await walk_to("return within reach of the released Core", approach, 18)
        held = await regrip("pick up the same Core after the stock release", {"hold": released.hold})
        proof.setdefault("stock_regrips", []).append({"release": released.hold, "pickup": held})
        return held["hold"]["yaw"]

    async def observe_parked_core():
        previous = None

        def settled_inside_deck(row, *_):
            nonlocal previous
            position = row["prop"]["position"]
            x, y, z = position
            if row["hold"].get("state") == "held" or not (-1697 < x < -1642 and abs(y) < 48 and 22 < z < 160):
                previous = None
                return False
            if previous and not all(a > b for a, b in zip(row["sequences"], previous["sequences"], strict=True)):
                return False
            settled = previous and math.dist(position, previous["prop"]["position"]) < 1.5
            previous = row
            return settled

        return await observe("verify the loose Core settles inside the lift cabin", settled_inside_deck, timeout=7)

    async def ride_lift(boarding):
        ready = await at_stop("both exact riders on the lower deck", "lower", riders=True)
        assert math.dist(ready["carrier"]["location"][:2], boarding["carrier"]["location"][:2]) < 20
        proof["both_boarded"] = ready
        pressed = await action(run, lift.FIXTURE, target="button")
        assert (pressed["input"], pressed["source_stop"]) == ("button", "lower"), pressed
        proof["stock_lift_button"] = pressed
        proof["ascending"] = await at_stop("moving stock lift with the Core", "moving", riders=True)
        upper = await at_stop("upper stock lift with the Core", "upper", riders=True)
        proof["upper"] = upper
        assert upper["prop"]["position"][2] - ready["prop"]["position"][2] > 2250
        assert upper["carrier"]["location"][2] - ready["carrier"]["location"][2] > 2250
        return upper

    async def pick_up_at_upper_stop(upper):
        nonlocal carrier_can_be_downed
        if upper["effects"][1] == "downed":
            revival = await action(run, "interact", target=character)
            assert revival["effect"] == "revive", revival
            proof["upper_revive"] = await observe("verify the revived guest remains aboard", lambda row, *_: (
                row["effects"][1:] == ["standing", "standing"] and on_deck(row)), timeout=10)
        carrier_can_be_downed = False
        proof["upper_regrip"] = await regrip("pick up the same Core from the parked upper deck", proof.get("deck_release"))

    async def cross_west_walkway():
        if not west_walkway:
            return

        def on_floor(row, seat):
            return 2470 < row[seat]["location"][2] < 2530

        await walk_to("leave the lift along the west floor", (-2110, 450), 27,
                      reached=lambda row: on_floor(row, "carrier"))
        proof["west_waypoint"] = await walk_to(
            "carry the same Core to stock Waypoint 25", (-2132, 1054), 28,
            reached=lambda row: on_floor(row, "carrier") and row["prop"]["position"][1] > 925,
        )
        await walk_to("bring the host to the west opening", (-1720, EXIT_Y), 5, seat="lead")
        await walk_to("walk the host onto the west floor", (-1990, EXIT_Y), 18, seat="lead",
                      reached=lambda row: on_floor(row, "lead"))
        await walk_to("leave the lift along the west floor with the host", (-2110, 450), 28, seat="lead",
                      reached=lambda row: on_floor(row, "lead"))
        regrouped = await walk_to("regroup with the Core at stock Waypoint 25", (-2110, 910), 28, seat="lead",
                                  reached=lambda row: on_floor(row, "lead"))
        assert math.dist(regrouped["prop"]["position"], regrouped["guest_prop"]["position"]) < 55
        assert regrouped["hold"]["lease"] == proof["landing"]["hold"]["lease"]
        proof["west_walkway"] = regrouped

    async def climb_east_stairs():
        if not east_stairs:
            return
        target = "route-upper-stairs" if upper_approach else "route-catwalk"
        route = proof["east_stair_path"] = await action(run, lift.FIXTURE, target=target)
        points = route["points"]
        corridor = next((index for index, (x, y, z) in enumerate(points)
                         if x > 2500 and -1500 < y < -1050 and 2680 < z < 2740), None)
        if corridor is None or corridor + 1 == len(points):
            raise RuntimeError("The stock path did not connect the east stairs to the upper corridor")
        end, beyond = points[corridor:corridor + 2]
        distance = math.dist(end[:2], beyond[:2])
        if distance < 180 or abs(end[2] - beyond[2]) > 12:
            raise RuntimeError("The east corridor did not provide room for both riders")
        last = len(points) - 1 if upper_approach else corridor
        if upper_approach:
            x, y, floor = points[last]
            if not (1280 < x < 1680 and 1900 < y < 2250 and 4475 < floor < 4560):
                raise RuntimeError("The stock path did not reach the upper stair approach")

        async def follow(seat, index, point, *, pulse_limit=2200):
            x, y, floor = point
            return await walk_to(f"follow the east stair path point {index} with the {seat}",
                                 (x, y), 25, seat=seat,
                                 reached=lambda row: 48 < row[seat]["location"][2] - floor < 136,
                                 pulse_limit=pulse_limit)

        async def cross_long_level_section(index, start, finish):
            distance = math.dist(start[:2], finish[:2])
            behind = [b + 175 * (a - b) / distance for a, b in zip(start[:2], finish[:2], strict=True)]
            moved = await asyncio.gather(follow("carrier", index, finish), follow("lead", index, [*behind, finish[2]]),
                                         return_exceptions=True)
            for result in moved:
                if isinstance(result, BaseException):
                    raise result
            return moved[0]

        base = proof["east_staged"] if stage_east else proof["west_walkway"]
        facing = base["hold"]["yaw"]
        south_doorway_was_crossed = False
        lead_point = corridor if stage_east else 1
        index = corridor + 1 if stage_east else 1
        while index <= last:
            x, y, floor = points[index]
            south_doorway = 500 < x < 760 and -2490 < y < -2400 and points[index - 1][1] > -2400
            try:
                if south_doorway:
                    await walk_to("line up the Core with the south doorway", (625, -2320), 20)
                    await turn("aim the Core down the south doorway", -90, pitch=8)
                    frame = await walk_to("move the Core beyond the south wall before turning east", (625, -2535), 20,
                                          reached=lambda row: row["prop"]["position"][1] < -2475)
                    proof["south_doorway"] = frame
                    facing = -90
                else:
                    previous = points[index - 1]
                    rising_from_north_landing = 3280 < previous[2] < 3350 and 3570 < floor < 3650
                    if rising_from_north_landing:
                        dx, dy = x - previous[0], y - previous[1]
                        heading = math.degrees(math.atan2(dy, dx))
                        await turn("aim the Core up the next east staircase", heading, pitch=52)
                        facing = heading
                        frame = await follow("carrier", index, (x, y, floor), pulse_limit=600)
                    else:
                        outward = math.degrees(math.atan2(y, x))
                        if abs((outward - facing + 180) % 360 - 180) > 28 or south_doorway_was_crossed:
                            await turn("keep the raised Core toward the outside of the ring", outward, pitch=68)
                            facing = outward
                        current = proof["last_observation"]
                        if (upper_approach and math.dist(previous[:2], (x, y)) > 650 and abs(previous[2] - floor) < 45
                                and math.dist(current["lead"]["location"][:2], previous[:2]) < 230):
                            frame = await cross_long_level_section(index, previous, (x, y, floor))
                            lead_point = index
                        else:
                            frame = await follow("carrier", index, (x, y, floor))
            except StockCoreReleased as released:
                if not upper_approach or len(proof.get("stock_regrips", ())) >= 3:
                    raise
                facing = await recover_stock_drop(released)
                south_doorway_was_crossed = True
                continue
            south_doorway_was_crossed = south_doorway
            proof["east_stair_progress"] = {"point": index, "frame": frame}
            while lead_point < index and math.dist(frame["carrier"]["location"][:2], points[lead_point][:2]) > 118:
                await follow("lead", lead_point, points[lead_point])
                lead_point += 1
            index += 1
        if upper_approach:
            finish = await observe("both players reach the upper stair approach with the same Core", lambda row, *_: (
                all(4545 < row[seat]["location"][2] < 4665 for seat in ("lead", "carrier"))
                and math.dist(row["lead"]["location"], row["carrier"]["location"]) < 800
                and math.dist(row["prop"]["position"], row["guest_prop"]["position"]) < 55))
            assert finish["hold"]["lease"] == lease and finish["hold"]["core"] == base["hold"]["core"]
            proof["upper_approach"] = finish
            return
        forward = [a + 180 * (b - a) / distance for a, b in zip(end[:2], beyond[:2], strict=True)]
        await follow("carrier", corridor + 1, [*forward, end[2]])
        for index in range(lead_point, corridor + 1):
            await follow("lead", index, points[index])
        finish = await observe("both players reach the upper east corridor with the same Core", lambda row, *_: (
            all(row[seat]["location"][2] > base[seat]["location"][2] + 265 for seat in ("lead", "carrier"))
            and math.dist(row["prop"]["position"], row["guest_prop"]["position"]) < 55))
        assert finish["hold"]["lease"] == base["hold"]["lease"]
        proof["east_stair"] = finish

    async def disembark():
        def above_west_rail(row, *_):
            return row["prop"]["position"][2] >= UPPER_LANDING_Z + 154

        picked_up_above = "upper_regrip" in proof
        if picked_up_above:
            await turn("raise the Core above the upper rail before moving west", 180, pitch=68)
            proof["upper_raised"] = await observe("verify the Core clears the upper rail", above_west_rail, timeout=5)
        await walk_to("align just inside the west edge of the upper deck", (-1720, EXIT_Y), 2.5, reached=on_deck)
        if picked_up_above:
            proof["upper_aligned"] = await observe("keep the Core above the rail while aligning with the opening",
                                                   above_west_rail, timeout=6)
        else:
            await turn("raise the Core outside the upper deck lip", 180, pitch=24)
            proof["upper_aligned"] = await observe("verify the Core is raised and centered before exiting", lambda row, *_: (
                row["prop"]["position"][0] < row["carrier"]["location"][0] - 75
                and abs(row["prop"]["position"][1] - EXIT_Y) < 12
                and row["prop"]["position"][2] >= UPPER_LANDING_Z + 28), timeout=6)
        landed = await walk_to("carry the Core onto the west landing", (-1990, EXIT_Y), 18, reached=lambda row: not on_deck(row))
        proof["landing"] = landed
        assert "guest" not in landed["bases"][0] and "local" not in landed["bases"][1]
        assert 2470 < landed["carrier"]["location"][2] < 2520
        assert landed["prop"]["position"][0] < -1820 and landed["prop"]["position"][2] >= UPPER_LANDING_Z
        pickup = proof.get("upper_regrip") or proof["grab"]
        assert landed["hold"]["sequence"] > pickup["hold"]["sequence"]
        await cross_west_walkway()
        await climb_east_stairs()

    proof["stock_fixture_gear"] = await equip_stock_protection(run, guest_run, action, helmet_profile="dust-filter")

    proof["lower"] = await at_stop("lower stock lift", "lower")
    guest_local_pawn = proof["lower"]["guest_local"]["name"]
    if stage_east:
        proof["fixture_cargo"] = await action(run, fixture.DECK_LOAD, target="east-corridor")
        lead = await action(run, lift.STAGE_RIDER, target="host-east-corridor")
        guest = await action(run, lift.STAGE_RIDER, target="guest-east-corridor")
        assert (lead["pawn"], guest["pawn"]) == (host_pawn, guest_pawn)
        await observe("both players stand on the east corridor beside the loose Core", lambda row, *_: (
            all(2770 < row[seat]["location"][2] < 2820 for seat in ("lead", "carrier"))
            and row["guest_hold"].get("state") == "ready" and row["prop"].get("simulating") is True
            and math.dist(row["prop"]["position"][:2], (2829, -1205)) < 24
            and 2690 < row["prop"]["position"][2] < 2760), timeout=8)
        proof["east_staged"] = await regrip("pick up the original Core on the east corridor", None)
        await climb_east_stairs()
        return
    if fixture_passengers:
        proof["fixture_cargo"] = await action(run, fixture.DECK_LOAD)
        proof["lower_parked"] = await observe_parked_core()
        carrier_can_be_downed = True
        host_boarding = await action(run, lift.STAGE_RIDER, target="host-clear-corner")
        guest_boarding = await action(run, lift.STAGE_RIDER, target="guest-clear-corner")
        assert (host_boarding["pawn"], guest_boarding["pawn"]) == (host_pawn, guest_pawn)
        boarded = await at_stop("the staged passengers settle on the stock lift mesh", "lower", riders=True)
        await pick_up_at_upper_stop(await ride_lift(boarded))
        await disembark()
        return
    if fixture_loose_cargo:
        proof["fixture_cargo"] = await action(run, fixture.DECK_LOAD)
        proof["lower_parked"] = await observe_parked_core()
        pressed = await action(run, lift.FIXTURE, target="power")
        assert (pressed["input"], pressed["source_stop"]) == ("power", "lower"), pressed
        proof["stock_lift_power"] = pressed
        proof["ascending"] = await observe(
            "both games carry the loose Core with the moving stock deck",
            lambda row, host, peer: (
                lift_pose_matches(host.get(lift.KEY, {}), peer.get(lift.KEY, {}), "moving")
                and all(prop.get("simulating") is False and prop.get("parent") == "deck"
                        for prop in (row["prop"], row["guest_prop"]))),
            timeout=16,
        )
        proof["upper"] = await at_stop("upper stock lift with loose cargo", "upper")
        lower_position = proof["lower_parked"]["prop"]["position"]
        upper_position = proof["upper"]["prop"]["position"]
        if upper_position[2] - lower_position[2] <= 2250:
            raise RuntimeError(
                f"Loose Core did not reach the upper deck. Lower position {lower_position}, "
                f"Core position at the upper lift stop {upper_position}"
            )
        expected = [*lower_position[:2], lower_position[2] + UPPER_LANDING_Z]
        proof["released_at_upper_stop"] = await observe(
            "the Core settles at the upper stop and the guest can carry it again",
            lambda row, *_: (row["lift"].get("stop") == "upper" and row["guest_hold"].get("state") == "ready"
                              and all(prop.get("simulating") is True and prop.get("parent") == "free"
                                      for prop in (row["prop"], row["guest_prop"]))
                              and math.dist(row["prop"]["position"], expected) < 25
                              and math.dist(row["prop"]["position"], row["guest_prop"]["position"]) < 12),
        )
        return
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
    await walk_to("reach the edge of the shallow shelf", (-755, 318), 16)
    await walk_to("round the shelf lip to the northeast", (-715, 395), 14)
    await walk_to("cross above the second pile face", (-795, 465), 16)
    await walk_to("clear the far side of the second pile", (-900, 450), 20)
    await walk_to("reach the west side of the lower lift", (-1900, 170), 17)
    await walk_to("bring the Core beside the west opening", (-1939, 126), 13)
    proof["before_regrip"] = await observe("place the Core north of the west opening", lambda row, *_: (
        row["prop"]["position"][0] < -1760 and row["prop"]["position"][1] > 118), timeout=3)
    proof["stock_set_down"] = await set_down("observe the exact Core after stock release")
    await walk_to("align the unladen guest with the west opening", (-1900, EXIT_Y), 6.4)
    proof["deck_regrip_position"] = await walk_to("walk past the Core to the lower deck rider position",
                                                  (-1672, EXIT_Y), 11, reached=on_deck)
    if park_on_deck:
        proof["stock_regrip"] = await regrip("verify a fresh stock hold from the lower deck", proof["stock_set_down"])
        await turn("lift the Core above the west railing", 180, pitch=68)
        await walk_to("move the held Core inward from the west rail", (-1654, EXIT_Y), 8, reached=on_deck)
        proof["above_lower_rail"] = await observe("verify the Core is above the rail and inside the cabin", lambda row, *_: (
            row["prop"]["position"][0] > DECK_WEST_EDGE_X and row["prop"]["position"][2] >= 154), timeout=5)
        await turn("rotate the raised Core along the longer side of the cabin", -90, pitch=52)
        proof["before_deck_release"] = await observe("verify the raised Core is over the middle of the lower deck", lambda row, *_: (
            -1697 < row["prop"]["position"][0] < -1642 and abs(row["prop"]["position"][1]) < 23
            and row["prop"]["position"][2] >= 148 and on_deck(row)), timeout=5)
        carrier_can_be_downed = True
        proof["deck_release"] = await set_down("release the Core over the stock deck")
        proof["lower_parked"] = await observe_parked_core()
        staged = await action(run, lift.STAGE_RIDER, target="host-clear-corner")
    else:
        staged, proof["stock_regrip"] = await asyncio.gather(
            action(run, lift.STAGE_RIDER, target="host"),
            regrip("verify a fresh stock hold on the same Core from the deck", proof["stock_set_down"]),
        )
    assert staged["pawn"] == host_pawn, staged
    if raise_before_ascent:
        await turn("raise the regripped Core before the lower deck starts moving", 178, pitch=14)
        proof["lower_aligned"] = await observe("verify the Core is above the lower deck before ascent", lambda row, *_: (
            row["prop"]["position"][2] >= 65 and abs(row["prop"]["position"][1] - EXIT_Y) < 38), timeout=4)
    upper = await ride_lift(proof.get("lower_parked", proof["stock_regrip"]))
    if park_on_deck:
        await pick_up_at_upper_stop(upper)
    await disembark()
