"""Verify two guests' stock standard SMG press, hold, release and recovery."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_ai_smoke import status, wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.silverfish.control import submit
from tools.silverfish.native_host_save import guest_record
from tools.silverfish.native_smg_hold import ALL, FINISHED, KEY, LOCAL, LOCAL_FIXTURE
from tools.silverfish.travel import OBSERVATORY


KIND = "smg-standard"
EQUIPPED = "native_guest_weapon"
FIXTURE = "native_guest_held_fire_fixture"
COMMIT = "native_guest_smg_hold_committed"
INPUT_EVENT = "native_guest_held_fire_input"


def events(directory, name):
    try:
        lines = (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("event") == name:
            found.append(row)
    return found


async def wait_event(directory, event, matches, *, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for row in reversed(events(directory, event)):
            if matches(row):
                return row
        if status(directory).get("state") == "failed":
            break
        await asyncio.sleep(0.1)
    raise RuntimeError(f"{directory} did not report {event}: {events(directory, event)[-6:]}")


async def verify(work: Path, originals_path: Path):
    name = "guest-held-fire-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    run = campaign / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    host = ("--campaign", name)
    roles = ("guest", "guest2")
    clients = [("--profile", f"{name}-{index}") for index in (1, 2)]
    client_runs = [work / "clients" / client[1] / "run" for client in clients]
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones",
                  "--native-guest-held-fire", "--max-guests", "2")
    client_flags = ("--headless", "--native-zones")
    paths_before = saved_hashes(originals_path)
    jobs = []
    evidence = {"guests": {}}

    async def cli_call(*arguments):
        process = await asyncio.create_subprocess_exec(
            *cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        raw, error = await asyncio.wait_for(process.communicate(), 25)
        if process.returncode or not raw:
            raise RuntimeError(f"{arguments}: {raw.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(raw)
        if reply.get("ok") is False:
            raise RuntimeError(f"{arguments}: {reply}")
        return reply

    async def launch(owner, label, *arguments):
        with (run / (label + ".txt")).open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, *arguments, cwd=REPO, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        jobs.append((owner, process))
        return process

    async def action(directory, name, **parameters):
        reply = await submit(directory, name, **parameters)
        if reply.get("ok") is not True:
            raise RuntimeError(f"{name}: {reply}")
        return reply

    def enrolled(state, count):
        game, gameplay = state.get("game", {}), state.get("gameplay", {})
        saves = game.get("native_guest_save_members", {})
        return (state.get("state") == "hosting" and game.get("map") == OBSERVATORY and game.get("remote_clients") == count
                and len(saves) == count and all(row.get("state") == "bound" for row in saves.values())
                and len(gameplay.get("native_pawns", {})) == count + 1)

    async def attach(index, invitation, stage):
        client = clients[index]
        process = await launch(client_runs[index], f"guest-{index + 1}-{stage}", "join", *client,
                               "--invite", invitation["invite_file"], *client_flags)
        await wait(client_runs[index], lambda s: s.get("state") == "connected" and s.get("game", {}).get("map") == OBSERVATORY)
        await wait(run, lambda state: enrolled(state, index + 1))
        return process

    async def stop(directory, process):
        if process.returncode is None:
            await action(directory, "stop")
        assert await asyncio.wait_for(process.wait(), 18) == 0, process.returncode

    async def reload_stock(index, guid, before, after):
        await action(run, "native-guest-weapon-seed-ammo", target=roles[index], item_guid=uuid.uuid4().hex.upper())
        input_proof = (await action(client_runs[index], "native-guest-reload-input-fixture"))["native_guest_reload_fixture"]
        receipt = await wait_event(client_runs[index], "native_guest_reload_input",
                                   lambda row: row.get("sequence") == input_proof["sequence"])
        loaded = receipt[EQUIPPED]
        assert (loaded["guid"], loaded["before"], loaded["after"]) == (guid, before, after), loaded
        return input_proof, loaded

    async def edge(index, kind):
        receipt = (await action(client_runs[index], LOCAL_FIXTURE, target=kind))[FIXTURE]
        expected = members[index]
        assert (receipt["map"], receipt["pawn"], receipt["edge"], receipt["guid"]) == (
            OBSERVATORY, expected["local_pawn"], kind, expected["guid"]
        ), receipt
        return receipt

    def hold(state, index):
        return state.get("game", {}).get(ALL, {}).get(members[index]["host_pawn"], {})

    async def committed(index, serial):
        member = members[index]
        row = await wait_event(run, COMMIT, lambda event: event.get("serial") == serial and event.get("guid") == member["guid"])
        assert (row["character_id"], row["pawn"], row["map"]) == (member["character_id"], member["host_pawn"], OBSERVATORY), row
        assert row["status"] in FINISHED and row["rounds"] == row["projectiles"] == row["before"] - row["after"] >= 1, row
        assert row["revision"] > 0, row
        return row

    async def relayed(index, fixture):
        row = await wait_event(client_runs[index], INPUT_EVENT, lambda event: (event.get("stroke"), event.get("edge")) == (
            fixture["stroke"], fixture["edge"]
        ))
        if fixture["edge"] == "press":
            assert row[KEY]["guid"] == fixture["guid"] and row[KEY]["pawn"] == members[index]["host_pawn"], row
        return row

    members = []
    try:
        authority = await launch(run, "host", "host", *host, "--port", "0", "--start-zone", OBSERVATORY, *host_flags)
        fresh = await wait(run, lambda state: enrolled(state, 0))
        guests = []
        for index in range(2):
            invitation = await cli_call("invite", *host, "--name", f"Held SMG tester {index + 1}",
                                        "--character", f"Gunsmith {index + 1}")
            guests.append(await attach(index, invitation, "initial"))
            members.append({"character_id": invitation["character_id"], "guest_id": invitation["guest_id"],
                            "guid": uuid.uuid4().hex.upper()})
        initial = await wait(run, lambda state: enrolled(state, 2))
        for index, member in enumerate(members):
            role, client_run, guid = roles[index], client_runs[index], member["guid"]
            member["host_pawn"] = initial["gameplay"]["native_pawns"][role]
            assert initial["gameplay"]["players"][role]["character_id"] == member["character_id"], initial
            seeded = (await action(run, "native-guest-weapon-seed", target=role, item_guid=guid, kind=KIND))[EQUIPPED]
            assert (seeded["guid"], seeded["pawn"], seeded["stored_ammo"]) == (guid, member["host_pawn"], 7), seeded
            equipped = (await action(client_run, "native-guest-weapon-equip", target=guid))[EQUIPPED]
            assert (equipped["guid"], equipped["actor_ammo"]) == (guid, 7), equipped
            before = 7
            member["reloads"] = []
            for after in (16, 25):
                fixture, verified = await reload_stock(index, guid, before, after)
                member["reloads"].append({"fixture": fixture, "stock": verified})
                before = after
            scope = (await wait(client_run, lambda state: state.get("game", {}).get("native_guest_primary_scope", {}).get(
                "guid") == guid))["game"]["native_guest_primary_scope"]
            assert scope["kind"] == KIND, scope
            member["local_pawn"] = scope["pawn"]
            evidence["guests"][role] = {key: value for key, value in member.items() if key != "guest_id"}
        assert len({member["host_pawn"] for member in members}) == 2
        for index, member in enumerate(members):
            await wait(client_runs[index], lambda state: state.get("gameplay_fresh") and
                       state.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == member["guid"])

        pressed = await asyncio.gather(edge(0, "press"), edge(1, "press"))
        evidence["press_fixtures"] = pressed
        simultaneous = await wait(run, lambda state: all(hold(state, index).get("status") == "active"
                                  and hold(state, index).get("rounds", 0) >= 1 for index in range(2)), timeout=4)
        ongoing = [hold(simultaneous, index) for index in range(2)]
        assert len({row["serial"] for row in ongoing}) == 2 and all(row["before"] == 25 for row in ongoing), ongoing
        evidence["overlapping_stock_holds"] = ongoing
        left_release = await edge(0, "release")
        assert left_release["stroke"] == pressed[0]["stroke"], left_release
        after_release = await wait(run, lambda state: hold(state, 0).get("status") == "released"
                                   and hold(state, 1).get("rounds", 0) > ongoing[1]["rounds"], timeout=5)
        right_after_left = hold(after_release, 1)
        assert right_after_left["status"] == "active", right_after_left
        right_release = await edge(1, "release")
        assert right_release["stroke"] == pressed[1]["stroke"], right_release
        first_ends = await asyncio.gather(*(committed(index, row["serial"]) for index, row in enumerate(ongoing)))
        assert first_ends[0]["status"] == "released" and first_ends[1]["rounds"] > right_after_left["rounds"], first_ends
        evidence["independent_terminal_frames"] = first_ends
        evidence["second_hold_continued_after_first_release"] = right_after_left
        evidence["relayed_press_release"] = [await relayed(index, fixture)
                                              for index, fixture in ((0, pressed[0]), (1, pressed[1]), (0, left_release))]
        if first_ends[1]["status"] == "released":
            evidence["relayed_press_release"].append(await relayed(1, right_release))

        right_guid = members[1]["guid"]
        count = first_ends[1]["after"]
        while count < 25:
            new = min(30, count + 9)
            await reload_stock(1, right_guid, count, new)
            count = new
        interrupted = await edge(1, "press")
        started = await wait(run, lambda state: hold(state, 1).get("status") == "active"
                             and hold(state, 1).get("serial") != ongoing[1]["serial"], timeout=4)
        interrupted_frame = hold(started, 1)
        dropped = await action(client_runs[1], "drop-link")
        assert dropped["link_connected"] is False, dropped
        ended = await committed(1, interrupted_frame["serial"])
        assert ended["status"] != "empty" and ended["before"] == count and ended["after"] > 0, ended
        await asyncio.sleep(0.75)
        stopped = status(run)
        assert hold(stopped, 1) == {key: ended[key] for key in interrupted_frame}, stopped
        left_stable = hold(stopped, 0)
        assert left_stable["rounds"] == first_ends[0]["rounds"], left_stable
        resumed = await action(client_runs[1], "reconnect")
        assert resumed["link_connected"] and resumed["resumed"], resumed
        await wait(client_runs[1], lambda state: state.get("gameplay_fresh") and LOCAL not in state.get("game", {}))
        await wait(run, lambda state: enrolled(state, 2))
        evidence["link_loss_stopped_and_committed"] = {"fixture": interrupted, "native": ended, "stable": True}
        expected_ammo = (first_ends[0]["after"], ended["after"])
        for index, ammunition in enumerate(expected_ammo):
            inspected = (await action(client_runs[index], "native-guest-weapon-inspect", target=members[index]["guid"]))[EQUIPPED]
            assert (inspected["stored_ammo"], inspected["actor_ammo"]) == (ammunition, ammunition), inspected

        await stop(run, authority)
        for index, process in enumerate(guests):
            await stop(client_runs[index], process)
        checkpoint = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        frames = [guest_record(campaign / "game-user", checkpoint["characters"][member["character_id"]]["state"]["native_guest_save"],
                               member["character_id"]) for member in members]
        assert all(frame["map"] == OBSERVATORY for frame in frames) and frames[0]["slot"] != frames[1]["slot"], frames
        evidence["independent_guest_save_frames"] = frames
        port = str(fresh["endpoint"]["port"])
        await launch(run, "host-restart", "host", *host, "--port", port, *host_flags)
        restarted = await wait(run, lambda state: enrolled(state, 0) and state.get("generation") != initial["generation"])
        assert restarted["endpoint"]["port"] == int(port), restarted
        restored = []
        for index, member in enumerate(members):
            renewed = await action(run, "refresh-invite", target=member["guest_id"])
            assert (renewed["guest_id"], renewed["character_id"]) == (member["guest_id"], member["character_id"]), renewed
            await attach(index, renewed, "restart")
            proof = (await action(client_runs[index], "native-guest-weapon-inspect", target=member["guid"]))[EQUIPPED]
            assert (proof["guid"], proof["stored_ammo"]) == (member["guid"], expected_ammo[index]), proof
            restored.append(proof)
        evidence.update(verified=True, same_port_restart={"port": int(port), "weapons": restored})
    except BaseException as error:
        evidence.update(verified=False, failure=f"{type(error).__name__}: {error}")
        raise
    finally:
        for directory, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(directory, process)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                    await asyncio.wait_for(process.wait(), 15)
        evidence["original_saves_verified"] = saved_hashes(originals_path) == paths_before
        evidence["original_file_count"] = len(paths_before)
        (run / "live-guest-held-fire-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "The original single-player files changed during the smoke"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve()))
