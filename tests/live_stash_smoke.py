"""Verify private rest-site storage through stock Interact and the in-game stash screen."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.silverfish import control, native_stash
from tools.silverfish.travel import DEAD_BRIDGE


async def verify(work: Path, vanilla_saved: Path):
    name = "stash-screen-" + uuid.uuid4().hex[:8]
    run = work / "campaigns" / name / "run"
    guest_run = work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_args = ("host", "--campaign", name, "--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    original_saves = saved_hashes(vanilla_saved)
    jobs, evidence = [], {}

    async def launch(label, directory, *args):
        with (run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, *args, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        jobs.append((process, directory))
        return process

    async def ask(directory, action, ok=True, **args):
        result = await control.submit(directory, action, **args)
        if bool(result.get("ok")) != ok:
            raise RuntimeError(f"{action} did not return its expected result: {result}")
        return result

    async def stop(process, directory):
        if process.returncode is None:
            await ask(directory, "stop")
        assert await asyncio.wait_for(process.wait(), 20) == 0

    async def join(invite, label):
        process = await launch(label, guest_run, "join", "--profile", name, "--invite", invite["invite_file"],
                               "--headless", "--native-zones")
        await wait(run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        return process

    async def stash_items(directory, site):
        page = (await ask(directory, native_stash.LIST, target=site))[native_stash.RESULT]
        return [row["guid"] for row in page["items"]]

    async def approach(role, site):
        await ask(run, "native-stash-approach-fixture", target=f"{role}@{site}")

    async def render(mode, *, after=0):
        await wait(guest_run, lambda state: (page := state.get("game", {}).get(native_stash.VIEW, {})).get("state")
                   == "rendered" and page["mode"] == mode and page["sequence"] > after)

    async def click(key):
        answer = await ask(guest_run, "native-stash-view-click-fixture", target=key)
        return (answer[native_stash.VIEW] or {}).get("sequence", 0)

    async def move_by_screen(site, item, mode, direction):
        seen = await ask(guest_run, "native-stash-view-fixture", target=site)
        opened = seen[native_stash.VIEW]["sequence"]
        await render("stash", after=opened - 1)
        if mode == "backpack":
            await click("backpack")
            await render(mode, after=opened)
        await click(item)
        await render(mode, after=opened + int(mode == "backpack"))
        events = (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        receipt = next(event for line in reversed(events) if (event := json.loads(line))["event"] == "native_stash_view_item_moved"
                       and event["guid"] == item and event["direction"] == direction)
        await click("close")
        await wait(guest_run, lambda state: not state.get("game", {}).get(native_stash.VIEW))
        target = f"{site}|{direction}|{item}"
        result = await ask(guest_run, native_stash.MOVE, target=target, operation_id=receipt["operation_id"])
        return result[native_stash.MOVED]

    try:
        host = await launch("host", run, *host_args, "--port", "0", "--start-zone", DEAD_BRIDGE)
        state = await wait(run, lambda status: status.get("state") == "hosting"
                           and len(status.get("game", {}).get(native_stash.SITES, {})) >= 2)
        sites = state["game"][native_stash.SITES]
        first = next(site for site, row in sites.items() if row["actor"].startswith("Stash_Large_BP_C "))
        second = next(site for site, row in sites.items() if row["actor"].startswith("Stash_Crate_BP_C "))
        invite = await ask(run, "invite", name="Stash guest", character="Guest")
        guest = await join(invite, "guest")

        await ask(run, "native-stash-setting-fixture", target="0")
        await approach("guest", first)
        item = uuid.uuid4().hex.upper()
        await ask(run, "native-guest-weapon-seed", target="guest", item_guid=item, kind="light-pistol-suppressed")
        deposit = await move_by_screen(first, item, "backpack", "deposit")
        assert (deposit["owner"], deposit["combined"], deposit["guid"], deposit["quantity"]) == (
            invite["character_id"], False, item, 1,
        )
        assert await stash_items(guest_run, first) == [item]
        backpack = (await ask(guest_run, "native-inventory-list"))["native_inventory"]
        assert item not in {row["guid"] for row in backpack["items"]}
        await approach("host", first)
        assert await stash_items(run, first) == []
        denied = await ask(run, native_stash.MOVE, ok=False, target=f"{first}|withdraw|{item}")
        assert denied["error"] in {"native_stash_move_source_or_capacity", "native_stash_move_stored_identity"}
        await approach("guest", second)
        assert await stash_items(guest_run, second) == []

        await ask(run, "native-stash-setting-fixture", target="1")
        assert await stash_items(guest_run, second) == [item]
        await ask(run, "checkpoint")
        await stop(guest, guest_run)
        await stop(host, run)

        host = await launch("host-restart", run, *host_args, "--port", str(state["endpoint"]["port"]))
        await wait(run, lambda status: status.get("state") == "hosting" and status["generation"] != state["generation"]
                   and status.get("game", {}).get(native_stash.SETTING) is True)
        await join(await ask(run, "refresh-invite", target=invite["guest_id"]), "guest-restart")
        await approach("guest", second)
        assert await stash_items(guest_run, second) == [item]
        withdrawal = await move_by_screen(second, item, "stash", "withdraw")
        assert (withdrawal["owner"], withdrawal["combined"], withdrawal["guid"], withdrawal["quantity"]) == (
            invite["character_id"], True, item, 1,
        )
        assert await stash_items(guest_run, second) == []
        weapon = (await ask(guest_run, "native-guest-weapon-inspect", target=item))["native_guest_weapon"]
        assert weapon["stored_ammo"] == 7
        evidence.update(deposit=deposit, withdrawal=withdrawal, magazine=weapon["stored_ammo"],
                        host_withdraw_refused=denied["error"], per_site_other_empty=True, verified=True)
    finally:
        for process, directory in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(process, directory)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        assert saved_hashes(vanilla_saved) == original_saves
        evidence["original_files_verified"] = len(original_saves)
        (run / "stock-stash-transfer-verification.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True)))
