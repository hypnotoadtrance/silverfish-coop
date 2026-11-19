"""Verify Mama's stock dialogue, exact Copper Wire barter and paired restart."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_computer_board_smoke import stock_flags
from live_guest_held_fire_smoke import wait_event
from live_multi_smoke import saved_hashes
from live_wire_quest_smoke import guest_role

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.silverfish import native_authored_pickup as pickup, native_borough_barter as barter
from tools.silverfish import native_dialogue as dialogue, native_host_save
from tools.silverfish.control import submit
from tools.silverfish.travel import BOROUGH, CITY


async def verify(work: Path, original: Path):
    name = "borough-barter-" + uuid.uuid4().hex[:8]
    root = work / "campaigns" / name
    host_run = root / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    if not original.is_dir():
        raise ValueError(f"The original Saved directory does not exist: {original}")
    originals = saved_hashes(original)
    host_args = ("--campaign", name)
    profiles = (name + "-1", name + "-2")
    runs = [work / "clients" / profile / "run" for profile in profiles]
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones",
                  "--native-expanded-zones", "--max-guests", "2")
    client_flags = ("--headless", "--native-zones")
    jobs, evidence = [], {}

    async def launch(directory, label, *args):
        with (host_run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, *args, cwd=REPO, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        jobs.append((directory, process))
        return process

    async def invite(index):
        process = await asyncio.create_subprocess_exec(
            *cli, "invite", *host_args, "--name", f"Borough visitor {index + 1}", "--character", f"Trader {index + 1}",
            cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        result, error = await asyncio.wait_for(process.communicate(), 20)
        if process.returncode or not result:
            raise RuntimeError(error.decode(errors="replace"))
        return json.loads(result)

    async def action(directory, verb, *, expected=True, **arguments):
        reply = await submit(directory, verb, **arguments)
        if reply.get("ok") is not expected:
            raise RuntimeError(f"{verb}: {reply}")
        return reply

    async def stop(directory, process):
        if process.returncode is None:
            await action(directory, "stop")
        assert await asyncio.wait_for(process.wait(), 18) == 0, process.returncode

    async def paired(area, members, previous_epoch=None, previous_generation=None):
        def enrolled(state):
            game, progress = state.get("game", {}), state.get("gameplay", {})
            frames = game.get("native_guest_save_members", {})
            pawns = progress.get("native_pawns", {})
            return (state.get("state") == "hosting" and state.get("generation") != previous_generation
                    and state.get("zone_epoch") != previous_epoch and game.get("map") == area
                    and game.get("remote_clients") == len(members)
                    and all(frames.get(pawns.get(guest_role(state, member)), {}).get("state") == "bound" for member in members))
        host = await wait(host_run, enrolled, timeout=75)
        guests = [await wait(runs[index], lambda state, member=member: state.get("state") == "connected"
                            and (state.get("host_session"), state.get("zone_epoch"), state.get("character_id"),
                                 state.get("game", {}).get("map")) == (host["generation"], host["zone_epoch"], member, area),
                            timeout=75) for index, member in enumerate(members)]
        return host, guests

    async def open_dialogue(directory, generation, expected_row, effect):
        await wait(directory, lambda s: s.get("game", {}).get("native_dialogue_view", {}).get("state") == "armed")
        return await press(directory, generation, "open", expected_row, effect)

    async def press(directory, generation, choice, expected_row, effect=None):
        shot = (await action(directory, dialogue.FIXTURE, target=choice))["native_dialogue_view"]
        event = await wait_event(directory, "native_dialogue_interaction",
                                 lambda row: row.get("generation") == generation and row.get("sequence") == shot["sequence"])
        assert (event.get("profile"), event.get("row"), event.get("effect")) == (dialogue.MAMA, expected_row, effect), event
        return event

    async def inventory(directory, character):
        rows, offset = {}, 0
        for _ in range(17):
            args = {"amount" if directory == host_run else "target": str(offset)} if offset else {}
            view = (await action(directory, "native-inventory-list", **args))["native_inventory"]
            assert (view["owner"], view["offset"]) == (character, offset), view
            for row in view["items"]:
                assert row["guid"] not in rows, row
                rows[row["guid"]] = {key: value for key, value in row.items() if key != "index"}
            offset = view["next_offset"]
            if offset is None:
                assert len(rows) == view["total"], view
                return rows
        raise RuntimeError(f"Inventory was over the page limit for {character}")

    try:
        host = await launch(host_run, "host", "host", *host_args, "--port", "0", "--start-zone", CITY, *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting")
        invitations, members, clients = [], [], []
        for index, profile in enumerate(profiles):
            invitation = await invite(index)
            invitations.append(invitation)
            members.append(invitation["character_id"])
            clients.append(await launch(runs[index], f"guest-{index + 1}", "join", "--profile", profile,
                                        "--invite", invitation["invite_file"], *client_flags))
            await paired(CITY, members)

        host_city, _ = await paired(CITY, members)
        first_role = guest_role(host_city, members[0])
        prepared = await action(host_run, pickup.APPROACH, target=first_role, pickup_source=pickup.CITY_WIRE)
        assert prepared["actor"] == pickup.SOURCES[pickup.CITY_WIRE][1], prepared
        raw = (await action(runs[0], pickup.STOCK_FIXTURE, pickup_source=pickup.CITY_WIRE))["native_authored_pickup_interaction"]
        assert (raw["entry"], raw["source"], raw["map"]) == ("stock", pickup.CITY_WIRE, CITY), raw
        acquired = await wait(host_run, lambda state: pickup.CITY_WIRE in state.get(
            "campaign", {}).get("world", {}).get("party", {}).get(pickup.STATE_KEY, {}))
        claim = acquired["campaign"]["world"]["party"][pickup.STATE_KEY][pickup.CITY_WIRE]
        assert (claim["owner"], claim["class_path"]) == (members[0], barter.COPPER), claim
        copper = claim["guid"]
        evidence["stock_pickup"] = {"input": raw, "claim": claim}

        for role in ("host", *(guest_role(host_city, member) for member in members)):
            await action(host_run, "native-exit-position", target=role, exit_position="near", entrance="City_Borough")
        await action(host_run, "native-exit-position", target="host", exit_position="inside", entrance="City_Borough")
        entered, guest_states = await paired(BOROUGH, members, previous_epoch=host_city["zone_epoch"])
        assert entered["generation"] == host_city["generation"], entered
        before = [await inventory(directory, member) for directory, member in zip(runs, members)]
        host_before = await inventory(host_run, "host")
        assert copper in before[0] and copper not in before[1] and copper not in host_before, before

        refusal = await action(runs[0], barter.ACTION, target=copper, expected=False)
        assert refusal["error"] == "borough_barter_refused_trade_dialogue_expired", refusal
        await action(host_run, dialogue.APPROACH, target=guest_role(entered, members[1]))
        second_gen = guest_states[1]["generation"]
        await open_dialogue(runs[1], second_gen, dialogue.MAMA_FIRST, "opened-first")
        await press(runs[1], second_gen, "3", dialogue.MAMA_QUESTIONS)
        await press(runs[1], second_gen, "1", dialogue.MAMA_ROOT)
        await press(runs[1], second_gen, "5", None, "trade-granted")
        blocked = await action(runs[1], barter.ACTION, target=copper, expected=False)
        assert blocked["error"] == "borough_barter_refused_exact_owned_copper_wire", blocked

        await action(host_run, dialogue.APPROACH, target=guest_role(entered, members[0]))
        first_gen = guest_states[0]["generation"]
        opened = await open_dialogue(runs[0], first_gen, dialogue.MAMA_ROOT, "opened-repeat")
        offered = await press(runs[0], first_gen, "5", None, "trade-granted")
        operation = uuid.uuid4().hex
        result = (await action(runs[0], barter.ACTION, target=copper, operation_id=operation))["native_borough_barter"]
        reward = result["receive"]
        assert result["give"] == {"guid": copper, "class_path": barter.COPPER, "quantity": 1}, result
        assert (reward["class_path"], reward["quantity"], result["reputation_change"]) == (barter.AMMO, 25, 0), result
        assert result["wallet_before"] == result["wallet_after"], result
        assert (await action(runs[0], barter.ACTION, target=copper, operation_id=operation))["native_borough_barter"] == result
        after = [await inventory(directory, member) for directory, member in zip(runs, members)]
        ammo_row = after[0][reward["guid"]]
        assert (ammo_row["class_path"], ammo_row["quantity"], ammo_row["source"]) == (barter.AMMO, 25, "backpack"), ammo_row
        assert after[0] == {**{guid: row for guid, row in before[0].items() if guid != copper}, reward["guid"]: ammo_row}, after
        assert after[1] == before[1] and await inventory(host_run, "host") == host_before
        evidence.update(route={"source": CITY, "destination": BOROUGH, "entrance": "City_Borough"},
                        dialogue={"open": opened, "offer": offered}, rejected_other=blocked["error"], result=result)

        await action(host_run, "checkpoint")
        for directory, process in zip(runs, clients):
            await stop(directory, process)
        await stop(host_run, host)
        checkpoint = json.loads((root / "save" / "campaign.json").read_text(encoding="utf-8"))
        profile = root / "game-user"
        host_frame = native_host_save.verify(profile, checkpoint["world"])
        guest_frame = native_host_save.guest_record(profile, checkpoint["characters"][members[0]]["state"]["native_guest_save"], members[0])
        assert host_frame["slot"] != guest_frame["slot"], (host_frame, guest_frame)
        flags = stock_flags(native_host_save.directory(profile), host_frame, BOROUGH)
        assert (flags.get("Met_Mamma"), flags.get("Player_Cash", 0)) == (1, result["wallet_after"]), flags
        await launch(host_run, "restart-host", "host", *host_args, "--port", str(entered["endpoint"]["port"]), *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != entered["generation"])
        for index, invitation in enumerate(invitations):
            refreshed = await action(host_run, "refresh-invite", target=invitation["guest_id"])
            assert refreshed["character_id"] == members[index], refreshed
            await launch(runs[index], f"restart-guest-{index + 1}", "join", "--profile", profiles[index],
                         "--invite", refreshed["invite_file"], *client_flags)
            await paired(BOROUGH, members[:index + 1], previous_generation=entered["generation"])
        assert [await inventory(directory, member) for directory, member in zip(runs, members)] == after
        assert await inventory(host_run, "host") == host_before
        assert (await action(runs[0], barter.ACTION, target=copper, operation_id=operation))["native_borough_barter"] == result
        evidence.update(verified=True, restart={"guest_frame": guest_frame, "host_meet": flags["Met_Mamma"], "ammo": ammo_row})
    except BaseException as error:
        evidence.update(verified=False, failure=f"{type(error).__name__}: {error}")
        raise
    finally:
        for directory, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(directory, process)
                except (RuntimeError, AssertionError, OSError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 15)
        evidence["original_saves_verified"] = saved_hashes(original) == originals
        evidence["original_file_count"] = len(originals)
        (host_run / "live-borough-barter-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "The original single-player files changed during the smoke"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve()))
