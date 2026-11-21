"""Verify two private listen sessions from one stock Windows title process."""

import argparse
import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_multi_smoke import saved_hashes
from tools.silverfish import runtime, udp_ownership
from tools.silverfish.win_job import GameProcess


MOD = "TitleListenProof"
PUBLIC_PORT = 7777


def install_fixture(game: Path, addon: Path) -> None:
    mods = game.parent / "ue4ss/Mods"
    mods.mkdir(exist_ok=True)
    if mods.resolve(strict=True) != mods or any(path.name not in {MOD, "mods.txt"} for path in mods.iterdir()):
        raise ValueError("Use the dedicated stock replica for the listen fixture")
    scripts = Path(__file__).parent / "fixtures"
    contents = {
        mods / MOD / "Scripts/main.lua": (scripts / "same_process_listener.lua").read_bytes(),
        mods / MOD / "dlls/main.dll": addon.read_bytes(),
        mods / "mods.txt": f"{MOD} : 1\n".encode("ascii"),
    }
    for path, source in contents.items():
        if path.resolve() != path or (path.exists() and path.stat().st_nlink != 1):
            raise ValueError("The fixture paths must be ordinary files")
        if path.name == "mods.txt" and path.exists() and path.read_bytes().replace(b"\r\n", b"\n") != source:
            raise ValueError("The stock replica has another mods list")
    for path, source in contents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)


def choose_ports() -> tuple[int, int]:
    with ExitStack() as stack:
        listeners = [stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) for _ in range(2)]
        for listener in listeners:
            listener.bind(("127.0.0.1", 0))
        return tuple(listener.getsockname()[1] for listener in listeners)


async def observe(case: Path, process: GameProcess, expected: str) -> list[str]:
    async with asyncio.timeout(20):
        while process.poll() is None:
            try:
                rows = (case / "progress.tsv").read_text(encoding="ascii").splitlines()
            except (FileNotFoundError, PermissionError):
                rows = []
            if any(row.startswith("arm_refused") for row in rows):
                raise RuntimeError("The native listener refused the stock title's request")
            if expected in rows:
                return rows
            await asyncio.sleep(.05)
    raise RuntimeError(f"The stock game exited before reporting {expected!r} ({process.returncode})")


async def verify(work: Path, addon: Path, original: Path) -> None:
    if (os.name != "nt" or "steamapps" in {part.casefold() for part in work.parts}
            or work.is_relative_to(original) or original.is_relative_to(work)):
        raise ValueError("Use a dedicated Windows replica and a disjoint original save tree")
    game = work / "stage" / runtime.SHIPPING
    if game.resolve(strict=True) != game or not game.is_file():
        raise ValueError("The stock replica executable must be an ordinary path")
    originals = saved_hashes(original)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory for hash comparison")
    install_fixture(game, addon)
    case = work / "title-probes" / f"same-listen-{uuid.uuid4().hex[:12]}"
    profile = case / "game-user"
    profile.mkdir(parents=True)
    print(case, flush=True)
    command = case / "command.txt"
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("SILVERFISH_COOP_", "UE4SS_", "SF_LISTEN_"))}
    env.update(SILVERFISH_COOP_HEADLESS="1", SF_LISTEN_NATIVE_PROBE="1", SF_LISTEN_PROOF_DIRECTORY=str(case))
    arguments = subprocess.list2cmdline([str(game)])
    arguments += (f' -userdir="{profile}" -unattended -NoSplash -windowed'
                  ' -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480'
                  ' -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,'
                  '[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    evidence = {"verified": False, "public_test_port": PUBLIC_PORT, "sessions": []}
    process = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as public:
            public.bind(("127.0.0.1", PUBLIC_PORT))
            ports = choose_ports()
            env.update(SF_LISTEN_PORT_1=str(ports[0]), SF_LISTEN_PORT_2=str(ports[1]))
            process = GameProcess(arguments, executable=game, cwd=game.parent, env=env, startupinfo=startup)
            evidence["pid"] = process.pid
            await observe(case, process, "title\t1")
            for index, port in enumerate(ports, 1):
                runtime.write_text(command, f"host{index}\n", newline="\n")
                await observe(case, process, f"host\t{index}\t1\t1\t1")
                udp_ownership.verify_loopback_socket(process.pid, port)
                assert not udp_ownership.owns_udp_port(process.pid, PUBLIC_PORT)
                assert not udp_ownership.owns_udp_port(process.pid, ports[2 - index])
                runtime.write_text(command, f"leave{index}\n", newline="\n")
                evidence["events"] = await observe(case, process, f"title\t{index + 1}")
                assert not udp_ownership.owns_udp_port(process.pid, port), "The retired listener is still bound"
                evidence["sessions"].append({"port": port, "loopback_only": True, "closed_at_title": True,
                                             "stock_bind_restored": True})
            evidence["verified"] = True
    except (OSError, RuntimeError, ValueError, TimeoutError, AssertionError) as error:
        evidence["failure"] = repr(error)
        raise
    finally:
        if process is not None:
            await runtime.stop_game(process)
        evidence["original_saves_verified"] = saved_hashes(original) == originals
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        runtime.write_json(case / "live-same-process-listener-verification.json", evidence)
        assert evidence["original_saves_verified"], "The original single-player save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--addon", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.addon.resolve(strict=True), args.vanilla_saved.resolve(strict=True)))
