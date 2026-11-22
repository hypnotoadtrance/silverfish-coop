"""Verify durable stock ammo, Bandage, and loaded pistol drops and party pickup."""

import argparse
import asyncio
from contextlib import contextmanager, nullcontext
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
from tools.silverfish import native_host_save, native_world_loot as projection
from tools.silverfish import native_host_stack_drop as host_drop_input, native_lantern as lantern
from tools.silverfish import native_prop_carry
from tools.silverfish.authored_supply import BANDAGE_CLASS
from tools.silverfish.native_guest_weapon import FIRE_CLASSES


EXIT_TAG, EXIT_ACTOR = "Obsv_Etr", "Level_Exit_BP_C_1"
NEAR_LANES = (1, 2, 3, 4, 5, 6, 7, 8, None)


@contextmanager
def staged_bandage_probe(work):
    scripts = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishCoop/Scripts"
    main, fixture = scripts / "main.lua", scripts / "host_bandage_split_probe.lua"
    original = main.read_bytes()
    carry = scripts / "world_loot_carry_probe.lua"
    copied = []
    try:
        for target in (fixture, carry):
            with target.open("xb") as stream:
                copied.append(target)
                stream.write((Path(__file__).with_name("fixtures") / target.name).read_bytes())
        main.write_bytes(original + b'\nrequire("host_bandage_split_probe")\n')
        yield
    finally:
        main.write_bytes(original)
        for target in copied:
            target.unlink()


async def bandage_probe(run, verb, value=""):
    mailbox = next((run / "game-bridge").iterdir())
    reply = mailbox / "bandage-split-reply"
    reply.unlink(missing_ok=True)
    (mailbox / "bandage-split-probe").write_text(f"{verb}|{value}|{uuid.uuid4().hex[:8]}\n", encoding="ascii")
    async with asyncio.timeout(8):
        while True:
            try:
                status, result = reply.read_text().split("|", 1)
            except (OSError, ValueError):
                await asyncio.sleep(.05)
                continue
            if status != "ok":
                raise RuntimeError(f"{verb}: {result}")
            return result


async def verify(work: Path, originals_path: Path, *, visible_projection=False, claimant="owner", host_origin=False,
                 host_origin_menu=None, pistol_kind=None, probe_lantern=False, bandage=False, guest_carry=False,
                 throw=False):
    host_origin = host_origin or host_origin_menu is not None
    claimant = "teammate" if pistol_kind and not host_origin else claimant
    mode = (f"host-menu-{host_origin_menu}" if host_origin_menu else
            "host-origin" if host_origin else pistol_kind or claimant)
    if bandage:
        mode += "-bandage"
    elif pistol_kind and host_origin:
        mode += f"-{pistol_kind}"
    item_class, quantity = (FIRE_CLASSES[pistol_kind], 1) if pistol_kind else (BANDAGE_CLASS if bandage else record.ITEM, 9)
    actor_class = item_class.rsplit(".", 1)[1]
    name = f"drop-{mode}-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    run = campaign / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    host = ("--campaign", name)
    owner, other = ("--profile", name), ("--profile", name + "-other")
    guests = (owner,) if host_origin or claimant == "host" else (owner, other)
    clients = {target: work / "clients" / target[1] / "run" for target in guests}
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones",
             "--max-guests", "2", "--start-zone", record.OBSERVATORY)
    originals = saved_hashes(originals_path)
    evidence, jobs = {"claimant": "guest" if host_origin else claimant, "host_origin": host_origin,
                      "class_path": item_class}, []

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

    async def inspect_loaded_pistol(target, guid):
        report = (await action(target, "native-guest-weapon-inspect", "--target", guid))["native_guest_weapon"]
        assert (report["guid"], report["status"], report["stored_ammo"], report["actor_ammo"]) == (
            guid, "backpack", 7, -1), report
        return report

    async def drop_pistol_from_overlay(guid, items):
        viewer = clients[owner]
        await wait(viewer, lambda state: state.get("gameplay_fresh")
                   and state.get("gameplay", {}).get(record.CAPABILITY) == 1)
        await action(owner, "native-inventory-view-fixture")
        await wait(viewer, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "rendered")
        key = ("native-inventory-view-key-fixture", "--target")
        for _ in range(next(index for index, item in enumerate(items) if item["guid"] == guid)):
            await action(owner, *key, "down")
        confirmation = (await action(owner, *key, "drop"))[record.VIEW]
        assert (confirmation["state"], confirmation["guid"], confirmation["class_path"], confirmation["count"]) == (
            "confirming", guid, item_class, 1), confirmation
        pending = (await action(owner, *key, "confirm"))[record.VIEW]
        assert pending == {**confirmation, "state": "dropping"}, pending
        result = await wait(viewer, lambda state: (view := state.get("game", {}).get(record.VIEW, {})).get("sequence")
                            == pending["sequence"] and view.get("state") in {"dropped", "refused"})
        rendered = result["game"][record.VIEW]
        assert rendered == {**pending, "state": "dropped"}, rendered
        events = (await command("events", *owner, "--tail", "100"))["events"]
        committed, = [event for event in events if event["event"] == "native_guest_inventory_stack_dropped"
                      and event["view_sequence"] == pending["sequence"]]
        assert committed["operation_id"] == committed["drop_id"], committed
        evidence["guest_inventory_drop"] = {"confirmation": confirmation, "result": rendered, "event": committed}
        await action(owner, *key, "back")
        fields = ("state", "drop_id", "owner", "guid", "quantity", "class_path", "map", "location")
        return {field: committed[field] for field in fields}

    def saved():
        checkpoint = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        native_host_save.verify_checkpoint(campaign / "game-user", checkpoint)
        return checkpoint["world"]["party"].get(record.STATE, {})

    async def projected(viewer, viewer_id, drop_id, dropper):
        identity = projection.player_drop_identity(drop_id)

        def predicate(frame):
            game = frame.get("game", {})
            proof = game.get(projection.KEY, {}).get(identity, {})
            if (proof.get("map"), proof.get("id"), proof.get("pawn")) != (
                    record.OBSERVATORY, identity, game.get("pawns", {}).get("local", {}).get("name")):
                return False
            if viewer == host:
                return frame.get("state") == "hosting"
            row = frame.get("gameplay", {}).get(record.STATE, {}).get(drop_id, {})
            return (frame.get("state"), frame.get("character_id"), frame.get("gameplay_fresh"), row.get("owner")) == (
                "connected", viewer_id, True, dropper)

        frame = await wait(run if viewer == host else clients[viewer], predicate)
        proof = frame["game"][projection.KEY][identity]
        assert proof["pawn"] and proof["actor"].startswith(actor_class + " "), proof
        return proof

    def exact_position(frame, character, role):
        game, gameplay = frame.get("game", {}), frame.get("gameplay", {})
        pawn = gameplay.get("native_pawns", {}).get(role)
        if frame.get("state") != "hosting" or game.get("map") != record.OBSERVATORY or not pawn:
            return None
        if role == "host":
            if character != "host" or game.get("pawns", {}).get("local", {}).get("name") != pawn:
                return None
        elif (gameplay.get("players", {}).get(role, {}).get("character_id") != character
              or game.get("native_guest_save_members", {}).get(pawn, {}).get("state") != "bound"):
            return None
        return next((row["location"] for row in game.get("pawns", {}).values()
                     if row.get("name") == pawn and record.valid_position(row.get("location"))), None)

    async def place(character, position, anchor=None, preferred_lane=None, *, role="guest"):
        start = await wait(run, lambda frame: exact_position(frame, character, role) is not None)
        prior = exact_position(start, character, role)
        pawn = start["gameplay"]["native_pawns"][role]
        generation, epoch = start["generation"], start["zone_epoch"]
        attempts = []
        result = {"pawn": pawn, "attempts": attempts}
        evidence.setdefault("placements", []).append(result)

        def placement_point(frame):
            if ((frame.get("generation"), frame.get("zone_epoch")) == (generation, epoch)
                    and frame.get("gameplay", {}).get("native_pawns", {}).get(role) == pawn):
                return exact_position(frame, character, role)
            return None

        if position == "far":
            candidates = (("far", None), ("near", 4))
        else:
            ordered = dict.fromkeys((preferred_lane, None, *NEAR_LANES)) if preferred_lane else NEAR_LANES
            candidates = tuple(("near", lane) for lane in ordered)

        for where, lane in candidates:
            before = await wait(run, lambda frame: placement_point(frame) is not None, timeout=8)
            sequence = before["game"]["sequence"]
            flags = ("--amount", str(lane)) if lane is not None else ()
            placed = await action(host, "native-exit-position", "--target", role, "--exit-position", where,
                                  "--entrance", EXIT_TAG, "--exit-actor", EXIT_ACTOR, *flags, success=None)
            attempt = {"requested": where, "lane": lane, "response": placed}
            attempts.append(attempt)
            if placed.get("ok") is not True:
                if placed.get("error") == "No verified stock floor at the requested expanded overlap position":
                    continue
                raise RuntimeError(f"The exact Observatory stock exit fixture declined placement: {attempts}")
            assert (placed["target"], placed["position"], placed["entrance"], placed["exit_actor"]) == (
                role, where, EXIT_TAG, EXIT_ACTOR), placed

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
                in_range = distance > 340 if position == "far" else distance < 205
                if not in_range:
                    continue
                result["distance_to_drop"] = distance
            result.update(location=location, lane=lane, samples=samples)
            return result

        raise RuntimeError(f"No exact Observatory lane settled at the required {position} distance: {attempts}")

    async def assert_reclaim(recipient, recipient_id, before, dropped, committed, typed, claim_id, reclaimed,
                             required_destination=None):
        source = dropped["guid"]
        destination = reclaimed.get("inventory_guid", source)
        after = await inventory(recipient, recipient_id)
        expected = {**dropped, "state": "reclaimed", "claimed_by": recipient_id}
        durable = {key: value for key, value in committed.items() if key != "escrow"}
        if required_destination is not None:
            assert destination == required_destination and destination != source, reclaimed
        if destination != source:
            previous, = (item for item in before if item["guid"] == destination)
            assert previous["source"] == "backpack" and previous["class_path"] == typed["class_path"], previous
            assert previous["stackable"] is True and typed["stackable"] is True, (previous, typed)
            units = previous["quantity"] + dropped["quantity"]
            merge = dict(amount=dropped["quantity"], before=previous["quantity"], after=units)
            expected.update(inventory_guid=destination, stack_merge=merge)
            durable.update(inventory_guid=destination, stack_merge=merge)
            assert after == [dict(item, quantity=units) if item["guid"] == destination else item
                             for item in before], after
        else:
            inserted, = (item for item in after if item["guid"] == source)
            assert {key: val for key, val in inserted.items() if key != "index"} == {
                key: val for key, val in typed.items() if key != "index"}
            assert [item for item in after if item["guid"] != source] == before
        assert reclaimed == expected, reclaimed
        assert saved()[dropped["drop_id"]] == dict(durable, claim_id=claim_id, claimed_by=recipient_id)
        return after

    async def collect(recipient, recipient_id, role, before, dropped, committed, typed, preferred_lane,
                      required_destination=None):
        drop_id = dropped["drop_id"]
        identity = projection.player_drop_identity(drop_id)
        near = await place(recipient_id, "near", dropped["location"], preferred_lane=preferred_lane, role=role)
        unclaimed = await projected(recipient, recipient_id, drop_id, dropped["owner"])
        if bandage:
            host_visible = await projected(host, "host", drop_id, dropped["owner"])
            actors = {run: host_visible["actor"].split(" ", 1)[1],
                      clients[recipient]: unclaimed["actor"].split(" ", 1)[1]}
            for directory, actor in actors.items():
                assert await bandage_probe(directory, "actor", actor) == "present"
            guest_run = clients[recipient]
            carrier_run = guest_run if guest_carry else run
            guest_before = (await bandage_probe(guest_run, "pose", actors[guest_run])).split(",")
            host_before = (await bandage_probe(run, "pose", actors[run])).split(",")
            address = await bandage_probe(carrier_run, "grip", identity)
            assert address == (guest_before[0] if guest_carry else host_before[0])
            if guest_carry:
                await wait(run, lambda state: (proof := state.get("game", {}).get(native_prop_carry.PROOF, {}))
                           .get("loot_id") == identity and proof.get("edge") == "grab")
            await wait(guest_run, lambda state: identity in state.get("gameplay", {}).get(projection.MOVING, []))
            await wait(guest_run, lambda state: state.get("game", {}).get(projection.KEY, {}).get(identity, {}).get("moving"))
            edge = "throw" if throw else "drop"
            release = list(map(float, (await bandage_probe(carrier_run, edge)).split(",")))
            if guest_carry:
                await wait(run, lambda state: (proof := state.get("game", {}).get(native_prop_carry.PROOF, {}))
                           .get("loot_id") == identity and proof.get("edge") == edge)
            watched = await wait(guest_run, lambda state: identity not in state.get("gameplay", {}).get(projection.MOVING, [])
                                  and state.get("gameplay", {}).get(record.STATE, {}).get(drop_id, {}).get("transform"))
            transform = watched["gameplay"][record.STATE][drop_id]["transform"]
            await wait(guest_run, lambda state: state.get("game", {}).get(projection.KEY, {}).get(identity) == unclaimed)
            host_pose = (await bandage_probe(run, "pose", actors[run])).split(",")
            guest_pose = (await bandage_probe(guest_run, "pose", actors[guest_run])).split(",")
            assert host_pose[0] == host_before[0] and guest_before[0] == guest_pose[0]
            assert release[2] - transform["position"][2] >= 12
            assert math.dist(transform["position"], list(map(float, host_pose[1:]))) <= 2
            assert math.dist(transform["position"], list(map(float, guest_pose[1:]))) <= 2
            committed = {**committed, "transform": transform}
            evidence["shared_stock_physics_body"] = {
                "identity": identity, "carrier": "guest" if guest_carry else "host", "host": host_pose, "guest": guest_pose,
                "edge": edge, "release": release, "settled_transform": transform,
            }
            await projected(host, "host", drop_id, dropped["owner"])
            await action(host, projection.APPROACH, "--target", f"{role}@{identity}")
        if recipient == host:
            assert unclaimed["pawn"] == near["pawn"], unclaimed
        press = (await action(recipient, projection.FIXTURE, "--target", identity))[projection.KEY][identity]
        assert press["sequence"] > unclaimed["sequence"] and press == {**unclaimed, "sequence": press["sequence"]}
        for target in clients:
            await wait(clients[target], lambda state: state.get("gameplay_fresh")
                       and drop_id not in state.get("gameplay", {}).get(record.STATE, {})
                       and identity not in state.get("game", {}).get(projection.KEY, {}))
        await wait(run, lambda state: identity not in state.get("game", {}).get(projection.KEY, {}))
        if bandage:
            async with asyncio.timeout(6):
                while True:
                    statuses = [await bandage_probe(directory, "actor", actor) for directory, actor in actors.items()]
                    if statuses == ["gone", "gone"]:
                        break
                    evidence["bandage_retirement_pending"] = statuses
                    await asyncio.sleep(.1)
            evidence["physical_bandage_actors_removed"] = {"host": actors[run], "guest": actors[clients[recipient]]}
        events = (await command("events", *recipient, "--tail", "50"))["events"]
        event, = [item for item in events if item["event"] == "local_interaction"
                  and item.get(drop.RESULT, {}).get("drop_id") == drop_id]
        if host_origin_menu in ("drag_to_floor", "equipped_context_menu", "equipped_drag_to_floor") and recipient != host:
            expected = event["operation_id"]
            witnesses = {}
            for target in (host, recipient):
                for _ in range(15):
                    log = (await command("events", *target, "--tail", "50"))["events"]
                    heard = [row for row in log if row["event"] == "native_pickup_sound_played"
                             and row.get("operation_id") == expected]
                    if heard:
                        assert len(heard) == 1 and heard[0]["map"] == record.OBSERVATORY, heard
                        witnesses[target[0]] = heard[0]
                        break
                    await asyncio.sleep(.15)
                else:
                    raise RuntimeError(f"The {target[0]} engine did not invoke the stock item pickup sound")
            evidence["pickup_audio"] = witnesses
        claim_id, reclaimed = event["operation_id"], event[drop.RESULT]
        after = await assert_reclaim(recipient, recipient_id, before, dropped, committed, typed, claim_id, reclaimed,
                                     required_destination)
        replay = await action(recipient, drop.HOST_RECLAIM if recipient == host else drop.RECLAIM,
                              "--target", drop_id, "--operation-id", claim_id)
        assert replay[drop.RESULT] == reclaimed, replay
        assert await inventory(recipient, recipient_id) == after
        return {"stock_projection_interact": press, "reclaim_position": near["location"], "reclaim": reclaimed,
                "stock_projection_reclaim_operation": claim_id, "lane": near["lane"]}

    async def drop_from_host(source, quantity, guest_before, expected_position, preferred_lane):
        host_before = await inventory(host, "host")
        host_stack, = (item for item in host_before if item["guid"] == source)

        partial = host_origin_menu == "split_drag_to_floor"
        dropped_quantity = 4 if partial else quantity
        drop_guid = source
        if host_origin_menu:
            await wait(run, lambda state: state.get("game", {}).get(host_drop_input.SCOPE) is not None)
            fixture = await action(host, host_drop_input.FIXTURE, "--target",
                                   f"{source}|{dropped_quantity}|{host_origin_menu}")
            if partial:
                drop_guid = fixture["drop_guid"]
                assert drop_guid != source, fixture
            if pistol_kind:
                assert fixture["stored_ammo"] == 7, fixture
                evidence["loaded_host"] = {"guid": source, "stored_ammo": fixture["stored_ammo"]}
            await wait(run, lambda state: any(row.get("owner") == "host" and row.get("guid") == drop_guid
                       for row in state.get("gameplay", {}).get(record.STATE, {}).values()))
            events = (await command("events", *host, "--tail", "50"))["events"]
            committed_by_menu, = [event for event in events if event["event"] == "native_host_stock_drop"
                                  and event.get(drop.RESULT, {}).get("guid") == drop_guid]
            host_drop_id, host_drop = committed_by_menu["operation_id"], committed_by_menu[drop.RESULT]
            assert (committed_by_menu["entry"], committed_by_menu["refresh"]) == (host_origin_menu, "refreshed"), committed_by_menu
            evidence["host_menu_drop"] = committed_by_menu
            await action(host, host_drop_input.CLOSE_FIXTURE, "--target", drop_guid)
        else:
            host_drop_id = uuid.uuid4().hex
            host_drop = (await action(host, drop.HOST_DROP, "--target", f"{source}|{quantity}",
                                      "--operation-id", host_drop_id))[drop.RESULT]
        host_request = (drop.HOST_DROP, "--target", f"{drop_guid}|{dropped_quantity}", "--operation-id", host_drop_id)
        evidence["host_drop"] = host_drop
        assert (host_drop["state"], host_drop["drop_id"], host_drop["owner"], host_drop["guid"],
                host_drop["quantity"], host_drop["class_path"]) == (
            "dropped", host_drop_id, "host", drop_guid, dropped_quantity, item_class), host_drop
        assert record.valid_position(host_drop["location"])
        assert math.dist(host_drop["location"], expected_position) < 180, host_drop
        assert (await action(host, *host_request))[drop.RESULT] == host_drop
        expected_host = ([dict(item, quantity=quantity - dropped_quantity) if item["guid"] == source else item
                          for item in host_before] if partial else [item for item in host_before if item["guid"] != source])
        assert await inventory(host, "host") == expected_host
        assert await inventory(owner, owner_id) == guest_before
        host_escrow = evidence["host_drop_durable_escrow"] = saved()[host_drop_id]
        assert "escrow" in host_escrow and host_escrow["claim_id"] is None and "actor" not in host_escrow, host_escrow
        evidence["host_drop_pickup"] = await collect(
            owner, owner_id, "guest", guest_before, host_drop, host_escrow,
            dict(host_stack, guid=drop_guid, quantity=dropped_quantity), preferred_lane)
        if pistol_kind:
            evidence["loaded_recipient"] = await inspect_loaded_pistol(owner, source)
        duplicate = await action(host, drop.HOST_RECLAIM, "--target", host_drop_id, success=False)
        assert duplicate["error"] == "native_guest_stack_drop_already_reclaimed", duplicate
        assert await inventory(host, "host") == expected_host
        evidence["verified"] = True

    try:
        server = await start("host", "host", host, "--port", "0", *flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get(probe.CAPABILITY) == 1)
        invite = await command("invite", *host, "--name", "Drop owner", "--character", "Drop owner")
        owner_id = invite["character_id"]
        owner_flags = ("--native-guest-inventory-view",) if pistol_kind else ()
        player = await start("owner", "join", owner, "--invite", invite["invite_file"], "--headless", "--native-zones",
                             *owner_flags)
        initial = await bound(1)
        if other in clients:
            spare = await command("invite", *host, "--name", "Teammate", "--character", "Teammate")
            other_id = spare["character_id"]
            teammate = await start("teammate", "join", other, "--invite", spare["invite_file"], "--headless", "--native-zones")
            await bound(2)
        for target in clients:
            await wait(clients[target], lambda state: state.get("state") == "connected")

        if probe_lantern:
            def report(state):
                return state.get("game", {}).get(lantern.KEY, {})

            original = report(await wait(run, lambda state: report(state).get("movement") is True))
            peer = clients[owner]
            readback = report(await wait(peer, lambda state: report(state).get("lit") is original["lit"]))
            assert math.dist(original["position"], readback["position"]) < 8, (original, readback)
            lit = (await action(host, lantern.FIXTURE, "--target", "toggle"))[lantern.KEY]
            assert lit["lit"] is not original["lit"], lit
            guest_lit = report(await wait(peer, lambda state: report(state).get("lit") is lit["lit"]))
            await action(host, lantern.FIXTURE, "--target", "impulse")
            moved = report(await wait(peer, lambda state: bool(report(state)) and
                                     math.dist(report(state)["position"], readback["position"]) > 10, timeout=5))
            evidence["observatory_lantern"] = dict(original=original, host_light=lit, guest_light=guest_lit, guest_motion=moved)
            relit = (await action(host, lantern.FIXTURE, "--target", "toggle"))[lantern.KEY]
            assert relit["lit"] is original["lit"], relit
            await wait(peer, lambda state: report(state).get("lit") is original["lit"])

        if bandage:
            guest_before = await inventory(owner, owner_id)
            source = await bandage_probe(run, "seed")
            seeded, = (item for item in await inventory(host, "host") if item["guid"] == source)
            assert (seeded["source"], seeded["class_path"], seeded["quantity"], seeded["stackable"]) == (
                "backpack", BANDAGE_CLASS, quantity, True), seeded
            evidence["host_stock_bandage_setup"] = seeded
            drop_site = await place("host", "near", role="host")
            await drop_from_host(source, quantity, guest_before, drop_site["location"], drop_site["lane"])
            return

        source, attempt, claim_id = uuid.uuid4().hex.upper(), uuid.uuid4().hex, uuid.uuid4().hex
        host_items = await inventory(host, "host")
        owner_destination = teammate_destination = None
        if other in clients:
            if not pistol_kind and claimant == "teammate":
                teammate_destination = uuid.uuid4().hex.upper()
                await action(host, "native-guest-weapon-seed-ammo", "--target", "guest2", "--item-guid",
                             teammate_destination)
            other_items = await inventory(other, other_id)
        if pistol_kind:
            await action(host, "native-guest-weapon-seed", "--target", "guest", "--item-guid", source,
                         "--kind", pistol_kind)
            evidence["loaded_source"] = await inspect_loaded_pistol(owner, source)
        else:
            await action(host, "native-guest-weapon-seed-ammo", "--target", "guest", "--item-guid", source)
            if claimant == "owner" and not host_origin:
                split = (await action(owner, "native-inventory-split", "--target",
                                      f"{source}|4|{quantity}"))["native_inventory_split"]
                owner_destination, quantity = split["new_guid"], split["remaining"]
                assert owner_destination != source and quantity == 5, split
                evidence["owner_ammo_split"] = split
        initial_items = await inventory(owner, owner_id)
        typed, = (item for item in initial_items if item["guid"] == source)
        assert (typed["source"], typed["class_path"], typed["quantity"]) == ("backpack", item_class, quantity), typed
        drop_site = await place(owner_id, "near")
        if pistol_kind:
            refused = await action(owner, drop.DROP, "--target", f"{source}|2", success=False)
            assert refused["error"] == "native_guest_stack_drop_stock_item_row_unqualified", refused
            assert await inventory(owner, owner_id) == initial_items
            assert not saved()
            evidence["wrong_drop_quantity"] = {"item": typed, "requested_quantity": 2, "error": refused["error"]}

            dropped = await drop_pistol_from_overlay(source, initial_items)
            attempt = dropped["drop_id"]
        else:
            dropped = (await action(owner, drop.DROP, "--target", f"{source}|{quantity}",
                                    "--operation-id", attempt))[drop.RESULT]
        request = (drop.DROP, "--target", f"{source}|{quantity}", "--operation-id", attempt)
        evidence["drop"] = dropped
        assert (dropped["state"], dropped["drop_id"], dropped["owner"], dropped["guid"], dropped["quantity"],
                dropped["class_path"]) == ("dropped", attempt, owner_id, source, quantity, item_class), dropped
        assert record.valid_position(dropped["location"])
        assert math.dist(dropped["location"], drop_site["location"]) < 180, (dropped, drop_site)
        assert (await action(owner, *request))[drop.RESULT] == dropped
        owner_after_drop = await inventory(owner, owner_id)
        assert [{key: value for key, value in item.items() if key != "index"} for item in owner_after_drop] == [
            {key: value for key, value in item.items() if key != "index"}
            for item in initial_items if item["guid"] != source], owner_after_drop
        committed = evidence["durable_escrow"] = saved()[attempt]
        assert "escrow" in committed and committed["claim_id"] is None and "actor" not in committed, committed
        if other in clients:
            assert await inventory(other, other_id) == other_items
        assert await inventory(host, "host") == host_items

        if host_origin:
            bootstrap = await collect(host, "host", "host", host_items, dropped, committed, typed, drop_site["lane"])
            evidence["host_stock_pistol_setup" if pistol_kind else "host_stock_ammo_setup"] = bootstrap
            guest_before = await inventory(owner, owner_id)
            assert guest_before == [item for item in initial_items if item["guid"] != source]
            await drop_from_host(source, quantity, guest_before, bootstrap["reclaim_position"], drop_site["lane"])
            return

        if claimant != "owner":
            if claimant == "host":
                recipient, recipient_id, role, before = host, "host", "host", host_items
            else:
                recipient, recipient_id, role, before = other, other_id, "guest2", other_items
            evidence["owner_projection"] = await projected(owner, owner_id, attempt, owner_id)
            evidence.update(await collect(recipient, recipient_id, role, before, dropped, committed, typed,
                                          drop_site["lane"], teammate_destination))
            claim_id = evidence["stock_projection_reclaim_operation"]
            assert await inventory(owner, owner_id) == [item for item in initial_items if item["guid"] != source]
            if recipient != host:
                assert await inventory(host, "host") == host_items
            if pistol_kind:
                evidence["loaded_recipient"] = await inspect_loaded_pistol(recipient, source)
            duplicate = await action(owner, drop.RECLAIM, "--target", attempt, "--operation-id", claim_id, success=False)
            assert duplicate["error"] == "native_guest_stack_drop_already_reclaimed", duplicate
            evidence["verified"] = True
            return

        if visible_projection:
            evidence["projection_before_restart"] = await projected(owner, owner_id, attempt, owner_id)
            evidence["teammate_projection_before_restart"] = await projected(other, other_id, attempt, owner_id)

        await stop(host, server)
        await stop(owner, player)
        await stop(other, teammate)
        await start("host-restart", "host", host, "--port", str(initial["endpoint"]["port"]), *flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != initial["generation"])
        renewed = await action(host, "refresh-invite", "--target", invite["guest_id"])
        await start("owner-restart", "join", owner, "--invite", renewed["invite_file"], "--headless", "--native-zones")
        await bound(1)
        owner_state = await wait(clients[owner], lambda state: state.get("state") == "connected"
                                 and state.get("character_id") == owner_id and attempt in state.get("gameplay", {}).get(record.STATE, {}))
        assert owner_state["gameplay"][record.STATE][attempt]["guid"] == source
        assert saved()[attempt] == committed
        assert await inventory(owner, owner_id) == owner_after_drop
        assert (await action(owner, *request))[drop.RESULT] == dropped

        anchor = dropped["location"]
        far = await place(owner_id, "far", anchor)
        evidence["out_of_range_position"] = far["location"]
        refused = await action(owner, drop.RECLAIM, "--target", attempt, success=False)
        assert refused["error"] == "native_guest_stack_drop_out_of_range", refused
        if visible_projection:
            after_restart = await projected(owner, owner_id, attempt, owner_id)
            assert after_restart["epoch"] != evidence["projection_before_restart"]["epoch"], after_restart
            evidence["projection_after_restart"] = after_restart
            assert (await action(owner, "drop-link"))["link_connected"] is False
            await wait(clients[owner], lambda state: state.get("link_connected") is False
                       and state.get("reconnect_suspended") is True and projection.KEY not in state.get("game", {}))
            refused = await action(owner, projection.FIXTURE, "--target", projection.player_drop_identity(attempt), success=False)
            assert refused["error"] == "native_world_loot_fixture_scope_unavailable", refused
            assert (await action(owner, "reconnect"))["resumed"] is True
            await bound(1)
            after_reconnect = await projected(owner, owner_id, attempt, owner_id)
            assert after_reconnect["epoch"] != after_restart["epoch"], after_reconnect
            evidence["projection_after_reconnect"] = after_reconnect
        near = await place(owner_id, "near", anchor, preferred_lane=drop_site["lane"])
        assert near["pawn"] == far["pawn"], (far, near)
        evidence["reclaim_position"] = near["location"]
        if visible_projection:
            unclaimed = await projected(owner, owner_id, attempt, owner_id)
            assert await inventory(owner, owner_id) == owner_after_drop
            identity = projection.player_drop_identity(attempt)
            press = (await action(owner, projection.FIXTURE, "--target", identity))[projection.KEY][identity]
            assert press["sequence"] > unclaimed["sequence"] and press == {**unclaimed, "sequence": press["sequence"]}
            evidence["stock_projection_interact"] = press
            await wait(clients[owner], lambda state: state.get("gameplay_fresh")
                       and state.get("gameplay", {}).get(record.STATE) == {} and projection.KEY not in state.get("game", {}))
            events = (await command("events", *owner, "--tail", "50"))["events"]
            routed = [event for event in events if event["event"] == "local_interaction"
                      and event.get(drop.RESULT, {}).get("drop_id") == attempt]
            assert len(routed) == 1, routed
            event, = routed
            claim_id, reclaimed = event["operation_id"], event[drop.RESULT]
            evidence["stock_projection_reclaim_operation"] = claim_id
        else:
            reclaimed = (await action(owner, drop.RECLAIM, "--target", attempt, "--operation-id", claim_id))[drop.RESULT]
        recover = (drop.RECLAIM, "--target", attempt, "--operation-id", claim_id)
        evidence["reclaim"] = reclaimed
        after = await assert_reclaim(owner, owner_id, owner_after_drop, dropped, committed, typed, claim_id, reclaimed,
                                     owner_destination)
        assert await inventory(host, "host") == host_items
        assert (await action(owner, *recover))[drop.RESULT] == reclaimed
        assert await inventory(owner, owner_id) == after
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
    parser.add_argument("--visible-projection", action="store_true", help="verify shared stock display and the owner's stock Interact")
    parser.add_argument("--claimant", choices=("owner", "host", "teammate"), default="owner",
                        help="verify the owner after restart, or run a short stock Interact pickup as host or teammate")
    origin = parser.add_mutually_exclusive_group()
    origin.add_argument("--host-origin", action="store_true", help="verify a host drop and a guest's stock Interact pickup")
    origin.add_argument("--host-origin-menu", choices=("ctrl_click", "context_menu", "drag_to_floor", "split_drag_to_floor",
                                                       "equipped_ctrl_click", "equipped_context_menu",
                                                       "equipped_drag_to_floor"),
                        help="drop through the selected stock host inventory route, then pick up the item as the guest")
    pistol = parser.add_mutually_exclusive_group()
    pistol.add_argument("--light-pistol", dest="pistol_kind", action="store_const", const="light-pistol",
                        help="drop a loaded pistol from the guest inventory, then verify the pickup and host route")
    pistol.add_argument("--suppressed-pistol", dest="pistol_kind", action="store_const", const="light-pistol-suppressed",
                        help="verify the same workflow for the suppressed pistol")
    parser.add_argument("--bandage", action="store_true", help="use a stock Bandage stack in the host split-and-drag trial")
    parser.add_argument("--guest-carry", action="store_true", help="have the guest carry the stock Bandage before pickup")
    parser.add_argument("--throw", action="store_true", help="throw the carried Bandage using the stock action")
    parser.add_argument("--probe-lantern", action="store_true", help="verify stock Observatory lantern light and physics")
    settings = parser.parse_args()
    if (settings.host_origin or settings.host_origin_menu) and (settings.visible_projection or settings.claimant != "owner"):
        parser.error("host-origin tests run separately from claimant-only tests")
    if settings.pistol_kind and settings.host_origin_menu == "split_drag_to_floor":
        parser.error("The split drag fixture requires a stackable item")
    if settings.pistol_kind and (settings.visible_projection or settings.claimant == "host"):
        parser.error("Loaded pistol tests use a second guest or a host-to-guest round trip")
    if settings.bandage and (settings.host_origin_menu != "split_drag_to_floor" or settings.pistol_kind):
        parser.error("--bandage requires --host-origin-menu split_drag_to_floor and a stackable item")
    if (settings.guest_carry or settings.throw) and not settings.bandage:
        parser.error("--guest-carry and --throw require --bandage")
    work = settings.work.resolve()
    with staged_bandage_probe(work) if settings.bandage else nullcontext():
        asyncio.run(verify(work, settings.vanilla_saved.resolve(),
                           visible_projection=settings.visible_projection, claimant=settings.claimant,
                           host_origin=settings.host_origin, host_origin_menu=settings.host_origin_menu,
                           pistol_kind=settings.pistol_kind, probe_lantern=settings.probe_lantern, bandage=settings.bandage,
                           guest_carry=settings.guest_carry, throw=settings.throw))
