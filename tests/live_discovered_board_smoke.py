"""Accept a stock contract from ShopKeep's second board in embedded co-op."""

import argparse
import asyncio
from hashlib import file_digest
import json
from math import dist
from pathlib import Path
import shutil
from uuid import UUID, uuid4

from live_ai_smoke import wait
from live_dynamic_board_smoke import AREA, QUEST, probe, staged_probe
from live_guest_held_fire_smoke import events, wait_event
from live_multi_smoke import saved_hashes
from live_portable_title_smoke import discover_mailbox, launch_portable, qualify_release
from live_same_process_listener_smoke import choose_ports
from live_unified_title_smoke import Seat, offered
from tools.silverfish import native_board_contract, native_host_save, native_wire_board, runtime, service
from tools.silverfish.control import submit


BOARD = f"BountyBoard_BP_C {AREA}.Shop_ShopKeep_P:PersistentLevel.BountyBoard_BP_C_2"
HOST_FORM = "native-title-host-form-fixture"
JOIN_FORM = "native-title-join-form-fixture"


def seed_campaign(source, destination, profile):
    stock = service.menu_host_checkpoint(source)
    assert stock["world"]["map"] == AREA and QUEST not in stock["world"]["party"].get("native_quests", {})
    source_storage = native_host_save.checkpoint_profile(source / "game-user", stock)
    scope = UUID(stock["campaign_id"]).hex
    stock["world"][native_host_save.SCOPE_FIELD] = scope
    target_storage = native_host_save.SaveScope(profile, scope)
    native_host_save.directory(target_storage).mkdir(parents=True)
    for frame in native_host_save.references(stock):
        slot = frame["slot"]
        shutil.copy2(native_host_save.slot_path(source_storage, slot), native_host_save.slot_path(target_storage, slot))
    runtime.write_json(destination / "save/campaign.json", stock)
    assert service.menu_host_checkpoint(destination, profile=profile, require_scoped=True) == stock


def campaign_hashes(directory):
    hashes = {}
    for path in directory.glob("campaign*.json"):
        with path.open("rb") as stream:
            hashes[path.name] = file_digest(stream, "sha256").hexdigest()
    return hashes


async def verify(work, game, original, canaries, source, manifest):
    roots = (work, game, original, canaries, source)
    if (any("steamapps" in {part.casefold() for part in bound.parts} for bound in roots)
            or any(game.is_relative_to(bound) or bound.is_relative_to(game) for bound in roots if bound != game)
            or any(not bound.is_dir() for bound in roots)):
        raise ValueError("Use one private portable game replica, a separate private workspace, and read-only save sources")
    assert BOARD not in native_wire_board.BOARDS.values()
    release = qualify_release(game, None)
    assert release.manifest_sha256 == manifest
    before = {path: saved_hashes(path) for path in (original, canaries, source / "game-user/Saved/SaveGames")}
    assert all(before.values())
    case = work / ("eye-board-" + uuid4().hex[:8])
    case.mkdir()
    print(case, flush=True)
    host_profile = case / "host" / release.title_directory / "game-user"
    campaign = case / "host/campaigns/discovery"
    seed_campaign(source, campaign, host_profile)
    host_run = campaign / "run"
    guest_run = case / "guest/clients/local/run"
    processes, seats = [], []
    proof = {"verified": False, "manifest_sha256": manifest, "board": BOARD, "original_canaries": before[original]}

    async def action(run, name, **kwargs):
        reply = await submit(run, name, **kwargs)
        assert reply["ok"], (name, reply)
        return reply

    async def make_seat(label, port):
        workspace = case / label
        profile = workspace / release.title_directory / "game-user"
        profile.mkdir(parents=True, exist_ok=True)
        process = launch_portable(game, profile)
        processes.append(process)
        mailbox = await discover_mailbox(profile.parent / "run/game-bridge", process)
        seat = Seat(profile, campaign, workspace / "clients/local", port, runtime.Bridge(mailbox, process))
        await offered(seat)
        return seat

    with staged_probe(game):
        try:
            host_port, guest_port = choose_ports()
            host, guest = await asyncio.gather(make_seat("host", host_port), make_seat("guest", guest_port))
            seats.extend((host, guest))
            async def host_form(operation, *args):
                return await host.bridge.command(HOST_FORM, operation, *args)

            await host_form("open")
            async with asyncio.timeout(12):
                while not (host.bridge.directory / "title-host-campaigns.tsv").is_file():
                    await asyncio.sleep(.1)
            await host_form("browse")
            selected = (await host_form("saved-state"))["ack"]["message"]
            assert selected.endswith(":discovery"), selected
            await host_form("choose-saved")
            await host_form("port", str(host_port))
            await host_form("submit")
            hosted = await wait(host_run, lambda frame: frame.get("state") == "hosting" and
                                frame.get("local_bridge") == "memory", timeout=45)
            assert (hosted["pid"], hosted["game"]["map"], hosted["local_bridge"]) == (
                host.bridge.process.pid, AREA, "memory"), hosted

            async def join(operation, *args):
                return await guest.bridge.command(JOIN_FORM, operation, *args)

            async def screen(expected):
                async with asyncio.timeout(18):
                    while (await join("observe"))["ack"]["message"] != "title_join_form_state=" + expected:
                        await asyncio.sleep(.1)

            await join("open")
            await join("address", "127.0.0.1")
            await join("port", str(host_port))
            await join("no_password")
            await screen("trust_host")
            await guest.bridge.command("native-title-button-fixture", "trust")
            await screen("catalog;count=0")
            await join("create", "Discovery courier")
            await screen("catalog;count=1")
            roster = service.menu_host_checkpoint(campaign, profile=host.profile, require_scoped=True)["characters"]
            character, = [key for key, row in roster.items() if row["name"] == "Discovery courier"]
            await join("select", character)
            await join("submit")
            joined = await wait(guest_run, lambda frame: frame.get("state") == "connected" and frame.get("gameplay_fresh"),
                                timeout=35)
            visit = hosted["zone_epoch"]
            assert (joined["pid"], joined["game"]["map"], joined["local_bridge"], joined["zone_epoch"]) == (
                guest.bridge.process.pid, AREA, "memory", visit), joined
            paired = await wait(host_run, lambda frame: frame.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            pawn = paired["game"]["native_guest_save"]["pawn"]
            assert paired["zone_epoch"] == visit

            assert await probe(host.bridge.directory, "globalxp") == "5"
            selected_board, *coordinates = (await probe(host.bridge.directory, "approach")).split(";")
            assert selected_board == BOARD and len(coordinates) == 3
            target = tuple(map(float, coordinates))
            await wait(guest_run, lambda frame: (at := frame.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
                       and dist(at, target) <= 25)
            catalog = (await action(guest_run, native_board_contract.CATALOG, target=BOARD))[native_board_contract.CATALOG_KEY]
            assert (catalog["map"], catalog["board"], catalog["pawn"]) == (AREA, BOARD, pawn), catalog
            assert QUEST in catalog["offers"] and QUEST not in catalog["turnins"], catalog
            campaign_before = campaign_hashes(campaign / "save")
            stock_dir = native_host_save.directory(native_host_save.SaveScope(host.profile, hosted["game"]["save_scope"]))
            stock_before = saved_hashes(stock_dir)

            pressed = (await action(guest_run, native_wire_board.STOCK_F_FIXTURE, target=BOARD))["native_wire_board_stock"]
            assert (pressed["state"], pressed["board"], pressed["quest"]) == ("loading", BOARD, "-"), pressed
            visible = (await wait(guest_run, lambda frame: (view := frame.get("game", {}).get("native_wire_board_stock", {}))
                                  and view["state"] == "visible" and view["sequence"] == pressed["sequence"]
                                  and view["board"] == BOARD and frame["zone_epoch"] == visit))["game"]["native_wire_board_stock"]
            click = (await action(guest_run, native_wire_board.STOCK_FIXTURE, target=f"{QUEST}|start"))["native_wire_board_stock"]
            assert (click["board"], click["quest"], click["state"], click["open"]) == (
                BOARD, QUEST, "starting", visible["sequence"]), click
            proof.update(visit=visit, character=character, pids=[hosted["pid"], joined["pid"]],
                         bridge={"host": "memory", "guest": "memory"}, press=pressed, visible=visible, click=click, catalog=catalog)

            def selected(entry):
                return (entry.get("sequence"), entry.get("quest"), entry.get("board")) == (click["sequence"], QUEST, BOARD)

            async with asyncio.timeout(12):
                while True:
                    rejected = [entry for entry in events(guest_run, "native_wire_board_interaction_rejected") if selected(entry)]
                    if rejected:
                        proof["rejected"] = rejected[-1]
                        raise RuntimeError(f"The exact stock button was rejected by co-op authority: {rejected[-1]['reason']}")
                    if any(selected(entry) and entry["state"] == "accepted"
                           for entry in events(guest_run, "native_wire_board_interaction")):
                        break
                    await asyncio.sleep(.1)

            expected = {"quest_id": QUEST, "step": 1, "actor": character}
            def accepted(frame):
                return (frame.get("zone_epoch") == visit and frame.get("gameplay", {}).get("native_quests", {}).get(QUEST) == expected)
            await asyncio.gather(wait(host_run, accepted, timeout=10), wait(guest_run, accepted, timeout=10))
            receipt, = [entry for entry in events(host_run, "native_board_contract_applied") if entry.get("quest_id") == QUEST]
            assert receipt["board"] == BOARD and all(receipt[k] == v for k, v in expected.items()), receipt
            journal = await wait_event(guest_run, "native_guest_ui_projected",
                                       lambda entry: entry["quests"] == 1 and entry["time_unix"] >= receipt["time_unix"])
            after = (await action(guest_run, native_board_contract.CATALOG, target=BOARD))[native_board_contract.CATALOG_KEY]
            assert QUEST not in after["offers"] and QUEST not in after["turnins"], after
            assert campaign_hashes(campaign / "save") == campaign_before and saved_hashes(stock_dir) == stock_before
            for seat in seats:
                assert not (seat.bridge.directory / native_wire_board.HEARTBEAT).exists()
                assert not (seat.bridge.directory / "native-wire-board-events").exists()
            proof.update(verified=True, receipt=receipt, journal=journal, both_party_views=True, deferred_saves=True)
        except BaseException as error:
            proof["failure"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            await asyncio.gather(*(runtime.stop_game(process) for process in processes), return_exceptions=True)
            proof["all_processes_closed"] = all(process.poll() is not None for process in processes)
            proof["source_and_original_saves_unchanged"] = all(saved_hashes(path) == prior for path, prior in before.items())
            (case / "discovered-board-proof.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
            assert proof["all_processes_closed"] and proof["source_and_original_saves_unchanged"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("work", "game", "original", "canaries", "source"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest", required=True)
    options = parser.parse_args()
    asyncio.run(verify(*(getattr(options, key).resolve(strict=True)
                         for key in ("work", "game", "original", "canaries", "source")), options.manifest))
