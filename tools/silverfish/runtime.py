"""Stage a private game copy and exchange bounded local messages with UE4SS."""

from __future__ import annotations

import asyncio
import contextlib
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
from .native_finite_shop import parse_item as parse_finite_shop_item, parse_summary as parse_finite_shop_summary
from . import native_authored_pickup, native_borough_barter, native_dead_drop, native_dialogue, native_downed_markers, native_enemy_shells, native_flood_jammer, native_mosby_station_view, native_mosby_tape
from . import native_dust_cabinet, native_savlo_locker
from . import native_inventory
from . import native_guest_stack_drop
from . import native_black_box
from . import native_subway_valve
from . import native_kill_volume
from .native_guest_consumable import MEALS, RESULT as CONSUMABLE_RESULT, parse_meal
from .native_computer_board import PREFIXES as COLLECTION_PREFIXES
from .native_guest_npc import parse_bandit
from .enemy_loot_profiles import BY_ACTOR as ENEMY_LOOT_ACTORS
from .native_host_save import SLOT
from . import native_smg_hold
from .native_guest_weapon import PRIMARY_KINDS
from .native_guest_armor import parse as parse_guest_armor
from .native_guest_melee import parse as parse_guest_melee
from . import native_host_dig
from .native_host_landmine import parse as parse_host_landmine
from .authored_routes import AUTHORED_ROUTES, AuthoredRoute
from .authored_loot import CRATES as AUTHORED_CRATES, LEGACY_CRATE, loot_capacity
from .native_matrix import MatrixRequest, parse_report as parse_matrix_report, parse_request as parse_matrix_request
from .native_quest_catalog import parse as parse_quest_catalog
from .native_roster import Assignment

REPO = Path(__file__).resolve().parents[2]
AUTHORED_PICKUP_TARGETS = frozenset(source.encode("ascii") for source in native_authored_pickup.SOURCES)
SHIPPING = Path("SilverFish/Binaries/Win64/SilverFish-Win64-Shipping.exe")
PAKS = Path("SilverFish/Content/Paks")
JAMMER_ROWS = {
    spec.command(verb): (spec, verb)
    for spec in native_flood_jammer.CONTRACTS.values()
    for verb in ("", "reputation", "objective", "aim", "projection")
}
WORLD_WEATHER_FIELDS = ("fog_density", "fog_falloff", "cloud_bottom_km", "cloud_height_km", "cloud_density_offset")
MANIFEST = Path("Manifest_NonUFSFiles_Win64.txt")
STOCK_PACKAGES = ("SilverFish-Windows.pak", "SilverFish-Windows.utoc",
                  "SilverFish-Windows.ucas", "global.utoc", "global.ucas")
MAP = "/Game/Classes/Maps/PlaytestMap_01"
MENU = "/Game/Classes/Maps/MainMenu_P"
CPP_MOD_NAME = "SilverfishCoopNative"
CPP_RUNTIME_DLL = Path("ue4ss/UE4SS.dll")
CPP_ADDON_DLL = Path(f"ue4ss/Mods/{CPP_MOD_NAME}/dlls/main.dll")
MOD_SCRIPTS = Path("ue4ss/Mods/SilverfishCoop/Scripts")
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
    for attempt in range(5):
        try:
            with path.open("rb") as source:
                data = source.read(limit + 1)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.01)
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


def staged_script(work: Path, name: str, marker: str | None = None) -> bool:
    path = (work / "stage" / SHIPPING).parent / MOD_SCRIPTS / name
    return path.is_file() and (marker is None or marker in path.read_text(encoding="utf-8"))


def staged_guest_controls(work: Path) -> bool:
    return native_death_backend(work) == "cpp" and all(staged_script(work, name, marker) for name, marker in (
        ("native_inventory_view.lua", "SilverfishSetNativeInventoryViewScope"),
        ("native_guest_primary_input.lua", "SilverfishSetNativeGuestPrimaryScope"),
        ("native_guest_reload_input.lua", "SilverfishReadNativeGuestReloadIntent"),
        ("native_guest_weapon.lua", "native-guest-equipped-weapon"),
    ))


def _code_fingerprint(directory: Path, extension: str) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in directory.rglob(f"*{extension}") if item.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
        digest.update(b"\0")
        contents = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(hashlib.sha256(contents).digest())
    return digest.hexdigest()


def staged_build(work: Path) -> str:
    stage = work / "stage"
    metadata = read_json(stage / "silverfish-coop-stage.json")
    if metadata.get("schema") != 1 or not (stage / SHIPPING).is_file():
        raise ValueError("Stage is missing or incompatible")
    game_build = metadata.get("game_build")
    if not isinstance(game_build, str) or len(game_build) != 64:
        raise ValueError("Stage game fingerprint is invalid")
    scripts = (stage / SHIPPING).parent / MOD_SCRIPTS
    if not (scripts / "main.lua").is_file():
        raise ValueError("Stage is missing the Silverfish co-op scripts")
    parts = ["silverfish-coop-content-v1", game_build, _code_fingerprint(scripts, ".lua"),
             _code_fingerprint(REPO / "tools/silverfish", ".py")]
    backend = native_death_backend(work, metadata)
    if backend == "overlay":
        parts.append("native-death-lab:1")
    if backend == "cpp":
        native = metadata["cpp_death_lab"]
        parts.extend(("native-death-cpp", native["runtime"], native["addon"]))
    return hashlib.sha256(":".join(parts).encode("ascii")).hexdigest()


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
           native_zones: bool = False, native_zone_walls: bool = False, native_fixtures: bool = False,
           expanded_zones: bool = False, guest_reload_input: bool = False, guest_inventory_view: bool = False,
           native_shop_view: bool = False, guest_primary_input: bool = False, native_loot_input: bool = False,
           guest_regen: bool = False, guest_sickness: bool = False, host_camera_input: bool = False, guest_mre: bool = False,
           guest_clock: bool = False,
           broker_input: bool = False, native_wipe_view: bool = False, guest_held_fire: bool = False, native_mosby_station: bool = False,
           native_dead_bridge_enemy_loot: bool = False, guest_melee_canary: bool = False):
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
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_REGEN", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_SICKNESS", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_MRE", None)
    env.pop("SILVERFISH_COOP_NATIVE_ZONE", None)
    env.pop("SILVERFISH_COOP_NATIVE_ZONE_WALLS", None)
    env.pop("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES", None)
    env.pop("SILVERFISH_COOP_NATIVE_TRANSPORT", None)
    env.pop("SILVERFISH_COOP_NATIVE_FIXTURES", None)
    env.pop("SILVERFISH_COOP_HEADLESS", None)
    env.pop("SILVERFISH_COOP_NATIVE_LOOT", None)
    env.pop("SILVERFISH_COOP_NATIVE_CQC_LOOT", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_RELOAD_INPUT", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_RELOAD_FIXTURES", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_INPUT", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_HELD_FIRE", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_CLOCK", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_CLOCK_FIXTURES", None)
    env.pop("SILVERFISH_COOP_NATIVE_HOST_CAMERA_PRIMARY_INPUT", None)
    env.pop("SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_FIXTURES", None)
    env.pop("SILVERFISH_COOP_NATIVE_INVENTORY_VIEW", None)
    env.pop("SILVERFISH_COOP_NATIVE_INTERACT_INPUT", None)
    env.pop("SILVERFISH_COOP_NATIVE_SHOP_VIEW", None)
    env.pop("SILVERFISH_COOP_NATIVE_BROKER_INPUT", None)
    env.pop("SILVERFISH_COOP_NATIVE_WIPE_VIEW", None)
    env.pop("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES", None)
    backend = native_death_backend(work)
    if native_events and (backend != "cpp" or port is None):
        raise ValueError("Native event scope requires a staged C++ listen host")
    if native_guest and not native_events:
        raise ValueError("Native guest ownership requires native event scope")
    if guest_regen and (not native_guest or not native_zones):
        raise ValueError("Stock guest regeneration requires native campaign guest ownership")
    if guest_sickness and (not native_guest or not native_zones):
        raise ValueError("Stock guest sickness requires native campaign guest ownership")
    if guest_mre and (not native_zones or port is not None and not native_guest):
        raise ValueError("Stock guest MRE use requires native campaign guest ownership")
    if native_zones and backend != "cpp":
        raise ValueError("Natural exit handling requires a staged C++ game")
    if native_zone_walls and not native_zones:
        raise ValueError("Physical exit blockers require native exit handling")
    if guest_reload_input and (port is not None or not native_zones):
        raise ValueError("Stock guest reload input requires a staged native client")
    if guest_primary_input and (port is not None or not native_zones):
        raise ValueError("Stock guest primary input requires a staged native client")
    if guest_held_fire and not (native_zones and (native_guest if port is not None else guest_primary_input)):
        raise ValueError("Stock held fire requires the corresponding native host and guest controls")
    if guest_clock and (port is not None or not native_zones):
        raise ValueError("The stock guest clock requires a staged native client")
    if host_camera_input and (port is None or not native_guest or not native_zones):
        raise ValueError("Stock host camera input requires a staged native campaign host")
    if guest_inventory_view and (port is not None or not native_zones):
        raise ValueError("The guest inventory view requires a staged native client")
    if native_shop_view and (not native_zones or port is not None and not native_guest):
        raise ValueError("The merchant view requires scoped native guest ownership")
    if native_loot_input and (not native_zones or port is not None and not native_guest):
        raise ValueError("Natural stock crate interaction requires scoped native guest ownership")
    if native_dead_bridge_enemy_loot and (port is None or not native_guest or not native_zones or not native_loot_input):
        raise ValueError("Stock Dead Bridge enemy loot requires the native campaign host and escrow bridge")
    if broker_input and (not native_zones or port is not None):
        raise ValueError("Broker interact input requires a staged native guest")
    if native_wipe_view and (not native_zones or port is not None and not native_guest):
        raise ValueError("The stock item retention overlay requires staged native guest ownership")
    if backend:
        env["SILVERFISH_COOP_NATIVE_DEATH"] = "1" if backend == "overlay" else backend
    if native_events:
        env["SILVERFISH_COOP_NATIVE_EVENTS"] = "1"
    if native_guest:
        env["SILVERFISH_COOP_NATIVE_GUEST_SAVE"] = "1"
    if guest_regen:
        env["SILVERFISH_COOP_NATIVE_GUEST_REGEN"] = "1"
    if guest_sickness:
        env["SILVERFISH_COOP_NATIVE_GUEST_SICKNESS"] = "1"
    if guest_mre:
        env["SILVERFISH_COOP_NATIVE_GUEST_MRE"] = "1"
    if native_zones:
        env["SILVERFISH_COOP_NATIVE_ZONE"] = "1"
        env["SILVERFISH_COOP_NATIVE_TRANSPORT"] = "1"
        env["SILVERFISH_COOP_NATIVE_INTERACT_INPUT"] = "1"
        if port is not None and native_fixtures:
            env["SILVERFISH_COOP_NATIVE_FIXTURES"] = "1"
            env["SILVERFISH_COOP_NATIVE_LOOT"] = "1"
    if native_loot_input:
        env["SILVERFISH_COOP_NATIVE_LOOT"] = "1"
        if headless and port is None:
            env["SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES"] = "1"
    if native_dead_bridge_enemy_loot:
        env["SILVERFISH_COOP_NATIVE_CQC_LOOT"] = "1"
    if native_zone_walls:
        env["SILVERFISH_COOP_NATIVE_ZONE_WALLS"] = "1"
    if expanded_zones:
        env["SILVERFISH_COOP_NATIVE_EXPANDED_ZONES"] = "1"
    if guest_reload_input:
        env["SILVERFISH_COOP_NATIVE_GUEST_RELOAD_INPUT"] = "1"
        if headless:
            env["SILVERFISH_COOP_NATIVE_GUEST_RELOAD_FIXTURES"] = "1"
    if guest_primary_input:
        env["SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_INPUT"] = "1"
        if headless:
            env["SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_FIXTURES"] = "1"
    if guest_held_fire:
        env["SILVERFISH_COOP_NATIVE_GUEST_HELD_FIRE"] = "1"
    if guest_melee_canary:
        if not (headless and native_guest and native_zones and native_fixtures and port is not None):
            raise ValueError("The Old Pipe animation canary requires a staged headless native host")
        env["SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY"] = "1"
    if guest_clock:
        env["SILVERFISH_COOP_NATIVE_GUEST_CLOCK"] = "1"
        if headless:
            env["SILVERFISH_COOP_NATIVE_GUEST_CLOCK_FIXTURES"] = "1"
    if host_camera_input:
        env["SILVERFISH_COOP_NATIVE_HOST_CAMERA_PRIMARY_INPUT"] = "1"
    if guest_inventory_view:
        env["SILVERFISH_COOP_NATIVE_INVENTORY_VIEW"] = "1"
    if native_shop_view:
        env["SILVERFISH_COOP_NATIVE_SHOP_VIEW"] = "1"
        if headless and port is None:
            env["SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES"] = "1"
    if broker_input:
        env["SILVERFISH_COOP_NATIVE_BROKER_INPUT"] = "1"
        if headless:
            env["SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES"] = "1"
    if native_mosby_station and headless and port is None:
        env["SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES"] = "1"
    if native_wipe_view:
        env["SILVERFISH_COOP_NATIVE_WIPE_VIEW"] = "1"
    arguments = subprocess.list2cmdline([str(game)])
    arguments += f' -userdir="{profile}" -MULTIHOME=127.0.0.1 -unattended -NoSplash -windowed'
    if port is not None:
        arguments += f" -port={port}"
    info = None
    if headless:
        env["SILVERFISH_COOP_HEADLESS"] = "1"
        arguments += (" -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480"
                      " -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,"
                      "[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock")
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
    from .win_job import GameProcess

    return GameProcess(arguments, executable=game, cwd=game.parent, env=env, startupinfo=info)


async def stop_game(process) -> None:
    process.close_job()
    await asyncio.to_thread(process.wait, timeout=10)


def _enemy_row(data: dict, key: str, actor: str, row: dict) -> None:
    if actor in ENEMY_LOOT_ACTORS:
        data.setdefault(key + "_by_actor", {})[actor] = row
        if actor != ENEMY_LOOT_ACTORS[actor].actors[0]:
            return
    data[key] = row


def _finite(value: str) -> float | None:
    if value == "-":
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Snapshot contained a non-finite number")
    return number


def _held_input_sequence(value: str) -> int:
    if 0 < len(value) <= 16 and value.isascii() and value.isdecimal() and 0 < (number := int(value)) < 2**53:
        return number
    return 0


class Bridge:
    """A single controller for one game's local, allowlisted commands."""

    def __init__(self, directory: Path, process: subprocess.Popen):
        self.directory = directory
        self.process = process
        self.input_namespace = uuid.uuid4()
        self.input_offset = 0
        self.death_offset = 0
        self.zone_offset = 0
        self.transport_offsets = dict.fromkeys(("gold", "blue", "stardust", "fruiting-stardust", "red", "green", "purple"), 0)
        self.matrix_offset = 0
        self.authored_zone_offset = 0
        self.rest_offset = 0
        self.inventory_view_offset = 0
        self.inventory_equip_offset = 0
        self.primary_input_offset = 0
        self.held_fire_offset = self.held_fire_sequence = 0
        self.host_camera_input_offset = 0
        self.shop_view_offset = 0
        self.broker_input_offset = 0
        self.wire_board_offset = 0
        self.dialogue_offset = 0
        self.mosby_station_offset = 0

    def interactions(self) -> list[tuple[str, str | None]]:
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
            fields = line.split(b"\t")
            if len(fields) not in (2, 3) or fields[0] != b"interact" or not fields[1].isdigit() or len(fields[1]) > 12:
                continue
            target = fields[2] if len(fields) == 3 else None
            if target is not None and not target.isascii():
                continue
            decoded = target.decode("ascii") if target is not None else None
            if (target is not None and decoded != native_subway_valve.INPUT
                    and target not in AUTHORED_PICKUP_TARGETS and not native_enemy_shells.target_parts(decoded)
                    and (len(target) != 32 or any(digit not in b"0123456789abcdef" for digit in target))):
                continue
            results.append((uuid.uuid5(self.input_namespace, fields[1].decode("ascii")).hex,
                            decoded))
        return results

    def native_inventory_view_requests(self) -> list[tuple[int, bool, str, str]]:
        try:
            with (self.directory / "native-inventory-view-events").open("rb") as stream:
                stream.seek(self.inventory_view_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.inventory_view_offset += end if end else len(batch) if len(batch) == 4096 else 0
        events = []
        for line in batch[:end].splitlines():
            fields = line.decode("utf-8", errors="replace").split("\t")
            if (len(fields) == 5 and fields[0] == "inventory-view" and fields[2] in ("open", "closed")
                    and fields[1].isascii() and fields[1].isdigit() and len(fields[1]) <= 16
                    and 0 < (sequence := int(fields[1])) < 2**53 and len(fields[3]) <= 256 and len(fields[4]) <= 512):
                events.append((sequence, fields[2] == "open", fields[3], fields[4]))
        return events

    def native_primary_input_requests(self) -> list[tuple[int, str, str, str, str]]:
        try:
            with (self.directory / "native-guest-primary-events").open("rb") as stream:
                stream.seek(self.primary_input_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.primary_input_offset += end if end else len(batch) if len(batch) == 4096 else 0
        events = []
        for line in batch[:end].splitlines():
            fields = line.decode("utf-8", errors="replace").split("\t")
            if (len(fields) != 6 or fields[0] != "native-guest-primary-intent"
                    or not fields[1].isascii() or not fields[1].isdigit() or len(fields[1]) > 16
                    or not 0 < (sequence := int(fields[1])) < 2**53
                    or not valid_guid(fields[4]) or fields[5] not in PRIMARY_KINDS
                    or len(fields[2]) > 256 or len(fields[3]) > 512):
                continue
            events.append((sequence, *fields[2:]))
        return events

    def guest_held_fire_requests(self) -> list[tuple[int, str, str, str, str, int]]:
        """Read ordered exact SMG edges without consuming an incomplete row."""
        try:
            with (self.directory / "native-guest-held-fire-events").open("rb") as stream:
                stream.seek(self.held_fire_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.held_fire_offset += end or (len(batch) if len(batch) == 4096 else 0)
        events = []
        for raw in batch[:end].splitlines():
            fields = raw.decode("utf-8", errors="replace").split("\t")
            if len(fields) != 7 or fields[0] != "native-guest-held-fire-intent":
                continue
            number, area, pawn, guid, edge, stroke_text = fields[1:]
            sequence, stroke = _held_input_sequence(number), _held_input_sequence(stroke_text)
            if (sequence <= self.held_fire_sequence or not stroke or not valid_guid(guid)
                    or not area.startswith("/Game/Classes/Maps/") or len(area) > 256
                    or not pawn.startswith("Player_FP_C ") or len(pawn) > 512
                    or not (edge == "press" and stroke == sequence or edge in ("release", "cancel") and stroke < sequence)):
                continue
            self.held_fire_sequence = sequence
            events.append((sequence, area, pawn, guid, edge, stroke))
        return events

    def native_host_camera_requests(self) -> list[tuple[int, str, str, str]]:
        try:
            with (self.directory / "native-host-camera-events").open("rb") as stream:
                stream.seek(self.host_camera_input_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.host_camera_input_offset += end if end else len(batch) if len(batch) == 4096 else 0
        events = []
        for line in batch[:end].splitlines():
            fields = line.decode("utf-8", errors="replace").split("\t")
            if (len(fields) == 5 and fields[0] == "native-host-camera-intent"
                    and fields[1].isascii() and fields[1].isdigit() and len(fields[1]) <= 16
                    and 0 < (sequence := int(fields[1])) < 2**53
                    and 0 < len(fields[2]) <= 256 and 0 < len(fields[3]) <= 512
                    and valid_guid(fields[4])):
                events.append((sequence, fields[2], fields[3], fields[4]))
        return events

    def native_inventory_equip_requests(self) -> list[tuple[int, str, str, str, str]]:
        try:
            with (self.directory / "native-guest-inventory-equip-events").open("rb") as stream:
                stream.seek(self.inventory_equip_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.inventory_equip_offset += end if end else len(batch) if len(batch) == 4096 else 0
        events = []
        for line in batch[:end].splitlines():
            fields = line.decode("utf-8", errors="replace").split("\t")
            if (len(fields) != 6 or fields[0] != "native-guest-inventory-equip"
                    or not fields[1].isascii() or not fields[1].isdigit() or len(fields[1]) > 16
                    or not 0 < (sequence := int(fields[1])) < 2**53
                    or len(fields[2]) > 256 or any(len(value) > 512 for value in fields[3:])):
                continue
            events.append((sequence, *fields[2:]))
        return events

    def native_shop_view_requests(self) -> list[tuple[int, int, str, str, str, str, str, str]]:
        try:
            with (self.directory / "native-shop-view-events").open("rb") as stream:
                stream.seek(self.shop_view_offset)
                batch = stream.read(8192)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.shop_view_offset += end if end else len(batch) if len(batch) == 8192 else 0
        events = []
        for line in batch[:end].splitlines():
            fields = line.decode("utf-8", errors="replace").split("\t")
            if (len(fields) == 9 and fields[0] == "shop-view" and fields[3] in ("open", "close", "buy", "page")
                    and all(value.isascii() and value.isdigit() and len(value) <= 16 and 0 < int(value) < 2**53
                            for value in fields[1:3])
                    and all(len(value) <= 512 for value in fields[4:])):
                events.append((int(fields[1]), int(fields[2]), *fields[3:]))
        return events

    def native_broker_requests(self) -> list[tuple[int, str, str, str]]:
        try:
            with (self.directory / "native-broker-input-events").open("rb") as stream:
                stream.seek(self.broker_input_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.broker_input_offset += end if end else len(batch) if len(batch) == 4096 else 0
        events = []
        for line in batch[:end].splitlines():
            fields = line.decode("utf-8", errors="replace").split("\t")
            if (len(fields) == 5 and fields[0] == "native-broker-input" and fields[1].isascii() and fields[1].isdigit()
                    and len(fields[1]) <= 16 and 0 < (sequence := int(fields[1])) < 2**53
                    and len(fields[2]) <= 256 and all(len(value) <= 512 for value in fields[3:])):
                events.append((sequence, *fields[2:]))
        return events

    def native_wire_board_requests(self) -> list[tuple[int, int, str, str, str, str, str]]:
        try:
            with (self.directory / "native-wire-board-events").open("rb") as stream:
                stream.seek(self.wire_board_offset)
                batch = stream.read(8192)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.wire_board_offset += end if end else len(batch) if len(batch) == 8192 else 0
        events = []
        for line in batch[:end].splitlines():
            fields = line.decode("utf-8", errors="replace").split("\t")
            if (len(fields) == 8 and fields[0] == "wire-board" and fields[3] in ("view", "start", "turnin")
                    and all(value.isascii() and value.isdigit() and len(value) <= 16 and 0 < int(value) < 2**53
                            for value in fields[1:3])
                    and all(0 < len(value) <= 512 for value in fields[4:])):
                events.append((int(fields[1]), int(fields[2]), *fields[3:]))
        return events

    def native_dialogue_requests(self) -> list[tuple[int, str, str, str]]:
        try:
            with (self.directory / "native-dialogue-events").open("rb") as stream:
                stream.seek(self.dialogue_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.dialogue_offset += end if end else len(batch) if len(batch) == 4096 else 0
        events = []
        for line in batch[:end].splitlines():
            fields = line.decode("utf-8", errors="replace").split("\t")
            if len(fields) != 5 or fields[0] != "dialogue-input":
                continue
            _, serial, pawn, token, choice = fields
            if (serial.isascii() and serial.isdigit() and len(serial) <= 16 and 0 < (sequence := int(serial)) < 2**53
                    and pawn.startswith("Player_FP_C ") and len(pawn) <= 512
                    and ((token, choice) == ("-", "-") or choice in native_dialogue.ORDINALS and native_dialogue.valid_token(token))):
                events.append((sequence, pawn, token, choice))
        return events

    def native_mosby_station_requests(self) -> list[tuple[int, int, str, str, str | None, str]]:
        try:
            with (self.directory / "native-mosby-station-events").open("rb") as stream:
                stream.seek(self.mosby_station_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.mosby_station_offset += end or (len(batch) if len(batch) == 4096 else 0)
        return [event for raw in batch[:end].splitlines()
                if (event := native_mosby_station_view.parse_intent(raw.decode("utf-8", errors="replace").split("\t")))]

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
            if (len(fields) in (3, 4) and fields[0] == b"downed"
                    and (fields[1] in (b"local", b"guest") or (len(fields) == 4 and fields[1] in (b"guest2", b"guest3", b"guest4")))
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

    def native_rest_requests(self) -> list[tuple[str, str, str, str, int, bool]]:
        try:
            with (self.directory / "native-rest-events").open("rb") as stream:
                stream.seek(self.rest_offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.rest_offset += end if end else len(batch) if len(batch) == 4096 else 0
        requests = []
        for line in batch[:end].splitlines():
            fields = line.split(b"\t")
            if (len(fields) not in (6, 7) or fields[0] != b"rest" or not fields[1].isdigit() or len(fields[1]) > 12
                    or not fields[5].isdigit() or len(fields[5]) > 2 or not 0 <= int(fields[5]) <= 10
                    or len(fields) == 7 and fields[6] not in (b"insomnia", b"screen")
                    or not all(0 < len(field) <= 512 for field in fields[2:5])):
                continue
            insomnia = len(fields) == 7 and fields[6] == b"insomnia"
            if insomnia and int(fields[5]) == 0:
                continue
            operation_id = uuid.uuid5(self.input_namespace, "rest" + fields[1].decode("ascii")).hex
            source, pawn, bed = (field.decode("utf-8", errors="replace") for field in fields[2:5])
            requests.append((operation_id, source, pawn, bed, int(fields[5]), insomnia))
        return requests

    def native_cube_requests(self, cube: str) -> list[GoldRequest]:
        offset = self.transport_offsets[cube]
        filename = "native-transport-events" if cube == "gold" else f"native-{cube}-events"
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

    def native_matrix_requests(self) -> list[MatrixRequest]:
        try:
            with (self.directory / "native-matrix-events").open("rb") as stream:
                stream.seek(self.matrix_offset)
                batch = stream.read(8192)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.matrix_offset += end or (len(batch) if len(batch) == 8192 else 0)
        return [request for line in batch[:end].splitlines()
                if (request := parse_matrix_request(line.decode("utf-8", errors="replace").split("\t")))]

    def authored_zone_requests(self) -> list[tuple[int, AuthoredRoute]]:
        try:
            with (self.directory / "native-authored-exit-events").open("rb") as stream:
                stream.seek(self.authored_zone_offset)
                batch = stream.read(8192)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.authored_zone_offset += end or (len(batch) if len(batch) == 8192 else 0)
        requests = []
        for raw in batch[:end].splitlines():
            fields = raw.decode("utf-8", errors="replace").split("\t")
            if len(fields) != 8 or fields[0] != "authored-request" or not fields[1].isascii() \
                    or not fields[1].isdigit() or len(fields[1]) > 12:
                continue
            source, destination, entrance, actor, kind, step = fields[2:]
            match = next((route for route in AUTHORED_ROUTES if route.source == source and route.actor == actor), None)
            if match and (match.destination, match.entrance, match.kind,
                          "-" if match.travel_time_step is None else str(match.travel_time_step)) == (
                          destination, entrance, kind, step):
                requests.append((int(fields[1]), match))
        return requests

    def snapshot(self) -> dict | None:
        try:
            text = (self.directory / "status.tsv").read_text(encoding="utf-8")
            data = {"pawns": {}, "player_effects": {}, "loot_actors": []}
            quest_rows, board_rows = [], []
            dialogue_frame, dialogue_progress, dialogue_lines, dialogue_choices = None, None, [], []
            mosby_frame, mosby_reputation = None, []
            matrix_rows, marker_rows = [], []
            marker_frame = None
            malformed_markers = False
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
                elif fields == ["native-zone-party-guard", "1"]:
                    data["native_zone_party_guard"] = True
                elif fields[0] in ("native-guest-melee-equip", "native-guest-melee-canary", "native-guest-melee-swing",
                                    "native-guest-melee-aim-fixture", "native-guest-melee-contact-fixture"):
                    parsed = parse_guest_melee(fields)
                    if parsed:
                        key, row = parsed
                        if (fields[0] != "native-guest-melee-contact-fixture"
                                or (data.get("state"), data.get("map")) == ("hosting", row["map"])):
                            data[key] = row
                elif fields[0] == "native-guest-smg-hold" and len(fields) == 10:
                    with contextlib.suppress(ValueError):
                        row = native_smg_hold.parse(fields)
                        data[native_smg_hold.KEY] = row
                        data.setdefault(native_smg_hold.ALL, {})[row["pawn"]] = row
                elif fields[0] == "player" and len(fields) >= 6:
                    data["player_effects"][fields[1]] = {
                        "state": fields[2], "crouched": fields[3] == "1",
                        "speed": _finite(fields[4]), "jump_velocity": _finite(fields[5]),
                    }
                elif fields[0] == "player-visual" and len(fields) == 4:
                    data.setdefault("player_visuals", {})[fields[1]] = {
                        "input_gate": fields[2] == "1", "marker": fields[3] == "1",
                    }
                elif fields[0] == "player-marker-frame":
                    if len(fields) == 4 and marker_frame is None:
                        marker_frame = fields
                    else:
                        malformed_markers = True
                elif fields[0] == "player-marker":
                    if len(fields) == 7 and len(marker_rows) < native_downed_markers.MAX_MARKERS:
                        marker_rows.append(fields)
                    else:
                        malformed_markers = True
                elif (fields[0] == "player-local-input" and len(fields) == 15
                      and fields[2] in ("standing", "downed") and fields[3] in ("verified", "waiting")
                      and fields[14] in ("not_applicable", "clear", "open", "closed", "waiting")
                      and all(value in ("0", "1", "-") for value in fields[4:14])):
                    flags = ("stock_context", "reduced_context", "look", "move", "jump", "inventory",
                             "primary", "secondary", "reload", "interact")
                    data["player_local_input"] = {"pawn": fields[1], "state": fields[2], "verification": fields[3],
                                                  "host_inventory": fields[14],
                                                  **{key: {"0": False, "1": True, "-": None}[value]
                                                     for key, value in zip(flags, fields[4:14], strict=True)}}
                elif fields[0] == "native-inventory-canary" and len(fields) == 4:
                    data["native_inventory_canary"] = {
                        "state": fields[1], "binding_restored": fields[2] == "1", "detail": fields[3],
                    }
                elif fields[0] == "native-guest-save" and len(fields) in (2, 3):
                    data["native_guest_save"] = {
                        "state": fields[1], "pawn": fields[2] if len(fields) == 3 else None,
                    }
                elif fields[0] == "native-guest-save-member" and len(fields) == 3:
                    data.setdefault("native_guest_save_members", {})[fields[2]] = {"state": fields[1], "pawn": fields[2]}
                elif fields == ["native-guest-xp-capability", "1"]:
                    data["native_guest_xp_ready"] = True
                elif fields[0] == "native-guest-xp" and len(fields) == 5:
                    data["native_guest_xp"] = {"map": fields[1], "pawn": fields[2], "before": int(fields[3]), "after": int(fields[4])}
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
                elif fields[0] == "native-zone-regroup" and len(fields) == 8:
                    area, exit_actor, display, pawn, role, name, meters = fields[1:]
                    distance = _finite(meters)
                    if distance is None or distance <= 15 or display not in ("visible", "hidden", "headless"):
                        continue
                    regroup = data.setdefault("native_zone_regroup", {
                        "map": area, "exit_actor": exit_actor, "display": display, "blockers": [],
                    })
                    if (area, exit_actor, display) == (regroup["map"], regroup["exit_actor"], regroup["display"]):
                        regroup["blockers"].append({"pawn": pawn, "role": role, "name": name, "distance_m": distance})
                elif fields[0] == "native-door" and len(fields) in (4, 5):
                    data["native_door"] = {
                        "decision": fields[1], "guest_denied": int(fields[2]), "requests": int(fields[3]),
                    }
                    if len(fields) == 5 and fields[4]:
                        data["native_door"]["detail"] = fields[4]
                elif fields[0] == "native-transport" and len(fields) == 7:
                    data["native_transport"] = {
                        "state": fields[1], "denied": int(fields[2]), "role": fields[3],
                        "item": fields[4], "entry": fields[5], "reason": fields[6],
                    }
                elif fields[0] in ("native-gold", "native-blue", "native-red", "native-green", "native-purple") and len(fields) == 6:
                    data[fields[0].replace("-", "_")] = {
                        "operation": fields[1], "state": fields[2], "map": fields[3],
                        "pawn": fields[4], "guid": fields[5],
                    }
                elif fields[0] == "native-blue-route" and len(fields) == 5:
                    data["native_blue_route"] = {
                        "source": fields[1], "destination": fields[2], "bed": fields[3], "pawn": fields[4],
                    }
                elif fields[0] == "native-green-options" and len(fields) == 5:
                    data["native_green_options"] = {
                        "source": fields[1], "pawn": fields[2], "destinations": fields[3].split("|"),
                        "reputation": {int(group): int(rank) for group, rank in
                                       (entry.split(":") for entry in fields[4].split("|"))},
                    }
                elif fields[0] in ("native-green-arrival", "native-purple-arrival", "native-authored-arrival") and len(fields) == 7:
                    data[fields[0].replace("-", "_")] = {
                        "destination": fields[1], "actor": fields[2], "placement": fields[3],
                        "location": [_finite(value) for value in fields[4:7]],
                    }
                elif fields[0] == "native-blue-arrival" and len(fields) == 10:
                    data["native_blue_arrival"] = {
                        "destination": fields[1], "bed": fields[2], "actor": fields[3], "placement": fields[4],
                        "location": [_finite(value) for value in fields[5:8]], "points": int(fields[8]),
                        "min_safe_rep": int(fields[9]),
                    }
                elif fields[0] in ("native-stardust", "native-fruiting-stardust") and len(fields) == 8:
                    data[fields[0].replace("-", "_")] = {
                        "operation": fields[1], "state": fields[2], "map": fields[3], "pawn": fields[4],
                        "guid": fields[5], "quantity": int(fields[6]), "payload": fields[7],
                    }
                elif fields[0] == "native-dream-route" and len(fields) == 12:
                    data["native_dream_route"] = {
                        "source": fields[1], "destination": fields[2], "pawn": fields[3], "arrival": fields[4],
                        "visit_seconds": int(fields[5]), "startup_seconds": int(fields[6]), "fade_seconds": int(fields[7]),
                        "return_map": fields[8], "return_bed": fields[9], "kind": fields[10], "tarot": fields[11],
                    }
                elif fields[0] == "native-dream-effects" and len(fields) in (8, 9):
                    data["native_dream_effects"] = {
                        "source": fields[1], "pawn": fields[2], "kind": fields[3], "destination": fields[4],
                        "tarot": fields[5], "before": int(fields[6]), "after": int(fields[7]),
                    }
                    if len(fields) == 9 and fields[8]:
                        data["native_dream_effects"]["stock_event"] = fields[8]
                elif fields[0] in ("native-guest-armor", "native-guest-armor-projection") and len(fields) in (14, 17):
                    report = parse_guest_armor(fields)
                    if fields[0] == "native-guest-armor":
                        data["native_guest_armor"] = report
                    else:
                        data.setdefault("native_guest_armor_projection", {})[report["pawn"]] = report
                elif fields[0] in ("native-guest-helmet", "native-guest-helmet-projection"):
                    report = parse_guest_armor(fields)
                    if data.get("state") != "hosting" or data.get("map") != report["map"]:
                        raise ValueError("Invalid stock helmet report scope")
                    if fields[0] == "native-guest-helmet":
                        if "native_guest_helmet" in data or report["action"] != "equip":
                            raise ValueError("Invalid stock helmet equip receipt")
                        data["native_guest_helmet"] = report
                    else:
                        projected = data.setdefault("native_guest_helmet_projection", {})
                        if report["pawn"] in projected or report["action"] != "project":
                            raise ValueError("Invalid stock helmet projection receipt")
                        projected[report["pawn"]] = report
                elif fields[0] == "native-guest-weapon" and len(fields) in (12, 14):
                    data["native_guest_weapon"] = {
                        "map": fields[1], "action": fields[2], "pawn": fields[3], "guid": fields[4],
                        "ok": fields[5] == "1", "status": fields[6],
                        **{key: (None if value == "-" else int(value))
                           for key, value in zip(("before", "after", "actor_ammo", "stored_ammo", "projectiles",
                                                  "reserve_before", "reserve_after"), fields[7:])},
                    }
                elif fields[0] == "native-rest" and len(fields) == 19:
                    data["native_rest"] = {
                        "map": fields[1], "pawn": fields[2], "actor": fields[3], "bed": fields[4], "level": fields[5],
                        "eligible": fields[6] == "1", "reason": fields[7], "points": int(fields[8]),
                        "distance": _finite(fields[9]), "range": _finite(fields[10]), "insomnia": fields[11] == "1",
                        "location": [_finite(value) for value in fields[12:15]],
                        "hours": None if fields[15] == "-" else int(fields[15]),
                        "before": _finite(fields[16]), "after": _finite(fields[17]), "ping": fields[18],
                    }
                elif fields[0] == "native-bandit":
                    data["native_bandit"] = parse_bandit(fields)
                elif fields[0] == "native-bandit-aim" and len(fields) in (8, 10):
                    data["native_bandit_aim"] = {"map": fields[1], "bandit": fields[2], "pawn": fields[3],
                                                 "range": _finite(fields[4]), "yaw": _finite(fields[5]), "pitch": _finite(fields[6]),
                                                 "component": fields[7]}
                    if len(fields) == 10:
                        data["native_bandit_aim"]["withdrawal"] = {
                            "distance": _finite(fields[8]), "return_error": _finite(fields[9]),
                        }
                elif fields[0] == "native-broker-input" and len(fields) == 6:
                    data["native_broker_input"] = {"map": fields[1], "pawn": fields[2], "station": fields[3],
                                                   "state": fields[4], "sequence": int(fields[5])}
                elif fields[0] == "native-wire-board" and len(fields) == 8:
                    data["native_wire_board"] = dict(zip(("map", "pawn", "board", "quest", "state"), fields[1:6]),
                                                     sequence=int(fields[6]), open=int(fields[7]))
                elif fields[0] == "native-turret-photo" and len(fields) == 16:
                    photo = dict(zip(("action", "map", "pawn", "quest", "target", "camera"), fields[1:7]))
                    photo["visibility"] = _finite(fields[7])
                    photo["step_before"], photo["step_after"] = (None if part == "-" else int(part) for part in fields[8:10])
                    balances = ("cash_before", "cash_after", "xp_before", "xp_after", "rep_before", "rep_after")
                    photo.update(zip(balances, map(int, fields[10:])))
                    data["native_turret_photo"] = photo
                elif fields[0] == "native-dialogue-view" and len(fields) == 6:
                    pawn, state, serial, row, token = fields[1:]
                    sequence = int(serial)
                    profile = native_dialogue.PROFILES.get(data.get("map"))
                    has_row = profile and row in profile.rows and native_dialogue.valid_token(token)
                    if (not pawn.startswith("Player_FP_C ") or len(pawn) > 512 or not 0 <= sequence < 2**53
                            or state not in ("waiting", "armed", "pending", "open", "closed")
                            or not (has_row or (row, token) == ("-", "-")) or (state == "open" and not has_row)
                            or (has_row and state not in ("pending", "open"))):
                        raise ValueError("Invalid local stock dialogue view")
                    data["native_dialogue_view"] = dict(pawn=pawn, state=state, sequence=sequence, row=row, token=token)
                elif fields[0] == "native-dialogue-stock":
                    if dialogue_frame is not None:
                        raise ValueError("Duplicate stock dialogue frame")
                    dialogue_frame = fields
                elif fields[0] == "native-dialogue-progress":
                    if dialogue_progress is not None:
                        raise ValueError("Duplicate stock dialogue progress")
                    dialogue_progress = fields
                elif fields[0] == "native-borough-barter":
                    proof = native_borough_barter.parse(fields)
                    if ("native_borough_barter" in data or data.get("state") != "hosting" or data.get("map") != proof["map"]):
                        raise ValueError("Invalid stock Borough barter frame scope")
                    data["native_borough_barter"] = proof
                elif profile := native_mosby_tape.BY_PREFIX.get(fields[0]):
                    if profile.key in data:
                        raise ValueError("Duplicate stock tape station proof")
                    data[profile.key] = native_mosby_tape.parse_exchange(fields)
                elif fields[0] == "native-mosby-station-view":
                    data[native_mosby_station_view.KEY] = native_mosby_station_view.parse(fields)
                elif fields[0] == "native-mosby-readings":
                    if mosby_frame is not None:
                        raise ValueError("Duplicate Stone Maze quest frame")
                    mosby_frame = fields
                elif fields[0] == "native-mosby-readings-rep":
                    mosby_reputation.append(fields)
                elif fields[0] == "native-mosby-reward":
                    data["native_mosby_reward"] = native_mosby_tape.parse_reward(fields)
                elif fields[0] == "native-subway-valve-board":
                    if native_subway_valve.BOARD_KEY in data:
                        raise ValueError("Duplicate Flood Control board report")
                    data[native_subway_valve.BOARD_KEY] = native_subway_valve.parse_board(fields, data.get("state"), data.get("map"))
                elif fields[0] == "native-subway-valve-rep":
                    report = data.get(native_subway_valve.BOARD_KEY)
                    if len(fields) != 4 or not report or fields[1] not in native_subway_valve.FACTIONS or fields[1] in report["reputation"]:
                        raise ValueError("Invalid Flood Control reputation row")
                    report["reputation"][fields[1]] = {"before": int(fields[2]), "after": int(fields[3])}
                elif fields[0] == "native-subway-valve-objective":
                    data[native_subway_valve.OBJECTIVE] = native_subway_valve.parse_objective(fields, data.get("state"), data.get("map"))
                elif fields[0] == "native-subway-valve-scene":
                    data[native_subway_valve.SCENE] = native_subway_valve.parse_scene(fields, data.get("state"), data.get("map"))
                elif fields[0] == "native-subway-valve-reward":
                    data[native_subway_valve.REWARD] = native_subway_valve.parse_reward(fields, data.get("state"), data.get("map"))
                elif fields[0] == "native-subway-valve-fixture":
                    data[native_subway_valve.FIXTURE_KEY] = native_subway_valve.parse_fixture(fields, data.get("state"), data.get("map"))
                elif fields[0] == "native-dialogue-line":
                    dialogue_lines.append(fields)
                elif fields[0] == "native-dialogue-choice":
                    dialogue_choices.append(fields)
                elif fields[0] == "native-bounty-board" and len(fields) == 4:
                    data["native_bounty_board"] = dict(zip(("map", "pawn", "board"), fields[1:]))
                elif fields[0] in COLLECTION_PREFIXES and len(fields) == 15 and fields[14] in ("0", "1"):
                    proof = dict(zip(("operation", "map", "pawn", "board", "quest"), fields[1:6]))
                    for name, value in zip(("step_before", "step_after", "cash_before", "cash_after", "xp_before", "xp_after"), fields[6:12]):
                        proof[name] = None if value == "-" else int(value)
                    proof.update(objective_guid=fields[12], objective_owner=fields[13], can_turn_in=fields[14] == "1", reputation={})
                    data[COLLECTION_PREFIXES[fields[0]].key] = proof
                elif fields[0].endswith("-reputation") and len(fields) == 4 and (spec := COLLECTION_PREFIXES.get(fields[0].removesuffix("-reputation"))):
                    if spec.key in data and fields[1] in spec.factions:
                        data[spec.key]["reputation"][fields[1]] = {"before": int(fields[2]), "after": int(fields[3])}
                elif fields[0] == native_black_box.COMPLETION_TAG and native_black_box.KEY in data:
                    proof = data[native_black_box.KEY]
                    proof["completion"] = None if "completion" in proof else native_black_box.parse_completion(fields)
                elif fields[0] == "native-meet-broker" and len(fields) == 13:
                    proof = dict(zip(("operation", "map", "pawn", "board", "quest"), fields[1:6]))
                    numbers = ("step_before", "step_after", "cash_before", "cash_after", "xp_before", "xp_after")
                    proof.update((name, None if value == "-" else int(value)) for name, value in zip(numbers, fields[6:12]))
                    data["native_meet_broker"] = dict(proof, weapon_guid=fields[12])
                elif fields[0] == "native-meet-broker-reputation" and len(fields) == 3 and "native_meet_broker" in data:
                    data["native_meet_broker"].update(reputation_before=int(fields[1]), reputation_after=int(fields[2]))
                elif fields[0] == "native-lenny-objective" and len(fields) == 16 and fields[12] in ("host", "guest", "-"):
                    numbers = ("step_before", "step_after", "flag_before", "flag_after", "xp_before", "xp_after")
                    data["native_lenny_objective"] = {
                        **dict(zip(("map", "spawner", "target", "state"), fields[1:5])),
                        "replicated": fields[5] == "1",
                        **{key: None if value == "-" else int(value) for key, value in zip(numbers, fields[6:12])},
                        "shooter_role": fields[12], "shooter": fields[13], "projectile": fields[14],
                        "health": None if fields[15] == "-" else _finite(fields[15]),
                    }
                elif fields[0] == "native-lenny-client" and len(fields) == 5:
                    data["native_lenny_client"] = {"map": fields[1], "spawner": fields[2],
                                                   "local": int(fields[3]), "simulated": int(fields[4])}
                elif fields[0] == "native-lenny-aim" and len(fields) == 7 and valid_guid(fields[4]):
                    data["native_lenny_aim"] = dict(zip(("map", "pawn", "target", "guid"), fields[1:5]),
                                                   distance=_finite(fields[5]), component=fields[6])
                elif fields[0] in JAMMER_ROWS:
                    spec, kind = JAMMER_ROWS[fields[0]]
                    key = spec.key
                    if kind == "" and len(fields) == 12:
                        names = ("operation", "map", "pawn", "board", "quest")
                        numbers = ("step_before", "step_after", "cash_before", "cash_after", "xp_before", "xp_after")
                        proof = dict(zip(names, fields[1:6]))
                        proof.update((name, None if value == "-" else int(value)) for name, value in zip(numbers, fields[6:]))
                        data[key] = {**proof, "reputation": {}}
                    elif kind == "reputation" and len(fields) == 4 and fields[1] in spec.factions and key in data:
                        data[key]["reputation"][fields[1]] = {"before": int(fields[2]), "after": int(fields[3])}
                    elif kind == "objective" and len(fields) == 12 and fields[9] in ("host", "guest", "-"):
                        names = ("step_before", "step_after", "xp_before", "xp_after")
                        numeric = dict((name, None if value == "-" else int(value)) for name, value in zip(names, fields[5:9]))
                        data[f"{key}_objective"] = {
                            "map": fields[1], "jammer": fields[2], "trigger": fields[3], "state": fields[4],
                            **numeric, "shooter_role": fields[9], "shooter": fields[10], "projectile": fields[11],
                        }
                    elif kind == "aim" and len(fields) == 7 and valid_guid(fields[4]):
                        data[f"{key}_aim"] = dict(zip(("map", "pawn", "jammer", "guid"), fields[1:5]),
                                                  distance=_finite(fields[5]), component=fields[6])
                    elif kind == "projection" and len(fields) == 5 and fields[3] in ("present", "hidden", "absent"):
                        data[f"{key}_projection"] = dict(zip(("map", "actor", "state", "audio"), fields[1:]))
                elif fields[0] == "native-guest-clock" and len(fields) == 9 and fields[6] in ("armed", "projected"):
                    data["native_guest_clock"] = {
                        "map": fields[1], "pawn": fields[2], "epoch": fields[3], "sequence": int(fields[4]),
                        "milliseconds": int(fields[5]), "state": fields[6], "calls": int(fields[7]),
                        "original_client_milliseconds": None if fields[8] == "-" else int(fields[8]),
                    }
                elif fields[0] == "native-guest-clock-fixture" and len(fields) == 8:
                    data["native_guest_clock_fixture"] = {
                        "map": fields[1], "pawn": fields[2], "epoch": fields[3], "sequence": int(fields[4]),
                        "milliseconds": int(fields[5]), "calls": int(fields[6]), "text": fields[7],
                    }
                elif fields[0] == "native-network-timeout" and len(fields) == 2:
                    data["native_network_timeout"] = _finite(fields[1])
                elif fields[0] == "native-fresh-campaign" and len(fields) == 17:
                    fresh = dict(zip(("state", "preset", "difficulty", "map", "working_slot"), fields[1:6]))
                    seeds_and_counters = ("instance_seed", "world_seed", "artifact_seed", "storm_seed", "cash", "debt", "xp", "backpack_count")
                    fresh.update(zip(seeds_and_counters, map(int, fields[6:14])))
                    fresh.update(zip(("armor", "rest_level", "proof"), fields[14:]))
                    data["native_fresh_campaign"] = fresh
                elif fields[0] == "native-enemy-loot" and len(fields) == 18 and fields[13] in ("0", "1"):
                    _enemy_row(data, "native_enemy_loot", fields[2], {"map": fields[1], "bandit": fields[2], "state": fields[3], "hits": int(fields[4]),
                                                 "killer_role": fields[5], "killer_pawn": fields[6], "projectile": fields[7],
                                                 "damage": _finite(fields[8]), "hp_before": _finite(fields[9]), "hp_after": _finite(fields[10]),
                                                 "loot_calls": int(fields[11]), "bounty_calls": int(fields[12]), "bounty_active": fields[13] == "1",
                                                 "bounty_instances": int(fields[14]), "ammo_actor": fields[15], "ammo_class": fields[16],
                                                 "ammo_quantity": int(fields[17])})
                elif fields[0] == "native-enemy-bounty" and len(fields) == 11:
                    _enemy_row(data, "native_enemy_bounty", fields[2], {
                        "map": fields[1], "bandit": fields[2], "source": fields[3], "override_count": int(fields[4]),
                        "original_active": fields[5] == "1", "original_instances": int(fields[6]),
                        "permanent_count": int(fields[7]), "timed_count": int(fields[8]), "gear_count": int(fields[9]),
                        "cache_unchanged": fields[10] == "1",
                    })
                elif (len(fields) == 2 and fields[0] == "native-enemy-loot-cqc-capability" and fields[1] in ("1", "2", "3")):
                    data["native_enemy_loot_cqc_capability"] = True
                    data["native_enemy_loot_cqc_version"] = int(fields[1])
                elif fields in (["native-enemy-shell-view-capability", "1"], ["native-enemy-shell-view-capability", "2"]):
                    data["native_enemy_shell_view_capability"] = True
                    data["native_enemy_shell_view_version"] = int(fields[1])
                elif fields[0] == "native-enemy-shell-authority" and len(fields) == 5 and fields[1] == data.get("map"):
                    if authority := native_enemy_shells.parse_authority(*fields[1:]):
                        data[native_enemy_shells.AUTHORITY_KEY] = authority
                elif fields[0] == "native-enemy-shell-view" and len(fields) == 5 and fields[1] == data.get("map"):
                    if view := native_enemy_shells.parse_view(*fields[1:]):
                        data[native_enemy_shells.KEY] = view
                elif fields[0] == "native-enemy-loot-cqc" and len(fields) == 6 and all(value in ("0", "1") for value in fields[4:]):
                    _enemy_row(data, "native_enemy_loot_cqc", fields[2], {"map": fields[1], "bandit": fields[2],
                                                     "killer_character": None if fields[3] == "-" else fields[3],
                                                     "reserved": fields[4] == "1", "captured": fields[5] == "1"})
                elif fields[0] == "native-enemy-loot-cqc-target" and len(fields) == 6 and fields[2] in ENEMY_LOOT_ACTORS:
                    data.setdefault("native_enemy_loot_cqc_targets", {})[fields[2]] = dict(
                        zip(("map", "actor", "target", "character", "ai_controller"), fields[1:]))
                elif fields[0] == "native-enemy-loot-death-view" and len(fields) == 8:
                    area, actor, mode, epoch, guid, hidden, collision = fields[1:]
                    if (hidden in ("0", "1") and collision in ("0", "1")
                            and (mode == "projected" and len(epoch) == 32
                                 and all(char in "0123456789abcdef" for char in epoch) and valid_guid(guid)
                                 and (hidden, collision) == ("1", "0")
                                 or mode in ("present", "stock") and (epoch, guid) == ("-", "-"))):
                        _enemy_row(data, "native_enemy_loot_death_view", actor, {
                            "map": area, "actor": actor, "state": mode, "epoch": None if epoch == "-" else epoch,
                            "guid": None if guid == "-" else guid, "hidden": hidden == "1", "collision": collision == "1",
                        })
                elif fields[0] == "native-enemy-loot-escrow" and len(fields) == 11:
                    data["native_enemy_loot_escrow"] = dict(zip(("map", "state", "pawn", "actor", "slot", "guid"), fields[1:7]),
                                                            units=None if fields[7] == "-" else int(fields[7]),
                                                            position=[_finite(axis) for axis in fields[8:11]])
                elif (fields[0] == "native-enemy-loot-retire-proof" and len(fields) == 6 and fields[1] == "1"
                        and fields[2] == data.get("map") and SLOT.fullmatch(fields[4]) and valid_guid(fields[5])):
                    data["native_enemy_loot_retire_proof"] = dict(schema=1, map=fields[2], actor=fields[3],
                                                                 slot=fields[4], guid=fields[5])
                elif fields[0] == "native-campaign-npc" and len(fields) == 10:
                    data["native_campaign_npc"] = {
                        "map": fields[1], "viewer": fields[2], "stock_receipts": int(fields[3]), "stage": fields[4],
                        "spawner": fields[5], "squad_data": fields[6], "cohort_size": int(fields[7]),
                        "replicated": int(fields[8]), "spawn_denials": int(fields[9]), "members": [],
                    }
                elif fields[0] == "native-campaign-npc-census" and len(fields) == 10 and "native_campaign_npc" in data:
                    metrics = ("natural_calls", "authority", "simulated", "other", "simulated_controllers",
                               "ai_denials", "antfarm")
                    data["native_campaign_npc"].update(zip(metrics, map(int, fields[3:])))
                elif fields[0] == "native-campaign-npc-network" and len(fields) == 10 and "native_campaign_npc" in data:
                    npc = data["native_campaign_npc"]
                    counts = tuple(map(int, fields[3:7]))
                    if fields[1:3] != [npc["map"], npc["viewer"]] or any(count < 0 for count in counts):
                        raise ValueError("Invalid native campaign NPC network report")
                    npc["network"] = {
                        **dict(zip(("authority_network_ready", "local_authority_begins", "local_authority_initializes",
                                    "early_spawn_denials"), counts)),
                        **dict(zip(("last_actor", "last_caller", "last_script"), fields[7:])),
                    }
                elif fields[0] == "native-campaign-npc-member" and len(fields) == 4 and "native_campaign_npc" in data:
                    data["native_campaign_npc"]["members"].append(fields[3])
                elif fields[0] == "native-campaign-npc-saved-squads" and len(fields) == 8:
                    data["native_campaign_npc_saved_squads"] = {
                        "map": fields[1], "viewer": fields[2], "observed_calls": int(fields[3]), "denials": int(fields[4]),
                        "qualification": fields[5], "spawner": fields[6], "data": fields[7],
                    }
                elif fields[0] == "native-campaign-npc-approach" and len(fields) == 7:
                    data["native_campaign_npc_approach"] = {"map": fields[1], "member": fields[2], "host_pawn": fields[3],
                                                           "guest_pawn": fields[4], "host_distance": _finite(fields[5]),
                                                           "guest_distance": _finite(fields[6])}
                elif fields[0] == "native-campaign-flayer-projectile" and len(fields) == 9:
                    area, serial, shooter, victim, projectile, before, after, amount = fields[1:]
                    level = area + ".CrossRoads_P:PersistentLevel."
                    sequence = int(serial)
                    before, after, amount = map(_finite, (before, after, amount))
                    rows = data.setdefault("native_campaign_flayer_projectile_hits", [])
                    suffixes = [name.removeprefix(f"{kind} {level}{kind}_") for kind, name in (
                        ("Player_FP_C", shooter), ("NPC_Flayer_C", victim), ("Projectile_Pistol_Light_C", projectile))]
                    if (area != "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P" or data.get("map") != area
                            or data.get("state") != "hosting" or not 1 <= sequence < 2**53 or len(rows) >= 16
                            or (rows and rows[-1]["sequence"] <= sequence) or None in (before, after, amount)
                            or not 0 < after < before <= 180 or not 0 < amount <= 180
                            or abs(before - after - amount) > 1e-6
                            or any(not suffix.isascii() or not suffix.isdecimal() for suffix in suffixes)):
                        raise ValueError("Invalid exact stock Flayer projectile hit")
                    rows.append(dict(map=area, sequence=sequence, shooter=shooter, victim=victim, projectile=projectile,
                                     before=before, after=after, damage=amount))
                elif fields[0] == "native-campaign-flayer-projectile-status" and len(fields) == 3:
                    area, proof = fields[1:]
                    if (area == "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P" and data.get("map") == area
                            and data.get("state") == "hosting" and len(proof) <= 64
                            and proof.isascii() and proof.islower() and proof.replace("_", "").isalpha()):
                        data["native_campaign_flayer_projectile_status"] = proof
                elif fields[0] == "native-campaign-flayer-shot-aim" and len(fields) in (5, 6):
                    area, victim, shooter, reach = fields[1:5]
                    stage = fields[5] if len(fields) == 6 else "applied"
                    reach = _finite(reach)
                    if (area != "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P" or data.get("map") != area
                            or data.get("state") != "hosting" or not (stage == "armed" and reach is None
                            or stage == "applied" and reach is not None and 300 <= reach <= 660)):
                        raise ValueError("Invalid exact stock Flayer shooting fixture")
                    data["native_campaign_flayer_shot_aim"] = dict(map=area, victim=victim, shooter=shooter, reach=reach, state=stage)
                elif fields[0] == "native-campaign-npc-approach-geometry" and len(fields) == 8:
                    data["native_campaign_npc_approach_geometry"] = {
                        "map": fields[1], "member": fields[2], "sweep": _finite(fields[3]),
                        "floor_pairs": int(fields[4]), "placed_pairs": int(fields[5]),
                        "trace": fields[6], "component": fields[7],
                    }
                elif fields[0] == "native-campaign-npc-refresh" and len(fields) == 13:
                    keys = ("population", "capacity", "markers", "eligible_markers", "stock_calls")
                    data["native_campaign_npc_refresh"] = {
                        "map": fields[1], "mode": fields[2], "state": fields[3],
                        **dict(zip(keys, map(int, fields[4:9]))),
                        "spawner": fields[9], "data": fields[10],
                        "husk_receipts_before": int(fields[11]), "husk_receipts_after": int(fields[12]),
                    }
                elif fields[0] == "native-campaign-npc-combat" and len(fields) == 16:
                    identifiers = ("map", "state", "host_pawn", "guest_pawn", "attacker", "current_target")
                    counters = ("attempts", "guest_hits", "host_hits")
                    health = ("host_baseline", "host_at_hit", "host_current", "guest_before", "guest_after", "guest_current")
                    data["native_campaign_npc_combat"] = {
                        **dict(zip(identifiers, fields[1:7])), **dict(zip(counters, map(int, fields[7:10]))),
                        **dict(zip(health, map(_finite, fields[10:]))),
                    }
                elif fields[0] == "native-campaign-npc-melee-sickness" and len(fields) == 15:
                    identities = ("map", "host_pawn", "guest_pawn", "attacker")
                    counters = ("guest_before", "guest_after", "host_before", "host_after", "host_current",
                                "reads_before", "reads_after", "writes_before", "writes_after", "stock_dose")
                    data["native_campaign_npc_melee_sickness"] = {
                        **dict(zip(identities, fields[1:5])), **dict(zip(counters, map(int, fields[5:]))),
                    }
                elif fields[0] == "native-guest-reload-intent" and len(fields) == 4:
                    data["native_guest_reload_intent"] = {"map": fields[1], "pawn": fields[2], "sequence": int(fields[3])}
                elif fields[0] == "native-guest-equipped-weapon" and len(fields) == 4 and fields[3] in PRIMARY_KINDS:
                    data.setdefault("native_guest_equipped_weapons", {})[fields[1]] = {"guid": fields[2], "kind": fields[3]}
                elif fields[0] == "native-guest-primary-fixture" and len(fields) == 6:
                    data["native_guest_primary_fixture"] = {"map": fields[1], "pawn": fields[2], "guid": fields[3],
                                                            "sequence": int(fields[4]), "stock_wrappers": int(fields[5])}
                elif fields[0] == "native-guest-primary-scope" and len(fields) == 6:
                    data["native_guest_primary_scope"] = dict(zip(("map", "pawn", "guid", "kind", "mode"), fields[1:]))
                elif (fields[0] == "native-guest-held-fire" and len(fields) == 7 and valid_guid(fields[3])
                      and (stroke := _held_input_sequence(fields[4])) and fields[5] == "held"
                      and fields[6] in ("mapped-key", "edge-window", "headless-fixture")):
                    data["native_guest_held_fire"] = dict(zip(("map", "pawn", "guid"), fields[1:4]),
                                                         stroke=stroke, state=fields[5], proof=fields[6])
                elif (fields[0] == "native-guest-held-fire-fixture" and len(fields) == 7 and valid_guid(fields[3])
                      and (sequence := _held_input_sequence(fields[4])) and (stroke := _held_input_sequence(fields[5]))
                      and (fields[6] == "press" and sequence == stroke or fields[6] == "release" and sequence > stroke)):
                    data["native_guest_held_fire_fixture"] = dict(zip(("map", "pawn", "guid"), fields[1:4]),
                                                                 sequence=sequence, stroke=stroke, edge=fields[6])
                elif fields[0] == "native-host-camera-ready" and len(fields) == 4 and valid_guid(fields[3]):
                    data["native_host_camera_ready"] = dict(zip(("map", "pawn", "guid"), fields[1:]))
                elif fields[0] == "native-host-camera-scope" and len(fields) == 5 and fields[4] == "stock_held" and valid_guid(fields[3]):
                    data["native_host_camera_scope"] = dict(zip(("map", "pawn", "guid", "mode"), fields[1:]))
                elif fields[0] == "native-host-camera-fixture" and len(fields) == 6 and valid_guid(fields[3]):
                    data["native_host_camera_fixture"] = {"map": fields[1], "pawn": fields[2], "guid": fields[3],
                                                          "sequence": int(fields[4]), "stock_wrappers": int(fields[5])}
                elif fields[0] == "native-host-camera-equip-fixture" and len(fields) == 5 and valid_guid(fields[3]):
                    data["native_host_camera_equip_fixture"] = dict(zip(("map", "pawn", "guid", "state"), fields[1:]))
                elif fields[0] == native_host_dig.TAG:
                    if native_host_dig.KEY in data:
                        raise ValueError("Repeated stock host dig report")
                    data[native_host_dig.KEY] = native_host_dig.parse(fields, data.get("state"), data.get("map"))
                elif fields[0] == "native-host-landmine":
                    report = parse_host_landmine(fields)
                    if report:
                        data["native_host_landmine"] = report
                elif fields[0] == "native-guest-bandage" and len(fields) == 13 and fields[12] in ("0", "1"):
                    data["native_guest_bandage"] = {"state": fields[1], "map": fields[2], "pawn": fields[3], "guid": fields[4],
                                                   "quantity_before": int(fields[5]), "quantity_after": int(fields[6]),
                                                   "regen_before": _finite(fields[7]), "regen_after": _finite(fields[8]),
                                                   "health_before": _finite(fields[9]), "health_after": _finite(fields[10]),
                                                   "max_health": _finite(fields[11]), "hemophilia": fields[12] == "1"}
                elif fields[0] in MEALS:
                    proof = parse_meal(fields)
                    key = CONSUMABLE_RESULT[fields[0]]
                    if key in data or data.get("state") != "hosting" or data.get("map") != proof["map"]:
                        raise ValueError("Invalid stock food report scope")
                    data[key] = proof
                elif fields[0] == "native-guest-mre-fixture" and len(fields) in (6, 10, 11):
                    area, pawn, food, intox, host_intox = fields[1:6]
                    if (data.get("state") != "hosting" or data.get("map") != area or not pawn.startswith("Player_FP_C " + area + ".")
                            or (float(food), int(intox)) != (17, 42) or not 0 <= int(host_intox) <= 100
                            or "native_guest_mre_fixture" in data):
                        raise ValueError("Invalid detached MRE fixture report")
                    data["native_guest_mre_fixture"] = {"map": area, "pawn": pawn, "food": 17, "intoxication": 42,
                                                         "host_intoxication": int(host_intox)}
                    if len(fields) == 10:
                        vitals = tuple(map(_finite, fields[6:]))
                        if vitals != (250, 210, 250, 230):
                            raise ValueError("Invalid detached MRE fixture wound baseline")
                        data["native_guest_mre_fixture"]["wound_baseline"] = dict(zip(
                            ("hp_before", "hp_after", "regen_before", "regen_after"), vitals))
                    elif len(fields) == 11:
                        damage = tuple(map(_finite, fields[6:]))
                        if damage != (40, 250, 210, 250, 230):
                            raise ValueError("Invalid stock MRE fixture nonlethal damage proof")
                        data["native_guest_mre_fixture"]["damage"] = dict(zip(
                            ("amount", "hp_before", "hp_after", "regen_before", "regen_after"), damage))
                elif fields[0] == "native-guest-regen" and len(fields) in (4, 16):
                    effect = {"state": fields[1], "map": fields[2], "pawn": fields[3]}
                    if len(fields) == 16:
                        keys = ("total_seconds", "pulse_seconds", "health_before", "health_after", "ceiling", "delay_before",
                                "delay_after", "food_before", "food_after", "stim_before", "stim_after")
                        effect.update(zip(keys, map(_finite, fields[5:])))
                        effect["pulses"] = int(fields[4])
                    data.setdefault("native_guest_regen", {})[fields[3]] = effect
                elif fields[0] == "native-guest-sickness" and len(fields) == 11:
                    keys = ("infect_reads", "infect_writes", "slow_reads", "slow_writes",
                            "guest_sickness", "host_sickness", "guest_before", "guest_after")
                    values = tuple(map(int, fields[3:]))
                    if any(value < 0 for value in values[:4]):
                        raise ValueError("Stock guest sickness counters must be nonnegative")
                    data.setdefault("native_guest_sickness", {})[fields[1]] = {
                        "state": fields[2], **dict(zip(keys, values)),
                    }
                elif fields[0] == "native-guest-sickness-status" and len(fields) == 3:
                    data.setdefault("native_guest_sickness", {})[fields[1]] = {"state": fields[2]}
                elif fields[0] == "native-guest-sickness-fixture" and len(fields) == 5:
                    data["native_guest_sickness_fixture"] = {
                        "map": fields[1], "pawn": fields[2], "amount": int(fields[3]), "state": fields[4],
                    }
                elif fields[0] == "native-guest-revive" and len(fields) == 6:
                    data.setdefault("native_guest_revivals", {})[fields[2]] = {
                        "map": fields[1], "pawn": fields[2], "owner": fields[3], "health": _finite(fields[4]), "maximum": _finite(fields[5]),
                    }
                elif fields[0] == "native-wipe-view" and len(fields) == 13:
                    data["native_wipe_view"] = {"state": fields[1], "id": fields[2], "map": fields[3], "pawn": fields[4],
                                                "owner": fields[5], "deadline": int(fields[6]), "offset": int(fields[7]),
                                                "total": int(fields[8]), "guid": fields[9], "selected": int(fields[10]),
                                                "limit": int(fields[11]), "sequence": int(fields[12])}
                elif (fields[0] == "native-loot-crate-input" and len(fields) == 4
                      and AUTHORED_CRATES.get(fields[2]) == fields[1] and fields[3] in ("ready", "exhausted")):
                    row = {"map": fields[1], "crate": fields[2], "state": fields[3]}
                    data.setdefault("native_loot_crates", {})[fields[2]] = row
                    if fields[2] == LEGACY_CRATE:
                        data["native_loot_crate_input"] = row
                elif (fields[0] == "native-loot-crate-diagnostic" and len(fields) == 10
                      and AUTHORED_CRATES.get(fields[2]) == fields[1]):
                    row = {"map": fields[1], "crate": fields[2], "phase": fields[3],
                           "scope": fields[4], "door": fields[5], "box": fields[6],
                           "looted": None if fields[7] == "-" else fields[7] == "1",
                           "collision": _finite(fields[8]), "roll": _finite(fields[9])}
                    data.setdefault("native_loot_crate_diagnostics", {})[fields[2]] = row
                    if fields[2] == LEGACY_CRATE:
                        data["native_loot_crate_diagnostic"] = row
                elif fields[0] == "native-loot-crate-interaction" and len(fields) == 6:
                    data["native_loot_crate_interaction"] = {"map": fields[1], "crate": fields[2], "pawn": fields[3],
                                                            "sequence": int(fields[4]), "entry": fields[5]}
                elif (fields[0] == "native-loot-view-nearby" and len(fields) == 5
                      and AUTHORED_CRATES.get(fields[2]) == fields[1] and 0 < len(fields[3]) <= 512
                      and (distance := _finite(fields[4])) is not None and 0 <= distance < 1000):
                    data.setdefault("native_loot_view_candidates", {})[fields[2]] = {
                        "map": fields[1], "crate": fields[2], "pawn": fields[3], "distance": distance,
                    }
                elif fields[0] == "native-loot-view" and len(fields) == 7 and fields[6] in AUTHORED_CRATES:
                    data["native_loot_view"] = {"pawn": fields[1], "id": fields[2], "state": fields[3],
                                                "sequence": None if fields[4] == "-" else int(fields[4]), "guid": fields[5],
                                                "crate": fields[6]}
                elif fields[0] == "native-guest-reload-fixture" and len(fields) == 5:
                    data["native_guest_reload_fixture"] = {"map": fields[1], "pawn": fields[2],
                                                           "sequence": int(fields[3]), "stock_wrappers": int(fields[4])}
                elif fields[0] == "native-inventory-view" and len(fields) == 8:
                    data["native_inventory_view"] = {"state": fields[1], "sequence": int(fields[2]),
                                                    "map": fields[3], "pawn": fields[4], "offset": int(fields[5]),
                                                    "total": int(fields[6]), "count": int(fields[7])}
                elif fields == ["native-inventory-sort-capability", "1"]:
                    data["native_inventory_sort_capability"] = 1
                elif fields == [native_guest_stack_drop.ACTION + "-capability", "1"]:
                    if data.get("state") == "hosting" and data.get("map") == native_guest_stack_drop.OBSERVATORY:
                        data[native_guest_stack_drop.CAPABILITY] = 1
                elif fields[0] == native_guest_stack_drop.ACTION:
                    if native_guest_stack_drop.KEY in data:
                        raise ValueError("Duplicate isolated stack drop report")
                    data[native_guest_stack_drop.KEY] = native_guest_stack_drop.parse(fields, data.get("state"), data.get("map"))
                elif fields[0] == native_guest_stack_drop.SETTLE:
                    if native_guest_stack_drop.SETTLE_KEY in data:
                        raise ValueError("Duplicate isolated stack drop settlement")
                    data[native_guest_stack_drop.SETTLE_KEY] = native_guest_stack_drop.parse(fields, data.get("state"), data.get("map"))
                elif fields[0] == "native-inventory-sort" and len(fields) == 5:
                    data["native_inventory_sort"] = {"map": fields[1], "pawn": fields[2], "state": fields[3],
                                                     "count": int(fields[4])}
                elif fields == ["native-inventory-split-capability", "1"]:
                    data[native_inventory.SPLIT_CAPABILITY] = 1
                elif (fields[0] == native_inventory.SPLIT and len(fields) == 9 and data.get("map") == fields[1]
                      and valid_guid(fields[3]) and valid_guid(fields[4]) and fields[3] != fields[4]
                      and fields[5] in native_inventory.SPLIT_CLASSES):
                    data[native_inventory.SPLIT_KEY] = dict(
                        map=fields[1], pawn=fields[2], source_guid=fields[3], new_guid=fields[4], class_path=fields[5],
                        quantity_before=int(fields[6]), remaining=int(fields[7]), split=int(fields[8]),
                    )
                elif fields == ["native-inventory-split-view-capability", "1"]:
                    data["native_inventory_split_view_capability"] = True
                elif (fields[0] == "native-guest-inventory-split-view" and len(fields) == 8
                      and fields[1].isascii() and fields[1].isdigit() and len(fields[1]) <= 16
                      and 0 < (sequence := int(fields[1])) < 2**53
                      and fields[2] == data.get("map") and 0 < len(fields[3]) <= 512 and valid_guid(fields[4])
                      and fields[5] in native_inventory.SPLIT_CLASSES and fields[6] in {"confirming", "splitting", "split", "refused"}
                      and fields[7].isascii() and fields[7].isdigit() and len(fields[7]) <= 10
                      and 1 < (count := int(fields[7])) < 2**31):
                    data["native_guest_inventory_split_view"] = dict(sequence=sequence, map=fields[2], pawn=fields[3],
                                                                   guid=fields[4], class_path=fields[5], state=fields[6], count=count)
                elif fields[0] == "native-guest-inventory-equip-view" and len(fields) == 6:
                    data["native_guest_inventory_equip_view"] = {"sequence": int(fields[1]), "map": fields[2],
                                                                 "pawn": fields[3], "guid": fields[4], "state": fields[5]}
                elif (fields[0] == "native-guest-inventory-use-view" and len(fields) == 6
                      and fields[1].isascii() and fields[1].isdigit() and len(fields[1]) <= 16
                      and 0 < (sequence := int(fields[1])) < 2**53
                      and 0 < len(fields[2]) <= 256 and 0 < len(fields[3]) <= 512 and valid_guid(fields[4])
                      and fields[5] in {"selecting", "confirming", "using", "used", "refused"}):
                    data["native_guest_inventory_use_view"] = {"sequence": sequence, "map": fields[2],
                                                                "pawn": fields[3], "guid": fields[4], "state": fields[5]}
                elif fields[0] == "native-bag-projection" and len(fields) == 7:
                    data["native_bag_projection"] = {"map": fields[1], "epoch": fields[2], "owner": fields[3],
                                                     "count": int(fields[4]), "requested": int(fields[5]), "status": fields[6]}
                elif fields[0] == "native-bag-projection-interaction" and len(fields) == 6:
                    data["native_bag_projection_interaction"] = {"map": fields[1], "epoch": fields[2], "bag": fields[3],
                                                                 "sequence": int(fields[4]), "entry": fields[5]}
                elif fields[0] == "native-finite-shop" and len(fields) == 10:
                    data["native_finite_shop"] = parse_finite_shop_summary(fields)
                elif fields[0] == "native-finite-shop-item" and len(fields) == 6 and "native_finite_shop" in data:
                    data["native_finite_shop"]["offers"].append(parse_finite_shop_item(fields))
                elif fields[0] == "native-shop-view" and len(fields) == 12:
                    data["native_shop_view"] = {
                        "state": fields[1], "sequence": int(fields[2]), "map": fields[3], "pawn": fields[4], "actor": fields[5],
                        "wallet": None if fields[6] == "-" else int(fields[6]), "total": int(fields[7]),
                        "visible": int(fields[8]), "selection": int(fields[9]),
                        "guid": None if fields[10] == "-" else fields[10], "price": None if fields[11] == "-" else int(fields[11]),
                    }
                elif fields[0] == "native-shop-view-page" and len(fields) == 9:
                    data["native_shop_view_page"] = {
                        "sequence": int(fields[1]), "map": fields[2], "pawn": fields[3], "actor": fields[4],
                        "offset": int(fields[5]), "pending_event": int(fields[6]), "wanted_offset": int(fields[7]),
                        "render_event": int(fields[8]),
                    }
                elif fields[0] in native_dust_cabinet.STATUS_KEYS:
                    key = native_dust_cabinet.STATUS_KEYS[fields[0]]
                    if key in data:
                        raise ValueError("Repeated stock cabinet report")
                    data[key] = native_dust_cabinet.parse(fields, data.get("state"), data.get("map"))
                elif fields[0] == native_savlo_locker.TAG:
                    if native_savlo_locker.KEY in data:
                        raise ValueError("Repeated stock secure locker report")
                    data[native_savlo_locker.KEY] = native_savlo_locker.parse(fields, data.get("state"), data.get("map"))
                elif fields[0] == "native-savlo-locker-diagnostic" and len(fields) == 3 and fields[1] == native_savlo_locker.TRENCH:
                    data["native_savlo_locker_diagnostic"] = fields[2]
                elif fields[0] == native_dead_drop.TAG:
                    if native_dead_drop.KEY in data:
                        raise ValueError("Repeated stock CrossRoads Dead Drop report")
                    data[native_dead_drop.KEY] = native_dead_drop.parse(fields, data.get("state"), data.get("map"))
                elif fields[0] == "native-authored-pickup" and len(fields) in (6, 7):
                    source, area, actor, item_class, state = fields[1:6]
                    placed = data.setdefault(native_authored_pickup.SOURCES_KEY, {})
                    if (source in placed or native_authored_pickup.SOURCES.get(source) != (area, actor, item_class)
                            or state not in ("available", "claimed")):
                        raise ValueError("Invalid authored pickup status")
                    placed[source] = {"source": source, "map": area, "actor": actor, "class_path": item_class, "state": state}
                    if len(fields) == 7:
                        amount = int(fields[6])
                        if (data.get("state"), data.get("map")) != ("hosting", area) or not (
                                native_authored_pickup.valid_cash_amount(source, amount)):
                            raise ValueError("Invalid authority authored stock cash value")
                        placed[source]["wallet_value"] = amount
                elif fields[0] == "native-authored-pickup-interaction" and len(fields) == 6:
                    source, area, pawn, serial, entry = fields[1:]
                    intents = data.setdefault(native_authored_pickup.INTERACTIONS_KEY, {})
                    if (source in intents or source not in native_authored_pickup.MAP_SOURCES.get(area, ())
                            or not pawn or not 0 < int(serial) < 2**53 or entry not in ("fixture", "stock")):
                        raise ValueError("Invalid authored pickup interaction status")
                    intents[source] = {"source": source, "map": area, "pawn": pawn, "sequence": int(serial), "entry": entry}
                elif fields[0] == "native-authored-pickup-claim" and len(fields) == 9:
                    data["native_authored_pickup_claim"] = {"source": fields[1], "map": fields[2], "pawn": fields[3],
                                                            "ok": fields[4] == "1", "applied": fields[5] == "1",
                                                            "status": fields[6], "guid": fields[7], "slot": fields[8]}
                elif fields[0] == native_authored_pickup.MERGE_TAG:
                    if native_authored_pickup.MERGE_KEY in data:
                        raise ValueError("Repeated authored supply merge report")
                    data[native_authored_pickup.MERGE_KEY] = native_authored_pickup.parse_merge(fields, data)
                elif fields[0] == native_authored_pickup.CASH_TAG:
                    if native_authored_pickup.CASH_KEY in data:
                        raise ValueError("Repeated exact authored cash credit")
                    data[native_authored_pickup.CASH_KEY] = native_authored_pickup.parse_cash(fields, data)
                elif fields[0] == "native-rest-guard" and len(fields) == 4:
                    data["native_rest_guard"] = {"armed": fields[1] == "1", "status": fields[2],
                                               "guest_denials": int(fields[3])}
                elif fields[0] == "native-rest-hook" and len(fields) == 5:
                    data["native_rest_hook"] = {"installed": fields[1] == "1", "armed": fields[2] == "1",
                                               "fresh": fields[3] == "1", "status": fields[4]}
                elif fields[0] == "native-cistern-keypad" and len(fields) == 7:
                    data["native_cistern_keypad"] = {
                        "map": fields[1], "pawn": fields[2], "door": fields[3], "key": int(fields[4]),
                        "locked": fields[5] == "1", "unlocked": fields[6] == "1",
                    }
                elif fields[0] == "native-world-view" and len(fields) in (21, 27) and fields[1] in ("sample", "applied"):
                    row = {"mode": fields[1], "map": fields[2], "actor": fields[3], "epoch": fields[4],
                           "sequence": int(fields[5]), "sun_visible": fields[13] == "1", "sun_intensity": _finite(fields[14]),
                           "clock_text": fields[18], "save_unchanged": fields[19] == "1", "sky_verified": fields[20] == "1"}
                    row.update(zip(("game_seconds", "pitch", "yaw", "roll", "sun_x", "sun_y", "sun_z"),
                                   map(_finite, fields[6:13])))
                    row.update(zip(("sky_r", "sky_g", "sky_b"), map(_finite, fields[15:18])))
                    if len(fields) == 27:
                        row.update(zip(WORLD_WEATHER_FIELDS, map(_finite, fields[21:26])))
                        row["weather_verified"] = fields[26] == "1"
                    data["native_world_view"] = row
                elif fields[0] == "native-loot" and len(fields) in (14, 15):
                    count = int(fields[12])
                    if AUTHORED_CRATES.get(fields[3]) != fields[2] or not 0 <= count <= loot_capacity(fields[3]):
                        raise ValueError("Stock container receipt has an unqualified map or item count")
                    data["native_loot"] = {
                        "action": fields[1], "map": fields[2], "crate": fields[3], "pawn": fields[4],
                        "ok": fields[5] == "1", "status": fields[6], "looted": fields[7] == "1",
                        "lucky_looter": fields[8] == "1", "trait_source": fields[9], "trait_calls": int(fields[10]),
                        "distance": _finite(fields[11]), "item_count": count, "invoked": fields[13] == "1",
                        "items": [], "escrow_slot": fields[14] if len(fields) == 15 else "-",
                    }
                elif fields[0] == "native-loot-item" and len(fields) in (3, 4) and "native_loot" in data:
                    loot = data["native_loot"]
                    if len(loot["items"]) >= loot["item_count"]:
                        raise ValueError("Stock container reported more rows than its receipt")
                    item = {"actor": fields[1], "class_path": fields[2]}
                    if len(fields) == 4:
                        item["guid"] = fields[3]
                    loot["items"].append(item)
                elif fields[0] == "native-loot-cash" and len(fields) == 5 and "native_loot" in data:
                    loot = data["native_loot"]
                    if loot.get("action") != "claim" or "cash" in loot:
                        raise ValueError("Stock crate cash proof is out of scope")
                    loot["cash"] = dict(zip(("guid", "amount", "before", "after"),
                                            (fields[1], *map(int, fields[2:]))))
                elif fields[0] == "native-wipe-inventory" and len(fields) == 8:
                    data["native_wipe_inventory"] = {
                        "map": fields[1], "source": fields[2], "replacement": fields[3],
                        "escrow": None if fields[4] == "-" else fields[4],
                        "kept": int(fields[5]), "dropped": int(fields[6]), "discarded": int(fields[7]),
                    }
                elif fields[0] == "native-wipe-inventory-action" and len(fields) == 9:
                    data["native_wipe_inventory_action"] = {
                        "action": fields[1], "map": fields[2], "pawn": fields[3], "source": fields[4],
                        "replacement": None if fields[5] == "-" else fields[5],
                        "ok": fields[6] == "1", "status": fields[7], "count": int(fields[8]),
                    }
                elif fields[0] == "native-inventory-census" and len(fields) == 6:
                    data["native_inventory_census"] = {
                        "slot": fields[1], "map": fields[2], "total": int(fields[3]), "offset": int(fields[4]),
                        "count": int(fields[5]), "items": [],
                    }
                elif fields[0] == "native-inventory-census-item" and len(fields) == 9 and "native_inventory_census" in data:
                    data["native_inventory_census"]["items"].append({
                        "guid": fields[1], "source": fields[2], "index": int(fields[3]), "class_path": fields[4],
                        "quantity": int(fields[5]), "stackable": fields[6] == "1",
                        "important": fields[7] == "1", "cannot_keep": fields[8] == "1",
                    })
                elif fields[0] == "native-dream-arrival" and len(fields) == 6:
                    data["native_dream_arrival"] = {
                        "destination": fields[1], "actor": fields[2], "location": [_finite(value) for value in fields[3:6]],
                    }
                elif fields[0] == "native-nothing-return" and len(fields) == 8:
                    data["native_nothing_return"] = {
                        "map": fields[1], "installed": fields[2] == "1", "armed": fields[3] == "1",
                        "fresh": fields[4] == "1", "status": fields[5],
                        "pawn": None if fields[6] == "-" else fields[6], "volume": None if fields[7] == "-" else fields[7],
                    }
                elif fields[0] == "native-kill-volume":
                    if native_kill_volume.KEY in data:
                        raise ValueError("Duplicate CrossRoads KillVolume state")
                    data[native_kill_volume.KEY] = native_kill_volume.parse_state(fields, data.get("state"), data.get("map"))
                elif fields[0] == "native-kill-volume-recovered":
                    recovery = native_kill_volume.parse_recovered(fields, data.get("state"), data.get(native_kill_volume.KEY))
                    recoveries = data.setdefault(native_kill_volume.RECOVERED, {})
                    pawn = recovery["pawn"]
                    if pawn in recoveries or len(recoveries) >= 5 or any(row["sequence"] == recovery["sequence"] for row in recoveries.values()):
                        raise ValueError("Duplicate or excessive CrossRoads KillVolume recovery proof")
                    recoveries[pawn] = recovery
                elif fields[0] == "native-dream-isolation" and len(fields) == 6:
                    data["native_dream_isolation"] = {
                        "state": fields[1], "map": fields[2], "actor": fields[3], "detail": fields[4],
                        "early_requested": fields[5] == "1",
                    }
                elif fields[0] == "native-black-stone" and len(fields) == 9:
                    data["native_black_stone"] = {
                        "map": fields[1], "installed": fields[2] == "1", "armed": fields[3] == "1",
                        "fresh": fields[4] == "1", "status": fields[5], "guid": fields[6],
                        "entry": fields[7], "pawn": fields[8],
                    }
                elif fields[0] == "native-dream-formation" and len(fields) in (3, 5):
                    data["native_dream_formation"] = {"map": fields[1], "adjustments": int(fields[2])}
                    if len(fields) == 5:
                        data["native_dream_formation"].update(enrolled=int(fields[3]), detail=fields[4])
                elif fields[0] == "native-pickup" and len(fields) in (9, 10):
                    data["native_pickup"] = {
                        "state": fields[1], "guid": fields[2], "class_path": fields[3],
                        "ammo": int(fields[4]) if fields[4] != "-" else None, "weight": _finite(fields[5]),
                        "value": int(fields[6]) if fields[6] != "-" else None,
                        "never_drop": fields[7] == "1", "actor": fields[8] if fields[8] != "-" else None,
                    }
                    if len(fields) == 10:
                        data["native_pickup"]["pawn"] = fields[9] if fields[9] != "-" else None
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
                elif fields[0] == "native-fall" and len(fields) == 13:
                    fall = {
                        "state": fields[1], "pawn": fields[2], "mode_changes": int(fields[3]), "starts": int(fields[4]),
                        "landings": int(fields[5]), "forwarded": int(fields[6]), "mode": int(fields[7]),
                        "elapsed": _finite(fields[8]), "vertical_speed": _finite(fields[9]),
                        "original_fall_time": _finite(fields[10]), "actor_tick": fields[11] == "1", "reason": fields[12],
                    }
                    data.setdefault("native_fall_members", {})[fall["pawn"]] = fall
                    if "native_fall" not in data:
                        data["native_fall"] = fall
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
                elif fields[0] == "native-seed" and len(fields) == 8:
                    data["native_seed"] = {
                        "state": fields[1], "source": fields[2], "destination": fields[3], "expected": int(fields[4]),
                        "observed": int(fields[5]) if fields[5] != "-" else None,
                        "stock_stream": int(fields[6]) if fields[6] != "-" else None, "reason": fields[7],
                    }
                elif fields[0] == "native-host-save" and len(fields) in (8, 9):
                    data["native_host_save"] = {
                        "state": fields[1], "slot": fields[2], "map": fields[3], "pawn": fields[4],
                        "seed": int(fields[5]), "backpack_count": int(fields[6]), "equipment_count": int(fields[7]),
                    }
                    if len(fields) == 9 and fields[8] != "-":
                        data["native_host_save"]["eather_remainder"] = _finite(fields[8])
                elif fields[0] == "native-world-flush" and len(fields) == 6:
                    data["native_world_flush"] = {"map": fields[1], "state": fields[2], "antfarm": fields[3],
                                                  "explosives": fields[4], "reason": fields[5]}
                elif fields[0] in ("native-guest-save-frame", "native-guest-save-member-frame") and len(fields) == 7:
                    frame = {
                        "state": fields[1], "slot": fields[2], "map": fields[3], "pawn": fields[4],
                        "backpack_count": int(fields[5]), "equipment_count": int(fields[6]),
                    }
                    if fields[0] == "native-guest-save-frame":
                        data["native_guest_save_frame"] = frame
                    else:
                        data.setdefault("native_guest_save_member_frames", {})[frame["pawn"]] = frame
                elif fields == ["native-roster-spawn-capability", "1"]:
                    data["native_roster_spawn_ready"] = True
                elif fields[0] == "native-roster-spawn" and len(fields) == 9 and fields[1] == "restarted":
                    data["native_roster_spawn"] = {
                        "map": fields[2], "connection": fields[3], "controller": fields[4], "pawn": fields[5],
                        "location": [_finite(axis) for axis in fields[6:]],
                    }
                elif fields[0] == "native-roster-state" and len(fields) == 5:
                    roster = data.setdefault("native_roster", {"connections": []})
                    roster.update(state=fields[1], driver=fields[2], count=int(fields[3]), reason=fields[4])
                elif fields[0] == "native-roster" and len(fields) == 6:
                    data.setdefault("native_roster", {"connections": []})["connections"].append({
                        "state": fields[1], "id": fields[2], "connection": fields[3],
                        "controller": None if fields[4] == "-" else fields[4],
                        "pawn": None if fields[5] == "-" else fields[5],
                    })
                elif fields[0] == "native-rule" and len(fields) == 3:
                    data.setdefault("native_rules", {})[fields[1]] = None if fields[2] == "-" else int(fields[2])
                elif fields[0] == "native-reputation" and len(fields) == 3:
                    data.setdefault("native_reputation", {})[fields[1]] = int(fields[2])
                elif fields[0] in ("native-quest-catalog", "native-quest-entry", "native-quest-reward-item"):
                    (board_rows if len(fields) >= 2 and fields[1] == "board" else quest_rows).append(fields)
                elif fields[0] == "native-matrix" or fields[0].startswith("native-matrix-"):
                    matrix_rows.append(fields)
                elif fields[0] == "native-quest" and len(fields) == 7:
                    data["native_quest"] = {
                        "id": fields[1], "state": fields[2], "step": fields[3], "final_step": fields[4],
                        "reward_cash": fields[5], "message": fields[6],
                    }
                elif fields[0] == "native-broker" and len(fields) in (15, 27):
                    data["native_broker"] = {
                        "operation": fields[1], "map": fields[2], "pawn": fields[3], "id": fields[4], "state": fields[5],
                        "step": None if fields[6] == "-" else int(fields[6]), "final_step": int(fields[7]),
                        "cash": int(fields[8]), "xp": int(fields[9]), "manifest_guid": fields[10],
                        "native_cash": int(fields[11]), "native_xp": int(fields[12]), "changed": fields[13] == "1",
                        "consumed_guid": fields[14],
                    }
                    if len(fields) == 27:
                        data["native_broker"].update({
                            key: int(value) for key, value in zip(("native_debt", "topic_epc", "topic_drill_lab",
                                "topic_micro_drill", "freelancer_rep", "fae_rep", "debt_reward", "voucher_rows",
                                "menu_guard_armed", "guarded_ping", "guarded_menu", "guarded_gameplay"),
                                fields[15:], strict=True)
                        })
                elif fields[0] == "native-wire-quest" and len(fields) == 18:
                    data["native_wire_quest"] = dict(zip(("operation", "map", "pawn", "board", "id", "state"), fields[1:7]))
                    data["native_wire_quest"].update(
                        step=None if fields[7] == "-" else int(fields[7]), final_step=int(fields[8]),
                        cash_reward=int(fields[9]), xp_reward=int(fields[10]), commune_base_reward=int(fields[11]),
                        changed=fields[12] == "1", native_cash=int(fields[13]), native_xp=int(fields[14]),
                        native_commune_rep=int(fields[15]), objective_guid=fields[16], objective_owner=fields[17],
                    )
                elif fields[0] == "native-wire-quest-redemption" and len(fields) == 13:
                    data["native_wire_quest_redemption"] = dict(zip(("map", "pawn", "board", "id", "weapon_guid"), fields[1:6]))
                    data["native_wire_quest_redemption"].update(zip(
                        ("ammo_units", "native_cash", "native_xp", "rep_before", "rep_after"), map(int, fields[6:11])))
                    data["native_wire_quest_redemption"].update(objective_guid=fields[11], objective_owner=fields[12])
                elif fields[0] in ("native-wire-reward", "native-meet-broker-reward") and len(fields) == 9:
                    reward = "native_wire_quest_reward" if fields[0] == "native-wire-reward" else "native_meet_broker_reward"
                    data[reward] = dict(zip(
                        ("state", "map", "pawn", "slot", "weapon_guid", "ammo_guid"), fields[1:7]))
                    data[reward].update(weapon_mag=int(fields[7]), ammo_units=int(fields[8]))
                elif fields[0] == "native-broker-position" and len(fields) == 8:
                    data["native_broker_position"] = {"map": fields[1], "pawn": fields[2], "target": fields[3],
                                                     "point": fields[4], "location": [_finite(axis) for axis in fields[5:]]}
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
                local_input = data.get("player_local_input")
                if local_input and local_input["pawn"] != data["pawns"].get("local", {}).get("name"):
                    del data["player_local_input"]
                station = data.get(native_mosby_station_view.KEY)
                if station and (data.get("state"), data.get("map"), data["pawns"].get("local", {}).get("name")) != (
                        "connected", station["map"], station["pawn"]):
                    del data[native_mosby_station_view.KEY]
                primary = data.get("native_guest_primary_scope", {})
                for name in ("native_guest_held_fire", "native_guest_held_fire_fixture"):
                    held_input = data.get(name)
                    if held_input and (data["state"] != "connected" or primary.get("kind") != "smg-standard"
                            or data.get("map") != held_input["map"]
                            or any(held_input[field] != primary.get(field) for field in ("map", "pawn", "guid"))):
                        del data[name]
                if marker_frame is not None and not malformed_markers:
                    with contextlib.suppress(ValueError):
                        data[native_downed_markers.KEY] = native_downed_markers.parse(marker_frame, marker_rows, data.get("map"))
                placed = data.get(native_authored_pickup.SOURCES_KEY, {})
                intents = data.get(native_authored_pickup.INTERACTIONS_KEY, {})
                configured = native_authored_pickup.MAP_SOURCES.get(data.get("map"), ())
                if any(source not in configured for source in placed) or intents.keys() - placed.keys():
                    raise ValueError("Authored pickup status does not match the current world")
                if len(configured) == 1:
                    source = configured[0]
                    if source in placed:
                        data["native_authored_pickup"] = placed[source]
                    if source in intents:
                        data["native_authored_pickup_interaction"] = intents[source]
                if dialogue_frame or dialogue_progress or dialogue_lines or dialogue_choices:
                    if not dialogue_frame or len(dialogue_frame) != 6 or data.get("state") != "hosting" or data.get("map") != dialogue_frame[1]:
                        raise ValueError("Stock dialogue frame has no qualified listen world")
                    data["native_dialogue_stock"] = native_dialogue.parse_stock(dialogue_frame, dialogue_lines, dialogue_choices, dialogue_progress)
                if mosby_frame or mosby_reputation:
                    if not mosby_frame:
                        raise ValueError("Missing Stone Maze quest frame")
                    data["native_mosby_readings"] = native_mosby_tape.parse_readings(mosby_frame, mosby_reputation)
                for key in (*(profile.key for profile in native_mosby_tape.BY_MAP.values()),
                            "native_mosby_readings", "native_mosby_reward"):
                    if key in data and (data.get("state"), data.get("map")) != ("hosting", data[key]["map"]):
                        raise ValueError("Stock tape proof is outside its authoritative map")
                if quest_rows:
                    try:
                        data["native_quest_catalog"] = parse_quest_catalog(quest_rows, sequence=data.get("sequence"))
                    except ValueError:
                        data["native_quest_catalog_error"] = "invalid_stock_report"
                if board_rows:
                    try:
                        data["native_bounty_offers"] = parse_quest_catalog(board_rows, sequence=data.get("sequence"), board=True)
                    except ValueError:
                        data["native_bounty_board_error"] = "invalid_stock_report"
                if matrix_rows:
                    try:
                        data["native_matrix"] = parse_matrix_report(matrix_rows)
                    except ValueError:
                        data["native_matrix_error"] = "invalid_stock_report"
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

    async def held_fire_command(self, operation: str, *arguments: str) -> dict:
        """Use the game-thread input lane without replacing campaign commands."""
        if operation not in {"start", "observe", "stop", "renew"} or any(
            len(value) > 512 or any(character in value for character in "\t\r\n") for value in arguments
        ):
            raise ValueError("Invalid stock held-fire command")
        nonce = uuid.uuid4().hex
        fields = (nonce, f"native-guest-smg-hold-{operation}", *arguments)
        command = self.directory / "held-fire-command.tsv"
        write_text(command, "\t".join(fields) + "\n")
        reply = self.directory / "held-fire-reply.tsv"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1.2
        try:
            while loop.time() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("The native host exited during a stock held-fire command")
                try:
                    with reply.open("r", encoding="utf-8") as stream:
                        raw = stream.read(8193)
                except (FileNotFoundError, PermissionError):
                    raw = ""
                if 0 < len(raw) <= 8192 and raw.endswith("\n"):
                    head, *rows = [line.split("\t") for line in raw.splitlines()]
                    if len(head) == 5 and head[:2] == ["held-fire-ack", nonce]:
                        _, _, result, area, message = head
                        if result != "accepted":
                            raise RuntimeError(message or "Stock held fire was refused")
                        proof = [native_smg_hold.parse(row) for row in rows if row[0] == "native-guest-smg-hold"]
                        return {"state": "hosting", "map": area, native_smg_hold.ALL: {row["pawn"]: row for row in proof}}
                await asyncio.sleep(.025)
            raise TimeoutError("The native game did not acknowledge stock held fire")
        finally:
            with contextlib.suppress(OSError):
                with command.open("rb") as stream:
                    pending = stream.read(33) == nonce.encode("ascii") + b"\t"
                if pending:
                    command.unlink()

    async def walk(self, x: float, y: float, milliseconds: int) -> None:
        if type(x) not in {int, float} or type(y) not in {int, float}:
            raise ValueError("Movement requires numeric x and y")
        if (not math.isfinite(x) or not math.isfinite(y) or math.hypot(x, y) > 1.001
                or math.hypot(x, y) < 0.01 or type(milliseconds) is not int
                or not 50 <= milliseconds <= 1000):
            raise ValueError("Use a normalized planar direction and a duration of 50 to 1000 ms")
        await self.command("walk", str(x), str(y), str(milliseconds))

    async def restore(self, role: str, pawn: dict, *, binding: Assignment | None = None,
                      respawning: bool = False) -> dict | None:
        coordinates = pawn.get("location")
        if not isinstance(coordinates, list) or len(coordinates) != 3:
            return
        if any(not isinstance(axis, (int, float)) or not math.isfinite(axis) for axis in coordinates):
            return
        health = pawn.get("health")
        raw_health = str(health) if isinstance(health, (int, float)) and math.isfinite(health) else "-"
        arguments = (raw_health, *(str(axis) for axis in coordinates), *(("native-wipe",) if respawning else ()))
        if binding is not None:
            return await self.command("restore-exact", binding.pawn, binding.controller, binding.connection, *arguments)
        return await self.command("restore", role, *arguments)
