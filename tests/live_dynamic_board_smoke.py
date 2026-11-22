"""Verify an unregistered ShopKeep contract via the actual stock board controls."""

import argparse
import asyncio
import contextlib
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import campaign_save_hashes, saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_board_contract as dynamic, native_computer_board, native_wire_board, travel
from tools.silverfish.native_gold import valid_guid


QUEST = "Clt_Eye_02"
ITEM = "/Game/Classes/Items/Item_MonsterEye.Item_MonsterEye_C"
AREA = travel.SHOPKEEP
BOARD = native_wire_board.BOARDS[AREA]


@contextlib.contextmanager
def staged_probe(work):
    scripts = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishCoop/Scripts"
    main, fixture = scripts / "main.lua", scripts / "eye_board_probe.lua"
    original = main.read_bytes()
    source = Path(__file__).with_name("fixtures") / fixture.name
    with fixture.open("xb") as stream:
        stream.write(source.read_bytes())
    try:
        main.write_bytes(original + b'\nrequire("eye_board_probe")\n')
        yield
    finally:
        main.write_bytes(original)
        fixture.unlink()


async def probe(run, operation):
    bridge, = (run / "game-bridge").iterdir()
    nonce = uuid.uuid4().hex
    (bridge / "eye-board-probe").write_text(f"{operation}|{nonce}\n", encoding="ascii")
    async with asyncio.timeout(8):
        while True:
            await asyncio.sleep(.05)
            try:
                transaction, state, response = (bridge / "eye-board-reply").read_text().split("|", 2)
            except (OSError, ValueError):
                continue
            if transaction == nonce:
                if state != "ok":
                    raise RuntimeError(response)
                return response


async def verify(work, vanilla_saved):
    canaries = saved_hashes(vanilla_saved)
    if not canaries or not (work / "stage").is_dir() or work in Path(vanilla_saved).parents:
        raise ValueError("Supply an isolated staged work directory and singleplayer save canaries")
    assert QUEST not in native_wire_board.QUESTS[AREA] and QUEST not in native_computer_board.CONTRACTS
    name = "eye-board-" + uuid.uuid4().hex[:8]
    root = work / "campaigns" / name
    run, remote = root / "run", work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work)
    host, guest = ("--campaign", name), ("--profile", name)
    jobs = []

    async def action(owner, verb, *arguments):
        return await request("action", *owner, verb, *arguments)

    async def request(*arguments):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            raise RuntimeError(f"{arguments!r}\n{stdout.decode(errors='replace')}\n{stderr.decode(errors='replace')}")
        return json.loads(stdout)

    async def launch(owner, label, *arguments):
        with (run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO,
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, process))

    async def open_native_board():
        pending = (await action(guest, native_wire_board.STOCK_F_FIXTURE))["native_wire_board_stock"]
        assert pending["state"] == "loading" and pending["board"] == BOARD and pending["quest"] == "-", pending
        seen = await wait(remote, lambda frame: (view := frame.get("game", {}).get("native_wire_board_stock", {}))
                          and view["state"] == "visible" and view["sequence"] == pending["sequence"])
        return seen["game"]["native_wire_board_stock"]

    async def inventory(owner):
        return {entry["guid"]: entry for entry in (await action(owner, "native-inventory-list"))["native_inventory"]["items"]}

    async def progress(step):
        authority = await wait(run, lambda frame: frame.get("gameplay", {}).get("native_quests", {})
                               .get(QUEST, {}).get("step") == step)
        return authority["gameplay"]["native_quests"][QUEST]

    with staged_probe(work):
        try:
            await launch(host, "host", "host", *host, "--port", "0", "--start-zone", AREA,
                         "--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones",
                         "--native-expanded-zones")
            await wait(run, lambda state: state.get("state") == "hosting", timeout=70)
            invite = await request("invite", *host, "--name", "Board tester", "--character", "Courier")
            char = invite["character_id"]
            await launch(guest, "guest", "join", *guest, "--headless", "--native-zones", "--invite", invite["invite_file"])
            await wait(run, lambda state: state.get("link_connected") is True
                       and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound", timeout=70)
            await wait(remote, lambda state: state.get("state") == "connected"
                       and state.get("character_id") == char and state.get("game", {}).get("map") == AREA, timeout=70)
            await action(host, native_computer_board.MOLASSES.command("approach"), "--target", char)
            authority = await wait(run, lambda frame: frame.get("game", {}).get("pawns", {}).get("guest", {}).get("location"))
            position = authority["game"]["pawns"]["guest"]["location"]
            await wait(remote, lambda frame: (point := frame.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
                       and math.dist(point, position) <= 25)
            assert await probe(run, "xp") == "5"
            before = campaign_save_hashes(root)
            offer = (await action(guest, dynamic.CATALOG))[dynamic.CATALOG_KEY]
            assert QUEST in offer["offers"] and QUEST not in offer["turnins"], offer
            first_menu = await open_native_board()
            await action(guest, native_wire_board.STOCK_FIXTURE, "--target", f"{QUEST}|start")
            await progress(1)
            await wait(remote, lambda frame: not frame.get("game", {}).get("native_wire_board_stock"))
            without_item = (await action(guest, dynamic.CATALOG))[dynamic.CATALOG_KEY]
            assert QUEST not in without_item["offers"] and QUEST not in without_item["turnins"], without_item

            guid = await probe(run, "eye")
            assert valid_guid(guid)
            item = (await inventory(guest))[guid]
            assert (item["class_path"], item["quantity"], item["source"]) == (ITEM, 1, "backpack"), item
            assert guid not in await inventory(host)
            ready = (await action(guest, dynamic.CATALOG))[dynamic.CATALOG_KEY]
            assert QUEST in ready["turnins"] and QUEST not in ready["offers"], ready
            second_menu = await open_native_board()
            await action(guest, native_wire_board.STOCK_FIXTURE, "--target", f"{QUEST}|turnin")
            paid = await progress(-10)
            assert (paid["cash"], paid["xp"], paid["consumed_guid"], paid["objective_owner"]) == (801, 1, guid, char), paid
            assert paid["wallet"] == 801 and set(paid["recipients"]) == {char, "host"}, paid
            assert guid not in await inventory(host) and guid not in await inventory(guest)
            replay = await action(guest, "native-quest-turnin", "--target", QUEST)
            assert replay["effect"] == "native_quest_already_settled" and replay["wallet"] == 801, replay
            assert campaign_save_hashes(root) == before, "Board actions wrote campaign saves outside a checkpoint"
            evidence = {"verified": True, "menus": [first_menu, second_menu], "ready": ready,
                        "item": item, "receipt": paid, "replay": replay, "deferred_writes": True}
            (run / "dynamic-board-proof.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        finally:
            for owner, process in reversed(jobs):
                if process.returncode is None:
                    with contextlib.suppress(RuntimeError, TimeoutError):
                        await action(owner, "stop")
                    try:
                        await asyncio.wait_for(process.wait(), 20)
                    except TimeoutError:
                        process.terminate()
                        await process.wait()
            assert saved_hashes(vanilla_saved) == canaries


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve()))
