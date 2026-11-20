"""Verify stock Data Silo overlap, guest Results compilation, and same-port custody."""

import argparse
import asyncio
from hashlib import sha256
import json
import math
from pathlib import Path
import shutil
import sys
import uuid

from live_ai_smoke import wait
from live_computer_board_smoke import stock_flags
from live_drill_test_results_smoke import copy_checkpoint
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_datasilo_lift as lift
from tools.silverfish import native_datasilo_door as door
from tools.silverfish import native_authored_pickup as pickup
from tools.silverfish import native_cistern_lockpick as lockpick
from tools.silverfish import native_host_save, native_mindcore as core, native_mindcore_fixture as fixture, runtime, service
from tools.silverfish import native_mindcore_view as console
from tools.silverfish import native_mindcore_carry as carry
from tools.silverfish import native_mosby_station_view as download_view
from tools.silverfish import native_savlo_locker as savlo_locker
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import submit
from tools.silverfish.native_tape_profiles import BLANK_CLASS, DRILL, INSTRUCTIONS, drill_download_unlocked
from tools.silverfish.travel import CISTERN, CROSSROADS, DEAD_BRIDGE, OBSERVATORY, RUIN_FIELDS, SCIENTIST_SHOP


STONE_MAZE = "/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P"
ANNEX = "/Game/Classes/Maps/Maps_Dungeons/Anex_01_P"
WALL = "/Game/Classes/Maps/Maps_OverWorld/TheWall_01_P"
BEACH = "/Game/Classes/Maps/Maps_OverWorld/Beach_01_P"
ROUTE = (
    (SCIENTIST_SHOP, RUIN_FIELDS, "Market_Flood", "door", "Dungeon_TransitionDoor_BP_C_2"),
    (RUIN_FIELDS, CROSSROADS, "CR_RF", "overlap", "Level_Exit_BP_C_1"),
    (CROSSROADS, DEAD_BRIDGE, "DB_CR", "overlap", "Level_Exit_BP_C_275"),
    (DEAD_BRIDGE, STONE_MAZE, "DB_SM", "overlap", "Level_Exit_BP_C_2"),
    (STONE_MAZE, ANNEX, "Maze_Annex", "door", "Dungeon_TransitionDoor_BP_C_4"),
    (ANNEX, WALL, "Wall_Annex", "door", "Dungeon_TransitionDoor_BP_C_0"),
    (WALL, core.MAP, "Wall_Data", "door", "Dungeon_TransitionDoor_BP_C_0"),
)
REPORT = "live-mindcore-verification.json"


def _route_proofs(rows):
    if not isinstance(rows, list) or len(rows) != len(ROUTE):
        return False
    fields = ("source", "destination", "entrance", "kind", "actor")
    for row, leg in zip(rows, ROUTE, strict=True):
        if not isinstance(row, dict) or tuple(row.get(key) for key in fields) != leg:
            return False
        _, destination, _, kind, _ = leg
        route, = [item for item in AUTHORED_ROUTES
                  if (item.source, item.destination, item.entrance, item.kind, item.actor) == leg]
        field = "Dungeon_TransitionDoor_BP_C" if kind == "door" else "LevelExit_Spawn_BP_C"
        world = destination.rsplit("/", 1)[-1]
        marker = f"{field} {destination}.{world}:PersistentLevel.{route.arrival_actor}"
        if (row.get("arrival", {}).get("actor"), row.get("arrival", {}).get("placement")) != (marker, "arrow"):
            return False
    return True


def baseline_inputs(work, baseline, *, resume=False):
    prefix, report, area = ("mindcore-", REPORT, core.MAP) if resume else (
        "drill-results-", "live-drill-test-results-verification.json", SCIENTIST_SHOP)
    if (baseline.resolve(strict=True) != baseline or baseline.is_junction()
            or baseline.parents[2] != work.parent or baseline.parent.name != "campaigns"
            or not baseline.name.startswith(prefix)):
        raise ValueError("Choose the requested exact campaign type from an isolated sibling lab")
    source = {
        "campaign": baseline / "save/campaign.json",
        "identity": baseline / "save/host-identity.json",
        "proof": baseline / "run" / report,
    }
    for path in source.values():
        if path.resolve(strict=True) != path or path.is_symlink():
            raise ValueError("A Results input is outside its exact isolated campaign")
    data, proof = runtime.read_json(source["campaign"]), runtime.read_json(source["proof"])
    receipt = proof["source_results"] if resume else proof["station"]["receipt"]
    character, guid, blank = (receipt[key] for key in ("owner", "filled_guid", "blank_guid"))
    party = data["world"]["party"]
    if (data["world"]["map"] != area or not drill_download_unlocked(party) or party.get(DRILL.ledger, {}).get(blank) != receipt
            or not core._conversion(party, character, guid) or core.LEDGER in party):
        raise ValueError("The checkpoint must have the verified Scientist Results and a fresh Data Silo console")
    profile = baseline / "game-user"
    native_host_save.verify_checkpoint(profile, data)
    if resume:
        dormant = core.parse_scene(["native-mindcore-scene", core.MAP, core.CRADLE, core.UPLOADER, core.CORE, core.FLAG, "0", "0"])
        frames = (data["world"]["native_host_save"], data["characters"][character]["state"]["native_guest_save"])
        if (proof.get("dormant") != dormant or not _route_proofs(proof.get("routes"))
                or frames[0]["slot"] == frames[1]["slot"]
                or any(stock_flags(profile / "Saved/SaveGames", frame, core.MAP).get(core.FLAG, 0) != 0 for frame in frames)):
            raise ValueError("Resume needs the exact dormant console, seven authored legs, and both original stage-zero frames")
    elif proof.get("verified") is not True:
        raise ValueError("The Scientist Results proof is unverified")
    guest_id = str(uuid.UUID(data["characters"][character]["guest_id"]))
    source["invite"] = baseline / "run/invites" / f"{guest_id}.invite.json"
    if source["invite"].resolve(strict=True) != source["invite"] or source["invite"].is_symlink():
        raise ValueError("The Results owner's paired invitation is unavailable")
    invitation = runtime.read_json(source["invite"])
    if (invitation.get("host"), invitation.get("guest_id"), invitation.get("character_id")) != (
            "127.0.0.1", guest_id, character):
        raise ValueError("The proof needs the exact loopback Results owner")
    return data, source, receipt, guest_id, proof["routes"] if resume else []


def lift_pose_matches(host, guest, stop):
    if ((host.get("actor"), host.get("state"), host.get("net_role"), host.get("replicates"))
            != (lift.ACTOR, "active", 3, True)
            or (guest.get("actor"), guest.get("state"), guest.get("net_role"), guest.get("button_collision"))
            != (lift.ACTOR, "guarded", 1, 0)):
        return False
    if any(row["stop"] != stop or not row["replicate_movement"] or row["deck_collision"] not in {1, 3, 5}
           for row in (host, guest)):
        return False
    height = host["root_position"][2]
    if stop == "moving" and not 600 < height < 1800:
        return False
    if stop in lift.STOPS and abs(height - lift.STOPS[stop]) > 5:
        return False
    tolerance = 220 if stop == "moving" else 8
    return all(math.dist(host[key], guest[key]) <= tolerance
               for key in ("root_position", "deck_position", "button_position"))


def lift_riders_match(host_frame, guest_frame, host_pawn, guest_pawn, *, allow_downed_guest=False, allow_rail=False):
    maximum_height = 215 if allow_rail else 165
    for frame, seats in ((host_frame, {"local": host_pawn, "guest": guest_pawn}),
                         (guest_frame, {"local": guest_frame.get("pawns", {}).get("local", {}).get("name")})):
        height = frame[lift.KEY]["root_position"][2]
        for slot, pawn in seats.items():
            base = frame.get(lift.RIDER_KEY, {}).get(slot, {})
            if not pawn or (base.get("pawn"), base.get("base")) != (pawn, lift.DECK):
                return False
            effect = frame.get("player_effects", {}).get(slot, {}).get("state")
            relative_height = base["position"][2] - height
            downed_seat = allow_downed_guest and (frame is guest_frame or slot == "guest")
            if downed_seat and effect == "downed" and 48 < relative_height < 105:
                continue
            if effect != "standing" or not 85 < relative_height < maximum_height:
                return False
    return True


async def exercise(work, baseline, vanilla_saved, *, resume=False, stock_input=False, instructions=False,
                   carry_only=False, shared_core=False, guest_carry=False, lift_ride=False, lift_keycard=False,
                   lift_disembark=False, upper_door=False, upper_door_keycard=False, stock_hazard_wipe=False,
                   prepare_core_carry=False, core_lift_carry=False, guest_hazard_authority=False,
                   raise_before_ascent=False, park_on_deck=False, fixture_loose_cargo=False, fixture_passengers=False,
                   west_walkway=False, route_probe=False, east_stairs=False):
    upper_door = upper_door or upper_door_keycard
    if fixture_passengers and not fixture_loose_cargo:
        raise ValueError("Fixture passengers require the isolated loose-cargo setup")
    if west_walkway and not (park_on_deck or fixture_passengers):
        raise ValueError("The west walkway requires a stock pickup at the upper stop")
    if east_stairs and not west_walkway:
        raise ValueError("The east stairs require the west walkway carry")
    if sum((raise_before_ascent, park_on_deck, fixture_loose_cargo)) > 1:
        raise ValueError("Choose one lower deck positioning scenario")
    core_lift_carry = core_lift_carry or raise_before_ascent or park_on_deck or fixture_loose_cargo
    if route_probe and (not resume or core_lift_carry or stock_input or instructions or carry_only
                        or shared_core or guest_carry or lift_ride or upper_door or stock_hazard_wipe
                        or prepare_core_carry or guest_hazard_authority):
        raise ValueError("Run the stock navigation survey separately from the dormant Data Silo checkpoint")
    if instructions and resume:
        raise ValueError("Start from the Scientist Results checkpoint to buy a genuine second blank")
    if carry_only and instructions:
        raise ValueError("Run the Core handling proof separately from the Instructions exchange")
    if guest_carry and (carry_only or instructions):
        raise ValueError("Run the guest Core interaction separately")
    if lift_ride and (not resume or stock_input or instructions or carry_only or guest_carry):
        raise ValueError("Run the stock lift ride separately from a dormant Data Silo checkpoint")
    if lift_keycard and not lift_ride:
        raise ValueError("Verify the stock button with the lift ride")
    if lift_disembark and not lift_ride:
        raise ValueError("Verify the stock landing after riding the lift")
    if upper_door and (not resume or stock_input or instructions or carry_only or guest_carry or lift_ride or shared_core):
        raise ValueError("Verify the upper door separately from a dormant Data Silo checkpoint")
    if stock_hazard_wipe and (not resume or stock_input or lift_ride
                              or upper_door or instructions or carry_only or guest_carry or shared_core):
        raise ValueError("Run the stock hazard wipe separately from the dormant lower lift checkpoint")
    if prepare_core_carry and (not resume or stock_input or lift_ride or upper_door or instructions
                               or carry_only or guest_carry or shared_core or stock_hazard_wipe):
        raise ValueError("Prepare the carry checkpoint separately from the verified dormant Savlo keycard checkpoint")
    if core_lift_carry and (not resume or stock_input or lift_ride or upper_door or instructions
                            or carry_only or guest_carry or shared_core or stock_hazard_wipe or prepare_core_carry):
        raise ValueError("Test the lift carry separately from the prepared dual-card checkpoint")
    if guest_hazard_authority and (not resume or stock_input or lift_ride or upper_door or instructions
                                   or carry_only or guest_carry or shared_core or stock_hazard_wipe
                                   or prepare_core_carry or core_lift_carry):
        raise ValueError("Test guest damage authority separately from the dormant Data Silo checkpoint")
    if not (runtime.qualified_authored_routes(work)
            and runtime.staged_script(work, "native_mindcore.lua", core.FLAG)
            and runtime.staged_script(work, "native_mindcore_fixture.lua", fixture.INSTALL)):
        raise ValueError("Use the matched isolated native stage with the stock overlap fixture")
    if stock_input and not runtime.staged_script(work, "native_mindcore_view.lua", "SilverfishSetNativeMindCoreInput"):
        raise ValueError("Use a matched stage with the ordinary MindCore Interact adapter")
    if carry_only and not runtime.staged_script(work, "native_mindcore_fixture.lua", fixture.CARRY):
        raise ValueError("Use the matched stock Core handling fixture")
    if shared_core and not runtime.staged_script(work, "native_mindcore_replication.lua", "SetReplicateMovement"):
        raise ValueError("Use the matched Data Silo Core replication stage")
    if (lift_ride or core_lift_carry) and not runtime.staged_script(
            work, "native_datasilo_lift.lua", "SilverfishDataSiloLiftGuardAvailable"):
        raise ValueError("Use the matched Data Silo lift stage")
    if core_lift_carry and not all(runtime.staged_script(work, script, marker) for script, marker in (
            ("native_mindcore_carry_view.lua", "local absolute_aim"),
            ("native_mindcore_replication.lua", "SetReplicateMovement"),
            ("players.lua", "walk-continuous"))):
        raise ValueError("Use the matched stage with continuous stock movement and absolute Core carry look")
    if (lift_keycard or prepare_core_carry) and not runtime.staged_script(work, "native_cistern_lockpick.lua", lockpick.FIXTURE):
        raise ValueError("Use the matched stock Cistern lockpick stage")
    if upper_door and not runtime.staged_script(work, "native_datasilo_door.lua", "SilverfishDataSiloDoorGuardAvailable"):
        raise ValueError("Use the matched upper Data Silo door stage")
    if upper_door_keycard and not (
            runtime.staged_script(work, "native_savlo_locker.lua", savlo_locker.FIXTURE)
            and runtime.staged_script(work, "native_authored_pickup_input_fixture.lua", pickup.SAVLO_KEYCARD)):
        raise ValueError("Use the matched stock Fort Leto key and Trench secure locker stage")
    originals = saved_hashes(vanilla_saved)
    if not originals:
        raise ValueError("Supply the original Saved directory for isolation verification")
    data, inputs, conversion, guest_id, prior_routes = baseline_inputs(work, baseline, resume=resume)
    if stock_hazard_wipe:
        saved_host = data["world"].get("host", {})
        if data["world"]["map"] != core.MAP or math.dist(saved_host["location"], [-1672, -49, 120]) > 15:
            raise ValueError("Use the paired dormant checkpoint on the lower lift in the stock hazard")
    if upper_door_keycard:
        claims = data["world"]["party"].get(pickup.STATE_KEY, {})
        if any(source in claims for source in (pickup.FORT_LETO_LOCKER_KEY, pickup.SAVLO_KEYCARD)):
            raise ValueError("Use a dormant checkpoint with the placed Fort Leto key and Trench keycard unclaimed")
    if prepare_core_carry:
        claims = data["world"]["party"].get(pickup.STATE_KEY, {})
        savlo_claim = claims.get(pickup.SAVLO_KEYCARD)
        source_proof = runtime.read_json(inputs["proof"])
        stock_savlo = source_proof.get("upper_door_keycard", {}).get("savlo_card", {}).get("claim")
        if (source_proof.get("verified") is not True or not savlo_claim or stock_savlo != savlo_claim
                or savlo_claim.get("owner") != "host"
                or any(source in claims for source in (pickup.CROSSROADS_LOCKPICK, pickup.CISTERN_OLD_NFTA_KEYCARD))):
            raise ValueError("Require the verified host Savlo card with the CrossRoads lockpick and Cistern card unclaimed")
    if core_lift_carry:
        claims = data["world"]["party"].get(pickup.STATE_KEY, {})
        source_proof = runtime.read_json(inputs["proof"])
        prepared = source_proof.get("carry_checkpoint", {})
        cards = tuple(claims.get(source, {}) for source in (pickup.SAVLO_KEYCARD, pickup.CISTERN_OLD_NFTA_KEYCARD))
        if (source_proof.get("verified") is not True or not isinstance(prepared, dict)
                or prepared.get("stock_flags") != [0, 0]
                or any(claim.get("owner") != "host" or not claim.get("guid")
                       or prepared.get("cards", {}).get(claim.get("source"), {}).get("guid") != claim["guid"]
                       for claim in cards)):
            raise ValueError("Require both exact stock cards from the verified paired carry checkpoint")
    if upper_door_keycard or stock_hazard_wipe or prepare_core_carry or core_lift_carry or guest_hazard_authority:
        for index, frame in enumerate(native_host_save.references(data)):
            inputs[f"stock_save_{index}"] = native_host_save.slot_path(baseline / "game-user", frame["slot"])
    initial_inputs = {key: sha256(path.read_bytes()).hexdigest() for key, path in inputs.items()}
    character, guid, blank = (conversion[key] for key in ("owner", "filled_guid", "blank_guid"))
    name = "mindcore-" + uuid.uuid4().hex[:8]
    campaign, client = work / "campaigns" / name, work / "clients" / name
    run, guest_run = campaign / "run", client / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    start_area = data["world"]["map"]
    proof = {"baseline": baseline.name, "routes": prior_routes, "source_results": conversion}
    if resume:
        proof["resumed_dormant_checkpoint"] = data["revision"]
    jobs = []

    async def action(directory, verb, *, success=True, **fields):
        result = await asyncio.wait_for(submit(directory, verb, **fields), 45)
        if result.get("ok") is not success:
            raise RuntimeError(f"{verb} returned {result.get('error', 'an unexpected response')}")
        return result

    def start_host(port):
        task = asyncio.create_task(service.host_game(work, campaign, "127.0.0.1", port, True, True,
            native_events=True, native_guest=True, native_zones=True, expanded_zones=True,
            guest_regen=raise_before_ascent or park_on_deck or fixture_loose_cargo))
        jobs.append((run, task))
        return task

    async def start_guest():
        invite = await action(run, "refresh-invite", target=guest_id)
        assert (invite["guest_id"], invite["character_id"]) == (guest_id, character)
        task = asyncio.create_task(service.join_game(work, client, Path(invite["invite_file"]), True))
        jobs.append((guest_run, task))
        return task

    async def stop(directory, task):
        if not task.done():
            await action(directory, "stop")
        await asyncio.wait_for(asyncio.shield(task), 18)

    async def paired(area, previous=None):
        authority = await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == area
            and (state["generation"], state.get("zone_epoch")) != previous
            and state["game"].get("native_guest_save", {}).get("state") == "bound"
            and any(row["character_id"] == character and row["state"] == "active"
                    for row in state["campaign"].get("leases", ())), timeout=75)
        await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("link_connected") is True
            and (state.get("character_id"), state.get("host_session"), state.get("zone_epoch"), state.get("game", {}).get("map"))
              == (character, authority["generation"], authority["zone_epoch"], area), timeout=75)
        return authority

    def events():
        with (run / "events.jsonl").open(encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.endswith("\n")]

    async def verify_stock_hazard_wipe(authority):
        previous = authority["generation"], authority["zone_epoch"]
        ready = await paired(OBSERVATORY, previous)
        assert ready["generation"] == authority["generation"]
        journal = events()
        downs = [row for row in journal if row["event"] == "gameplay_action"
                 and row.get("effect") in ("downed", "party_wipe")]
        assert [(row["target"], row["effect"], row["health"]) for row in downs] == [
            ("host", "downed", 0), (character, "party_wipe", 0),
        ], downs
        respawn, = (row for row in journal if row["event"] == "party_respawn_committed")
        assert (respawn["map"], respawn["stock_rest"]) == (OBSERVATORY, True)
        assert downs[0]["revision"] < downs[1]["revision"] < respawn["revision"]
        await action(run, "checkpoint")
        proof["stock_hazard_wipe"] = {"native_downs": downs, "respawn": respawn,
                                     "same_host": ready["generation"], "same_guest": character}

    async def travel(authority, leg, *, receipts="routes"):
        area, destination, entrance, kind, actor = leg
        pending = dict(source=area, destination=destination, entrance=entrance, kind=kind, actor=actor)
        proof["pending_route"] = pending
        assert authority["game"]["map"] == area
        route, = [row for row in AUTHORED_ROUTES if (row.source, row.destination, row.entrance, row.kind, row.actor) == leg]
        recipe = "native-door-position" if kind == "door" else "native-exit-position"
        selector = {"entrance": entrance} if kind == "door" else {"entrance": entrance, "exit_actor": actor}
        for role in ("host", "guest"):
            await action(run, recipe, target=role, exit_position="near", **selector)
        if kind == "door":
            await action(run, "native-door-probe", target="host", **selector)
        else:
            await action(run, recipe, target="host", exit_position="inside", **selector)
        next_frame = await paired(destination, (authority["generation"], authority["zone_epoch"]))
        def arrivals():
            return [row for row in events() if row.get("event") == "zone_travel_ready" and row.get("epoch") == next_frame["zone_epoch"]]
        await wait(run, lambda _: arrivals(), timeout=15)
        arrival, = arrivals()
        assert (arrival.get("source"), arrival.get("destination"), arrival.get("entrance")) == (area, destination, entrance)
        actor_type = "Dungeon_TransitionDoor_BP_C" if kind == "door" else "LevelExit_Spawn_BP_C"
        package = destination.rsplit("/", 1)[-1]
        placement = arrival["authored_arrival"]
        assert (placement["actor"], placement["placement"]) == (
            f"{actor_type} {destination}.{package}:PersistentLevel.{route.arrival_actor}", "arrow"), placement
        proof.setdefault(receipts, []).append({**proof.pop("pending_route"), "arrival": placement})
        return next_frame

    async def flags():
        manifest = campaign / "save/campaign.json"
        profile = campaign / "game-user"
        for _ in range(6):
            frame = runtime.read_json(manifest)
            host = frame["world"]["native_host_save"]
            guest = frame["characters"][character]["state"]["native_guest_save"]
            assert host["slot"] != guest["slot"], frame
            try:
                native_host_save.verify_checkpoint(profile, frame)
                folder = profile / "Saved/SaveGames"
                return frame, [stock_flags(folder, row, core.MAP) for row in (host, guest)]
            except (FileNotFoundError, native_host_save.CampaignFormatError):
                if runtime.read_json(manifest)["revision"] == frame["revision"]:
                    raise
                await asyncio.sleep(.05)
        raise RuntimeError("Could not verify the paired Data Silo stock checkpoint")

    async def core_poses(previous=None):
        async def observe(directory, role):
            def ready(state):
                game = state.get("game", {})
                row = game.get(fixture.POSE_KEY, {})
                if row.get("role") != role or previous and game.get("sequence", 0) < previous[role]["sequence"] + 4:
                    return False
                return {**row, "sequence": game["sequence"]}

            try:
                state = await wait(directory, ready, timeout=12)
            except RuntimeError:
                raise RuntimeError(f"No fresh {role} Data Silo Core pose during the carry trial") from None
            return ready(state)

        host_pose, guest_pose = await asyncio.gather(observe(run, "host"), observe(guest_run, "guest"))
        return {"host": host_pose, "guest": guest_pose}

    async def shared_core_at(stage, *, label):
        def current(directory):
            state = runtime.read_json(directory / "status.json")
            return state.get("game", {}).get(core.REPLICATION, {})

        host_view = guest_view = {}
        deadline = asyncio.get_running_loop().time() + 16
        while asyncio.get_running_loop().time() < deadline:
            host_view, guest_view = current(run), current(guest_run)
            if ((host_view.get("state"), host_view.get("stage"), host_view.get("capsule_overlap"))
                    == ("active", stage, True)
                    and (guest_view.get("state"), guest_view.get("stage"), guest_view.get("capsule_overlap"))
                    == ("guarded", 0, False)):
                distance = math.dist(host_view["position"], guest_view["position"])
                if distance <= 24:
                    row = {"host": host_view, "guest": guest_view, "distance_uu": round(distance, 3)}
                    proof.setdefault("shared_core", {})[label] = row
                    return row
            await asyncio.sleep(.2)
        raise RuntimeError(f"Core did not synchronize at {label}: host={host_view}, guest={guest_view}")

    async def inventory(directory, owner):
        items, offset = {}, 0
        for _ in range(17):
            field = {"amount": offset} if directory == run else {"target": str(offset)}
            page = (await action(directory, "native-inventory-list", **(field if offset else {})))["native_inventory"]
            assert (page["owner"], page["offset"]) == (owner, offset), page
            for row in page["items"]:
                assert row["guid"] not in items, row
                items[row["guid"]] = {key: value for key, value in row.items() if key != "index"}
            offset = page["next_offset"]
            if offset is None:
                assert page["total"] == len(items), page
                return items
        raise RuntimeError("Data Silo inventory census exceeded its page bound")

    async def view(state, sequence=None, opened=None):
        row = await wait(guest_run, lambda status: (
            (proof := status.get("game", {}).get(console.KEY, {})).get("state") == state
            and (sequence is None or proof.get("sequence") == sequence)
            and (opened is None or proof.get("opened") == opened)), timeout=30)
        return row["game"][console.KEY]

    async def download_state(state, sequence=None, opened=None):
        frame = await wait(guest_run, lambda reply: (
            (row := reply.get("game", {}).get(download_view.KEY, {})).get("state") == state
            and row.get("station") == INSTRUCTIONS.station
            and (sequence is None or row.get("sequence") == sequence)
            and (opened is None or row.get("open") == opened)), timeout=30)
        return frame["game"][download_view.KEY]

    async def buy_blank():
        await action(run, "native-finite-shop-approach", target="guest")
        listing = (await action(guest_run, "native-finite-shop-list"))["native_finite_shop"]
        shelf, = [row for row in listing["offers"] if row["class_path"] == BLANK_CLASS]
        assert (shelf["quantity"], shelf["value"]) == (1, 30) and 0 < shelf["price"] <= 75, shelf
        result = await action(guest_run, "native-finite-shop-buy", target=f"{shelf['guid']}@{shelf['price']}")
        purchase = result["native_finite_purchase"]
        bought, = purchase["offers"]
        assert purchase["stock_source"] == "stock_shelf" and purchase["offer_guid"] == shelf["guid"]
        assert bought["guid"] != shelf["guid"] and dict(bought, guid=shelf["guid"]) == shelf
        assert purchase["wallet_before"] - purchase["wallet_after"] == shelf["price"]
        assert purchase["merchant_cash_after"] - purchase["merchant_cash_before"] == shelf["price"]
        proof["scientist_blank_purchase"] = purchase
        return bought["guid"]

    async def download_tape(owned_blank, expected_inventory, expected_host, compiled_flags):
        await wait(guest_run, lambda reply: (row := reply.get("game", {}).get(download_view.KEY, {})).get("station")
                   == INSTRUCTIONS.station and row["state"] in ("armed", "focused"), timeout=15)
        first = (await action(guest_run, download_view.FIXTURE))[download_view.KEY]
        opened = first["sequence"]
        assert (first["state"], first["open"], first["station"], first["blank_guid"]) == (
            "checking", opened, INSTRUCTIONS.station, None), first
        offer = await download_state("offer", opened, opened)
        assert offer["blank_guid"] == owned_blank, offer
        before, _ = await flags()
        assert not before["world"]["party"].get(INSTRUCTIONS.ledger)
        assert await inventory(guest_run, character) == expected_inventory
        second = (await action(guest_run, download_view.FIXTURE))[download_view.KEY]
        assert (second["state"], second["open"], second["blank_guid"]) == ("trading", opened, owned_blank), second
        assert second["sequence"] > opened
        exchanged = await download_state("exchanged", second["sequence"], opened)
        product = uuid.uuid5(uuid.NAMESPACE_URL, f"{INSTRUCTIONS.namespace}:{character}:{owned_blank}").hex.upper()
        assert exchanged["filled_guid"] == product
        current, stock_flags_after = await flags()
        assert stock_flags_after == compiled_flags
        current_party = current["world"]["party"]
        account = current_party[INSTRUCTIONS.ledger][owned_blank]
        assert (account["owner"], account["blank_guid"], account["filled_guid"], account["class_path"], account["console_status"]) == (
            character, owned_blank, product, INSTRUCTIONS.product, 2), account
        assert current_party["native_quests"] == data["world"]["party"]["native_quests"], current_party["native_quests"]
        items = await inventory(guest_run, character)
        assert (items[product]["class_path"], items[product]["source"], items[product]["quantity"]) == (INSTRUCTIONS.product, "backpack", 1)
        assert owned_blank not in items and product not in expected_host
        assert {id_: row for id_, row in items.items() if id_ != product} == {
            id_: row for id_, row in expected_inventory.items() if id_ != owned_blank}
        assert await inventory(run, "host") == expected_host
        proof["instructions"] = dict(first=first, offer=offer, second=second, exchanged=exchanged, receipt=account)
        return items, account

    async def inspect_buttons():
        attempts = []
        for variant in (None, *range(1, 13)):
            try:
                selector = {} if variant is None else {"amount": variant}
                placement = await action(run, fixture.APPROACH, **selector)
                await wait(guest_run, lambda state: state.get("game", {}).get(console.KEY, {}).get("state")
                           in ("armed", "focused"), timeout=8)
                first = (await action(guest_run, console.FIXTURE))[console.KEY]
                return placement, first
            except (TimeoutError, RuntimeError) as error:
                reason = str(error)
                if not isinstance(error, TimeoutError) and all(fragment not in reason for fragment in (
                        "console_approach_no_safe_floor", "fixture_stock_focus")):
                    raise
                attempts.append(dict(variant=variant, reason=reason))
                proof["stock_button_approach_attempts"] = attempts
        raise AssertionError("The stock client did not reach or focus the exact MindCore tape button")

    async def traverse(frame, route, *, receipts):
        for area, destination in zip(route, route[1:]):
            leg, = (row for row in AUTHORED_ROUTES if (row.source, row.destination) == (area, destination))
            frame = await travel(frame, (area, destination, leg.entrance, leg.kind, leg.actor), receipts=receipts)
        return frame

    async def stock_pickup(frame, source):
        for attempt in range(4):
            try:
                staged = await action(run, pickup.APPROACH, target="host", pickup_source=source)
                break
            except RuntimeError as error:
                if attempt == 3 or "no_clear_sight" not in str(error):
                    raise
                await asyncio.sleep(.4)
        area, actor, item_class = pickup.SOURCES[source]
        assert staged["actor"] == actor and frame["game"]["map"] == area
        receipt = (await action(run, pickup.STOCK_FIXTURE, pickup_source=source))["native_authored_pickup_interaction"]
        assert (receipt["entry"], receipt["source"], receipt["map"], receipt["pawn"]) == (
            "stock", source, area, frame["game"]["pawns"]["local"]["name"])
        picked = await wait(run, lambda state: state.get("campaign", {}).get("world", {}).get("party", {})
                            .get(pickup.STATE_KEY, {}).get(source, {}).get("owner") == "host", timeout=18)
        claim = picked["campaign"]["world"]["party"][pickup.STATE_KEY][source]
        assert (claim["source"], claim["map"], claim["actor"], claim["class_path"], claim["owner"]) == (
            source, area, actor, item_class, "host"), claim
        return {"interaction": receipt, "claim": claim}

    async def retrieve_lift_key(authority):
        path = (core.MAP, WALL, ANNEX, STONE_MAZE, DEAD_BRIDGE, CROSSROADS, CISTERN)

        async def collect_stock(frame, source):
            claim = (await stock_pickup(frame, source))["claim"]
            item_class = pickup.SOURCES[source][2]
            if source == pickup.CROSSROADS_LOCKPICK:
                # Leave the hazardous console as soon as the stock pickup is durable.
                await action(run, "native-door-position", target="host", entrance="CR_Cstrn", exit_position="near")
            item = (await inventory(run, "host"))[claim["guid"]]
            assert item["class_path"] == item_class
            return item

        authority = await traverse(authority, path[:-1], receipts="lift_key_routes")
        pick = await collect_stock(authority, pickup.CROSSROADS_LOCKPICK)
        assert (pick["source"], pick["quantity"]) == ("backpack", 2)
        proof["lift_stock_pick"] = pick
        authority = await traverse(authority, path[-2:], receipts="lift_key_routes")
        opened = await asyncio.wait_for(submit(run, pickup.CISTERN_LOCKER_FIXTURE), 45)
        if not opened.get("ok"):
            if opened.get("error") != "stock_cistern_nfta_locker_locked":
                raise RuntimeError(f"Stock Cistern locker preflight failed: {opened.get('error')}")
            attempts = proof["lift_key_lockpick_attempts"] = []
            before_host, before_guest = await inventory(run, "host"), await inventory(guest_run, character)
            charges = pick["quantity"]
            for _ in range(charges):
                result = (await action(run, lockpick.FIXTURE, target=pick["guid"]))[lockpick.KEY]
                attempts.append(result)
                assert result["charges_before"] == charges
                charges = result["charges_after"]
                if result["state"] == "unlocked" or not charges:
                    break
            after_host = await inventory(run, "host")
            assert after_host.get(pick["guid"], {}).get("quantity", 0) == charges
            assert {key: row for key, row in before_host.items() if key != pick["guid"]} == {
                key: row for key, row in after_host.items() if key != pick["guid"]}
            assert await inventory(guest_run, character) == before_guest
            if attempts[-1]["state"] != "unlocked":
                raise RuntimeError("The stock Cistern locker broke both genuine lockpick charges")
            opened = await action(run, pickup.CISTERN_LOCKER_FIXTURE)
        for _ in range(5):
            if opened["locker"] != "stock_cistern_nfta_locker_opening":
                break
            await asyncio.sleep(.25)
            opened = await action(run, pickup.CISTERN_LOCKER_FIXTURE)
        assert opened["locker"] == "stock_cistern_nfta_locker_opened", opened
        proof["lift_key_locker"] = opened["locker"]
        source = pickup.CISTERN_OLD_NFTA_KEYCARD
        key = await collect_stock(authority, source)
        proof["lift_stock_key"] = {"source": source, "guid": key["guid"], "class_path": key["class_path"]}
        return await traverse(authority, path[::-1], receipts="lift_key_routes")

    async def assert_key_custody(claim):
        key_guid = claim["guid"]
        host_items = await inventory(run, "host")
        matching = [item for item in host_items.values() if item["class_path"] == claim["class_path"]]
        assert len(matching) == 1, matching
        row = matching[0]
        assert (row["guid"], row["source"], row["quantity"], row["stackable"]) == (
            key_guid, "backpack", 1, False), row
        assert key_guid not in await inventory(guest_run, character)
        return row

    async def retrieve_savlo_keycard(authority):
        to_annex = (core.MAP, WALL, ANNEX)
        to_fort = (ANNEX, STONE_MAZE, DEAD_BRIDGE, CROSSROADS, RUIN_FIELDS, BEACH, pickup.FORT_LETO)
        keycard_proof = proof["upper_door_keycard"] = {}

        async def collect_key(frame, source, label):
            record = await stock_pickup(frame, source)
            keycard_proof[label] = record
            record["inventory"] = await assert_key_custody(record["claim"])
            return record

        authority = await traverse(authority, to_annex + to_fort[1:], receipts="upper_door_keycard_routes")
        fort_key = await collect_key(authority, pickup.FORT_LETO_LOCKER_KEY, "fort_leto_key")
        authority = await traverse(authority, to_fort[::-1] + (pickup.TRENCH,), receipts="upper_door_keycard_routes")

        locked = await wait(run, lambda state: savlo_locker.observed(state.get("game", {}), "locked"))
        initial = await wait(guest_run, lambda state: savlo_locker.observed(state.get("game", {}), "locked")
                             or savlo_locker.observed(state.get("game", {}), "unlocked"))
        guest_locker = initial["game"][savlo_locker.KEY]
        refused = await action(guest_run, pickup.ACTION, target=pickup.SAVLO_KEYCARD, success=False)
        assert refused["error"] == "native_authored_pickup_savlo_locker_closed", refused
        locker = keycard_proof["trench_locker"] = {"closed_claim_refused": refused["error"], "authority": []}
        for stage in ("unlocked", "opened"):
            report = (await action(run, savlo_locker.FIXTURE))[savlo_locker.KEY]
            assert (report["state"], report["actor"], report["map"], report["pawn"], report["locked"]) == (
                stage, savlo_locker.ACTOR, pickup.TRENCH, locked["game"][savlo_locker.KEY]["pawn"], False), report
            locker["authority"].append(report)
        projected = await wait(guest_run, lambda state: savlo_locker.observed(
            state.get("game", {}), "projected").get("epoch") == authority["zone_epoch"])
        locker["guest"] = projected["game"][savlo_locker.KEY]
        assert (locker["guest"]["actor"], locker["guest"]["pawn"], locker["guest"]["locked"]) == (
            guest_locker["actor"], guest_locker["pawn"], guest_locker["locked"])
        assert await assert_key_custody(fort_key["claim"]) == fort_key["inventory"]
        card = await collect_key(authority, pickup.SAVLO_KEYCARD, "savlo_card")
        authority = await traverse(authority, (pickup.TRENCH,) + to_annex[::-1], receipts="upper_door_keycard_routes")
        assert await assert_key_custody(card["claim"]) == card["inventory"]
        return authority

    async def ride_lift(authority):
        lift_proof = proof["lift"] = {}
        host_pawn = authority["game"]["pawns"]["local"]["name"]
        guest_pawn = authority["game"]["native_guest_save"]["pawn"]

        async def sample(label, stop, *, riders=True):
            deadline = asyncio.get_running_loop().time() + 16
            observed = {}
            while asyncio.get_running_loop().time() < deadline:
                main = runtime.read_json(run / "status.json").get("game", {})
                peer = runtime.read_json(guest_run / "status.json")
                peer_game = peer.get("game", {})
                host_view, guest_view = main.get(lift.KEY, {}), peer_game.get(lift.KEY, {})
                observed = {"host": host_view, "guest": guest_view, "host_riders": main.get(lift.RIDER_KEY, {}),
                            "guest_riders": peer_game.get(lift.RIDER_KEY, {})}
                if (lift_pose_matches(host_view, guest_view, stop)
                        and (peer.get("character_id"), peer.get("zone_epoch")) == (character, authority["zone_epoch"])
                        and peer.get("link_connected") is True
                        and (not riders or lift_riders_match(main, peer_game, host_pawn, guest_pawn))):
                    lift_proof[label] = observed
                    return
                await asyncio.sleep(.12)
            raise RuntimeError(f"Stock lift did not qualify {label}: {observed}")

        await sample("lower", "lower", riders=False)
        refused = await action(guest_run, lift.FIXTURE, target="guard")
        assert refused[lift.KEY]["stop"] == "lower"
        await asyncio.sleep(1)
        await sample("guest_power_refused", "lower", riders=False)
        for name, pawn in (("host", host_pawn), ("guest", guest_pawn)):
            staged = await action(run, lift.STAGE_RIDER, target=name)
            assert staged["pawn"] == pawn
        await sample("lower_boarded", "lower")
        button = "button" if lift_keycard else "power"
        first = await action(run, lift.FIXTURE, target=button)
        assert first["source_stop"] == "lower"
        await sample("ascending", "moving")
        await sample("upper_boarded", "upper")
        if lift_disembark:
            from datasilo_lift_exit import verify_disembark

            lift_proof["disembark"] = await verify_disembark(
                run, guest_run, action, host_pawn, guest_pawn, character, authority["zone_epoch"])
        else:
            second = await action(run, lift.FIXTURE, target=button)
            assert second["source_stop"] == "upper"
            await sample("descending", "moving")
            await sample("lower_returned", "lower")
        await action(run, "checkpoint")
        saved, stock_flags = await flags()
        assert tuple(row.get(core.FLAG, 0) for row in stock_flags) == (0, 0)
        if lift_keycard:
            key = proof["lift_stock_key"]
            assert (await inventory(run, "host"))[key["guid"]]["class_path"] == key["class_path"]
        lift_proof["input"] = button
        lift_proof["checkpoint"] = saved["revision"]

    try:
        copy_checkpoint(baseline, campaign, data)
        shutil.copyfile(inputs["identity"], campaign / "save/host-identity.json")
        (run / "invites").mkdir()
        shutil.copyfile(inputs["invite"], run / "invites" / f"{guest_id}.invite.json")
        host_job = start_host(0)
        opening = await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == start_area, timeout=55)
        port = opening["endpoint"]["port"]
        guest_job = await start_guest()
        authority = await paired(start_area)
        if stock_hazard_wipe:
            await verify_stock_hazard_wipe(authority)
            proof["verified"] = True
            return proof
        if guest_hazard_authority:
            from datasilo_core_lift import verify_guest_hazard_authority

            await verify_guest_hazard_authority(run, guest_run, action, authority, character,
                                                proof.setdefault("guest_hazard_authority", {}))
            proof["verified"] = True
            return proof
        if prepare_core_carry:
            await assert_key_custody(savlo_claim)
        if lift_keycard or prepare_core_carry:
            authority = await retrieve_lift_key(authority)
        if upper_door_keycard:
            authority = await retrieve_savlo_keycard(authority)
        owned_blank = await buy_blank() if instructions else None
        if not resume:
            for leg in ROUTE:
                authority = await travel(authority, leg)

        inactive = await wait(run, lambda state: (scene := state.get("game", {}).get(core.SCENE, {}))
                              and (scene["status"], scene["installed"]) == (0, False), timeout=20)
        proof["dormant"] = inactive["game"][core.SCENE]
        if route_probe:
            navigation = proof["stock_navigation"] = {}
            for target in lift.ROUTE_TARGETS:
                try:
                    navigation[target] = await action(run, lift.FIXTURE, target=target)
                except RuntimeError as error:
                    navigation[target] = {"error": str(error)}
            if navigation["route-west-landing"].get("complete") is not True:
                raise RuntimeError("The stock navigation query could not reproduce the known west walkway")
            proof["verified"] = True
            return proof
        if core_lift_carry:
            from datasilo_core_lift import verify_core_lift

            route_proof = proof["core_lift_carry"] = {"cards": [await assert_key_custody(claim) for claim in cards]}
            await verify_core_lift(run, guest_run, action, inactive, character, route_proof,
                                   lift_pose_matches=lift_pose_matches, lift_riders_match=lift_riders_match,
                                   raise_before_ascent=raise_before_ascent, park_on_deck=park_on_deck,
                                   fixture_loose_cargo=fixture_loose_cargo, fixture_passengers=fixture_passengers,
                                   west_walkway=west_walkway, east_stairs=east_stairs)
            await action(run, "checkpoint")
            saved, lift_flags = await flags()
            assert tuple(row.get(core.FLAG, 0) for row in lift_flags) == (0, 0)
            route_proof.update(checkpoint=saved["revision"], stock_flags=[0, 0])
            proof["verified"] = True
            return proof
        if prepare_core_carry:
            await action(run, "checkpoint")
            saved, dormant_flags = await flags()
            assert tuple(row.get(core.FLAG, 0) for row in dormant_flags) == (0, 0)
            ledger = saved["world"]["party"][pickup.STATE_KEY]
            nfta_claim = ledger[pickup.CISTERN_OLD_NFTA_KEYCARD]
            assert ledger[pickup.SAVLO_KEYCARD] == savlo_claim
            assert (nfta_claim["owner"], nfta_claim["guid"]) == ("host", proof["lift_stock_key"]["guid"])
            arrival = proof["lift_key_routes"][-1]["arrival"]
            positions = {"host": saved["world"]["host"], "guest": saved["characters"][character]["state"]}
            assert arrival["destination"] == core.MAP and len(proof["lift_key_routes"]) == 12
            for pose in positions.values():
                assert pose["map"] == core.MAP and pose["health"] > 0 and pose["location"][2] > 8000
                assert math.dist(pose["location"], arrival["location"]) < 350
            proof["carry_checkpoint"] = {
                "revision": saved["revision"],
                "cards": {claim["source"]: await assert_key_custody(claim) for claim in (savlo_claim, nfta_claim)},
                "positions": {seat: {key: pose[key] for key in ("health", "location")} for seat, pose in positions.items()},
                "stock_flags": [0, 0],
            }
            proof["verified"] = True
            return proof
        if upper_door:
            from datasilo_door_passage import verify_passage

            async def restart_guest():
                nonlocal guest_job
                departure = await action(guest_run, "leave-session")
                assert departure["retirement_confirmed"] is True
                await asyncio.wait_for(asyncio.shield(guest_job), 18)
                await wait(run, lambda state: state.get("game", {}).get("remote_clients") == 0
                           and all(row["character_id"] != character
                                   for row in state.get("campaign", {}).get("leases", ())), timeout=15)
                guest_job = await start_guest()
                await paired(core.MAP)
                return departure

            proof["upper_door"] = await verify_passage(
                run, guest_run, action, restart_guest, character, authority["generation"], authority["zone_epoch"],
                keycard=upper_door_keycard)
            await action(run, "checkpoint")
            saved, door_checkpoint_flags = await flags()
            assert tuple(row.get(core.FLAG, 0) for row in door_checkpoint_flags) == (0, 0)
            if upper_door_keycard:
                for label in ("fort_leto_key", "savlo_card"):
                    record = proof["upper_door_keycard"][label]
                    claim = record["claim"]
                    assert saved["world"]["party"][pickup.STATE_KEY][claim["source"]] == claim
                    assert await assert_key_custody(claim) == record["inventory"]
            proof["upper_door"]["checkpoint"] = saved["revision"]
            proof["verified"] = True
            return proof
        if lift_ride:
            await ride_lift(authority)
            proof["verified"] = True
            return proof
        if shared_core:
            await shared_core_at(0, label="dormant")
        locked = await action(guest_run, core.UPLOAD, target=guid, success=False)
        assert locked["error"] == core.LOCKED, locked
        if stock_input:
            await view("pass-through", 0, 0)
            dormant_f = await action(guest_run, console.FIXTURE, success=False)
            assert dormant_f["error"] == "The exact stock MindCore upload is unavailable", dormant_f
        await action(run, "checkpoint")
        dormant_checkpoint, dormant_flags = await flags()
        assert tuple(row.get(core.FLAG, 0) for row in dormant_flags) == (0, 0), dormant_flags
        if guest_carry:
            original_guest, original_host = await inventory(guest_run, character), await inventory(run, "host")
            await action(run, carry.APPROACH_FIXTURE, target=character)
            readied = await wait(guest_run, lambda state: (
                state.get("game", {}).get(carry.VIEW, {}).get("state") == "ready"
                and math.dist(state["game"]["pawns"]["local"]["location"], [714.39, -1467, 134.85]) <= 20
            ), timeout=20)
            assert readied["character_id"] == character
            host_before = await shared_core_at(0, label="before_guest_grab")
            await action(guest_run, carry.FIXTURE, target="grab")

            def active_carry(state):
                game = state.get("game", {})
                row = game.get(carry.AUTHORITY, {})
                return (row.get("state") == "held" and row.get("core") == core.CORE
                        and row.get("pawn") == game.get("native_guest_save", {}).get("pawn"))

            grabbing = await wait(run, active_carry, timeout=14)
            held = grabbing["game"][carry.AUTHORITY]
            await wait(guest_run, lambda state: state.get("game", {}).get(carry.VIEW, {}).get("state") == "held", timeout=12)
            await wait(run, lambda state: active_carry(state)
                       and state["game"][carry.AUTHORITY]["sequence"] > held["sequence"], timeout=14)
            after_grab = await shared_core_at(0, label="guest_held")
            # Move away from the Core on the stock approach before aiming sideways into the room.
            before_walk = runtime.read_json(run / "status.json")["game"]["pawns"]["guest"]["location"]
            motion = proof["guest_carry_motion"] = {}

            def moving_with_core(state):
                game = state.get("game", {})
                pawn = game.get("pawns", {}).get("guest", {})
                prop = game.get(core.REPLICATION, {})
                if not active_carry(state) or pawn.get("name") != held["pawn"] or "position" not in prop:
                    return False
                motion.update(pawn_y=round(pawn["location"][1] - before_walk[1], 3),
                              core_y=round(prop["position"][1] - after_grab["host"]["position"][1], 3))
                return motion["pawn_y"] >= 20 and motion["core_y"] >= 20

            for _ in range(3):
                await action(guest_run, "walk", x=0, y=1, milliseconds=1000)
                await asyncio.sleep(.6)
                if moving_with_core(runtime.read_json(run / "status.json")):
                    break
            await wait(run, moving_with_core, timeout=4)
            assert runtime.read_json(guest_run / "status.json")["game"][carry.VIEW]["state"] == "held"
            await shared_core_at(0, label="guest_walked_with_core")
            turn = await action(guest_run, carry.FIXTURE, target="turn")
            aim = turn["aim"]
            assert abs((aim["yaw"] - held["yaw"] + 180) % 360 - 180) >= 15
            turned = await wait(run, lambda state: active_carry(state)
                                and (row := state["game"][carry.AUTHORITY])["sequence"] > held["sequence"]
                                and row["reason"] == "stock_core_renewed"
                                and abs((row["yaw"] - aim["yaw"] + 180) % 360 - 180) <= 1, timeout=5)
            assert turned["game"][carry.AUTHORITY]["lease"] == held["lease"]
            await shared_core_at(0, label="guest_turned_core")
            pitch = round(aim["pitch"] + (8 if aim["pitch"] <= 80 else -8), 3)
            yaw = round((aim["yaw"] - 15 + 180) % 360 - 180, 3)
            await action(guest_run, carry.FIXTURE, target=f"turn@{pitch:.3f}@{yaw:.3f}")
            aimed = await wait(run, lambda state: active_carry(state)
                               and (row := state["game"][carry.AUTHORITY])["lease"] == held["lease"]
                               and row["sequence"] > turned["game"][carry.AUTHORITY]["sequence"]
                               and abs(row["pitch"] - pitch) <= 1
                               and abs((row["yaw"] - yaw + 180) % 360 - 180) <= 1, timeout=5)
            await shared_core_at(0, label="guest_aimed_core")
            await action(guest_run, carry.FIXTURE, target="release")
            released = await wait(run, lambda state: (
                (row := state.get("game", {}).get(carry.AUTHORITY, {})).get("state") == "released"
                and row.get("lease") == held["lease"] and row.get("pawn") == held["pawn"]
            ), timeout=14)
            assert released["game"][carry.AUTHORITY]["reason"] == "stock_core_released"
            actual_release = await shared_core_at(0, label="guest_released")
            assert await inventory(guest_run, character) == original_guest
            assert await inventory(run, "host") == original_host
            await action(run, "checkpoint")
            saved, released_flags = await flags()
            assert saved["revision"] > dormant_checkpoint["revision"]
            assert tuple(row.get(core.FLAG, 0) for row in released_flags) == (0, 0)
            assert core.LEDGER not in saved["world"]["party"]
            proof.update(verified=True, guest_carry={key: {
                name: value for name, value in row.items() if name != "lease"
            } for key, row in (("held", held), ("turn", turned["game"][carry.AUTHORITY]),
                               ("aim", aimed["game"][carry.AUTHORITY]),
                               ("release", released["game"][carry.AUTHORITY]))},
                carry_checkpoint=saved["revision"], carry_stock_flags=[0, 0], inventories_verified=True,
                guest_core_displacement_uu=round(math.dist(host_before["guest"]["position"],
                                                           actual_release["guest"]["position"]), 3))
            return proof

        if carry_only:
            proof["stock_carry"] = {}
            poses = proof["core_poses"] = {"before": await core_poses()}
            previous = poses["before"]
            for step, expected in (("grab", "held"), ("step", "carried"), ("release", "released")):
                result = await action(run, fixture.CARRY, target=step)
                observed = result[fixture.CARRY_KEY]
                assert (observed["map"], observed["core"], observed["state"]) == (core.MAP, core.CORE, expected)
                assert (result[core.SCENE]["status"], result[core.SCENE]["installed"]) == (0, False)
                proof["stock_carry"][step] = observed
                poses[step] = previous = await core_poses(previous)
                if shared_core:
                    await shared_core_at(0, label=step)
                    if step == "step":
                        dropped = await action(guest_run, "drop-link")
                        assert dropped["link_connected"] is False
                        await wait(run, lambda state: any(
                            row["character_id"] == character and row["state"] == "reserved"
                            for row in state.get("campaign", {}).get("leases", ())
                        ), timeout=16)
                        recovered = await action(guest_run, "reconnect")
                        assert recovered.get("resumed") is True and recovered.get("link_connected") is True
                        await paired(core.MAP)
                        proof["core_carry_guest_reconnected"] = True
                        await shared_core_at(0, label="reconnected_while_held")
            movement = proof["stock_carry"]["step"]
            assert movement["pawn_movement_uu"] >= 20 and movement["core_movement_uu"] >= 20, movement
            assert all(row["pawn"] == movement["pawn"] for row in proof["stock_carry"].values())
            proof["core_displacement"] = {role: round(math.dist(poses["before"][role]["position"],
                                                               poses["release"][role]["position"]), 2)
                                          for role in ("host", "guest")}
            if shared_core:
                assert proof["core_displacement"]["guest"] >= 20, proof["core_displacement"]
                assert poses["step"]["host"]["replicates"] and poses["step"]["host"]["replicate_movement"]
                assert (poses["step"]["guest"]["local_role"], poses["step"]["guest"]["replicate_movement"]) == (1, True)
            await paired(core.MAP)
            await action(run, "checkpoint")
            saved, held_flags = await flags()
            assert saved["revision"] > dormant_checkpoint["revision"]
            assert tuple(row.get(core.FLAG, 0) for row in held_flags) == (0, 0)
            assert core.LEDGER not in saved["world"]["party"]
            proof.update(verified=True, carry_checkpoint=saved["revision"],
                         carry_stock_flags=[0, 0], physical_test="stock_grab_short_carry_release")
            return proof

        installed = (await action(run, fixture.INSTALL))[core.SCENE]
        _, mounted_flags = await flags()
        assert (installed["status"], installed["installed"]) == (1, True), installed
        assert tuple(row.get(core.FLAG, 0) for row in mounted_flags) == (1, 0), mounted_flags
        proof["installation"] = {"scene": installed, "headless_relocation": True, "stock_flags": [1, 0]}
        if shared_core:
            await shared_core_at(1, label="mounted")

        near, first = await inspect_buttons() if stock_input else (await action(run, fixture.APPROACH), None)
        if instructions:
            early = await action(guest_run, INSTRUCTIONS.verb("preflight"), target=owned_blank, success=False)
            assert early["error"] == "native_drill_instructions_compiled_console_required", early
            await download_state("waiting", 0, 0)
            proof["instructions_before_compilation"] = early["error"]
        wrong = await action(guest_run, core.UPLOAD, target=blank, success=False)
        assert wrong["error"] == "native_mindcore_refused_durable_results_owner", wrong
        ready = (await action(guest_run, core.PREFLIGHT, target=guid))[core.PROOF]
        assert (ready["status"], ready["pawn"], ready["guid"], ready["proof"]) == (1, near["pawn"], guid, "owned"), ready
        original_guest, original_host = await inventory(guest_run, character), await inventory(run, "host")
        assert (original_guest[guid]["class_path"], original_guest[guid]["quantity"], original_guest[guid]["source"]) == (
            DRILL.product, 1, "backpack"), original_guest[guid]
        assert guid not in original_host and blank not in original_guest
        if stock_input:
            opened = first["sequence"]
            assert (first["state"], first["opened"], first["guid"], first["cradle"]) == ("checking", opened, None, core.CRADLE)
            offer = await view("offer", opened, opened)
            assert offer["guid"] == guid
            _, checked = await flags()
            assert tuple(row.get(core.FLAG, 0) for row in checked) == (1, 0)
            second = (await action(guest_run, console.FIXTURE))[console.KEY]
            assert (second["state"], second["opened"], second["guid"], second["pawn"]) == ("submitting", opened, guid, first["pawn"])
            assert second["sequence"] > opened
            compiled = await view("compiled", second["sequence"], opened)
            assert compiled["guid"] == guid
            proof["stock_buttons"] = dict(first=first, offer=offer, second=second, compiled=compiled)
        else:
            operation_id = uuid.uuid4().hex
            result = await action(guest_run, core.UPLOAD, target=guid, operation_id=operation_id)
            assert result["effect"] == "native_mindcore_results_compiled", result
        final, compiled_flags = await flags()
        assert tuple(row.get(core.FLAG, 0) for row in compiled_flags) == (2, 0), compiled_flags
        assert {key: value for key, value in compiled_flags[0].items() if key != core.FLAG} == {
            key: value for key, value in mounted_flags[0].items() if key != core.FLAG}
        assert compiled_flags[1] == mounted_flags[1]
        receipt = final["world"]["party"][core.LEDGER]
        assert core.valid_receipt(final["world"]["party"], final["characters"])
        if stock_input:
            operation_id = receipt["operation_id"]
        assert (receipt["operation_id"], receipt["owner"], receipt["guid"]) == (operation_id, character, guid)
        assert await inventory(guest_run, character) == original_guest and await inventory(run, "host") == original_host
        proof["upload"] = {"locked": locked["error"], "wrong_guid": wrong["error"], "preflight": ready,
                           "receipt": receipt, "tape": original_guest[guid], "stock_flags": [2, 0]}
        final_guest, instructions_receipt = (await download_tape(owned_blank, original_guest, original_host, compiled_flags)
                                             if instructions else (original_guest, None))

        await stop(guest_run, guest_job)
        await stop(run, host_job)
        start_host(port)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != authority["generation"], timeout=55)
        await start_guest()
        restarted = await paired(core.MAP, (authority["generation"], authority["zone_epoch"]))
        assert restarted["endpoint"]["port"] == port
        restored = await wait(run, lambda state: core._ready(state.get("game", {}), 2), timeout=25)
        if shared_core:
            await shared_core_at(2, label="late_join_after_restart")
        if stock_input:
            await view("pass-through", 0, 0)
            stage_two = await action(guest_run, console.FIXTURE, success=False)
            assert stage_two["error"] == "The exact stock MindCore upload is unavailable", stage_two
            proof["stage_two_upload_input"] = "pass-through"
        replay = await action(guest_run, core.UPLOAD, target=guid, operation_id=operation_id)
        assert replay["effect"] == "native_mindcore_already_compiled", replay
        assert await inventory(guest_run, character) == final_guest and await inventory(run, "host") == original_host
        after, restored_flags = await flags()
        assert after["world"]["party"][core.LEDGER] == receipt
        assert tuple(row.get(core.FLAG, 0) for row in restored_flags) == (2, 0), restored_flags
        commits = [row for row in events() if row["event"] == "native_mindcore_results_committed"]
        assert len(commits) == 1 and commits[0]["operation_id"] == operation_id, commits
        if instructions:
            await action(run, fixture.APPROACH)
            instructions_replay = await action(guest_run, INSTRUCTIONS.verb("convert"), target=owned_blank,
                                               operation_id=instructions_receipt["operation_id"])
            assert instructions_replay["effect"] == INSTRUCTIONS.effect + "_already_exchanged"
            assert after["world"]["party"][INSTRUCTIONS.ledger] == {owned_blank: instructions_receipt}
            assert await inventory(guest_run, character) == final_guest
            exchanges = [row for row in events() if row["event"] == INSTRUCTIONS.key + "_committed"]
            assert len(exchanges) == 1 and exchanges[0]["filled_guid"] == instructions_receipt["filled_guid"]
            proof["instructions"]["restart_replay"] = instructions_replay["effect"]
        proof.update(restart={"port": port, "scene": restored["game"][core.SCENE], "stock_flags": [2, 0],
                              "replay": replay["effect"], "upload_commits": len(commits)}, verified=True)
    except BaseException as error:
        proof.update(verified=False, failure=f"{type(error).__name__}: {error}")
        raise
    finally:
        for directory, task in reversed(jobs):
            if not task.done():
                try:
                    await stop(directory, task)
                except (OSError, RuntimeError, TimeoutError, ValueError):
                    task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        current_originals = saved_hashes(vanilla_saved)
        proof["original_saves_changed_paths"] = sorted(
            path for path in originals.keys() | current_originals.keys() if originals.get(path) != current_originals.get(path)
        )
        proof["original_saves_verified"] = not proof["original_saves_changed_paths"]
        proof["baseline_verified"] = initial_inputs == {key: sha256(path.read_bytes()).hexdigest() for key, path in inputs.items()}
        isolated = proof["original_saves_verified"] and proof["baseline_verified"]
        proof["verified"] = isolated and proof.get("verified", False)
        runtime.write_json(run / REPORT, proof)
        assert isolated, (f"Changed original files: {proof['original_saves_changed_paths']}. "
                          f"Source checkpoint matched: {proof['baseline_verified']}")
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--vanilla-saved", required=True, type=Path)
    parser.add_argument("--resume", action="store_true", help="clone the paired dormant Data Silo stage-zero checkpoint")
    parser.add_argument("--stock-input", action="store_true", help="confirm the upload with two genuine stock Interact presses")
    parser.add_argument("--instructions", action="store_true", help="buy a second blank and use the compiled console's stock exchange")
    parser.add_argument("--carry-only", action="store_true", help="grab, move, and release the Core with stock gameplay")
    parser.add_argument("--shared-core", action="store_true", help="verify the guest sees the authoritative exact Core")
    parser.add_argument("--guest-carry", action="store_true", help="use real guest Interact and aim to carry the exact Core")
    parser.add_argument("--lift-ride", action="store_true", help="ride the native lift with both players and verify both stops")
    parser.add_argument("--lift-keycard", action="store_true", help="get the Cistern keycard and operate the stock lift button")
    parser.add_argument("--lift-disembark", action="store_true", help="walk both players to the stock upper landing")
    parser.add_argument("--upper-door", action="store_true", help="verify physical passage and late join at the upper door")
    parser.add_argument("--upper-door-keycard", action="store_true",
                        help="collect the stock Savlo keycard and unlock the upper door with genuine Interact")
    parser.add_argument("--stock-hazard-wipe", action="store_true",
                        help="verify stock lower-lift damage downs both players before a wipe to the host bed")
    parser.add_argument("--prepare-core-carry", action="store_true",
                        help="clone the verified Savlo checkpoint and acquire the Cistern card for the same host")
    parser.add_argument("--core-lift-carry", action="store_true",
                        help="regrip the exact stock Core and carry it through the lift to the upper west landing")
    parser.add_argument("--core-lift-raise-before-ascent", action="store_true",
                        help="try raising the held Core before the lower lift starts moving")
    parser.add_argument("--core-lift-park-on-deck", action="store_true",
                        help="use stock carry and release to park the Core on the lower deck before ascent")
    parser.add_argument("--core-lift-fixture-loose-cargo", action="store_true",
                        help="use an isolated fixture to load the Core and test only the stock loose-cargo ascent")
    parser.add_argument("--core-lift-fixture-passengers", action="store_true",
                        help="stage both riders for the loose-cargo test and continue to stock pickup and disembark")
    parser.add_argument("--core-lift-west-walkway", action="store_true",
                        help="continue the parked-Core test from the landing along the stock west walkway")
    parser.add_argument("--core-lift-probe-route", action="store_true",
                        help="survey the stock navigation mesh from the west waypoint without moving any pawn")
    parser.add_argument("--core-lift-east-stairs", action="store_true",
                        help="continue the west walkway carry to the first east stair")
    parser.add_argument("--guest-hazard-authority", action="store_true",
                        help="verify stock lower-floor damage downs the guest through the host only")
    options = parser.parse_args()
    paths = (value.resolve(strict=True) for value in (options.work, options.baseline, options.vanilla_saved))
    asyncio.run(exercise(*paths, resume=options.resume, stock_input=options.stock_input or options.instructions,
                         instructions=options.instructions, carry_only=options.carry_only, shared_core=options.shared_core,
                          guest_carry=options.guest_carry, lift_ride=options.lift_ride, lift_keycard=options.lift_keycard,
                          lift_disembark=options.lift_disembark, upper_door=options.upper_door,
                         upper_door_keycard=options.upper_door_keycard, stock_hazard_wipe=options.stock_hazard_wipe,
                         prepare_core_carry=options.prepare_core_carry, core_lift_carry=options.core_lift_carry,
                         guest_hazard_authority=options.guest_hazard_authority,
                         raise_before_ascent=options.core_lift_raise_before_ascent,
                         park_on_deck=options.core_lift_park_on_deck,
                         fixture_loose_cargo=options.core_lift_fixture_loose_cargo,
                         fixture_passengers=options.core_lift_fixture_passengers,
                         west_walkway=options.core_lift_west_walkway,
                         route_probe=options.core_lift_probe_route,
                         east_stairs=options.core_lift_east_stairs))
