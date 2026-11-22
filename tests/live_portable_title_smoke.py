"""Cold-start two private replicas with the drag-and-drop co-op release."""

import argparse
import asyncio
from contextlib import ExitStack
from dataclasses import dataclass
from itertools import combinations
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap import install_stage
from tools.silverfish import (
    embedded_startup, native_guest_consumable, native_guest_drop, native_host_save, native_inventory, native_loose_pickup,
    native_loot, native_world_loot, runtime,
)
from tools.silverfish.authored_loot import GUARANTEED_CRATES
from tools.silverfish.control import NAME, submit
from tools.silverfish.service import menu_host_checkpoint
from tools.silverfish.steam_cloud import FOLDER
from tools.silverfish.win_job import GameProcess
from live_installed_title_smoke import boot_states, native_mailbox
from live_loose_pickup_smoke import DEFAULT_ACTOR
from live_ai_smoke import wait
from live_multi_smoke import saved_hashes
from live_resident_host_smoke import stock_profile, stock_save_hashes
from live_same_process_listener_smoke import choose_ports
from live_stock_creator_smoke import creator_action, installed_probe
from live_stock_pause_exit_smoke import stock_pause
from live_unified_title_smoke import Seat, offered, start_pair


@dataclass(frozen=True)
class PortableRelease:
    package: Path
    manifest_sha256: str
    title_directory: Path
    selection: dict
    activation: dict[Path, str | None]


def launch_portable(game: Path, profile: Path) -> GameProcess:
    image = game / runtime.SHIPPING
    install_stage._ordinary(image)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("SILVERFISH_COOP_")}
    arguments = subprocess.list2cmdline([str(image)])
    arguments += (f' -SilverfishCoopIsolated -userdir="{profile}" -unattended -NoSplash -windowed'
                  " -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480"
                  " -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,"
                  "[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock")
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return GameProcess(arguments, executable=image, cwd=image.parent, env=env, startupinfo=info)


def fingerprint(path: Path) -> str | None:
    if not os.path.lexists(path):
        return None
    install_stage._ordinary(path)
    return runtime._file_sha256(path)


def qualify_release(game: Path, legacy_work: Path | None) -> PortableRelease:
    mods = game / runtime.SHIPPING.parent / "ue4ss" / "Mods"
    embedded = mods / install_stage.MOD
    marker = embedded / "resident-portable.json"
    descriptor = embedded / "resident-install.json"
    stage = game / runtime.PORTABLE_STAGE_MANIFEST
    activation = {
        mods / "mods.txt": fingerprint(mods / "mods.txt"),
        descriptor: fingerprint(descriptor),
        marker: fingerprint(marker),
        stage: fingerprint(stage),
    }
    if activation[marker] is None or activation[stage] is None:
        raise ValueError("Apply the release ZIP over the stock replica before running this test")
    for module in ("SilverfishCoop", runtime.CPP_MOD_NAME, install_stage.MOD):
        enabled = mods / module / "enabled.txt"
        install_stage._ordinary(enabled)
        activation[enabled] = fingerprint(enabled)
    metadata = runtime.read_json(marker, limit=8192)
    if metadata["schema"] != 1 or runtime._file_sha256(game / runtime.SHIPPING) != metadata["shipping_sha256"]:
        raise ValueError("The portable release does not match the shipping executable")

    addon = embedded / "dlls" / "main.dll"
    install_stage._ordinary(addon)
    image = addon.read_bytes()
    pinned = []
    for candidate in embedded.glob("runtime-*"):
        if re.fullmatch(r"runtime-[0-9a-f]{16}", candidate.name) and candidate.is_dir():
            digest = fingerprint(candidate / "runtime-manifest.json")
            if digest is not None and digest.encode("ascii") in image:
                manifest, _, _ = install_stage._manifest(candidate, addon)
                if candidate.name == "runtime-" + manifest[:16]:
                    pinned.append((candidate, manifest))
    if len(pinned) != 1:
        raise ValueError("The DLL must pin exactly one qualified versioned embedded runtime")
    package, digest = pinned[0]

    boot = {"schema": 1, "campaign": "default", "bind": "127.0.0.1", "port": 7777}
    seat_name = "local"
    if legacy_work is None:
        if activation[descriptor] is not None:
            raise ValueError("A clean portable replica must have no resident-install.json")
    else:
        if activation[descriptor] is None:
            raise ValueError("The upgrade replica needs its original isolated resident-install.json")
        legacy = runtime.read_json(descriptor, limit=8192)
        if (legacy.get("schema"), legacy.get("isolated"), legacy.get("shipping_sha256")) != (
                1, True, metadata["shipping_sha256"]
        ) or Path(legacy["work"]) != legacy_work or not NAME.fullmatch(legacy["seat"]):
            raise ValueError("The original upgrade descriptor must point at the supplied private isolated workspace")
        seat_name = legacy["seat"]
        boot.update({key: legacy[key] for key in ("campaign", "bind", "port")})
    return PortableRelease(package, digest, Path("titles") / seat_name, boot, activation)


async def discover_mailbox(root: Path, process: GameProcess) -> Path:
    async with asyncio.timeout(30):
        while process.poll() is None and not root.is_dir():
            await asyncio.sleep(.1)
        if process.poll() is not None:
            raise RuntimeError(f"The shipping executable exited before creating its portable mailbox ({process.returncode})")
        return await native_mailbox(root, process)


async def verify_gameplay(host: Seat, guest: Seat, pawn: str, *, stock_drop: bool = False,
                          stock_split: bool = False, stock_equip: bool = False, stock_nearby: bool = False,
                          projected_nearby: bool = False) -> dict:
    def qualified(game):
        return (game.get("native_enemy_loot_cqc_version") == 3
                and game.get("native_guest_regen", {}).get(pawn, {}).get("state") in {"applied", "anchored", "paused_game_clock"}
                and "guest_sickness" in game.get("native_guest_sickness", {}).get(pawn, {}))

    game = await host.bridge.wait_for(qualified)
    heartbeat = guest.bridge.directory / "native-shop-view-heartbeat"
    async with asyncio.timeout(10):
        while not heartbeat.is_file():
            await asyncio.sleep(.1)
    assert 0 <= int(time.time()) - int(heartbeat.read_text(encoding="ascii")) <= 5
    await guest.bridge.wait_for(lambda frame: frame.get("native_inventory_view_binding") == "ready")
    guest_run = guest.client / "run"
    async with asyncio.timeout(12):
        while True:
            try:
                events = (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if any(json.loads(line).get("event") == "native_guest_ui_projected" for line in events):
                    break
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                pass
            await asyncio.sleep(.15)
    preview = await submit(guest_run, "native-inventory-view-fixture", target="stock")
    assert preview["ok"] and re.fullmatch(r"verified stock guest inventory with \d+ backpack cards",
                                          preview.get("stock_inventory_proof", "")), preview
    stock_drop_proof, stock_sort_proof, stock_use_proofs = None, None, {}
    if stock_drop:
        before = await submit(guest_run, "native-inventory-list")
        host_run = host.campaign / "run"
        host_before = await submit(host_run, "native-inventory-list")
        assert before["ok"] and host_before["ok"], (before, host_before)
        sorted_pack = await submit(guest_run, native_guest_drop.STOCK_SORT_FIXTURE)
        assert sorted_pack["ok"], sorted_pack
        stock_sort_proof = sorted_pack[native_inventory.SORT_KEY]
        assert re.fullmatch(r"verified open stock backpack sort with \d+ cards",
                            sorted_pack.get("stock_inventory_proof", "")), sorted_pack
        contents = await submit(guest_run, "native-inventory-list")
        assert contents["ok"], contents
        original_rows, sorted_rows = (part["native_inventory"]["items"] for part in (before, contents))
        assert stock_sort_proof["count"] == sum(row["source"] == "backpack" for row in sorted_rows)
        original_payload = {row["guid"]: {key: value for key, value in row.items() if key != "index"} for row in original_rows}
        sorted_payload = {row["guid"]: {key: value for key, value in row.items() if key != "index"} for row in sorted_rows}
        assert sorted_payload == original_payload
        assert (original_rows != sorted_rows) == (stock_sort_proof["state"] == "sorted")
        host_after = await submit(host_run, "native-inventory-list")
        assert host_after["ok"] and host_before["native_inventory"] == host_after["native_inventory"], host_after
        selected = next((row for row in sorted_rows if row["class_path"] not in native_guest_consumable.COMMANDS
                         and native_guest_drop.droppable(row, generic=True)), None)
        assert selected is not None, contents
        dropped = await submit(guest_run, native_guest_drop.STOCK_DROP_FIXTURE, target=selected["guid"])
        assert dropped["ok"], dropped
        assert re.fullmatch(r"verified open stock backpack refresh with \d+ cards",
                            dropped.get("stock_inventory_proof", "")), dropped
        stock_drop_proof = dropped[native_guest_drop.RESULT]
        assert (stock_drop_proof["guid"], stock_drop_proof["class_path"], stock_drop_proof["quantity"]) == (
            selected["guid"], selected["class_path"], selected["quantity"],
        ), dropped
        identity = native_world_loot.player_drop_identity(stock_drop_proof["drop_id"])
        for seat in (host, guest):
            await seat.bridge.wait_for(lambda frame: identity in frame.get(native_world_loot.KEY, {}))
        contents = await submit(guest_run, "native-inventory-list")
        assert contents["ok"] and selected["guid"] not in {
            row["guid"] for row in contents["native_inventory"]["items"]
        }, contents
        for class_path in (native_guest_consumable.BANDAGE_CLASS, native_guest_consumable.MRE_CLASS):
            selected = next((row for row in contents["native_inventory"]["items"]
                             if row["class_path"] == class_path and row["source"] == "backpack"), None)
            assert selected is not None, contents
            used = await submit(guest_run, native_guest_drop.STOCK_USE_FIXTURE, target=selected["guid"])
            assert used["ok"], used
            assert re.fullmatch(r"verified open stock backpack refresh with \d+ cards",
                                used.get("stock_inventory_proof", "")), used
            key = native_guest_consumable.RESULT[native_guest_consumable.COMMANDS[class_path]]
            proof = stock_use_proofs[key] = used[key]
            assert (proof["guid"], proof["state"], proof["quantity_before"], proof["quantity_after"]) == (
                selected["guid"], "used", selected["quantity"], selected["quantity"] - 1,
            ), used
            contents = await submit(guest_run, "native-inventory-list")
            assert contents["ok"], contents
            remaining = [row["quantity"] for row in contents["native_inventory"]["items"]
                         if row["guid"] == selected["guid"]]
            assert remaining == ([proof["quantity_after"]] if proof["quantity_after"] else []), contents
    split_proof = None
    if stock_split:
        before = await submit(guest_run, "native-inventory-list")
        host_run = host.campaign / "run"
        host_before = await submit(host_run, "native-inventory-list")
        assert before["ok"] and host_before["ok"], (before, host_before)
        inventory = before["native_inventory"]
        stack = next((row for row in inventory["items"] if native_inventory.splittable(row)), None)
        assert stack is not None, inventory
        guid, quantity = stack["guid"], stack["quantity"]
        take = min(2, quantity - 1)
        stale = await submit(guest_run, native_guest_drop.STOCK_SPLIT_FIXTURE, target=f"{guid}|{take}|{quantity + 1}")
        assert not stale["ok"] and "stack changed" in stale["error"], stale
        assert (await submit(guest_run, "native-inventory-list"))["native_inventory"] == inventory
        split = await submit(guest_run, native_guest_drop.STOCK_SPLIT_FIXTURE, target=f"{guid}|{take}|{quantity}")
        assert split["ok"] and re.fullmatch(r"verified two stock stacks in an open backpack with \d+ cards",
                                           split.get("stock_inventory_proof", "")), split
        split_proof = split[native_inventory.SPLIT_KEY]
        after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        by_guid = {row["guid"]: row for row in after["items"]}
        new_guid = split_proof["new_guid"]
        assert (by_guid[guid]["quantity"], by_guid[new_guid]["quantity"]) == (quantity - take, take), after
        assert by_guid[guid]["class_path"] == by_guid[new_guid]["class_path"] == stack["class_path"], after
        assert len(after["items"]) == len(inventory["items"]) + 1, after
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before["native_inventory"]
    equip_proof = None
    if stock_equip:
        before = await submit(guest_run, "native-inventory-list")
        host_run = host.campaign / "run"
        host_before = await submit(host_run, "native-inventory-list")
        assert before["ok"] and host_before["ok"], (before, host_before)
        inventory = before["native_inventory"]
        shotgun = "/Game/Classes/Weapons/Wep_BoltAction_Shotgun.Wep_BoltAction_Shotgun_C"
        item = next((row for row in inventory["items"] if row["class_path"] == shotgun and row["source"] == "backpack"), None)
        assert item is not None, inventory
        equipped = await submit(guest_run, native_guest_drop.STOCK_EQUIP_FIXTURE, target=item["guid"])
        assert equipped["ok"], equipped
        equip_proof = equipped[native_inventory.STORE_KEY]
        assert equipped["stock_inventory_proof"] == f"verified stock equipment slot {equip_proof['slot']}"
        after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        row, = (row for row in after["items"] if row["guid"] == item["guid"])
        assert row == dict(item, source="equipment", index=equip_proof["slot"], droppable=False), (row, item)
        assert {row["guid"] for row in after["items"]} == {row["guid"] for row in inventory["items"]}
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before["native_inventory"]
    if stock_nearby:
        host_run = host.campaign / "run"
        target = f"loose:{DEFAULT_ACTOR}"
        host_pawn = game["pawns"]["local"]["name"]
        for participant in (host_pawn, pawn):
            approached = await host.bridge.command(native_loose_pickup.APPROACH, DEFAULT_ACTOR, participant, timeout=5)
            assert approached.get("ack", {}).get("message") == "shared level pickup approach verified", approached["ack"]
        def bolt_in_reach(state):
            game = state.get("game", {})
            item = game.get(native_loose_pickup.KEY, {}).get(DEFAULT_ACTOR, {})
            origin = game.get("pawns", {}).get("local", {}).get("location")
            return item.get("state") == "available" and origin and math.dist(item["location"], origin) <= 250
        for run in (host_run, guest_run):
            await wait(run, bolt_in_reach)
        host_before = (await submit(host_run, "native-inventory-list"))["native_inventory"]
    dropped_actor = None
    if projected_nearby:
        if stock_split:
            picked = await submit(guest_run, native_guest_drop.STOCK_DROP_FIXTURE, target=split_proof["source_guid"])
            assert picked["ok"], picked
            stock_drop_proof = picked[native_guest_drop.RESULT]
            assert stock_drop_proof["quantity"] == split_proof["remaining"] > 1, picked
        identity = native_world_loot.player_drop_identity(stock_drop_proof["drop_id"])
        await host.bridge.wait_for(lambda state: identity in state.get(native_world_loot.KEY, {}))
        approached = await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=5)
        assert approached["ack"]["message"] == "stock loot height and line of sight verified", approached["ack"]
        frame = await guest.bridge.wait_for(lambda state: identity in state.get(native_world_loot.KEY, {})
            and math.dist(state["pawns"]["local"]["location"], stock_drop_proof["location"]) < 225)
        dropped_actor = frame[native_world_loot.KEY][identity]["actor"].split(" ", 1)[1]
    requested = await submit(guest_run, "native-inventory-view-fixture")
    assert requested["ok"], requested
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    nearby_proof = None
    if stock_nearby:
        clicked = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=target)
        assert clicked["ok"] and clicked["proof"] == "stock Nearby Items card requested", clicked
        for run in (host_run, guest_run):
            await wait(run, lambda state: state.get("game", {}).get(native_loose_pickup.KEY, {})
                       .get(DEFAULT_ACTOR, {}).get("state") == "claimed")
        events = [json.loads(line) for line in (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        nearby_proof, = [event[native_loose_pickup.PROOF] for event in events
                         if event.get(native_loose_pickup.PROOF, {}).get("actor") == DEFAULT_ACTOR]
        display = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=f"{target}@{nearby_proof['guid']}")
        assert display["ok"] and display["proof"] == "verified stock Nearby Items transfer", display
        inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        assert inventory["owner"] == nearby_proof["owner"], nearby_proof
        matching = [row for row in inventory["items"] if row["guid"] == nearby_proof["guid"]]
        assert [(row["class_path"], row["quantity"]) for row in matching] == [(nearby_proof["class_path"], 1)], matching
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before
        duplicate = await submit(host_run, native_loose_pickup.CLAIM, target=target)
        assert duplicate.get("error") == "loose_pickup_already_claimed", duplicate
    drop_reclaim_proof = None
    if projected_nearby:
        clicked = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=dropped_actor)
        count = stock_drop_proof["quantity"]
        expected = "stock Nearby Items card requested" + (f" x{count}" if count > 1 else "")
        assert clicked["ok"] and clicked["proof"] == expected, clicked
        for run in (host_run, guest_run):
            await wait(run, lambda state: identity not in state.get("game", {}).get(native_world_loot.KEY, {})
                and stock_drop_proof["drop_id"] not in state.get("gameplay", {}).get("native_guest_stack_drops", {}))
        events = [json.loads(line) for line in (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        drop_reclaim_proof, = [row for event in events if (row := event.get(native_guest_drop.RESULT))
                               and row.get("state") == "reclaimed" and row.get("drop_id") == stock_drop_proof["drop_id"]]
        assert (drop_reclaim_proof["guid"], drop_reclaim_proof["quantity"]) == (stock_drop_proof["guid"], count)
        display = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE,
                               target=f"{dropped_actor}@{drop_reclaim_proof['guid']}")
        assert display["ok"] and display["proof"] == "verified stock Nearby Items transfer", display
        guest_inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        assert drop_reclaim_proof["claimed_by"] == guest_inventory["owner"]
        returned = [row["quantity"] for row in guest_inventory["items"] if row["guid"] == drop_reclaim_proof["guid"]]
        assert returned == [count], guest_inventory
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before["native_inventory"]
    normal_tab_proof = None
    if stock_drop:
        before = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        selected = next((row for row in before["items"] if row["source"] == "backpack"
                         and row["class_path"] == native_guest_consumable.BANDAGE_CLASS), None)
        assert selected is not None, before
        clicked = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=selected["guid"])
        assert clicked["ok"] and clicked["stock_action"]["guid"] == selected["guid"], clicked
        await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock"
                   and "native_inventory_stock_action" not in state.get("game", {}))
        after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        remaining = [row["quantity"] for row in after["items"] if row["guid"] == selected["guid"]]
        assert remaining == ([selected["quantity"] - 1] if selected["quantity"] > 1 else []), after
        normal_tab_proof = {"guid": selected["guid"], "remaining": selected["quantity"] - 1}
    requested = await submit(guest_run, "native-inventory-view-fixture")
    assert requested["ok"], requested
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    return {"placed_enemy_loot_bridge": game["native_enemy_loot_cqc_version"],
            "guest_regeneration_and_sickness_bound": True, "guest_shop_heartbeat": True,
            "guest_inventory_action_verified": True, "guest_inventory_projection_verified": True,
            **({"guest_stock_nearby_pickup": nearby_proof} if nearby_proof else {}),
            **({"guest_stock_nearby_drop_reclaim": drop_reclaim_proof} if drop_reclaim_proof else {}),
            **({"normal_tab_use": normal_tab_proof} if normal_tab_proof else {}),
            **({"guest_stock_menu_split": split_proof} if split_proof else {}),
            **({"guest_stock_menu_equip": equip_proof} if equip_proof else {}),
            **({"guest_stock_menu_drop": stock_drop_proof, "guest_stock_menu_use": stock_use_proofs,
                "guest_stock_menu_sort": stock_sort_proof}
               if stock_drop_proof else {})}


async def verify_container_pickup(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    game = await host.bridge.wait_for(lambda frame: frame.get("state") == "hosting")
    area = game["map"]
    crate, = [name for name in GUARANTEED_CRATES if native_loot.CRATES[name] == area
              and name.startswith("LootCrate_Small_BP_C ")]
    host_pawn = game["pawns"]["local"]["name"]
    await host.bridge.command("native-loot-approach", host_pawn, crate, timeout=5)
    opened = await submit(host_run, "native-loot-open", target=crate)
    assert opened["ok"], opened
    roll = opened["native_loot_roll"]
    item = next((row for row in roll["items"] if row["class_path"] not in native_loot.CASH_RANGES), None)
    assert item is not None and "ammo" in item, roll
    identity = uuid.uuid5(uuid.NAMESPACE_OID, f"{roll['id']}:{item['guid']}").hex
    await host.bridge.wait_for(lambda frame: identity in frame.get(native_world_loot.KEY, {}))
    approached = await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=5)
    assert approached["ack"]["message"] == "stock loot height and line of sight verified", approached["ack"]
    seen = await guest.bridge.wait_for(lambda frame: identity in frame.get(native_world_loot.KEY, {})
        and math.dist(frame["pawns"]["local"]["location"], item["transform"]["position"]) < 225)
    actor = seen[native_world_loot.KEY][identity]["actor"].split(" ", 1)[1]
    host_before = (await submit(host_run, "native-inventory-list"))["native_inventory"]

    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    clicked = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=actor)
    assert clicked["ok"] and clicked["proof"].startswith("stock Nearby Items card requested"), clicked

    def settled(state):
        rows = state.get("gameplay", {}).get(native_loot.STATE_KEY, {}).get(crate, {}).get("items", ())
        return identity not in state.get("game", {}).get(native_world_loot.KEY, {}) and any(
            row["guid"] == item["guid"] and row.get("claimed_by") for row in rows)

    await wait(host_run, settled)
    await wait(guest_run, settled)
    display = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=f"{actor}@{item['guid']}")
    assert display["ok"] and display["proof"] == "verified stock Nearby Items transfer", display
    inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    received, = [row for row in inventory["items"] if row["guid"] == item["guid"]]
    assert received["class_path"] == item["class_path"], received
    if received["stackable"]:
        assert received["quantity"] == item["ammo"], received
        if received["quantity"] > 1:
            assert clicked["proof"] == f"stock Nearby Items card requested x{received['quantity']}", clicked
    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before
    duplicate = await submit(host_run, "native-loot-claim", target=f"{item['guid']}@{crate}")
    assert duplicate.get("error") == "native_loot_already_claimed", duplicate
    saved = json.loads((host.campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
    native_host_save.verify_checkpoint(host.profile, saved)
    durable = saved["world"]["party"][native_loot.STATE_KEY][crate]
    committed, = [row for row in durable["items"] if row["guid"] == item["guid"]]
    assert committed == {**item, "claimed_by": inventory["owner"]}, committed
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    return {"crate": crate, "roll": roll["id"], "item": committed, "rendered": clicked["proof"], "received": received,
            "duplicate": duplicate["error"]}


async def create_stock_drop_character(guest, join, name):
    await join("create-editor", name)
    await guest.bridge.wait_for(lambda frame: frame.get("native_guest_stock_creator", {}).get("state") == "creating")
    await creator_action(guest, "guestkit")
    assert await creator_action(guest, "guestnext") == "page=3"
    await creator_action(guest, "finish")
    await offered(guest)


async def create_stock_split_character(guest, join, name):
    await join("create-editor", name)
    await guest.bridge.wait_for(lambda frame: frame.get("native_guest_stock_creator", {}).get("state") == "creating")
    assert await creator_action(guest, "preset") == "page=4"
    assert (await creator_action(guest, "veteran")).startswith("class=Veteran;")
    assert await creator_action(guest, "next") == "page=3"
    await creator_action(guest, "finish")
    await offered(guest)


async def verify_cloud_restore(first, second, session):
    campaign_id = uuid.UUID(session["campaign_id"]).hex
    source = first.profile / "Saved/SaveGames" / FOLDER / f"SFCoopBackup-{campaign_id}.sav"
    assert source.is_file(), "The installed title did not back up the committed host campaign"
    name = f"campaign-{campaign_id}"
    directory = second.campaign.parent / name
    destination = second.profile / "Saved/SaveGames" / FOLDER / source.name
    destination.parent.mkdir(exist_ok=True)
    assert not directory.exists() and not destination.exists()
    shutil.copy2(source, destination)  # Stand in for Steam's download on a second PC.
    solo = stock_save_hashes(second.profile)
    solo.pop("Config.sav", None)
    preferences = await stock_profile(second.bridge)
    form = "native-title-host-form-fixture"

    async def pick_restored():
        await second.bridge.command(form, "open")
        async with asyncio.timeout(12):
            while not (second.bridge.directory / "title-host-campaigns.tsv").is_file():
                await asyncio.sleep(.1)
        await second.bridge.command(form, "browse")
        for _ in range(3):
            observed = (await second.bridge.command(form, "saved-state"))["ack"]["message"]
            if observed.endswith(":" + name):
                return
            await second.bridge.command(form, "next")
        raise AssertionError("The downloaded backup is missing from Host Load Game")

    def checkpoint():
        return menu_host_checkpoint(directory, profile=second.profile, require_scoped=True)

    await pick_restored()
    restored = checkpoint()
    assert restored["campaign_id"] == session["campaign_id"] and restored["revision"] == session["revision"]
    character = restored["characters"][session["character_id"]]
    assert character["state"]["native_guest_save"]
    await second.bridge.command(form, "choose-saved")
    await second.bridge.command(form, "port", str(second.port))
    await second.bridge.command(form, "submit")
    hosted = await wait(directory / "run", lambda frame: frame.get("state") == "hosting")
    assert hosted["campaign"]["campaign_id"] == restored["campaign_id"]
    assert hosted["game"]["save_scope"] == campaign_id
    await second.bridge.wait_for(lambda game: game.get("stock_pause_exit", {}).get("state") == "armed")
    await stock_pause(second.bridge, "open")
    await stock_pause(second.bridge, "save_and_quit")
    await wait(directory / "run", lambda frame: frame.get("state") == "stopped")
    await offered(second)
    newer = checkpoint()
    assert newer["revision"] > restored["revision"]
    assert destination.read_bytes() != source.read_bytes(), "The resumed host's backup was not refreshed"

    shutil.copy2(source, destination)
    await pick_restored()
    assert checkpoint() == newer, "The old cloud backup replaced a newer local campaign"
    await second.bridge.command(form, "cancel")
    await second.bridge.command(form, "cancel")
    observed_solo = stock_save_hashes(second.profile)
    observed_solo.pop("Config.sav", None)
    assert observed_solo == solo and await stock_profile(second.bridge) == preferences
    return {"restored_revision": restored["revision"], "retained_revision": newer["revision"],
            "guest_character_restored": session["character_id"], "singleplayer_unchanged": True,
            "steam_download_simulated": True}


async def verify(work: Path, first_game: Path, second_game: Path, original: Path,
                 second_legacy_work: Path | None = None, *, stock_drop: bool = False, stock_split: bool = False,
                 stock_equip: bool = False, stock_nearby: bool = False, projected_nearby: bool = False,
                 container_nearby: bool = False, steam_cloud: bool = False) -> None:
    roots = (work, first_game, second_game, original)
    if second_legacy_work is not None:
        roots += (second_legacy_work,)
    if (os.name != "nt" or any("steamapps" in {part.casefold() for part in path.parts} for path in roots if path != original)
            or any(a.is_relative_to(b) or b.is_relative_to(a) for a, b in combinations(roots, 2))):
        raise ValueError("Use separate private replica and work directories outside the original saves")
    before = saved_hashes(original)
    stock_slots = sorted((original / "SaveGames").glob("*.sav"))
    if not before or len(stock_slots) < 2 or not any(path.name == "Config.sav" for path in stock_slots):
        raise ValueError("Supply the original Saved directory with a stock configuration and at least one campaign slot")
    qualified = (qualify_release(first_game, None), qualify_release(second_game, second_legacy_work))
    if qualified[0].manifest_sha256 != qualified[1].manifest_sha256:
        raise ValueError("Both replicas must use the same portable release")
    if qualified[0].activation[first_game / runtime.SHIPPING.parent / "ue4ss/Mods/mods.txt"] is not None:
        raise ValueError("The first replica must have no mods.txt")
    if os.path.lexists(first_game / runtime.STAGE_MANIFEST):
        raise ValueError("The clean replica must start from the contents of the SilverFish folder alone")

    case = work / ("portable-title-" + uuid.uuid4().hex[:8])
    case.mkdir()
    print(case, flush=True)
    workspaces = case / "first", second_legacy_work or case / "second"
    games = first_game, second_game
    proof = {"verified": False, "native_bridges": [], "sessions": []}
    processes, seats, baselines = [], [], []
    probes = ExitStack()

    try:
        if stock_drop or stock_split or stock_equip or stock_nearby or container_nearby:
            for game in games:
                probes.enter_context(installed_probe(game))
        for game, workspace, release, port in zip(games, workspaces, qualified, choose_ports(), strict=True):
            title = workspace / release.title_directory
            profile, mailbox_root = title / "game-user", title / "run/game-bridge"
            if mailbox_root.is_dir() and any(mailbox_root.iterdir()):
                raise ValueError("Use an unused isolated title configuration for the upgrade test")
            if os.path.lexists(mailbox_root):
                install_stage._ordinary(mailbox_root, directory=True)
            stock = profile / "Saved/SaveGames"
            stock.mkdir(parents=True, exist_ok=True)
            install_stage._ordinary(stock, directory=True)
            for source in stock_slots:
                destination = stock / source.name
                if not os.path.lexists(destination):
                    shutil.copy2(source, destination)
                install_stage._ordinary(destination)
            hashes = stock_save_hashes(profile)
            del hashes["Config.sav"]

            process = launch_portable(game, profile)
            processes.append((game, process))
            mailbox = await discover_mailbox(mailbox_root, process)
            assert runtime.read_json(mailbox / embedded_startup.UNIFIED_FILE) == release.selection
            campaign = "title-host-settings-" + uuid.uuid4().hex[:8]
            seat = Seat(profile, workspace / "campaigns" / campaign, workspace / "clients" / title.name,
                        port, runtime.Bridge(mailbox, process))
            seats.append(seat)
            await offered(seat)
            proof["native_bridges"].append({
                "seat": title.name, "pid": process.pid, "bridge": mailbox.name,
                "runtime": release.package.name, "runtime_manifest_sha256": release.manifest_sha256,
                "workspace": str(workspace), "legacy_descriptor_selected": workspace == second_legacy_work,
            })
            baselines.append((hashes, await stock_profile(seat.bridge)))

        first, second = seats
        async def check_gameplay(host, guest, pawn):
            evidence = await verify_gameplay(host, guest, pawn, stock_drop=stock_drop, stock_split=stock_split,
                                             stock_equip=stock_equip, stock_nearby=stock_nearby, projected_nearby=projected_nearby)
            if container_nearby:
                evidence["guest_stock_nearby_container_pickup"] = await verify_container_pickup(host, guest, pawn)
            return evidence
        create = create_stock_split_character if stock_split or stock_equip else create_stock_drop_character if stock_drop else None
        proof["sessions"].append(await start_pair(first, second, "Second portable scout", on_join=check_gameplay,
                                                  create_character=create))
        proof["sessions"].append(await start_pair(second, first, "First portable scout", on_join=check_gameplay,
                                                  create_character=create))
        assert proof["sessions"][0]["host_pid"] == proof["sessions"][1]["guest_pid"]
        assert proof["sessions"][0]["guest_pid"] == proof["sessions"][1]["host_pid"]
        assert len({leg["campaign_id"] for leg in proof["sessions"]}) == 2
        if steam_cloud:
            proof["steam_cloud"] = await verify_cloud_restore(first, second, proof["sessions"][0])
        for seat, (hashes, preferences) in zip(seats, baselines, strict=True):
            for key in ("singleplayer", "modes", "multiplayer", "modes", "singleplayer"):
                button = await seat.bridge.command("native-title-button-fixture", key)
                await seat.bridge.wait_for(lambda frame: frame["sequence"] > button["sequence"])
            cached = await seat.bridge.command("native-title-stock-profile-fixture", "cache")
            assert cached["ack"]["message"] == preferences
            observed = stock_save_hashes(seat.profile)
            del observed["Config.sav"]
            assert observed == hashes and await stock_profile(seat.bridge) == preferences
            assert seat.bridge.process.poll() is None
        proof.update(verified=True, solo_slots_and_settings_preserved=True,
                     both_titles_reused=True, launched_without_coop_environment=True)
    finally:
        proof["native_boot_states"] = {str(game): boot_states(game) for game, _ in processes}
        for _, process in reversed(processes):
            if process.poll() is None:
                await runtime.stop_game(process)
        probes.close()
        proof["original_saves_verified"] = saved_hashes(original) == before
        proof["portable_files_and_existing_activation_preserved"] = all(
            fingerprint(path) == digest for release in qualified for path, digest in release.activation.items()
        )
        proof["verified"] &= proof["original_saves_verified"] and proof["portable_files_and_existing_activation_preserved"]
        runtime.write_json(case / "portable-title-verification.json", proof)
        assert proof["original_saves_verified"], "The original player saves changed"
        assert proof["portable_files_and_existing_activation_preserved"], "The portable marker or existing activation changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="Existing private parent for new evidence and profile directories")
    parser.add_argument("--first-game", type=Path, required=True, help="Clean replica with the release ZIP applied and no mods.txt")
    parser.add_argument("--second-game", type=Path, required=True, help="Separate replica with the same release ZIP applied")
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--stock-drop", action="store_true", help="test stock guest backpack Sort, Drop, and Use actions")
    parser.add_argument("--stock-split", action="store_true", help="test the stock guest stack-split popup with a Veteran loadout")
    parser.add_argument("--stock-equip", action="store_true", help="test the stock guest Equip action with a Veteran loadout")
    parser.add_argument("--stock-nearby", action="store_true", help="pick up a loose actor from the guest's stock Nearby Items panel")
    parser.add_argument("--projected-nearby", action="store_true", help="reclaim a player drop through stock Nearby Items")
    parser.add_argument("--container-nearby", action="store_true", help="pick up live stock container loot through Nearby Items")
    parser.add_argument("--steam-cloud", action="store_true", help="test a copied cloud backup through Host Load Game")
    parser.add_argument("--second-legacy-work", type=Path,
                        help="Unused isolated workspace from the second replica's retained legacy descriptor")
    args = parser.parse_args()
    if args.projected_nearby and (args.stock_drop == args.stock_split or args.stock_nearby):
        parser.error("Use --projected-nearby with either --stock-drop or --stock-split and without --stock-nearby")
    legacy = args.second_legacy_work.resolve(strict=True) if args.second_legacy_work else None
    asyncio.run(verify(args.work.resolve(strict=True), args.first_game.resolve(strict=True),
                       args.second_game.resolve(strict=True), args.vanilla_saved.resolve(strict=True), legacy,
                       stock_drop=args.stock_drop, stock_split=args.stock_split, stock_equip=args.stock_equip,
                       stock_nearby=args.stock_nearby, projected_nearby=args.projected_nearby,
                       container_nearby=args.container_nearby, steam_cloud=args.steam_cloud))
