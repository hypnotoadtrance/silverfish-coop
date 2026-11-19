"""Verify the authored Copper Stop contract and personal rewards in a staged campaign."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes


BOROUGH = "/Game/Classes/Maps/Maps_SafeZones/Borough"
CITY = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
QUEST = "Clt_Wire"
WIRE = "city-copper-wire"
RIFLE = "/Game/Classes/Weapons/Wep_Nagant_Scoped.Wep_Nagant_Scoped_C"
AMMO = "/Game/Classes/Items/Ammo_30-06_Incendiary_BP.Ammo_30-06_Incendiary_BP_C"
ENTRANCE = "City_Borough"
REWARD_QUEUE = "native_wire_quest_reward"


def lease(state, character):
    return next((row for row in state.get("campaign", {}).get("leases", []) if row["character_id"] == character), {})


def saved_party(state):
    return state.get("campaign", {}).get("world", {}).get("party", {})


def guest_role(state, character):
    return next((role for role, player in state.get("gameplay", {}).get("players", {}).items()
                 if player.get("character_id") == character), None)


def guest_pawn(state, character):
    name = state.get("gameplay", {}).get("native_pawns", {}).get(guest_role(state, character))
    return next((pawn for pawn in state.get("game", {}).get("pawns", {}).values()
                 if name and pawn.get("name") == name), {})


async def exercise(work: Path, vanilla_saved: Path | None, turn_in: bool, *, deferred: bool = False,
                   board_input: bool = False, nagant: bool = False) -> None:
    turn_in = turn_in or deferred or board_input or nagant
    name = "wire-quest-" + uuid.uuid4().hex[:8]
    campaign = ("--campaign", name)
    profile = ("--profile", name)
    host_run = work / "campaigns" / name / "run"
    guest_run = work / "clients" / name / "run"
    late_profile = ("--profile", name + "-late")
    late_run = work / "clients" / late_profile[1] / "run"
    host_run.mkdir(parents=True, exist_ok=True)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    environment = dict(os.environ)
    if nagant:
        environment["SILVERFISH_COOP_NATIVE_GUEST_RELOAD_FIXTURES"] = "1"
    processes = []
    clients = {}
    proof = {}
    flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    print(host_run, flush=True)

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 40)
        reply = json.loads(output)
        if (reply.get("ok", True) is not expected) or bool(process.returncode) == expected:
            raise RuntimeError(f"{arguments}: {reply} {error.decode(errors='replace')}")
        return reply

    async def launch(kind, owner, label, *arguments):
        with (host_run / (label + ".txt")).open("wb") as output:
            process = await asyncio.create_subprocess_exec(*cli, kind, *owner, *arguments, stdout=output, stderr=output,
                                                            creationflags=subprocess.CREATE_NO_WINDOW, env=environment)
        processes.append((process, owner))
        return process

    async def action(owner, verb, *options, expected=True):
        return await command("action", *owner, verb, *options, expected=expected)

    async def roster(area, members=None):
        if not deferred:
            await wait(guest_run, lambda state: state.get("state") == "connected"
                       and state.get("game", {}).get("map") == area)
            return await wait(host_run, lambda state: state.get("state") == "hosting"
                              and state.get("game", {}).get("map") == area
                              and state["game"].get("native_guest_save", {}).get("state") == "bound")

        members = tuple(clients) if members is None else members

        def enrolled(state):
            game = state.get("game", {})
            if state.get("state") != "hosting" or game.get("map") != area or game.get("remote_clients") != len(members):
                return False
            scopes = game.get("native_guest_save_members", {})
            connections = game.get("native_roster", {}).get("connections", [])
            for character in members:
                pawn = guest_pawn(state, character)
                if (not pawn or lease(state, character).get("state") != "active"
                        or scopes.get(pawn["name"], {}).get("state") != "bound"
                        or not any(row.get("state") == "ready" and row.get("pawn") == pawn["name"]
                                   and row.get("controller") == pawn["controller"] for row in connections)):
                    return False
            return True

        host = await wait(host_run, enrolled)
        for character in members:
            await wait(clients[character], lambda state: state.get("state") == "connected"
                       and state.get("character_id") == character and state.get("link_connected") is True
                       and state.get("host_session") == host["generation"] and state.get("zone_epoch") == host["zone_epoch"]
                       and state.get("game", {}).get("map") == area)
        return host

    async def approach(role="guest"):
        return await action(campaign, "native-wire-quest-approach", "--target", role)

    async def board_press(waiting, rendered):
        await wait(guest_run, lambda state: state.get("game", {}).get("native_wire_board", {}).get("state")
                   in {"armed", "focused", "offer", "missing", "ready", "completed"})
        pressed = (await action(profile, "native-wire-board-fixture"))["native_wire_board"]
        assert pressed["state"] == waiting and pressed["quest"] == QUEST, pressed
        shown = await wait(guest_run, lambda state: (row := state.get("game", {}).get("native_wire_board", {}))
                           and row.get("sequence") == pressed["sequence"] and row.get("state") == rendered)
        result = shown["game"]["native_wire_board"]
        proof.setdefault("stock_board_presses", []).append(result)
        return result

    async def position(role, where):
        await action(campaign, "native-exit-position", "--target", role, "--exit-position", where,
                     "--entrance", ENTRANCE)

    async def cross_exit(destination, guests, members=None):
        await position("host", "near")
        for role in guests:
            await position(role, "near")
        await position("host", "inside")
        return await roster(destination, members)

    async def rewards_for(owner):
        inventory = (await action(owner, "native-inventory-list"))["native_inventory"]
        assert inventory["next_offset"] is None, inventory
        rifle, = [row for row in inventory["items"] if row["class_path"] == RIFLE]
        ammo, = [row for row in inventory["items"] if row["class_path"] == AMMO]
        assert (rifle["quantity"], ammo["quantity"]) == (1, 14), inventory
        return {"rifle": rifle, "ammo": ammo}

    async def stock_weapon_input(which, guid):
        fixture = (await action(profile, f"native-guest-{which}-input-fixture"))[f"native_guest_{which}_fixture"]
        assert fixture["stock_wrappers"] == 2, fixture
        if which == "primary":
            assert fixture["guid"] == guid, fixture
        for _ in range(22):
            events = (await command("events", *profile, "--tail", "30"))["events"]
            kind = f"native_guest_{which}_input"
            entered = next((event for event in events if event.get("sequence") == fixture["sequence"]
                            and event["event"] in (kind, kind + "_rejected")), None)
            if entered:
                assert entered["event"] == kind and entered["native_guest_weapon"]["guid"] == guid, entered
                return entered["native_guest_weapon"]
            await asyncio.sleep(.2)
        raise RuntimeError(f"The Nagant's {which} input did not produce an authenticated receipt")

    async def use_nagant(rewards, home_rewards):
        await cross_exit(CITY, ("guest",))
        guid = rewards["rifle"]["guid"]
        ammo = rewards["ammo"]["guid"]
        equipped = (await action(profile, "native-guest-weapon-equip", "--target", guid))["native_guest_weapon"]
        assert equipped["status"] == "equipped" and equipped["actor_ammo"] == equipped["stored_ammo"] == 5, equipped
        await wait(guest_run, lambda state: (scope := state.get("game", {}).get("native_guest_primary_scope", {}))
                   and (scope.get("kind"), scope.get("guid")) == ("nagant-scoped", guid))
        stock = {"equipped": equipped, "fmj_shots": []}
        proof["guest_nagant"] = stock
        for remaining in range(5, 0, -1):
            fired = await stock_weapon_input("primary", guid)
            assert (fired["before"], fired["after"], fired["projectiles"]) == (remaining, remaining - 1, 1), fired
            stock["fmj_shots"].append(fired)
            await asyncio.sleep(1.3)
        reload = await stock_weapon_input("reload", guid)
        assert (reload["before"], reload["after"], reload["reserve_before"], reload["reserve_after"]) == (0, 1, 14, 13), reload
        stock["incendiary_reload"] = reload
        fired = await stock_weapon_input("primary", guid)
        assert (fired["before"], fired["after"], fired["projectiles"]) == (1, 0, 1), fired
        stock["incendiary_shot"] = fired
        assert await rewards_for(campaign) == home_rewards
        gear = (await action(profile, "native-inventory-list"))["native_inventory"]
        round_stack, = [row for row in gear["items"] if row["guid"] == ammo]
        assert round_stack["class_path"] == AMMO and round_stack["quantity"] == 13, round_stack
        stock["detached_reserve"] = round_stack

    try:
        area = CITY if turn_in else BOROUGH
        zone_flags = (*flags, "--native-expanded-zones") if turn_in else flags
        if deferred:
            zone_flags += ("--max-guests", "2")
        host = await launch("host", campaign, "host-first", "--port", "0", "--start-zone", area, *zone_flags)
        ready = await wait(host_run, lambda state: state.get("state") == "hosting")
        port = ready["endpoint"]["port"]
        invite = await command("invite", *campaign, "--name", "Borough visitor", "--character", "Borough guest")
        main_character = invite["character_id"]
        clients[main_character] = guest_run
        input_flags = ("--native-guest-primary-input", "--native-guest-reload-input") if nagant else ()
        await launch("join", profile, "guest", "--headless", "--native-zones", "--invite", invite["invite_file"], *input_flags)
        await roster(area)
        if turn_in:
            await action(campaign, "native-authored-pickup-approach", "--target", "guest")
            interaction = (await action(profile, "native-authored-pickup-fixture"))["native_authored_pickup_interaction"]
            assert (interaction["source"], interaction["map"], interaction["entry"]) == (WIRE, CITY, "fixture"), interaction
            await action(profile, "native-authored-pickup-claim", "--target", WIRE)
            owned = await wait(guest_run, lambda state: WIRE in state.get("gameplay", {}).get("native_authored_pickups", {}))
            pickup = owned["gameplay"]["native_authored_pickups"][WIRE]
            assert pickup["owner"] == invite["character_id"] and pickup["map"] == CITY, pickup
            await cross_exit(BOROUGH, ("guest",))
            proof["stock_city_pickup"] = pickup
        else:
            far = await action(profile, "native-wire-quest-preflight", expected=False)
            assert far["error"] == "stock_wire_quest_board_out_of_reach", far
        moved = await approach()
        before = (await action(profile, "native-wire-quest-preflight"))["native_wire_quest"]
        assert before["state"] == "missing" and before["pawn"] == moved["native_wire_quest_approach"]["pawn"], before
        if board_input:
            offered = await board_press("loading", "offer")
            accepted = await board_press("starting", "ready")
            assert offered["sequence"] == accepted["open"] < accepted["sequence"], (offered, accepted)
            committed = await wait(host_run, lambda state: saved_party(state).get("native_quests", {}).get(QUEST, {}).get("step") == 1)
            started = saved_party(committed)["native_quests"][QUEST]
        else:
            started = await action(profile, "native-quest-start", "--target", QUEST)
            assert started["effect"] == "native_quest_changed", started
        assert (started["step"], started["actor"]) == (1, invite["character_id"]), started
        repeated = await action(profile, "native-quest-start", "--target", QUEST)
        assert repeated["effect"] == "native_quest_present" and repeated["step"] == 1, repeated
        proof["stock_start"] = started
        final = 1
        if turn_in:
            if deferred:
                late_invite = await command("invite", *campaign, "--name", "Borough reserve", "--character", "Returning guest")
                late_character = late_invite["character_id"]
                clients[late_character] = late_run
                await launch("join", late_profile, "guest-late", "--headless", "--native-zones", "--invite", late_invite["invite_file"])
                enrolled = await roster(BOROUGH)
                assert guest_role(enrolled, late_character) == "guest2", enrolled
                baseline = (await action(late_profile, "native-inventory-list"))["native_inventory"]
                assert baseline["owner"] == late_character and baseline["next_offset"] is None, baseline
                assert all(item["class_path"] not in (RIFLE, AMMO) for item in baseline["items"]), baseline
                await position("host", "near")
                await position("guest2", "near")

            objective = (await action(profile, "native-wire-quest-preflight"))["native_wire_quest"]
            assert (objective["objective_guid"], objective["objective_owner"]) == (pickup["guid"], "guest"), objective
            if deferred:
                dropped = await action(late_profile, "drop-link")
                assert dropped["link_connected"] is False, dropped
                await wait(late_run, lambda state: state.get("link_connected") is False
                           and state.get("reconnect_suspended") is True)
                held = await wait(host_run, lambda state: lease(state, late_character).get("state") == "reserved"
                                  and guest_pawn(state, late_character))
                held_lease = lease(held, late_character)
                assert held_lease["grace_remaining"] > 0, held_lease
                guest_location = guest_pawn(held, late_character)["location"]
                host_location = held["game"]["pawns"]["local"]["location"]
                assert math.dist(guest_location, host_location) <= 1500, (guest_location, host_location)
                prior_xp = saved_party(held).get("players", {}).get(late_character, {}).get("quest_xp", 0)
                proof["deferred"] = {"character_id": late_character, "reserved": held_lease,
                                     "source_epoch": held["zone_epoch"], "source_location": guest_location}

            if board_input:
                ready = json.loads((guest_run / "status.json").read_text(encoding="utf-8"))["game"]["native_wire_board"]
                if ready["state"] != "ready":
                    ready = await board_press("loading", "ready")
                completed = await board_press("redeeming", "completed")
                assert ready["open"] == completed["open"] < completed["sequence"], completed
                committed = await wait(host_run, lambda state: saved_party(state).get("native_quests", {}).get(QUEST, {}).get("step") == -10)
                redeemed = saved_party(committed)["native_quests"][QUEST]
            else:
                redeemed = await action(profile, "native-quest-turnin", "--target", QUEST)
                assert redeemed["effect"] == "native_quest_changed", redeemed
            assert (redeemed["step"], redeemed["actor"]) == (-10, invite["character_id"]), redeemed
            repeat = await action(profile, "native-quest-turnin", "--target", QUEST)
            assert repeat["effect"] == "native_quest_already_settled", repeat
            assert (redeemed["consumed_wire"], redeemed["objective_owner"], redeemed["commune_rep"]) == (
                pickup["guid"], invite["character_id"], objective["native_commune_rep"] + 20), redeemed
            proof["stock_turn_in"] = redeemed
            if deferred:
                assert sorted(redeemed["recipients"]) == sorted(("host", main_character, late_character)), redeemed
                borough = await wait(host_run, lambda state: set(saved_party(state).get(REWARD_QUEUE, {}).get("pending", {}))
                                     == {late_character})
                queued = saved_party(borough)[REWARD_QUEUE]
                expected_ids = queued["pending"][late_character]
                assert queued["escrow"]["map"] == BOROUGH and len(set(expected_ids)) == 2, queued
                proof["deferred"]["queued_in_borough"] = expected_ids

                city = await cross_exit(CITY, ("guest",), (main_character,))
                city_lease = lease(city, late_character)
                assert city["zone_epoch"] != held["zone_epoch"] and city_lease["state"] == "reserved", city_lease
                assert city_lease["grace_remaining"] > 0 and not guest_pawn(city, late_character), city
                assert saved_party(city)[REWARD_QUEUE] == queued, city
                proof["deferred"]["arrived_in_city"] = {"epoch": city["zone_epoch"], "reserved": city_lease,
                                                        "pending_ids": expected_ids}

                resumed = await action(late_profile, "reconnect")
                assert resumed["link_connected"] is True and resumed["resumed"] is True, resumed
                reunited = await roster(CITY)
                assert reunited["zone_epoch"] == city["zone_epoch"], reunited
                late_pawn = guest_pawn(reunited, late_character)
                lead = reunited["game"]["pawns"]["local"]["location"]
                assert math.dist(late_pawn["location"], lead) <= 1500, late_pawn
                settled = await wait(host_run, lambda state: state.get("game", {}).get("map") == CITY
                                     and REWARD_QUEUE not in saved_party(state))
                assert saved_party(settled)["players"][late_character]["quest_xp"] == prior_xp + 1, settled
                frame, = [row["state"]["native_guest_save"] for row in settled["campaign"]["characters"]
                          if row["character_id"] == late_character]
                assert (frame["map"], frame["character_id"]) == (CITY, late_character), frame
                late_rewards = await rewards_for(late_profile)
                assert [late_rewards[item]["guid"] for item in ("rifle", "ammo")] == expected_ids, late_rewards
                proof["deferred"].update(resumed=resumed["resumed"], city_pawn=late_pawn["name"],
                                         receipt_frame=frame, rewards=late_rewards)

            host_rewards, guest_rewards = await asyncio.gather(rewards_for(campaign), rewards_for(profile))
            assert len({row["guid"] for inventory in (host_rewards, guest_rewards) for row in inventory.values()}) == 4
            proof["personal_rewards"] = {"host": host_rewards, "guest": guest_rewards}
            if deferred:
                assert len({row["guid"] for inventory in (host_rewards, guest_rewards, late_rewards)
                            for row in inventory.values()}) == 6
                await cross_exit(BOROUGH, ("guest", "guest2"))
            final = -10

        await action(campaign, "stop")
        await asyncio.wait_for(host.wait(), 15)
        await launch("host", campaign, "host-restart", "--port", str(port), *zone_flags)
        restored_roster = await roster(BOROUGH)
        await approach(guest_role(restored_roster, main_character) if deferred else "guest")
        restored = (await action(profile, "native-wire-quest-preflight"))["native_wire_quest"]
        assert restored["step"] == final, restored
        exported = (await action(profile, "native-quest-list"))["native_quest_page"]
        records = [row for row in exported["quests"] if row["id"] == QUEST]
        assert len(records) == 1 and records[0]["step"] == final, exported
        if turn_in:
            assert (restored["native_cash"], restored["native_xp"], restored["native_commune_rep"]) == (
                objective["native_cash"], objective["native_xp"] + 1, redeemed["commune_rep"]), restored
            assert await rewards_for(campaign) == host_rewards
            assert await rewards_for(profile) == guest_rewards
        if deferred:
            assert await rewards_for(late_profile) == late_rewards
            events = (await command("events", *campaign, "--tail", "1000"))["events"]
            settlements = [event for event in events if event["event"] == "native_wire_quest_pending_awards_committed"
                           and late_character in event["recipients"]]
            assert len(settlements) == 1 and settlements[0]["recipients"] == [late_character], settlements
            proof["deferred"]["single_settlement"] = settlements[0]
        proof["stock_restart"] = restored
        if nagant:
            await use_nagant(guest_rewards, host_rewards)
        proof["verified"] = True
    except BaseException as error:
        proof.update(verified=False, failure=str(error))
        raise
    finally:
        for process, owner in reversed(processes):
            if process.returncode is None:
                try:
                    await action(owner, "stop")
                    await asyncio.wait_for(process.wait(), 15)
                except (RuntimeError, OSError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await asyncio.wait_for(process.wait(), 12)
        if original is not None:
            proof["original_saves_verified"] = saved_hashes(vanilla_saved) == original
        (host_run / "live-wire-quest-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(proof), flush=True)
        if original is not None:
            assert proof["original_saves_verified"], "The original single-player directory changed during the run"


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", type=Path, required=True)
    options.add_argument("--vanilla-saved", type=Path)
    options.add_argument("--turn-in", action="store_true")
    options.add_argument("--deferred", action="store_true", help="turn in while a second guest is offline, then verify delivery in City")
    options.add_argument("--board-input", action="store_true", help="accept and turn in through the authentic stock Interact binding")
    options.add_argument("--nagant", action="store_true", help="equip the rewarded rifle and exercise its stock primary and reload inputs")
    args = options.parse_args()
    if args.nagant and args.deferred:
        options.error("The two-player Nagant exercise and deferred party exercise run separately")
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved, args.turn_in, deferred=args.deferred,
                         board_input=args.board_input, nagant=args.nagant))
