"""Exercise a neutral stock sleep screen in a staged City listen session."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes


CITY = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
BED = "SavePoint_BP_C_1-City_01_P-"


async def validate(work: Path, vanilla_saved: Path | None) -> None:
    campaign = "neutral-rest-" + uuid.uuid4().hex[:8]
    run = work / "campaigns" / campaign / "run"
    run.mkdir(parents=True, exist_ok=True)
    cli = [sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work)]
    target = ["--campaign", campaign]
    baseline = saved_hashes(vanilla_saved) if vanilla_saved else None

    async def command(*arguments):
        process = await asyncio.create_subprocess_exec(
            *cli, *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output, error = await asyncio.wait_for(process.communicate(), 45)
        if process.returncode:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(output)
        if reply.get("ok") is False:
            raise RuntimeError(f"{arguments}: {reply}")
        return reply

    async def wait(condition, timeout=45):
        deadline = time.monotonic() + timeout
        status = {}
        while time.monotonic() < deadline:
            try:
                status = json.loads((run / "status.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                await asyncio.sleep(0.2)
                continue
            if condition(status):
                return status
            if status.get("state") == "failed":
                break
            await asyncio.sleep(0.2)
        raise RuntimeError(f"{run} did not reach the expected neutral rest state: {status}")

    with (run / "host.txt").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            *cli, "host", *target, "--port", "0", "--headless", "--fixtures", "--native-zones", "--start-zone", CITY,
            stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    try:
        await wait(lambda status: status.get("state") == "hosting" and status["game"]["map"] == CITY
                   and status["game"].get("native_rest_hook", {}).get("fresh") is True)
        approach = (await command("action", *target, "native-rest-approach"))["native_rest"]
        assert approach["eligible"] and approach["bed"] == BED and not approach["insomnia"], approach
        confirmation = (await command("action", *target, "native-rest-ui-fixture", "--amount", "1"))["native_rest_hook"]
        assert confirmation["status"] == "selected", confirmation
        await wait(lambda status: status.get("campaign", {}).get("world", {}).get("native_rest", {}).get("bed") == BED)
        events = (await command("events", *target, "--tail", "50"))["events"]
        sleeps = [row for row in events if row["event"] == "native_rest_checkpoint"]
        assert len(sleeps) == 1, sleeps
        sleep = sleeps[0]
        assert sleep["map"] == CITY and sleep["bed"] == BED and sleep["actor"] == approach["actor"], sleep
        assert math.isclose(sleep["after"] - sleep["before"], 3600, rel_tol=0, abs_tol=0.25), sleep
    finally:
        if process.returncode is None:
            try:
                await command("action", *target, "stop")
                await asyncio.wait_for(process.wait(), 12)
            except (RuntimeError, TimeoutError):
                if process.returncode is None:
                    process.terminate()
                await asyncio.wait_for(process.wait(), 12)
        if vanilla_saved:
            assert baseline == saved_hashes(vanilla_saved), "Original singleplayer save files changed"
        print(run, flush=True)

    evidence = {"neutral_stock_rest": sleep, "stock_sleep_screen_confirmation": confirmation}
    if baseline is not None:
        evidence["original_files_verified"] = len(baseline)
    (run / "live-neutral-rest-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", type=Path, required=True)
    options.add_argument("--vanilla-saved", type=Path)
    arguments = options.parse_args()
    asyncio.run(validate(arguments.work.resolve(), arguments.vanilla_saved))
