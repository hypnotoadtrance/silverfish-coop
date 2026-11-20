"""Verify one scoped, durable native guest stock 9mm drop and reclaim."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_guest_drop as drop, native_guest_drop_record as record, native_guest_stack_drop as probe
from tools.silverfish import native_host_save


EXIT_TAG, EXIT_ACTOR = "Obsv_Etr", "Level_Exit_BP_C_1"
NEAR_LANES = (1, 2, 3, 4, 5, 6, 7, 8, None)


async def verify(work: Path, originals_path: Path):
    name = "manual-stack-drop-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    run = campaign / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    host = ("--campaign", name)
    owner, other = ("--profile", name), ("--profile", name + "-other")
    clients = {target: work / "clients" / target[1] / "run" for target in (owner, other)}
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones",
             "--max-guests", "2", "--start-zone", record.OBSERVATORY)
    originals = saved_hashes(originals_path)
    evidence, jobs = {}, []

    async def command(*arguments, success=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, error = await asyncio.wait_for(process.communicate(), 50)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if not output or (success is not None and (process.returncode == 0) != success):
            raise RuntimeError(f"{arguments[:4]} returned {output.decode(errors='replace')} {error.decode(errors='replace')}")
        return json.loads(output)

    async def action(target, verb, *arguments, success=True):
        return await command("action", *target, verb, *arguments, success=success)

    async def start(label, kind, target, *arguments):
        with (run / (label + ".txt")).open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, kind, *target, *arguments, cwd=REPO, stdout=log,
                                                            stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((target, process))
        return process

    async def stop(target, process):
        if process.returncode is None:
            await action(target, "stop")
        assert await asyncio.wait_for(process.wait(), 15) == 0

    async def bound(count):
        return await wait(run, lambda state: state.get("state") == "hosting"
                          and state.get("game", {}).get("remote_clients") == count
                          and state["game"].get(probe.CAPABILITY) == 1
                          and state["game"].get("native_zone", {}).get("state") == "ready"
                          and len(state["game"].get("native_guest_save_members", {})) == count
                          and all(row.get("state") == "bound" for row in state["game"]["native_guest_save_members"].values()))

    async def inventory(target, character):
        page = (await action(target, "native-inventory-list"))["native_inventory"]
        assert page["owner"] == character and page["offset"] == 0 and page["next_offset"] is None, page
        return page["items"]

    def saved():
        checkpoint = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        native_host_save.verify_checkpoint(campaign / "game-user", checkpoint)
        return checkpoint["world"]["party"][record.STATE]

    def exact_position(frame, character):
        game, gameplay = frame.get("game", {}), frame.get("gameplay", {})
        pawn = gameplay.get("native_pawns", {}).get("guest")
        if (frame.get("state") != "hosting" or game.get("map") != record.OBSERVATORY or not pawn
                or gameplay.get("players", {}).get("guest", {}).get("character_id") != character
                or game.get("native_guest_save_members", {}).get(pawn, {}).get("state") != "bound"):
            return None
        return next((row["location"] for row in game.get("pawns", {}).values()
                     if row.get("name") == pawn and record.valid_position(row.get("location"))), None)

    async def place(character, position, anchor=None, preferred_lane=None):
        start = await wait(run, lambda frame: exact_position(frame, character) is not None)
        prior = exact_position(start, character)
        pawn = start["gameplay"]["native_pawns"]["guest"]
        generation, epoch = start["generation"], start["zone_epoch"]
        attempts = []
        result = {"pawn": pawn, "attempts": attempts}
        evidence.setdefault("placements", []).append(result)

        def placement_point(frame):
            if ((frame.get("generation"), frame.get("zone_epoch")) == (generation, epoch)
                    and frame.get("gameplay", {}).get("native_pawns", {}).get("guest") == pawn):
                return exact_position(frame, character)
            return None

        if position == "far":
            candidates = (("far", None), ("near", 4))
        else:
            ordered = dict.fromkeys((preferred_lane, *NEAR_LANES)) if preferred_lane else NEAR_LANES
            candidates = tuple(("near", lane) for lane in ordered)

        for where, lane in candidates:
            before = await wait(run, lambda frame: placement_point(frame) is not None, timeout=8)
            sequence = before["game"]["sequence"]
            flags = ("--amount", str(lane)) if lane is not None else ()
            placed = await action(host, "native-exit-position", "--target", "guest", "--exit-position", where,
                                  "--entrance", EXIT_TAG, "--exit-actor", EXIT_ACTOR, *flags, success=None)
            attempt = {"requested": where, "lane": lane, "response": placed}
            attempts.append(attempt)
            if placed.get("ok") is not True:
                if placed.get("error") == "No verified stock floor at the requested expanded overlap position":
                    continue
                raise RuntimeError(f"The exact Observatory stock exit fixture declined placement: {attempts}")
            assert (placed["target"], placed["position"], placed["entrance"], placed["exit_actor"]) == (
                "guest", where, EXIT_TAG, EXIT_ACTOR), placed

            samples = attempt["samples"] = []

            def settled(frame):
                point = placement_point(frame)
                observed = frame.get("game", {}).get("sequence", 0)
                if point is None or observed <= (samples[-1]["sequence"] if samples else sequence):
                    return False
                if samples and math.dist(samples[-1]["location"], point) > 18:
                    samples.clear()
                samples.append({"sequence": observed, "location": point})
                return len(samples) >= 3

            await wait(run, settled, timeout=15)
            location = attempt["location"] = samples[-1]["location"]
            if anchor is None:
                attempt["displacement"] = math.dist(location, prior)
                if attempt["displacement"] <= 75:
                    continue
            else:
                distance = attempt["distance_to_drop"] = math.dist(location, anchor)
                in_range = distance > 340 if position == "far" else distance < 180
                if not in_range:
                    continue
                result["distance_to_drop"] = distance
            result.update(location=location, lane=lane, samples=samples)
            return result

        raise RuntimeError(f"No exact Observatory lane settled at the required {position} distance: {attempts}")

    try:
        server = await start("host", "host", host, "--port", "0", *flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get(probe.CAPABILITY) == 1)
        invite = await command("invite", *host, "--name", "Drop owner", "--character", "Drop owner")
        owner_id = invite["character_id"]
        player = await start("owner", "join", owner, "--invite", invite["invite_file"], "--headless", "--native-zones")
        initial = await bound(1)
        spare = await command("invite", *host, "--name", "Non-owner", "--character", "Non-owner")
        outsider = await start("non-owner", "join", other, "--invite", spare["invite_file"], "--headless", "--native-zones")
        await bound(2)
        for target in clients:
            await wait(clients[target], lambda state: state.get("state") == "connected")

        source, attempt, claim_id = uuid.uuid4().hex.upper(), uuid.uuid4().hex, uuid.uuid4().hex
        host_items, other_items = await inventory(host, "host"), await inventory(other, spare["character_id"])
        await action(host, "native-guest-weapon-seed-ammo", "--target", "guest", "--item-guid", source)
        initial_items = await inventory(owner, owner_id)
        typed, = (item for item in initial_items if item["guid"] == source)
        assert (typed["source"], typed["class_path"], typed["quantity"]) == ("backpack", record.ITEM, 9), typed
        drop_site = await place(owner_id, "near")
        request = (drop.DROP, "--target", f"{source}|9", "--operation-id", attempt)
        dropped = (await action(owner, *request))[drop.RESULT]
        evidence["drop"] = dropped
        assert (dropped["state"], dropped["drop_id"], dropped["owner"], dropped["guid"], dropped["quantity"]) == (
            "dropped", attempt, owner_id, source, 9), dropped
        assert record.valid_position(dropped["location"])
        assert math.dist(dropped["location"], drop_site["location"]) < 180, (dropped, drop_site)
        assert (await action(owner, *request))[drop.RESULT] == dropped
        assert all(item["guid"] != source for item in await inventory(owner, owner_id))
        committed = evidence["durable_escrow"] = saved()[attempt]
        assert "escrow" in committed and committed["claim_id"] is None and "actor" not in committed, committed
        assert (await action(other, drop.RECLAIM, "--target", attempt, success=False))["error"] == "native_guest_stack_drop_not_owned"
        assert await inventory(other, spare["character_id"]) == other_items
        assert await inventory(host, "host") == host_items

        await stop(host, server)
        await stop(owner, player)
        await stop(other, outsider)
        await start("host-restart", "host", host, "--port", str(initial["endpoint"]["port"]), *flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != initial["generation"])
        renewed = await action(host, "refresh-invite", "--target", invite["guest_id"])
        await start("owner-restart", "join", owner, "--invite", renewed["invite_file"], "--headless", "--native-zones")
        await bound(1)
        owner_state = await wait(clients[owner], lambda state: state.get("state") == "connected"
                                 and state.get("character_id") == owner_id and attempt in state.get("gameplay", {}).get(record.STATE, {}))
        assert owner_state["gameplay"][record.STATE][attempt]["guid"] == source
        assert saved()[attempt] == committed
        assert all(item["guid"] != source for item in await inventory(owner, owner_id))
        assert (await action(owner, *request))[drop.RESULT] == dropped

        anchor = dropped["location"]
        far = await place(owner_id, "far", anchor)
        evidence["out_of_range_position"] = far["location"]
        refused = await action(owner, drop.RECLAIM, "--target", attempt, success=False)
        assert refused["error"] == "native_guest_stack_drop_out_of_range", refused
        near = await place(owner_id, "near", anchor, preferred_lane=drop_site["lane"])
        assert near["pawn"] == far["pawn"], (far, near)
        evidence["reclaim_position"] = near["location"]
        recover = (drop.RECLAIM, "--target", attempt, "--operation-id", claim_id)
        reclaimed = (await action(owner, *recover))[drop.RESULT]
        evidence["reclaim"] = reclaimed
        assert reclaimed == {**dropped, "state": "reclaimed"}, reclaimed
        assert saved()[attempt] == {key: value if key != "claim_id" else claim_id for key, value in committed.items() if key != "escrow"}
        assert await inventory(owner, owner_id) == initial_items and await inventory(host, "host") == host_items
        assert (await action(owner, *recover))[drop.RESULT] == reclaimed
        assert (await action(owner, drop.RECLAIM, "--target", attempt, success=False))["error"] == "native_guest_stack_drop_already_reclaimed"
        evidence["verified"] = True
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for target, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(target, process)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        evidence["original_saves_verified"] = saved_hashes(originals_path) == originals
        (run / "live-guest-stack-drop-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "Original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    settings = parser.parse_args()
    asyncio.run(verify(settings.work.resolve(), settings.vanilla_saved.resolve()))
