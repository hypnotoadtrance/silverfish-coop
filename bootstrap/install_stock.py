"""Enable the matched co-op title bootstrap in a stock Steam-layout installation."""

import argparse
from hashlib import sha256
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from tools.silverfish import embedded_title_plan, runtime
from tools.silverfish.control import NAME
from tools.silverfish.embedded_engine import assert_private_saved_tree
from tools.silverfish.service import load_invite, menu_host_start

from . import install_stage, package


MOD = "SilverfishCoopBootstrap"
UE4SS_OFFICIAL = {
    Path("dwmapi.dll"): "21fafb3e49958e2b16721d57d5c1af3efcaaae6d91a430bb6124a67e80f5fb42",
    Path("ue4ss/UE4SS.dll"): "3843d81a8280096b0de3c750eebf15507e374c250dc551cbf07e7be7acc1e2ba",
    Path("ue4ss/UE4SS-settings.ini"): "c96a3b1ac2f29f4aca3c72198eb557075ee30fc1d1186c332a025a8d2e66bd48",
}
SIGNATURE = Path("UE4SS_Signatures/FText_Constructor.lua")
SIGNATURE_SHA256 = "da8a9c02ebf629f8e1dd112c6584d647cdafeb0c3a8b29e7f88c1d76bc9067d3"
SETTINGS = Path("ue4ss/UE4SS-settings.ini")
PROXY = Path("dwmapi.dll")
ARCHIVE_LIMIT = 64 * 1024 * 1024
AFTER_ACTIVATION = b"SilverfishCoopBootstrap : 1"
BOOTSTRAP_SCRIPTS = {
    Path("Scripts/main.lua"): package.BOOTSTRAP / package.MOD / "main.lua",
    Path("Scripts/native_title_entry.lua"): package.SHARED / "native_title_entry.lua",
    Path("Scripts/native_title_display.lua"): package.SHARED / "native_title_display.lua",
}
TITLE_DLL = Path("dlls/main.dll")
WORKER = Path("dlls/SilverfishCoopCoordinator.exe")


def _absolute(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if os.name == "nt" and re.fullmatch(r"[A-Za-z]:", path.drive) is None:
        raise ValueError("Use an absolute path on a local Windows drive")
    return path


def _ordinary(path: Path, *, directory: bool = False, missing: bool = False) -> bool:
    for component in (*reversed(path.parents), path):
        try:
            status = component.lstat()
        except FileNotFoundError:
            if missing:
                return False
            raise
        expected = stat.S_ISDIR if directory or component != path else stat.S_ISREG
        if not expected(status.st_mode) or getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Expected an ordinary local path at {component}")
    if path.resolve(strict=True) != path:
        raise ValueError(f"Path resolves outside its expected location: {path}")
    return True


def _read(path: Path, limit: int, *, missing: bool = False) -> bytes | None:
    if not _ordinary(path, missing=missing):
        return None
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"File exceeds its expected size: {path.name}")
    return data


def _pinned(path: Path, expected: str) -> bytes:
    data = _read(path, ARCHIVE_LIMIT)
    if sha256(data).hexdigest() != expected:
        raise ValueError(f"{path.name} differs from the pinned official UE4SS release")
    return data


def _bootstrap_payload(path: Path) -> dict[Path, bytes]:
    _ordinary(path)
    if path.stat().st_size > ARCHIVE_LIMIT:
        raise ValueError("Bootstrap archive exceeds its expected size")
    expected = {f"{MOD}/{relative.as_posix()}": relative for relative in (*BOOTSTRAP_SCRIPTS, TITLE_DLL, WORKER)}
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if (len(entries) != len(expected) or {entry.filename for entry in entries} != expected.keys()
                or any(entry.is_dir() or entry.orig_filename != entry.filename or entry.flag_bits & 1
                       or not 0 < entry.file_size <= ARCHIVE_LIMIT
                       or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG) for entry in entries)
                or sum(entry.file_size for entry in entries) > ARCHIVE_LIMIT):
            raise ValueError("Expected the full bootstrap zip with exactly its five ordinary files")
        payload = {expected[entry.filename]: archive.read(entry) for entry in entries}
    for relative, script in BOOTSTRAP_SCRIPTS.items():
        if payload[relative].replace(b"\r\n", b"\n") != script.read_bytes().replace(b"\r\n", b"\n"):
            raise ValueError(f"Bootstrap script differs from this checkout: {relative.name}")
    bridge, worker = payload[TITLE_DLL], payload[WORKER]
    if b"SilverfishReadCoopBootstrapTestKey" in bridge:
        raise ValueError("The headless proof adapter is for isolated replicas")
    if not bridge.startswith(b"MZ") or not worker.startswith(b"MZ") or sha256(worker).hexdigest().encode("ascii") not in bridge:
        raise ValueError("The bootstrap bridge and coordinator do not share the same SHA-256 pin")
    return payload


def _compatible_settings(raw: bytes, *, fresh: bool = False) -> None:
    if not raw:
        raise ValueError("UE4SS settings are empty")
    overrides = {"modsfolderpath", "modsfolderpaths", "controllingmodstxt"}
    console = {"consoleenabled", "guiconsoleenabled", "guiconsolevisible"}
    checked_console = set()
    for line in raw.decode("utf-8-sig").splitlines():
        if line.lstrip().startswith((";", "#")):
            continue
        key, _, value = line.partition("=")
        name, setting = key.strip().lstrip("+-").strip().casefold(), value.strip()
        if name in overrides and setting:
            raise ValueError("UE4SS settings redirect mod loading away from the default Mods list")
        if fresh and name in console:
            if setting != "0":
                raise ValueError("The official settings would enable the UE4SS debug consoles")
            checked_console.add(name)
    if fresh and checked_console != console:
        raise ValueError("The official settings do not explicitly disable the UE4SS debug consoles")


def _activation(raw: bytes | None) -> tuple[bytes, bool]:
    raw = raw or b""
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    text = raw[len(bom):].decode("utf-8")
    if "\r" in text.replace("\r\n", "") or "\x00" in text:
        raise ValueError("The existing UE4SS Mods list has an unsupported line format")
    lines = text.splitlines(keepends=True)
    found = None
    for offset, line in enumerate(lines):
        if ";" in line:
            continue
        parsed = line.rstrip("\r\n").replace(" ", "")
        name, separator, enabled = parsed.partition(":")
        if name.strip().casefold() != MOD.casefold():
            continue
        if found is not None or (name, separator) != (MOD, ":") or enabled not in ("0", "1"):
            raise ValueError("The UE4SS Mods list contains a conflicting Bootstrap activation")
        found = offset, enabled
    if found is not None and found[1] == "1":
        return raw, True
    newline = "\r\n" if "\r\n" in text else "\n"
    row = AFTER_ACTIVATION.decode("ascii")
    if found is None:
        return bom + (text + (newline if text and not text.endswith("\n") else "") + row + newline).encode("utf-8"), False
    offset = found[0]
    ending = lines[offset][len(lines[offset].rstrip("\r\n")):]
    lines[offset] = row + ending
    return bom + "".join(lines).encode("utf-8"), False


def _files(root: Path) -> set[Path]:
    pending, files = [root], set()
    while pending:
        for path in pending.pop().iterdir():
            if stat.S_ISDIR(path.lstat().st_mode):
                _ordinary(path, directory=True)
                pending.append(path)
            else:
                _ordinary(path)
                files.add(path.relative_to(root))
    return files


def _matched_mod(mod: Path, payload: dict[Path, bytes]) -> bool:
    if not _ordinary(mod, directory=True, missing=True):
        return False
    if {entry.name for entry in mod.iterdir()} != {"Scripts", "dlls"} or _files(mod) != payload.keys():
        raise ValueError("A different or incomplete title bootstrap already occupies the mod directory")
    if any(_read(mod / path, len(data)) != data for path, data in payload.items()):
        raise ValueError("The existing title bootstrap differs from the supplied package")
    return True


def _stock_signature(game: Path, stage: Path) -> str:
    digest = sha256()
    for relative in (runtime.SHIPPING, *(runtime.PAKS / name for name in runtime.STOCK_PACKAGES)):
        original, copied = game / relative, stage / relative
        _ordinary(original)
        _ordinary(copied)
        fingerprint = runtime._file_sha256(original)
        if runtime._file_sha256(copied) != fingerprint:
            raise ValueError(f"The staged and stock game builds differ at {relative.name}")
        digest.update(bytes.fromhex(fingerprint))
    return digest.hexdigest()


def _verify_stage(game: Path, work: Path) -> str:
    stage = work / "stage"
    _ordinary(game / runtime.MANIFEST)
    _ordinary(game / "SilverFish.exe")
    runtime._manifest_paths(game)
    _ordinary(stage / "silverfish-coop-stage.json")
    metadata = runtime.read_json(stage / "silverfish-coop-stage.json")
    if type(metadata.get("schema")) is not int or metadata["schema"] != 1:
        raise ValueError("The private co-op stage has no supported installation manifest")
    if _stock_signature(game, stage) != metadata.get("game_build"):
        raise ValueError("The private stage game fingerprint does not match its files")
    binaries = stage / runtime.SHIPPING.parent
    for relative in (PROXY, runtime.CPP_RUNTIME_DLL, runtime.CPP_ADDON_DLL, SETTINGS):
        _ordinary(binaries / relative)
    scripts = binaries / runtime.MOD_SCRIPTS
    _ordinary(scripts, directory=True)
    _files(scripts)
    if (not runtime.qualified_authored_routes(work)
            or runtime._code_fingerprint(scripts, ".lua") != runtime._code_fingerprint(runtime.REPO / "mod/SilverfishCoop/Scripts", ".lua")):
        raise ValueError("Expected the current matched native co-op stage")
    _compatible_settings(_read(binaries / SETTINGS, 128 * 1024))
    mods = _read(binaries / "ue4ss/Mods/mods.txt", 1024 * 1024)
    for name in ("SilverfishCoop", runtime.CPP_MOD_NAME):
        entries = [line.replace(" ", "") for line in mods.decode("utf-8-sig").splitlines()
                   if ";" not in line and line.replace(" ", "").partition(":")[0] == name]
        if entries != [f"{name}:1"]:
            raise ValueError(f"The private stage does not activate {name}")
    embedded = binaries / "ue4ss/Mods" / install_stage.MOD
    if not embedded_title_plan.installed(work):
        raise ValueError("The private stage requires its enabled embedded runtime")
    install_stage._verify(embedded / "runtime", embedded / "dlls/main.dll")
    return runtime.staged_build(work)


def _run_path(work: Path, kind: str, name: object) -> Path:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Invalid co-op campaign or client profile name")
    path = work / kind / name
    for part in (path, path / "run", path / "save"):
        _ordinary(part, directory=True, missing=True)
    _ordinary(path / "run" / "control.lock", missing=True)
    assert_private_saved_tree(path / "game-user")
    return path


def _plan(work: Path, build: str, host: dict | None, join: dict | None) -> dict:
    if host is None and join is None:
        raise ValueError("Configure a Host action, a Join action, or both")
    _run_path(work, "titles", "bootstrap")
    if host is not None:
        if (type(host) is not dict or set(host) != {"campaign", "bind", "port"} or not isinstance(host["bind"], str)
                or type(host["port"]) is not int or not 1 <= host["port"] <= 65535):
            raise ValueError("Invalid configured Host action")
        host = dict(host, bind=str(ip_address(host["bind"])))
        menu_host_start(_run_path(work, "campaigns", host["campaign"]))
    if join is not None:
        if type(join) is not dict or set(join) != {"profile", "invite", "character"}:
            raise ValueError("Invalid configured Join action")
        _run_path(work, "clients", join["profile"])
        if join["invite"] is None:
            if join["character"] is not None:
                raise ValueError("Choose a character after importing an invitation")
        else:
            invite = _absolute(join["invite"])
            if not invite.is_relative_to(work) or not invite.name.endswith(".invite.json"):
                raise ValueError("Import the invitation into the private co-op workspace")
            _ordinary(invite)
            loaded = load_invite(invite, build)
            chosen = loaded["character_id"] if join["character"] is None else join["character"]
            if not isinstance(chosen, str) or str(UUID(chosen)) != chosen:
                raise ValueError("Invalid configured guest character ID")
            join = dict(join, invite=str(invite))
    return {"schema": 1, "work": str(work), "host": host, "join": join}


def _atomic(path: Path, data: bytes, *, previous: bytes | None = None) -> None:
    _ordinary(path.parent, directory=True, missing=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ordinary(path.parent, directory=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if previous is not None:
            if _read(path, 1024 * 1024) != previous:
                raise ValueError(f"{path.name} changed during installation")
            temporary.replace(path)
        elif os.name == "nt":
            temporary.rename(path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(game: Path, work: Path, ue4ss: Path, signatures: Path, archive: Path, *,
            host: dict | None = None, join: dict | None = None, config: Path | None = None) -> dict:
    """Validate all inputs and publish the activation row only after the package and config."""
    game, work, ue4ss, signatures, archive = map(_absolute, (game, work, ue4ss, signatures, archive))
    for root in (game, work, ue4ss, signatures):
        _ordinary(root, directory=True)
    if game.is_relative_to(work) or work.is_relative_to(game):
        raise ValueError("The Steam game and private co-op workspace must be separate")
    local_value = os.environ.get("LOCALAPPDATA")
    local = _absolute(Path(local_value)) if local_value else None
    single_player = local / "SilverFish" if local else None
    if single_player:
        if work.is_relative_to(single_player) or single_player.is_relative_to(work):
            raise ValueError("The co-op workspace must be separate from the single-player save tree")
    if config is None:
        if not local:
            raise ValueError("The local Windows profile directory is unavailable")
        config = local / "SilverfishCoop" / "coordinator.json"
    else:
        config = _absolute(config)
    if config.is_relative_to(game) or single_player and config.is_relative_to(single_player):
        raise ValueError("The coordinator config must be outside the Steam game and single-player saves")

    payload = _bootstrap_payload(archive)
    official = {relative: _pinned(ue4ss / relative, digest) for relative, digest in UE4SS_OFFICIAL.items()}
    _compatible_settings(official[SETTINGS], fresh=True)
    official[Path("ue4ss") / SIGNATURE] = _pinned(signatures / SIGNATURE.name, SIGNATURE_SHA256)
    build = _verify_stage(game, work)
    plan = _plan(work, build, host, join)
    settings = (json.dumps(plan, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
    saved = _read(config, 8192, missing=True)
    if saved is not None and json.dumps(json.loads(saved), sort_keys=True) != json.dumps(plan, sort_keys=True):
        raise ValueError("A different local co-op coordinator configuration already exists")

    binaries = game / runtime.SHIPPING.parent
    loader = binaries / "ue4ss"
    mods = loader / "Mods"
    activation = mods / "mods.txt"
    mod = mods / MOD
    original = _read(activation, 1024 * 1024, missing=True)
    desired, active = _activation(original)
    missing, expected = {}, {}
    for relative, data in official.items():
        installed = _read(binaries / relative, ARCHIVE_LIMIT, missing=True)
        if installed is None:
            missing[relative] = data
        elif relative == SETTINGS:
            _compatible_settings(installed)
        elif installed != data:
            raise ValueError(f"A different loader or signature is already present at {relative}")
        expected[relative] = data if installed is None else installed
    present = _matched_mod(mod, payload)
    if active and (missing or not present or saved is None):
        raise ValueError("The Bootstrap is already enabled without its complete matching installation")

    installed_files = []
    for relative, data in missing.items():
        if relative != PROXY:
            _atomic(binaries / relative, data)
            installed_files.append(relative.as_posix())
    if not present:
        mods.mkdir(parents=True, exist_ok=True)
        _ordinary(mods, directory=True)
        with tempfile.TemporaryDirectory(prefix=".coop-bootstrap-", dir=loader) as temporary:
            candidate = Path(temporary) / MOD
            for relative, data in payload.items():
                _atomic(candidate / relative, data)
            _matched_mod(candidate, payload)
            candidate.rename(mod)
        installed_files.append(f"ue4ss/Mods/{MOD}")
    if saved is None:
        _atomic(config, settings)
    if PROXY in missing:
        _atomic(binaries / PROXY, missing[PROXY])
        installed_files.append(PROXY.as_posix())
    if not active:
        if (not _matched_mod(mod, payload) or _read(config, 8192) != (settings if saved is None else saved)
                or any(_read(binaries / relative, len(data)) != data for relative, data in expected.items())):
            raise ValueError("The installed bootstrap prerequisites changed before activation")
        if _read(activation, 1024 * 1024, missing=True) != original:
            raise ValueError("The UE4SS Mods list changed during installation")
        _atomic(activation, desired, previous=original)
    return {"schema": 1, "stock": str(game), "work": str(work), "build": build,
            "installed": installed_files, "config_created": saved is None, "activated": not active}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--ue4ss", type=Path, required=True)
    parser.add_argument("--signatures", type=Path, required=True)
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--host-campaign")
    parser.add_argument("--bind")
    parser.add_argument("--port", type=int)
    parser.add_argument("--join-profile")
    parser.add_argument("--invite", type=Path)
    parser.add_argument("--character")
    args = parser.parse_args()
    if args.host_campaign is None and (args.bind is not None or args.port is not None):
        parser.error("--bind and --port require --host-campaign")
    if args.invite is not None and args.join_profile is None or args.character is not None and args.invite is None:
        parser.error("--invite requires --join-profile. --character requires an invitation.")
    host = None if args.host_campaign is None else {
        "campaign": args.host_campaign, "bind": args.bind or "127.0.0.1", "port": 7777 if args.port is None else args.port,
    }
    join = None if args.join_profile is None else {"profile": args.join_profile, "invite": args.invite, "character": args.character}
    try:
        result = install(args.game, args.work, args.ue4ss, args.signatures, args.bootstrap, host=host, join=join)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, BadZipFile, NotImplementedError) as error:
        parser.exit(1, f"Stock co-op installation failed: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
