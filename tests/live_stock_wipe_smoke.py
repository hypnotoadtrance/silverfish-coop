"""Verify typed extraction penalties and owner-only recovery on a staged Observatory host."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes
from live_wanderer_preset_smoke import DIFFICULTY_RULES, SEED


OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
LOOSE_PICKUP = "observatory-comp-board"


async def validate(work: Path, vanilla_saved: Path | None, visible_bag: bool, authored_pickup: bool, stock_rules: bool,
                   wipe_view: bool, difficulty: str | None = None) -> None:
    name = "stock-wipe-" + uuid.uuid4().hex[:8]
    host = work / "campaigns" / name / "run"
    guest = work / "clients" / name / "run"
    host.mkdir(parents=True, exist_ok=True)
    guest.mkdir(parents=True, exist_ok=True)
    primary = ("--campaign", name)
    secondary = ("--profile", name)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    if difficulty:
        boot = ("--native-start-preset", "wanderer", "--native-world-seed", str(SEED), "--native-difficulty", difficulty)
        extraction_fields = DIFFICULTY_RULES[difficulty].copy()
    else:
        options = ("--native-extraction-keep", "1", "--native-extraction-backpacks", "--native-extraction-lose-slot") \
            if stock_rules else ("--fixture-keep-slots", "1", "--fixture-backpacks")
        boot = (*options, "--start-zone", OBSERVATORY)
        extraction_fields = {"extraction_mode": 1, "items_kept_on_death": 1, "drop_bone_pile": 1, "lose_slot_per_death": 1}
    keep_slots = extraction_fields["items_kept_on_death"]
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    processes = []
    print(host, flush=True)

    async def start(log, owner, *arguments):
        with log.open("wb") as output:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=output, stderr=output,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))
        return process

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE,
                                                        creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 42)
        if not output or (expected is not None and (process.returncode == 0) != expected):
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        return json.loads(output)

    async def action(owner, operation, *arguments, expected=True):
        return await command("action", *owner, operation, *arguments, expected=expected)

    async def wait(directory, predicate):
        deadline = time.monotonic() + 40
        last = None
        while time.monotonic() < deadline:
            try:
                last = json.loads((directory / "status.json").read_text(encoding="utf-8"))
                if predicate(last):
                    return last
                if last.get("state") == "failed":
                    break
            except (OSError, ValueError):
                pass
            await asyncio.sleep(.15)
        raise RuntimeError(f"{directory} did not reach the expected state: {last}")

    try:
        host_process = await start(host / "host.txt", primary, "host", *primary, "--port", "0", *host_flags,
                                   *boot)
        initial = await wait(host, lambda status: status.get("state") == "hosting"
                             and status["game"].get("native_rest_hook", {}).get("fresh"))
        if stock_rules or difficulty:
            assert all(initial["game"]["native_rules"].get(key) == value for key, value in extraction_fields.items()), initial
        await action(primary, "native-rest-approach")
        rest = (await action(primary, "native-rest", "--amount", "0"))["native_rest"]
        invitation = await command("invite", *primary, "--name", "Native wipe guest", "--character", "Native wipe character")
        await start(guest / "guest.txt", secondary, "join", *secondary, "--headless", "--native-zones",
                    "--invite", invitation["invite_file"])
        await wait(guest, lambda status: status.get("state") == "connected")
        await wait(host, lambda status: status.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        character = invitation["character_id"]
        gun, reserve = (uuid.uuid4().hex.upper() for _ in range(2))
        await action(primary, "native-guest-weapon-seed", "--item-guid", gun)
        await action(secondary, "native-guest-weapon-equip", "--target", gun)
        await action(primary, "native-guest-weapon-seed-ammo", "--item-guid", reserve)
        await action(primary, "native-guest-damage", "--target", "guest")
        await action(primary, "native-host-damage", "--target", "host")
        if keep_slots:
            await wait(host, lambda status: (status.get("gameplay", {}).get("wipe") or {}).get("keep_slots") == keep_slots)
            draft = None
            for _ in range(20):
                frame = await action(secondary, "native-wipe-items", expected=None)
                if frame.get("ok"):
                    draft = frame["native_wipe_items"]
                    break
                await asyncio.sleep(.2)
            assert draft and {item["guid"] for item in draft["items"]} == {gun, reserve}, draft
        if wipe_view:
            async def key(owner, button):
                return (await action(owner, "native-wipe-view-key-fixture", "--target", button))["native_wipe_view"]

            for directory in (host, guest):
                await wait(directory, lambda status: status.get("game", {}).get("native_wipe_view", {}).get("state") == "selecting")
            current = (await command("status", *secondary))["game"]["native_wipe_view"]
            if current["guid"] != gun:
                current = await key(secondary, "down")
            assert current["guid"] == gun and current["limit"] == keep_slots and current["total"] == 2, current
            selected = await key(secondary, "toggle")
            assert selected["selected"] == 1 and selected["guid"] == gun, selected
            submitted = []
            for owner, count in ((secondary, 1), (primary, 0)):
                confirmed = await key(owner, "submit")
                assert confirmed["state"] == "confirming" and confirmed["selected"] == count, confirmed
                ready = await key(owner, "submit")
                assert ready["state"] == "submitting" and ready["sequence"] > 0, ready
                submitted.append(ready)
        elif keep_slots:
            await action(secondary, "keep", "--keep", f"{gun}=1")
            await action(primary, "keep")
        revived = await wait(host, lambda status: status.get("state") == "hosting"
                             and status.get("gameplay", {}).get("native_backpacks")
                             and status["gameplay"].get("wipe") is None and not status["gameplay"].get("pending_spawns"))
        owned = revived["gameplay"]["native_backpacks"]
        bag_id, = [key for key, value in owned.items() if value["owner_id"] == character]
        bag = owned[bag_id]
        dropped_count = 1 if keep_slots else 2
        assert bag["count"] == dropped_count and bag["position"]["map"] == OBSERVATORY, bag
        if difficulty:
            assert any(value["owner_id"] == "host" for value in owned.values()), owned
        assert len({value["owner_id"] for value in owned.values()}) == len(owned), owned
        denied = await action(primary, "native-wipe-recover", "--target", bag_id, expected=False)
        assert denied["error"] == "native_wipe_bag_not_owned", denied
        after_wipe = (await action(secondary, "native-inventory-list"))["native_inventory"]
        assert [row["guid"] for row in after_wipe["items"]] == ([gun] if keep_slots else []), after_wipe
        if visible_bag:
            viewed = await wait(guest, lambda status: status.get("game", {}).get("native_bag_projection", {}).get("count") == 1)
            bag_projection = viewed["game"]["native_bag_projection"]
            assert bag_projection["owner"] == character and bag_projection["map"] == OBSERVATORY, bag_projection
            if not difficulty:
                assert not revived["game"].get("native_bag_projection", {}).get("count"), revived["game"]
            tested = (await action(secondary, "native-bag-projection-fixture", "--target", bag_id))["native_bag_projection_interaction"]
            assert tested["bag"] == bag_id and tested["entry"] == "fixture", tested
            assert (await action(secondary, "native-inventory-list"))["native_inventory"] == after_wipe
        operation = uuid.uuid4().hex
        recover = ("native-wipe-recover", "--target", bag_id, "--operation-id", operation)
        recovered = await action(secondary, *recover)
        assert recovered["count"] == dropped_count and (await action(secondary, *recover))["native_bag"] == bag_id, recovered
        after_recovery = (await action(secondary, "native-inventory-list"))["native_inventory"]
        assert {row["guid"] for row in after_recovery["items"]} == {gun, reserve}, after_recovery
        proof = {"rest": rest, "retained": after_wipe, "backpack": bag, "recovered": after_recovery,
                 "difficulty": difficulty, "keep_slots": keep_slots}
        if wipe_view:
            proof["native_inventory_selection_input"] = submitted
        if visible_bag:
            await wait(guest, lambda status: status.get("game", {}).get("state") == "connected"
                       and not status["game"].get("native_bag_projection", {}).get("count")
                       and bag_id not in status.get("gameplay", {}).get("native_backpacks", {}))
            proof["owner_only_stock_backpack_projection"] = {"projection": bag_projection, "intercept": tested}
        if authored_pickup:
            await action(primary, "native-authored-pickup-approach")
            await wait(guest, lambda status: status.get("game", {}).get("native_authored_pickup", {}).get("state") == "available")
            preview = (await action(secondary, "native-authored-pickup-fixture"))["native_authored_pickup_interaction"]
            assert preview["source"] == LOOSE_PICKUP and preview["entry"] == "fixture", preview
            assert (await action(secondary, "native-inventory-list"))["native_inventory"] == after_recovery
            claim = ("native-authored-pickup-claim", "--target", LOOSE_PICKUP, "--operation-id", uuid.uuid4().hex)
            award = (await action(secondary, *claim))["native_authored_pickup_claim"]
            assert (await action(secondary, *claim))["native_authored_pickup_claim"] == award
            rival = await action(primary, "native-authored-pickup-claim", "--target", LOOSE_PICKUP, expected=False)
            assert rival["error"] == "native_authored_pickup_already_claimed", rival
            for directory in (host, guest):
                await wait(directory, lambda status: status.get("game", {}).get("native_authored_pickup", {}).get("state") == "claimed")
            changed = (await action(secondary, "native-inventory-list"))["native_inventory"]
            item, = [row for row in changed["items"] if row["guid"] == award["guid"]]
            assert item["class_path"] == award["class_path"] and item["quantity"] == 1, item
            proof["ordinary_authored_stock_pickup"] = {"intercept": preview, "claim": award, "inventory_row": item}
        if stock_rules or difficulty:
            expected_slots = max(0, keep_slots - extraction_fields.get("lose_slot_per_death", 0))
            extraction_fields["items_kept_on_death"] = expected_slots
            await wait(host, lambda status: status.get("game", {}).get("native_rules", {}).get("items_kept_on_death")
                       == expected_slots)
            await action(primary, "stop")
            await asyncio.wait_for(host_process.wait(), 12)
            await start(host / "host-restart.txt", primary, "host", *primary, "--port", str(initial["endpoint"]["port"]),
                        *host_flags)
            resumed = await wait(host, lambda status: status.get("state") == "hosting"
                                 and status.get("generation") != initial["generation"])
            assert all(resumed["game"]["native_rules"].get(key) == value for key, value in extraction_fields.items()), resumed
            proof["stock_rules_after_wipe_and_restart"] = extraction_fields
    finally:
        for process, owner in reversed(processes):
            if process.returncode is None:
                try:
                    await action(owner, "stop")
                    await asyncio.wait_for(process.wait(), 12)
                except (OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 12)
        if vanilla_saved:
            assert saved_hashes(vanilla_saved) == original
    (host / "live-stock-wipe-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(proof), flush=True)


if __name__ == "__main__":
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--work", required=True, type=Path)
    arguments.add_argument("--vanilla-saved", type=Path)
    arguments.add_argument("--visible-bag", action="store_true")
    arguments.add_argument("--authored-pickup", action="store_true")
    arguments.add_argument("--stock-rules", action="store_true")
    arguments.add_argument("--difficulty", choices=("Respawn_WtPenalty", "Decimation"))
    arguments.add_argument("--wipe-view", action="store_true")
    options = arguments.parse_args()
    if options.difficulty and options.stock_rules:
        arguments.error("Choose a stock difficulty preset or custom stock extraction fields")
    if options.difficulty == "Decimation" and options.wipe_view:
        arguments.error("Decimation has no retained item selector")
    asyncio.run(validate(options.work.resolve(), options.vanilla_saved, options.visible_bag, options.authored_pickup,
                         options.stock_rules, options.wipe_view, options.difficulty))
