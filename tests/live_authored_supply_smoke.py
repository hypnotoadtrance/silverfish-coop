"""Verify two stock .45 Ball pickups, exact owner saves, and stale zone refusal."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_ai_smoke import status, wait
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_authored_pickup as pickup
from tools.silverfish.authored_supply import PLACEMENTS
from tools.silverfish.control import submit
from tools.silverfish.native_gold import valid_guid
from tools.silverfish.travel import CITY, CROSSROADS, RAIL_YARD


CITY_AMMO, CROSSROADS_AMMO = PLACEMENTS
ROLES = ("guest", "guest2")


async def exercise(work: Path, vanilla_saved: Path) -> None:
    if not vanilla_saved.is_dir():
        raise ValueError("The original Saved directory is missing")
    original = saved_hashes(vanilla_saved)
    name = "stock-supplies-" + uuid.uuid4().hex[:8]
    run = work / "campaigns" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    campaign = ("--campaign", name)
    profiles = [("--profile", f"{name}-{i}") for i in range(1, 3)]
    clients = [work / "clients" / profile[1] / "run" for profile in profiles]
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones",
                  "--native-expanded-zones", "--max-guests", "2")
    launches, invites = [], []
    proof = {"routes": [], "claims": {}}

    async def local(directory, action, *, expected=True, **arguments):
        reply = await submit(directory, action, **arguments)
        assert reply.get("ok") is expected, (action, reply)
        return reply

    async def start(directory, label, *args):
        with (run / f"{label}.txt").open("wb") as log:
            child = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=log, stderr=log,
                                                         creationflags=subprocess.CREATE_NO_WINDOW)
        launches.append((directory, child))
        return child

    async def invite(index):
        reply = await asyncio.create_subprocess_exec(
            *cli, "invite", *campaign, "--name", f"Supply guest {index+1}", "--character", f"Collector {index+1}",
            cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output, errors = await asyncio.wait_for(reply.communicate(), 30)
        assert reply.returncode == 0, errors.decode(errors="replace")
        return json.loads(output)

    async def start_peer(index, issued, label):
        return await start(clients[index], label, "join", *profiles[index], "--invite", issued["invite_file"],
                           "--headless", "--native-zones")

    async def stop(directory, child):
        if child.returncode is None:
            await local(directory, "stop")
        assert await asyncio.wait_for(child.wait(), 18) == 0

    def ledger(state):
        return state.get("campaign", {}).get("world", {}).get("party", {}).get(pickup.STATE_KEY, {})

    def projected(state, placement, expected):
        source = state.get("game", {}).get(pickup.SOURCES_KEY, {}).get(placement.source, {})
        return tuple(source.get(field) for field in ("map", "actor", "class_path", "state")) == (
            placement.area, placement.actor, placement.ammo.item_path, expected)

    async def paired(area, after=None, *, count=2):
        def bound(state):
            game = state.get("game", {})
            saves = game.get("native_guest_save_members", {})
            roles = state.get("gameplay", {}).get("players", {})
            return (state.get("state") == "hosting" and state.get("zone_epoch") != after and game.get("map") == area
                    and not state.get("awaiting_zone_guest") and game.get("remote_clients") == count and len(saves) == count
                    and all(row["state"] == "bound" for row in saves.values())
                    and all(roles.get(role, {}).get("character_id") == issued["character_id"]
                            for role, issued in zip(ROLES, invites[:count])))

        host = await wait(run, bound, timeout=80)
        for directory, issue in zip(clients, invites[:count]):
            await wait(directory, lambda state: (state.get("state"), state.get("character_id"), state.get("host_session"),
                       state.get("zone_epoch"), state.get("game", {}).get("map")) ==
                       ("connected", issue["character_id"], host["generation"], host["zone_epoch"], area), timeout=75)
        return host

    async def inventory(directory, owner):
        page = (await local(directory, "native-inventory-list"))["native_inventory"]
        assert page["owner"] == owner and page["next_offset"] is None, page
        return {row["guid"]: {key: value for key, value in row.items() if key != "index"} for row in page["items"]}

    async def pickup_with_stock_f(placement, index, visit):
        source = placement.source
        directory = clients[index]
        await wait(run, lambda state: projected(state, placement, "available"))
        await wait(directory, lambda state: projected(state, placement, "available"))
        approach = await local(run, pickup.APPROACH, target=ROLES[index], pickup_source=source)
        assert approach.get("actor") == placement.actor, approach
        completed = time.time()

        def arrived(replica):
            host = status(run)
            pawns = host.get("game", {}).get("pawns", {})
            site = next((row.get("location") for row in pawns.values() if row.get("name") == approach["pawn"]), None)
            point = replica.get("game", {}).get("pawns", {}).get("local", {}).get("location")
            return (host.get("updated_unix", 0) >= completed and replica.get("updated_unix", 0) >= completed
                    and (replica.get("host_session"), host.get("zone_epoch"), replica.get("zone_epoch")) ==
                    (visit["generation"], visit["zone_epoch"], visit["zone_epoch"])
                    and projected(host, placement, "available") and projected(replica, placement, "available")
                    and site and point and math.dist(site, point) <= 25 and math.dist(point, placement.position) <= 350)

        state = await wait(directory, arrived)
        pressed = (await local(directory, pickup.STOCK_FIXTURE, pickup_source=source))["native_authored_pickup_interaction"]
        assert (pressed["source"], pressed["map"], pressed["entry"], pressed["pawn"]) == (
            source, placement.area, "stock", state["game"]["pawns"]["local"]["name"]), pressed
        saved = await wait(run, lambda state: source in ledger(state), timeout=55)
        receipt = ledger(saved)[source]
        assert (receipt["source"], receipt["map"], receipt["actor"], receipt["class_path"], receipt["owner"]) == (
            source, placement.area, placement.actor, placement.ammo.item_path, invites[index]["character_id"]), receipt
        assert valid_guid(receipt["guid"]), receipt
        for directory in (run, *clients):
            await wait(directory, lambda state: projected(state, placement, "claimed"))
        proof["claims"][source] = dict(stock_input=pressed, receipt=receipt)
        return receipt

    async def travel(prior, destination, entrance, actor):
        for role in ROLES:
            await local(run, "native-exit-position", target=role, exit_position="near", entrance=entrance, exit_actor=actor)
        await local(run, "native-exit-position", target="host", exit_position="inside", entrance=entrance, exit_actor=actor)
        arrived = await paired(destination, prior["zone_epoch"])
        assert arrived["generation"] == prior["generation"]
        proof["routes"].append(dict(destination=destination, entrance=entrance, epoch=arrived["zone_epoch"]))
        return arrived

    try:
        host = await start(run, "host", "host", *campaign, "--port", "0", "--start-zone", CITY, *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting")
        peers = []
        for index in range(2):
            issued = await invite(index)
            invites.append(issued)
            peers.append(await start_peer(index, issued, f"guest-{index+1}"))
            city = await paired(CITY, count=index + 1)
        party_members = [issued["character_id"] for issued in invites]
        host_items = await inventory(run, "host")
        baseline = [await inventory(directory, identity) for directory, identity in zip(clients, party_members)]
        source = CITY_AMMO.source
        await wait(run, lambda state: projected(state, CITY_AMMO, "available"))
        old = uuid.uuid4().hex
        assert old != city["zone_epoch"]
        stale = await local(clients[0], pickup.ACTION, expected=False, target=f"{source}@{old}")
        wrong_map = await local(clients[0], pickup.ACTION, expected=False, target=CROSSROADS_AMMO.source)
        await local(run, "native-exit-position", target=ROLES[0], exit_position="far", entrance="Rail_City",
                    exit_actor="Level_Exit_BP_C_1")
        too_far = await local(clients[0], pickup.ACTION, expected=False, target=source)
        assert (stale["error"], wrong_map["error"], too_far["error"]) == (
            "native_authored_pickup_stale_zone", "native_authored_pickup_unavailable",
            "authored_pickup_exact_authority_sight_required")
        assert await inventory(clients[0], party_members[0]) == baseline[0] and source not in ledger(status(run))
        proof["refusals"] = [stale["error"], wrong_map["error"], too_far["error"]]

        first = await pickup_with_stock_f(CITY_AMMO, 0, city)
        contested = await local(clients[1], pickup.ACTION, target=source, expected=False)
        assert contested["error"] == "native_authored_pickup_already_claimed", contested
        assert await inventory(clients[1], party_members[1]) == baseline[1]
        proof["contested"] = contested["error"]
        rail = await travel(city, RAIL_YARD, "Rail_City", "Level_Exit_BP_C_1")
        crossroads = await travel(rail, CROSSROADS, "Depot_Cross_Rail", "Level_Exit_BP_C_0")
        delayed = await local(clients[1], pickup.ACTION, expected=False,
                              target=f"{CROSSROADS_AMMO.source}@{city['zone_epoch']}")
        assert delayed["error"] == "native_authored_pickup_stale_zone", delayed
        second = await pickup_with_stock_f(CROSSROADS_AMMO, 1, crossroads)
        assert first["guid"] != second["guid"]
        owned = [await inventory(directory, identity) for directory, identity in zip(clients, party_members)]
        for index, (placement, receipt) in enumerate(zip(PLACEMENTS, (first, second), strict=True)):
            row = owned[index][receipt["guid"]]
            assert (row["class_path"], row["source"], row["quantity"], row["stackable"]) == (
                placement.ammo.item_path, "backpack", placement.units, True), row
            assert {guid: item for guid, item in owned[index].items() if guid != receipt["guid"]} == baseline[index]
        assert await inventory(run, "host") == host_items

        await local(run, "checkpoint")
        await stop(run, host)
        for directory, child in zip(clients, peers):
            await stop(directory, child)
        await start(run, "host-restart", "host", *campaign, "--port", str(crossroads["endpoint"]["port"]), *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != crossroads["generation"])
        for index, issue in enumerate(invites):
            renewed = await local(run, "refresh-invite", target=issue["guest_id"])
            assert (renewed["character_id"], renewed["guest_id"]) == (issue["character_id"], issue["guest_id"]), renewed
            await start_peer(index, renewed, f"guest-{index+1}-restart")
            restored = await paired(CROSSROADS, crossroads["zone_epoch"], count=index + 1)
        assert ledger(restored) == {first["source"]: first, second["source"]: second}
        for directory in (run, *clients):
            await wait(directory, lambda state: projected(state, CROSSROADS_AMMO, "claimed"))
        rail = await travel(restored, RAIL_YARD, "Depot_Cross_Rail", "Level_Exit_BP_C_1")
        await travel(rail, CITY, "Rail_City", "Level_Exit_BP_C_220")
        for directory in (run, *clients):
            await wait(directory, lambda state: projected(state, CITY_AMMO, "claimed"))
        assert [await inventory(directory, identity) for directory, identity in zip(clients, party_members)] == owned
        assert await inventory(run, "host") == host_items
        proof.update(verified=True, restored_inventory=owned, original_saves_unchanged=saved_hashes(vanilla_saved) == original)
    except BaseException as error:
        proof.update(verified=False, failure=str(error))
        raise
    finally:
        for directory, child in reversed(launches):
            if child.returncode is None:
                try:
                    await stop(directory, child)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if child.returncode is None:
                        child.terminate()
                        await child.wait()
        proof["original_saves_unchanged"] = saved_hashes(vanilla_saved) == original
        (run / "live-authored-supply-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        assert proof["original_saves_unchanged"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="prepared isolated stage workspace")
    parser.add_argument("--vanilla-saved", type=Path, required=True, help="original Saved directory to hash before and after")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve()))
