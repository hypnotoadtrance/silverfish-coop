"""Verify stock Data Silo overlap, guest Results compilation, and same-port custody."""

import argparse
import asyncio
from hashlib import sha256
import json
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

from tools.silverfish import native_host_save, native_mindcore as core, native_mindcore_fixture as fixture, runtime, service
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import submit
from tools.silverfish.native_tape_profiles import DRILL, drill_download_unlocked
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, RUIN_FIELDS, SCIENTIST_SHOP


STONE_MAZE = "/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P"
ANNEX = "/Game/Classes/Maps/Maps_Dungeons/Anex_01_P"
WALL = "/Game/Classes/Maps/Maps_OverWorld/TheWall_01_P"
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


def baseline_inputs(work, baseline):
    if (baseline.resolve(strict=True) != baseline or baseline.is_junction()
            or baseline.parents[2] != work.parent or baseline.parent.name != "campaigns"
            or not baseline.name.startswith("drill-results-")):
        raise ValueError("Choose a verified Results campaign from an isolated sibling lab")
    source = {
        "campaign": baseline / "save/campaign.json",
        "identity": baseline / "save/host-identity.json",
        "proof": baseline / "run/live-drill-test-results-verification.json",
    }
    for path in source.values():
        if path.resolve(strict=True) != path or path.is_symlink():
            raise ValueError("A Results input is outside its exact isolated campaign")
    data, proof = runtime.read_json(source["campaign"]), runtime.read_json(source["proof"])
    receipt = proof["station"]["receipt"]
    character, guid, blank = (receipt[key] for key in ("owner", "filled_guid", "blank_guid"))
    party = data["world"]["party"]
    if (data["world"]["map"] != SCIENTIST_SHOP or proof.get("verified") is not True
            or not drill_download_unlocked(party) or party.get(DRILL.ledger, {}).get(blank) != receipt
            or not core._conversion(party, character, guid) or core.LEDGER in party):
        raise ValueError("The checkpoint must have the verified Scientist Results and a fresh Data Silo console")
    native_host_save.verify_checkpoint(baseline / "game-user", data)
    guest_id = str(uuid.UUID(data["characters"][character]["guest_id"]))
    source["invite"] = baseline / "run/invites" / f"{guest_id}.invite.json"
    if source["invite"].resolve(strict=True) != source["invite"] or source["invite"].is_symlink():
        raise ValueError("The Results owner's paired invitation is unavailable")
    invitation = runtime.read_json(source["invite"])
    if (invitation.get("host"), invitation.get("guest_id"), invitation.get("character_id")) != (
            "127.0.0.1", guest_id, character):
        raise ValueError("The proof needs the exact loopback Results owner")
    return data, source, receipt, guest_id


async def exercise(work, baseline, vanilla_saved):
    if not (runtime.qualified_authored_routes(work)
            and runtime.staged_script(work, "native_mindcore.lua", core.FLAG)
            and runtime.staged_script(work, "native_mindcore_fixture.lua", fixture.INSTALL)):
        raise ValueError("Use the matched isolated native stage with the stock overlap fixture")
    originals = saved_hashes(vanilla_saved)
    if not originals:
        raise ValueError("Supply the original Saved directory for isolation verification")
    data, inputs, conversion, guest_id = baseline_inputs(work, baseline)
    initial_inputs = {key: sha256(path.read_bytes()).hexdigest() for key, path in inputs.items()}
    character, guid, blank = (conversion[key] for key in ("owner", "filled_guid", "blank_guid"))
    name = "mindcore-" + uuid.uuid4().hex[:8]
    campaign, client = work / "campaigns" / name, work / "clients" / name
    run, guest_run = campaign / "run", client / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    proof = {"baseline": baseline.name, "routes": [], "source_results": conversion}
    jobs = []

    async def action(directory, verb, *, success=True, **fields):
        result = await asyncio.wait_for(submit(directory, verb, **fields), 45)
        if result.get("ok") is not success:
            raise RuntimeError(f"{verb} returned {result.get('error', 'an unexpected response')}")
        return result

    def start_host(port):
        task = asyncio.create_task(service.host_game(work, campaign, "127.0.0.1", port, True, True,
            native_events=True, native_guest=True, native_zones=True, expanded_zones=True))
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

    async def travel(authority, leg):
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
        proof["routes"].append({**proof.pop("pending_route"), "arrival": placement})
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

    try:
        copy_checkpoint(baseline, campaign, data)
        shutil.copyfile(inputs["identity"], campaign / "save/host-identity.json")
        (run / "invites").mkdir()
        shutil.copyfile(inputs["invite"], run / "invites" / f"{guest_id}.invite.json")
        host_job = start_host(0)
        opening = await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == SCIENTIST_SHOP, timeout=55)
        port = opening["endpoint"]["port"]
        guest_job = await start_guest()
        authority = await paired(SCIENTIST_SHOP)
        for leg in ROUTE:
            authority = await travel(authority, leg)

        inactive = await wait(run, lambda state: (scene := state.get("game", {}).get(core.SCENE, {}))
                              and (scene["status"], scene["installed"]) == (0, False), timeout=20)
        proof["dormant"] = inactive["game"][core.SCENE]
        locked = await action(guest_run, core.UPLOAD, target=guid, success=False)
        assert locked["error"] == core.LOCKED, locked
        await action(run, "checkpoint")
        _, dormant_flags = await flags()
        assert tuple(row.get(core.FLAG, 0) for row in dormant_flags) == (0, 0), dormant_flags
        installed = (await action(run, fixture.INSTALL))[core.SCENE]
        _, mounted_flags = await flags()
        assert (installed["status"], installed["installed"]) == (1, True), installed
        assert tuple(row.get(core.FLAG, 0) for row in mounted_flags) == (1, 0), mounted_flags
        proof["installation"] = {"scene": installed, "headless_relocation": True, "stock_flags": [1, 0]}

        near = await action(run, fixture.APPROACH)
        wrong = await action(guest_run, core.UPLOAD, target=blank, success=False)
        assert wrong["error"] == "native_mindcore_refused_durable_results_owner", wrong
        ready = (await action(guest_run, core.PREFLIGHT, target=guid))[core.PROOF]
        assert (ready["status"], ready["pawn"], ready["guid"], ready["proof"]) == (1, near["pawn"], guid, "owned"), ready
        original_guest, original_host = await inventory(guest_run, character), await inventory(run, "host")
        assert (original_guest[guid]["class_path"], original_guest[guid]["quantity"], original_guest[guid]["source"]) == (
            DRILL.product, 1, "backpack"), original_guest[guid]
        assert guid not in original_host and blank not in original_guest
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
        assert (receipt["operation_id"], receipt["owner"], receipt["guid"]) == (operation_id, character, guid)
        assert await inventory(guest_run, character) == original_guest and await inventory(run, "host") == original_host
        proof["upload"] = {"locked": locked["error"], "wrong_guid": wrong["error"], "preflight": ready,
                           "receipt": receipt, "tape": original_guest[guid], "stock_flags": [2, 0]}

        await stop(guest_run, guest_job)
        await stop(run, host_job)
        start_host(port)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != authority["generation"], timeout=55)
        await start_guest()
        restarted = await paired(core.MAP, (authority["generation"], authority["zone_epoch"]))
        assert restarted["endpoint"]["port"] == port
        restored = await wait(run, lambda state: core._ready(state.get("game", {}), 2), timeout=25)
        replay = await action(guest_run, core.UPLOAD, target=guid, operation_id=operation_id)
        assert replay["effect"] == "native_mindcore_already_compiled", replay
        assert await inventory(guest_run, character) == original_guest and await inventory(run, "host") == original_host
        after, restored_flags = await flags()
        assert after["world"]["party"][core.LEDGER] == receipt
        assert tuple(row.get(core.FLAG, 0) for row in restored_flags) == (2, 0), restored_flags
        commits = [row for row in events() if row["event"] == "native_mindcore_results_committed"]
        assert len(commits) == 1 and commits[0]["operation_id"] == operation_id, commits
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
        proof["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        proof["baseline_verified"] = initial_inputs == {key: sha256(path.read_bytes()).hexdigest() for key, path in inputs.items()}
        isolated = proof["original_saves_verified"] and proof["baseline_verified"]
        proof["verified"] = isolated and proof.get("verified", False)
        runtime.write_json(run / REPORT, proof)
        assert isolated
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--vanilla-saved", required=True, type=Path)
    options = parser.parse_args()
    asyncio.run(exercise(*(value.resolve(strict=True) for value in (options.work, options.baseline, options.vanilla_saved))))
