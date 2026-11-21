"""Exercise the Playtest Bandit or exact placed NFTA CQC in a headless loopback party."""

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
REFINERY = "/Game/Classes/Maps/Maps_Dungeons/Refinery_01_P"
REFINERY_CQC = f"NPC_State_Infantry_CQC_C {REFINERY}.Refinery_01_P:PersistentLevel.NPC_State_Infantry_CQC_C_1"
REFINERY_CQC_SECOND = REFINERY_CQC.removesuffix("_1") + "_3"
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


def cqc_counterpart(directory: Path, actor: str = CQC, area: str = DEAD_BRIDGE) -> dict | None:
    paths = tuple((directory / "game-bridge").glob("*/status.tsv"))
    if len(paths) != 1:
        return None
    try:
        lines = paths[0].read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    for line in lines:
        fields = line.split("\t")
        if fields[:3] != ["native-enemy-loot-counterpart", area, actor] or len(fields) != 9:
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
                   automatic_cqc_loot: bool = False, both_cqc: bool = False, stock_shell_input: str | None = None,
                   restart_unclaimed_shells: bool = False, refinery_cqc: bool = False, stock_drop_census: bool = False,
                   stock_weapon_input: bool = False, restart_unclaimed_weapon: bool = False) -> dict:
    placed_cqc = dead_bridge_cqc or refinery_cqc
    area = REFINERY if refinery_cqc else DEAD_BRIDGE if dead_bridge_cqc else MAP
    first_target, second_target = (REFINERY_CQC, REFINERY_CQC_SECOND) if refinery_cqc else (CQC, CQC_SECOND)
    encounter = "refinery-cqc" if refinery_cqc else "dead-bridge-cqc" if dead_bridge_cqc else f"bandit-{subject}"
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
        route_options = ("--native-expanded-zones",) if placed_cqc else ()
        if automatic_cqc_loot:
            route_options += ("--native-dead-bridge-enemy-loot",)
            evidence["automatic_cqc_loot"] = True
        host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", *route_options)
        await start("host.txt", host, "host", *host, "--port", "0", *host_flags, "--start-zone", area)
        await wait(run, lambda state: state.get("state") == "hosting"
                   and state.get("game", {}).get("map") == area
                   and (placed_cqc or bandit(state).get("status") == "waiting_for_stock_bandit"))
        invitation = await command("invite", *host, "--name", "Loopback enemy observer", "--character", "Enemy observer")
        client_flags = ("--invite", invitation["invite_file"], "--headless", "--native-zones")
        await start("client.txt", client, "join", *client, *client_flags)
        await wait(guest_run, lambda state: state.get("state") == "connected"
                   and state.get("game", {}).get("map") == area
                   and (placed_cqc or bandit(state).get("status") == "waiting_for_stock_bandit"))
        paired = await wait(run, lambda state: state.get("game", {}).get("native_roster", {}).get("count") == 1
                            and (state.get("game", {}).get("native_guest_save", {}).get("state") == "bound"
                                 if placed_cqc else bandit(state).get("watched_pawn")))
        if placed_cqc:
            exact_pawn = paired["game"]["native_guest_save"]["pawn"]
            visible = await wait(guest_run, lambda state: cqc_counterpart(guest_run, first_target, area) is not None
                                 and state.get("game", {}).get("native_enemy_loot_death_view", {}).get("state") == "present")
            evidence["client_cqc_before_combat"] = cqc_counterpart(guest_run, first_target, area)
            before = visible["game"]["native_enemy_loot_death_view"]
            assert (before["actor"], before["hidden"], before["collision"]) == (first_target, False, True), before
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
        loadout = "sg-71-a" if both_cqc else "light-pistol"
        min_loaded, projectiles = (1, 9) if both_cqc else (2, 1)
        projectile = "Projectile_ShotGun_Pellet_C" if both_cqc else "Projectile_Pistol_Light_C"
        if enemy_loot:
            await wait(run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            seeded = accepted("stage stock loadout", await combat_action(run, "native-guest-weapon-seed", kind=loadout))
            weapon_guid = seeded["native_guest_weapon"]["guid"]
            equipped = accepted("equip stock loadout", await combat_action(guest_run, "native-guest-weapon-equip", weapon_guid))
            proof = equipped["native_guest_weapon"]
            assert proof["status"] == "equipped" and proof["pawn"] == exact_pawn and proof["guid"] == weapon_guid, proof
            remaining = proof["stored_ammo"]
            reserve = accepted("stage the weapon's stock reserve", await combat_action(run, "native-guest-weapon-seed-ammo"))
            evidence["stock_loadout"] = {"kind": loadout, "weapon": proof, "reserve": reserve["native_guest_weapon"]}

        if placed_cqc:
            target = first_target
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
            if placed_cqc:
                evidence["authority_watch"] = armed
            if both_cqc:
                second_watched = await wait(run, lambda state: actor_proof(state, "native_enemy_loot", second_target)
                                            .get("state") == "watching")
                evidence["second_authority_watch"] = actor_proof(second_watched, "native_enemy_loot", second_target)
        if not placed_cqc:
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

                if remaining < min_loaded:
                    loaded = await combat_action(guest_run, "native-guest-weapon-reload", weapon_guid)
                    if loaded.get("error") == "downed":
                        await wait(run, lambda state: killed(state) or guest_downed(state), timeout=4)
                        continue
                    remaining = accepted("reload stock firearm", loaded)["native_guest_weapon"]["after"]
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
                    accepted("aim and fire the stock firearm", fire)
                aim = fire["native_bandit_aim"]
                assert (aim["bandit"], aim["pawn"]) == (target, exact_pawn), aim
                if refinery_cqc:
                    distance, deviation = (aim["withdrawal"][field] for field in ("distance", "return_error"))
                    assert distance >= 0 and 0 <= deviation <= 25, aim
                fired = fire["native_guest_weapon"]
                assert (fired["pawn"], fired["guid"], fired["before"], fired["after"], fired["projectiles"]) == (
                    exact_pawn, weapon_guid, remaining, remaining - 1, projectiles
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
            assert observed["hp_before"] > 0 >= observed["hp_after"] and observed["projectile"].startswith(f"{projectile} "), observed
            proof_log["enemy_loot_from_exact_guest_projectile"] = observed
            if stock_drop_census:
                from tools.silverfish import native_host_save, native_stock_enemy_drops
                from tools.silverfish.native_stock_enemy_drops import KEY

                def same_kill(frame):
                    return [item for item in frame.get("game", {}).get(KEY, {}).values()
                            if (item["source"], item["killer"]) == (target, exact_pawn)]

                receipt = await wait(run, lambda frame: any(item["class_path"] == SHELLS for item in same_kill(frame))
                                     and any(item["class_path"].startswith("/Game/Classes/Weapons/") for item in same_kill(frame)),
                                     timeout=8)
                reports = same_kill(receipt)
                assert len({item["roll"] for item in reports}) == 1, reports
                assert sum(item["actor"] == observed["ammo_actor"] for item in reports) == 1, reports
                assert all(item["live_at_capture"] for item in reports if item["class_path"] != SHELLS), reports
                proof_log["stock_drop_census"] = reports
                weapon = next(item for item in reports if item["class_path"].startswith("/Game/Classes/Weapons/"))
                campaign = run.parent

                def durable_drop(_):
                    saved = json.loads((campaign / "save" / "campaign.json").read_bytes())
                    loot = saved["world"]["party"].get(native_stock_enemy_drops.STATE, {})
                    return next((row for row in loot.values() if row["actor"] == weapon["actor"]), None)

                durable = durable_drop(await wait(run, durable_drop, timeout=12))
                native_host_save.verify_record(campaign / "game-user", durable["escrow"])
                assert (durable["class_path"], durable["killer"], durable["claimed_by"]) == (
                    weapon["class_path"], invitation["character_id"], None), durable
                assert durable["ammo"] > 0, durable
                proof_log["stock_dropped_weapon_payload"] = durable
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

        async def restart_pair():
            old = status(run)
            port = old["endpoint"]["port"]
            for process, controller in reversed(processes.copy()):
                await command("action", *controller, "stop")
                await asyncio.wait_for(process.wait(), 20)
            await start("host-restart.txt", host, "host", *host, "--port", str(port), *host_flags)
            restored = await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != old["generation"]
                                  and state.get("game", {}).get("map") == area and state.get("zone_epoch") != old["zone_epoch"])
            assert restored["endpoint"]["port"] == port, restored
            renewed = await command("action", *host, "refresh-invite", "--target", invitation["guest_id"])
            assert (renewed["guest_id"], renewed["character_id"]) == (invitation["guest_id"], invitation["character_id"])
            await start("client-restart.txt", client, "join", *client, "--invite", renewed["invite_file"], "--headless", "--native-zones")
            await wait(guest_run, lambda state: state.get("gameplay_fresh") and state.get("zone_epoch") == restored["zone_epoch"])
            await wait(run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            return old["zone_epoch"], restored["zone_epoch"]

        async def restart_with_unclaimed_shell(guid, target):
            old_epoch, restored_epoch = await restart_pair()
            resumed = await wait(guest_run, lambda state: actor_proof(state, "native_enemy_loot_death_view", target).get(
                "state") == "present")
            await wait(run, lambda state: actor_proof(state, "native_enemy_loot", target).get("state") == "watching")
            selected = f"enemy-shell:{old_epoch}:{guid}"
            stale = await combat_action(run, "native-enemy-loot-claim", selected)
            assert stale.get("ok") is False and stale.get("error") == "native_enemy_loot_stale_focus", stale
            evidence["restart_unclaimed_shell"] = {
                "guid": guid, "drop_epoch": old_epoch, "interaction_epoch": restored_epoch,
                "former_input_refused": stale["error"], "restored_stock_npc": actor_proof(resumed, "native_enemy_loot_death_view", target),
                "character_id": invitation["character_id"],
            }
            return restored_epoch

        async def share(target, proof_log, observed, *, defer_until_restart=False):
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
                if placed_cqc:
                    assert (roll["map"], roll["bandit"], roll["class_path"]) == (area, target, SHELLS), roll
                    assert 3 <= roll["quantity"] <= 9, roll
                    from tools.silverfish.native_host_save import cqc_retired, verify_record
                    campaign = run.parent
                    saved = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
                    durable = saved["world"]["party"]["native_enemy_loot_rolls"][guid]
                    verify_record(campaign / "game-user", durable["escrow"])
                    assert {key: value for key, value in durable.items() if key not in ("escrow", "retirement")} == roll, durable
                    if restart_unclaimed_shells or (automatic_cqc_loot and status(run)["game"].get("native_enemy_shell_view_version") == 2):
                        assert cqc_retired(durable), durable
                    proof_log["verified_stock_shell_escrow"] = durable

                    def projected(state):
                        view = actor_proof(state, "native_enemy_loot_death_view", target)
                        return state.get("gameplay_fresh") and state.get("zone_epoch") == roll["epoch"] and (
                            view.get("actor"), view.get("state"), view.get("epoch"), view.get("guid"),
                            view.get("hidden"), view.get("collision"),
                        ) == (target, "projected", roll["epoch"], guid, True, False)

                    defeat = await wait(guest_run, projected, timeout=12)
                    counterpart = cqc_counterpart(guest_run, target, area)
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
                interaction_epoch = await restart_with_unclaimed_shell(guid, target) if defer_until_restart else roll["epoch"]
                winner = stock_shell_input or "host"
                recipient = invitation["character_id"] if winner == "guest" else "host"
                winner_run, loser_run = (guest_run, run) if winner == "guest" else (run, guest_run)

                async def approach_drop(role):
                    deadline = time.monotonic() + 5
                    while True:
                        placed = await combat_action(run, "native-enemy-loot-approach", f"{role}|{guid}")
                        if placed.get("error") != "native_enemy_loot_claimant_unavailable" or time.monotonic() >= deadline:
                            return accepted(f"place the {role} near the stock drop", placed)
                        await asyncio.sleep(.25)

                selection, operation = guid, uuid.uuid4().hex
                if stock_shell_input:
                    observer = "host" if winner == "guest" else "guest"
                    await approach_drop(observer)
                    await approach_drop(winner)
                    identity = uuid.uuid5(uuid.NAMESPACE_OID, f"enemy-loot:{guid}").hex

                    def projected_item(state):
                        return state.get("game", {}).get("native_world_loot_items", {}).get(identity, {})

                    for local_run in (run, guest_run):
                        visible = await wait(local_run, lambda state: projected_item(state).get("class_path") == SHELLS, timeout=10)
                        assert not visible["game"].get("native_enemy_shell_view"), visible["game"].get("native_enemy_shell_view")
                    selection = f"enemy-shell:{interaction_epoch}:{guid}"
                    pressed = await combat_action(winner_run, "native-world-loot-fixture", identity)
                    if pressed.get("error", "").startswith(("world_loot_fixture_focus_unavailable",
                                                            "world_loot_fixture_outside_verified_reach")):
                        accepted("place the claimant in sight of the stock actor", await combat_action(
                            run, "native-world-loot-approach", f"{winner}@{identity}"))
                        pressed = await combat_action(winner_run, "native-world-loot-fixture", identity)
                    press = accepted("exercise the claimant's stock Interact", pressed)["native_world_loot_items"][identity]
                    assert press["map"] == area and press["sequence"] > 0, press
                    settled = await wait(run, lambda state: state.get("gameplay", {}).get(
                        "native_enemy_loot_rolls", {}).get(guid, {}).get("claimed_by") == recipient, timeout=12)
                    claimed = settled["gameplay"]["native_enemy_loot_rolls"][guid]
                    events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
                    event, = [entry for entry in events if (entry.get("event"), entry.get("guid")) == (
                        "native_enemy_loot_claim_committed", guid
                    )]
                    operation = event["operation_id"]
                    for local_run in (run, guest_run):
                        await wait(local_run, lambda state: state.get("active") and not projected_item(state), timeout=10)
                    proof_log["stock_shell_input"] = {"press": press, "commit": event, "both_actors_cleared": True}
                else:
                    await approach_drop(winner)
                    claimed = accepted("claim the stock shells for the host", await combat_action(
                        run, "native-enemy-loot-claim", guid, operation_id=operation))["native_enemy_loot_claim"]
                assert claimed == dict(roll, claimed_by=recipient), claimed
                retry = accepted("replay the exact stock claim", await combat_action(
                    winner_run, "native-enemy-loot-claim", selection, operation_id=operation))["native_enemy_loot_claim"]
                assert retry == claimed, retry
                refused = await combat_action(loser_run, "native-enemy-loot-claim", guid)
                assert refused.get("ok") is False and refused.get("error") == "native_enemy_loot_already_claimed", refused
                home = accepted("inspect the claimant's inventory", await combat_action(winner_run, "native-inventory-list"))["native_inventory"]
                away = accepted("inspect the other member's inventory", await combat_action(loser_run, "native-inventory-list"))["native_inventory"]
                row, = [item for item in home["items"] if item["guid"] == guid]
                assert (row["class_path"], row["quantity"]) == (roll["class_path"], roll["quantity"]), row
                assert all(item["guid"] != guid for item in away["items"]), away
                proof_log["shared_enemy_ammunition"] = {"roll": roll, "claim": claimed, "stored_item": row,
                                                       "duplicate_guest_claim" if winner == "host" else "duplicate_host_claim": refused["error"]}
                if placed_cqc:
                    settled = await wait(guest_run, lambda state: state.get("gameplay", {}).get(
                        "native_enemy_loot_rolls", {}).get(guid) == claimed)
                    assert settled["gameplay_fresh"], settled
                    saved = json.loads((run.parent / "save" / "campaign.json").read_text(encoding="utf-8"))
                    durable = saved["world"]["party"]["native_enemy_loot_rolls"][guid]
                    assert {key: value for key, value in durable.items() if key != "retirement"} == claimed, durable
                    if defer_until_restart:
                        assert cqc_retired(durable), durable
                    assert saved["world"]["map"] == area, saved["world"]
                    if defer_until_restart:
                        return
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
        async def pickup_stock_weapon(payload, stock_source):
            from tools.silverfish import native_host_save, native_stock_enemy_drops, native_world_loot

            guid = payload["guid"]
            identity = native_world_loot.stock_enemy_identity(guid)
            area = payload["map"]

            def projection(state):
                return state.get("game", {}).get(native_world_loot.KEY, {}).get(identity, {})

            async def look(directory):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    state = status(directory)
                    if projection(state).get("class_path") == payload["class_path"]:
                        return projection(state)
                    await asyncio.sleep(.2)
                raise AssertionError({
                    "directory": str(directory), "expected": identity, "world": state.get("state"),
                    "pawns": state["game"].get("pawns"), "items": state["game"].get(native_world_loot.KEY),
                    "errors": state["game"].get(native_world_loot.ERROR_KEY),
                    "public": state.get("gameplay", {}).get(native_stock_enemy_drops.STATE),
                })

            shells = await wait(run, lambda state: any(row["bandit"] == stock_source for row in state.get(
                "gameplay", {}).get("native_enemy_loot_rolls", {}).values()))
            shell = next(item for item in shells["gameplay"]["native_enemy_loot_rolls"].values() if item["bandit"] == stock_source)
            if restart_unclaimed_weapon:
                await restart_pair()
                campaign = run.parent
                saved = json.loads((campaign / "save" / "campaign.json").read_bytes())
                assert saved["world"]["party"][native_stock_enemy_drops.STATE][guid] == payload
                native_host_save.verify_record(campaign / "game-user", payload["escrow"])
                evidence["restart_unclaimed_weapon"] = {"guid": guid, "port": status(run)["endpoint"]["port"],
                                                        "character_id": invitation["character_id"]}
            for role in ("host", "guest"):
                accepted(f"place the {role} near the stock loot", await combat_action(
                    run, "native-enemy-loot-approach", f"{role}|{shell['guid']}"))
            visible = await look(run), await look(guest_run)
            accepted("approach the visible stock weapon", await combat_action(
                run, native_world_loot.APPROACH, f"guest@{identity}"))
            if status(run)["gameplay"]["players"]["guest"]["downed"]:
                accepted("place the host in revive range", await combat_action(
                    run, native_world_loot.APPROACH, f"host@{identity}"))
                result = accepted("revive the guest before picking up the weapon", await combat_action(
                    run, "interact", invitation["character_id"]))
                assert result["effect"] == "revive", result
                await wait(run, lambda state: state["gameplay"]["players"]["guest"]["downed"] is False
                           and state["game"].get("player_effects", {}).get("guest", {}).get("state") == "standing")
            pressed = accepted("pick up the weapon with stock Interact", await combat_action(
                guest_run, native_world_loot.FIXTURE, identity))[native_world_loot.KEY][identity]
            assert pressed["map"] == area and pressed["sequence"] > 0, pressed

            def claim_event(_):
                events = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
                return next((entry for line in events if (entry := json.loads(line)).get("guid") == guid
                             and entry.get("event") == "native_stock_enemy_loot_claimed"), None)

            commit = claim_event(await wait(run, claim_event, timeout=12))
            assert (commit["claimed_by"], commit["ammo"]) == (invitation["character_id"], payload["ammo"]), commit
            for directory in (run, guest_run):
                await wait(directory, lambda state: state.get("active") and not projection(state))
            replay = accepted("replay the stock weapon claim", await combat_action(
                guest_run, native_stock_enemy_drops.CLAIM, guid, operation_id=commit["operation_id"]))
            assert replay[native_stock_enemy_drops.CLAIM_KEY]["guid"] == guid, replay
            competing = await combat_action(run, native_stock_enemy_drops.CLAIM, guid)
            assert competing["error"] == "stock_enemy_loot_already_claimed", competing
            inventory = accepted("inspect the stock weapon in the guest's backpack",
                                 await combat_action(guest_run, "native-inventory-list"))["native_inventory"]
            item, = [row for row in inventory["items"] if row["guid"] == guid]
            assert item["class_path"] == payload["class_path"], item
            campaign = run.parent
            saved = json.loads((campaign / "save" / "campaign.json").read_bytes())
            native_host_save.verify_checkpoint(campaign / "game-user", saved)
            durable = saved["world"]["party"][native_stock_enemy_drops.STATE][guid]
            assert "escrow" not in durable and durable["claimed_by"] == invitation["character_id"], durable
            return {"visible": visible, "press": pressed, "claim": commit, "inventory": item,
                    "competing_claim": competing["error"], "replayed": replay[native_stock_enemy_drops.CLAIM_KEY]}

        if enemy_loot and both_cqc:
            encounters = evidence["stock_cqc_encounters"] = {first_target: {}, second_target: {}}
            # Complete both stock fights before keeping the party exposed during
            # inventory, shared claims or Direct IP reconnect verification.
            remaining, first_observed = await fight(first_target, encounters[first_target], remaining)
            sibling = actor_proof(status(guest_run), "native_enemy_loot_death_view", second_target)
            assert (sibling.get("state"), sibling.get("actor")) == ("present", second_target), sibling
            _, second_observed = await fight(second_target, encounters[second_target], remaining)
            if stock_drop_census:
                first_drops = encounters[first_target]["stock_drop_census"]
                second_drops = encounters[second_target]["stock_drop_census"]
                assert first_drops[0]["roll"] != second_drops[0]["roll"]
                assert {item["actor"] for item in first_drops}.isdisjoint(item["actor"] for item in second_drops)
                if stock_weapon_input:
                    second = encounters[second_target]
                    second["stock_weapon_pickup"] = await pickup_stock_weapon(second["stock_dropped_weapon_payload"], second_target)
            if not stock_drop_census or stock_shell_input:
                await share(first_target, encounters[first_target], first_observed)
                await share(second_target, encounters[second_target], second_observed, defer_until_restart=restart_unclaimed_shells)
                first, second = (encounters[actor]["shared_enemy_ammunition"]["claim"] for actor in (first_target, second_target))
                assert first["epoch"] == second["epoch"] and first["guid"] != second["guid"] and first["actor"] != second["actor"]
                final = status(run)["gameplay"]["native_enemy_loot_rolls"]
                assert final == {first["guid"]: first, second["guid"]: second}, final
        elif enemy_loot:
            _, observed = await fight(target, evidence, remaining)
            if stock_weapon_input:
                evidence["stock_weapon_pickup"] = await pickup_stock_weapon(evidence["stock_dropped_weapon_payload"], target)
            if not stock_drop_census or stock_shell_input:
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
                       automatic_cqc_loot=args.automatic_cqc_loot, both_cqc=args.both_cqc,
                       stock_shell_input=args.stock_shell_input, restart_unclaimed_shells=args.restart_unclaimed_shells,
                       refinery_cqc=args.refinery_cqc, stock_drop_census=args.stock_drop_census,
                       stock_weapon_input=args.stock_weapon_input, restart_unclaimed_weapon=args.restart_unclaimed_weapon)


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
    parser.add_argument("--refinery-cqc", action="store_true", help="test both exact naturally placed Refinery CQC")
    parser.add_argument("--automatic-cqc-loot", action="store_true",
                        help="arm and bank the exact stock CQC kill through the ordinary host path")
    parser.add_argument("--both-cqc", action="store_true", help="defeat and claim from both placed CQC in one native visit")
    parser.add_argument("--stock-shell-input", choices=("host", "guest"), help="claim visible shells through stock Interact")
    parser.add_argument("--restart-unclaimed-shells", action="store_true", help="restart before claiming the second stock shell stack")
    parser.add_argument("--stock-drop-census", action="store_true",
                        help="verify observed ammunition and weapon payloads; add --stock-shell-input to test shell pickup too")
    parser.add_argument("--stock-weapon-input", action="store_true", help="claim the stock dropped weapon through Interact")
    parser.add_argument("--restart-unclaimed-weapon", action="store_true", help="restart before the stock weapon pickup")
    parser.add_argument("--vanilla-saved", type=Path)
    args = parser.parse_args()
    if args.refinery_cqc and (args.dead_bridge_cqc or not args.both_cqc):
        parser.error("--refinery-cqc requires --both-cqc and its own encounter")
    placed_cqc = args.dead_bridge_cqc or args.refinery_cqc
    if args.restart_unclaimed_weapon:
        args.stock_weapon_input = True
    if args.stock_weapon_input:
        args.stock_drop_census = True
    if args.stock_drop_census and not args.automatic_cqc_loot:
        parser.error("--stock-drop-census requires --automatic-cqc-loot")
    if args.both_cqc and not args.automatic_cqc_loot:
        parser.error("--both-cqc requires --automatic-cqc-loot")
    if args.stock_shell_input and not args.automatic_cqc_loot:
        parser.error("--stock-shell-input requires --automatic-cqc-loot")
    if args.restart_unclaimed_shells and (not args.both_cqc or args.stock_shell_input != "host"):
        parser.error("--restart-unclaimed-shells requires --both-cqc and --stock-shell-input host")
    if args.automatic_cqc_loot and (not placed_cqc or args.enemy_bounty):
        parser.error("--automatic-cqc-loot requires a placed Dead Bridge or Refinery encounter and uses existing character traits")
    if placed_cqc:
        if args.subject != "guest" or args.hostile or args.require_combat or args.require_movement or args.require_damage:
            parser.error("--dead-bridge-cqc requires --subject guest and uses its own stock scene")
        args.shared_enemy_loot = True
    args.enemy_loot = args.enemy_loot or args.enemy_bounty or args.shared_enemy_loot
    if args.enemy_loot and (args.subject != "guest" or args.hostile):
        parser.error("The stock shot and loot fixture uses a nonaggressive Bandit and --subject guest")
    if args.require_damage and not args.hostile:
        parser.error("--require-damage requires --hostile")
    asyncio.run(main(args))
