"""Verify real stock Interact dispatch for City Copper Wire and Paladin Brandy."""

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

from tools.silverfish.native_authored_pickup import (APPROACH, CITY_BRANDY, CITY_WIRE, SOURCES, SOURCES_KEY,
                                                     STATE_KEY, STOCK_FIXTURE)
from tools.silverfish.native_gold import valid_guid
from tools.silverfish.travel import CITY


CITY_POSITIONS = {
    CITY_WIRE: (1299.7166, 3418.7960, 1239.4920),
    CITY_BRANDY: (-571.3227, -8316.4123, -199.9999),
}


async def exercise(work: Path, vanilla_saved: Path | None) -> None:
    name = "stock-city-pickup-" + uuid.uuid4().hex[:8]
    campaign, profile = ("--campaign", name), ("--profile", name)
    host_run = work / "campaigns" / name / "run"
    guest_run = work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    guest_flags = ("--headless", "--native-zones")
    processes = []
    proof = {"sources": {}}

    async def command(*args, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, errors = await asyncio.wait_for(process.communicate(), 45)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if not output or (process.returncode == 0) != expected:
            raise RuntimeError(f"{args[:2]} request failed: {output.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(output)

    async def action(owner, operation, *args, expected=True):
        return await command("action", *owner, operation, *args, expected=expected)

    async def launch(kind, owner, label, *args):
        with (host_run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, kind, *owner, *args, cwd=REPO, stdout=log, stderr=log,
                                                             creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))
        return process

    async def stop(process, owner):
        if process.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(process.wait(), 18) == 0, process.returncode

    def game_source(state, source):
        return state.get("game", {}).get(SOURCES_KEY, {}).get(source, {})

    def ledger(state):
        return state.get("campaign", {}).get("world", {}).get("party", {}).get(STATE_KEY, {})

    def source_state(state, source, expected):
        row = game_source(state, source)
        return tuple(row.get(key) for key in ("map", "actor", "class_path", "state")) == (*SOURCES[source], expected)

    async def paired(character, previous=None):
        host = await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != previous
                          and state.get("game", {}).get("map") == CITY
                          and state["game"].get("native_guest_save", {}).get("state") == "bound"
                          and any(row["character_id"] == character and row["state"] == "active"
                                  for row in state.get("campaign", {}).get("leases", ())), timeout=60)
        guest = await wait(guest_run, lambda state: state.get("state") == "connected"
                           and state.get("link_connected") is True and state.get("character_id") == character
                           and state.get("host_session") == host["generation"] and state.get("zone_epoch") == host["zone_epoch"]
                           and state.get("game", {}).get("map") == CITY, timeout=60)
        return host, guest

    async def inventory(owner, character):
        rows, offset, total = {}, 0, None
        for _ in range(17):
            options = (("--amount" if owner == campaign else "--target"), str(offset)) if offset else ()
            page = (await action(owner, "native-inventory-list", *options))["native_inventory"]
            assert (page["owner"], page["offset"]) == (character, offset) and total in (None, page["total"]), page
            total = page["total"]
            for item in page["items"]:
                assert item["guid"] not in rows, item
                rows[item["guid"]] = {key: value for key, value in item.items() if key != "index"}
            offset = page["next_offset"]
            if offset is None:
                assert len(rows) == total, page
                return rows
            assert offset == len(rows), page
        raise AssertionError("The exact owned stock inventory census exceeded its page bound")

    async def press(source, character):
        assert (await action(campaign, APPROACH, "--target", "guest", "--pickup-source", source))["actor"] == SOURCES[source][1]

        def in_reach(state):
            position = state.get("game", {}).get("pawns", {}).get("local", {}).get("location")
            return (position and len(position) == 3 and all(type(value) in (int, float) for value in position)
                    and math.dist(position, CITY_POSITIONS[source]) < 238 and source_state(state, source, "available"))

        guest = await wait(guest_run, in_reach)
        receipt = (await action(profile, STOCK_FIXTURE, "--pickup-source", source))["native_authored_pickup_interaction"]
        assert tuple(receipt[key] for key in ("source", "map", "entry", "pawn")) == (
            source, CITY, "stock", guest["game"]["pawns"]["local"]["name"],
        ), receipt
        assert type(receipt["sequence"]) is int and receipt["sequence"] > 0, receipt
        commit = await wait(host_run, lambda state: source in ledger(state))
        row = ledger(commit)[source]
        assert tuple(row[key] for key in ("map", "actor", "class_path", "owner")) == (*SOURCES[source], character), row
        assert row["source"] == source and valid_guid(row["guid"]), row
        await wait(guest_run, lambda state: source_state(state, source, "claimed"))
        events = (await command("events", *profile, "--tail", "40"))["events"]
        routed = [event for event in events if event["event"] == "local_interaction"
                  and event.get("native_authored_pickup_claim") == row]
        assert len(routed) == 1, events
        proof["sources"][source] = {"stock_press": receipt, "committed": row, "operation": routed[0]["operation_id"]}

    try:
        host = await launch("host", campaign, "host", "--port", "0", "--start-zone", CITY, *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == CITY)
        invite = await command("invite", *campaign, "--name", "City pickup guest", "--character", "Courier")
        character = invite["character_id"]
        guest = await launch("join", profile, "guest", "--invite", invite["invite_file"], *guest_flags)
        _, initial = await paired(character)
        await wait(guest_run, lambda state: all(source_state(state, source, "available") for source in CITY_POSITIONS))
        unspecified = await action(profile, STOCK_FIXTURE, expected=False)
        assert unspecified["error"] == "authored_pickup_exact_source_required", unspecified

        await press(CITY_WIRE, character)
        await wait(guest_run, lambda state: source_state(state, CITY_BRANDY, "available"))
        contested = await action(campaign, "native-authored-pickup-claim", "--target", CITY_WIRE, expected=False)
        assert contested["error"] == "native_authored_pickup_already_claimed", contested
        proof["contested"] = contested["error"]
        await press(CITY_BRANDY, character)

        claims = {source: values["committed"] for source, values in proof["sources"].items()}
        presses = [values["stock_press"]["sequence"] for values in proof["sources"].values()]
        assert presses[0] < presses[1] and len({claim["guid"] for claim in claims.values()}) == 2, proof
        assert len({values["operation"] for values in proof["sources"].values()}) == 2, proof
        visitor_items, host_items = await inventory(profile, character), await inventory(campaign, "host")
        for row in claims.values():
            item = visitor_items[row["guid"]]
            assert (item["class_path"], item["source"], item["quantity"]) == (row["class_path"], "backpack", 1), item
            assert row["guid"] not in host_items, host_items
        await action(campaign, "checkpoint")
        await stop(host, campaign)
        await stop(guest, profile)

        await launch("host", campaign, "host-restart", "--port", "0", *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != initial["host_session"])
        renewed = await action(campaign, "refresh-invite", "--target", invite["guest_id"])
        assert (renewed["guest_id"], renewed["character_id"]) == (invite["guest_id"], character), renewed
        await launch("join", profile, "guest-restart", "--invite", renewed["invite_file"], *guest_flags)
        restored, _ = await paired(character, initial["host_session"])
        assert ledger(restored) == claims, ledger(restored)
        assert await inventory(profile, character) == visitor_items
        assert await inventory(campaign, "host") == host_items
        for directory in (host_run, guest_run):
            await wait(directory, lambda state: all(source_state(state, source, "claimed") for source in CITY_POSITIONS))
        proof.update(restart={"generation": restored["generation"], "owner": character, "sources": list(claims)}, verified=True)
    except BaseException as error:
        proof.update(verified=False, failure=str(error))
        raise
    finally:
        try:
            for process, owner in reversed(processes):
                if process.returncode is None:
                    try:
                        await stop(process, owner)
                    except (OSError, RuntimeError, TimeoutError, AssertionError):
                        if process.returncode is None:
                            process.terminate()
                            await asyncio.wait_for(process.wait(), 12)
        finally:
            if original is not None:
                proof["original_saves_verified"] = saved_hashes(vanilla_saved) == original
            (host_run / "live-authored-pickup-input-verification.json").write_text(json.dumps(proof, indent=2) + "\n",
                                                                                  encoding="utf-8")
            print(json.dumps(proof), flush=True)
        if original is not None:
            assert proof["original_saves_verified"], "The original single-player Saved directory changed during the run"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="prepared isolated stage workspace")
    parser.add_argument("--vanilla-saved", type=Path, help="original Saved directory to hash before and after")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved))
