"""Exercise Flood Control through stock board, valve and party travel inputs."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_computer_board_smoke import stock_flags
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_host_save, native_subway_valve as valve
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.travel import BOROUGH, CITY


async def verify(work: Path, vanilla_saved: Path, *, deferred_save: bool = False):
    name = "subway-valve-" + uuid.uuid4().hex[:8]
    host, guest = ("--campaign", name), ("--profile", name)
    campaign = work / "campaigns" / name
    run, guest_run = campaign / "run", work / "clients" / name / "run"
    original = saved_hashes(vanilla_saved)
    if not original:
        raise ValueError("Supply the original single-player Saved directory")
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    guest_flags = ("--headless", "--native-zones")
    evidence, jobs = {"routes": [], "verified": False}, []
    if deferred_save:
        evidence["deferred_save"] = {}

    async def command(*args, accepted=True):
        proc = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, errors = await asyncio.wait_for(proc.communicate(), 45)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if not output or (proc.returncode == 0) != accepted:
            raise RuntimeError(f"{args[:4]}: {output.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(output)

    async def action(owner, verb, *args, accepted=True):
        return await command("action", *owner, verb, *args, accepted=accepted)

    async def launch(owner, label, *args):
        with (run / f"{label}.txt").open("wb") as log:
            proc = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=log, stderr=log,
                                                        creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, proc))
        return proc

    async def stop(owner, proc):
        if proc.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(proc.wait(), 20) == 0, proc.returncode

    async def paired(area, prior=None):
        authority = await wait(run, lambda frame: frame.get("state") == "hosting" and frame.get("link_connected") is True
                               and frame.get("game", {}).get("map") == area
                               and frame["game"].get("native_guest_save", {}).get("state") == "bound"
                               and (frame.get("generation"), frame.get("zone_epoch")) != prior, timeout=70)
        await wait(guest_run, lambda frame: frame.get("state") == "connected" and frame.get("game", {}).get("map") == area
                   and frame.get("character_id") == character and frame.get("host_session") == authority["generation"]
                   and frame.get("zone_epoch") == authority["zone_epoch"], timeout=70)
        return authority

    async def cross(frame, destination, entrance):
        source = frame["game"]["map"]
        (route,) = [r for r in AUTHORED_ROUTES if (r.source, r.destination, r.entrance) == (source, destination, entrance)]
        evidence["pending_route"] = [source, destination, entrance, route.actor]
        options = ("--entrance", entrance)
        if route.kind == "door":
            for role in ("host", "guest"):
                await action(host, "native-door-position", "--target", role, "--exit-position", "near", *options)
            await action(host, "native-door-probe", "--target", "host", *options)
        else:
            options += ("--exit-actor", route.actor)
            for role in ("host", "guest"):
                await action(host, "native-exit-position", "--target", role, "--exit-position", "near", *options)
            await action(host, "native-exit-position", "--target", "host", "--exit-position", "inside", *options)
        arrived = await paired(destination, (frame["generation"], frame["zone_epoch"]))
        evidence["routes"].append(evidence.pop("pending_route"))
        return arrived

    async def board_state(*states, selected=True):
        def qualified(frame):
            game = frame.get("game", {})
            row = game.get("native_wire_board", {})
            return (game.get("state") == "connected" and game.get("map") == BOROUGH
                    and row.get("board") == valve.BOARD and row.get("state") in states
                    and row.get("pawn") == game.get("pawns", {}).get("local", {}).get("name")
                    and (not selected or row.get("quest") == valve.QUEST))

        await wait(guest_run, qualified)

    async def board_press(expected):
        proof = (await action(guest, "native-wire-board-fixture", "--target", valve.QUEST))["native_wire_board"]
        assert (proof["board"], proof["quest"], proof["state"]) == (valve.BOARD, valve.QUEST, expected), proof
        evidence.setdefault("stock_board", []).append(proof)

    async def committed(step):
        frame = await wait(run, lambda status: status.get("gameplay", {}).get("native_quests", {}).get(valve.QUEST, {}).get("step") == step)
        return frame["gameplay"]["native_quests"][valve.QUEST]

    async def inventory(owner, ids, *, classes=()):
        matched, offset = {}, 0
        while offset is not None:
            options = ("--amount", str(offset)) if owner == host else ("--target", str(offset))
            page = (await action(owner, "native-inventory-list", *options))["native_inventory"]
            for row in page["items"]:
                guid = row["guid"]
                if guid in ids or row["class_path"] in classes:
                    assert guid not in matched, row
                    matched[guid] = row
            offset = page["next_offset"]
        return matched

    def disk_checkpoint():
        return json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))

    def save_snapshot():
        saved = disk_checkpoint()
        quest = saved["world"].get("party", {}).get("native_quests", {}).get(valve.QUEST, {})
        return {
            "host": saved_hashes(campaign / "game-user/Saved/SaveGames"),
            "guest": saved_hashes(guest_run.parent / "game-user/Saved/SaveGames"),
            "host_generation": saved["world"].get("native_host_save"),
            "guest_generation": saved["characters"][character]["state"].get("native_guest_save"),
            "durable_step": quest.get("step"),
        }

    def unchanged_save(operation, before):
        after = save_snapshot()
        evidence["deferred_save"][operation] = {"before": before, "after": after}
        assert before["host"] and before["host_generation"] and before == after, (operation, before, after)

    async def balances(area, *, checkpoint=True, departure=None):
        if checkpoint:
            await action(host, "checkpoint")
        saved = disk_checkpoint()
        frames = [saved["world"]["native_host_save"], saved["characters"][character]["state"]["native_guest_save"]]
        assert frames[0]["slot"] != frames[1]["slot"] and frames[1]["character_id"] == character, frames
        # Travel can carry the guest's verified departure generation into the destination.
        guest_area = frames[1]["map"]
        assert guest_area in (area, departure), frames[1]
        root = native_host_save.directory(campaign / "game-user")
        flags = ("SubValve_Closed", "Player_Cash", "Player_Xp", "Rep_Commune", "Rep_State", "Rep_Coup", "Adv_Commune")
        return [{name: values.get(name, 0) for name in flags}
                for values in (stock_flags(root, frames[0], area), stock_flags(root, frames[1], guest_area))]

    try:
        host_proc = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", BOROUGH, *host_flags)
        first = await wait(run, lambda frame: frame.get("state") == "hosting" and frame.get("game", {}).get("map") == BOROUGH)
        await action(host, valve.APPROACH)
        evidence["stock_eligibility"] = (await action(host, valve.XP_FIXTURE))[valve.BOARD_KEY]
        invite = await command("invite", *host, "--name", "Flood Control", "--character", "Inspector")
        character = invite["character_id"]
        guest_proc = await launch(guest, "guest", "join", *guest, "--invite", invite["invite_file"], *guest_flags)
        current = await paired(BOROUGH)
        await action(host, valve.APPROACH, "--target", "guest")
        await board_state("armed", "focused", selected=False)
        await board_press("loading")
        await board_state("offer")
        if deferred_save:
            before_acceptance = save_snapshot()
        await board_press("starting")
        assert (await committed(1))["actor"] == character
        refused = await action(guest, "native-quest-turnin", "--target", valve.QUEST, accepted=False)
        assert refused["error"] == "stock_subway_valve_quest_objective_not_complete", refused
        if deferred_save:
            unchanged_save("acceptance", before_acceptance)
            assert before_acceptance["durable_step"] is None, before_acceptance
        else:
            initial = await balances(BOROUGH)

        current = await cross(current, CITY, "City_Borough")
        if deferred_save:
            initial = await balances(CITY, checkpoint=False, departure=BOROUGH)
            assert save_snapshot()["durable_step"] == 1
        evidence["baseline"] = initial
        current = await cross(current, valve.SUBWAY, "Subway")
        await wait(run, lambda frame: frame.get("game", {}).get(valve.SCENE, {}).get("state") == "present")
        await wait(guest_run, lambda frame: frame.get("game", {}).get(valve.SCENE, {}).get("state") == "present")
        refused = await action(guest, valve.ACTIVATE, "--target", uuid.uuid4().hex, accepted=False)
        assert refused["error"] == "stock_subway_valve_stale_visit", refused
        aim = (await action(host, valve.POSITION))[valve.FIXTURE_KEY]
        if deferred_save:
            before_valve = save_snapshot()
        fire = (await action(host, valve.PRESS))[valve.FIXTURE_KEY]
        assert (aim["pawn"], fire["map"], fire["state"]) == (fire["pawn"], valve.SUBWAY, "stock_input"), (aim, fire)
        objective = await committed(2)
        assert (objective["actor"], objective["objective"]["pawn"], objective["objective"]["epoch"]) == (
            "host", fire["pawn"], current["zone_epoch"]), objective
        replica = await wait(guest_run, lambda frame: (scene := frame.get("game", {}).get(valve.SCENE, {}))
                             and scene.get("state") == "closed" and scene.get("epoch") == current["zone_epoch"])
        if not deferred_save:
            live = await balances(valve.SUBWAY)
        replay = await action(guest, valve.ACTIVATE, "--target", current["zone_epoch"])
        assert replay["effect"] == "native_subway_valve_already_closed", replay
        evidence.update(stock_valve_input=fire, authority_objective=objective, replica=replica["game"][valve.SCENE])
        if deferred_save:
            unchanged_save("valve_and_replay", before_valve)
            assert before_valve["durable_step"] == 1, before_valve

        current = await cross(current, CITY, "Subway")
        if deferred_save:
            live = await balances(CITY, checkpoint=False, departure=valve.SUBWAY)
            assert save_snapshot()["durable_step"] == 2
        assert live[0] == dict(initial[0], SubValve_Closed=1, Rep_Commune=initial[0]["Rep_Commune"]+20,
                               Adv_Commune=initial[0]["Adv_Commune"]+200), live
        assert live[1] == initial[1], live
        evidence["objective_flags"] = live
        current = await cross(current, BOROUGH, "City_Borough")
        await action(host, valve.APPROACH, "--target", "guest")
        await board_state("armed", "focused", selected=False)
        await board_press("loading")
        await board_state("ready")
        quote = (await action(guest, valve.PREFLIGHT))[valve.BOARD_KEY]
        members = ((host, "host"), (guest, character))
        reward_classes = (valve.ARTIFACT, valve.HELMET) if deferred_save else ()
        if deferred_save:
            existing_rewards = {member: await inventory(owner, (), classes=reward_classes) for owner, member in members}
            before_turn_in = save_snapshot()
        await board_press("redeeming")
        result = await committed(-10)
        assert (result["actor"], result["xp"], result["cash"], set(result["recipients"])) == (character, 1, 0, {"host", character}), result
        ids = result["rewards"]["host"] + result["rewards"][character]
        assert len(ids) == len(set(ids)) == 4, result
        rewarded = {}
        for owner, member in members:
            items = await inventory(owner, ids, classes=reward_classes)
            if deferred_save:
                assert set(items) - set(existing_rewards[member]) == set(result["rewards"][member]), items
                rewarded[member] = items
                items = {guid: row for guid, row in items.items() if guid in ids}
            assert set(items) == set(result["rewards"][member]), items
            assert [items[guid]["class_path"] for guid in result["rewards"][member]] == [valve.ARTIFACT, valve.HELMET], items
            assert all(row["source"] == "backpack" and row["quantity"] == 1 for row in items.values()), items
        if deferred_save:
            replay = await action(guest, "native-quest-turnin", "--target", valve.QUEST)
            assert replay["effect"] == "native_quest_already_settled", replay
            for owner, member in members:
                assert await inventory(owner, ids, classes=reward_classes) == rewarded[member]
            unchanged_save("turn_in_and_replay", before_turn_in)
            assert before_turn_in["durable_step"] == 2, before_turn_in
            evidence["deferred_save"]["replay_before_checkpoint"] = replay
            await stop(host, host_proc)
            settled = save_snapshot()
            evidence["deferred_save"]["normal_save"] = settled
            assert settled["durable_step"] == -10, settled
            for frame in ("host_generation", "guest_generation"):
                assert settled[frame] and settled[frame] != before_turn_in[frame], settled
            saved_party = disk_checkpoint()["world"]["party"]
            assert valve.PENDING not in saved_party, saved_party[valve.PENDING]
            assert all(saved_party["players"][member]["quest_xp"] == 1 for _, member in members)
            after = await balances(BOROUGH, checkpoint=False)
        else:
            after = await balances(BOROUGH)
        assert after[0] == dict(live[0], Player_Xp=live[0]["Player_Xp"]+1,
                               Rep_State=quote["reputation"]["4"]["before"]-40, Rep_Coup=quote["reputation"]["5"]["before"]-50,
                               Rep_Commune=quote["reputation"]["2"]["before"]+150), after
        assert after[1] == dict(live[1], Player_Xp=live[1]["Player_Xp"]+1), after
        evidence.update(stock_turn_in=result, final_flags=after)

        if not deferred_save:
            await stop(host, host_proc)
        await stop(guest, guest_proc)
        await launch(host, "host-restart", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
        await wait(run, lambda frame: frame.get("state") == "hosting" and frame.get("generation") != first["generation"])
        renewed = await action(host, "refresh-invite", "--target", invite["guest_id"])
        await launch(guest, "guest-restart", "join", *guest, "--invite", renewed["invite_file"], *guest_flags)
        await paired(BOROUGH)
        assert await balances(BOROUGH, checkpoint=not deferred_save) == after
        if deferred_save:
            def typed_rows(rows):
                return {guid: (row["class_path"], row["source"], row["quantity"]) for guid, row in rows.items()}

            restored = {member: typed_rows(await inventory(owner, ids, classes=reward_classes)) for owner, member in members}
            assert restored == {member: typed_rows(rows) for member, rows in rewarded.items()}, restored
            evidence["deferred_save"]["restored_inventory"] = restored
            await action(host, valve.APPROACH, "--target", "guest")
            reopened = (await action(guest, valve.PREFLIGHT))[valve.BOARD_KEY]
            assert (reopened["step_before"], reopened["step_after"], reopened["flag"]) == (-10, -10, 1), reopened
            evidence["deferred_save"]["restored_stock_quest"] = reopened
        else:
            assert set(await inventory(guest, ids)) == set(result["rewards"][character])
        assert (await action(guest, "native-quest-turnin", "--target", valve.QUEST))["effect"] == "native_quest_already_settled"
        evidence["verified"] = True
    except BaseException as error:
        evidence["failure"] = str(error)
        raise
    finally:
        for owner, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(owner, process)
                except (OSError, TimeoutError, RuntimeError, AssertionError):
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
        evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        (run / "live-subway-valve-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "Original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--deferred-save", action="store_true", help="verify stock interactions leave save files untouched until a save boundary")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve(), deferred_save=args.deferred_save))
