"""Verify the stock LookTrace and Interact path on authored crate maps."""

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_computer_board_smoke import stock_flags
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.silverfish import native_host_save, native_loot
from tools.silverfish.authored_loot import (CRATES, GUARANTEED_REPLENISHMENT, OFFICE_DESK, OFFICE_DESK_PROFILE,
                                           SMALL_CABINET, SMALL_CABINET_PROFILE, full_name)
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.travel import CISTERN, CITY, CROSSROADS, LAB_E206, OBSERVATORY, RAIL_YARD


SCENES = {
    "city": (CITY, ("LootCrate_Small_BP_C_5", "LootCrate_Small_BP_C_7")),
    "crossroads-overrides": (CROSSROADS, ("LootCrate_Small_BP_C_2", "LootCrate_Small_BP_C_11", "LootCrate_Small_BP_C_14")),
    "rail-yard": (RAIL_YARD, ("LootCrate_Small_BP_C_3",)),
    "cistern": (CISTERN, ("LootCrate_Small_BP_C_7",)),
    "beach": ("/Game/Classes/Maps/Maps_OverWorld/Beach_01_P", ("LootCrate_Small_BP_C_10",)),
    "southeast-road": ("/Game/Classes/Maps/Maps_OverWorld/SouthEast_Road_P", ("LootCrate_Small_BP_C_0",)),
    "trench": ("/Game/Classes/Maps/Maps_OverWorld/Trench_01_P", ("LootCrate_Small_BP_C_1", "LootCrate_Small_BP_C_3")),
}
DEFAULT_SCENES = tuple(SCENES)
GUARANTEED_SCENES = {
    "guaranteed-observatory": (OBSERVATORY, ("LootCrate_Small_BP_C_1",)),
    # Match the lower stock shelves on opposite sides of the sorting aisle.
    "guaranteed-docks": ("/Game/Classes/Maps/Maps_OverWorld/Docks_01_P", ("LootCrate_Small_BP_C_7", "LootCrate_Small_BP_C_16")),
}
SCENES.update(GUARANTEED_SCENES)
CABINET_SCENE = "lab-small-cabinet"
SCENES[CABINET_SCENE] = (SMALL_CABINET_PROFILE[0], (SMALL_CABINET_PROFILE[1],))
LAB_WANDERER_SEED = 964155
LAB_STATIC_WORLD_SEED = 1443576583
LAB_ROUTE = (
    (OBSERVATORY, CROSSROADS, "Obsv_Etr", "Level_Exit_BP_C_1", "overlap"),
    (CROSSROADS, RAIL_YARD, "Depot_Cross_Rail", "Level_Exit_BP_C_1", "overlap"),
    (RAIL_YARD, LAB_E206, "RY_Lab206", "Dungeon_TransitionDoor_BP_C_2", "door"),
)
DESK_SCENE = "lake-office-desk"
SCENES[DESK_SCENE] = (OFFICE_DESK_PROFILE[0], (OFFICE_DESK_PROFILE[1],))
FAMILY_SCENES = {
    "stock-suitcase": (CISTERN, "Loot_SuitCase_BP_C", "Loot_SuitCase_BP_C_1"),
    "stock-trunk": (CITY, "Loot_Trunk_BP_C", "LootCrate_Large_BP_C_1"),
    "stock-cult-crate": ("/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P", "LootCrate_Cult_Small_BP_C", "LootCrate_Cult_Small_BP_C_1"),
    "stock-loot-stash": (CROSSROADS, "Loot_Stash_Small_BP_C", "Loot_Stash_Small_BP_C_1"),
}
SCENES.update({name: (area, (actor,)) for name, (area, _, actor) in FAMILY_SCENES.items()})
FISH_CANNERY_ITEMS = {
    "/Game/Classes/Items/Item_CannedTuna.Item_CannedTuna_C",
    "/Game/Classes/Items/Item_DeadBeetle.Item_DeadBeetle_C",
}


def clone_baseline(work: Path, source: str, campaign: Path, area: str, targets: list[str]) -> int:
    campaigns = (work / "campaigns").resolve(strict=True)
    origin = (campaigns / source).resolve(strict=True)
    if origin.parent != campaigns or origin.name != source or not source.startswith("crate-"):
        raise ValueError("Baseline must be an existing crate smoke campaign in this lab")
    save = (origin / "save" / "campaign.json").resolve(strict=True)
    profile = (origin / "game-user").resolve(strict=True)
    if not (save.is_relative_to(origin) and profile.parent == origin):
        raise ValueError("Baseline save paths must remain inside the crate smoke campaign")
    data = json.loads(save.read_text(encoding="utf-8"))
    world = data["world"]
    seed = world.get("native_observation", {}).get("native_scene", {}).get("instance_seed")
    rolls = world["party"].get(native_loot.STATE_KEY, {})
    if (world.get("map") != area or not world.get("native_host_save") or type(seed) not in (int, float)
            or not 1 <= seed <= 999_999 or int(seed) != seed or any(target in rolls for target in targets)):
        raise ValueError("Baseline must have a verified stock save on this map before the target rolls")
    native_host_save.verify_checkpoint(profile, data)
    destination = campaign / "game-user"
    for frame in native_host_save.references(data):
        slot = frame["slot"]
        src = native_host_save.slot_path(profile, slot)
        dst = native_host_save.slot_path(destination, slot)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    native_host_save.verify_checkpoint(destination, data)
    (campaign / "save").mkdir()
    shutil.copyfile(save, campaign / "save" / "campaign.json")
    return int(seed)


def stock_input_result(run: Path, crate: str, sequence: int) -> dict | None:
    path = run / "events.jsonl"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (row.get("crate") == crate and row.get("sequence") == sequence and row.get("event") in
                ("native_stock_crate_interaction", "native_stock_crate_interaction_rejected")):
            return row
    return None


async def exercise(work: Path, name: str, original_save: Path | None, baseline: str | None = None,
                   force_stock_roll: bool = False, require_cash: bool = False, natural_lab_roll: bool = False) -> None:
    if force_stock_roll and (name != "rail-yard" or original_save is None):
        raise ValueError("The exact Rail Yard forced stock draw requires single-player save verification")
    if natural_lab_roll and (name != CABINET_SCENE or baseline or force_stock_roll):
        raise ValueError("The seeded Lab roll needs its fresh Wanderer route")
    area, actors = SCENES[name]
    guaranteed = name in GUARANTEED_SCENES or name == "stock-loot-stash"
    cabinet = name == CABINET_SCENE
    desk = name == DESK_SCENE
    permanent = guaranteed or cabinet or desk
    family = FAMILY_SCENES[name][1] if name in FAMILY_SCENES else None
    targets = [full_name(area, actor, family) for actor in actors]
    assert all(CRATES.get(crate) == area for crate in targets)
    if name in GUARANTEED_SCENES:
        assert all(GUARANTEED_REPLENISHMENT.get((area, actor)) == 2 for actor in actors)
    if cabinet:
        assert targets == [SMALL_CABINET]
    if desk:
        assert targets == [OFFICE_DESK]
    key = f"crate-{name}-{uuid.uuid4().hex[:8]}"
    campaign = work / "campaigns" / key
    run = campaign / "run"
    guest_run = work / "clients" / key / "run"
    base = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    host, guest = ("--campaign", key), ("--profile", key)
    originals = saved_hashes(original_save) if original_save else None
    if (force_stock_roll or permanent) and not originals:
        raise ValueError("The single-player save directory must contain files to verify")
    evidence = {"map": area, "stock_interactions": []}
    if force_stock_roll:
        evidence["force_stock_roll"] = True
    if natural_lab_roll:
        evidence["wanderer_route"] = []
    processes = []
    cash_requests = []
    exhaustion_flags = {crate: f"{actor}-{area.rsplit('/', 1)[1]}-" for crate, actor in zip(targets, actors)} if permanent else {}
    desk_siblings = tuple(f"LootCabinet_Desk_BP_C_{index}" for index in (1, 2, 7, 8, 9)) if desk else ()
    sibling_names = {full_name(area, actor) for actor in desk_siblings}
    sibling_flags = {f"{actor}-Lake_01_P-" for actor in desk_siblings}
    print(run, flush=True)

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*base, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        out, error = await asyncio.wait_for(process.communicate(), timeout=40)
        reply = json.loads(out) if out else {}
        if reply.get("ok") is not expected:
            raise RuntimeError(f"{arguments}: {reply or error.decode(errors='replace')}")
        return reply

    async def start(label, controller, *arguments):
        run.mkdir(parents=True, exist_ok=True)
        with (run / (label + ".txt")).open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*base, *arguments, cwd=REPO, stdout=stream, stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, controller))

    def scene(status):
        game = status.get("game", {})
        return game.get("native_loot_crates", {}) if game.get("map") == area else {}

    def status_of(status, crate):
        return scene(status).get(crate, {}).get("state")

    def public_roll(status, crate):
        return status.get("gameplay", {}).get(native_loot.STATE_KEY, {}).get(crate)

    def saved_checkpoint():
        saved = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        native_host_save.verify_checkpoint(campaign / "game-user", saved)
        return saved

    def canonical_flags(saved, map_name):
        return stock_flags(native_host_save.directory(campaign / "game-user"), saved["world"]["native_host_save"], map_name)

    def permanent_flags(saved):
        found = canonical_flags(saved, area)
        assert sibling_flags.isdisjoint(found), found
        return {flag: found.get(flag) for flag in exhaustion_flags.values()}

    async def route_pair(map_name, character, prior_epoch=None):
        authority = await wait(run, lambda state: state.get("state") == "hosting" and state.get("link_connected")
                               and state.get("game", {}).get("map") == map_name and state.get("zone_epoch")
                               and state["zone_epoch"] != prior_epoch
                               and state["game"].get("native_guest_save", {}).get("state") == "bound", timeout=70)
        await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("link_connected")
                   and state.get("gameplay_fresh") and state.get("game", {}).get("map") == map_name
                   and state.get("character_id") == character and state.get("host_session") == authority["generation"]
                   and state.get("zone_epoch") == authority["zone_epoch"], timeout=70)
        return authority

    async def verify_lab_seed(authority, map_name):
        observed = authority["game"]["native_scene"]
        assert observed["instance_seed"] == LAB_WANDERER_SEED and observed["game_seconds"] // 86400 == 1, observed
        await command("action", *host, "checkpoint")
        saved = saved_checkpoint()
        frame = saved["world"]["native_observation"]["native_scene"]
        assert frame["instance_seed"] == LAB_WANDERER_SEED and frame["game_seconds"] // 86400 == 1, frame
        flags = canonical_flags(saved, map_name)
        assert flags.get("Static_WorldSeed") == LAB_STATIC_WORLD_SEED, flags
        return {"map": map_name, "epoch": authority["zone_epoch"], "day": 1, "game_seconds": frame["game_seconds"],
                "instance_seed": LAB_WANDERER_SEED, "static_world_seed": LAB_STATIC_WORLD_SEED,
                "loot_karma": flags.get("Loot_Karma", 0)}

    async def reach_lab(character):
        authority = await route_pair(OBSERVATORY, character)
        evidence["wanderer_start"] = await verify_lab_seed(authority, OBSERVATORY)
        for source, destination, entrance, actor, kind in LAB_ROUTE:
            assert sum((route.source, route.destination, route.entrance, route.actor, route.kind) ==
                       (source, destination, entrance, actor, kind) for route in AUTHORED_ROUTES) == 1
            assert authority["game"]["map"] == source
            pending = {"source": source, "destination": destination, "entrance": entrance, "actor": actor, "kind": kind}
            evidence["pending_route"] = pending
            endpoint = ("--entrance", entrance)
            epoch = authority["zone_epoch"]
            if kind == "door":
                for role in ("host", "guest"):
                    await command("action", *host, "native-door-position", "--target", role, "--exit-position", "near", *endpoint)
                await command("action", *host, "native-door-probe", "--target", "host", *endpoint)
            else:
                endpoint += ("--exit-actor", actor)
                for role in ("host", "guest"):
                    await command("action", *host, "native-exit-position", "--target", role, "--exit-position", "near", *endpoint)
                await command("action", *host, "native-exit-position", "--target", "host", "--exit-position", "inside", *endpoint)
            authority = await route_pair(destination, character, epoch)
            seeded = await verify_lab_seed(authority, destination)
            evidence["wanderer_route"].append({**evidence.pop("pending_route"), **seeded})
        assert evidence["wanderer_route"][-1]["loot_karma"] == 0, evidence["wanderer_route"][-1]

    async def typed_inventory_item(character, guid):
        offset = 0
        while offset is not None:
            page = (await command("action", *guest, "native-inventory-list", "--target", str(offset)))["native_inventory"]
            assert (page["owner"], page["offset"]) == (character, offset), page
            item = next((item for item in page["items"] if item["guid"] == guid), None)
            if item:
                return item
            next_offset = page["next_offset"]
            assert next_offset is None or offset < next_offset <= 4096, page
            offset = next_offset

    try:
        seed = clone_baseline(work, baseline, campaign, area, targets) if baseline else None
        if baseline:
            evidence["baseline_seed"] = seed
        start_zone = () if baseline else ("--start-zone", area)
        if natural_lab_roll:
            start_zone = ("--native-start-preset", "wanderer", "--native-world-seed", str(LAB_WANDERER_SEED))
        await start("host", host, "host", *host, "--port", "0", "--headless", "--fixtures", "--native-events",
                    "--native-guest", "--native-zones", "--native-expanded-zones", *start_zone)
        opening = await wait(run, lambda state: state.get("state") == "hosting"
                             and (state.get("game", {}).get("native_fresh_campaign", {}).get("state") == "verified"
                                  if natural_lab_roll else all(status_of(state, crate) == "ready" for crate in targets)))
        if baseline:
            live_seed = opening["game"]["native_scene"]["instance_seed"]
            assert live_seed == seed, (seed, live_seed)
        invitation = await command("invite", *host, "--name", f"{name} crate runner", "--character", "Crate runner")
        character = invitation["character_id"]
        join_zones = ("--native-zones",) if natural_lab_roll else ()
        await start("guest", guest, "join", *guest, "--headless", *join_zones, "--invite", invitation["invite_file"])
        if natural_lab_roll:
            await reach_lab(character)
        ready = await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("gameplay_fresh")
                           and all(status_of(state, crate) == "ready" for crate in targets))
        assert sibling_names.isdisjoint(scene(opening)) and sibling_names.isdisjoint(scene(ready))
        pawn = ready["game"]["pawns"]["local"]["name"]
        bound = await wait(run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        authority_pawn = bound["game"]["native_guest_save"]["pawn"]
        await command("action", *host, "native-loot-seed", "--target", targets[0])
        if permanent:
            await command("action", *host, "checkpoint")
            initial = permanent_flags(saved_checkpoint())
            assert all(value is None for value in initial.values()), initial
            evidence["stock_persistent_flags_before"] = initial

        for position, crate in enumerate(targets):
            await command("action", *host, "native-loot-approach", "--target", crate)
            if family:
                await command("action", *host, "native-loot-approach", "--target", f"host@{crate}")
            forced = None
            if force_stock_roll:
                operation = uuid.uuid4().hex
                reply = await command("action", *host, "native-loot-open-fixture", "--target", f"guest@{crate}",
                                      "--operation-id", operation)
                forced, pressed = reply["native_loot_roll"], reply["native_loot_fixture"]
                assert forced["id"] == operation and forced["items"] and forced["trait_calls"] == 3, forced
                assert (pressed["map"], pressed["crate"], pressed["pawn"], pressed["action"], pressed["status"]) == (
                    area, crate, authority_pawn, "open", native_loot.FORCED_ROLL_STATUS), pressed
                assert (pressed["item_count"], pressed["trait_calls"], pressed["invoked"], pressed["looted"],
                        pressed["ok"]) == (len(forced["items"]), 3, True, True, True), pressed
            else:
                pressed = (await command("action", *guest, "native-loot-crate-fixture", "--target", crate))["native_loot_crate_interaction"]
                assert (pressed["map"], pressed["crate"], pressed["entry"], pressed["pawn"]) == (area, crate, "fixture", pawn), pressed
                await wait(guest_run, lambda _: stock_input_result(guest_run, crate, pressed["sequence"]) is not None)
                result = stock_input_result(guest_run, crate, pressed["sequence"])
                if result["event"] != "native_stock_crate_interaction":
                    evidence["failure"] = result
                    raise RuntimeError(result["reason"])
            committed = await wait(run, lambda state: public_roll(state, crate) and status_of(state, crate) == "exhausted")
            replicated = await wait(guest_run, lambda state: public_roll(state, crate) and status_of(state, crate) == "exhausted")
            roll = public_roll(committed, crate)
            assert public_roll(replicated, crate) == roll and roll["opener"] == character, roll
            assert roll["lucky_looter"], roll
            if desk:
                assert roll["trait_calls"] == 0 and len(roll["items"]) <= 4, roll
                assert sibling_names.isdisjoint(scene(committed)) and sibling_names.isdisjoint(scene(replicated))
            elif guaranteed:
                assert roll["trait_calls"] == 0 and 1 <= len(roll["items"]) <= 2, roll
            elif cabinet:
                assert roll["trait_calls"] == 5 and len(roll["items"]) <= 4, roll
                if natural_lab_roll and not roll["items"]:
                    evidence["unexpected_empty_stock_roll"] = {"receipt": pressed, "roll": roll}
                    raise AssertionError("The seeded stock Lab cabinet returned an empty roll")
            else:
                assert 1 <= roll["trait_calls"] <= 3, roll
            if name == "guaranteed-docks":
                assert {item["class_path"] for item in roll["items"]} <= FISH_CANNERY_ITEMS, roll
            if forced:
                assert roll == forced, (roll, forced)
            for sibling in targets[position + 1:]:
                assert status_of(committed, sibling) == status_of(replicated, sibling) == "ready", sibling
                assert public_roll(committed, sibling) is None, sibling

            claim = None
            item = None
            if roll["items"]:
                guid = roll["items"][0]["guid"]
                target = f"{guid}@{crate}"
                operation = uuid.uuid4().hex
                if family:
                    identity = uuid.uuid5(uuid.NAMESPACE_OID, f"{roll['id']}:{guid}").hex

                    def world_item(state):
                        return state.get("game", {}).get("native_world_loot_items", {}).get(identity)

                    for directory in (run, guest_run):
                        present = await wait(directory, world_item)
                        assert world_item(present)["class_path"] == roll["items"][0]["class_path"]
                        assert not present["game"].get("native_loot_view")
                    await command("action", *host, "native-world-loot-approach", "--target", f"guest@{identity}")
                    press = await command("action", *guest, "native-world-loot-fixture", "--target", identity)
                    assert press["native_world_loot_items"][identity]["sequence"] > 0, press
                    for directory in (run, guest_run):
                        await wait(directory, lambda state: not world_item(state))
                    events = (json.loads(line) for line in (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines())
                    accepted, = [event for event in events if event.get("native_loot_claim", {}).get("guid") == guid]
                    evidence["physical_pickup"] = accepted
                    operation = accepted["operation_id"]
                request = ("action", *guest, "native-loot-claim", "--target", target, "--operation-id", operation)
                claim = (await command(*request))["native_loot_claim"]
                assert claim["claimed_by"] == character and claim["guid"] == roll["items"][0]["guid"], claim
                assert (await command(*request))["native_loot_claim"] == claim
                cash = claim["class_path"] in native_loot.CASH_RANGES
                if cash:
                    credit = claim["wallet_credit"]
                    low, high = native_loot.CASH_RANGES[claim["class_path"]]
                    assert (credit["before"] == committed["game"]["native_wallet"]
                            and low <= credit["amount"] <= high
                            and credit["after"] == credit["before"] + credit["amount"]), credit
                    assert await typed_inventory_item(character, claim["guid"]) is None
                    await wait(run, lambda state: state.get("game", {}).get("native_wallet") == credit["after"])
                    await wait(guest_run, lambda state: state.get("gameplay", {}).get("shared_wallet") == credit["after"])
                    cash_requests.append((request, claim))
                    roll["items"][0]["wallet_credit"] = credit
                elif force_stock_roll or permanent:
                    item = await typed_inventory_item(character, claim["guid"])
                    assert item and (item["source"], item["class_path"]) == ("backpack", claim["class_path"]), item
                denied = await command("action", *host, "native-loot-claim", "--target", target, expected=False)
                assert denied["error"] == "native_loot_already_claimed", denied
                roll["items"][0]["claimed_by"] = character
            replay = (await command("action", *host, "native-loot-open", "--target", crate))["native_loot_roll"]
            assert replay == roll, replay
            saved = saved_checkpoint()
            durable = saved["world"]["party"][native_loot.STATE_KEY][crate]
            assert native_loot.public_roll(durable) == roll, durable
            entry = {"receipt": pressed, "roll": roll, "claim": claim}
            if force_stock_roll or permanent:
                entry["typed_guest_item"] = item
            if permanent:
                actual_flags = permanent_flags(saved)
                expected_flags = {flag: 1 if index <= position else None for index, flag in enumerate(exhaustion_flags.values())}
                assert actual_flags == expected_flags, actual_flags
                entry["stock_persistent_flags"] = actual_flags
            evidence["stock_interactions"].append(entry)

        rolls = evidence["stock_interactions"]
        assert len({entry["roll"]["id"] for entry in rolls}) == len(targets)
        if permanent:
            guids = [item["guid"] for entry in rolls for item in entry["roll"]["items"]]
            assert len(set(guids)) == len(guids), guids
        if require_cash or permanent:
            if require_cash:
                assert cash_requests, "The stock crate rolls produced no cash in this campaign"
                expected = cash_requests[-1][1]["wallet_credit"]["after"]
            port = opening["endpoint"]["port"]
            for process, controller in reversed(processes):
                await command("action", *controller, "stop")
                await asyncio.wait_for(process.wait(), timeout=10)

            await start("host-restart", host, "host", *host, "--port", str(port), "--headless",
                        "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
            resumed = await wait(run, lambda state: state.get("state") == "hosting"
                                 and state.get("generation") != opening["generation"] and state["endpoint"]["port"] == port
                                 and (not require_cash or state.get("game", {}).get("native_wallet") == expected)
                                 and (not permanent or all(status_of(state, crate) == "exhausted" for crate in targets)))
            fresh = await command("action", *host, "refresh-invite", "--target", invitation["guest_id"])
            await start("guest-restart", guest, "join", *guest, "--headless", *join_zones, "--invite", fresh["invite_file"])
            peer = await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("link_connected")
                              and state.get("generation") != ready["generation"] and state.get("character_id") == character
                              and state.get("host_session") == resumed["generation"] and state.get("zone_epoch") == resumed["zone_epoch"]
                              and (not require_cash or state.get("gameplay", {}).get("shared_wallet") == expected)
                              and (not permanent or all(status_of(state, crate) == "exhausted" for crate in targets)))
            for request, claim in cash_requests:
                assert (await command(*request))["native_loot_claim"] == claim
                assert await typed_inventory_item(character, claim["guid"]) is None
            if require_cash:
                evidence["wallet_restart"] = {"balance": expected, "same_port": True, "receipt_count": len(cash_requests)}
            if permanent:
                for crate, entry in zip(targets, rolls):
                    assert public_roll(resumed, crate) == public_roll(peer, crate) == entry["roll"], entry
                    item = entry["typed_guest_item"]
                    if item:
                        restored = await typed_inventory_item(character, item["guid"])
                        fields = ("guid", "class_path", "source", "quantity")
                        assert restored and tuple(restored[key] for key in fields) == tuple(item[key] for key in fields), restored
                await command("action", *host, "checkpoint")
                persisted = permanent_flags(saved_checkpoint())
                assert persisted == dict.fromkeys(exhaustion_flags.values(), 1), persisted
                evidence["stock_persistent_flags_restart"] = {"same_port": True, "flags": persisted}
                if natural_lab_roll:
                    evidence["wanderer_restart"] = await verify_lab_seed(resumed, area)
    except BaseException as error:
        evidence.setdefault("failure", str(error))
        raise
    finally:
        for process, controller in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *controller, "stop")
                    await asyncio.wait_for(process.wait(), timeout=10)
                except (RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=10)
        if originals is not None:
            hashes_match = saved_hashes(original_save) == originals
            evidence["original_files_verified"] = len(originals) if hashes_match else 0
        run.mkdir(parents=True, exist_ok=True)
        (run / "live-authored-crate-expansion-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if originals is not None:
            assert hashes_match, "The original single-player saves changed"


async def main(arguments):
    for name in arguments.area or DEFAULT_SCENES:
        await exercise(arguments.work.resolve(), name, arguments.vanilla_saved, arguments.baseline,
                       arguments.force_stock_roll, arguments.require_cash, arguments.natural_lab_roll)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--area", action="append", choices=SCENES, help="play this scene instead of running the default smoke scenarios")
    parser.add_argument("--vanilla-saved", type=Path)
    parser.add_argument("--baseline", help="resume a copy of a matching crate smoke checkpoint in this lab")
    parser.add_argument("--force-stock-roll", action="store_true", help="use the scoped Rail Yard stock stream fixture")
    parser.add_argument("--require-cash", action="store_true", help="require a stock cash roll and verify its shared balance after restart")
    parser.add_argument("--natural-lab-roll", action="store_true",
                        help="start a seeded Wanderer campaign, take the stock route into Lab E206, and require a nonempty cabinet")
    arguments = parser.parse_args()
    if arguments.require_cash and (not arguments.vanilla_saved or not arguments.area or len(arguments.area) != 1):
        parser.error("--require-cash requires one --area and --vanilla-saved")
    if any(area in GUARANTEED_SCENES or area in (CABINET_SCENE, DESK_SCENE, "stock-loot-stash")
           for area in arguments.area or ()) and not arguments.vanilla_saved:
        parser.error("Permanent stock loot scenes require --vanilla-saved")
    if arguments.force_stock_roll:
        if (arguments.area is not None and arguments.area != ["rail-yard"]) or not arguments.vanilla_saved:
            parser.error("--force-stock-roll requires Rail Yard and --vanilla-saved")
        arguments.area = ["rail-yard"]
    if arguments.baseline and (not arguments.area or len(arguments.area) != 1):
        parser.error("--baseline requires exactly one --area")
    if arguments.natural_lab_roll and (arguments.area != [CABINET_SCENE] or arguments.baseline or arguments.force_stock_roll):
        parser.error("--natural-lab-roll requires --area lab-small-cabinet and a fresh campaign")
    asyncio.run(main(arguments))
