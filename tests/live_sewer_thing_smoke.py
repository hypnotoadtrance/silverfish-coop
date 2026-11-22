"""Verify independent Sewer Thing dialogue and trade on an isolated three-player session."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_dialogue as dialogue


async def exercise(work: Path, original: Path):
    name = "sewer-thing-" + uuid.uuid4().hex[:8]
    host = ("--campaign", name)
    profiles = [("--profile", f"{name}-{index}") for index in (1, 2)]
    run = work / "campaigns" / name / "run"
    client_runs = [work / "clients" / profile[1] / "run" for profile in profiles]
    run.mkdir(parents=True)
    print(run, flush=True)
    original_hashes = saved_hashes(original)
    processes = []
    evidence = {"guests": [], "host_menu": []}
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))

    async def command(*args, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, error = await asyncio.wait_for(process.communicate(), 30)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if not output or (process.returncode == 0) != expected:
            raise RuntimeError(f"{args}: {(output or error).decode(errors='replace')}")
        return json.loads(output)

    async def launch(owner, *args):
        with (run / f"{owner[1]}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=log, stderr=log,
                                                             creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))

    async def action(owner, verb, target=None, *, expected=True, operation=None):
        select = ("--target", target) if target is not None else ()
        named = ("--operation-id", operation) if operation is not None else ()
        return await command("action", *owner, verb, *select, *named, expected=expected)

    def view(status):
        return status.get("game", {}).get("native_dialogue_view", {})

    def host_menu(status):
        return status.get("game", {}).get("native_dialogue_sewer_host_menu")

    def in_cistern(status, phase):
        return status.get("state") == phase and status.get("game", {}).get("map") == dialogue.CISTERN

    async def wallet():
        status = await wait(run, lambda row: in_cistern(row, "hosting") and type(row["game"].get("native_wallet")) is int)
        return status["game"]["native_wallet"]

    async def open_trade(profile, client_run):
        pending = (await action(profile, dialogue.FIXTURE, "1"))["native_dialogue_view"]
        rendered = await wait(client_run, lambda row: view(row).get("row") == dialogue.SEWER_OFFERS
                              and view(row).get("sequence") == pending["sequence"] and view(row).get("state") == "open")
        assert view(rendered)["surface"] == "stock", view(rendered)
        return view(rendered)

    try:
        await launch(host, "host", *host, "--port", "0", "--start-zone", dialogue.CISTERN, "--headless", "--fixtures",
                     "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones", "--max-guests", "2")
        await wait(run, lambda row: in_cistern(row, "hosting"), timeout=60)
        balance_before = await wallet()
        identities = []
        for index, (profile, client_run) in enumerate(zip(profiles, client_runs, strict=True), start=1):
            invite = await command("invite", *host, "--name", f"Cistern visitor {index}", "--character", f"Cistern player {index}")
            await launch(profile, "join", *profile, "--invite", invite["invite_file"], "--headless", "--native-zones")
            character = invite["character_id"]
            await wait(client_run, lambda row: in_cistern(row, "connected") and row.get("character_id") == character, timeout=60)
            identities.append(character)
            await wait(run, lambda row: row.get("gameplay", {}).get("players", {}).get("guest" if index == 1 else "guest2", {})
                       .get("character_id") == character)

        async def approach(index):
            profile, client_run, character = profiles[index], client_runs[index], identities[index]
            await action(host, dialogue.APPROACH, character)
            greet = dialogue.SEWER_FIRST if index == 0 else dialogue.SEWER_ROOT
            greeted = await wait(client_run, lambda row: view(row).get("row") == greet and view(row).get("state") == "open")
            assert (await wait(run, lambda row: in_cistern(row, "hosting")))["game"].get("native_dialogue_sewer_host_menu") is None
            trade = await open_trade(profile, client_run)
            evidence["guests"].append({"character": character, "greeting": view(greeted)["row"], "trade": trade})
            return trade["token"]

        await approach(0)
        seeded = (await action(host, "native-sewer-mushroom-fixture", identities[0]))["native_sewer_mushroom_fixture"]
        reopened = (await action(profiles[0], dialogue.FIXTURE, "open"))["native_dialogue_view"]
        await wait(client_runs[0], lambda row: view(row).get("row") == dialogue.SEWER_ROOT
                   and view(row).get("sequence") == reopened["sequence"] and view(row).get("state") == "open")
        first = (await open_trade(profiles[0], client_runs[0]))["token"]
        pending = (await action(profiles[0], dialogue.FIXTURE, "2"))["native_dialogue_view"]
        traded = await wait(client_runs[0], lambda row: view(row).get("row") == dialogue.SEWER_OFFERS
                            and view(row).get("sequence") == pending["sequence"] and view(row).get("state") == "open")
        for _ in range(25):
            events = (json.loads(line) for line in (client_runs[0] / "events.jsonl").read_text(encoding="utf-8").splitlines())
            matches = [event for event in events if event.get("event") == "native_sewer_trade_interaction"
                       and event.get("sequence") == pending["sequence"]]
            if matches:
                break
            await asyncio.sleep(.1)
        receipt, = matches
        operation, exchange = receipt["operation_id"], receipt["native_sewer_trade"]
        assert exchange["ordinal"] == 2, exchange
        assert exchange["receive"]["class_path"].endswith(".Eqp_LockPick_C"), exchange
        assert exchange["spent"][0]["guid"] == seeded["guid"], exchange
        replay = await action(profiles[0], dialogue.CHOOSE, f"{first}@2", operation=operation)
        assert replay["native_sewer_trade"] == exchange and replay["native_dialogue"]["token"] == view(traded)["token"]
        await action(host, dialogue.APPROACH, f"{identities[0]}@exit")
        await wait(client_runs[0], lambda row: view(row).get("state") == "waiting")
        second = await approach(1)
        assert first != second
        denied = await action(profiles[1], dialogue.CHOOSE, f"{first}@2", expected=False)
        assert denied["error"] == "native_dialogue_choice_expired", denied
        refused = await action(profiles[1], dialogue.CHOOSE, f"{second}@2", expected=False)
        assert refused["error"] == "sewer_trade_refused_unaffordable", refused
        evidence["trade"] = {"seeded": seeded, "exchange": exchange, "operation_id": operation,
                             "other_guest_denied": denied["error"], "other_guest_unaffordable": refused["error"]}

        await action(host, dialogue.SEWER_FIXTURE, "enter")
        await wait(run, lambda row: host_menu(row) == "dialogue")
        evidence["host_menu"].append("dialogue")
        for _ in range(10):
            if (await action(host, dialogue.SEWER_FIXTURE, "trade"))["menu"] == "trade":
                break
            await asyncio.sleep(.25)
        await wait(run, lambda row: host_menu(row) == "trade")
        evidence["host_menu"].append("trade")
        assert await wallet() == balance_before
        evidence["shared_wallet"] = balance_before
    except BaseException as error:
        evidence["failure"] = str(error)
        raise
    finally:
        try:
            for process, owner in reversed(processes):
                if process.returncode is None:
                    try:
                        await action(owner, "stop")
                        await asyncio.wait_for(process.wait(), 12)
                    except (OSError, RuntimeError, TimeoutError):
                        if process.returncode is None:
                            process.terminate()
                            await asyncio.wait_for(process.wait(), 10)
        finally:
            evidence["original_files_verified"] = len(original_hashes) if saved_hashes(original) == original_hashes else 0
            (run / "live-sewer-thing-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_files_verified"] == len(original_hashes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve()))
