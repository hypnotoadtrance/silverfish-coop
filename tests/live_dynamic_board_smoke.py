"""Verify an unregistered ShopKeep contract via the actual stock board controls."""

import argparse
import asyncio
import contextlib
from hashlib import sha256
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

from tools.silverfish import native_board_contract as dynamic, native_computer_board, native_wire_board, runtime, travel
from tools.silverfish.control import submit
from tools.silverfish.native_gold import valid_guid


QUEST = "Clt_Eye_02"
ITEM = "/Game/Classes/Items/Item_MonsterEye.Item_MonsterEye_C"
AREA = travel.SHOPKEEP
BOARD = native_wire_board.BOARDS[AREA]


@contextlib.contextmanager
def staged_probe(game, module="eye_board_probe"):
    scripts = game / runtime.SHIPPING.parent / runtime.MOD_SCRIPTS
    main, fixture = scripts / "main.lua", scripts / f"{module}.lua"
    original = main.read_bytes()
    manifest = runtime.stage_manifest(game)
    original_manifest = manifest.read_bytes()
    source = Path(__file__).with_name("fixtures") / fixture.name
    with fixture.open("xb") as stream:
        stream.write(source.read_bytes())
    try:
        anchors = (
            b"native_wire_board.sync(state, current_phase)",
            b'status_publication.profile.call("native_wire_board.sync", native_wire_board.sync, state, current_phase)',
        )
        matches = [anchor for anchor in anchors if original.count(anchor) == 1]
        if len(matches) != 1:
            raise ValueError("The staged board tick is unavailable for this fixture")
        anchor, = matches
        hook = f'\n        require("{module}").sync(state, current_phase)'.encode("ascii")
        runtime.write_text(main, original.replace(anchor, anchor + hook).decode("utf-8"), newline="")
        staged = json.loads(original_manifest)
        if "lua_files" in staged:
            for path in (main, fixture):
                staged["lua_files"][path.name] = sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            runtime.write_text(manifest, json.dumps(staged) + "\n", newline="")
        yield
    finally:
        runtime.write_text(main, original.decode("utf-8"), newline="")
        runtime.write_text(manifest, original_manifest.decode("utf-8"), newline="")
        fixture.unlink()


async def probe(bridge, operation, *, prefix="eye-board", timeout=8):
    nonce = uuid.uuid4().hex
    runtime.write_text(bridge / f"{prefix}-probe", f"{operation}|{nonce}\n", newline="")
    async with asyncio.timeout(timeout):
        while True:
            await asyncio.sleep(.05)
            try:
                transaction, state, response = (bridge / f"{prefix}-reply").read_text().split("|", 2)
            except (OSError, ValueError):
                continue
            if transaction == nonce:
                if state != "ok":
                    raise RuntimeError(response)
                return response


async def verify_eye_board(run, remote, bridge, character, *, wait_status=wait, mounted_board=None):
    host, guest = run, remote

    async def action(directory, verb, **fields):
        reply = await submit(directory, verb, **fields)
        assert reply["ok"], (verb, reply)
        return reply

    async def open_native_board():
        pending = (await action(guest, native_wire_board.STOCK_F_FIXTURE, target=board))["native_wire_board_stock"]
        assert pending["state"] == "loading" and pending["board"] == board and pending["quest"] == "-", pending
        seen = await wait_status(remote, lambda frame: (view := frame.get("game", {}).get("native_wire_board_stock", {}))
                          and view["state"] == "visible" and view["sequence"] == pending["sequence"])
        return seen["game"]["native_wire_board_stock"]

    async def inventory(owner):
        return {entry["guid"]: entry for entry in (await action(owner, "native-inventory-list"))["native_inventory"]["items"]}

    async def progress(step):
        authority = await wait_status(run, lambda frame: frame.get("gameplay", {}).get("native_quests", {})
                               .get(QUEST, {}).get("step") == step)
        return authority["gameplay"]["native_quests"][QUEST]

    board = mounted_board or BOARD
    if not mounted_board:
        selected_board, *coordinates = (await probe(bridge, "approachturnin")).split(";")
        assert selected_board == board and len(coordinates) == 3, (selected_board, coordinates)
        position = tuple(map(float, coordinates))
        await wait_status(remote, lambda frame: (point := frame.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
                   and math.dist(point, position) <= 25)
    assert await probe(bridge, "xp") == "5"
    before = campaign_save_hashes(run.parent)
    current = await wait_status(run, lambda frame: type(frame.get("game", {}).get("native_wallet")) is int)
    wallet = current["game"]["native_wallet"]
    offer = (await action(guest, dynamic.CATALOG, target=board))[dynamic.CATALOG_KEY]
    assert QUEST in offer["offers"] and QUEST not in offer["turnins"], offer
    first_menu = await open_native_board()
    await action(guest, native_wire_board.STOCK_FIXTURE, target=f"{QUEST}|start")
    await progress(1)
    await wait_status(remote, lambda frame: not frame.get("game", {}).get("native_wire_board_stock"))
    without_item = (await action(guest, dynamic.CATALOG, target=board))[dynamic.CATALOG_KEY]
    assert QUEST not in without_item["offers"] and QUEST not in without_item["turnins"], without_item

    guid, spare_guid, host_guid = (await probe(bridge, "eye")).split(";")
    assert len({guid, spare_guid, host_guid}) == 3 and all(map(valid_guid, (guid, spare_guid, host_guid)))
    before_pack = await inventory(guest)
    item, spare = before_pack[guid], before_pack[spare_guid]
    assert (item["class_path"], item["quantity"], item["source"], item["stackable"]) == (ITEM, 2, "backpack", True), item
    assert (spare["class_path"], spare["quantity"], spare["source"], spare["stackable"]) == (ITEM, 1, "backpack", True), spare
    original_host_pack = await inventory(host)
    assert not {guid, spare_guid} & original_host_pack.keys() and host_guid not in before_pack
    host_stack = original_host_pack[host_guid]
    assert (host_stack["class_path"], host_stack["quantity"], host_stack["source"]) == (ITEM, 3, "backpack"), host_stack
    ready = (await action(guest, dynamic.CATALOG, target=board))[dynamic.CATALOG_KEY]
    assert QUEST in ready["turnins"] and QUEST not in ready["offers"], ready
    second_menu = await open_native_board()
    await action(guest, native_wire_board.STOCK_FIXTURE, target=f"{QUEST}|turnin")
    paid = await progress(-10)
    assert (paid["cash"], paid["xp"], paid["objective_guid"], paid["objective_owner"]) == (801, 1, guid, character), paid
    assert (paid["objective_units_before"], paid["objective_units_consumed"], paid["objective_units_remaining"]) == (2, 1, 1), paid
    consumed = paid["consumed_guid"]
    assert valid_guid(consumed) and consumed != guid, paid
    assert paid["wallet"] == wallet + 801 and set(paid["recipients"]) == {character, "host"}, paid
    host_pack, guest_pack = await inventory(host), await inventory(guest)
    assert host_pack == original_host_pack and consumed not in guest_pack
    remaining = guest_pack[guid]
    assert remaining == {**item, "quantity": 1}, (item, remaining)
    assert guest_pack[spare_guid] == spare
    visit = (await wait_status(remote, lambda frame: frame.get("zone_epoch")))["zone_epoch"]
    replay = await action(guest, "native-quest-turnin", target=dynamic.menu_target(visit, board, QUEST))
    assert replay["effect"] == "native_quest_already_settled" and replay["wallet"] == paid["wallet"], replay
    after_replay = await inventory(guest)
    assert after_replay[guid] == remaining and after_replay[spare_guid] == spare
    assert campaign_save_hashes(run.parent) == before, "Board actions wrote campaign saves outside a checkpoint"
    evidence = {"verified": True, "menus": [first_menu, second_menu], "ready": ready,
                "item": item, "remainder": remaining, "untouched_eye_stack": spare, "untouched_host_stack": host_stack,
                "receipt": paid, "replay": replay, "deferred_writes": True}
    return evidence


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

    with staged_probe(work / "stage"):
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
            bridge, = (run / "game-bridge").iterdir()
            evidence = await verify_eye_board(run, remote, bridge, char)
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
