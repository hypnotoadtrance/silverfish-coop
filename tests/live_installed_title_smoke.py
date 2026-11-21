"""Exercise a cold Direct IP start from two installed stock-layout replicas."""

import argparse
import asyncio
from itertools import combinations
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.silverfish import runtime
from tools.silverfish.win_job import GameProcess
from live_multi_smoke import saved_hashes
from live_resident_host_smoke import stock_profile, stock_save_hashes
from live_same_process_listener_smoke import choose_ports
from live_unified_title_smoke import Seat, offered, start_pair


_NONCE = re.compile(r"[0-9a-f]{32}")
_BOOT_STATE = re.compile(rb"\[SilverfishCoopEmbeddedRuntime\] state=([a-z_]+)")


def launch_installed(game, profile):
    image = (game / runtime.SHIPPING).resolve(strict=True)
    if image != game / runtime.SHIPPING or not image.is_file():
        raise ValueError("The shipping executable must be an ordinary file in the replica")
    env = {name: value for name, value in os.environ.items() if not name.upper().startswith("SILVERFISH_COOP_")}
    arguments = subprocess.list2cmdline([str(image)])
    arguments += (f' -userdir="{profile}" -unattended -NoSplash -windowed -nullrhi -RenderOffScreen'
                  " -nosound -ResX=640 -ResY=480"
                  " -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,"
                  "[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock")
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return GameProcess(arguments, executable=image, cwd=image.parent, env=env, startupinfo=info)


def boot_states(game):
    log = game / runtime.SHIPPING.parent / "ue4ss" / "UE4SS.log"
    try:
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 65536))
            return [state.decode("ascii") for state in _BOOT_STATE.findall(stream.read(65536))][-8:]
    except OSError:
        return []


async def native_mailbox(root, process):
    async with asyncio.timeout(30):
        while process.poll() is None:
            mailboxes = [path for path in root.iterdir() if _NONCE.fullmatch(path.name) and path.is_dir()]
            if len(mailboxes) > 1:
                raise RuntimeError("The cold start published multiple bridge directories")
            if mailboxes:
                mailbox = mailboxes[0]
                if mailbox.name == "0" * 32 or mailbox.resolve(strict=True) != mailbox:
                    raise ValueError("The installed runtime published an invalid bridge")
                return mailbox
            await asyncio.sleep(.1)
        raise RuntimeError(f"The shipping process exited before publishing its bridge ({process.returncode})")


async def verify(work, first_game, second_game, original):
    from bootstrap.resident_boot_config import configure

    games = first_game, second_game
    if (os.name != "nt" or any("steamapps" in {part.casefold() for part in root.parts} for root in (work, *games))
            or any(left.is_relative_to(right) or right.is_relative_to(left)
                   for left, right in combinations((work, *games, original), 2))):
        raise ValueError("Use two separate private replicas and a private workspace outside the original saves")
    before = saved_hashes(original)
    if not before:
        raise ValueError("Supply the original single-player save folder")
    probes = work / "title-probes"
    if probes.resolve() != probes:
        raise ValueError("The proof directory must stay in the private workspace")
    case = probes / ("installed-title-" + uuid.uuid4().hex[:8])
    case.mkdir(parents=True)
    print(case, flush=True)

    processes, seats, baselines = [], [], []
    proof = {"verified": False, "sessions": [], "native_bridges": []}
    try:
        for game, port in zip(games, choose_ports(), strict=True):
            name = "title-host-settings-" + uuid.uuid4().hex[:8]
            title = work / "titles" / name
            profile = title / "game-user"
            configure(game, work, seat=name, campaign=name, bind="127.0.0.1", port=port, isolated=True)
            mailbox_root = title / "run" / "game-bridge"
            assert mailbox_root.is_dir() and not any(mailbox_root.iterdir()), "The bridge must be absent before launch"
            stock = profile / "Saved" / "SaveGames"
            stock.mkdir(parents=True, exist_ok=True)
            for path in (original / "SaveGames").glob("*.sav"):
                shutil.copy2(path, stock / path.name)
            hashes = stock_save_hashes(profile)
            assert "Config.sav" in hashes and len(hashes) >= 2
            del hashes["Config.sav"]

            process = launch_installed(game, profile)
            processes.append((game, process))
            mailbox = await native_mailbox(mailbox_root, process)
            seat = Seat(profile, work / "campaigns" / name, work / "clients" / name,
                        port, runtime.Bridge(mailbox, process))
            seats.append(seat)
            await offered(seat)
            proof["native_bridges"].append({"seat": name, "pid": process.pid,
                                           "bridge": mailbox.name, "installed_image": str(game / runtime.SHIPPING)})
            baselines.append((hashes, await stock_profile(seat.bridge)))

        first, second = seats
        proof["sessions"].append(await start_pair(first, second, "Second seat scout"))
        proof["sessions"].append(await start_pair(second, first, "First seat scout"))
        assert proof["sessions"][0]["host_pid"] == proof["sessions"][1]["guest_pid"]
        assert proof["sessions"][0]["guest_pid"] == proof["sessions"][1]["host_pid"]
        assert len({row["campaign_id"] for row in proof["sessions"]}) == 2
        for seat, (hashes, preferences) in zip(seats, baselines, strict=True):
            observed = stock_save_hashes(seat.profile)
            del observed["Config.sav"]
            assert observed == hashes and await stock_profile(seat.bridge) == preferences
            assert seat.bridge.process.poll() is None
        proof.update(verified=True, solo_slots_and_settings_preserved=True, both_titles_reused=True,
                     launched_without_coop_environment=True, installed_isolated_profiles=True)
    finally:
        proof["native_boot_states"] = {str(game): boot_states(game) for game, _ in processes}
        for _, process in reversed(processes):
            if process.poll() is None:
                await runtime.stop_game(process)
        proof["original_saves_verified"] = saved_hashes(original) == before
        proof["verified"] &= proof["original_saves_verified"]
        runtime.write_json(case / "installed-title-verification.json", proof)
        assert proof["original_saves_verified"], "The original player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.first_game.resolve(strict=True),
                       args.second_game.resolve(strict=True), args.vanilla_saved.resolve(strict=True)))
