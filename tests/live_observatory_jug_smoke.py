"""Observe stock attacks on the exact Observatory stalker and its response to host downing."""

import argparse
import asyncio
from contextlib import contextmanager
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_downed_travel_smoke import HOST_FLAGS, REPO, exact_binding
from live_multi_smoke import OBSERVATORY, saved_hashes
from tools.silverfish.control import submit


FIELDS = ("npc", "authority", "controller", "target", "npc_health", "aggro", "target_los", "local_pawn",
          "local_health", "guest", "guest_health", "threats", "impacts", "collision_target", "host_hits", "guest_hits",
          "largest_impulse", "impact_instigator", "impulses_decoded", "npc_damage_calls", "npc_damage_instigator")


@contextmanager
def installed_probe(work: Path):
    mod = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishObservatoryJugProbe"
    with_script = mod / "Scripts"
    script, activation = with_script / "main.lua", mod / "enabled.txt"
    mod.mkdir()
    try:
        with_script.mkdir()
        shutil.copyfile(Path(__file__).with_name("fixtures") / "observatory_jug_probe.lua", script)
        activation.touch()
        yield
    finally:
        script.unlink(missing_ok=True)
        activation.unlink(missing_ok=True)
        if with_script.exists():
            with_script.rmdir()
        mod.rmdir()


async def probe(run: Path, operation: str) -> list[str]:
    ticket = uuid.uuid4().hex
    mailbox, = (run / "game-bridge").iterdir()
    pending = mailbox / "npc-jug-command.pending"
    response = mailbox / "npc-jug-reply.tsv"
    pending.write_text(f"{ticket}\t{operation}\n", encoding="ascii", newline="")
    async with asyncio.timeout(2):
        while True:
            try:
                pending.replace(mailbox / "npc-jug-command.tsv")
                break
            except PermissionError:
                await asyncio.sleep(.025)
    async with asyncio.timeout(5):
        while True:
            try:
                fields = response.read_text(encoding="utf-8").rstrip("\r\n").split("\t")
                if fields[0] == ticket and len(fields) >= 3:
                    if fields[1] != "ok":
                        raise RuntimeError(f"{operation} on {run.parts[-3]}: {' '.join(fields[2:])}")
                    return fields[2:]
            except (FileNotFoundError, PermissionError):
                pass
            await asyncio.sleep(.025)


async def sample(run: Path) -> dict:
    row = dict(zip(FIELDS, await probe(run, "sample"), strict=True))
    for key in ("authority", "target_los", "impacts", "host_hits", "guest_hits", "impulses_decoded", "npc_damage_calls"):
        row[key] = int(row[key])
    for key in ("npc_health", "local_health", "guest_health", "largest_impulse"):
        row[key] = None if row[key] == "-" else float(row[key])
    row["threats"] = row["threats"].split("|") if row["threats"] else []
    return row


async def stock_health(run: Path) -> dict:
    pawn, slot, health, maximum, hud, player_card = await probe(run, "hudhealth")
    health, maximum, hud, player_card = map(float, (health, maximum, hud, player_card))
    assert maximum > 0 and math.isclose(hud, health / maximum, abs_tol=.00001)
    assert math.isclose(player_card, hud, abs_tol=.00001)
    return {"pawn": pawn, "slot": slot, "health": health, "maximum": maximum, "hud": hud, "player_card": player_card}


async def exercise(work: Path, canary: Path, *, attack: str, require_guest_damage: bool,
                   require_stock_health: bool = False) -> None:
    name = "observatory-jug-" + uuid.uuid4().hex[:8]
    host_run, guest_run = (work / folder / name / "run" for folder in ("campaigns", "clients"))
    evidence_path = host_run / "observatory-jug-verification.json"
    host_run.mkdir(parents=True)
    original = saved_hashes(canary)
    assert original, "The canary Saved directory has no files"
    print(evidence_path, flush=True)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    processes, evidence = [], {"attack": attack, "required_guest_damage": require_guest_damage}
    loop = asyncio.get_running_loop()

    async def start(role, run, *arguments):
        with (host_run / f"{role}.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, role, *arguments, cwd=REPO, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, run))

    async def checked(run, action, **arguments):
        reply = await submit(run, action, **arguments)
        assert reply.get("ok") is True, reply
        return reply

    async def pair():
        host, guest = await asyncio.gather(sample(host_run), sample(guest_run))
        return {"host": host, "guest": guest}

    try:
        await start("host", host_run, "--campaign", name, "--port", "0", "--start-zone", OBSERVATORY,
                    *HOST_FLAGS, "--native-expanded-zones")
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == OBSERVATORY)
        invite = await asyncio.create_subprocess_exec(*cli, "invite", "--campaign", name, "--name", "Jug observer",
                                                       "--character", "Medic", cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.STDOUT,
                                                       creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, _ = await asyncio.wait_for(invite.communicate(), 15)
        finally:
            if invite.returncode is None:
                invite.kill()
                await invite.wait()
        assert invite.returncode == 0, output.decode(errors="replace")
        invitation = json.loads(output)
        character = invitation["character_id"]
        health_flags = ("--native-guest-inventory-view",) if require_stock_health else ()
        await start("join", guest_run, "--profile", name, "--headless", "--native-zones", "--invite",
                    invitation["invite_file"], *health_flags)
        enrolled = await wait(host_run, lambda state: exact_binding(state, OBSERVATORY, character))
        scope = await wait(host_run, lambda state: (hook := state.get("game", {}).get("native_death_hook", {}))
                           and (hook.get("state"), hook.get("backend"), hook.get("scope")) == ("ready", "cpp-scoped", "party"))
        evidence["death_scope"] = scope["game"]["native_death_hook"]
        await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("link_connected")
                   and state.get("game", {}).get("map") == OBSERVATORY and state.get("character_id") == character)
        baseline = evidence["baseline"] = await pair()
        assert baseline["host"]["authority"] == 1 and baseline["host"]["target"] == "-", baseline
        assert baseline["guest"]["authority"] == 0 and baseline["guest"]["controller"] == "-", baseline
        assert baseline["host"]["local_health"] > 0 and baseline["host"]["guest_health"] > 0, baseline
        assert baseline["host"]["guest"] == enrolled["game"]["pawns"]["guest"]["name"], baseline

        pre_host_health = baseline["host"]["npc_health"]
        both_threats = attack == "pistols"
        if both_threats:
            guid = uuid.uuid4().hex.upper()
            evidence["guest_seed"] = await checked(host_run, "native-guest-weapon-seed", target="guest", item_guid=guid,
                                                   kind="light-pistol")
            evidence["guest_equip"] = await checked(guest_run, "native-guest-weapon-equip", target=guid)
            evidence["guest_shot"] = await probe(host_run, "pistolguest")
            observations = evidence["after_guest_shot"] = []
            other = baseline["host"]["guest"]
            deadline = loop.time() + 5
            while loop.time() < deadline:
                row = await pair()
                observations.append(row)
                hit = row["host"]
                if (hit["npc_damage_calls"] == 1 and hit["npc_damage_instigator"] == other
                        and 0 < hit["npc_health"] < pre_host_health and other in hit["threats"]):
                    pre_host_health = hit["npc_health"]
                    break
                await asyncio.sleep(.12)
            else:
                raise AssertionError("the genuine guest shot did not enter the stock stalker's threat memory")

        if attack in ("pistol", "pistols"):
            keys = ("weapon", "npc", "host_pawn", "guest_pawn", "shot_lane_component")
            values = await probe(host_run, "pistol")
        else:
            keys = ("prop", "npc", "host_pawn", "guest_pawn", "mass_kg", "hold_limit_kg")
            values = await probe(host_run, "grabtoolbox" if attack == "toolbox" else "grab")
            await asyncio.sleep(.3)
            assert await probe(host_run, "throw") == values[:2], values
        stock = evidence["stock_attack"] = dict(zip(keys, values, strict=True))
        assert (stock["npc"], stock["host_pawn"], stock["guest_pawn"]) == tuple(
            baseline["host"][key] for key in ("npc", "local_pawn", "guest")), stock
        observations = evidence["after_stock_attack"] = []
        deadline = loop.time() + 5
        while loop.time() < deadline:
            snapshot = await pair()
            observations.append(snapshot)
            hit = snapshot["host"]
            impact_verified = attack in ("pistol", "pistols") or (
                hit["impacts"] > 0 and hit["collision_target"] == stock["npc"])
            remembered = not both_threats or all(hit["threats"].count(stock[role]) == 1
                                                 for role in ("host_pawn", "guest_pawn"))
            if (impact_verified and hit["target"] == stock["host_pawn"]
                    and 0 < hit["npc_health"] < pre_host_health and hit["npc_damage_calls"] == (2 if both_threats else 1)
                    and hit["npc_damage_instigator"] == stock["host_pawn"] and remembered):
                break
            await asyncio.sleep(.12)
        else:
            if attack in ("jug", "toolbox") and hit["impacts"] == 0:
                raise AssertionError("the authored prop missed the stalker")
            raise AssertionError("the stock attack did not establish damage and the host as the stalker's target")

        if require_stock_health:
            await checked(guest_run, "native-inventory-view-fixture")
            await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
            before_health = evidence["stock_health_before"] = await stock_health(guest_run)
            assert before_health["pawn"] == baseline["guest"]["local_pawn"]

        identities_and_health = ("host_pawn", "host_before", "host_after", "guest_pawn", "guest_before", "guest_after")
        damage = dict(zip(identities_and_health, await probe(host_run, "downhost"), strict=True))
        for key in ("host_before", "host_after", "guest_before", "guest_after"):
            damage[key] = float(damage[key])
        evidence["qualified_host_lethal"] = damage
        assert (damage["host_pawn"], damage["guest_pawn"]) == (stock["host_pawn"], stock["guest_pawn"]), damage
        assert damage["host_before"] > 0 >= damage["host_after"] and damage["guest_before"] == damage["guest_after"], damage
        await wait(host_run, lambda state: state.get("gameplay", {}).get("players", {}).get("host", {}).get("downed")
                   is True and state.get("game", {}).get("native_dead_flags", {}).get("local") is False, timeout=8)

        observations = evidence["after_host_down"] = []
        upright = stock["guest_pawn"]
        deadline = loop.time() + (12 if require_guest_damage else 4)
        while loop.time() < deadline:
            row = await pair()
            observations.append(row)
            if (both_threats and row["host"]["target"] == upright and row["host"]["guest_hits"] > hit["guest_hits"]
                    and row["host"]["guest_health"] < hit["guest_health"]):
                break
            await asyncio.sleep(.18)
        observation = evidence["observed"] = {
            "guest_was_a_remembered_threat": upright in hit["threats"],
            "host_target_cleared_or_changed": observations[-1]["host"]["target"] != stock["host_pawn"],
            "host_npc_targeted_guest": any(row["host"]["target"] == upright for row in observations),
            "downed_host_removed_from_threats": any(row["host"]["target"] == upright
                                                    and stock["host_pawn"] not in row["host"]["threats"]
                                                    for row in observations),
            "guest_npc_had_local_authority": any(row["guest"]["authority"] == 1 for row in observations),
            "npc_damage_to_downed_host": any(row["host"]["host_hits"] > hit["host_hits"] for row in observations),
            "guest_line_of_sight_samples": sum(row["host"]["target_los"] for row in observations
                                                if row["host"]["target"] == upright),
            "guest_took_damage_from_stalker": any(row["host"]["target"] == upright
                                                   and row["host"]["guest_hits"] > hit["guest_hits"]
                                                   and row["host"]["guest_health"] < hit["guest_health"]
                                                   for row in observations),
        }
        assert observation["host_target_cleared_or_changed"] and not observation["guest_npc_had_local_authority"], observation
        assert not observation["npc_damage_to_downed_host"], observation
        assert all(row["host"]["authority"] == 1 and row["guest"]["controller"] == "-" for row in observations)
        if both_threats:
            assert observation["guest_was_a_remembered_threat"] and observation["host_npc_targeted_guest"], observation
            assert observation["downed_host_removed_from_threats"], observation
        if require_guest_damage:
            assert observation["guest_took_damage_from_stalker"], observation
        if require_stock_health:
            deadline = loop.time() + 5
            while loop.time() < deadline:
                current = await sample(host_run)
                display = await stock_health(guest_run)
                if (current["guest_hits"] > hit["guest_hits"] and display["pawn"] == before_health["pawn"]
                        and display["slot"] == before_health["slot"] and 0 < display["health"] < before_health["health"]
                        and math.isclose(display["health"], current["guest_health"], abs_tol=1)):
                    evidence["stock_health_after"] = {"authority_health": current["guest_health"], **display}
                    break
                await asyncio.sleep(.1)
            else:
                raise AssertionError(f"the stock HUD and PlayerCard missed the guest's nonlethal health debit: {display}")
        evidence["stock_repro_completed"] = True
    except BaseException as error:
        evidence.update(stock_repro_completed=False, failure=str(error) or type(error).__name__)
        raise
    finally:
        try:
            for process, run in reversed(processes):
                if process.returncode is not None:
                    continue
                try:
                    await asyncio.wait_for(checked(run, "stop"), 10)
                    await asyncio.wait_for(process.wait(), 12)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 12)
        finally:
            evidence["workers_closed"] = all(process.returncode is not None for process, _ in processes)
            evidence["canary_files_verified"] = len(original)
            evidence["canary_unchanged"] = saved_hashes(canary) == original
            evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["canary_unchanged"], "The canary Saved directory changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="prepared isolated stage workspace")
    parser.add_argument("--canary", type=Path, required=True, help="canary Saved directory to hash before and after")
    attack = parser.add_mutually_exclusive_group()
    attack.add_argument("--toolbox", dest="attack", action="store_const", const="toolbox",
                        help="throw the authored 25 kg toolbox instead of the plastic jug")
    attack.add_argument("--pistol", dest="attack", action="store_const", const="pistol",
                        help="fire one round from the stock light pistol instead of throwing the jug")
    attack.add_argument("--pistols", dest="attack", action="store_const", const="pistols",
                        help="fire one guest round and one host round to establish both stock threats")
    parser.set_defaults(attack="jug")
    parser.add_argument("--require-guest-damage", action="store_true",
                        help="also require one stock hit on the standing guest during 12 seconds after retargeting")
    parser.add_argument("--require-stock-health", action="store_true",
                        help="also verify the HUD and PlayerCard progress getters after a real guest hit")
    options = parser.parse_args()
    if options.require_guest_damage and options.attack != "pistols":
        parser.error("--require-guest-damage requires --pistols")
    if options.require_stock_health and not options.require_guest_damage:
        parser.error("--require-stock-health requires --require-guest-damage")
    work = options.work.resolve()
    with installed_probe(work):
        asyncio.run(exercise(work, options.canary.resolve(), attack=options.attack,
                             require_guest_damage=options.require_guest_damage,
                             require_stock_health=options.require_stock_health))
