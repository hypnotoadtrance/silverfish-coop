"""Verify stock loose cash input, the pair's shared wallet and same-port restore."""

import argparse
import asyncio
from hashlib import sha256
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
from tools.silverfish.travel import CITY, CROSSROADS


CASES = {
    "city": (CITY, (("city-couple-bills-7", "guest"), ("city-couple-bills-8", "host"))),
    "city-large-stack": (CITY, (("city-large-stack-1", "guest"),)),
    "city-massive-pile": (CITY, (("city-massive-pile-1", "guest"),)),
    "crossroads-small-stack": (CROSSROADS, (("crossroads-small-stack-1", "guest"),)),
}


async def verify(work: Path, original: Path, case="city"):
    area, sources = CASES[case]
    if not original.is_dir():
        raise ValueError(f"The original Saved directory does not exist: {original}")
    originals = saved_hashes(original)
    name = ("city-cash" if case == "city" else case + "-cash") + "-" + uuid.uuid4().hex[:8]
    root = work / "campaigns" / name
    host_run, guest_run = root / "run", work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    host_args = ("--campaign", name, "--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones",
                 "--native-expanded-zones")
    guest_args = ("--profile", name, "--headless", "--native-zones")
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    jobs = []
    proof = {"case": case, "map": area, "claims": {}}

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
                          and packet.get("zone_epoch") != previous_epoch and packet.get("game", {}).get("map") == area
                          and packet["game"].get("native_guest_save", {}).get("state") == "bound", timeout=80)
        await wait(guest_run, lambda packet: packet.get("state") == "connected" and packet.get("gameplay_fresh")
                   and (packet.get("character_id"), packet.get("zone_epoch"), packet.get("host_session"),
                        packet.get("game", {}).get("map")) == (character, host["zone_epoch"], host["generation"], area), timeout=80)
        return host

    async def inventory(directory):
        offset, items = None, {}
        for _ in range(32):
            position = 0 if offset is None else offset
            options = {"amount": position} if directory == host_run else {"target": str(position)}
            page = (await action(directory, "native-inventory-list", **options))["native_inventory"]
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
        return saved["world"]["party"], stock_flags(slots, authority, area), stock_flags(slots, detached, area)

    def save_hashes():
        paths = list((root / "save").glob("campaign*.json"))
        paths.extend((root / "game-user").rglob("*.sav"))
        return {path.relative_to(root): sha256(path.read_bytes()).hexdigest() for path in paths}

    try:
        authority = await launch(host_run, "host", "host", *host_args, "--port", "0", "--start-zone", area)
        await wait(host_run, lambda packet: packet.get("state") == "hosting")
        display_name = "City cash tester" if case == "city" else "Loose cash tester"
        invitation = await action(host_run, "invite", name=display_name, character="Independent inventory")
        character = invitation["character_id"]
        guest = await launch(guest_run, "guest", "join", *guest_args, "--invite", invitation["invite_file"])
        initial = await paired(character)
        await action(host_run, "checkpoint")
        _, authority_base, guest_base = checkpoint(character)
        balance = authority_base.get("Player_Cash", 0)
        before = {directory: await inventory(directory) for directory in (host_run, guest_run)}
        baseline = save_hashes()
        assert baseline

        for source, role in sources:
            placement = pickup.STOCK_CASH[source]
            directory, owner = (guest_run, character) if role == "guest" else (host_run, "host")
            observed = await wait(host_run, lambda packet: published(packet, source).get("state") == "available"
                                  and placement.accepts(published(packet, source).get("wallet_value")))
            amount = published(observed, source)["wallet_value"]
            assert observed["game"]["native_wallet"] == balance, observed["game"]["native_wallet"]
            await action(host_run, pickup.APPROACH, target=role, pickup_source=source)
            stock = (await action(directory, pickup.STOCK_FIXTURE, pickup_source=source))["native_authored_pickup_interaction"]
            assert (stock["entry"], stock["source"], stock["map"]) == ("stock", source, area), stock
            committed = await wait(host_run, lambda packet: source in packet.get("gameplay", {}).get(pickup.STATE_KEY, {}))
            claim = committed["gameplay"][pickup.STATE_KEY][source]
            credit = {"amount": amount, "before": balance, "after": balance + amount,
                      "stock_flag": placement.stock_flag}
            assert claim == {"source": source, "map": area, "actor": placement.actor, "class_path": placement.item.class_path,
                             "owner": owner, "wallet_credit": credit}, claim
            balance = credit["after"]
            await wait(guest_run, lambda packet: packet.get("gameplay", {}).get("shared_wallet") == balance
                       and published(packet, source).get("state") == "claimed")
            proof["claims"][source] = {"stock_input": stock, "receipt": claim}

        assert save_hashes() == baseline, "A stock cash pickup wrote to disk before an explicit save"
        proof["no_save_on_pickup"] = True
        await action(host_run, "checkpoint")
        saved, host_flags, guest_flags = checkpoint(character)
        assert host_flags.get("Player_Cash") == balance, host_flags
        for source, role in sources:
            owner = character if role == "guest" else "host"
            claim = proof["claims"][source]["receipt"]
            credit = claim["wallet_credit"]
            receipts = [item for item in saved["receipts"][owner] if item["request"] == [pickup.ACTION, source, None]]
            assert len(receipts) == 1 and receipts[0]["result"]["native_authored_pickup_claim"] == claim, receipts
            assert host_flags.get(credit["stock_flag"]) == 1, host_flags
            assert guest_flags.get(credit["stock_flag"]) == guest_base.get(credit["stock_flag"]) and (
                guest_flags.get("Player_Cash") == guest_base.get("Player_Cash")), guest_flags
            proof["claims"][source]["operation_id"] = receipts[0]["id"]

        for directory in (host_run, guest_run):
            assert before[directory] == await inventory(directory)
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
                   and all(published(packet, source).get("state") == "claimed" for source, _ in sources))
        for source, role in sources:
            directory = guest_run if role == "guest" else host_run
            recorded = proof["claims"][source]
            replay = await action(directory, pickup.ACTION, target=source, operation_id=recorded["operation_id"])
            assert replay["native_authored_pickup_claim"] == recorded["receipt"], replay
            repeated = await action(guest_run, pickup.ACTION, target=source, expected=False)
            assert repeated["error"] == "native_authored_pickup_already_claimed", repeated
        for directory in (host_run, guest_run):
            assert before[directory] == await inventory(directory)
        replay_proof = {"both_replayed": True} if case == "city" else {"replayed": True}
        proof.update(verified=True, restart={"port": port, "wallet": balance, "inventory_unchanged": True, **replay_proof})
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
    parser.add_argument("--case", choices=CASES, default="city")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve(), args.case))
