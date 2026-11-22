"""Exercise stock Observatory barter confirmation and its host save."""

import argparse
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.silverfish import native_host_save, native_npc_barter
from tools.silverfish.control import submit
from tools.silverfish.travel import OBSERVATORY


@contextmanager
def staged_probe(work):
    scripts = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishCoop/Scripts"
    main, probe = scripts / "main.lua", scripts / "npc_barter_flow.lua"
    original = main.read_bytes()
    point = b"    native_npc_barter.sync(state, current_phase)\n"
    normalized = original.replace(b"\r\n", b"\n")
    assert normalized.count(point) == 1, "The stock barter stage callback is unavailable"
    body = (Path(__file__).with_name("fixtures") / probe.name).read_bytes()
    with probe.open("xb") as output:
        output.write(body)
    try:
        main.write_bytes(b'local npc_barter_flow = require("npc_barter_flow")\n'
                         + normalized.replace(point, point + b"    npc_barter_flow.sample(state, current_phase)\n"))
        yield
    finally:
        main.write_bytes(original)
        probe.unlink()


def events(run, name):
    journal = run / "events.jsonl"
    if not journal.is_file():
        return []
    text = journal.read_text(encoding="utf-8")
    return [record for line in text.split("\n")[:-1]
            if (record := json.loads(line)).get("event") == name]


async def verify(work, canary, offer):
    profile = "npc-barter-flow-" + uuid.uuid4().hex[:8]
    campaign, client = work / "campaigns" / profile, work / "clients" / profile
    host, guest = campaign / "run", client / "run"
    host.mkdir(parents=True)
    print(host, flush=True)
    baseline = saved_hashes(canary)
    assert baseline
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    environment = dict(os.environ, SILVERFISH_COOP_BARTER_PROBE=offer)
    jobs = []

    async def start(run, *args):
        with (host / f"{args[0]}.txt").open("wb") as stream:
            child = await asyncio.create_subprocess_exec(*cli, *args, stdout=stream, stderr=stream,
                                                        cwd=ROOT, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((run, child))

    def tail(run):
        return [line for file in (run / "game-bridge").glob("*/npc-barter-flow.tsv")
                for line in file.read_text(encoding="utf-8").splitlines()][-6:]

    with staged_probe(work):
        try:
            await start(host, "host", "--campaign", profile, "--port", "0", "--headless", "--fixtures",
                        "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones",
                        "--native-shop-view", "--start-zone", OBSERVATORY)
            await wait(host, lambda state: state.get("state") == "hosting")
            invitation = await asyncio.create_subprocess_exec(*cli, "invite", "--campaign", profile,
                "--name", "Barter guest", "--character", "Barter guest", stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
            output, error = await asyncio.wait_for(invitation.communicate(), 20)
            assert invitation.returncode == 0, error.decode(errors="replace")
            descriptor = json.loads(output)
            await start(guest, "join", "--profile", profile, "--invite", descriptor["invite_file"], "--headless",
                        "--native-zones")
            await wait(guest, lambda state: state.get("state") == "connected" and state.get("gameplay_fresh"))
            async with asyncio.timeout(22):
                while not (commits := events(host, "native_npc_barter_committed")):
                    refused = events(guest, "native_npc_barter_refused")
                    assert not refused, refused
                    await asyncio.sleep(.15)
            commit, = commits
            view = await wait(guest, lambda state: (screen := state.get("game", {}).get("native_npc_barter_view", {}))
                              and screen.get("state") == "ready" and screen.get("sequence", 0) > 1, timeout=8)
            inventory = events(guest, "native_guest_ui_projected")[-1]
            if offer != "donation":
                assert inventory["items"] > 0, inventory
            checkpoint = json.loads((campaign / "save" / "campaign.json").read_bytes())
            native_host_save.verify_checkpoint(campaign / "game-user", checkpoint)
            receipt, = [row for row in checkpoint["world"]["party"]["receipts"][commit["character_id"]]
                        if row["request"][0] == native_npc_barter.EXCHANGE]
            result = receipt["result"]["native_npc_barter"]
            assert result["transfers"] == commit["transfers"], receipt
            transfers = {(row["direction"], row["class_path"].rsplit(".", 1)[-1], row["quantity"])
                         for row in commit["transfers"]}
            if offer == "donation":
                assert transfers == {("give", "Item_Jewely_C", 12)} and commit["cash_before"] == commit["cash"] == 1200, commit
                reputation = commit["reputation"]
                assert result["reputation"] == reputation and reputation["flag"] == "Rep_Freelancer", reputation
                assert reputation["after"] > reputation["before"] and any(line.startswith("confirmation\t") for line in tail(guest))
            elif offer == "high-value":
                assert transfers == {("receive", "Item_Jewely_C", 8), ("give", "Item_CannedPeaches_C", 1)}, transfers
                assert commit["cash_before"] == 20000 and 1000 < 20000 - commit["cash"] <= 20000, commit
            evidence = {"offer": offer, "commit": commit, "receipt": receipt, "checkpoint_revision": checkpoint["revision"],
                        "guest_stock_screen": view["game"]["native_npc_barter_view"], "guest_inventory": inventory}
            (host / "npc-barter-flow.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            print(host / "npc-barter-flow.json", flush=True)
        except TimeoutError as error:
            raise RuntimeError(f"Stock barter timed out. Host {tail(host)}. Guest {tail(guest)}.") from error
        finally:
            for run, child in reversed(jobs):
                if child.returncode is None:
                    try:
                        await submit(run, "stop")
                        await asyncio.wait_for(child.wait(), 10)
                    except (OSError, RuntimeError, TimeoutError):
                        if child.returncode is None:
                            child.terminate()
                        await asyncio.wait_for(child.wait(), 8)
            assert saved_hashes(canary) == baseline


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--offer", choices=("cash", "high-value", "donation"), default="cash")
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve(strict=True), options.offer))
