"""Stage a private game copy and exchange bounded local messages with UE4SS."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import subprocess
import tempfile
import time
import uuid

REPO = Path(__file__).resolve().parents[2]
SHIPPING = Path("SilverFish/Binaries/Win64/SilverFish-Win64-Shipping.exe")
PAKS = Path("SilverFish/Content/Paks")
MANIFEST = Path("Manifest_NonUFSFiles_Win64.txt")
STOCK_PACKAGES = ("SilverFish-Windows.pak", "SilverFish-Windows.utoc",
                  "SilverFish-Windows.ucas", "global.utoc", "global.ucas")
MAP = "/Game/Classes/Maps/PlaytestMap_01"
MENU = "/Game/Classes/Maps/MainMenu_P"


def write_text(path: Path, data: str) -> None:
    """Publish a complete file on the same volume; retry Windows sharing races."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(data, encoding="utf-8")
        for attempt in range(5):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")


def read_json(path: Path, limit: int = 1_048_576) -> dict:
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("JSON file exceeds its size limit")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def _manifest_paths(source: Path) -> list[Path]:
    files = [PAKS / name for name in STOCK_PACKAGES]
    for line in (source / MANIFEST).read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        asset = PurePosixPath(line.partition("\t")[0])
        if asset.is_absolute() or ".." in asset.parts or "\\" in str(asset) or ":" in str(asset):
            raise ValueError(f"Invalid file path in Steam's staging manifest: {asset}")
        files.append(Path(*asset.parts))
    required = {SHIPPING, Path("SilverFish.exe")}
    if not required.issubset(files):
        raise ValueError("The source is missing the Project Silverfish Windows staging manifest entries")
    return files


def prepare_game(work: Path, source: Path, ue4ss: Path, signatures: Path | None) -> dict:
    """Copy stock files only, then install the pinned loader and this mod in the copy."""
    source = source.resolve(strict=True)
    ue4ss = ue4ss.resolve(strict=True)
    work = work.resolve()
    stage = work / "stage"
    if work.is_relative_to(source) or source.is_relative_to(stage):
        raise ValueError("The prototype workspace and original installation must be separate")
    if stage.exists():
        raise ValueError(f"Stage already exists at {stage}. Use a new workspace to stage a fresh copy.")
    for relative in (Path("dwmapi.dll"), Path("ue4ss/UE4SS.dll"), Path("ue4ss/UE4SS-settings.ini")):
        if not (ue4ss / relative).is_file():
            raise ValueError(f"UE4SS release folder is missing {relative}")

    files = _manifest_paths(source)
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=work) as temporary:
        building = Path(temporary) / "stage"
        for relative in files:
            original = (source / relative).resolve(strict=True)
            if not original.is_relative_to(source):
                raise ValueError(f"Staged file points outside the source: {relative}")
            target = building / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)

        binaries = (building / SHIPPING).parent
        shutil.copy2(ue4ss / "dwmapi.dll", binaries)
        loader = binaries / "ue4ss"
        shutil.copytree(ue4ss / "ue4ss", loader)
        if signatures:
            shutil.copytree(signatures, loader / "UE4SS_Signatures", dirs_exist_ok=True)
        settings = loader / "UE4SS-settings.ini"
        disable = {"ConsoleEnabled", "GuiConsoleEnabled", "GuiConsoleVisible", "EnableHotReloadSystem"}
        configured = []
        for line in settings.read_text(encoding="utf-8-sig").splitlines():
            key = line.partition("=")[0].strip()
            configured.append(f"{key} = 0" if key in disable else line)
        write_text(settings, "\n".join(configured) + "\n")
        shutil.copytree(REPO / "mod/SilverfishCoop", loader / "Mods/SilverfishCoop")
        write_text(loader / "Mods/mods.txt", "SilverfishCoop : 1\n")

        digest = hashlib.sha256()
        for relative in (SHIPPING, *(PAKS / name for name in STOCK_PACKAGES)):
            with (building / relative).open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
        metadata = {"schema": 1, "game_build": digest.hexdigest(), "stock_files": len(files)}
        write_json(building / "silverfish-coop-stage.json", metadata)
        building.replace(stage)
    return metadata


def staged_build(work: Path) -> str:
    stage = work / "stage"
    metadata = read_json(stage / "silverfish-coop-stage.json")
    if metadata.get("schema") != 1 or not (stage / SHIPPING).is_file():
        raise ValueError("Stage is missing or incompatible")
    game_build = metadata.get("game_build")
    if not isinstance(game_build, str) or len(game_build) != 64:
        raise ValueError("Stage game fingerprint is invalid")
    return game_build


def spare_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def verify_loopback_socket(pid: int, port: int) -> None:
    result = subprocess.run(["netstat.exe", "-ano", "-p", "udp"], capture_output=True,
                            text=True, check=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    endpoints = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[0] == "UDP" and fields[-1] == str(pid):
            endpoints.add(fields[1])
    expected = f"127.0.0.1:{port}"
    matching = {endpoint for endpoint in endpoints if endpoint.endswith(f":{port}")}
    if matching != {expected}:
        raise RuntimeError("The staged game did not bind its expected loopback-only UDP socket")


def launch(work: Path, profile: Path, bridge: Path, headless: bool,
           port: int | None = None):
    """Launch the staged executable with a dedicated Unreal user directory."""
    game = (work / "stage" / SHIPPING).resolve(strict=True)
    if not game.is_relative_to(work.resolve() / "stage"):
        raise ValueError("The staged shipping executable points outside the stage")
    profile.mkdir(parents=True, exist_ok=True)
    bridge.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, SILVERFISH_COOP_BRIDGE=str(bridge))
    arguments = subprocess.list2cmdline([str(game)])
    arguments += f' -userdir="{profile}" -MULTIHOME=127.0.0.1 -unattended -NoSplash -windowed'
    if port is not None:
        arguments += f" -port={port}"
    if headless:
        arguments += " -nullrhi -nosound -ResX=640 -ResY=480"
    info = None
    if headless:
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
    from .win_job import GameProcess

    return GameProcess(arguments, executable=game, cwd=game.parent, env=env,
                       startupinfo=info)


async def stop_game(process) -> None:
    process.close_job()
    await asyncio.to_thread(process.wait, timeout=10)


def _finite(value: str) -> float | None:
    if value == "-":
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Snapshot contained a non-finite number")
    return number


class Bridge:
    """A single controller for one game's local, allowlisted commands."""

    def __init__(self, directory: Path, process: subprocess.Popen):
        self.directory = directory
        self.process = process

    def snapshot(self) -> dict | None:
        try:
            text = (self.directory / "status.tsv").read_text(encoding="utf-8")
            data = {"pawns": {}}
            for line in text.splitlines():
                fields = line.split("\t")
                if fields[0] == "schema" and fields[1:] == ["1"]:
                    data["schema"] = 1
                elif fields[0] == "seq" and len(fields) == 2:
                    data["sequence"] = int(fields[1])
                elif fields[0] == "ack" and len(fields) >= 3:
                    data["ack"] = {"id": fields[1], "result": fields[2],
                                   "message": fields[3] if len(fields) > 3 else ""}
                elif fields[0] == "diagnostic" and len(fields) == 2:
                    data["diagnostic"] = fields[1]
                elif fields[0] == "session" and len(fields) == 6:
                    data.update({"mode": fields[1], "state": fields[2], "map": fields[3],
                                 "remote_clients": int(fields[4]), "server_connection": fields[5] == "1"})
                elif fields[0] == "pawn" and len(fields) == 8:
                    data["pawns"][fields[1]] = {"name": fields[2], "health": _finite(fields[3]),
                                               "location": [_finite(axis) for axis in fields[4:7]],
                                               "controller": fields[7]}
            if data.get("schema") == 1 and "state" in data:
                return data
        except (OSError, UnicodeError, ValueError):
            return None
        return None

    async def wait_for(self, condition, timeout: float = 30) -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Staged game exited with code {self.process.returncode}")
            snapshot = self.snapshot()
            if snapshot is not None and condition(snapshot):
                return snapshot
            await asyncio.sleep(0.15)
        raise TimeoutError("The game bridge did not reach the requested state")

    async def command(self, action: str, *arguments: str) -> dict:
        fields = (uuid.uuid4().hex, action, *arguments)
        if any(len(value) > 1024 or "\t" in value or "\r" in value or "\n" in value for value in fields):
            raise ValueError("Invalid game bridge command value")
        write_text(self.directory / "command.tsv", "\t".join(fields) + "\n")
        status = await self.wait_for(lambda state: state.get("ack", {}).get("id") == fields[0])
        ack = status["ack"]
        if ack["result"] != "accepted":
            raise RuntimeError(ack["message"] or f"The game declined the {action} command")
        return status

    async def restore(self, role: str, pawn: dict) -> None:
        coordinates = pawn.get("location")
        if not isinstance(coordinates, list) or len(coordinates) != 3:
            return
        if any(not isinstance(axis, (int, float)) or not math.isfinite(axis) for axis in coordinates):
            return
        health = pawn.get("health")
        raw_health = str(health) if isinstance(health, (int, float)) and math.isfinite(health) else "-"
        await self.command("restore", role, raw_health, *(str(axis) for axis in coordinates))
