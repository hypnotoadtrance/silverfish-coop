"""Verify stock Data Silo overlap, guest Results compilation, and same-port custody."""

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
from live_drill_test_results_smoke import copy_checkpoint
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_host_save, native_mindcore as core, native_mindcore_fixture as fixture, runtime, service
from tools.silverfish import native_mindcore_view as console
from tools.silverfish import native_mosby_station_view as download_view
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import submit
from tools.silverfish.native_tape_profiles import BLANK_CLASS, DRILL, INSTRUCTIONS, drill_download_unlocked
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


def _route_proofs(rows):
    if not isinstance(rows, list) or len(rows) != len(ROUTE):
        return False
    fields = ("source", "destination", "entrance", "kind", "actor")
    for row, leg in zip(rows, ROUTE, strict=True):
        if not isinstance(row, dict) or tuple(row.get(key) for key in fields) != leg:
            return False
        _, destination, _, kind, _ = leg
        route, = [item for item in AUTHORED_ROUTES
                  if (item.source, item.destination, item.entrance, item.kind, item.actor) == leg]
        field = "Dungeon_TransitionDoor_BP_C" if kind == "door" else "LevelExit_Spawn_BP_C"
        world = destination.rsplit("/", 1)[-1]
        marker = f"{field} {destination}.{world}:PersistentLevel.{route.arrival_actor}"
        if (row.get("arrival", {}).get("actor"), row.get("arrival", {}).get("placement")) != (marker, "arrow"):
            return False
    return True


def baseline_inputs(work, baseline, *, resume=False):
    prefix, report, area = ("mindcore-", REPORT, core.MAP) if resume else (
        "drill-results-", "live-drill-test-results-verification.json", SCIENTIST_SHOP)
    if (baseline.resolve(strict=True) != baseline or baseline.is_junction()
            or baseline.parents[2] != work.parent or baseline.parent.name != "campaigns"
            or not baseline.name.startswith(prefix)):
        raise ValueError("Choose the requested exact campaign type from an isolated sibling lab")
    source = {
        "campaign": baseline / "save/campaign.json",
        "identity": baseline / "save/host-identity.json",
        "proof": baseline / "run" / report,
    }
    for path in source.values():
        if path.resolve(strict=True) != path or path.is_symlink():
            raise ValueError("A Results input is outside its exact isolated campaign")
    data, proof = runtime.read_json(source["campaign"]), runtime.read_json(source["proof"])
    receipt = proof["source_results"] if resume else proof["station"]["receipt"]
    character, guid, blank = (receipt[key] for key in ("owner", "filled_guid", "blank_guid"))
    party = data["world"]["party"]
    if (data["world"]["map"] != area or not drill_download_unlocked(party) or party.get(DRILL.ledger, {}).get(blank) != receipt
            or not core._conversion(party, character, guid) or core.LEDGER in party):
        raise ValueError("The checkpoint must have the verified Scientist Results and a fresh Data Silo console")
    profile = baseline / "game-user"
    native_host_save.verify_checkpoint(profile, data)
    if resume:
        dormant = core.parse_scene(["native-mindcore-scene", core.MAP, core.CRADLE, core.UPLOADER, core.CORE, core.FLAG, "0", "0"])
        frames = (data["world"]["native_host_save"], data["characters"][character]["state"]["native_guest_save"])
        if (proof.get("dormant") != dormant or not _route_proofs(proof.get("routes"))
                or frames[0]["slot"] == frames[1]["slot"]
                or any(stock_flags(profile / "Saved/SaveGames", frame, core.MAP).get(core.FLAG, 0) != 0 for frame in frames)):
            raise ValueError("Resume needs the exact dormant console, seven authored legs, and both original stage-zero frames")
    elif proof.get("verified") is not True:
        raise ValueError("The Scientist Results proof is unverified")
    guest_id = str(uuid.UUID(data["characters"][character]["guest_id"]))
    source["invite"] = baseline / "run/invites" / f"{guest_id}.invite.json"
    if source["invite"].resolve(strict=True) != source["invite"] or source["invite"].is_symlink():
        raise ValueError("The Results owner's paired invitation is unavailable")
    invitation = runtime.read_json(source["invite"])
    if (invitation.get("host"), invitation.get("guest_id"), invitation.get("character_id")) != (
            "127.0.0.1", guest_id, character):
        raise ValueError("The proof needs the exact loopback Results owner")
    return data, source, receipt, guest_id, proof["routes"] if resume else []


async def exercise(work, baseline, vanilla_saved, *, resume=False, stock_input=False, instructions=False,
                   carry_only=False, shared_core=False):
    if instructions and resume:
        raise ValueError("Start from the Scientist Results checkpoint to buy a genuine second blank")
    if carry_only and instructions:
        raise ValueError("Run the Core handling proof separately from the Instructions exchange")
    if not (runtime.qualified_authored_routes(work)
            and runtime.staged_script(work, "native_mindcore.lua", core.FLAG)
            and runtime.staged_script(work, "native_mindcore_fixture.lua", fixture.INSTALL)):
        raise ValueError("Use the matched isolated native stage with the stock overlap fixture")
    if stock_input and not runtime.staged_script(work, "native_mindcore_view.lua", "SilverfishSetNativeMindCoreInput"):
        raise ValueError("Use a matched stage with the ordinary MindCore Interact adapter")
    if carry_only and not runtime.staged_script(work, "native_mindcore_fixture.lua", fixture.CARRY):
        raise ValueError("Use the matched stock Core handling fixture")
    if shared_core and not runtime.staged_script(work, "native_mindcore_replication.lua", "SetReplicateMovement"):
        raise ValueError("Use the matched Data Silo Core replication stage")
    originals = saved_hashes(vanilla_saved)
    if not originals:
        raise ValueError("Supply the original Saved directory for isolation verification")
    data, inputs, conversion, guest_id, prior_routes = baseline_inputs(work, baseline, resume=resume)
    initial_inputs = {key: sha256(path.read_bytes()).hexdigest() for key, path in inputs.items()}
    character, guid, blank = (conversion[key] for key in ("owner", "filled_guid", "blank_guid"))
    name = "mindcore-" + uuid.uuid4().hex[:8]
    campaign, client = work / "campaigns" / name, work / "clients" / name
    run, guest_run = campaign / "run", client / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    start_area = data["world"]["map"]
    proof = {"baseline": baseline.name, "routes": prior_routes, "source_results": conversion}
    if resume:
        proof["resumed_dormant_checkpoint"] = data["revision"]
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

    async def core_poses(previous=None):
        async def observe(directory, role):
            def ready(state):
                game = state.get("game", {})
                row = game.get(fixture.POSE_KEY, {})
                if row.get("role") != role or previous and game.get("sequence", 0) < previous[role]["sequence"] + 4:
                    return False
                return {**row, "sequence": game["sequence"]}

            try:
                state = await wait(directory, ready, timeout=12)
            except RuntimeError:
                raise RuntimeError(f"No fresh {role} Data Silo Core pose during the carry trial") from None
            return ready(state)

        host_pose, guest_pose = await asyncio.gather(observe(run, "host"), observe(guest_run, "guest"))
        return {"host": host_pose, "guest": guest_pose}

    async def shared_core_at(stage, *, label):
        def current(directory):
            state = runtime.read_json(directory / "status.json")
            return state.get("game", {}).get(core.REPLICATION, {})

        host_view = guest_view = {}
        deadline = asyncio.get_running_loop().time() + 16
        while asyncio.get_running_loop().time() < deadline:
            host_view, guest_view = current(run), current(guest_run)
            if ((host_view.get("state"), host_view.get("stage"), host_view.get("capsule_overlap"))
                    == ("active", stage, True)
                    and (guest_view.get("state"), guest_view.get("stage"), guest_view.get("capsule_overlap"))
                    == ("guarded", 0, False)):
                distance = math.dist(host_view["position"], guest_view["position"])
                if distance <= 24:
                    row = {"host": host_view, "guest": guest_view, "distance_uu": round(distance, 3)}
                    proof.setdefault("shared_core", {})[label] = row
                    return row
            await asyncio.sleep(.2)
        raise RuntimeError(f"Core did not synchronize at {label}: host={host_view}, guest={guest_view}")

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

    async def view(state, sequence=None, opened=None):
        row = await wait(guest_run, lambda status: (
            (proof := status.get("game", {}).get(console.KEY, {})).get("state") == state
            and (sequence is None or proof.get("sequence") == sequence)
            and (opened is None or proof.get("opened") == opened)), timeout=30)
        return row["game"][console.KEY]

    async def download_state(state, sequence=None, opened=None):
        frame = await wait(guest_run, lambda reply: (
            (row := reply.get("game", {}).get(download_view.KEY, {})).get("state") == state
            and row.get("station") == INSTRUCTIONS.station
            and (sequence is None or row.get("sequence") == sequence)
            and (opened is None or row.get("open") == opened)), timeout=30)
        return frame["game"][download_view.KEY]

    async def buy_blank():
        await action(run, "native-finite-shop-approach", target="guest")
        listing = (await action(guest_run, "native-finite-shop-list"))["native_finite_shop"]
        shelf, = [row for row in listing["offers"] if row["class_path"] == BLANK_CLASS]
        assert (shelf["quantity"], shelf["value"]) == (1, 30) and 0 < shelf["price"] <= 75, shelf
        result = await action(guest_run, "native-finite-shop-buy", target=f"{shelf['guid']}@{shelf['price']}")
        purchase = result["native_finite_purchase"]
        bought, = purchase["offers"]
        assert purchase["stock_source"] == "stock_shelf" and purchase["offer_guid"] == shelf["guid"]
        assert bought["guid"] != shelf["guid"] and dict(bought, guid=shelf["guid"]) == shelf
        assert purchase["wallet_before"] - purchase["wallet_after"] == shelf["price"]
        assert purchase["merchant_cash_after"] - purchase["merchant_cash_before"] == shelf["price"]
        proof["scientist_blank_purchase"] = purchase
        return bought["guid"]

    async def download_tape(owned_blank, expected_inventory, expected_host, compiled_flags):
        await wait(guest_run, lambda reply: (row := reply.get("game", {}).get(download_view.KEY, {})).get("station")
                   == INSTRUCTIONS.station and row["state"] in ("armed", "focused"), timeout=15)
        first = (await action(guest_run, download_view.FIXTURE))[download_view.KEY]
        opened = first["sequence"]
        assert (first["state"], first["open"], first["station"], first["blank_guid"]) == (
            "checking", opened, INSTRUCTIONS.station, None), first
        offer = await download_state("offer", opened, opened)
        assert offer["blank_guid"] == owned_blank, offer
        before, _ = await flags()
        assert not before["world"]["party"].get(INSTRUCTIONS.ledger)
        assert await inventory(guest_run, character) == expected_inventory
        second = (await action(guest_run, download_view.FIXTURE))[download_view.KEY]
        assert (second["state"], second["open"], second["blank_guid"]) == ("trading", opened, owned_blank), second
        assert second["sequence"] > opened
        exchanged = await download_state("exchanged", second["sequence"], opened)
        product = uuid.uuid5(uuid.NAMESPACE_URL, f"{INSTRUCTIONS.namespace}:{character}:{owned_blank}").hex.upper()
        assert exchanged["filled_guid"] == product
        current, stock_flags_after = await flags()
        assert stock_flags_after == compiled_flags
        current_party = current["world"]["party"]
        account = current_party[INSTRUCTIONS.ledger][owned_blank]
        assert (account["owner"], account["blank_guid"], account["filled_guid"], account["class_path"], account["console_status"]) == (
            character, owned_blank, product, INSTRUCTIONS.product, 2), account
        assert current_party["native_quests"] == data["world"]["party"]["native_quests"], current_party["native_quests"]
        items = await inventory(guest_run, character)
        assert (items[product]["class_path"], items[product]["source"], items[product]["quantity"]) == (INSTRUCTIONS.product, "backpack", 1)
        assert owned_blank not in items and product not in expected_host
        assert {id_: row for id_, row in items.items() if id_ != product} == {
            id_: row for id_, row in expected_inventory.items() if id_ != owned_blank}
        assert await inventory(run, "host") == expected_host
        proof["instructions"] = dict(first=first, offer=offer, second=second, exchanged=exchanged, receipt=account)
        return items, account

    async def inspect_buttons():
        attempts = []
        for variant in (None, *range(1, 13)):
            try:
                selector = {} if variant is None else {"amount": variant}
                placement = await action(run, fixture.APPROACH, **selector)
                await wait(guest_run, lambda state: state.get("game", {}).get(console.KEY, {}).get("state")
                           in ("armed", "focused"), timeout=8)
                first = (await action(guest_run, console.FIXTURE))[console.KEY]
                return placement, first
            except (TimeoutError, RuntimeError) as error:
                reason = str(error)
                if not isinstance(error, TimeoutError) and all(fragment not in reason for fragment in (
                        "console_approach_no_safe_floor", "fixture_stock_focus")):
                    raise
                attempts.append(dict(variant=variant, reason=reason))
                proof["stock_button_approach_attempts"] = attempts
        raise AssertionError("The stock client did not reach or focus the exact MindCore tape button")

    try:
        copy_checkpoint(baseline, campaign, data)
        shutil.copyfile(inputs["identity"], campaign / "save/host-identity.json")
        (run / "invites").mkdir()
        shutil.copyfile(inputs["invite"], run / "invites" / f"{guest_id}.invite.json")
        host_job = start_host(0)
        opening = await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == start_area, timeout=55)
        port = opening["endpoint"]["port"]
        guest_job = await start_guest()
        authority = await paired(start_area)
        owned_blank = await buy_blank() if instructions else None
        if not resume:
            for leg in ROUTE:
                authority = await travel(authority, leg)

        inactive = await wait(run, lambda state: (scene := state.get("game", {}).get(core.SCENE, {}))
                              and (scene["status"], scene["installed"]) == (0, False), timeout=20)
        proof["dormant"] = inactive["game"][core.SCENE]
        if shared_core:
            await shared_core_at(0, label="dormant")
        locked = await action(guest_run, core.UPLOAD, target=guid, success=False)
        assert locked["error"] == core.LOCKED, locked
        if stock_input:
            await view("pass-through", 0, 0)
            dormant_f = await action(guest_run, console.FIXTURE, success=False)
            assert dormant_f["error"] == "The exact stock MindCore upload is unavailable", dormant_f
        await action(run, "checkpoint")
        dormant_checkpoint, dormant_flags = await flags()
        assert tuple(row.get(core.FLAG, 0) for row in dormant_flags) == (0, 0), dormant_flags
        if carry_only:
            proof["stock_carry"] = {}
            poses = proof["core_poses"] = {"before": await core_poses()}
            previous = poses["before"]
            for step, expected in (("grab", "held"), ("step", "carried"), ("release", "released")):
                result = await action(run, fixture.CARRY, target=step)
                observed = result[fixture.CARRY_KEY]
                assert (observed["map"], observed["core"], observed["state"]) == (core.MAP, core.CORE, expected)
                assert (result[core.SCENE]["status"], result[core.SCENE]["installed"]) == (0, False)
                proof["stock_carry"][step] = observed
                poses[step] = previous = await core_poses(previous)
                if shared_core:
                    await shared_core_at(0, label=step)
            movement = proof["stock_carry"]["step"]
            assert movement["pawn_movement_uu"] >= 20 and movement["core_movement_uu"] >= 20, movement
            assert all(row["pawn"] == movement["pawn"] for row in proof["stock_carry"].values())
            proof["core_displacement"] = {role: round(math.dist(poses["before"][role]["position"],
                                                               poses["release"][role]["position"]), 2)
                                          for role in ("host", "guest")}
            if shared_core:
                assert proof["core_displacement"]["guest"] >= 20, proof["core_displacement"]
                assert poses["step"]["host"]["replicates"] and poses["step"]["host"]["replicate_movement"]
                assert (poses["step"]["guest"]["local_role"], poses["step"]["guest"]["replicate_movement"]) == (1, True)
            await paired(core.MAP)
            await action(run, "checkpoint")
            saved, held_flags = await flags()
            assert saved["revision"] > dormant_checkpoint["revision"]
            assert tuple(row.get(core.FLAG, 0) for row in held_flags) == (0, 0)
            assert core.LEDGER not in saved["world"]["party"]
            proof.update(verified=True, carry_checkpoint=saved["revision"],
                         carry_stock_flags=[0, 0], physical_test="stock_grab_short_carry_release")
            return proof

        installed = (await action(run, fixture.INSTALL))[core.SCENE]
        _, mounted_flags = await flags()
        assert (installed["status"], installed["installed"]) == (1, True), installed
        assert tuple(row.get(core.FLAG, 0) for row in mounted_flags) == (1, 0), mounted_flags
        proof["installation"] = {"scene": installed, "headless_relocation": True, "stock_flags": [1, 0]}
        if shared_core:
            await shared_core_at(1, label="mounted")

        near, first = await inspect_buttons() if stock_input else (await action(run, fixture.APPROACH), None)
        if instructions:
            early = await action(guest_run, INSTRUCTIONS.verb("preflight"), target=owned_blank, success=False)
            assert early["error"] == "native_drill_instructions_compiled_console_required", early
            await download_state("waiting", 0, 0)
            proof["instructions_before_compilation"] = early["error"]
        wrong = await action(guest_run, core.UPLOAD, target=blank, success=False)
        assert wrong["error"] == "native_mindcore_refused_durable_results_owner", wrong
        ready = (await action(guest_run, core.PREFLIGHT, target=guid))[core.PROOF]
        assert (ready["status"], ready["pawn"], ready["guid"], ready["proof"]) == (1, near["pawn"], guid, "owned"), ready
        original_guest, original_host = await inventory(guest_run, character), await inventory(run, "host")
        assert (original_guest[guid]["class_path"], original_guest[guid]["quantity"], original_guest[guid]["source"]) == (
            DRILL.product, 1, "backpack"), original_guest[guid]
        assert guid not in original_host and blank not in original_guest
        if stock_input:
            opened = first["sequence"]
            assert (first["state"], first["opened"], first["guid"], first["cradle"]) == ("checking", opened, None, core.CRADLE)
            offer = await view("offer", opened, opened)
            assert offer["guid"] == guid
            _, checked = await flags()
            assert tuple(row.get(core.FLAG, 0) for row in checked) == (1, 0)
            second = (await action(guest_run, console.FIXTURE))[console.KEY]
            assert (second["state"], second["opened"], second["guid"], second["pawn"]) == ("submitting", opened, guid, first["pawn"])
            assert second["sequence"] > opened
            compiled = await view("compiled", second["sequence"], opened)
            assert compiled["guid"] == guid
            proof["stock_buttons"] = dict(first=first, offer=offer, second=second, compiled=compiled)
        else:
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
        if stock_input:
            operation_id = receipt["operation_id"]
        assert (receipt["operation_id"], receipt["owner"], receipt["guid"]) == (operation_id, character, guid)
        assert await inventory(guest_run, character) == original_guest and await inventory(run, "host") == original_host
        proof["upload"] = {"locked": locked["error"], "wrong_guid": wrong["error"], "preflight": ready,
                           "receipt": receipt, "tape": original_guest[guid], "stock_flags": [2, 0]}
        final_guest, instructions_receipt = (await download_tape(owned_blank, original_guest, original_host, compiled_flags)
                                             if instructions else (original_guest, None))

        await stop(guest_run, guest_job)
        await stop(run, host_job)
        start_host(port)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != authority["generation"], timeout=55)
        await start_guest()
        restarted = await paired(core.MAP, (authority["generation"], authority["zone_epoch"]))
        assert restarted["endpoint"]["port"] == port
        restored = await wait(run, lambda state: core._ready(state.get("game", {}), 2), timeout=25)
        if shared_core:
            await shared_core_at(2, label="late_join_after_restart")
        if stock_input:
            await view("pass-through", 0, 0)
            stage_two = await action(guest_run, console.FIXTURE, success=False)
            assert stage_two["error"] == "The exact stock MindCore upload is unavailable", stage_two
            proof["stage_two_upload_input"] = "pass-through"
        replay = await action(guest_run, core.UPLOAD, target=guid, operation_id=operation_id)
        assert replay["effect"] == "native_mindcore_already_compiled", replay
        assert await inventory(guest_run, character) == final_guest and await inventory(run, "host") == original_host
        after, restored_flags = await flags()
        assert after["world"]["party"][core.LEDGER] == receipt
        assert tuple(row.get(core.FLAG, 0) for row in restored_flags) == (2, 0), restored_flags
        commits = [row for row in events() if row["event"] == "native_mindcore_results_committed"]
        assert len(commits) == 1 and commits[0]["operation_id"] == operation_id, commits
        if instructions:
            await action(run, fixture.APPROACH)
            instructions_replay = await action(guest_run, INSTRUCTIONS.verb("convert"), target=owned_blank,
                                               operation_id=instructions_receipt["operation_id"])
            assert instructions_replay["effect"] == INSTRUCTIONS.effect + "_already_exchanged"
            assert after["world"]["party"][INSTRUCTIONS.ledger] == {owned_blank: instructions_receipt}
            assert await inventory(guest_run, character) == final_guest
            exchanges = [row for row in events() if row["event"] == INSTRUCTIONS.key + "_committed"]
            assert len(exchanges) == 1 and exchanges[0]["filled_guid"] == instructions_receipt["filled_guid"]
            proof["instructions"]["restart_replay"] = instructions_replay["effect"]
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
    parser.add_argument("--resume", action="store_true", help="clone the paired dormant Data Silo stage-zero checkpoint")
    parser.add_argument("--stock-input", action="store_true", help="confirm the upload with two genuine stock Interact presses")
    parser.add_argument("--instructions", action="store_true", help="buy a second blank and use the compiled console's stock exchange")
    parser.add_argument("--carry-only", action="store_true", help="grab, move, and release the Core with stock gameplay")
    parser.add_argument("--shared-core", action="store_true", help="verify the guest sees the authoritative exact Core")
    options = parser.parse_args()
    paths = (value.resolve(strict=True) for value in (options.work, options.baseline, options.vanilla_saved))
    asyncio.run(exercise(*paths, resume=options.resume, stock_input=options.stock_input or options.instructions,
                         instructions=options.instructions, carry_only=options.carry_only, shared_core=options.shared_core))
