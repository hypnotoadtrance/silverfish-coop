"""Verify two exact stock City cash pickups, the pair's shared wallet and same-port restore."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_computer_board_smoke import stock_flags
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.silverfish import native_authored_pickup as pickup, native_host_save
from tools.silverfish.control import submit
from tools.silverfish.travel import CITY


async def verify(work: Path, original: Path):
    if not original.is_dir():
        raise ValueError(f"The original Saved directory does not exist: {original}")
    originals = saved_hashes(original)
    name = "city-cash-" + uuid.uuid4().hex[:8]
    root = work / "campaigns" / name
    host_run, guest_run = root / "run", work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    host_args = ("--campaign", name, "--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones",
                 "--native-expanded-zones")
    guest_args = ("--profile", name, "--headless", "--native-zones")
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    jobs = []
    proof = {"claims": {}}

    async def launch(directory, label, *arguments):
        with (host_run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((directory, process))
        return process

    async def action(directory, verb, *, expected=True, **arguments):
        result = await submit(directory, verb, **arguments)
        assert result.get("ok") is expected, (verb, result)
        return result

    async def stop(directory, process):
        if process.returncode is None:
            await action(directory, "stop")
            await asyncio.wait_for(process.wait(), 18)
        assert process.returncode == 0, process.returncode

    def published(status, source):
        return status.get("game", {}).get(pickup.SOURCES_KEY, {}).get(source, {})

    async def paired(character, previous_epoch=None):
        host = await wait(host_run, lambda packet: packet.get("state") == "hosting" and packet.get("link_connected")
                          and packet.get("zone_epoch") != previous_epoch and packet.get("game", {}).get("map") == CITY
                          and packet["game"].get("native_guest_save", {}).get("state") == "bound", timeout=80)
        await wait(guest_run, lambda packet: packet.get("state") == "connected" and packet.get("gameplay_fresh")
                   and (packet.get("character_id"), packet.get("zone_epoch"), packet.get("host_session"),
                        packet.get("game", {}).get("map")) == (character, host["zone_epoch"], host["generation"], CITY), timeout=80)
        return host

    async def inventory(directory):
        offset, items = None, {}
        for _ in range(32):
            page = (await action(directory, "native-inventory-list", target=str(offset or 0)))["native_inventory"]
            for item in page["items"]:
                assert item["guid"] not in items, item
                items[item["guid"]] = item
            offset = page["next_offset"]
            if offset is None:
                return items
        raise AssertionError("Inventory exceeded the bounded staged page scan")

    def checkpoint(character):
        profile = root / "game-user"
        saved = json.loads((root / "save" / "campaign.json").read_text(encoding="utf-8"))
        native_host_save.verify_checkpoint(profile, saved)
        authority = saved["world"]["native_host_save"]
        detached = saved["characters"][character]["state"]["native_guest_save"]
        assert authority["slot"] != detached["slot"], (authority, detached)
        slots = native_host_save.slot_path(profile, authority["slot"]).parent
        return saved["world"]["party"], stock_flags(slots, authority, CITY), stock_flags(slots, detached, CITY)

    try:
        authority = await launch(host_run, "host", "host", *host_args, "--port", "0", "--start-zone", CITY)
        await wait(host_run, lambda packet: packet.get("state") == "hosting")
        invitation = await action(host_run, "invite", name="City cash tester", character="Independent inventory")
        character = invitation["character_id"]
        guest = await launch(guest_run, "guest", "join", *guest_args, "--invite", invitation["invite_file"])
        initial = await paired(character)
        await action(host_run, "checkpoint")
        _, authority_base, guest_base = checkpoint(character)
        balance = authority_base.get("Player_Cash", 0)
        before = {directory: await inventory(directory) for directory in (host_run, guest_run)}

        for number, role, directory, owner in ((7, "guest", guest_run, character), (8, "host", host_run, "host")):
            source = f"city-couple-bills-{number}"
            area, actor, item_class = pickup.SOURCES[source]
            observed = await wait(host_run, lambda packet: published(packet, source).get("state") == "available"
                                  and 6 <= published(packet, source).get("wallet_value", 0) <= 10)
            amount = published(observed, source)["wallet_value"]
            assert observed["game"]["native_wallet"] == balance, observed["game"]["native_wallet"]
            await action(host_run, pickup.APPROACH, target=role, pickup_source=source)
            stock = (await action(directory, pickup.STOCK_FIXTURE, pickup_source=source))["native_authored_pickup_interaction"]
            assert (stock["entry"], stock["source"], stock["map"]) == ("stock", source, CITY), stock
            committed = await wait(host_run, lambda packet: source in packet.get("gameplay", {}).get(pickup.STATE_KEY, {}))
            claim = committed["gameplay"][pickup.STATE_KEY][source]
            credit = {"amount": amount, "before": balance, "after": balance + amount,
                      "stock_flag": f"{pickup.CASH_ACTORS[source]}-City_01_P-"}
            assert claim == {"source": source, "map": area, "actor": actor, "class_path": item_class,
                             "owner": owner, "wallet_credit": credit}, claim
            saved, host_flags, guest_flags = checkpoint(character)
            receipts = [item for item in saved["receipts"][owner] if item["request"] == [pickup.ACTION, source, None]]
            assert len(receipts) == 1 and receipts[0]["result"]["native_authored_pickup_claim"] == claim, receipts
            assert host_flags.get(credit["stock_flag"]) == 1 and host_flags.get("Player_Cash") == credit["after"], host_flags
            assert guest_flags.get(credit["stock_flag"]) == guest_base.get(credit["stock_flag"]) and (
                guest_flags.get("Player_Cash") == guest_base.get("Player_Cash")), guest_flags
            balance = credit["after"]
            await wait(guest_run, lambda packet: packet.get("gameplay", {}).get("shared_wallet") == balance
                       and published(packet, source).get("state") == "claimed")
            proof["claims"][source] = {"stock_input": stock, "receipt": claim, "operation_id": receipts[0]["id"]}

        for directory in (host_run, guest_run):
            assert before[directory] == await inventory(directory)
        first, second = tuple(proof["claims"])
        await stop(host_run, authority)
        await stop(guest_run, guest)
        port = initial["endpoint"]["port"]
        await launch(host_run, "host-restart", "host", *host_args, "--port", str(port))
        await wait(host_run, lambda packet: packet.get("state") == "hosting" and packet.get("generation") != initial["generation"])
        renewed = await action(host_run, "refresh-invite", target=invitation["guest_id"])
        assert renewed["character_id"] == character, renewed
        await launch(guest_run, "guest-restart", "join", *guest_args, "--invite", renewed["invite_file"])
        restarted = await paired(character, initial["zone_epoch"])
        assert restarted["endpoint"]["port"] == port and restarted["game"]["native_wallet"] == balance, restarted["game"]
        await wait(guest_run, lambda packet: packet.get("gameplay", {}).get("shared_wallet") == balance
                   and all(published(packet, source).get("state") == "claimed" for source in (first, second)))
        for source, directory in ((first, guest_run), (second, host_run)):
            recorded = proof["claims"][source]
            replay = await action(directory, pickup.ACTION, target=source, operation_id=recorded["operation_id"])
            assert replay["native_authored_pickup_claim"] == recorded["receipt"], replay
            repeated = await action(guest_run, pickup.ACTION, target=source, expected=False)
            assert repeated["error"] == "native_authored_pickup_already_claimed", repeated
        proof.update(verified=True, restart={"port": port, "wallet": balance, "inventory_unchanged": True, "both_replayed": True})
    except BaseException as error:
        proof.update(verified=False, failure=f"{type(error).__name__}: {error}")
        raise
    finally:
        for directory, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(directory, process)
                except (OSError, RuntimeError, AssertionError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        proof["original_saves_verified"] = saved_hashes(original) == originals
        (host_run / "live-authored-cash-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(proof), flush=True)
        assert proof["original_saves_verified"], "The original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--vanilla-saved", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve()))
