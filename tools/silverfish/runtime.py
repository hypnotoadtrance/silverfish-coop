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

from .native_gold import GoldRequest, INVENTORY_ENTRIES, valid_guid

REPO = Path(__file__).resolve().parents[2]
SHIPPING = Path("SilverFish/Binaries/Win64/SilverFish-Win64-Shipping.exe")
PAKS = Path("SilverFish/Content/Paks")
MANIFEST = Path("Manifest_NonUFSFiles_Win64.txt")
STOCK_PACKAGES = ("SilverFish-Windows.pak", "SilverFish-Windows.utoc",
                  "SilverFish-Windows.ucas", "global.utoc", "global.ucas")
MAP = "/Game/Classes/Maps/PlaytestMap_01"
MENU = "/Game/Classes/Maps/MainMenu_P"
CPP_MOD_NAME = "SilverfishCoopNative"
CPP_RUNTIME_DLL = Path("ue4ss/UE4SS.dll")
CPP_ADDON_DLL = Path(f"ue4ss/Mods/{CPP_MOD_NAME}/dlls/main.dll")
DEATH_PATCH = "zzzz_SilverfishCoop_Death_P"
DEATH_PATCH_HASHES = {
    ".pak": "75e7144577253917f6da7312ef5e585b12fb728226a22b0938323751a6b555cd",
    ".ucas": "26ccbb17669b9de03bef98b1fc552a91cc430b7530a9baca93f0cad6addae5b4",
    ".utoc": "d34b8791c49c88c4d81e7fff3aeefbf29e1c27c60c179c999bd45464e9321f4d",
}


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


def _file_sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def prepare_game(work: Path, source: Path, ue4ss: Path, signatures: Path | None, *,
                 cpp_runtime: Path | None = None, cpp_addon: Path | None = None) -> dict:
    """Copy stock files only, then install the pinned loader and this mod in the copy."""
    if bool(cpp_runtime) != bool(cpp_addon):
        raise ValueError("Supply both the locally built UE4SS runtime and its matching C++ add-on")
    for dll in (cpp_runtime, cpp_addon):
        if dll and (dll.suffix.lower() != ".dll" or not dll.is_file()):
            raise ValueError(f"Native module input is not a DLL file: {dll}")
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
        enabled_mods = "SilverfishCoop : 1\n"
        cpp_hashes = None
        if cpp_runtime and cpp_addon:
            for source_dll, relative in ((cpp_runtime, CPP_RUNTIME_DLL), (cpp_addon, CPP_ADDON_DLL)):
                target = binaries / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_dll, target)
            cpp_hashes = {"runtime": _file_sha256(binaries / CPP_RUNTIME_DLL),
                          "addon": _file_sha256(binaries / CPP_ADDON_DLL)}
            enabled_mods = f"{CPP_MOD_NAME} : 1\n" + enabled_mods
        write_text(loader / "Mods/mods.txt", enabled_mods)

        digest = hashlib.sha256()
        for relative in (SHIPPING, *(PAKS / name for name in STOCK_PACKAGES)):
            with (building / relative).open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
        metadata = {"schema": 1, "game_build": digest.hexdigest(), "stock_files": len(files)}
        if cpp_hashes is not None:
            metadata["cpp_death_lab"] = cpp_hashes
        write_json(building / "silverfish-coop-stage.json", metadata)
        building.replace(stage)
    return metadata


def has_native_death_patch(work: Path) -> bool:
    packs = work / "stage" / PAKS
    files = {suffix: packs / (DEATH_PATCH + suffix) for suffix in DEATH_PATCH_HASHES}
    if not any(path.exists() for path in files.values()):
        return False
    for suffix, path in files.items():
        if not path.is_file():
            raise ValueError("Incomplete locally generated death lab patch")
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != DEATH_PATCH_HASHES[suffix]:
                raise ValueError("The local death lab patch has an unqualified file fingerprint")
    return True


def native_death_backend(work: Path, metadata: dict | None = None) -> str | None:
    stage = work / "stage"
    if metadata is None:
        metadata = read_json(stage / "silverfish-coop-stage.json")
    overlay = has_native_death_patch(work)
    native = metadata.get("cpp_death_lab")
    binaries = (stage / SHIPPING).parent
    if native is None:
        if (binaries / CPP_ADDON_DLL).exists():
            raise ValueError("The C++ add-on is missing its stage manifest")
        return "overlay" if overlay else None
    if overlay or not isinstance(native, dict) or set(native) != {"runtime", "addon"}:
        raise ValueError("The C++ death lab requires a separate stage with stock cooked packages")
    for key, relative in (("runtime", CPP_RUNTIME_DLL), ("addon", CPP_ADDON_DLL)):
        path = binaries / relative
        if not path.is_file() or _file_sha256(path) != native[key]:
            raise ValueError(f"The staged native lab {key} differs from its manifest")
    return "cpp"


def qualified_authored_routes(work: Path) -> bool:
    metadata = read_json(work / "stage" / "silverfish-coop-stage.json")
    return (metadata.get("game_build") == "8682dc057e1b38738862760a8c3b848a837c81d72c0e6ddffa3b7f963960374d"
            and native_death_backend(work, metadata) == "cpp")


def staged_build(work: Path) -> str:
    stage = work / "stage"
    metadata = read_json(stage / "silverfish-coop-stage.json")
    if metadata.get("schema") != 1 or not (stage / SHIPPING).is_file():
        raise ValueError("Stage is missing or incompatible")
    game_build = metadata.get("game_build")
    if not isinstance(game_build, str) or len(game_build) != 64:
        raise ValueError("Stage game fingerprint is invalid")
    backend = native_death_backend(work, metadata)
    if backend == "overlay":
        return hashlib.sha256((game_build + ":native-death-lab:1").encode("ascii")).hexdigest()
    if backend == "cpp":
        native = metadata["cpp_death_lab"]
        fingerprint = ":".join((game_build, "native-death-cpp", native["runtime"], native["addon"]))
        return hashlib.sha256(fingerprint.encode("ascii")).hexdigest()
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
           port: int | None = None, *, native_events: bool = False, native_guest: bool = False,
           native_zones: bool = False, native_zone_walls: bool = False, native_fixtures: bool = False):
    """Launch the staged executable with a dedicated Unreal user directory."""
    game = (work / "stage" / SHIPPING).resolve(strict=True)
    if not game.is_relative_to(work.resolve() / "stage"):
        raise ValueError("The staged shipping executable points outside the stage")
    profile.mkdir(parents=True, exist_ok=True)
    bridge.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, SILVERFISH_COOP_BRIDGE=str(bridge))
    env.pop("SILVERFISH_COOP_NATIVE_DEATH", None)
    env.pop("SILVERFISH_COOP_NATIVE_EVENTS", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_SAVE", None)
    env.pop("SILVERFISH_COOP_NATIVE_ZONE", None)
    env.pop("SILVERFISH_COOP_NATIVE_ZONE_WALLS", None)
    env.pop("SILVERFISH_COOP_NATIVE_TRANSPORT", None)
    env.pop("SILVERFISH_COOP_NATIVE_FIXTURES", None)
    backend = native_death_backend(work)
    if native_events and (backend != "cpp" or port is None):
        raise ValueError("Native event scope requires a staged C++ listen host")
    if native_guest and not native_events:
        raise ValueError("Native guest ownership requires native event scope")
    if native_zones and backend != "cpp":
        raise ValueError("Natural exit handling requires a staged C++ game")
    if native_zone_walls and not native_zones:
        raise ValueError("Physical exit blockers require native exit handling")
    if backend:
        env["SILVERFISH_COOP_NATIVE_DEATH"] = "1" if backend == "overlay" else backend
    if native_events:
        env["SILVERFISH_COOP_NATIVE_EVENTS"] = "1"
    if native_guest:
        env["SILVERFISH_COOP_NATIVE_GUEST_SAVE"] = "1"
    if native_zones:
        env["SILVERFISH_COOP_NATIVE_ZONE"] = "1"
        env["SILVERFISH_COOP_NATIVE_TRANSPORT"] = "1"
        if port is not None and native_fixtures:
            env["SILVERFISH_COOP_NATIVE_FIXTURES"] = "1"
    if native_zone_walls:
        env["SILVERFISH_COOP_NATIVE_ZONE_WALLS"] = "1"
    arguments = subprocess.list2cmdline([str(game)])
    arguments += f' -userdir="{profile}" -MULTIHOME=127.0.0.1 -unattended -NoSplash -windowed'
    if port is not None:
        arguments += f" -port={port}"
    info = None
    if headless:
        arguments += " -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480"
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
    from .win_job import GameProcess

    return GameProcess(arguments, executable=game, cwd=game.parent, env=env, startupinfo=info)


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
        self.input_namespace = uuid.uuid4()
        self.input_offset = 0
        self.death_offset = 0
        self.zone_offset = 0
        self.transport_offsets = {"gold": 0, "blue": 0}

    def interactions(self) -> list[str]:
        """Consume complete local input records once, even if writing is in progress."""
        try:
            with (self.directory / "input.tsv").open("rb") as stream:
                stream.seek(self.input_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.input_offset += end if end else len(batch) if len(batch) == 4096 else 0
        results = []
        for line in batch[:end].splitlines():
            action, _, count = line.partition(b"\t")
            if action == b"interact" and count.isdigit() and len(count) <= 12:
                results.append(uuid.uuid5(self.input_namespace, count.decode("ascii")).hex)
        return results

    def native_deaths(self) -> list[tuple[str, str, str | None]]:
        try:
            with (self.directory / "death.tsv").open("rb") as stream:
                stream.seek(self.death_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.death_offset += end if end else len(batch) if len(batch) == 4096 else 0
        events = []
        for line in batch[:end].splitlines():
            fields = line.split(b"\t")
            if (len(fields) in (3, 4) and fields[0] == b"downed" and fields[1] in (b"local", b"guest")
                    and fields[2].isdigit() and len(fields[2]) <= 12):
                pawn = fields[3].decode("utf-8", errors="replace") if len(fields) == 4 else None
                identifier = uuid.uuid5(self.input_namespace, "death" + fields[2].decode("ascii")).hex
                events.append((fields[1].decode("ascii"), identifier, pawn))
        return events

    def native_zone_requests(self) -> list[tuple[int, str, str, str | None]]:
        try:
            with (self.directory / "native-zone-events").open("rb") as stream:
                stream.seek(self.zone_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.zone_offset += end if end else len(batch) if len(batch) == 4096 else 0
        requests = []
        for line in batch[:end].splitlines():
            fields = line.split(b"\t")
            if len(fields) not in (4, 5) or fields[0] != b"request" or not fields[1].isdigit() or len(fields[1]) > 12:
                continue
            requests.append((int(fields[1]), fields[2].decode("utf-8", errors="replace"),
                             fields[3].decode("utf-8", errors="replace"),
                             fields[4].decode("utf-8", errors="replace") if len(fields) == 5 else None))
        return requests

    def native_cube_requests(self, cube: str) -> list[GoldRequest]:
        offset = self.transport_offsets[cube]
        filename = "native-transport-events" if cube == "gold" else "native-blue-events"
        try:
            with (self.directory / filename).open("rb") as stream:
                stream.seek(offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.transport_offsets[cube] = offset + (end or (len(batch) if len(batch) == 4096 else 0))
        requests = []
        for line in batch[:end].splitlines():
            fields = line.decode("utf-8", errors="replace").split("\t")
            if len(fields) != 9 or fields[0] != f"{cube}-request" or not fields[1].isascii() \
                    or not fields[1].isdigit() or len(fields[1]) > 12:
                continue
            source, pawn, guid, entry = fields[2:6]
            try:
                location = tuple(_finite(value) for value in fields[6:])
            except ValueError:
                continue
            if (valid_guid(guid) and entry in INVENTORY_ENTRIES and 0 < len(pawn) <= 512
                    and all(value is not None and abs(value) <= 1e8 for value in location)):
                requests.append(GoldRequest(int(fields[1]), source, pawn, guid, entry, location))
        return requests

    def snapshot(self) -> dict | None:
        try:
            text = (self.directory / "status.tsv").read_text(encoding="utf-8")
            data = {"pawns": {}, "player_effects": {}, "loot_actors": []}
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
                elif fields[0] == "player" and len(fields) >= 6:
                    data["player_effects"][fields[1]] = {
                        "state": fields[2], "crouched": fields[3] == "1",
                        "speed": _finite(fields[4]), "jump_velocity": _finite(fields[5]),
                    }
                elif fields[0] == "player-visual" and len(fields) == 4:
                    data.setdefault("player_visuals", {})[fields[1]] = {
                        "input_gate": fields[2] == "1", "marker": fields[3] == "1",
                    }
                elif fields[0] == "native-inventory-canary" and len(fields) == 4:
                    data["native_inventory_canary"] = {
                        "state": fields[1], "binding_restored": fields[2] == "1", "detail": fields[3],
                    }
                elif fields[0] == "native-guest-save" and len(fields) in (2, 3):
                    data["native_guest_save"] = {
                        "state": fields[1], "pawn": fields[2] if len(fields) == 3 else None,
                    }
                elif fields[0] == "native-zone" and len(fields) == 7:
                    data["native_zone"] = {
                        "state": fields[1], "decision": fields[2], "requests": int(fields[3]),
                        "guest_denied": int(fields[4]), "too_far": int(fields[5]), "failure": fields[6],
                    }
                elif fields[0] == "native-zone-wall" and len(fields) == 7:
                    data["native_zone_wall"] = {
                        "state": fields[1], "host_open": fields[2] == "1", "slot": fields[3],
                        "outcome": fields[4], "remaining_uu": _finite(fields[5]), "failure": fields[6],
                    }
                elif fields[0] == "native-door" and len(fields) == 4:
                    data["native_door"] = {
                        "decision": fields[1], "guest_denied": int(fields[2]), "requests": int(fields[3]),
                    }
                elif fields[0] == "native-transport" and len(fields) == 7:
                    data["native_transport"] = {
                        "state": fields[1], "denied": int(fields[2]), "role": fields[3],
                        "item": fields[4], "entry": fields[5], "reason": fields[6],
                    }
                elif fields[0] in ("native-gold", "native-blue") and len(fields) == 6:
                    data[fields[0].replace("-", "_")] = {
                        "operation": fields[1], "state": fields[2], "map": fields[3],
                        "pawn": fields[4], "guid": fields[5],
                    }
                elif fields[0] == "native-blue-route" and len(fields) == 5:
                    data["native_blue_route"] = {
                        "source": fields[1], "destination": fields[2], "bed": fields[3], "pawn": fields[4],
                    }
                elif fields[0] == "native-blue-arrival" and len(fields) == 10:
                    data["native_blue_arrival"] = {
                        "destination": fields[1], "bed": fields[2], "actor": fields[3], "placement": fields[4],
                        "location": [_finite(value) for value in fields[5:8]], "points": int(fields[8]),
                        "min_safe_rep": int(fields[9]),
                    }
                elif fields[0] == "native-pickup" and len(fields) == 9:
                    data["native_pickup"] = {
                        "state": fields[1], "guid": fields[2], "class_path": fields[3],
                        "ammo": int(fields[4]) if fields[4] != "-" else None, "weight": _finite(fields[5]),
                        "value": int(fields[6]) if fields[6] != "-" else None,
                        "never_drop": fields[7] == "1", "actor": fields[8] if fields[8] != "-" else None,
                    }
                elif fields[0] == "native-weapon-roundtrip" and len(fields) == 8:
                    data["native_weapon_roundtrip"] = {
                        "state": fields[1], "class_path": fields[2], "payload_type": fields[3], "guid": fields[4],
                        "ammo": int(fields[5]), "item_text_chars": int(fields[6]), "payload_text_chars": int(fields[7]),
                    }
                elif fields[0] == "native-shop-purchase" and len(fields) == 12:
                    data["native_shop_purchase"] = {
                        "state": fields[1], "guid": fields[2], "shop": fields[3], "pawn": fields[4],
                        "offer": fields[5], "price": int(fields[6]), "class_path": fields[7], "ammo": int(fields[8]),
                        "weight": _finite(fields[9]), "value": int(fields[10]), "never_drop": fields[11] == "1",
                    }
                elif fields[0] == "native-damage-canary" and len(fields) == 8:
                    data["native_damage_canary"] = {
                        "slot": fields[1], "guest_before": _finite(fields[2]), "guest_after": _finite(fields[3]),
                        "host_before": _finite(fields[4]), "host_after": _finite(fields[5]),
                        "binding_restored": fields[6] == "1", "sequence": int(fields[7]),
                    }
                elif fields[0] == "native-death-pawn" and len(fields) == 3:
                    data.setdefault("native_dead_flags", {})[fields[1]] = fields[2] == "1"
                elif fields[0] == "native-friendly-fire" and len(fields) == 2:
                    data["native_friendly_fire"] = {"blocked": int(fields[1])}
                elif fields[0] == "native-host-revive" and len(fields) == 2:
                    data["native_host_revived_health"] = _finite(fields[1])
                elif fields[0] == "native-hazard" and len(fields) == 10:
                    data["native_hazard"] = {
                        "state": fields[1], "tank": fields[2], "gas": fields[3], "guest": fields[4],
                        "guest_before": _finite(fields[5]), "guest_health": _finite(fields[6]),
                        "host_before": _finite(fields[7]), "host_health": _finite(fields[8]), "detail": fields[9],
                    }
                elif fields[0] == "native-death" and len(fields) >= 3:
                    data["native_death_hook"] = {
                        "state": fields[1], "sequence": int(fields[2]),
                        "failure": fields[3] if len(fields) >= 4 else "",
                        "backend": fields[4] if len(fields) >= 5 else "overlay",
                        "scope": fields[5] if len(fields) >= 6 and fields[5] else None,
                    }
                elif fields[0] == "native-scene" and len(fields) == 18:
                    keys = ("active_map", "saved_level", "entrance", "rest_level", "bed", "instance_seed",
                            "game_seconds", "weather_offset", "ongoing_storm", "artifact_area_count",
                            "temporary_activation_count", "storm_activation_count", "storm_seed",
                            "storm_activated", "storm_class", "status", "message")
                    numeric = {"instance_seed", "game_seconds", "weather_offset", "artifact_area_count",
                               "temporary_activation_count", "storm_activation_count", "storm_seed"}
                    flags = {"ongoing_storm", "storm_activated"}
                    data["native_scene"] = {
                        key: None if value == "-" else _finite(value) if key in numeric
                        else value == "1" if key in flags else value
                        for key, value in zip(keys, fields[1:], strict=True)
                    }
                elif fields[0] == "native-rule" and len(fields) == 3:
                    data.setdefault("native_rules", {})[fields[1]] = int(fields[2])
                elif fields[0] == "native-reputation" and len(fields) == 3:
                    data.setdefault("native_reputation", {})[fields[1]] = int(fields[2])
                elif fields[0] == "native-quest" and len(fields) == 7:
                    data["native_quest"] = {
                        "id": fields[1], "state": fields[2], "step": fields[3], "final_step": fields[4],
                        "reward_cash": fields[5], "message": fields[6],
                    }
                elif fields[0] == "native-shop" and len(fields) == 9:
                    data.setdefault("native_shops", []).append({
                        "id": fields[1], "actor": fields[2], "location": [_finite(v) for v in fields[3:6]],
                        "cash": _finite(fields[6]), "state": fields[7], "message": fields[8],
                    })
                elif fields[0] == "native-offer" and len(fields) == 8:
                    data.setdefault("native_offers", []).append({
                        "shop": fields[1], "bucket": fields[2], "index": int(fields[3]), "asset": fields[4],
                        "id": None if fields[5] == "-" else fields[5],
                        "ammo": _finite(fields[6]), "value": _finite(fields[7]),
                    })
                elif fields[0] == "wallet" and len(fields) == 2:
                    data["native_wallet"] = None if fields[1] == "-" else int(fields[1])
                elif fields[0] == "vitals" and len(fields) == 6:
                    data["native_vitals_probe"] = {
                        "mode": fields[1], "host_health": _finite(fields[2]), "guest_health": _finite(fields[3]),
                        "host_uses_game_slot": fields[4] == "1", "shared_save": fields[5] == "1",
                    }
                elif fields[0] == "loot" and len(fields) == 6:
                    data["loot_actors"].append({
                        "id": None if fields[1] == "-" else fields[1],
                        "location": [_finite(axis) for axis in fields[2:5]], "name": fields[5],
                    })
                elif fields[0] == "pawn" and len(fields) in (8, 9):
                    data["pawns"][fields[1]] = {"name": fields[2], "health": _finite(fields[3]),
                                               "location": [_finite(axis) for axis in fields[4:7]],
                                               "controller": fields[7],
                                               "max_health": _finite(fields[8]) if len(fields) == 9 else None}
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

    async def command(self, action: str, *arguments: str, timeout: float = 8) -> dict:
        fields = (uuid.uuid4().hex, action, *arguments)
        if any(len(value) > 1024 or "\t" in value or "\r" in value or "\n" in value for value in fields):
            raise ValueError("Invalid game bridge command value")
        write_text(self.directory / "command.tsv", "\t".join(fields) + "\n")
        status = await self.wait_for(lambda state: state.get("ack", {}).get("id") == fields[0], timeout)
        ack = status["ack"]
        if ack["result"] != "accepted":
            raise RuntimeError(ack["message"] or f"The game declined the {action} command")
        return status

    async def walk(self, x: float, y: float, milliseconds: int) -> None:
        if type(x) not in {int, float} or type(y) not in {int, float}:
            raise ValueError("Movement requires numeric x and y")
        if (not math.isfinite(x) or not math.isfinite(y) or math.hypot(x, y) > 1.001
                or math.hypot(x, y) < 0.01 or type(milliseconds) is not int
                or not 50 <= milliseconds <= 1000):
            raise ValueError("Use a normalized planar direction and a duration of 50 to 1000 ms")
        await self.command("walk", str(x), str(y), str(milliseconds))

    async def restore(self, role: str, pawn: dict) -> dict | None:
        coordinates = pawn.get("location")
        if not isinstance(coordinates, list) or len(coordinates) != 3:
            return
        if any(not isinstance(axis, (int, float)) or not math.isfinite(axis) for axis in coordinates):
            return
        health = pawn.get("health")
        raw_health = str(health) if isinstance(health, (int, float)) and math.isfinite(health) else "-"
        return await self.command("restore", role, raw_health, *(str(axis) for axis in coordinates))
