"""Verify real Old Pipe pickup and a fenced stock swing or exact Dead Bridge hit."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish.native_authored_pickup import ACTION, APPROACH, OLD_PIPE, SOURCES, SOURCES_KEY
from tools.silverfish.native_gold import valid_guid
from tools.silverfish import native_host_save
from tools.silverfish.native_guest_melee import (AIM_FIXTURE, AIM_KEY, CANARY, CANARY_KEY, DAMAGE, EQUIP,
                                                EQUIP_KEY, KIND, SWING, SWING_KEY, verified_damage, verified_miss)
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE


async def verify(work: Path, vanilla_saved: Path | None, *, stock_damage=False, ordinary=False) -> None:
    mode = "ordinary-" if ordinary else "damage-" if stock_damage else ""
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

    async def normal_primary(guid, expected):
        press = (await action(guest, "native-guest-primary-input-fixture"))["native_guest_primary_fixture"]
        assert (press["guid"], press["stock_wrappers"]) == (guid, 2), press
        for _ in range(50):
            rows = (await ask("events", *guest, "--tail", "40"))["events"]
            event = next((row for row in rows if row.get("sequence") == press["sequence"]
                          and row["event"] in ("native_guest_melee_input", "native_guest_melee_input_rejected")), None)
            if event:
                assert event["event"] == "native_guest_melee_input", event
                swing = event[SWING_KEY]
                assert (swing["map"], swing["guid"], swing["status"], swing["presses"], swing["releases"],
                        swing["contacts"], swing["qualified"], swing["settled"], swing["passive_free"]) == (
                    DEAD_BRIDGE, guid, expected, 1, 1, 1, 1, True, True), swing
                assert (verified_damage if expected == "stock_damage_verified" else verified_miss)(swing), swing
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

    try:
        await launch("host", host, "--port", "0", "--start-zone", CROSSROADS, "--headless", "--fixtures",
                     "--native-events", "--native-guest", "--native-zones", "--native-guest-melee-canary")
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
            for role, where in (("host", "near"), ("guest", "near"), ("host", "inside")):
                await action(host, "native-exit-position", "--target", role, "--exit-position", where, "--entrance", "DB_CR")
            bound = await wait(host_run, lambda state: state.get("state") == "hosting"
                               and state.get("generation") == coupled["generation"]
                               and state.get("zone_epoch") != coupled["zone_epoch"] and not state.get("awaiting_zone_guest")
                               and state.get("game", {}).get("map") == DEAD_BRIDGE
                               and state["game"].get("native_guest_save", {}).get("state") == "bound", timeout=80)
            await wait(guest_run, lambda state: (state.get("state"), state.get("host_session"), state.get("zone_epoch"),
                       state.get("character_id"), state.get("game", {}).get("map")) == (
                       "connected", bound["generation"], bound["zone_epoch"], character, DEAD_BRIDGE), timeout=80)
            assert await inventory_item(character, guid) == before
            area = DEAD_BRIDGE
            proof["stock_route"] = {"source": CROSSROADS, "destination": area, "entrance": "DB_CR",
                                    "source_epoch": coupled["zone_epoch"], "destination_epoch": bound["zone_epoch"]}
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
            posed = (await action(host, AIM_FIXTURE, "--target", "guest", "--item-guid", guid))[AIM_KEY]
            assert (posed["map"], posed["pawn"], posed["guid"]) == (area, equipped["pawn"], guid), posed
            proof["headless_aim_setup"] = posed
            proof["ordinary_primary_damage"] = await normal_primary(guid, "stock_damage_verified")
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
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve() if options.vanilla_saved else None,
                       stock_damage=options.stock_damage, ordinary=options.ordinary_primary))
