"""Verify the Lab E206 blank and the Scientist's stock Drill Test Results exchange."""

import argparse
import asyncio
from hashlib import sha256
import json
import math
from pathlib import Path
import shutil
import sys
import uuid

from live_ai_smoke import wait
from live_computer_board_smoke import stock_flags
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_authored_pickup as pickup, native_host_save, runtime, service
from tools.silverfish import native_mosby_station_view as station
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import submit
from tools.silverfish.native_gold import valid_guid
from tools.silverfish.native_tape_profiles import BLANK_CLASS, DRILL, DRILL_QUEST, drill_download_unlocked
from tools.silverfish.travel import LAB_E206, RAIL_YARD, RUIN_FIELDS, SCIENTIST_SHOP


SOURCE = pickup.LAB_E206_BLANK_TAPE
BLANK_LOCATION = (9789.3119, 465.9586, 677.8869)
REPORT = "live-drill-test-results-verification.json"
ROUTE = (
    (SCIENTIST_SHOP, RUIN_FIELDS, "Market_Flood", "door", "Dungeon_TransitionDoor_BP_C_2"),
    (RUIN_FIELDS, RAIL_YARD, "Ry_Rf", "overlap", "Level_Exit_BP_C_2"),
    (RAIL_YARD, LAB_E206, "RY_Lab206", "door", "Dungeon_TransitionDoor_BP_C_2"),
    (LAB_E206, RAIL_YARD, "RY_Lab206", "door", "Dungeon_TransitionDoor_BP_C_1"),
    (RAIL_YARD, RUIN_FIELDS, "Ry_Rf", "overlap", "Level_Exit_BP_C_221"),
    (RUIN_FIELDS, SCIENTIST_SHOP, "Market_Flood", "door", "Dungeon_TransitionDoor_BP_C_2"),
)


def baseline_snapshot(work: Path, baseline: Path) -> dict:
    if (baseline.resolve(strict=True) != baseline or baseline.is_junction() or baseline.parent.name != "campaigns"
            or baseline.parents[2] != work.parent or not baseline.name.startswith("drill-retry-")):
        raise ValueError("Choose a completed Prototype Drill smoke checkpoint from an isolated sibling lab")
    path = baseline / "save/campaign.json"
    if path.resolve(strict=True) != path or path.is_symlink():
        raise ValueError("The Prototype Drill campaign JSON is outside its exact isolated save path")
    data = runtime.read_json(path)
    world, canonical = data["world"], data["world"]["party"]
    if (world.get("map") != SCIENTIST_SHOP or not drill_download_unlocked(canonical)
            or canonical.get(DRILL.ledger) or SOURCE in canonical.get(pickup.STATE_KEY, {})):
        raise ValueError("The checkpoint must have the Scientist Results unlock and the exact Lab blank still available")
    native_host_save.verify_checkpoint(baseline / "game-user", data)
    return data


def copy_checkpoint(baseline: Path, campaign: Path, data: dict) -> None:
    source_profile, destination_profile = baseline / "game-user", campaign / "game-user"
    for frame in native_host_save.references(data):
        source = native_host_save.slot_path(source_profile, frame["slot"])
        destination = native_host_save.slot_path(destination_profile, frame["slot"])
        if source.resolve(strict=True) != source or source.is_symlink():
            raise ValueError("A referenced Prototype Drill frame leaves the exact isolated save path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(source, destination)
    (campaign / "save").mkdir()
    native_host_save.verify_checkpoint(destination_profile, data)
    runtime.write_json(campaign / "save/campaign.json", data)


async def exercise(work: Path, baseline: Path, vanilla_saved: Path) -> dict:
    initial = baseline_snapshot(work, baseline)
    if not (runtime.qualified_authored_routes(work)
            and runtime.staged_script(work, "native_authored_pickup.lua", SOURCE)
            and runtime.staged_script(work, "native_tape_profiles.lua", DRILL.prefix)):
        raise ValueError("Use a matched native stage containing the exact Lab blank and Scientist Results profiles")
    routes = {}
    for leg in ROUTE:
        match, = [route for route in AUTHORED_ROUTES if
                  (route.source, route.destination, route.entrance, route.kind, route.actor) == leg]
        routes[leg] = match
    originals = saved_hashes(vanilla_saved)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory to verify")
    name = "drill-results-" + uuid.uuid4().hex[:8]
    campaign, client = work / "campaigns" / name, work / "clients" / name
    host_run, guest_run = campaign / "run", client / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    source_checkpoint_hash = sha256((baseline / "save/campaign.json").read_bytes()).hexdigest()
    evidence = {"baseline": baseline.name, "baseline_revision": initial["revision"], "routes": []}
    jobs = []

    async def action(run, verb, *, expected=True, **fields):
        result = await asyncio.wait_for(submit(run, verb, **fields), 45)
        if result.get("ok") is not expected:
            raise RuntimeError(f"{verb} returned {result.get('error', 'an unexpected response')}")
        return result

    def start_host(port):
        task = asyncio.create_task(service.host_game(work, campaign, "127.0.0.1", port, True, True,
                                                     native_events=True, native_guest=True, native_zones=True,
                                                     expanded_zones=True))
        jobs.append((host_run, task))
        return task

    def start_guest(invitation):
        task = asyncio.create_task(service.join_game(work, client, Path(invitation["invite_file"]), True))
        jobs.append((guest_run, task))
        return task

    async def stop(run, task):
        if not task.done():
            await action(run, "stop")
        await asyncio.wait_for(asyncio.shield(task), 18)

    def events():
        with (host_run / "events.jsonl").open(encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.endswith("\n")]

    def saved():
        data = runtime.read_json(campaign / "save/campaign.json")
        native_host_save.verify_checkpoint(campaign / "game-user", data)
        return data

    def flags(data):
        area = data["world"]["map"]
        frames = (data["world"]["native_host_save"], data["characters"][character]["state"]["native_guest_save"])
        assert frames[0]["slot"] != frames[1]["slot"], frames
        root = campaign / "game-user/Saved/SaveGames"
        keys = ("Player_Cash", "Player_Xp", "Mosby_DrillTest", "Mosby_SM_Readings")
        return {owner: {key: all_flags.get(key, 0) for key in keys}
                for owner, frame in zip(("host", "guest"), frames, strict=True)
                for all_flags in (stock_flags(root, frame, area),)}

    async def capture_flags():
        manifest = campaign / "save/campaign.json"
        profile = campaign / "game-user"
        for _ in range(6):
            data = runtime.read_json(manifest)
            try:
                native_host_save.verify_checkpoint(profile, data)
                return data, flags(data)
            except (FileNotFoundError, native_host_save.CampaignFormatError):
                # A newer commit can retire this reader's generation between the two file reads.
                if runtime.read_json(manifest)["revision"] == data["revision"]:
                    raise
                await asyncio.sleep(.05)
        raise RuntimeError("Could not read the committed paired stock flags")

    def local_pawn(state):
        return state.get("game", {}).get("pawns", {}).get("local", {})

    def station_row(state):
        return state.get("game", {}).get(station.KEY, {})

    def pickup_row(state):
        return state.get("game", {}).get(pickup.SOURCES_KEY, {}).get(SOURCE, {})

    def available_blank(state):
        row = pickup_row(state)
        return tuple(row.get(key) for key in ("map", "actor", "class_path", "state")) == (*pickup.SOURCES[SOURCE], "available")

    async def paired(area, previous=None):
        authority = await wait(host_run, lambda state: state.get("state") == "hosting"
                               and state.get("game", {}).get("map") == area
                               and (state["generation"], state.get("zone_epoch")) != previous
                               and state["game"].get("native_guest_save", {}).get("state") == "bound"
                               and any(row["character_id"] == character and row["state"] == "active"
                                       for row in state["campaign"].get("leases", ())), timeout=75)
        guest = await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("link_connected") is True
                           and (state.get("character_id"), state.get("host_session"), state.get("zone_epoch"),
                                state.get("game", {}).get("map")) == (character, authority["generation"],
                                                                    authority["zone_epoch"], area), timeout=75)
        return authority, guest

    async def cross(authority, leg):
        area, destination, entrance, kind, actor = leg
        assert authority["game"]["map"] == area
        pending = dict(source=area, destination=destination, entrance=entrance, kind=kind, actor=actor)
        evidence["pending_route"] = pending
        recipe = "native-door-position" if kind == "door" else "native-exit-position"
        selector = {"entrance": entrance} if kind == "door" else {"entrance": entrance, "exit_actor": actor}
        for role in ("host", "guest"):
            await action(host_run, recipe, target=role, exit_position="near", **selector)
        if kind == "door":
            await action(host_run, "native-door-probe", target="host", **selector)
        else:
            await action(host_run, recipe, target="host", exit_position="inside", **selector)
        updated, _ = await paired(destination, (authority["generation"], authority["zone_epoch"]))
        epoch = updated["zone_epoch"]
        def arrivals():
            return [row for row in events() if row["event"] == "zone_travel_ready" and row["epoch"] == epoch]

        await wait(host_run, lambda _: arrivals(), timeout=15)
        arrived, = arrivals()
        assert (arrived["source"], arrived["destination"], arrived["entrance"]) == (area, destination, entrance), arrived
        actor_class = "Dungeon_TransitionDoor_BP_C" if kind == "door" else "LevelExit_Spawn_BP_C"
        basename = destination.rsplit("/", 1)[-1]
        expected = f"{actor_class} {destination}.{basename}:PersistentLevel.{routes[leg].arrival_actor}"
        marker = arrived.get("authored_arrival", {})
        assert (marker.get("destination"), marker.get("actor"), marker.get("placement")) == (
            destination, expected, "arrow"), arrived
        evidence["routes"].append({**evidence.pop("pending_route"), "zone_epoch": epoch, "arrival": marker})
        return updated

    async def inventory(run, owner):
        result, offset, total = {}, 0, None
        for _ in range(17):
            selector = {"amount": offset} if run == host_run else {"target": str(offset)}
            page = (await action(run, "native-inventory-list", **(selector if offset else {})))["native_inventory"]
            assert (page["owner"], page["offset"]) == (owner, offset) and total in (None, page["total"]), page
            total = page["total"]
            for row in page["items"]:
                assert row["guid"] not in result, row
                result[row["guid"]] = {key: value for key, value in row.items() if key != "index"}
            offset = page["next_offset"]
            if offset is None:
                assert total == len(result), page
                return result
            assert offset == len(result) <= 4096, page
        raise AssertionError("Inventory census exceeded its page bound")

    async def take_blank():
        await wait(host_run, available_blank, timeout=20)
        placed = await action(host_run, pickup.APPROACH, target="guest", pickup_source=SOURCE)
        assert placed["actor"] == pickup.SOURCES[SOURCE][1], placed

        def at_blank(state):
            position = local_pawn(state).get("location")
            return (available_blank(state) and position and len(position) == 3
                    and all(type(value) in (int, float) for value in position)
                    and math.dist(position, BLANK_LOCATION) < 250)

        guest = await wait(guest_run, at_blank, timeout=20)
        pressed = (await action(guest_run, pickup.STOCK_FIXTURE, pickup_source=SOURCE))["native_authored_pickup_interaction"]
        assert (pressed["source"], pressed["map"], pressed["pawn"], pressed["entry"]) == (
            SOURCE, LAB_E206, local_pawn(guest)["name"], "stock"), pressed
        assert type(pressed["sequence"]) is int and pressed["sequence"] > 0
        await wait(host_run, lambda state: SOURCE in state.get("campaign", {}).get("world", {})
                   .get("party", {}).get(pickup.STATE_KEY, {}), timeout=30)
        claim = saved()["world"]["party"][pickup.STATE_KEY][SOURCE]
        assert (claim["map"], claim["actor"], claim["class_path"], claim["owner"]) == (*pickup.SOURCES[SOURCE], character), claim
        assert valid_guid(claim["guid"]), claim
        await wait(guest_run, lambda state: pickup_row(state).get("state") == "claimed", timeout=15)
        evidence["stock_pickup"] = {"press": pressed, "receipt": claim}
        return claim["guid"]

    async def open_offer():
        evidence["station_approach_attempts"] = attempts = []
        for variant in (None, 1, 2, 3, 4, 5, 6, 7, 8):
            try:
                options = {} if variant is None else {"amount": variant}
                placement = (await action(host_run, DRILL.verb("approach"), target="guest", **options))[DRILL.key + "_approach"]
                assert (placement["map"], placement["target"]) == (SCIENTIST_SHOP, DRILL.station), placement
            except RuntimeError as error:
                if "approach_no_safe_floor" not in str(error):
                    raise
                attempts.append({"variant": variant, "refusal": str(error)})
                continue
            await wait(guest_run, lambda state: station_row(state).get("state") in ("armed", "focused"), timeout=15)
            try:
                return placement, (await action(guest_run, station.FIXTURE))[station.KEY]
            except RuntimeError as error:
                if "fixture_stock_focus_unavailable" not in str(error):
                    raise
                attempts.append({"variant": variant, "refusal": str(error)})
        raise RuntimeError("No verified station floor position could focus the exact stock interaction Box")

    async def displayed(state, pressed, timeout):
        frame = await wait(guest_run, lambda report: tuple(station_row(report).get(field) for field in
            ("station", "state", "sequence", "open")) == (DRILL.station, state, pressed["sequence"], pressed["open"]),
            timeout=timeout)
        return station_row(frame)

    async def repeat_exchange(blank, receipt):
        found = await action(guest_run, DRILL.verb("convert"), target=blank, operation_id=receipt["operation_id"])
        assert {key: value for key, value in found.items() if key != "id"} == {
            "ok": True, "effect": DRILL.effect + "_already_exchanged", **receipt}, found
        return found["effect"]

    try:
        copy_checkpoint(baseline, campaign, initial)
        host_job = start_host(0)
        listening = await wait(host_run, lambda state: state.get("state") == "hosting"
                               and state.get("game", {}).get("map") == SCIENTIST_SHOP, timeout=55)
        port = listening["endpoint"]["port"]
        invitation = await action(host_run, "invite", name="Scientist Results verifier", character="Lab courier")
        character = invitation["character_id"]
        assert character not in initial["characters"]
        assert service.load_invite(Path(invitation["invite_file"]), runtime.staged_build(work))["native_mosby_station"] is True
        guest_job = start_guest(invitation)
        authority, _ = await paired(SCIENTIST_SHOP)
        assert saved()["world"]["party"]["native_quests"][DRILL_QUEST] == initial["world"]["party"]["native_quests"][DRILL_QUEST]

        for leg in ROUTE[:3]:
            authority = await cross(authority, leg)
        blank = await take_blank()
        for leg in ROUTE[3:]:
            authority = await cross(authority, leg)
        guest_items, host_items = await inventory(guest_run, character), await inventory(host_run, "host")
        assert blank in guest_items and blank not in host_items
        assert (guest_items[blank]["class_path"], guest_items[blank]["source"], guest_items[blank]["quantity"]) == (
            BLANK_CLASS, "backpack", 1)
        assert sum(row["class_path"] == BLANK_CLASS for row in guest_items.values()) == 1
        await action(host_run, "checkpoint")
        _, stock_before = await capture_flags()
        assert (stock_before["host"]["Mosby_DrillTest"], stock_before["host"]["Mosby_SM_Readings"]) == (1, 2), stock_before

        await action(host_run, DRILL.verb("approach"), target="host")
        wrong_owner = await action(host_run, DRILL.verb("convert"), expected=False, target=blank)
        assert wrong_owner["error"] == "native_mosby_tape_blank_not_owned", wrong_owner
        assert await inventory(guest_run, character) == guest_items

        placement, first = await open_offer()
        assert first["state"] == "checking" and first["open"] == first["sequence"] and first["blank_guid"] is None, first
        offer = await displayed("offer", first, 20)
        exact_offer = (await action(guest_run, DRILL.verb("preflight"), target=blank))[DRILL.key]
        assert (offer["blank_guid"], offer["station"], exact_offer["pawn"], exact_offer["quest_step"],
                exact_offer["mosby_drill_test"]) == (blank, DRILL.station, placement["pawn"], -10, 1), (offer, exact_offer)
        assert exact_offer["give"] == {"class_path": BLANK_CLASS, "quantity": 1, "cash": 0}, exact_offer
        assert exact_offer["receive"] == {"class_path": DRILL.product, "quantity": 1, "cash": 0}, exact_offer
        assert await inventory(guest_run, character) == guest_items
        assert not saved()["world"]["party"].get(DRILL.ledger), "A single inspection press traded the blank"
        second = (await action(guest_run, station.FIXTURE))[station.KEY]
        assert (second["state"], second["open"], second["blank_guid"]) == ("trading", first["open"], blank), second
        assert second["sequence"] > first["sequence"], second
        delivered = await displayed("exchanged", second, 30)
        filled = delivered["filled_guid"]
        assert filled == uuid.uuid5(uuid.NAMESPACE_URL, f"{DRILL.namespace}:{character}:{blank}").hex.upper() and filled != blank
        if not saved()["world"]["party"].get(DRILL.ledger):
            await action(host_run, "checkpoint")
        durable, stock_after = await capture_flags()
        receipt = durable["world"]["party"][DRILL.ledger][blank]
        assert (receipt["owner"], receipt["blank_guid"], receipt["filled_guid"], receipt["map"], receipt["station"],
                receipt["class_path"], receipt["mosby_drill_test"]) == (character, blank, filled, SCIENTIST_SHOP, DRILL.station,
                                                                      DRILL.product, 1), receipt
        expected_guest = await inventory(guest_run, character)
        assert filled in expected_guest and blank not in expected_guest
        fill_row = expected_guest[filled]
        assert (fill_row["class_path"], fill_row["source"], fill_row["quantity"]) == (DRILL.product, "backpack", 1), fill_row
        assert {guid: row for guid, row in expected_guest.items() if guid != filled} == {
            guid: row for guid, row in guest_items.items() if guid != blank}
        assert await inventory(host_run, "host") == host_items
        assert stock_after == stock_before
        assert durable["world"]["party"]["native_quests"][DRILL_QUEST] == initial["world"]["party"]["native_quests"][DRILL_QUEST]

        contested = await action(host_run, DRILL.verb("convert"), expected=False, target=blank)
        assert contested["error"] == "native_mosby_tape_blank_already_exchanged", contested
        replay = await repeat_exchange(blank, receipt)
        evidence["station"] = dict(review=offer, stock_offer=exact_offer, confirmation=second, delivered=delivered,
                                   receipt=receipt, wrong_owner=wrong_owner["error"], contested=contested["error"],
                                   replay=replay, unchanged_stock_flags=stock_before)

        await stop(guest_run, guest_job)
        await stop(host_run, host_job)
        start_host(port)
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != authority["generation"],
                   timeout=55)
        renewed = await action(host_run, "refresh-invite", target=invitation["guest_id"])
        assert (renewed["guest_id"], renewed["character_id"]) == (invitation["guest_id"], character)
        start_guest(renewed)
        restarted, _ = await paired(SCIENTIST_SHOP, (authority["generation"], authority["zone_epoch"]))
        assert restarted["endpoint"]["port"] == port and restarted["generation"] != authority["generation"]
        assert await inventory(guest_run, character) == expected_guest
        assert await inventory(host_run, "host") == host_items
        await action(host_run, DRILL.verb("approach"), target="guest")
        restarted_replay = await repeat_exchange(blank, receipt)
        after, restart_flags = await capture_flags()
        assert after["world"]["party"][DRILL.ledger] == {blank: receipt}
        assert restart_flags == stock_before
        commits = [event for event in events() if event["event"] == DRILL.key + "_committed"]
        assert len(commits) == 1 and commits[0]["filled_guid"] == filled, commits
        evidence.update(restart={"port": port, "generation": restarted["generation"], "filled_guid": filled,
                                 "exchange_commits": len(commits), "replay": restarted_replay}, verified=True)
    except BaseException as error:
        evidence.update(verified=False, failure=f"{type(error).__name__}: {error}")
        raise
    finally:
        try:
            for run, task in reversed(jobs):
                if not task.done():
                    try:
                        await stop(run, task)
                    except (OSError, RuntimeError, TimeoutError, ValueError):
                        task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
            evidence["baseline_verified"] = sha256((baseline / "save/campaign.json").read_bytes()).hexdigest() == source_checkpoint_hash
            if not (evidence["original_saves_verified"] and evidence["baseline_verified"]):
                evidence["verified"] = False
            runtime.write_json(host_run / REPORT, evidence)
        assert evidence["original_saves_verified"] and evidence["baseline_verified"]
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path, help="matched isolated native stage workspace")
    parser.add_argument("--baseline", required=True, type=Path, help="completed Prototype Drill smoke campaign")
    parser.add_argument("--vanilla-saved", required=True, type=Path, help="original single-player Saved directory")
    options = parser.parse_args()
    proof = asyncio.run(exercise(options.work.resolve(strict=True), options.baseline.resolve(strict=True),
                                 options.vanilla_saved.resolve(strict=True)))
    print(json.dumps(proof), flush=True)


if __name__ == "__main__":
    main()
