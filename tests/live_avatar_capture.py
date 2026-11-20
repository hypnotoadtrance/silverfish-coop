"""Verify both remote avatar projections and capture an offscreen guest PNG."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import CROSSROADS, saved_hashes


REPO = Path(__file__).resolve().parents[1]


async def capture(work: Path, vanilla_saved: Path) -> None:
    profile = "avatar-" + uuid.uuid4().hex[:8]
    host_dir = work / "campaigns" / profile
    guest_dir = work / "clients" / profile
    host_run, guest_run = host_dir / "run", guest_dir / "run"
    host_run.mkdir(parents=True)
    env = dict(os.environ, SILVERFISH_COOP_RENDER_CAPTURE="guest")
    base = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    before = saved_hashes(vanilla_saved)
    processes = []
    host = ("--campaign", profile)
    guest = ("--profile", profile)

    async def command(*args):
        process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 25)
        if process.returncode:
            raise RuntimeError(f"{args} failed with {output!r} {error!r}")
        return json.loads(output)

    async def start(log, target, *args):
        with (host_run / log).open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, env=env,
                stdout=stream, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, target))

    def projections(state):
        game = state.get("game", {})
        remote = game.get("pawns", {}).get("guest", {}).get("name")
        items = game.get("native_player_avatars", {})
        return items if remote and set(items) == {remote} else None

    print(host_run, flush=True)
    try:
        await start("host.txt", host, "host", *host, "--port", "0", "--headless", "--fixtures",
            "--native-events", "--native-guest", "--native-zones", "--start-zone", CROSSROADS)
        await wait(host_run, lambda state: state.get("state") == "hosting")
        invitation = await command("invite", *host, "--name", "Loopback avatar guest", "--character", "Avatar guest")
        await start("guest.txt", guest, "join", *guest, "--invite", invitation["invite_file"], "--headless", "--native-zones")
        evidence = {}
        for role, run in (("host", host_run), ("guest", guest_run)):
            state = await wait(run, projections)
            evidence[role] = projections(state)
            assert all(row["state"] == "default_body" for row in evidence[role].values()), evidence
        for _ in range(80):
            images = list((guest_dir / "game-user").rglob("*.png"))
            if images:
                screenshot = images[0]
                assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), screenshot
                break
            await asyncio.sleep(.25)
        else:
            raise RuntimeError("The isolated guest did not write a screenshot")
        evidence["screenshot"] = str(screenshot)
        print(json.dumps(evidence, indent=2), flush=True)
        (host_run / "live-avatar-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    finally:
        for process, target in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *target, "stop")
                    await asyncio.wait_for(process.wait(), 12)
                except (RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await process.wait()
        assert before == saved_hashes(vanilla_saved), "Original singleplayer save files changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(capture(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True)))
