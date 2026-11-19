"""Verify stock Old Pipe pickup and input at exact authored CQC encounters."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_authored_route_matrix_smoke import select_routes
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish.native_authored_pickup import ACTION, APPROACH, OLD_PIPE, SOURCES, SOURCES_KEY
from tools.silverfish.native_gold import valid_guid
from tools.silverfish import native_host_save
from tools.silverfish.native_guest_melee import (AIM_FIXTURE, AIM_KEY, CANARY, CANARY_KEY, CONTACT_KEY, DAMAGE, EQUIP,
                                                EQUIP_KEY, KIND, SWING, SWING_KEY, verified_damage, verified_miss)
from tools.silverfish.enemy_loot_profiles import BY_MAP as CQC_ZONES
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, REFINERY


REFINERY_ROUTES = (
    "CrossRoads_P@Depot_Cross_Rail@Level_Exit_BP_C_1",
    "RailYard_P@Slum_Depot@Level_Exit_BP_C_611",
    "Slums_P@Slum_Refine@Level_Exit_BP_C_3",
)


async def verify(work: Path, vanilla_saved: Path | None, *, stock_damage=False, ordinary=False,
                 second_cqc=False, refinery=False) -> None:
    mode = "ordinary-second-cqc-" if second_cqc else "ordinary-" if ordinary else "damage-" if stock_damage else ""
    if refinery:
        mode = "refinery-" + mode
    name = "stock-old-pipe-" + mode + uuid.uuid4().hex[:8]
    host, guest = ("--campaign", name), ("--profile", name)
    host_run, guest_run = work / "campaigns" / name / "run", work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    originals = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    processes, proof = [], {}

    async def ask(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            raw, errors = await asyncio.wait_for(process.communicate(), 40)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if not raw or (process.returncode == 0) != expected:
            raise RuntimeError(f"{arguments[:4]}: {raw.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(raw)

    async def action(owner, operation, *arguments, expected=True):
        return await ask("action", *owner, operation, *arguments, expected=expected)

    async def launch(kind, owner, *arguments):
        with (host_run / f"{kind}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, kind, *owner, *arguments, cwd=REPO, stdout=log,
                                                            stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((owner, process))

    async def inventory_item(character, guid):
        offset = 0
        for _ in range(17):
            page = (await action(guest, "native-inventory-list", "--target", str(offset)))["native_inventory"]
            assert (page["owner"], page["offset"]) == (character, offset), page
            for item in page["items"]:
                if item["guid"] == guid:
                    return item
            next_offset = page["next_offset"]
            if next_offset is None:
                return None
            assert next_offset > offset, page
            offset = next_offset
        raise RuntimeError("The staged inventory exceeded its configured page limit")

    def available(status):
        row = status.get("game", {}).get(SOURCES_KEY, {}).get(OLD_PIPE, {})
        return tuple(row.get(key) for key in ("map", "actor", "class_path", "state")) == (*SOURCES[OLD_PIPE], "available")

    async def normal_primary(guid, expected, target="-", *, allow_patrol_miss=False):
        press = (await action(guest, "native-guest-primary-input-fixture"))["native_guest_primary_fixture"]
        assert (press["guid"], press["stock_wrappers"]) == (guid, 2), press
        for _ in range(50):
            rows = (await ask("events", *guest, "--tail", "40"))["events"]
            event = next((row for row in rows if row.get("sequence") == press["sequence"]
                          and row["event"] in ("native_guest_melee_input", "native_guest_melee_input_rejected")), None)
            if event:
                if target != "-":
                    proof["headless_contact_input"] = event
                assert event["event"] == "native_guest_melee_input", event
                swing = event[SWING_KEY]
                if target != "-":
                    diagnostic = await wait(host_run, lambda state: (row := state.get("game", {}).get(CONTACT_KEY, {}))
                                            and (row["map"], row["pawn"], row["guid"], row["serial"], row["target"]) == (
                                                area, swing["pawn"], guid, swing["serial"], target)
                                            and row["stage"] != "awaiting_contact", timeout=5)
                    proof["headless_contact"] = diagnostic["game"][CONTACT_KEY]
                    assert proof["headless_contact"]["camera_scope"] in ("unchanged", "restored"), proof["headless_contact"]
                expected_status = "stock_miss_verified" if (
                    allow_patrol_miss and swing["status"] == "stock_miss_verified"
                ) else expected
                assert (swing["map"], swing["guid"], swing["status"], swing["presses"], swing["releases"],
                        swing["contacts"], swing["qualified"], swing["settled"], swing["passive_free"]) == (
                    area, guid, expected_status, 1, 1, 1, 1, True, True), swing
                if expected_status == "stock_damage_verified":
                    assert verified_damage(swing) and swing["target"] == target, (swing, target)
                else:
                    assert verified_miss(swing), swing
                reply = await action(guest, SWING, "--target", guid, "--operation-id", event["operation_id"])
                assert reply[SWING_KEY] == swing, reply
                directory = host_run.parent
                saved = json.loads((directory / "save" / "campaign.json").read_text(encoding="utf-8"))
                native_host_save.verify_checkpoint(directory / "game-user", saved)
                receipts = saved["world"]["party"]["receipts"][character]
                assert any(item["id"] == event["operation_id"] and item["result"][SWING_KEY] == swing for item in receipts)
                return {"stock_input": press, "host_receipt": swing, "replayed": True, "paired_checkpoint": True}
            await asyncio.sleep(.16)
        raise RuntimeError(f"The real guest primary sequence {press['sequence']} did not return a stock Old Pipe receipt")

    async def cross_exit(previous, destination, entrance, actor=None):
        options = ("--entrance", entrance)
        if actor is not None:
            options += ("--exit-actor", actor)
        for role, where in (("host", "near"), ("guest", "near"), ("host", "inside")):
            await action(host, "native-exit-position", "--target", role, "--exit-position", where, *options)
        arrived = await wait(host_run, lambda state: state.get("state") == "hosting"
                             and state.get("generation") == previous["generation"]
                             and state.get("zone_epoch") != previous["zone_epoch"] and not state.get("awaiting_zone_guest")
                             and state.get("game", {}).get("map") == destination
                             and state["game"].get("native_guest_save", {}).get("state") == "bound", timeout=80)
        await wait(guest_run, lambda state: (state.get("state"), state.get("host_session"), state.get("zone_epoch"),
                   state.get("character_id"), state.get("game", {}).get("map")) == (
                   "connected", arrived["generation"], arrived["zone_epoch"], character, destination), timeout=80)
        leg = {"source": previous["game"]["map"], "destination": destination, "entrance": entrance,
               "source_epoch": previous["zone_epoch"], "destination_epoch": arrived["zone_epoch"]}
        if actor is not None:
            events = (await ask("events", *host, "--tail", "40"))["events"]
            matches = [event for event in events if event["event"] == "native_authored_zone_request"
                       and (event["source"], event["destination"], event["entrance"], event["actor"]) == (
                           leg["source"], destination, entrance, actor)
                       and event["result"].get("travel_epoch") == arrived["zone_epoch"] and event["result"]["ok"] is True]
            assert len(matches) == 1, (leg, actor, matches)
            leg["actor"] = actor
        return arrived, leg

    try:
        expanded = ("--native-expanded-zones",) if refinery else ()
        await launch("host", host, "--port", "0", "--start-zone", CROSSROADS, "--headless", "--fixtures",
                     "--native-events", "--native-guest", "--native-zones", "--native-guest-melee-canary", *expanded)
        ready = await wait(host_run, lambda state: state.get("state") == "hosting" and available(state), timeout=65)
        proof["placed_source"] = ready["game"][SOURCES_KEY][OLD_PIPE]
        invitation = await ask("invite", *host, "--name", "Stock Old Pipe tester", "--character", "Surface witness")
        await launch("join", guest, "--headless", "--native-zones", "--invite", invitation["invite_file"])
        character = invitation["character_id"]
        await wait(guest_run, lambda state: state.get("state") == "connected"
                   and state.get("character_id") == character and state.get("game", {}).get("map") == CROSSROADS)
        coupled = await wait(host_run, lambda state: state.get("state") == "hosting" and available(state)
                             and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

        await action(host, APPROACH, "--target", "guest", "--pickup-source", OLD_PIPE)
        claim = (await action(guest, ACTION, "--target", OLD_PIPE))["native_authored_pickup_claim"]
        assert (claim["source"], claim["map"], claim["actor"], claim["class_path"], claim["owner"]) == (
            OLD_PIPE, *SOURCES[OLD_PIPE], character
        ), claim
        guid = claim["guid"]
        assert valid_guid(guid), claim
        before = await inventory_item(character, guid)
        assert before and before["source"] == "backpack" and before["class_path"] == SOURCES[OLD_PIPE][2], before
        proof.update(claim=claim, backpack=before)

        area = CROSSROADS
        if stock_damage or ordinary:
            denied = await action(host, DAMAGE, "--target", "guest", "--item-guid", guid, expected=False)
            assert denied["error"] == "native_guest_melee_fixture_unavailable", denied
            if refinery:
                area = REFINERY
                proof["stock_routes"] = []
                for route in select_routes(REFINERY_ROUTES):
                    coupled, leg = await cross_exit(coupled, route.destination, route.entrance, route.actor)
                    proof["stock_routes"].append(leg)
            else:
                area = DEAD_BRIDGE
                _, proof["stock_route"] = await cross_exit(coupled, area, "DB_CR")
            assert await inventory_item(character, guid) == before
            denied = await action(host, CANARY, "--target", "guest", "--item-guid", guid, expected=False)
            assert denied["error"] == "native_guest_melee_fixture_unavailable", denied

        if ordinary:
            equipped = (await action(guest, "native-guest-weapon-equip", "--target", guid))["native_guest_weapon"]
            assert (equipped["map"], equipped["guid"], equipped["status"], equipped["ok"], equipped["kind"],
                    equipped["actor_ammo"], equipped["stored_ammo"]) == (area, guid, "old_pipe_equipped", True, KIND, -1, -1), equipped
        else:
            equipped = (await action(host, EQUIP, "--target", "guest", "--item-guid", guid))[EQUIP_KEY]
            assert (equipped["map"], equipped["guid"], equipped["status"], equipped["ok"]) == (
                area, guid, "old_pipe_equipped", True
            ), equipped
        held = await inventory_item(character, guid)
        assert held and held["source"] == "equipment" and held["class_path"] == before["class_path"], held
        proof.update(stock_equipped=equipped, equipment=held)

        if ordinary:
            await wait(guest_run, lambda state: (scope := state.get("game", {}).get("native_guest_primary_scope", {}))
                       and (scope["map"], scope["guid"], scope["kind"]) == (area, guid, KIND))
            proof["unassisted_primary_miss"] = await normal_primary(guid, "stock_miss_verified")
            lane = ("--amount", "2") if second_cqc else ()
            target = CQC_ZONES[area].actors[int(second_cqc)]
            for _ in range(3 if refinery else 1):
                posed = (await action(host, AIM_FIXTURE, "--target", "guest", "--item-guid", guid, *lane))[AIM_KEY]
                assert (posed["map"], posed["pawn"], posed["guid"], posed["target"]) == (area, equipped["pawn"], guid, target), posed
                proof["headless_aim_setup"] = posed
                strike = await normal_primary(guid, "stock_damage_verified", target, allow_patrol_miss=refinery)
                if strike["host_receipt"]["status"] == "stock_damage_verified":
                    proof["ordinary_primary_damage"] = strike
                    break
                proof.setdefault("patrol_misses", []).append({"strike": strike, "contact": proof["headless_contact"]})
            else:
                raise AssertionError(f"All three qualified approaches to {target} missed the stock patrol")
            after = await inventory_item(character, guid)
            assert after == held, (held, after)
            proof.update(unchanged_equipment=after, verified=True)
            return

        operation = uuid.uuid4().hex
        args = ("--target", "guest", "--item-guid", guid, "--operation-id", operation)
        verb = DAMAGE if stock_damage else CANARY
        observed = (await action(host, verb, *args))[CANARY_KEY]
        assert tuple(observed[key] for key in ("map", "pawn", "guid", "status", "presses", "releases", "contacts",
                                               "qualified", "settled", "passive_free")) == (
            area, equipped["pawn"], guid, "stock_damage_verified" if stock_damage else "surface_verified",
            1, 1, 1, 1, True, True
        ), observed
        if stock_damage:
            assert verified_damage(observed), observed
        assert (await action(host, verb, *args))[CANARY_KEY] == observed
        after = await inventory_item(character, guid)
        assert after == held, (held, after)
        proof.update(stock_animation=observed, operation_replayed=True, unchanged_equipment=after, verified=True)
    except BaseException as error:
        proof.update(verified=False, failure=str(error))
        raise
    finally:
        for owner, process in reversed(processes):
            if process.returncode is None:
                try:
                    await action(owner, "stop")
                    await asyncio.wait_for(process.wait(), 15)
                except (OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        if originals is not None:
            proof["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        report = f"live-old-pipe-{mode}verification.json"
        (host_run / report).write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(proof), flush=True)
        if originals is not None:
            assert proof["original_saves_verified"], "The original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--stock-damage", action="store_true", help="take the actual DB_CR exit and verify one stock NPC hit")
    modes.add_argument("--ordinary-primary", action="store_true", help="exercise the normal guest equip and primary input path")
    parser.add_argument("--second-cqc", action="store_true", help="stage the second registered CQC for ordinary primary")
    parser.add_argument("--refinery", action="store_true", help="use stock exits through Rail Yard and Slums to Refinery")
    options = parser.parse_args()
    if options.second_cqc and not options.ordinary_primary:
        parser.error("--second-cqc requires --ordinary-primary")
    if options.refinery and not options.ordinary_primary:
        parser.error("--refinery requires --ordinary-primary")
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve() if options.vanilla_saved else None,
                       stock_damage=options.stock_damage, ordinary=options.ordinary_primary, second_cqc=options.second_cqc,
                       refinery=options.refinery))
