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
from tools.silverfish import native_authored_pickup as pickup, native_guest_consumable as consume, native_host_save
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
        prepared = await action(host_run, pickup.APPROACH, target="guest", pickup_source=pickup.BOROUGH_MRE)
        assert prepared["actor"] == pickup.SOURCES[pickup.BOROUGH_MRE][1], prepared
        raw = (await action(guest_run, pickup.STOCK_FIXTURE, pickup_source=pickup.BOROUGH_MRE))["native_authored_pickup_interaction"]
        assert (raw["entry"], raw["source"], raw["map"]) == ("stock", pickup.BOROUGH_MRE, BOROUGH), raw
        observed = await wait(host_run, lambda state: pickup.BOROUGH_MRE in state.get(
            "campaign", {}).get("world", {}).get("party", {}).get(pickup.STATE_KEY, {}))
        claim = observed["campaign"]["world"]["party"][pickup.STATE_KEY][pickup.BOROUGH_MRE]
        assert (claim["owner"], claim["class_path"]) == (character, consume.MRE_CLASS), claim
        guid = claim["guid"]
        guest_before, host_before = await inventory(guest_run, character), await inventory(host_run, "host")
        assert guest_before[guid]["quantity"] == 1 and guid not in host_before, (guest_before, host_before)
        evidence["stock_pickup"] = {"interaction": raw, "claim": claim}

        await action(guest_run, "native-inventory-view-fixture")
        rendered = await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "rendered")
        selection = rendered["game"].get("native_guest_inventory_use_view", {})
        key = "native-inventory-view-key-fixture"
        for _ in range(rendered["game"]["native_inventory_view"]["count"]):
            if selection.get("guid") == guid:
                break
            selection = (await action(guest_run, key, target="down")).get("native_guest_inventory_use_view", {})
        assert (selection.get("state"), selection.get("guid")) == ("selecting", guid), selection
        confirm = (await action(guest_run, key, target="confirm"))["native_guest_inventory_use_view"]
        assert (confirm["state"], confirm["guid"]) == ("confirming", guid), confirm
        seeded = (await action(host_run, "native-guest-mre-fixture", target="guest"))["native_guest_mre_fixture"]
        assert (seeded["food"], seeded["intoxication"]) == (17, 42), seeded
        pressed = (await action(guest_run, key, target="confirm"))["native_guest_inventory_use_view"]
        assert (pressed["state"], pressed["guid"]) == ("using", guid), pressed
        receipt = await wait_event(guest_run, "native_guest_inventory_used", lambda row: row.get(
            "generation") == attached["generation"] and row.get("view_sequence") == pressed["sequence"])
        proof = receipt["native_guest_mre"]
        assert proof["guid"] == guid and (proof["quantity_before"], proof["quantity_after"]) == (1, 0), proof
        assert 0 <= proof["food_before"] <= 17 and proof["food_after"] == proof["food_before"] + 30, proof
        assert 3 < proof["intox_before"] <= 42 and proof["intox_after"] == proof["intox_before"] - 3, proof
        assert proof["host_intox_before"] == proof["host_intox_after"] == seeded["host_intoxication"], proof
        await wait(guest_run, lambda state: state.get("game", {}).get("native_guest_inventory_use_view", {})
                   == dict(pressed, state="used"))
        await action(guest_run, key, target="back")
        operation = receipt["operation_id"]
        assert (await action(guest_run, consume.MRE, target=guid, operation_id=operation))["native_guest_mre"] == proof
        assert (await action(guest_run, consume.MRE, target=guid, expected=False))["error"] == "guest_mre_refused_stock_owned_stack"
        after = {held: row for held, row in guest_before.items() if held != guid}
        assert await inventory(guest_run, character) == after
        assert await inventory(host_run, "host") == host_before
        evidence.update(seed=seeded, confirmation=pressed, effect=proof)

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

        await launch(host_run, "restart-host", "host", *campaign, "--port", str(initial["endpoint"]["port"]), *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != initial["generation"])
        renewed = await action(host_run, "refresh-invite", target=invitation["guest_id"])
        assert renewed["character_id"] == character, renewed
        await launch(guest_run, "restart-guest", "join", *client, "--invite", renewed["invite_file"], *guest_flags)
        restarted, _ = await paired(character, initial["generation"])
        assert restarted["endpoint"]["port"] == initial["endpoint"]["port"]
        assert await inventory(guest_run, character) == after and await inventory(host_run, "host") == host_before
        assert (await action(guest_run, consume.MRE, target=guid, operation_id=operation))["native_guest_mre"] == proof
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
