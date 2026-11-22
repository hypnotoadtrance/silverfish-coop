"""Verify an authored stock target through a headless guest's real weapon input."""

import argparse
import asyncio
from hashlib import file_digest
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import status, wait
from live_computer_board_smoke import stock_flags
from live_guest_held_fire_smoke import events
from live_multi_smoke import saved_hashes
from live_turret_photo_smoke import BOROUGH, CITY, RAIL, RUINS


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.native_flood_jammer import (
    BLACK_LAKE, CROSSROADS_JAMMER, DEFERRED_SAVE_BOUNTIES, FLOOD, LENNY, POSE, SPRATT, same_pose,
)
from tools.silverfish import native_lenny, native_spratt, native_spratt_fixture
from tools.silverfish.guest_weapon_profiles import NINE_MM_BALL, TWELVE_GAUGE_BUCK
from tools.silverfish.travel import CROSSROADS


def quest_record(campaign, quest):
    return campaign.get("world", {}).get("party", {}).get("native_quests", {}).get(quest, {})


async def exercise(work, vanilla_saved, *, client_projection=False, pose_only=False, spec=FLOOD, stock_menu=False):
    lake, crossroads, lenny = spec == BLACK_LAKE, spec == CROSSROADS_JAMMER, spec == LENNY
    spratt = spec == SPRATT
    assassination = lenny or spratt
    stock_menu = stock_menu or crossroads
    stock_board_input = lake or crossroads or assassination
    defer_save = spec in DEFERRED_SAVE_BOUNTIES
    weapon = "sg-71-a" if assassination else "light-pistol"
    weapon_label = "shotgun" if assassination else "pistol"
    ammo = TWELVE_GAUGE_BUCK if assassination else NINE_MM_BALL
    quest, board_map, area = spec.quest, spec.board_map, spec.objective_map
    level = area + "." + area.rsplit("/", 1)[1] + ":PersistentLevel."
    objective_key = f"{spec.key}_objective"
    objective_flags = ("Adv_Freelancer", "Adv_Coup") if lake or crossroads else ("LennyDead", "Player_Kills") if assassination else ()
    faction = "8" if stock_board_input else "2"
    outbound = ((area, "BlackMarket"),) if lake else ((CITY, "City_Borough"), (RAIL, "Rail_City"), (RUINS, "Ry_Rf"))
    returning = ((board_map, "BlackMarket"),) if lake else ((RAIL, "Ry_Rf"), (CITY, "Rail_City"), (BOROUGH, "City_Borough"))
    if crossroads:
        outbound, returning = ((area, "Obsv_Etr"),), ((board_map, "Obsv_Etr"),)
    if lenny:
        lake_area = BLACK_LAKE.objective_map
        outbound = ((lake_area, "BlackMarket"), (area, "Depot_Lake"))
        returning = ((lake_area, "Depot_Lake"), (board_map, "BlackMarket"))
        qualification_route = ((CITY, "City_Borough"), (RAIL, "Rail_City"), *returning)
    elif spratt:
        lake_area = BLACK_LAKE.objective_map
        outbound = ((lake_area, "BlackMarket"), (CROSSROADS, "Lake_Cave"), (area, "CR_Cstrn"))
        returning = ((CROSSROADS, "CR_Cstrn"), (lake_area, "Lake_Cave"), (board_map, "BlackMarket"))
        qualification_route = (*outbound, *returning)
    name = spec.command().removeprefix("native-") + "-" + uuid.uuid4().hex[:8]
    host, client = ("--campaign", name), ("--profile", name)
    campaign = work / "campaigns" / name
    run, client_run = campaign / "run", work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    if vanilla_saved is not None and not vanilla_saved.is_dir():
        raise ValueError(f"Original Saved folder does not exist: {vanilla_saved}")
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    if stock_menu:
        host_flags += ("--native-shop-view",)
    client_flags = ("--headless", "--native-zones", "--native-guest-primary-input")
    evidence = {"quest": quest, "routes": [], "shots": [], "stock_board_input": stock_board_input,
                "stock_menu": stock_menu, "client_projection_requested": client_projection}
    processes = []
    character = None

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, errors = await asyncio.wait_for(process.communicate(), 40)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if not output or (process.returncode == 0) != expected:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(output)

    async def action(owner, verb, *arguments, expected=True):
        return await command("action", *owner, verb, *arguments, expected=expected)

    async def launch(owner, label, *arguments):
        with (run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((owner, process))
        return process

    async def stop(owner, process):
        if process.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(process.wait(), 18) == 0, process.returncode

    async def enrolled(area, previous=None):
        def bound(state):
            game = state.get("game", {})
            return (state.get("state") == "hosting" and game.get("map") == area
                    and (state.get("generation"), state.get("zone_epoch")) != previous
                    and game.get("native_guest_save", {}).get("state") == "bound"
                    and any(lease["character_id"] == character and lease["state"] == "active"
                            for lease in state.get("campaign", {}).get("leases", ())))

        authority = await wait(run, bound, timeout=75)
        await wait(client_run, lambda state: state.get("state") == "connected" and state.get("link_connected") is True
                   and state.get("character_id") == character and state.get("host_session") == authority["generation"]
                   and state.get("zone_epoch") == authority["zone_epoch"] and state.get("game", {}).get("map") == area,
                   timeout=75)
        return authority

    async def cross(current, destination, tag):
        source = current["game"]["map"]
        matches = [route for route in AUTHORED_ROUTES if (route.source, route.destination, route.entrance) == (source, destination, tag)]
        assert len(matches) == 1, (source, destination, tag, matches)
        stock_route = matches[0]
        route = {"from": source, "to": destination, "entrance": tag, "actor": stock_route.actor, "kind": stock_route.kind}
        evidence["pending_route"] = route
        if stock_route.kind == "door":
            for role in ("host", "guest"):
                await action(host, "native-door-position", "--target", role, "--exit-position", "near", "--entrance", tag)
            await action(host, "native-door-probe", "--target", "host", "--entrance", tag)
        elif stock_route.kind == "overlap":
            endpoint = ("--entrance", tag, "--exit-actor", stock_route.actor)
            for role in ("host", "guest"):
                await action(host, "native-exit-position", "--target", role, "--exit-position", "near", *endpoint)
            await action(host, "native-exit-position", "--target", "host", "--exit-position", "inside", *endpoint)
        else:
            raise ValueError(f"Unexpected stock jammer route {stock_route.kind}")
        arrived = await enrolled(destination, (current["generation"], current["zone_epoch"]))
        evidence["routes"].append({**route, "zone_epoch": arrived["zone_epoch"]})
        del evidence["pending_route"]
        return arrived

    async def board():
        approached = (await action(host, spec.command("approach"), "--target", "guest"))[f"{spec.key}_approach"]
        readback = (await action(client, spec.command("preflight")))[spec.key]
        assert (readback["map"], readback["board"], readback["quest"], readback["pawn"]) == (
            board_map, spec.board, quest, approached["pawn"],
        ), readback
        return readback

    async def board_state(*states, selected=True):
        state = await wait(client_run, lambda frame: (view := frame.get("game", {}).get("native_wire_board", {}))
                           and (view.get("map"), view.get("board")) == (board_map, spec.board)
                           and (not selected or view.get("quest") == quest) and view.get("state") in states)
        return state["game"]["native_wire_board"]

    async def board_press(expected):
        pressed = (await action(client, "native-wire-board-fixture", "--target", quest))["native_wire_board"]
        assert (pressed["map"], pressed["board"], pressed["quest"], pressed["state"]) == (board_map, spec.board, quest, expected), pressed
        evidence.setdefault("board_presses", []).append(pressed)

    async def stock_button(verb, expected):
        pressed = (await action(client, "native-wire-board-stock-fixture", "--target", f"{quest}|{verb}"))["native_wire_board_stock"]
        assert (pressed["map"], pressed["board"], pressed["quest"], pressed["state"]) == (board_map, spec.board, quest, expected), pressed
        evidence.setdefault("board_presses", []).append(pressed)

    def live_quest(frame):
        return frame.get("gameplay", {}).get("native_quests", {}).get(quest, {})

    async def committed(step):
        result = await wait(run, lambda frame: live_quest(frame).get("step") == step)
        return live_quest(result)

    def read_saved():
        return json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))

    def save_files():
        hashes = {}
        if not defer_save:
            return hashes
        for folder in ("save", "game-user/Saved/SaveGames"):
            for path in (campaign / folder).rglob("*"):
                # The campaign lease exclusively locks a byte while the host runs.
                if path.is_file() and path.name != ".campaign.lock":
                    with path.open("rb") as stream:
                        hashes[path.relative_to(campaign)] = file_digest(stream, "sha256").hexdigest()
        return hashes

    def require_deferred_save(phase, before):
        if defer_save:
            assert save_files() == before, f"The stock bounty wrote save files during {phase}"
            evidence.setdefault("no_save_during_bounty", []).append(phase)

    async def balances():
        await action(host, "checkpoint")
        saved = read_saved()
        stock = saved["world"]["native_host_save"]
        guest = saved["characters"][character]["state"]["native_guest_save"]
        assert guest["character_id"] == character and guest["slot"] != stock["slot"], (stock, guest)
        root = campaign / "game-user" / "Saved" / "SaveGames"
        result = {}
        for owner, frame in (("host", stock), ("guest", guest)):
            flags = stock_flags(root, frame, board_map)
            result[owner] = {key: flags.get(key, 0) for key in ("Player_Cash", "Player_Xp", *objective_flags)}
        return result

    async def projected(*states):
        key = f"{spec.key}_projection"
        client_state = await wait(client_run, lambda frame: (view := frame.get("game", {}).get(key, {}))
                                  and (view.get("map"), view.get("actor")) == (area, spec.jammer)
                                  and view.get("state") in states, timeout=20)
        return client_state["game"][key]

    async def fire(guid, magazine):
        try:
            aim = (await action(host, spec.command("aim"), "--target", "guest", "--item-guid", guid))[f"{spec.key}_aim"]
        except RuntimeError:
            if assassination and guest_downed(status(run)):
                return None
            raise
        target = evidence["stock_watch"]["target"] if assassination else spec.jammer
        assert (aim["map"], aim["target" if assassination else "jammer"], aim["guid"]) == (area, target, guid), aim
        max_range = 1600 if crossroads else 550
        assert 75 <= aim["distance"] <= max_range and aim["component"].split(" ", 1)[-1].startswith(target.split(" ", 1)[-1] + "."), aim
        try:
            press = (await action(client, "native-guest-primary-input-fixture"))["native_guest_primary_fixture"]
        except RuntimeError:
            if spratt and guest_downed(status(run)):
                return None
            raise
        attempt = {"aim": aim, "press": press}
        evidence["shots"].append(attempt)
        assert (press["map"], press["guid"], press["stock_wrappers"]) == (area, guid, 2), press
        for _ in range(36):
            candidates = events(client_run, "native_guest_primary_input")
            candidates += events(client_run, "native_guest_primary_input_rejected")
            event = next((row for row in reversed(candidates) if row.get("sequence") == press["sequence"]
                          and row.get("generation") == status(client_run).get("generation")), None)
            if event:
                attempt["receipt"] = event
                if (assassination and event["event"] == "native_guest_primary_input_rejected"
                        and event.get("reason") in ("downed", "stock_flood_jammer_aim_standing_guest_required")
                        and guest_downed(status(run))):
                    return None
                assert event["event"] == "native_guest_primary_input", event
                shot = event["native_guest_weapon"]
                assert (shot["map"], shot["pawn"], shot["guid"], shot["before"], shot["after"], shot["projectiles"]) == (
                    area, aim["pawn"], guid, magazine, magazine - 1, ammo.projectile_count,
                ), shot
                return shot
            if spratt and guest_downed(status(run)):
                attempt["blocked"] = "canonical_guest_downed"
                return None
            await asyncio.sleep(.1)
        raise RuntimeError(f"No authoritative stock primary-input receipt for sequence {press['sequence']}")

    def guest_downed(state):
        return state.get("gameplay", {}).get("players", {}).get("guest", {}).get("downed") is True

    def objective_committed(state):
        return live_quest(state).get("step") == 2

    async def revive_assassination_guest():
        armed = await wait(run, lambda state: objective_committed(state) or (
            guest_downed(state) and state.get("game", {}).get("player_effects", {}).get("guest", {}).get("state") == "downed"
        ), timeout=5)
        if objective_committed(armed):
            return
        route = native_spratt_fixture.REVIVE if spratt else native_lenny.REVIVE_APPROACH
        key = native_spratt_fixture.REVIVE_KEY if spratt else "native_lenny_revive_approach"
        approach = (await action(host, route, "--target", "guest"))[key]
        assert (approach["map"], approach["target"]) == (area, evidence["stock_watch"]["target"]), approach
        if spratt:
            recovered = await action(host, "native-interaction-fixture")
            assert recovered["native_interaction_fixture"] is True, recovered
        else:
            recovered = await action(host, "interact", "--target", character)
            assert (recovered["effect"], recovered["target"]) == ("revive", character), recovered
        settled = await wait(run, lambda state: objective_committed(state) or (
            state.get("game", {}).get("native_guest_revivals", {}).get(approach["pawn"])
        ), timeout=6)
        native = settled["game"].get("native_guest_revivals", {}).get(approach["pawn"])
        if spratt:
            assert native and native["map"] == area and native["pawn"] == approach["pawn"], native
        if native:
            assert native["owner"] == character and math.isclose(native["health"], native["maximum"] * .10, abs_tol=.01), native
        evidence["ordinary_guest_revivals"].append({"approach": approach, "interaction": recovered, "native": native})

    try:
        start_map = area if pose_only else BOROUGH if lenny else board_map
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", start_map, *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == start_map, timeout=75)
        if lenny:
            await action(host, "native-wire-quest-approach", "--target", "host")
            evidence["stock_xp_eligibility_fixture"] = await action(host, "native-turret-photo-xp-fixture")
        invitation = await command("invite", *host, "--name", "Jammer guest", "--character", "Signal Hunter")
        character = invitation["character_id"]
        visitor = await launch(client, "guest", "join", *client, "--invite", invitation["invite_file"], *client_flags)
        current = await enrolled(start_map)
        if pose_only:
            def current_pose(state):
                row = state.get("game", {}).get(POSE, {})
                if row.get("actor") == spec.jammer and row.get("map") == area:
                    return row

            authority = current_pose(await wait(run, current_pose, timeout=20))

            def synced(state):
                row = current_pose(state)
                return row and same_pose(row["transform"], authority["transform"])

            peer = current_pose(await wait(client_run, synced, timeout=20))
            evidence.update(host_jammer=authority, guest_jammer=peer, verified=True)
            return
        port = current["endpoint"]["port"]
        if spratt:
            await action(host, spec.command("approach"), "--target", "host")
            hidden = await action(host, spec.command("preflight"), expected=False)
            assert hidden["error"] == "stock_spratt_quest_not_offered", hidden
            eligibility = (await action(host, spec.command("xp-fixture")))[spec.key]
            assert (eligibility["step_before"], eligibility["step_after"], eligibility["xp_before"], eligibility["xp_after"]) == (
                None, None, 0, 7), eligibility
            evidence["stock_xp_eligibility_fixture"] = eligibility
            evidence["stock_ineligible"] = hidden
        if assassination:
            for destination, tag in qualification_route:
                current = await cross(current, destination, tag)
                if destination == area:
                    report = await wait(run, lambda frame: frame.get("game", {}).get(objective_key, {}).get("state")
                                        == f"stock_{spec.key.removeprefix('native_')}_target_unavailable")
                    evidence["stock_precontract_spawn"] = report["game"][objective_key]

        guid = uuid.uuid4().hex.upper()
        seed_command = ("native-guest-weapon-seed", "--kind", weapon) if assassination else ("native-guest-weapon-seed-light-pistol",)
        seeded = (await action(host, *seed_command, "--target", "guest", "--item-guid", guid))["native_guest_weapon"]
        evidence[f"stock_{weapon_label}_seed"] = seeded
        assert (seeded["guid"], seeded["status"], seeded["stored_ammo"]) == (guid, "backpack", 7), seeded

        offer = await board()
        evidence["stock_offer"] = offer
        assert offer["step_before"] is None and offer["step_after"] is None, offer
        if stock_board_input:
            assert offer["xp_before"] == (7 if spratt else 4 if lenny else 0), offer
            offset = 0
            while offset is not None:
                catalog = (await action(client, "native-bounty-board-list", "--target", str(offset)))["native_bounty_board"]
                assert (catalog["board"], catalog["offset"]) == (spec.board, offset), catalog
                listed = next((row for row in catalog["quests"] if row["id"] == quest), None)
                if listed:
                    assert listed["co_op"] in ("experimental", "verified"), listed
                    evidence["stock_catalog"] = listed
                    break
                next_offset = catalog["next_offset"]
                assert next_offset is None or offset < next_offset <= 128, catalog
                offset = next_offset
            assert "stock_catalog" in evidence, catalog
            evidence["native_balances_at_offer"] = await balances()
            acceptance_save_files = save_files()
            await board_state("armed", "focused", selected=False)
            if stock_menu:
                await stock_button("start", "starting")
            else:
                await board_press("loading")
                await board_state("offer")
                await board_press("starting")
            accepted = await committed(1)
        else:
            acceptance_save_files = save_files()
            accepted = await action(client, "native-quest-start", "--target", quest)
            assert accepted["effect"] == "native_quest_changed", accepted
        evidence["stock_acceptance"] = accepted
        assert (accepted["quest_id"], accepted["step"], accepted["actor"]) == (quest, 1, character), accepted
        require_deferred_save("acceptance", acceptance_save_files)
        premature = await action(client, "native-quest-turnin", "--target", quest, expected=False)
        assert premature["error"] == f"{spec.key}_objective_incomplete", premature
        evidence["early_turn_in"] = premature["error"]

        for destination, tag in outbound:
            current = await cross(current, destination, tag)
        assert quest_record(read_saved(), quest)["step"] == 1

        watcher = await wait(run, lambda state: state.get("game", {}).get(objective_key, {}).get("state") == "watching")
        watched = watcher["game"][objective_key]
        if assassination:
            spawner = native_spratt.SPAWNER if spratt else native_lenny.SPAWNER
            exact_target = native_spratt.TARGET.fullmatch if spratt else native_lenny.exact_target
            client_key = native_spratt.CLIENT_KEY if spratt else "native_lenny_client"
            authority_key = "authority" if spratt else "local"
            assert (watched["map"], watched["spawner"], watched["replicated"], watched["health"]) == (
                area, spawner, True, 300), watched
            assert exact_target(watched["target"]), watched
            visible = await wait(client_run, lambda frame: (row := frame.get("game", {}).get(client_key, {}))
                                 and row.get(authority_key) == 0 and row.get("simulated") == 1, timeout=45)
            evidence["client_target"] = visible["game"][client_key]
        else:
            assert (watched["map"], watched["jammer"], watched["trigger"]) == (area, spec.jammer, spec.trigger), watched
        evidence["stock_watch"] = watched
        if client_projection and not assassination:
            visible = await projected("present")
            evidence["client_projection_before"] = visible
            assert visible["audio"] == "-", visible
        equipped = (await action(client, "native-guest-weapon-equip", "--target", guid))["native_guest_weapon"]
        evidence[f"stock_{weapon_label}_equip"] = equipped
        assert (equipped["map"], equipped["status"], equipped["guid"], equipped["actor_ammo"], equipped["stored_ammo"]) == (
            area, "equipped", guid, 7, 7,
        ), equipped
        await wait(client_run, lambda state: (scope := state.get("game", {}).get("native_guest_primary_scope", {}))
                   and (scope["map"], scope["guid"], scope["kind"]) == (area, guid, weapon))

        if assassination:
            reserve = uuid.uuid4().hex.upper()
            seeded = (await action(host, "native-guest-weapon-seed-ammo", "--target", "guest", "--item-guid", reserve))["native_guest_weapon"]
            assert seeded["guid"] == reserve, seeded
            inventory = (await action(client, "native-inventory-list"))["native_inventory"]
            shells = next(row for row in inventory["items"] if row["guid"] == reserve)
            assert (shells["class_path"], shells["quantity"], shells["source"]) == (ammo.item_path, ammo.fixture_units, "backpack"), shells
            evidence["stock_buckshot_reserve"] = shells
        magazine = 7
        max_shots = 12 if assassination else 3
        if assassination:
            evidence["ordinary_guest_revivals"] = []
        fired = 0
        observed = None
        objective_save_files = save_files()
        for _ in range(max_shots + (8 if assassination else 0)):
            present = status(run)
            if objective_committed(present):
                observed = present
                break
            if assassination and guest_downed(present):
                if len(evidence["ordinary_guest_revivals"]) >= 4:
                    raise RuntimeError(f"{quest} downed the guest through four ordinary revives")
                await revive_assassination_guest()
                continue
            if fired >= max_shots:
                break
            if magazine == 0 and assassination:
                reload = (await action(client, "native-guest-weapon-reload", "--target", guid))["native_guest_weapon"]
                magazine = reload["after"]
                assert (reload["guid"], reload["before"], magazine, reload["reserve_before"], reload["reserve_after"]) == (
                    guid, 0, ammo.fixture_units, ammo.fixture_units, 0,
                ), reload
                evidence["stock_reload"] = reload
            shot = await fire(guid, magazine)
            if shot is None:
                await wait(run, lambda state: guest_downed(state) or objective_committed(state), timeout=5)
                continue
            magazine = shot["after"]
            fired += 1
            try:
                observed = await wait(run, objective_committed, timeout=.8 if assassination else 10)
                break
            except RuntimeError:
                last_observer = status(run).get("game", {}).get(objective_key, {})
                evidence["shots"][-1]["last_objective"] = last_observer
                remaining_hp = last_observer.get("health")
                if assassination and (last_observer.get("state") == "stock_objective_verified"
                              or isinstance(remaining_hp, (int, float)) and remaining_hp <= 0):
                    observed = await wait(run, objective_committed, timeout=9)
                    break
                if fired == max_shots or last_observer.get("state") != "watching":
                    raise
        if observed is None:
            raise RuntimeError("The party did not verify the stock objective within its shot and revive limit")
        completed = live_quest(observed)
        proof = completed["objective"]
        evidence["live_objective"] = completed
        assert completed["actor"] == character and (
            proof["map"], proof["state"], proof["step_before"], proof["step_after"], proof["shooter_role"], proof["shooter"]
        ) == (area, "stock_objective_verified", 1, 2, "guest", shot["pawn"]), completed
        if spratt:
            assert (proof["spawner"], proof["target"], proof["callback_count"], proof["cash_before"]) == (
                native_spratt.SPAWNER, watched["target"], 1, proof["cash_after"]), proof
        elif lenny:
            assert (proof["spawner"], proof["target"], proof["flag_before"], proof["flag_after"]) == (
                native_lenny.SPAWNER, watched["target"], 0, 1), proof
        else:
            assert (proof["jammer"], proof["trigger"]) == (spec.jammer, spec.trigger), proof
        projectile = proof["shot" if spratt else "projectile"]
        assert proof["xp_before"] == proof["xp_after"] and projectile.split(" ", 1)[-1].startswith(level), proof
        if assassination:
            assert projectile.startswith(f"{ammo.projectile}_C "), proof
        if client_projection and not assassination:
            concealed = await projected("hidden", "absent")
            evidence["client_projection_after"] = concealed
            assert (concealed["state"], concealed["audio"]) in {("hidden", "muted"), ("absent", "none")}, concealed
        require_deferred_save("objective", objective_save_files)

        for destination, tag in returning:
            current = await cross(current, destination, tag)
        assert quest_record(read_saved(), quest) == completed, completed
        evidence["durable_objective"] = completed
        ready = await board()
        before = await balances()
        evidence["stock_redemption_ready"], evidence["native_balances_before"] = ready, before
        turn_in_save_files = save_files()
        assert ready["step_after"] == 2 and ready["xp_after"] == offer["xp_after"], ready
        if spratt:
            assert ready["objective_qualified"] is True, ready
            assert ready["reputation"] == offer["reputation"], (offer, ready)
        elif not lenny:
            assert ready["reputation"][faction]["after"] == offer["reputation"][faction]["after"] + 10, (offer, ready)
            assert ready["reputation"]["5"]["after"] == offer["reputation"]["5"]["after"], (offer, ready)
        if stock_board_input:
            baseline = evidence["native_balances_at_offer"]
            if lake or crossroads:
                assert before["host"]["Adv_Freelancer"] == baseline["host"]["Adv_Freelancer"] + 50, (baseline, before)
                assert before["host"]["Adv_Coup"] == baseline["host"]["Adv_Coup"] - 10, (baseline, before)
            elif lenny:
                assert before["host"]["LennyDead"] == 1 and baseline["host"]["LennyDead"] == 0, (baseline, before)
                assert before["host"]["Player_Kills"] == baseline["host"]["Player_Kills"] + 1, (baseline, before)
            elif spratt:
                # Spratt inherits Count_PlayerKill. The stock NPC death graph applies it to the canonical save.
                kills = baseline["host"]["Player_Kills"]
                assert before["host"] == {**baseline["host"], "Player_Kills": kills + 1}, (baseline, before)
                evidence["stock_player_kills"] = {"before": kills, "after": before["host"]["Player_Kills"]}
            else:
                assert before["host"] == baseline["host"], (baseline, before)
            assert before["guest"] == baseline["guest"], (baseline, before)
            await board_state("armed", "focused", selected=False)
            if stock_menu:
                await stock_button("turnin", "redeeming")
            else:
                await board_press("loading")
                await board_state("ready")
                await board_press("redeeming")
            settled = await committed(-10)
            if not stock_menu:
                await board_state("completed")
            reputation_after = (await board())["reputation"]
            reputation = {key: {"before": row["after"], "after": reputation_after[key]["after"]}
                          for key, row in ready["reputation"].items()}
        else:
            settled = await action(client, "native-quest-turnin", "--target", quest)
            assert settled["effect"] == "native_quest_changed", settled
            reputation = settled[spec.key]["reputation"]
        evidence["stock_turn_in"], evidence["stock_reputation"] = settled, reputation
        assert (settled["step"], settled["cash"], settled["xp"], settled["wallet"], set(settled["recipients"])) == (
            -10, spec.cash, 1, before["host"]["Player_Cash"] + spec.cash, {"host", character},
        ), settled
        if stock_menu:
            guest_paid = await wait(client_run, lambda state: state.get("game", {}).get("native_wallet") == settled["wallet"])
            evidence["stock_guest_wallet"] = guest_paid["game"]["native_wallet"]
        assert settled["objective"] == proof, settled
        assert (reputation[faction]["after"] - reputation[faction]["before"],
                reputation["5"]["after"] - reputation["5"]["before"]) == (
                    (30, 50) if spratt else (15, 50) if lenny else (30, -30) if crossroads else (50, -40)), reputation

        def reward_materialized(state):
            players = state.get("gameplay", {}).get("players", {})
            return all(players.get(role, {}).get("quest_xp") == 1 and not players[role].get("native_quest_xp_pending")
                       for role in ("host", "guest"))

        await wait(run, reward_materialized)
        require_deferred_save("redemption and detached XP", turn_in_save_files)
        paid = await balances()
        expected = {owner: {**values, "Player_Cash": values["Player_Cash"] + (spec.cash if owner == "host" else 0),
                            "Player_Xp": values["Player_Xp"] + 1} for owner, values in before.items()}
        assert paid == expected, (before, paid)
        evidence["native_balances_paid"] = paid
        repeated = await action(client, "native-quest-turnin", "--target", quest)
        assert (repeated["effect"], repeated["wallet"], repeated["objective"]) == (
            "native_quest_already_settled", settled["wallet"], proof,
        ), repeated
        if spratt:
            replay_balances = await balances()
            assert replay_balances == paid, (paid, replay_balances)
            evidence["stock_player_kills"]["after_replay"] = replay_balances["host"]["Player_Kills"]
        evidence["same_session_replay"] = repeated["effect"]

        await stop(host, authority)
        await stop(client, visitor)
        await launch(host, "host-restart", "host", *host, "--port", str(port), *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != current["generation"], timeout=75)
        renewed = await action(host, "refresh-invite", "--target", invitation["guest_id"])
        assert (renewed["guest_id"], renewed["character_id"]) == (invitation["guest_id"], character), renewed
        await launch(client, "guest-restart", "join", *client, "--invite", renewed["invite_file"], *client_flags)
        restarted = await enrolled(board_map, (current["generation"], current["zone_epoch"]))
        assert restarted["endpoint"]["port"] == port, restarted["endpoint"]
        restored = await board()
        assert (restored["step_after"], restored["cash_after"], restored["xp_after"]) == (
            -10, paid["host"]["Player_Cash"], paid["host"]["Player_Xp"],
        ), restored
        assert {key: row["after"] for key, row in restored["reputation"].items()} == settled["reputation"], restored
        restored_weapon = (await action(client, "native-guest-weapon-inspect", "--target", guid))["native_guest_weapon"]
        assert (restored_weapon["guid"], restored_weapon["stored_ammo"]) == (guid, magazine), restored_weapon
        replay = await action(client, "native-quest-turnin", "--target", quest)
        assert (replay["effect"], replay["wallet"], replay["objective"]) == (
            "native_quest_already_settled", settled["wallet"], proof,
        ), replay
        restored_balances = await balances()
        assert restored_balances == paid
        if spratt:
            evidence["stock_player_kills"]["after_restart"] = restored_balances["host"]["Player_Kills"]
        evidence.update(same_port_restart={"port": port, "generation": restarted["generation"], "character": character,
                                          "quest": restored, weapon_label: restored_weapon, "replay": replay["effect"]}, verified=True)
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for owner, process in reversed(processes):
            if process.returncode is None:
                try:
                    await stop(owner, process)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 12)
        if original is not None:
            evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
            if not evidence["original_saves_verified"]:
                evidence.update(verified=False, failure="The original Saved directory changed")
        (run / f"live-{spec.command().removeprefix('native-')}-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if original is not None:
            assert evidence["original_saves_verified"], "The original Saved directory changed during the smoke"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path, help="prepared isolated stage workspace")
    parser.add_argument("--vanilla-saved", type=Path, help="original Saved directory to hash before and after")
    parser.add_argument("--client-projection", action="store_true", help="require the exact client-local jammer to disappear after the stock kill")
    parser.add_argument("--pose-only", action="store_true", help="compare both authored jammer transforms before the bounty")
    parser.add_argument("--stock-bounty-menu", action="store_true", help="use the stock Black Market bounty buttons")
    profiles = parser.add_mutually_exclusive_group()
    profiles.add_argument("--black-lake", action="store_true", help="run the authored Black Market and Black Lake contract")
    profiles.add_argument("--simmi", action="store_true", help="accept and turn in the Crossroads jammer through Simmi's stock menu")
    profiles.add_argument("--lenny", action="store_true", help="run Lenny's House and its stock quest-gated NPC spawn")
    profiles.add_argument("--spratt", action="store_true", help="run Kill Spratt through its stock board, Cistern spawn, and redemption")
    arguments = parser.parse_args()
    if arguments.pose_only and (arguments.lenny or arguments.spratt):
        parser.error("The pose check requires an authored jammer")
    if arguments.stock_bounty_menu and not (arguments.black_lake or arguments.simmi):
        parser.error("The stock bounty menu fixture requires --black-lake or --simmi")
    asyncio.run(exercise(arguments.work.resolve(), arguments.vanilla_saved, client_projection=arguments.client_projection,
                         pose_only=arguments.pose_only, stock_menu=arguments.stock_bounty_menu,
                         spec=SPRATT if arguments.spratt else LENNY if arguments.lenny else BLACK_LAKE if arguments.black_lake
                         else CROSSROADS_JAMMER if arguments.simmi else FLOOD))
