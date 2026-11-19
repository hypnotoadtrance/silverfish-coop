"""Exercise the Playtest Bandit or exact Dead Bridge CQC on a headless loopback party."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
MAP = "/Game/Classes/Maps/PlaytestMap_01"
DEAD_BRIDGE = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
CQC = f"NPC_State_Infantry_CQC_C {DEAD_BRIDGE}.DeadBridge_P:PersistentLevel.NPC_State_Infantry_CQC_C_0"
CQC_SECOND = CQC.removesuffix("_0") + "_1"
SHELLS = "/Game/Classes/Items/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C"


def status(directory: Path) -> dict:
    try:
        return json.loads((directory / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def bandit(state: dict) -> dict:
    return state.get("game", {}).get("native_bandit", {})


def actor_proof(state: dict, key: str, actor: str) -> dict:
    game = state.get("game", {})
    rows = game.get(key + "_by_actor")
    return rows.get(actor, {}) if rows is not None else game.get(key, {})


def cqc_counterpart(directory: Path, actor: str = CQC) -> dict | None:
    paths = tuple((directory / "game-bridge").glob("*/status.tsv"))
    if len(paths) != 1:
        return None
    try:
        lines = paths[0].read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    for line in lines:
        fields = line.split("\t")
        if fields[:3] != ["native-enemy-loot-counterpart", DEAD_BRIDGE, actor] or len(fields) != 9:
            continue
        if (fields[3] not in ("-", "0", "1", "2", "3") or fields[4] not in ("0", "1")
                or fields[5] not in ("0", "1") or not all(value.isdecimal() for value in fields[6:])):
            raise AssertionError(fields)
        return dict(actor=actor, role=None if fields[3] == "-" else int(fields[3]), controller=fields[4] == "1",
                    alive=fields[5] == "1", authorities=int(fields[6]), replicas=int(fields[7]), other=int(fields[8]))
    return None


async def wait(directory: Path, predicate, *, timeout=42) -> dict:
    deadline = time.monotonic() + timeout
    current = {}
    while time.monotonic() < deadline:
        current = status(directory)
        if predicate(current):
            return current
        if current.get("state") == "failed":
            break
        await asyncio.sleep(0.2)
    raise RuntimeError(f"{directory.parent.name} did not reach the expected fixture state: {current}")


async def exercise(work: Path, subject: str, *, hostile: bool, require_combat: bool, require_movement: bool,
                   require_damage: bool, enemy_loot: bool, original_save: Path | None,
                   enemy_bounty: bool = False, shared_enemy_loot: bool = False, dead_bridge_cqc: bool = False,
                   automatic_cqc_loot: bool = False, both_cqc: bool = False) -> dict:
    area = DEAD_BRIDGE if dead_bridge_cqc else MAP
    encounter = "dead-bridge-cqc" if dead_bridge_cqc else f"bandit-{subject}"
    profile = f"{encounter}-{uuid.uuid4().hex[:8]}"
    run = work / "campaigns" / profile / "run"
    guest_run = work / "clients" / profile / "run"
    base = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    environment = dict(os.environ, SILVERFISH_COOP_NATIVE_NPC_TEST="1")
    environment.pop("SILVERFISH_COOP_NATIVE_NPC_HOSTILE", None)
    environment.pop("SILVERFISH_COOP_NATIVE_ENEMY_BOUNTY_FIXTURE", None)
    if enemy_bounty:
        environment["SILVERFISH_COOP_NATIVE_ENEMY_BOUNTY_FIXTURE"] = "1"
    if hostile:
        environment["SILVERFISH_COOP_NATIVE_NPC_HOSTILE"] = "1"
    if enemy_loot:
        environment["SILVERFISH_COOP_NATIVE_ENEMY_LOOT"] = "1"
    if enemy_loot:
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        from tools.silverfish.control import submit
    original = saved_hashes(original_save) if original_save else None
    processes = []
    print(run, flush=True)

    async def command(*arguments):
        process = await asyncio.create_subprocess_exec(*base, *arguments, cwd=REPO, env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 45)
        if process.returncode:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(output)
        if not reply.get("ok"):
            raise RuntimeError(f"{arguments}: {reply}")
        return reply

    async def combat_action(directory: Path, action: str, target: str | None = None, **options) -> dict:
        return await submit(directory, action, target=target, **options)

    def accepted(action, reply):
        if reply.get("ok") is not True:
            raise RuntimeError(f"{action}: {reply.get('error', 'control request failed')}")
        return reply


    async def start(log_name, controller, *arguments):
        run.mkdir(parents=True, exist_ok=True)
        with (run / log_name).open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*base, *arguments, cwd=REPO, env=environment,
                stdout=stream, stderr=stream, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, controller))
        return process

    host = ("--campaign", profile)
    client = ("--profile", profile)
    evidence = {"subject": subject, "hostile": hostile, "bounty_fixture": enemy_bounty, "map": area}
    try:
        route_options = ("--native-expanded-zones",) if dead_bridge_cqc else ()
        if automatic_cqc_loot:
            route_options += ("--native-dead-bridge-enemy-loot",)
            evidence["automatic_cqc_loot"] = True
        await start("host.txt", host, "host", *host, "--port", "0", "--headless", "--fixtures", "--native-events",
                    "--native-guest", "--native-zones", *route_options, "--start-zone", area)
        await wait(run, lambda state: state.get("state") == "hosting"
                   and state.get("game", {}).get("map") == area
                   and (dead_bridge_cqc or bandit(state).get("status") == "waiting_for_stock_bandit"))
        invitation = await command("invite", *host, "--name", "Loopback enemy observer", "--character", "Enemy observer")
        client_flags = ("--invite", invitation["invite_file"], "--headless", "--native-zones")
        await start("client.txt", client, "join", *client, *client_flags)
        await wait(guest_run, lambda state: state.get("state") == "connected"
                   and state.get("game", {}).get("map") == area
                   and (dead_bridge_cqc or bandit(state).get("status") == "waiting_for_stock_bandit"))
        paired = await wait(run, lambda state: state.get("game", {}).get("native_roster", {}).get("count") == 1
                            and (state.get("game", {}).get("native_guest_save", {}).get("state") == "bound"
                                 if dead_bridge_cqc else bandit(state).get("watched_pawn")))
        if dead_bridge_cqc:
            exact_pawn = paired["game"]["native_guest_save"]["pawn"]
            visible = await wait(guest_run, lambda state: cqc_counterpart(guest_run) is not None
                                 and state.get("game", {}).get("native_enemy_loot_death_view", {}).get("state") == "present")
            evidence["client_cqc_before_combat"] = cqc_counterpart(guest_run)
            before = visible["game"]["native_enemy_loot_death_view"]
            assert (before["actor"], before["hidden"], before["collision"]) == (CQC, False, True), before
            if not automatic_cqc_loot:
                wrong = await combat_action(run, "native-enemy-loot-watch", CQC.removesuffix("_0") + "_1")
                assert wrong.get("ok") is False and wrong.get("error") == "Choose the exact staged stock enemy", wrong
                evidence["neighboring_stock_cqc_refused"] = True
        else:
            pair_report = bandit(paired)
            exact_pawn = pair_report["local_pawn" if subject == "host" else "watched_pawn"]
            if not exact_pawn:
                raise AssertionError(pair_report)
            denied = (await command("action", *client, "native-bandit-client-spawner-probe"))["native_bandit"]
            assert denied["spawner_denials"] > 0 and denied["authority_count"] == denied["simulated_count"] == 0, denied
            evidence["stock_client_spawn_denied"] = denied

        weapon_guid, remaining = None, None
        if enemy_loot:
            await wait(run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            seeded = accepted("seed stock pistol", await combat_action(run, "native-guest-weapon-seed-light-pistol"))
            weapon_guid = seeded["native_guest_weapon"]["guid"]
            equipped = accepted("equip stock pistol", await combat_action(guest_run, "native-guest-weapon-equip", weapon_guid))
            proof = equipped["native_guest_weapon"]
            assert proof["status"] == "equipped" and proof["pawn"] == exact_pawn and proof["guid"] == weapon_guid, proof
            remaining = proof["stored_ammo"]
            accepted("seed stock reserve", await combat_action(run, "native-guest-weapon-seed-ammo"))

        if dead_bridge_cqc:
            target = CQC
        else:
            await command("action", *host, "native-bandit-position", "--target", subject)
            await command("action", *host, "native-bandit-spawn")
            server_spawn = bandit(await wait(run, lambda state: bandit(state).get("status") == "stock_bandit_authority"
                                                 and bandit(state).get("initialized")))
            target = server_spawn["npc"]
        if enemy_loot:
            if automatic_cqc_loot:
                watched = await wait(run, lambda state: state.get("game", {}).get("native_enemy_loot_cqc_capability")
                                     and state["game"].get("native_enemy_loot", {}).get("state") == "watching")
                armed = watched["game"]["native_enemy_loot"]
            else:
                armed = accepted("watch exact stock enemy", await combat_action(
                    run, "native-enemy-loot-watch", target))["native_enemy_loot"]
            assert (armed["state"], armed["bandit"], armed["map"]) == ("watching", target, area), armed
            if dead_bridge_cqc:
                evidence["authority_watch"] = armed
            if both_cqc:
                second_watched = await wait(run, lambda state: actor_proof(state, "native_enemy_loot", CQC_SECOND)
                                            .get("state") == "watching")
                evidence["second_authority_watch"] = actor_proof(second_watched, "native_enemy_loot", CQC_SECOND)
        if not dead_bridge_cqc:
            replica_spawn = bandit(await wait(guest_run, lambda state: bandit(state).get("status") == "stock_bandit_replica"
                                                  and bandit(state).get("replica_ai_denials", 0) > 0))
            assert server_spawn["role"] == 3 and server_spawn["authority_count"] == 1 and server_spawn["simulated_count"] == 0, server_spawn
            assert server_spawn["replicates"] and server_spawn["replicate_movement"] and server_spawn["alive"], server_spawn
            assert server_spawn["controller"].startswith("MasterNPC_AI_Controler_BP_C "), server_spawn
            assert replica_spawn["role"] == 1 and replica_spawn["authority_count"] == 0 and replica_spawn["simulated_count"] == 1, replica_spawn
            assert not replica_spawn["initialized"] and replica_spawn["controller"] is None, replica_spawn
            if hostile:
                assert server_spawn.get("combat", {}).get("armed") is True, server_spawn
            evidence["authority"] = server_spawn
            evidence["simulated_proxy"] = replica_spawn

        if not enemy_loot or require_combat or require_movement:
            sight_field = "perceives_local" if subject == "host" else "perceives_watched"
            sight_state = bandit(await wait(run, lambda state: bandit(state).get("perception_available")
                                             and bandit(state).get(sight_field)
                                             and (not (hostile or require_combat)
                                                  or bandit(state).get("combat_target") == exact_pawn)))
            assert sight_state["local_pawn" if subject == "host" else "watched_pawn"] == exact_pawn, sight_state
            evidence["stock_sight"] = {"exact_subject": exact_pawn, "observed": sight_state,
                                       "combat_target_matched": sight_state["combat_target"] == exact_pawn}

        movement, damage = None, None
        other = "guest" if subject == "host" else "host"
        movement_deadline = time.monotonic() + (8 if not enemy_loot or require_movement else 0)
        while time.monotonic() < movement_deadline:
            server = bandit(status(run))
            replica = bandit(status(guest_run))
            if server.get("npc") == server_spawn["npc"] and replica.get("npc") == replica_spawn["npc"]:
                if not movement and (math.dist(server["location"], server_spawn["location"]) >= 60
                    and math.dist(replica["location"], replica_spawn["location"]) >= 60
                    and math.dist(server["location"], replica["location"]) <= 400):
                    movement = {"authority_location": server["location"], "replica_location": replica["location"]}
                if hostile and not damage:
                    combat = server.get("combat", {})
                    if combat.get(f"{subject}_hits", 0) > 0 and combat.get(f"{subject}_health_lost", 0) > 0:
                        damage = {**combat, "other_player_untouched": combat.get(f"{other}_hits") == 0}
                if movement and (not hostile or damage):
                    break
            await asyncio.sleep(0.2)
        evidence["replicated_stock_movement"] = movement
        if hostile:
            evidence["stock_attack_damage"] = damage
        if require_movement and not movement:
            raise AssertionError("The Bandit did not show enough stock movement to verify replica motion")
        if require_damage and not (damage and damage["other_player_untouched"]):
            raise AssertionError("The Bandit did not damage the exposed pawn independently of the other pawn")
        async def fight(target, proof_log, remaining):
            def read_observer(current):
                return actor_proof(current, "native_enemy_loot", target)

            def killed(current):
                return read_observer(current).get("state") in {"stock_kill_confirmed", "stock_loot_observed"}

            def guest_downed(current):
                return current.get("gameplay", {}).get("players", {}).get("guest", {}).get("downed") is True

            shots, revivals = [], []
            proof_log["stock_projectile_proofs"] = shots
            proof_log["ordinary_guest_revivals"] = revivals
            for _ in range(20):
                current = status(run)
                proof_log["enemy_loot_last"] = read_observer(current)
                stock_target = current.get("game", {}).get("native_enemy_loot_cqc_targets", {}).get(target)
                if stock_target and (stock_target["target"], stock_target["character"]) == (exact_pawn, invitation["character_id"]):
                    proof_log["stock_cqc_ai_targeted_guest"] = stock_target
                if killed(current) or len(shots) >= 12:
                    break
                if guest_downed(current):
                    if len(revivals) >= 4:
                        raise AssertionError("The guest reached four ordinary revives before a stock kill")
                    if current["gameplay"]["players"]["host"]["downed"]:
                        raise AssertionError("Both stock players were downed in the Bandit fixture")
                    prepared = await wait(run, lambda state: killed(state) or (
                        guest_downed(state)
                        and state.get("game", {}).get("player_effects", {}).get("guest", {}).get("state") == "downed"
                    ), timeout=4)
                    if killed(prepared):
                        break
                    approach = await combat_action(run, "native-bandit-aim-fixture", target)
                    if killed(status(run)):
                        break
                    accepted("stage host inside ordinary revive range", approach)
                    reply = accepted("revive exact guest", await combat_action(run, "interact", invitation["character_id"]))
                    assert reply["effect"] == "revive" and reply["target"] == invitation["character_id"], reply
                    revived_at = time.monotonic()
                    before_sequence = prepared.get("game", {}).get("sequence", 0)
                    standing = await wait(run, lambda state: killed(state) or (
                        not guest_downed(state)
                        and state.get("game", {}).get("native_guest_revivals", {}).get(exact_pawn)
                    ) or (both_cqc and guest_downed(state) and time.monotonic() - revived_at >= 1
                          and state.get("game", {}).get("sequence", 0) > before_sequence), timeout=7)
                    if killed(standing):
                        break
                    if guest_downed(standing):
                        revivals.append({"interaction": reply, "downed_again_before_standing_sample": True})
                        continue
                    restored = standing["game"]["native_guest_revivals"][exact_pawn]
                    assert restored["owner"] == invitation["character_id"] and math.isclose(
                        restored["health"], restored["maximum"] * .10, abs_tol=.01
                    ), restored
                    revivals.append({"interaction": reply, "native": restored})

                if remaining < 2:
                    loaded = await combat_action(guest_run, "native-guest-weapon-reload", weapon_guid)
                    if loaded.get("error") == "downed":
                        await wait(run, lambda state: killed(state) or guest_downed(state), timeout=4)
                        continue
                    remaining = accepted("reload stock pistol", loaded)["native_guest_weapon"]["after"]
                fire = await combat_action(run, "native-bandit-shot-fixture", f"{target}|{weapon_guid}")
                if not fire["ok"]:
                    if killed(status(run)):
                        break
                    if fire.get("error") in {"downed", "guest_weapon_primary_ineligible",
                                              "The verified standing guest, stock equipped weapon or living Bandit is unavailable"}:
                        await wait(run, lambda state: killed(state) or guest_downed(state), timeout=4)
                        continue
                    if fire.get("error") == "No exact stock Bandit line of fire from the qualified staging points":
                        continue
                    accepted("aim and fire the stock pistol", fire)
                aim = fire["native_bandit_aim"]
                assert (aim["bandit"], aim["pawn"]) == (target, exact_pawn), aim
                fired = fire["native_guest_weapon"]
                assert (fired["pawn"], fired["guid"], fired["before"], fired["after"], fired["projectiles"]) == (
                    exact_pawn, weapon_guid, remaining, remaining - 1, 1
                ), fired
                remaining = fired["after"]
                shots.append({"aim": aim, "shot": fired})
            resolved = await wait(run, lambda state: read_observer(state).get("state") == "stock_loot_observed",
                                  timeout=12)
            observed = read_observer(resolved)
            assert (observed["map"], observed["bandit"], observed["killer_role"], observed["killer_pawn"]) == (
                area, target, "guest", exact_pawn
            ), observed
            assert shots and observed["hits"] > 0 and observed["loot_calls"] == 1 and observed["bounty_calls"] > 0, observed
            assert observed["hp_before"] > 0 >= observed["hp_after"] and observed["projectile"] != "-", observed
            proof_log["enemy_loot_from_exact_guest_projectile"] = observed
            bounty = actor_proof(resolved, "native_enemy_bounty", target)
            if bounty:
                proof_log["stock_enemy_bounty_scope"] = bounty
            if enemy_bounty:
                assert observed["bounty_active"] is True and observed["bounty_instances"] == 1, observed
                assert bounty and (bounty["map"], bounty["bandit"], bounty["source"]) == (area, target, "guest_detached"), bounty
                assert (bounty["override_count"], bounty["original_active"], bounty["original_instances"],
                        bounty["permanent_count"], bounty["timed_count"], bounty["gear_count"], bounty["cache_unchanged"]) == (
                    observed["bounty_calls"], False, 0, 1, 0, 0, True
                ), bounty
            return remaining, observed

        async def share(target, proof_log, observed):
            if shared_enemy_loot:
                if automatic_cqc_loot:
                    def own_rolls(current):
                        return [roll for roll in current.get("gameplay", {}).get("native_enemy_loot_rolls", {}).values()
                                if roll.get("bandit") == target and roll.get("epoch") == paired["zone_epoch"]]

                    def committed(current):
                        scope = actor_proof(current, "native_enemy_loot_cqc", target)
                        if (scope.get("reserved"), scope.get("captured"), scope.get("killer_character")) == (
                                False, True, invitation["character_id"]):
                            return len(own_rolls(current)) == 1
                        return False

                    settled = await wait(run, committed, timeout=15)
                    roll, = own_rolls(settled)
                    proof_log["automatic_stock_escrow"] = actor_proof(settled, "native_enemy_loot_cqc", target)
                else:
                    roll = accepted("bank the stock enemy roll", await combat_action(
                        run, "native-enemy-loot-bank", target))["native_enemy_loot_roll"]
                guid = roll["guid"]
                assert (roll["killer"], roll["actor"], roll["quantity"], roll["claimed_by"]) == (
                    invitation["character_id"], observed["ammo_actor"], observed["ammo_quantity"], None
                ), roll
                if dead_bridge_cqc:
                    assert (roll["map"], roll["bandit"], roll["class_path"]) == (area, target, SHELLS), roll
                    assert 3 <= roll["quantity"] <= 9, roll
                    from tools.silverfish.native_host_save import verify_record
                    campaign = run.parent
                    saved = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
                    durable = saved["world"]["party"]["native_enemy_loot_rolls"][guid]
                    verify_record(campaign / "game-user", durable["escrow"])
                    assert {key: value for key, value in durable.items() if key != "escrow"} == roll, durable
                    proof_log["verified_stock_shell_escrow"] = durable

                    def projected(state):
                        view = actor_proof(state, "native_enemy_loot_death_view", target)
                        return state.get("gameplay_fresh") and state.get("zone_epoch") == roll["epoch"] and (
                            view.get("actor"), view.get("state"), view.get("epoch"), view.get("guid"),
                            view.get("hidden"), view.get("collision"),
                        ) == (target, "projected", roll["epoch"], guid, True, False)

                    defeat = await wait(guest_run, projected, timeout=12)
                    counterpart = cqc_counterpart(guest_run, target)
                    assert counterpart and counterpart["role"] == 1 and counterpart["authorities"] == 0, counterpart
                    assert counterpart["controller"] is False and counterpart["alive"] is True, counterpart
                    proof_log["client_cqc_after_combat"] = counterpart
                    proof_log["client_cqc_death_view"] = actor_proof(defeat, "native_enemy_loot_death_view", target)
                published = await wait(guest_run, lambda state: state.get("gameplay", {}).get(
                    "native_enemy_loot_rolls", {}).get(guid) == roll)
                assert published["gameplay_fresh"], published
                if not automatic_cqc_loot:
                    repeated = accepted("repeat the bank request", await combat_action(
                        run, "native-enemy-loot-bank", target))["native_enemy_loot_roll"]
                    assert repeated == roll, repeated
                accepted("place the host within stock pickup range", await combat_action(
                    run, "native-enemy-loot-approach", f"host|{guid}"))
                operation = uuid.uuid4().hex
                claimed = accepted("claim the stock shells for the host", await combat_action(
                    run, "native-enemy-loot-claim", guid, operation_id=operation))["native_enemy_loot_claim"]
                assert claimed == dict(roll, claimed_by="host"), claimed
                retry = accepted("replay the stock host claim", await combat_action(
                    run, "native-enemy-loot-claim", guid, operation_id=operation))["native_enemy_loot_claim"]
                assert retry == claimed, retry
                refused = await combat_action(guest_run, "native-enemy-loot-claim", guid)
                assert refused.get("ok") is False and refused.get("error") == "native_enemy_loot_already_claimed", refused
                home = accepted("inspect the exact host inventory", await combat_action(run, "native-inventory-list"))["native_inventory"]
                away = accepted("inspect the killer's separate inventory", await combat_action(guest_run, "native-inventory-list"))["native_inventory"]
                row, = [item for item in home["items"] if item["guid"] == guid]
                assert (row["class_path"], row["quantity"]) == (roll["class_path"], roll["quantity"]), row
                assert all(item["guid"] != guid for item in away["items"]), away
                proof_log["shared_enemy_ammunition"] = {"roll": roll, "claim": claimed, "stored_item": row,
                                                       "duplicate_guest_claim": refused["error"]}
                if dead_bridge_cqc:
                    settled = await wait(guest_run, lambda state: state.get("gameplay", {}).get(
                        "native_enemy_loot_rolls", {}).get(guid) == claimed)
                    assert settled["gameplay_fresh"], settled
                    saved = json.loads((run.parent / "save" / "campaign.json").read_text(encoding="utf-8"))
                    durable = saved["world"]["party"]["native_enemy_loot_rolls"][guid]
                    assert durable == claimed, durable
                    assert saved["world"]["map"] == DEAD_BRIDGE, saved["world"]
                    await command("action", *client, "drop-link")
                    await wait(guest_run, lambda state: state.get("reconnect_suspended") and not state.get("link_connected"))
                    await wait(run, lambda state: not state.get("link_connected"))
                    await command("action", *client, "reconnect")
                    rejoined = await wait(guest_run, lambda state: state.get("link_connected")
                                          and state.get("gameplay_fresh") and projected(state))
                    same_visit = status(run)
                    assert same_visit["generation"] == paired["generation"] and same_visit["zone_epoch"] == roll["epoch"], same_visit
                    assert (rejoined["game"]["pawns"]["local"]["name"], same_visit["game"]["native_guest_save"]["pawn"]) == (
                        settled["game"]["pawns"]["local"]["name"], exact_pawn
                    )
                    proof_log["client_cqc_death_view_after_reconnect"] = actor_proof(rejoined, "native_enemy_loot_death_view", target)
        if enemy_loot and both_cqc:
            encounters = evidence["stock_cqc_encounters"] = {CQC: {}, CQC_SECOND: {}}
            # Complete both stock fights before keeping the party exposed during
            # inventory, shared claims or Direct IP reconnect verification.
            remaining, first_observed = await fight(CQC, encounters[CQC], remaining)
            sibling = actor_proof(status(guest_run), "native_enemy_loot_death_view", CQC_SECOND)
            assert (sibling.get("state"), sibling.get("actor")) == ("present", CQC_SECOND), sibling
            _, second_observed = await fight(CQC_SECOND, encounters[CQC_SECOND], remaining)
            await share(CQC, encounters[CQC], first_observed)
            await share(CQC_SECOND, encounters[CQC_SECOND], second_observed)
            first, second = (encounters[actor]["shared_enemy_ammunition"]["claim"] for actor in (CQC, CQC_SECOND))
            assert first["epoch"] == second["epoch"] and first["guid"] != second["guid"] and first["actor"] != second["actor"]
            final = status(run)["gameplay"]["native_enemy_loot_rolls"]
            assert final == {first["guid"]: first, second["guid"]: second}, final
        elif enemy_loot:
            _, observed = await fight(target, evidence, remaining)
            await share(target, evidence, observed)
    finally:
        for process, controller in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *controller, "stop")
                    await asyncio.wait_for(process.wait(), 12)
                except (RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await asyncio.wait_for(process.wait(), 12)
        if evidence:
            if original_save:
                assert saved_hashes(original_save) == original
                evidence["original_files_verified"] = len(original)
            (run / "live-ai-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence), flush=True)
    return evidence


async def main(args) -> None:
    subjects = ("host", "guest") if args.subject == "both" else (args.subject,)
    for subject in subjects:
        await exercise(args.work.resolve(), subject, hostile=args.hostile, require_combat=args.require_combat,
                       require_movement=args.require_movement, require_damage=args.require_damage,
                       enemy_loot=args.enemy_loot, original_save=args.vanilla_saved, enemy_bounty=args.enemy_bounty,
                       shared_enemy_loot=args.shared_enemy_loot, dead_bridge_cqc=args.dead_bridge_cqc,
                       automatic_cqc_loot=args.automatic_cqc_loot, both_cqc=args.both_cqc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--subject", choices=("host", "guest", "both"), default="both")
    parser.add_argument("--hostile", action="store_true", help="use the stock aggressive faction response for the staged Bandit")
    parser.add_argument("--require-combat", action="store_true")
    parser.add_argument("--require-movement", action="store_true")
    parser.add_argument("--require-damage", action="store_true")
    parser.add_argument("--enemy-loot", action="store_true", help="verify stock enemy loot attribution to the guest")
    parser.add_argument("--enemy-bounty", action="store_true", help="verify a detached Bounty perk against an empty host cache")
    parser.add_argument("--shared-enemy-loot", action="store_true", help="check the shared typed roll and alternate member's single claim")
    parser.add_argument("--dead-bridge-cqc", action="store_true",
                        help="test the exact authored Dead Bridge CQC guest kill, typed shells and once-only shared claim")
    parser.add_argument("--automatic-cqc-loot", action="store_true",
                        help="arm and bank the exact stock CQC kill through the ordinary host path")
    parser.add_argument("--both-cqc", action="store_true", help="defeat and claim from both placed CQC in one native visit")
    parser.add_argument("--vanilla-saved", type=Path)
    args = parser.parse_args()
    if args.both_cqc and not args.automatic_cqc_loot:
        parser.error("--both-cqc requires --automatic-cqc-loot")
    if args.automatic_cqc_loot and (not args.dead_bridge_cqc or args.enemy_bounty):
        parser.error("--automatic-cqc-loot requires --dead-bridge-cqc and uses existing character traits")
    if args.dead_bridge_cqc:
        if args.subject != "guest" or args.hostile or args.require_combat or args.require_movement or args.require_damage:
            parser.error("--dead-bridge-cqc requires --subject guest and uses its own stock scene")
        args.shared_enemy_loot = True
    args.enemy_loot = args.enemy_loot or args.enemy_bounty or args.shared_enemy_loot
    if args.enemy_loot and (args.subject != "guest" or args.hostile):
        parser.error("The stock shot and loot fixture uses a nonaggressive Bandit and --subject guest")
    if args.require_damage and not args.hostile:
        parser.error("--require-damage requires --hostile")
    asyncio.run(main(args))
