"""Verify the stock listen-host inventory closes on downing and inputs return on revive."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_downed_travel_smoke import HOST_FLAGS, REPO, SOURCE, exact_binding
from live_multi_smoke import saved_hashes


BLOCKED = ("jump", "inventory", "primary", "secondary", "reload", "interact")
OPEN = "native-host-downed-inventory-fixture"


def input_proof(game: dict, pawn: str, *, downed: bool, inventory: str) -> dict:
    row = game.get("player_local_input", {})
    if ((row.get("pawn"), row.get("state"), row.get("verification"), row.get("host_inventory"))
            != (pawn, "downed" if downed else "standing", "verified", inventory)
            or row.get("stock_context") is not (not downed) or row.get("reduced_context") is not downed
            or row.get("look") is not True or row.get("move") is not True
            or any(row.get(name) is not (not downed) for name in BLOCKED)):
        return {}
    return row


async def exercise(work: Path, vanilla_saved: Path | None) -> None:
    name = "downed-input-" + uuid.uuid4().hex[:8]
    host_run = work / "campaigns" / name / "run"
    guest_run = work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    host_owner, guest_owner = ("--campaign", name), ("--profile", name)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    processes = []
    evidence = {}

    async def command(*args):
        process = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, error = await asyncio.wait_for(process.communicate(), 40)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if not output or process.returncode:
            raise RuntimeError(f"{args[:2]}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        return json.loads(output)

    async def action(owner, verb, *args):
        reply = await command("action", *owner, verb, *args)
        assert reply.get("ok") is True, reply
        return reply

    async def start(kind, owner, *args):
        with (host_run / f"{kind}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, kind, *owner, *args, cwd=REPO, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))

    def authority_close(state, pawn):
        game = state.get("game", {})
        effect = game.get("player_effects", {}).get("local", {})
        return (state.get("gameplay", {}).get("players", {}).get("host", {}).get("downed") is True
                and game.get("native_dead_flags", {}).get("local") is False and effect.get("crouched") is True
                and effect.get("speed") == 200 and effect.get("jump_velocity") == 0
                and game.get("player_visuals", {}).get("local", {}).get("input_gate") is True
                and input_proof(game, pawn, downed=True, inventory="closed"))

    def guest_observes(state, host, downed):
        return (state.get("state") == "connected" and state.get("link_connected") is True
                and state.get("gameplay_fresh") is True and state.get("host_session") == host["generation"]
                and state.get("zone_epoch") == host["zone_epoch"] and state.get("game", {}).get("map") == SOURCE
                and state.get("gameplay", {}).get("players", {}).get("host", {}).get("downed") is downed)

    try:
        await start("host", host_owner, "--port", "0", "--start-zone", SOURCE, *HOST_FLAGS)
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == SOURCE)
        invite = await command("invite", *host_owner, "--name", "Stock input observer", "--character", "Medic")
        character = invite["character_id"]
        await start("join", guest_owner, "--headless", "--native-zones", "--invite", invite["invite_file"])
        baseline = await wait(host_run, lambda state: exact_binding(state, SOURCE, character)
                              and (pawn := state["game"]["pawns"]["local"]["name"])
                              and input_proof(state["game"], pawn, downed=False, inventory="clear"))
        game = baseline["game"]
        body, other = game["pawns"]["local"], game["pawns"]["guest"]
        pawn, before = body["name"], body["max_health"]
        assert before == 250 and body["health"] == before and math.dist(body["location"], other["location"]) < 250, game["pawns"]
        await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("character_id") == character
                   and state.get("link_connected") is True and state.get("host_session") == baseline["generation"]
                   and state.get("zone_epoch") == baseline["zone_epoch"])
        evidence["standing_input"] = input_proof(game, pawn, downed=False, inventory="clear")

        opened = (await action(host_owner, OPEN))["player_local_input"]
        assert (opened["pawn"], opened["state"], opened["host_inventory"]) == (pawn, "standing", "open"), opened
        visible = await wait(host_run, lambda state: input_proof(state.get("game", {}), pawn, downed=False, inventory="open"))
        evidence["open_stock_inventory"] = visible["game"]["player_local_input"]
        damage = (await action(host_owner, "native-host-damage", "--target", "host"))["native_damage_canary"]
        assert damage["slot"] == "local" and damage["guest_before"] > 0 >= damage["guest_after"], damage
        down = await wait(host_run, lambda state: authority_close(state, pawn))
        evidence["downed"] = {"stock_lethal": damage, "input": down["game"]["player_local_input"],
                              "movement": down["game"]["player_effects"]["local"]}

        await wait(guest_run, lambda state: guest_observes(state, down, True))
        interaction = await action(guest_owner, "native-interaction-fixture")
        assert interaction["native_interaction_fixture"] is True, interaction
        original_movement = baseline["game"]["player_effects"]["local"]
        revived = await wait(host_run, lambda state: state.get("gameplay", {}).get("players", {}).get("host", {}).get("downed") is False
                             and state.get("game", {}).get("native_host_revived_health") == before * 0.10
                             and input_proof(state["game"], pawn, downed=False, inventory="clear")
                             and state["game"].get("player_effects", {}).get("local") == original_movement)
        game = revived["game"]
        local = game["pawns"]["local"]
        restored_health = game["native_host_revived_health"]
        assert local["name"] == pawn and restored_health <= local["health"] <= before, game["pawns"]
        assert game["pawns"]["guest"]["name"] == other["name"] and game["pawns"]["guest"]["health"] == other["health"], game["pawns"]
        await wait(guest_run, lambda state: guest_observes(state, revived, False))
        events = (await command("events", *guest_owner, "--tail", "30"))["events"]
        receipts = [row for row in events if (row.get("event"), row.get("effect"), row.get("target")) == (
            "local_interaction", "revive", "host",
        )]
        assert len(receipts) == 1, events
        evidence["revived"] = {"input": game["player_local_input"], "movement": game["player_effects"]["local"],
                               "native_health": restored_health, "observed_health": local["health"],
                               "stock_interaction": receipts[0]}
        evidence["verified"] = True
    except BaseException as error:
        evidence.update(verified=False, failure=str(error) or type(error).__name__)
        raise
    finally:
        try:
            for process, owner in reversed(processes):
                if process.returncode is None:
                    try:
                        await action(owner, "stop")
                        await asyncio.wait_for(process.wait(), 12)
                    except (OSError, RuntimeError, TimeoutError, AssertionError):
                        if process.returncode is None:
                            process.terminate()
                            await asyncio.wait_for(process.wait(), 12)
        finally:
            if original is not None:
                evidence["original_saves_unchanged"] = saved_hashes(vanilla_saved) == original
                evidence["original_files_verified"] = len(original)
            (host_run / "live-downed-input-verification.json").write_text(json.dumps(evidence, indent=2) + "\n",
                                                                         encoding="utf-8")
            print(json.dumps(evidence), flush=True)
        if original is not None:
            assert evidence["original_saves_unchanged"], "The original single-player Saved directory changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="prepared isolated stage workspace")
    parser.add_argument("--vanilla-saved", type=Path, help="original Saved directory to hash before and after")
    options = parser.parse_args()
    asyncio.run(exercise(options.work.resolve(), options.vanilla_saved))
