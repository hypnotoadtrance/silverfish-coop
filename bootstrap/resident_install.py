"""Install the native resident co-op title into the existing Windows game."""

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from hashlib import sha256
from ipaddress import ip_address
import json
import os
from pathlib import Path

from tools.silverfish import runtime

from . import install_stage, install_stock, release_setup, resident_boot_config
from .release_bundle import _pe


_GAME_BUILD = "8682dc057e1b38738862760a8c3b848a837c81d72c0e6ddffa3b7f963960374d"
_CONTENT_MANIFEST = Path("silverfish-coop-stage.json")
_MODS = runtime.SHIPPING.parent / "ue4ss" / "Mods"
_ACTIVATION = _MODS / "mods.txt"
_SETTINGS = runtime.SHIPPING.parent / install_stock.SETTINGS
_EMBEDDED = _MODS / install_stage.MOD
_DESCRIPTOR = _EMBEDDED / resident_boot_config.DESCRIPTOR
_MODULES = (runtime.CPP_MOD_NAME, "SilverfishCoop", install_stage.MOD)


@contextmanager
def _install_lock(game: Path):
    """Serialize two setup processes targeting the same Windows installation."""
    api = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
    create, wait, release, close = api.CreateMutexW, api.WaitForSingleObject, api.ReleaseMutex, api.CloseHandle
    create.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    wait.argtypes, wait.restype = (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD
    release.argtypes = close.argtypes = (wintypes.HANDLE,)
    release.restype = close.restype = wintypes.BOOL
    identity = sha256(str(game).casefold().encode("utf-8")).hexdigest()
    handle = create(None, False, f"Local\\SilverfishCoop-Install-{identity}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = wait(handle, 0)
        if result == 0x102:  # WAIT_TIMEOUT
            raise RuntimeError("Another co-op setup is using this game installation")
        if result not in (0, 0x80):  # WAIT_OBJECT_0 or WAIT_ABANDONED
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            release(handle)
    finally:
        close(handle)


def _game_build(game: Path) -> str:
    install_stock._ordinary(game / "SilverFish.exe")
    digest = sha256()
    for relative in (runtime.SHIPPING, *(runtime.PAKS / name for name in runtime.STOCK_PACKAGES)):
        stock = game / relative
        install_stock._ordinary(stock)
        digest.update(bytes.fromhex(runtime._file_sha256(stock)))
    if digest.hexdigest() != _GAME_BUILD:
        raise ValueError("The installed Project Silverfish build does not match this co-op package")
    return digest.hexdigest()


def _activation(raw: bytes | None) -> bytes:
    raw = raw or b""
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    text = raw[len(bom):].decode("utf-8")
    if "\r" in text.replace("\r\n", "") or "\x00" in text:
        raise ValueError("The UE4SS Mods list has an unsupported line format")
    lines = text.splitlines(keepends=True)
    managed = {name.casefold(): (name, "1") for name in _MODULES}
    legacy = install_stock.MOD
    managed[legacy.casefold()] = (legacy, "0")
    seen = set()
    for index, line in enumerate(lines):
        if ";" in line:
            continue
        name, separator, setting = line.strip().partition(":")
        name, setting = name.strip(), setting.strip()
        key = name.casefold()
        if key not in managed:
            continue
        exact, desired = managed[key]
        if key in seen or name != exact or not separator or setting not in ("0", "1"):
            raise ValueError(f"The UE4SS Mods list has a conflicting {exact} row")
        seen.add(key)
        if "\t" in line:
            ending = line[len(line.rstrip("\r\n")):]
            lines[index] = f"{name} : {desired}{ending}"
        elif setting != desired:
            value_at = line.index(":") + 1
            digit_at = value_at + len(line[value_at:]) - len(line[value_at:].lstrip())
            lines[index] = line[:digit_at] + desired + line[digit_at + 1:]
    missing = [name for name in _MODULES if name.casefold() not in seen]
    if missing:
        newline = "\r\n" if "\r\n" in text else "\n"
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += newline
        lines.extend(f"{name} : 1{newline}" for name in missing)
    return bom + "".join(lines).encode("utf-8")


def _payload(ue4ss: Path, signatures: Path, cpp_addon: Path, embedded_addon: Path,
             embedded_runtime: Path) -> dict[Path, bytes]:
    binaries = runtime.SHIPPING.parent
    files = {binaries / relative: install_stock._pinned(ue4ss / relative, digest)
             for relative, digest in install_stock.UE4SS_OFFICIAL.items()}
    install_stock._compatible_settings(files[_SETTINGS], fresh=True)
    signature = install_stock.SIGNATURE
    files[binaries / "ue4ss" / signature] = install_stock._pinned(
        signatures / signature.name, install_stock.SIGNATURE_SHA256)

    gameplay = install_stock._read(cpp_addon, install_stock.ARCHIVE_LIMIT)
    _pe(gameplay, dll=True, name="gameplay add-on")
    files[binaries / runtime.CPP_ADDON_DLL] = gameplay
    scripts = runtime.REPO / "mod" / "SilverfishCoop" / "Scripts"
    install_stock._ordinary(scripts, directory=True)
    for relative in sorted(install_stock._files(scripts)):
        if relative.suffix != ".lua":
            raise ValueError(f"Unexpected gameplay script payload {relative}")
        files[binaries / runtime.MOD_SCRIPTS / relative] = install_stock._read(scripts / relative, 2 * 1024**2)
    if binaries / runtime.MOD_SCRIPTS / "main.lua" not in files:
        raise ValueError("The gameplay scripts are missing their entry point")

    verified = install_stage._verify(embedded_runtime, embedded_addon)
    addon = install_stock._pinned(embedded_addon, verified["addon_sha256"])
    _pe(addon, dll=True, name="embedded add-on")
    files[_EMBEDDED / "dlls" / "main.dll"] = addon
    manifest = install_stock._pinned(embedded_runtime / install_stage.MANIFEST, verified["manifest_sha256"])
    files[_EMBEDDED / "runtime" / install_stage.MANIFEST] = manifest
    for name, digest in sorted(json.loads(manifest)["files"].items()):
        relative = Path(name)
        files[_EMBEDDED / "runtime" / relative] = install_stock._pinned(embedded_runtime / relative, digest)
    return files


def _preflight(game: Path, files: dict[Path, bytes], build: str) -> dict[Path, bytes]:
    scripts = runtime.SHIPPING.parent / runtime.MOD_SCRIPTS
    for directory in (scripts, _EMBEDDED / "runtime"):
        if not install_stock._ordinary(game / directory, directory=True, missing=True):
            continue
        expected = {relative.relative_to(directory) for relative in files if relative.is_relative_to(directory)}
        present = install_stock._files(game / directory)
        if directory == scripts:
            present = {relative for relative in present if relative.suffix.casefold() == ".lua"}
        if present - expected:
            raise ValueError(f"A different set of managed files is already present at {directory}")
    missing = {}
    for relative, expected in files.items():
        present = install_stock._read(game / relative, install_stock.ARCHIVE_LIMIT, missing=True)
        if present is None:
            missing[relative] = expected
        elif relative == _SETTINGS:
            install_stock._compatible_settings(present)
        elif relative.is_relative_to(scripts):
            if present.replace(b"\r\n", b"\n") != expected.replace(b"\r\n", b"\n"):
                raise ValueError(f"A different gameplay script is already present at {relative}")
        elif present != expected:
            raise ValueError(f"A different installation file is already present at {relative}")

    binaries = runtime.SHIPPING.parent
    metadata = {
        "schema": 1, "game_build": build, "stock_files": 1 + len(runtime.STOCK_PACKAGES),
        "cpp_death_lab": {
            "runtime": sha256(files[binaries / runtime.CPP_RUNTIME_DLL]).hexdigest(),
            "addon": sha256(files[binaries / runtime.CPP_ADDON_DLL]).hexdigest(),
        },
    }
    if (marker := install_stock._read(game / _CONTENT_MANIFEST, 8192, missing=True)) is None:
        missing[_CONTENT_MANIFEST] = (json.dumps(metadata) + "\n").encode("ascii")
    else:
        current = json.loads(marker)
        if (type(current) is not dict or type(current.get("schema")) is not int or any(
                current.get(key) != metadata[key] for key in ("schema", "game_build", "cpp_death_lab"))):
            raise ValueError("A different co-op content manifest is already present")
    return missing


def install(game: Path, work: Path, ue4ss: Path, signatures: Path, *, cpp_addon: Path,
            embedded_addon: Path, embedded_runtime: Path, seat: str = "local", campaign: str = "default",
            bind: str = "0.0.0.0", port: int = 7777, isolated: bool = False) -> dict:
    """Install checked co-op components in place without copying stock assets."""
    game, work, ue4ss, signatures, cpp_addon, embedded_addon, embedded_runtime = map(
        install_stock._absolute, (game, work, ue4ss, signatures, cpp_addon, embedded_addon, embedded_runtime))
    for directory in (game, ue4ss, signatures):
        install_stock._ordinary(directory, directory=True)
    install_stock._ordinary(work, directory=True, missing=True)
    if game.is_relative_to(work) or work.is_relative_to(game):
        raise ValueError("The installed game and co-op workspace must be separate")
    if local := os.environ.get("LOCALAPPDATA"):
        solo = install_stock._absolute(Path(local)) / "SilverFish"
        if work.is_relative_to(solo) or solo.is_relative_to(work):
            raise ValueError("The co-op workspace must be separate from single-player saves")
    if not resident_boot_config._name(seat) or not resident_boot_config._name(campaign):
        raise ValueError("Use a valid local seat and campaign name")
    if type(port) is not int or not 1 <= port <= 65535 or type(isolated) is not bool:
        raise ValueError("Use a UDP port from 1 through 65535 and a valid isolation option")
    if not isinstance(bind, str) or "%" in bind:
        raise ValueError("Use a numeric host IP address")
    bind = str(ip_address(bind))

    build = _game_build(game)
    files = _payload(ue4ss, signatures, cpp_addon, embedded_addon, embedded_runtime)
    with _install_lock(game):
        activation = game / _ACTIVATION
        original = install_stock._read(activation, 1024 * 1024, missing=True)
        desired = _activation(original)
        if install_stock._ordinary(game / _MODS / install_stock.MOD / "enabled.txt", missing=True):
            raise ValueError("Remove the legacy co-op bootstrap's enabled.txt before enabling the resident title")
        descriptor = game / _DESCRIPTOR
        existing = install_stock._read(descriptor, resident_boot_config.DESCRIPTOR_LIMIT, missing=True)
        if existing is not None:
            previous = json.loads(existing)
            plan = {"work": str(work), "seat": seat, "campaign": campaign, "bind": bind,
                    "port": port, "isolated": isolated}
            if type(previous) is not dict or any(previous.get(key) != value for key, value in plan.items()):
                raise ValueError("A different resident startup configuration already exists")
        missing = _preflight(game, files, build)
        for relative, data in missing.items():
            install_stock._atomic(game / relative, data)
        work.mkdir(parents=True, exist_ok=True)
        install_stock._ordinary(work, directory=True)
        if original != desired:
            install_stock._atomic(activation, desired, previous=original)
        try:
            resident_boot_config.configure(game, work, seat=seat, campaign=campaign, bind=bind,
                                           port=port, isolated=isolated)
        except (OSError, ValueError, RuntimeError):
            if original != desired and install_stock._read(activation, 1024 * 1024, missing=True) == desired:
                if original is None:
                    activation.unlink()
                else:
                    install_stock._atomic(activation, original, previous=desired)
            raise

    return {"schema": 1, "game": str(game), "work": str(work), "build": build, "descriptor": str(descriptor),
            "installed": sorted(relative.as_posix() for relative in missing),
            "activation_changed": original != desired, "configuration_created": existing is None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("ue4ss", "signatures", "cpp-addon", "embedded-addon", "embedded-runtime"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--game", type=Path, help="Steam game folder, detected automatically when omitted")
    parser.add_argument("--work", type=Path, help="private co-op workspace")
    parser.add_argument("--seat", default="local")
    parser.add_argument("--campaign", default="default")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--isolated", action="store_true", help="use a private headless test profile")
    args = parser.parse_args()
    try:
        if args.game is None:
            args.game = release_setup._find_game(release_setup._steam_roots(), shipping=runtime.SHIPPING,
                                                manifest=runtime.MANIFEST)
            if args.game is None:
                raise ValueError("Supply the Project Silverfish folder shown by Steam's Browse local files")
        if args.work is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise ValueError("Supply a private workspace with --work")
            args.work = Path(local) / "SilverfishCoop" / "Resident"
        result = install(**vars(args))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Resident co-op installation failed: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
