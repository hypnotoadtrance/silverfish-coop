"""Prove independent guest food, regeneration, and intoxication from a stock MRE."""

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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from placed_food_sources import BOROUGH_MRE, PLACED_FOOD
from tools.silverfish import native_guest_consumable as consume, native_host_save
from tools.silverfish import native_loose_pickup as loose
from tools.silverfish.control import submit
from tools.silverfish.travel import BOROUGH


async def verify(work: Path, original: Path):
    name = "borough-mre-" + uuid.uuid4().hex[:8]
    root = work / "campaigns" / name
    host_run, guest_run = root / "run", work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    if not original.is_dir():
        raise ValueError(f"The original Saved directory does not exist: {original}")
    originals = saved_hashes(original)
    campaign, client = ("--campaign", name), ("--profile", name)
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones",
                  "--native-expanded-zones", "--native-guest-mre")
    guest_flags = ("--headless", "--native-zones", "--native-guest-inventory-view")
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    jobs, evidence = [], {}

    async def launch(directory, label, *arguments):
        with (host_run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, *arguments, cwd=REPO, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        jobs.append((directory, process))
        return process

    async def invite():
        process = await asyncio.create_subprocess_exec(
            *cli, "invite", *campaign, "--name", "Borough MRE visitor", "--character", "Borough diner",
            cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output, error = await asyncio.wait_for(process.communicate(), 20)
        if process.returncode or not output:
            raise RuntimeError(error.decode(errors="replace"))
        return json.loads(output)

    async def action(directory, verb, *, expected=True, **arguments):
        reply = await submit(directory, verb, **arguments)
        if reply.get("ok") is not expected:
            raise RuntimeError(f"{verb}: {reply}")
        return reply

    async def stop(directory, process):
        if process.returncode is None:
            await action(directory, "stop")
        assert await asyncio.wait_for(process.wait(), 18) == 0, process.returncode

    async def paired(character, previous=None):
        host = await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != previous
                          and state.get("game", {}).get("map") == BOROUGH
                          and state["game"].get("native_guest_save", {}).get("state") == "bound", timeout=60)
        guest = await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("character_id") == character
                           and (state.get("host_session"), state.get("zone_epoch"), state.get("game", {}).get("map"))
                           == (host["generation"], host["zone_epoch"], BOROUGH), timeout=60)
        return host, guest

    async def inventory(directory, owner):
        view = (await action(directory, "native-inventory-list"))["native_inventory"]
        assert view["owner"] == owner and view["next_offset"] is None, view
        return {item["guid"]: {key: value for key, value in item.items() if key != "index"} for item in view["items"]}

    try:
        host = await launch(host_run, "host", "host", *campaign, "--port", "0", "--start-zone", BOROUGH, *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting")
        invitation = await invite()
        character = invitation["character_id"]
        guest = await launch(guest_run, "guest", "join", *client, "--invite", invitation["invite_file"], *guest_flags)
        initial, attached = await paired(character)
        actor = PLACED_FOOD[BOROUGH_MRE][1].split(" ", 1)[1]
        target = f"loose:{actor}"
        prepared = await action(host_run, loose.APPROACH, target=f"guest@{target}")
        assert prepared["actor"] == actor, prepared
        raw = await action(guest_run, loose.FIXTURE, target=target)
        assert raw["actor"] == actor, raw
        committed = await wait_event(host_run, "native_loose_pickup_claimed", lambda event: event.get("actor") == actor)
        claim = (await action(guest_run, loose.CLAIM, target=target, operation_id=committed["operation_id"]))[loose.PROOF]
        assert (claim["owner"], claim["class_path"]) == (character, consume.MRE_CLASS), claim
        guid = claim["guid"]
        guest_before, host_before = await inventory(guest_run, character), await inventory(host_run, "host")
        assert guest_before[guid]["quantity"] == 1 and guid not in host_before, (guest_before, host_before)
        evidence["stock_pickup"] = {"interaction": raw, "claim": claim}

        seeded = (await action(host_run, "native-guest-mre-fixture", target="guest"))["native_guest_mre_fixture"]
        assert (seeded["food"], seeded["intoxication"]) == (17, 42), seeded
        assert seeded["wound_baseline"] == {"hp_before": 250, "hp_after": 210, "regen_before": 250, "regen_after": 230}, seeded
        evidence["seed"] = seeded
        guest_before = await inventory(guest_run, character)
        tuna = "native-guest-canned-tuna"
        tuna_key = consume.RESULT[tuna]
        tuna_guid, = [held for held, row in guest_before.items() if row["class_path"] == consume.MEALS[tuna].class_path]
        assert guest_before[tuna_guid]["quantity"] == 2

        operation = uuid.uuid4().hex
        proof = (await action(guest_run, consume.MRE, target=guid, operation_id=operation))["native_guest_mre"]
        assert proof["guid"] == guid and (proof["quantity_before"], proof["quantity_after"]) == (1, 0), proof
        assert (proof["hemophilia"], proof["gourmand"], proof["healthy_eating"]) == (True, 1, 1), proof
        assert (proof["regen_before"], proof["regen_after"]) == (230, 230 + (10 + 24 * 0.3) * 0.6), proof
        assert 0 < proof["health_before"] == proof["health_after"] < proof["max_health"] == 250, proof
        assert 0 <= proof["food_before"] <= 17 and proof["food_after"] == proof["food_before"] + 24, proof
        assert 2 < proof["intox_before"] <= 42 and proof["intox_after"] == proof["intox_before"] - 2, proof
        assert proof["host_intox_before"] == proof["host_intox_after"] == seeded["host_intoxication"], proof
        assert (await action(guest_run, consume.MRE, target=guid, operation_id=operation))["native_guest_mre"] == proof
        assert (await action(guest_run, consume.MRE, target=guid, expected=False))["error"] == "guest_mre_refused_stock_owned_stack"
        after = {held: row for held, row in guest_before.items() if held != guid}
        assert await inventory(guest_run, character) == after
        assert await inventory(host_run, "host") == host_before
        evidence.update(operation_id=operation, effect=proof)

        await action(guest_run, "native-inventory-view-fixture")
        await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
        pressed = (await action(guest_run, "native-inventory-stock-use-click-fixture", target=tuna_guid))["stock_action"]
        assert pressed["guid"] == tuna_guid and pressed["entry"] == "use-canned-tuna", pressed
        receipt = await wait_event(guest_run, "native_stock_inventory_action", lambda row: row.get(
            "generation") == attached["generation"] and row.get(tuna_key, {}).get("guid") == tuna_guid)
        meal = receipt[tuna_key]
        assert (meal["quantity_before"], meal["quantity_after"], meal["infection_dose"]) == (2, 1, 4), meal
        assert 1 <= meal["sickness_after"] - meal["sickness_before"] <= 4, meal
        assert meal["food_after"] == meal["food_before"] + 6 * 0.8, meal
        actual = await inventory(guest_run, character)
        assert actual[tuna_guid]["quantity"] == 1 and set(actual) == set(after), actual
        assert {key: row for key, row in actual.items() if key != tuna_guid} == {
            key: row for key, row in after.items() if key != tuna_guid}
        assert await inventory(host_run, "host") == host_before
        evidence["stock_tuna_use"] = receipt

        await action(host_run, "checkpoint")
        await stop(guest_run, guest)
        await stop(host_run, host)
        checkpoint = json.loads((root / "save" / "campaign.json").read_text(encoding="utf-8"))
        game_profile = root / "game-user"
        guest_frame = native_host_save.guest_record(game_profile, checkpoint["characters"][character]["state"]["native_guest_save"], character)
        host_frame = native_host_save.verify(game_profile, checkpoint["world"])
        assert guest_frame["slot"] != host_frame["slot"], (guest_frame, host_frame)
        save_root = native_host_save.directory(game_profile)
        guest_flags_saved = stock_flags(save_root, guest_frame, BOROUGH)
        host_flags_saved = stock_flags(save_root, host_frame, BOROUGH)
        assert 0 < guest_flags_saved["Player_Intox"] <= proof["intox_after"], guest_flags_saved
        assert host_flags_saved.get("Player_Intox", 0) == proof["host_intox_after"], host_flags_saved
        assert guest_flags_saved.get("Sickness", 0) == meal["sickness_after"], guest_flags_saved
        assert host_flags_saved.get("Sickness", 0) == 0, host_flags_saved

        await launch(host_run, "restart-host", "host", *campaign, "--port", str(initial["endpoint"]["port"]), *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != initial["generation"])
        renewed = await action(host_run, "refresh-invite", target=invitation["guest_id"])
        assert renewed["character_id"] == character, renewed
        await launch(guest_run, "restart-guest", "join", *client, "--invite", renewed["invite_file"], *guest_flags)
        restarted, _ = await paired(character, initial["generation"])
        assert restarted["endpoint"]["port"] == initial["endpoint"]["port"]
        assert await inventory(guest_run, character) == actual and await inventory(host_run, "host") == host_before
        assert (await action(guest_run, consume.MRE, target=guid, operation_id=operation))["native_guest_mre"] == proof
        assert (await action(guest_run, tuna, target=tuna_guid, operation_id=receipt["operation_id"]))[tuna_key] == meal
        evidence.update(verified=True, restart={"guest_intoxication_saved": guest_flags_saved["Player_Intox"],
                                               "host_intoxication_saved": host_flags_saved.get("Player_Intox", 0), "item_absent": guid})
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
        (host_run / "live-borough-mre-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "The original single-player files changed during the smoke"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve()))
