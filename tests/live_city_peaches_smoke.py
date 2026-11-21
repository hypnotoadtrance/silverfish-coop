"""Verify an authored stock food pickup, backpack use, and isolated restart."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_guest_held_fire_smoke import wait_event
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.silverfish import native_authored_pickup as pickup
from tools.silverfish import native_guest_consumable as consumable, native_host_save
from tools.silverfish.control import submit

FOOD_SOURCES = (pickup.CITY_PEACHES, pickup.CITY_BEANS, pickup.CRASH_SITE_BEANS, pickup.CROSSROADS_MUSHROOM)


async def verify(work: Path, original: Path, source=pickup.CITY_PEACHES):
    name = source + "-" + uuid.uuid4().hex[:8]
    area, actor, item_class = pickup.SOURCES[source]
    use_action = consumable.COMMANDS[item_class]
    expected = consumable.MEALS[use_action]
    result_key = consumable.RESULT[use_action]
    root = work / "campaigns" / name
    host_run, guest_run = root / "run", work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    if not original.is_dir():
        raise ValueError(f"The original Saved directory does not exist: {original}")
    originals = saved_hashes(original)
    team = ("--campaign", name)
    player = ("--profile", name)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones",
                  "--native-expanded-zones", "--native-guest-mre")
    guest_flags = ("--headless", "--native-zones", "--native-guest-inventory-view")
    jobs = []
    evidence = {}

    async def launch(directory, label, *arguments):
        with (host_run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, *arguments, cwd=REPO, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        jobs.append((directory, process))
        return process

    async def initial_invite():
        process = await asyncio.create_subprocess_exec(
            *cli, "invite", *team, "--name", "Loopback City scavenger", "--character", "City scavenger",
            cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        result, error = await asyncio.wait_for(process.communicate(), 20)
        if process.returncode or not result:
            raise RuntimeError(error.decode(errors="replace"))
        return json.loads(result)

    async def action(directory, verb, *, expected=True, **arguments):
        reply = await submit(directory, verb, **arguments)
        if reply.get("ok") is not expected:
            raise RuntimeError(f"{verb}: {reply}")
        return reply

    async def stop(directory, process):
        if process.returncode is None:
            await action(directory, "stop")
        assert await asyncio.wait_for(process.wait(), 15) == 0, process.returncode

    async def paired(area, previous_epoch=None):
        state = await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("link_connected")
                           and state.get("zone_epoch") != previous_epoch and state.get("game", {}).get("map") == area
                           and state["game"].get("native_guest_save", {}).get("state") == "bound", timeout=75)
        await wait(guest_run, lambda state_local: state_local.get("state") == "connected"
                   and (state_local.get("host_session"), state_local.get("zone_epoch"), state_local.get("character_id"),
                        state_local.get("game", {}).get("map")) == (state["generation"], state["zone_epoch"], character, area),
                   timeout=75)
        return state

    async def inventory(directory, **arguments):
        view = (await action(directory, "native-inventory-list", **arguments))["native_inventory"]
        assert view["next_offset"] is None, view
        return {row["guid"]: row for row in view["items"]}

    async def eat(item):
        guid = item["guid"]
        await action(guest_run, "native-inventory-view-fixture")
        state = await wait(guest_run, lambda s: s.get("game", {}).get("native_inventory_view", {}).get("state") == "rendered")
        selection = state["game"].get("native_guest_inventory_use_view", {})
        key = "native-inventory-view-key-fixture"
        for _ in range(state["game"]["native_inventory_view"]["count"]):
            if selection.get("guid") == guid:
                break
            selection = (await action(guest_run, key, target="down")).get("native_guest_inventory_use_view", {})
        assert (selection.get("state"), selection.get("guid")) == ("selecting", guid), selection
        confirming = (await action(guest_run, key, target="confirm"))["native_guest_inventory_use_view"]
        assert (confirming["state"], confirming["guid"]) == ("confirming", guid), confirming
        pressed = (await action(guest_run, key, target="confirm"))["native_guest_inventory_use_view"]
        assert (pressed["state"], pressed["guid"]) == ("using", guid), pressed
        receipt = await wait_event(guest_run, "native_guest_inventory_used", lambda event: event.get("view_sequence") ==
                                  pressed["sequence"] and event.get(result_key, {}).get("guid") == guid)
        proof = receipt[result_key]
        assert (proof["class_path"], proof["quantity_before"], proof["quantity_after"]) == (
            item["class_path"], item["quantity"], item["quantity"] - 1), proof
        assert proof["food_after"] == min(proof["food_before"] + expected.food, 200) > proof["food_before"], proof
        assert proof["health_before"] == proof["health_after"] and proof["regen_after"] == max(25, proof["regen_before"]), proof
        assert proof["intox_after"] == max(proof["intox_before"] - int(expected.food / 10), 0), proof
        assert proof["host_intox_before"] == proof["host_intox_after"], proof
        await wait(guest_run, lambda s: s.get("game", {}).get("native_guest_inventory_use_view", {}) ==
                   dict(pressed, state="used"))
        await action(guest_run, key, target="back")
        return receipt

    async def verify_food(authority, guest, invitation, initial):
        approach = await action(host_run, pickup.APPROACH, target="guest", pickup_source=source)
        assert approach["actor"] == actor, approach

        def projected(state):
            return state.get("game", {}).get(pickup.SOURCES_KEY, {}).get(source, {})

        available = await wait(guest_run, lambda state: projected(state).get("state") == "available")
        assert (projected(available)["actor"], projected(available)["class_path"]) == (actor, item_class), available
        interaction = (await action(guest_run, "native-authored-pickup-stock-fixture",
                                    pickup_source=source))["native_authored_pickup_interaction"]
        assert (interaction["map"], interaction["source"], interaction["entry"]) == (area, source, "stock"), interaction
        claimed = await wait(host_run, lambda state: source in state.get("gameplay", {}).get(pickup.STATE_KEY, {}))
        claim = claimed["gameplay"][pickup.STATE_KEY][source]
        assert (claim["map"], claim["actor"], claim["owner"], claim["class_path"]) == (area, actor, character, item_class), claim
        guid = claim["guid"]
        item = (await inventory(guest_run))[guid]
        assert (item["class_path"], item["quantity"], item["source"], item["stackable"]) == (
            item_class, 1, "backpack", True
        ), item
        host_inventory = await inventory(host_run)
        assert guid not in host_inventory, claim
        evidence["stock_pickup"] = {"source": projected(available), "interaction": interaction, "receipt": claim, "item": item}
        if source == pickup.CRASH_SITE_BEANS:
            # A guest can take lethal damage beside this pickup. Eat at the checked tunnel door.
            for target in ("host", "guest"):
                await action(host_run, "native-door-position", target=target, entrance="Crash_Tut", exit_position="near")
            current = await wait(host_run, lambda state: state.get("gameplay", {}).get("players", {}).get("guest", {})
                                 .get("character_id") == character)
            if current["gameplay"]["players"]["guest"]["downed"]:
                revived = await action(host_run, "interact", target=character)
                assert revived["effect"] == "revive", revived
                evidence["stock_revive"] = True
            await wait(guest_run, lambda state: state.get("game", {}).get("player_effects", {}).get("local", {}).get("state")
                       == "standing")
        consumed = await eat(item)
        evidence["stock_food_used"] = consumed
        assert guid not in await inventory(guest_run)
        assert await inventory(host_run) == host_inventory

        await stop(host_run, authority)
        await stop(guest_run, guest)
        checkpoint = json.loads((root / "save" / "campaign.json").read_text(encoding="utf-8"))
        host_frame = native_host_save.verify(root / "game-user", checkpoint["world"])
        member = checkpoint["characters"][character]["state"]["native_guest_save"]
        guest_frame = native_host_save.guest_record(root / "game-user", member, character)
        assert host_frame and guest_frame and host_frame["slot"] != guest_frame["slot"], (host_frame, guest_frame)
        assert host_frame["map"] == guest_frame["map"] == area, (host_frame, guest_frame)
        record = checkpoint["world"]["party"]
        assert record[pickup.STATE_KEY][source] == claim, record[pickup.STATE_KEY]
        assert any(row["id"] == consumed["operation_id"] for row in record["receipts"][character]), record["receipts"]

        port = initial["endpoint"]["port"]
        await launch(host_run, "restart-host", "host", *team, "--port", str(port), *host_flags)
        restarted = await wait(host_run, lambda state: state.get("state") == "hosting"
                               and state.get("generation") != initial["generation"] and state.get("game", {}).get("map") == area)
        renewed = await action(host_run, "refresh-invite", target=invitation["guest_id"])
        assert restarted["endpoint"]["port"] == port and renewed["character_id"] == character, restarted
        await launch(guest_run, "restart-guest", "join", *player, "--invite", renewed["invite_file"], *guest_flags)
        await paired(area, initial["zone_epoch"])
        claimed_again = await wait(guest_run, lambda state: projected(state).get("state") == "claimed")
        assert (projected(claimed_again)["actor"], projected(claimed_again)["class_path"]) == (actor, item_class), claimed_again
        replay = await action(guest_run, use_action, target=guid, operation_id=consumed["operation_id"])
        assert replay[result_key] == consumed[result_key] and guid not in await inventory(guest_run), replay
        repeated = await action(guest_run, pickup.ACTION, target=source, expected=False)
        assert repeated["error"] == "native_authored_pickup_already_claimed", repeated
        evidence.update(verified=True, restart={"map": area, "port": port, "host_slot": host_frame["slot"],
                                                "guest_slot": guest_frame["slot"], "replayed_use": replay[result_key],
                                                "repeated_pickup": repeated["error"]})

    try:
        authority = await launch(host_run, "host", "host", *team, "--port", "0", "--start-zone", area, *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting")
        invitation = await initial_invite()
        character = invitation["character_id"]
        guest = await launch(guest_run, "guest", "join", *player, "--invite", invitation["invite_file"], *guest_flags)
        await verify_food(authority, guest, invitation, await paired(area))
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
        proof = "live-city-peaches-verification.json" if source == pickup.CITY_PEACHES else f"live-{source}-verification.json"
        (host_run / proof).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "The original single-player files changed during the smoke"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--source", choices=FOOD_SOURCES, default=pickup.CITY_PEACHES)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve(), args.source))
