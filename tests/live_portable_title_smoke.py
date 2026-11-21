"""Cold-start two private replicas with the drag-and-drop co-op release."""

import argparse
import asyncio
from dataclasses import dataclass
from itertools import combinations
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap import install_stage
from tools.silverfish import embedded_startup, runtime
from tools.silverfish.control import NAME
from tools.silverfish.win_job import GameProcess
from live_installed_title_smoke import boot_states, native_mailbox
from live_multi_smoke import saved_hashes
from live_resident_host_smoke import stock_profile, stock_save_hashes
from live_same_process_listener_smoke import choose_ports
from live_unified_title_smoke import Seat, offered, start_pair


@dataclass(frozen=True)
class PortableRelease:
    package: Path
    manifest_sha256: str
    title_directory: Path
    selection: dict
    activation: dict[Path, str | None]


def launch_portable(game: Path, profile: Path) -> GameProcess:
    image = game / runtime.SHIPPING
    install_stage._ordinary(image)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("SILVERFISH_COOP_")}
    arguments = subprocess.list2cmdline([str(image)])
    arguments += (f' -SilverfishCoopIsolated -userdir="{profile}" -unattended -NoSplash -windowed'
                  " -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480"
                  " -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,"
                  "[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock")
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return GameProcess(arguments, executable=image, cwd=image.parent, env=env, startupinfo=info)


def fingerprint(path: Path) -> str | None:
    if not os.path.lexists(path):
        return None
    install_stage._ordinary(path)
    return runtime._file_sha256(path)


def qualify_release(game: Path, legacy_work: Path | None) -> PortableRelease:
    mods = game / runtime.SHIPPING.parent / "ue4ss" / "Mods"
    embedded = mods / install_stage.MOD
    marker = embedded / "resident-portable.json"
    descriptor = embedded / "resident-install.json"
    stage = game / "silverfish-coop-stage.json"
    activation = {
        mods / "mods.txt": fingerprint(mods / "mods.txt"),
        descriptor: fingerprint(descriptor),
        marker: fingerprint(marker),
        stage: fingerprint(stage),
    }
    if activation[marker] is None or activation[stage] is None:
        raise ValueError("Apply the release ZIP over the stock replica before running this test")
    for module in ("SilverfishCoop", runtime.CPP_MOD_NAME, install_stage.MOD):
        enabled = mods / module / "enabled.txt"
        install_stage._ordinary(enabled)
        activation[enabled] = fingerprint(enabled)
    metadata = runtime.read_json(marker, limit=8192)
    if metadata["schema"] != 1 or runtime._file_sha256(game / runtime.SHIPPING) != metadata["shipping_sha256"]:
        raise ValueError("The portable release does not match the shipping executable")

    addon = embedded / "dlls" / "main.dll"
    install_stage._ordinary(addon)
    image = addon.read_bytes()
    pinned = []
    for candidate in embedded.glob("runtime-*"):
        if re.fullmatch(r"runtime-[0-9a-f]{16}", candidate.name) and candidate.is_dir():
            digest = fingerprint(candidate / "runtime-manifest.json")
            if digest is not None and digest.encode("ascii") in image:
                manifest, _, _ = install_stage._manifest(candidate, addon)
                if candidate.name == "runtime-" + manifest[:16]:
                    pinned.append((candidate, manifest))
    if len(pinned) != 1:
        raise ValueError("The DLL must pin exactly one qualified versioned embedded runtime")
    package, digest = pinned[0]

    boot = {"schema": 1, "campaign": "default", "bind": "0.0.0.0", "port": 7777}
    seat_name = "local"
    if legacy_work is None:
        if activation[descriptor] is not None:
            raise ValueError("A clean portable replica must have no resident-install.json")
    else:
        if activation[descriptor] is None:
            raise ValueError("The upgrade replica needs its original isolated resident-install.json")
        legacy = runtime.read_json(descriptor, limit=8192)
        if (legacy.get("schema"), legacy.get("isolated"), legacy.get("shipping_sha256")) != (
                1, True, metadata["shipping_sha256"]
        ) or Path(legacy["work"]) != legacy_work or not NAME.fullmatch(legacy["seat"]):
            raise ValueError("The original upgrade descriptor must point at the supplied private isolated workspace")
        seat_name = legacy["seat"]
        boot.update({key: legacy[key] for key in ("campaign", "bind", "port")})
    return PortableRelease(package, digest, Path("titles") / seat_name, boot, activation)


async def discover_mailbox(root: Path, process: GameProcess) -> Path:
    async with asyncio.timeout(30):
        while process.poll() is None and not root.is_dir():
            await asyncio.sleep(.1)
        if process.poll() is not None:
            raise RuntimeError(f"The shipping executable exited before creating its portable mailbox ({process.returncode})")
        return await native_mailbox(root, process)


async def verify(work: Path, first_game: Path, second_game: Path, original: Path,
                 second_legacy_work: Path | None = None) -> None:
    roots = (work, first_game, second_game, original)
    if second_legacy_work is not None:
        roots += (second_legacy_work,)
    if (os.name != "nt" or any("steamapps" in {part.casefold() for part in path.parts} for path in roots if path != original)
            or any(a.is_relative_to(b) or b.is_relative_to(a) for a, b in combinations(roots, 2))):
        raise ValueError("Use separate private replica and work directories outside the original saves")
    before = saved_hashes(original)
    stock_slots = sorted((original / "SaveGames").glob("*.sav"))
    if not before or len(stock_slots) < 2 or not any(path.name == "Config.sav" for path in stock_slots):
        raise ValueError("Supply the original Saved directory with a stock configuration and at least one campaign slot")
    qualified = (qualify_release(first_game, None), qualify_release(second_game, second_legacy_work))
    if qualified[0].manifest_sha256 != qualified[1].manifest_sha256:
        raise ValueError("Both replicas must use the same portable release")
    if qualified[0].activation[first_game / runtime.SHIPPING.parent / "ue4ss/Mods/mods.txt"] is not None:
        raise ValueError("The first replica must have no mods.txt")

    case = work / ("portable-title-" + uuid.uuid4().hex[:8])
    case.mkdir()
    print(case, flush=True)
    workspaces = case / "first", second_legacy_work or case / "second"
    games = first_game, second_game
    proof = {"verified": False, "native_bridges": [], "sessions": []}
    processes, seats, baselines = [], [], []

    try:
        for game, workspace, release, port in zip(games, workspaces, qualified, choose_ports(), strict=True):
            title = workspace / release.title_directory
            profile, mailbox_root = title / "game-user", title / "run/game-bridge"
            if mailbox_root.is_dir() and any(mailbox_root.iterdir()):
                raise ValueError("Use an unused isolated title configuration for the upgrade test")
            if os.path.lexists(mailbox_root):
                install_stage._ordinary(mailbox_root, directory=True)
            stock = profile / "Saved/SaveGames"
            stock.mkdir(parents=True, exist_ok=True)
            install_stage._ordinary(stock, directory=True)
            for source in stock_slots:
                destination = stock / source.name
                if not os.path.lexists(destination):
                    shutil.copy2(source, destination)
                install_stage._ordinary(destination)
            hashes = stock_save_hashes(profile)
            del hashes["Config.sav"]

            process = launch_portable(game, profile)
            processes.append((game, process))
            mailbox = await discover_mailbox(mailbox_root, process)
            assert runtime.read_json(mailbox / embedded_startup.UNIFIED_FILE) == release.selection
            campaign = "title-host-settings-" + uuid.uuid4().hex[:8]
            seat = Seat(profile, workspace / "campaigns" / campaign, workspace / "clients" / title.name,
                        port, runtime.Bridge(mailbox, process))
            seats.append(seat)
            await offered(seat)
            proof["native_bridges"].append({
                "seat": title.name, "pid": process.pid, "bridge": mailbox.name,
                "runtime": release.package.name, "runtime_manifest_sha256": release.manifest_sha256,
                "workspace": str(workspace), "legacy_descriptor_selected": workspace == second_legacy_work,
            })
            baselines.append((hashes, await stock_profile(seat.bridge)))

        first, second = seats
        proof["sessions"].append(await start_pair(first, second, "Second portable scout"))
        proof["sessions"].append(await start_pair(second, first, "First portable scout"))
        assert proof["sessions"][0]["host_pid"] == proof["sessions"][1]["guest_pid"]
        assert proof["sessions"][0]["guest_pid"] == proof["sessions"][1]["host_pid"]
        assert len({leg["campaign_id"] for leg in proof["sessions"]}) == 2
        for seat, (hashes, preferences) in zip(seats, baselines, strict=True):
            observed = stock_save_hashes(seat.profile)
            del observed["Config.sav"]
            assert observed == hashes and await stock_profile(seat.bridge) == preferences
            assert seat.bridge.process.poll() is None
        proof.update(verified=True, solo_slots_and_settings_preserved=True,
                     both_titles_reused=True, launched_without_coop_environment=True)
    finally:
        proof["native_boot_states"] = {str(game): boot_states(game) for game, _ in processes}
        for _, process in reversed(processes):
            if process.poll() is None:
                await runtime.stop_game(process)
        proof["original_saves_verified"] = saved_hashes(original) == before
        proof["portable_files_and_existing_activation_preserved"] = all(
            fingerprint(path) == digest for release in qualified for path, digest in release.activation.items()
        )
        proof["verified"] &= proof["original_saves_verified"] and proof["portable_files_and_existing_activation_preserved"]
        runtime.write_json(case / "portable-title-verification.json", proof)
        assert proof["original_saves_verified"], "The original player saves changed"
        assert proof["portable_files_and_existing_activation_preserved"], "The portable marker or existing activation changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="Existing private parent for new evidence and profile directories")
    parser.add_argument("--first-game", type=Path, required=True, help="Clean replica with the release ZIP applied and no mods.txt")
    parser.add_argument("--second-game", type=Path, required=True, help="Separate replica with the same release ZIP applied")
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--second-legacy-work", type=Path,
                        help="Unused isolated workspace from the second replica's retained legacy descriptor")
    args = parser.parse_args()
    legacy = args.second_legacy_work.resolve(strict=True) if args.second_legacy_work else None
    asyncio.run(verify(args.work.resolve(strict=True), args.first_game.resolve(strict=True),
                       args.second_game.resolve(strict=True), args.vanilla_saved.resolve(strict=True), legacy))
