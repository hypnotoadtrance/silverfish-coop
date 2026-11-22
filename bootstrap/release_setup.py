"""Set up Project Silverfish co-op once from an extracted Windows release."""

import argparse
from hashlib import file_digest
from ipaddress import ip_address
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo


MANIFEST = "release.json"
STOCK_BOOTSTRAP = "payload/stock-bootstrap.zip"
GAMEPLAY_ADDON = "payload/native-gameplay.dll"
EMBEDDED_ADDON = "payload/embedded-addon.dll"
RUNTIME = "payload/runtime"
PYTHON = RUNTIME + "/python.exe"
LOADER = "payload/loader"
SIGNATURES = "payload/signatures"
LOADER_FILES = frozenset({
    LOADER + "/dwmapi.dll", LOADER + "/ue4ss/UE4SS.dll", LOADER + "/ue4ss/UE4SS-settings.ini",
    LOADER + "/ue4ss/LICENSE", SIGNATURES + "/FText_Constructor.lua",
})
GAMEPLAY_SOURCES = frozenset({
    "mod/SilverfishCoop/Scripts/main.lua", "mod/SilverfishCoop/Scripts/native_title_entry.lua",
    "mod/SilverfishCoop/Scripts/native_title_display.lua", "tools/__init__.py", "tools/silverfish/__init__.py",
    "tools/silverfish/embedded_startup.py",
})
INSTALLER_SOURCES = frozenset({
    "bootstrap/release_setup.py", "bootstrap/install_stage.py", "bootstrap/install_stock.py", "bootstrap/package.py",
})
FIXED_SOURCES = GAMEPLAY_SOURCES | INSTALLER_SOURCES | {"bootstrap/SilverfishCoopBootstrap/Scripts/main.lua"}
SETUP_FILES = FIXED_SOURCES | LOADER_FILES | {
    "Install.cmd", "Setup.ps1", "START-HERE.txt", STOCK_BOOTSTRAP, GAMEPLAY_ADDON, EMBEDDED_ADDON,
    PYTHON, RUNTIME + "/runtime-manifest.json",
}
SOURCE_PREFIXES = {"tools/silverfish/": ".py", "mod/SilverfishCoop/Scripts/": ".lua"}
SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
REVISION = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
COMPONENT = re.compile(r"[A-Za-z0-9_.-]+")
DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?", re.IGNORECASE)
EXCLUDED_RUNTIME_SUFFIXES = (".pak", ".utoc", ".ucas", ".uasset", ".uexp", ".ubulk", ".umap",
                             ".sav", ".usmap", ".jmap", ".invite.json", ".key", ".pem")
EXCLUDED_RUNTIME_NAMES = {"campaign.json", "campaign.previous.json", "host-identity.json"}
CA_BUNDLE = RUNTIME + "/site-packages/certifi/cacert.pem"
ENTRY_LIMIT = 6000
FILE_LIMIT = 64 * 1024**2
TOTAL_LIMIT = 192 * 1024**2
MANIFEST_LIMIT = 2 * 1024**2


def release_name(path: str) -> PurePosixPath:
    if (type(path) is not str or len(path) > 512 or any(
            not COMPONENT.fullmatch(part) or part.endswith(".") or DEVICE.fullmatch(part) for part in path.split("/"))):
        raise ValueError("Invalid release member path")
    return PurePosixPath(path)


def permitted_source(path: str, fixed_sources: frozenset[str] = FIXED_SOURCES) -> bool:
    return path in fixed_sources or any(
        path.startswith(prefix) and path.endswith(extension) for prefix, extension in SOURCE_PREFIXES.items()
    )


def write_zip(output: Path, files: dict[str, bytes]) -> None:
    """Write a deterministic ZIP with Windows-safe paths and bounded contents."""
    paths = set()
    total = 0
    for name, data in files.items():
        path = release_name(name)
        paths.add(path.as_posix())
        paths.update(parent.as_posix() for parent in path.parents[:-1])
        total += len(data)
        if len(data) > FILE_LIMIT:
            raise ValueError(f"Resident release file exceeds its size limit at {name}")
    if (len(paths) >= ENTRY_LIMIT or total > TOTAL_LIMIT
            or len(paths) != len({path.casefold() for path in paths})):
        raise ValueError("Resident release contains conflicting paths or exceeds its size limit")
    with ZipFile(output, "x", ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            member = ZipInfo(name)
            member.compress_type = ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            archive.writestr(member, data)


def permitted_member(path: str) -> bool:
    if path == CA_BUNDLE or path in SETUP_FILES or permitted_source(path):
        return True
    name = path.rsplit("/", 1)[-1].casefold()
    return (path.startswith(RUNTIME + "/") and not name.endswith(EXCLUDED_RUNTIME_SUFFIXES)
            and name not in EXCLUDED_RUNTIME_NAMES)


def _ordinary(path: Path, *, directory: bool) -> os.stat_result:
    info = path.lstat()
    predicate = stat.S_ISDIR if directory else stat.S_ISREG
    if (not predicate(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or path.resolve(strict=True) != path):
        raise ValueError(f"Expected an ordinary release path at {path.name}")
    return info


def members(root: Path):
    _ordinary(root, directory=True)
    stack, names = [root], set()
    while stack:
        for path in stack.pop().iterdir():
            relative = path.relative_to(root).as_posix()
            release_name(relative)
            folded = relative.casefold()
            if folded in names or len(names) >= ENTRY_LIMIT:
                raise ValueError("Release contains duplicate or too many entries")
            names.add(folded)
            directory = stat.S_ISDIR(path.lstat().st_mode)
            info = _ordinary(path, directory=directory)
            if directory:
                stack.append(path)
            else:
                yield relative, path, info.st_size


def _unique_object(items):
    output = {}
    for key, value in items:
        if key in output:
            raise ValueError("Release manifest contains a repeated field")
        output[key] = value
    return output


def verify_release(root: Path) -> dict:
    _ordinary(root, directory=True)
    manifest = root / MANIFEST
    if _ordinary(manifest, directory=False).st_size > MANIFEST_LIMIT:
        raise ValueError("Release manifest exceeds its size limit")
    with manifest.open("rb") as stream:
        raw = stream.read(MANIFEST_LIMIT + 1)
    if len(raw) > MANIFEST_LIMIT:
        raise ValueError("Release manifest exceeds its size limit")
    result = json.loads(raw, object_pairs_hook=_unique_object)
    if (type(result) is not dict or set(result) != {"schema", "version", "revision", "files"}
            or type(result["schema"]) is not int or result["schema"] != 1
            or not isinstance(result["version"], str) or not VERSION.fullmatch(result["version"])
            or not isinstance(result["revision"], str) or not REVISION.fullmatch(result["revision"])):
        raise ValueError("Unrecognized co-op release manifest")
    expected = result["files"]
    if type(expected) is not dict or len(expected) >= ENTRY_LIMIT or not SETUP_FILES.issubset(expected):
        raise ValueError("Release manifest is missing required files")
    total = 0
    for relative, record in expected.items():
        release_name(relative)
        if not permitted_member(relative):
            raise ValueError(f"Unexpected release member {relative}")
        if (type(record) is not dict or set(record) != {"bytes", "sha256"} or type(record["bytes"]) is not int
                or not 0 <= record["bytes"] <= FILE_LIMIT or not isinstance(record["sha256"], str)
                or not SHA256.fullmatch(record["sha256"])):
            raise ValueError(f"Invalid release file record at {relative}")
        total += record["bytes"]
        if total > TOTAL_LIMIT:
            raise ValueError("Release exceeds its size limit")
    seen = set()
    for relative, path, size in members(root):
        if relative == MANIFEST:
            continue
        record = expected.get(relative)
        if record is None or size != record["bytes"]:
            raise ValueError(f"Release has an unexpected file or size at {relative}")
        with path.open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != record["sha256"]:
                raise ValueError(f"Release file differs from its checksum at {relative}")
        seen.add(relative)
    if seen != expected.keys():
        raise ValueError("Release is missing one or more manifest files")
    return result


def _steam_roots():
    import winreg

    for hive, key, name in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key) as entry:
                path, _ = winreg.QueryValueEx(entry, name)
        except OSError:
            continue
        if isinstance(path, str) and path:
            yield Path(path)
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if path := os.environ.get(variable):
            yield Path(path) / "Steam"


def _find_game(roots, *, shipping: Path, manifest: Path) -> Path | None:
    libraries = list(dict.fromkeys(roots))
    for steam in tuple(libraries):
        try:
            with (steam / "steamapps/libraryfolders.vdf").open(encoding="utf-8-sig") as stream:
                content = stream.read(1024 * 1024)
        except (OSError, UnicodeError):
            continue
        libraries.extend(Path(path.replace("\\\\", "\\")) for path in re.findall(r'^\s*"path"\s+"([^"\r\n]+)"', content, re.M))
    for library in dict.fromkeys(libraries):
        game = library / "steamapps/common/Project Silverfish"
        if (game / shipping).is_file() and (game / manifest).is_file():
            return game
    return None


def _install_bundle(root: Path, manifest: dict, args) -> dict:
    from bootstrap import install_stage, install_stock
    from tools.silverfish import runtime

    game, work, ue4ss, signatures = map(install_stock._absolute, (args.game, args.work, args.ue4ss, args.signatures))
    if (game == root or game.is_relative_to(root) or root.is_relative_to(game)
            or work.is_relative_to(root) or game.is_relative_to(work) or work.is_relative_to(game)):
        raise ValueError("Use distinct paths for Steam, the extracted release, and the co-op workspace")
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise ValueError("The local Windows profile directory is unavailable")
    solo = install_stock._absolute(Path(local)) / "SilverFish"
    if work.is_relative_to(solo) or solo.is_relative_to(work):
        raise ValueError("The co-op workspace must be separate from single-player saves")
    if "%" in args.bind or not 1 <= args.port <= 65535 or not NAME.fullmatch(args.host_campaign) or not NAME.fullmatch(args.join_profile):
        raise ValueError("Choose a numeric listening IP, UDP port, and valid default profile names")
    host = {"campaign": args.host_campaign, "bind": str(ip_address(args.bind)), "port": args.port}
    join = {"profile": args.join_profile, "invite": None, "character": None}

    # Validate the loader and stock bootstrap before a private stage copy.
    install_stock._ordinary(game, directory=True)
    for relative, digest in install_stock.UE4SS_OFFICIAL.items():
        install_stock._pinned(ue4ss / relative, digest)
    install_stock._pinned(signatures / install_stock.SIGNATURE.name, install_stock.SIGNATURE_SHA256)
    install_stock._bootstrap_payload(root / STOCK_BOOTSTRAP)
    install_stock._ordinary(work, directory=True, missing=True)
    stage = work / "stage"
    present = install_stock._ordinary(stage, directory=True, missing=True)
    if present:
        installed = stage / runtime.SHIPPING.parent / runtime.CPP_ADDON_DLL
        install_stock._pinned(installed, manifest["files"][GAMEPLAY_ADDON]["sha256"])
        install_stock._pinned(stage / runtime.SHIPPING.parent / runtime.CPP_RUNTIME_DLL,
                              install_stock.UE4SS_OFFICIAL[runtime.CPP_RUNTIME_DLL])
        install_stock._verify_stage(game, work)
    options = {} if present else {"game": game, "ue4ss": ue4ss, "signatures": signatures, "cpp_addon": root / GAMEPLAY_ADDON}
    staged = install_stage.install(work, root / RUNTIME, root / EMBEDDED_ADDON, **options)
    stock = install_stock.install(game, work, ue4ss, signatures, root / STOCK_BOOTSTRAP, host=host, join=join)
    return {"schema": 1, "release": manifest["version"], "stage": staged, "stock": stock}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("game", "work", "ue4ss", "signatures"):
        parser.add_argument("--" + argument, type=Path)
    parser.add_argument("--host-campaign", default="coop")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--join-profile", default="guest")
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        if (os.name != "nt" or Path(sys.executable).resolve() != root / PYTHON
                or not sys.flags.isolated or not sys.dont_write_bytecode):
            raise ValueError("Run Setup.ps1 from the extracted Windows bundle")
        manifest = verify_release(root)
        sys.path.insert(0, str(root))
        from tools.silverfish import runtime

        if args.game is None:
            args.game = _find_game(_steam_roots(), shipping=runtime.SHIPPING, manifest=runtime.MANIFEST)
            if args.game is None:
                print("In Steam, open Project Silverfish > Manage > Browse local files.", file=sys.stderr)
                folder = input("Paste that folder path and press Enter: ").strip().strip('"')
                if not folder:
                    raise ValueError("Choose the installed Project Silverfish folder")
                args.game = Path(folder)
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise ValueError("The local Windows profile directory is unavailable")
        args.work = args.work or Path(local) / "SilverfishCoop" / "Trials" / manifest["version"]
        args.ue4ss = args.ue4ss or root / LOADER
        args.signatures = args.signatures or root / SIGNATURES
        print(f"Preparing Project Silverfish Co-op in {args.work}. The first install copies about 5.2 GB.",
              file=sys.stderr, flush=True)
        result = _install_bundle(root, manifest, args)
    except (OSError, ValueError, RuntimeError, BadZipFile, EOFError) as error:
        parser.exit(1, f"Co-op setup failed: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
