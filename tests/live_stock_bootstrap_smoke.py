"""Verify the read-only co-op entry in a stock-layout Windows replica."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_multi_smoke import saved_hashes
from tools.silverfish import runtime


BOOTSTRAP = "SilverfishCoopBootstrap"
STATE = re.compile(r"\[SilverfishCoopBootstrap\] state=([a-z_]+)\b")


async def verify(work: Path, original: Path) -> None:
    if os.name != "nt":
        raise OSError("The stock-layout title proof requires Windows")
    from tools.silverfish.win_job import GameProcess

    stage = work / "stage"
    game = (stage / runtime.SHIPPING).resolve(strict=True)
    mods = game.parent / "ue4ss" / "Mods"
    if game != stage / runtime.SHIPPING or {entry.name for entry in mods.iterdir()} != {BOOTSTRAP, "mods.txt"}:
        raise ValueError("Use the dedicated replica with only the title bootstrap installed")
    if (mods / "mods.txt").read_text(encoding="utf-8").strip() != f"{BOOTSTRAP} : 1":
        raise ValueError("The isolated stock title must load only the bootstrap")

    root = Path(__file__).resolve().parents[1]
    shared = root / "mod" / "SilverfishCoop" / "Scripts"
    scripts = mods / BOOTSTRAP / "Scripts"
    expected = {"main.lua": root / "bootstrap" / BOOTSTRAP / "Scripts" / "main.lua",
                "native_title_entry.lua": shared / "native_title_entry.lua",
                "native_title_display.lua": shared / "native_title_display.lua"}
    if {entry.name for entry in scripts.iterdir()} != expected.keys() or any(
        (scripts / name).read_bytes() != source.read_bytes() for name, source in expected.items()
    ):
        raise ValueError("The replica must contain the exact bootstrap source under test")

    originals = saved_hashes(original)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory")
    case = work / "title-probes" / ("stock-bootstrap-" + uuid.uuid4().hex[:8])
    profile = case / "game-user"
    profile.mkdir(parents=True)
    print(case, flush=True)
    evidence = {"verified": False}
    process = None
    log = game.parent / "ue4ss" / "UE4SS.log"
    arguments = subprocess.list2cmdline([str(game)])
    arguments += (f' -userdir="{profile}" -unattended -NoSplash -windowed'
                  ' -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480'
                  ' -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,'
                  '[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock')
    environment = {name: value for name, value in os.environ.items() if not name.startswith("SILVERFISH_COOP_")}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    baseline = log.stat().st_mtime_ns if log.is_file() else None
    try:
        process = GameProcess(arguments, executable=game, cwd=game.parent, env=environment, startupinfo=startup)
        async with asyncio.timeout(30):
            while process.poll() is None:
                if log.is_file() and log.stat().st_mtime_ns != baseline:
                    output = log.read_text(encoding="utf-8", errors="replace")
                    states = STATE.findall(output)
                    evidence["states"] = states
                    if "error" in states:
                        raise RuntimeError("The stock title bootstrap reported an error")
                    if "unavailable" in states:
                        break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("The stock-layout game exited before the bootstrap mounted")
        assert states in (["unavailable"], ["waiting_title", "unavailable"]), states
        saves = {path.relative_to(profile).as_posix() for path in profile.rglob("*.sav")}
        assert saves == {"Saved/SaveGames/Config.sav"}, saves
        evidence.update(verified=True, loaded_mod=BOOTSTRAP, isolated_title_files=sorted(saves))
    except (OSError, RuntimeError, ValueError, AssertionError) as error:
        evidence["failure"] = str(error)
        raise
    finally:
        if process is not None:
            await runtime.stop_game(process)
        evidence["original_saves_verified"] = saved_hashes(original) == originals
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        (case / "live-stock-bootstrap-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "The original stock save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True)))
