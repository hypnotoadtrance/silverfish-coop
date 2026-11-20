"""Install a matched embedded co-op runtime in a private Windows game stage."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from tools.silverfish import runtime


MOD = "SilverfishCoopEmbeddedRuntime"
MANIFEST = "runtime-manifest.json"
WORKER = Path("app/tools/silverfish")
MODULE_PATHS = ["python314.zip", ".", "site-packages", "app"]
STARTUP_FILES = {
    "python314.dll", "python314.zip", "python314._pth", "app/tools/__init__.py",
    "app/tools/silverfish/__init__.py", "app/tools/silverfish/embedded_startup.py",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
COMPONENT = re.compile(r"[A-Za-z0-9_.-]+")
PAYLOAD_LIMIT = 60 * 1024 * 1024
ENTRY_LIMIT = 4096


def _ordinary(path: Path, directory: bool = False) -> os.stat_result:
    status = path.lstat()
    type_matches = stat.S_ISDIR(status.st_mode) if directory else stat.S_ISREG(status.st_mode)
    if (not type_matches or getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or path.resolve(strict=True) != path):
        raise ValueError(f"Expected an ordinary {'directory' if directory else 'file'} at {path}")
    return status


def _manifest(source: Path, addon: Path) -> tuple[str, str, dict]:
    _ordinary(source, directory=True)
    path = source / MANIFEST
    _ordinary(path)
    with path.open("rb") as stream:
        raw = stream.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise ValueError("Embedded runtime manifest exceeds its size limit")
    package = json.loads(raw)
    if (not isinstance(package, dict) or type(package.get("schema")) is not int or package["schema"] != 1
            or package.get("platform") != "win-amd64" or package.get("python_library") != "python314.dll"
            or not re.fullmatch(r"3\.14\.[0-9]+", str(package.get("python_version")))
            or package.get("module_paths") != MODULE_PATHS):
        raise ValueError("Embedded runtime manifest is incompatible")
    files, size = package.get("files"), package.get("payload_bytes")
    if (not isinstance(files, dict) or len(files) > ENTRY_LIMIT or not STARTUP_FILES.issubset(files)
            or type(size) is not int or not 0 < size <= PAYLOAD_LIMIT):
        raise ValueError("Embedded runtime manifest has invalid file or byte counts")
    _ordinary(addon)
    with addon.open("rb") as stream:
        image = stream.read(16 * 1024 * 1024 + 1)
    manifest_sha = hashlib.sha256(raw).hexdigest()
    if len(image) > 16 * 1024 * 1024 or not image.startswith(b"MZ") or manifest_sha.encode("ascii") not in image:
        raise ValueError("Embedded add-on does not contain the runtime manifest SHA-256 pin")
    return manifest_sha, hashlib.sha256(image).hexdigest(), package


def _verify(source: Path, addon: Path) -> dict:
    manifest_sha, addon_sha, package = _manifest(source, addon)
    for directory in ("app", "site-packages"):
        _ordinary(source / directory, directory=True)
    expected = package["files"]
    seen, entries, size = set(), 0, 0
    pending = [source]
    while pending:
        for path in pending.pop().iterdir():
            entries += 1
            if entries > ENTRY_LIMIT or not COMPONENT.fullmatch(path.name) or path.name.endswith("."):
                raise ValueError(f"Invalid entry in embedded runtime: {path}")
            relative = path.relative_to(source).as_posix()
            if relative == MANIFEST:
                continue
            if stat.S_ISDIR(path.lstat().st_mode):
                _ordinary(path, directory=True)
                pending.append(path)
                continue
            size += _ordinary(path).st_size
            digest = expected.get(relative)
            if (size > package["payload_bytes"] or not isinstance(digest, str) or not SHA256.fullmatch(digest)
                    or runtime._file_sha256(path) != digest):
                raise ValueError(f"Embedded runtime file differs from its manifest: {relative}")
            seen.add(relative)
    if seen != expected.keys() or size != package["payload_bytes"]:
        raise ValueError("Embedded runtime is missing manifest files or bytes")
    if (source / "python314._pth").read_bytes() != ("\n".join(MODULE_PATHS) + "\n").encode("ascii"):
        raise ValueError("Embedded Python module paths differ from the expected isolated paths")
    if runtime._code_fingerprint(source / WORKER, ".py") != runtime._code_fingerprint(runtime.REPO / "tools/silverfish", ".py"):
        raise ValueError("Embedded runtime sources differ from this installer checkout")
    return {"manifest_sha256": manifest_sha, "addon_sha256": addon_sha, "payload_bytes": size}


def _activate(work: Path, source: Path, addon: Path, verified: dict) -> dict:
    stage = work / "stage"
    _ordinary(stage, directory=True)
    _ordinary(stage / "silverfish-coop-stage.json")
    build = runtime.staged_build(work)
    if runtime.native_death_backend(work) != "cpp":
        raise ValueError("Embedded co-op requires a staged native gameplay add-on")
    mods = stage / runtime.SHIPPING.parent / "ue4ss/Mods"
    _ordinary(mods, directory=True)
    if runtime._code_fingerprint(mods / "SilverfishCoop/Scripts", ".lua") != runtime._code_fingerprint(
            runtime.REPO / "mod/SilverfishCoop/Scripts", ".lua"):
        raise ValueError("Staged co-op scripts differ from this installer checkout")
    activation = mods / "mods.txt"
    _ordinary(activation)
    original = activation.read_text(encoding="utf-8")
    names = (line.partition(":") for line in original.removeprefix("\ufeff").splitlines())
    settings = [(name.strip(), value.strip()) for name, _, value in names if name.strip().casefold() == MOD.casefold()]
    if settings and settings != [(MOD, "1")]:
        raise ValueError(f"Conflicting {MOD} entry in {activation}")
    installed = mods / MOD
    if settings or os.path.lexists(installed):
        _ordinary(installed, directory=True)
        if _verify(installed / "runtime", installed / "dlls/main.dll") != verified:
            raise ValueError(f"A different embedded runtime is already present at {installed}")
    else:
        with tempfile.TemporaryDirectory(prefix=".coop-runtime-", dir=mods) as temporary:
            candidate = Path(temporary) / MOD
            (candidate / "dlls").mkdir(parents=True)
            shutil.copy2(addon, candidate / "dlls/main.dll")
            shutil.copytree(source, candidate / "runtime", symlinks=True)
            if _verify(candidate / "runtime", candidate / "dlls/main.dll") != verified:
                raise ValueError("Copied embedded runtime differs from the supplied package")
            candidate.rename(installed)
    if not settings:
        if activation.read_text(encoding="utf-8") != original:
            raise ValueError(f"{activation} changed during installation. Rerun to activate the verified runtime.")
        separator = "\n" if original and not original.endswith("\n") else ""
        runtime.write_text(activation, original + separator + f"{MOD} : 1\n")
    return {"stage": str(stage), "build": build, "runtime": verified, "activated": not settings}


def install(work: Path, source: Path, addon: Path, *, game: Path | None = None,
            ue4ss: Path | None = None, signatures: Path | None = None, cpp_addon: Path | None = None,
            cpp_runtime: Path | None = None) -> dict:
    """Activate the matched package in an existing stage or atomically publish a new one."""
    work = work.resolve()
    source, addon = Path(os.path.abspath(source)), Path(os.path.abspath(addon))
    verified = _verify(source, addon)
    if game is None:
        if any(value is not None for value in (ue4ss, signatures, cpp_addon, cpp_runtime)):
            raise ValueError("Stage creation options require --game")
        return _activate(work, source, addon, verified)
    if ue4ss is None or signatures is None or cpp_addon is None:
        raise ValueError("A new stage requires --ue4ss, --signatures, and --cpp-addon")
    game = game.resolve(strict=True)
    stage = work / "stage"
    if work.is_relative_to(game) or game.is_relative_to(stage):
        raise ValueError("The co-op workspace and original installation must be separate")
    if os.path.lexists(stage):
        raise ValueError(f"Stage already exists at {stage}. Omit --game to install in that stage.")
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".coop-stage-", dir=work) as temporary:
        building = Path(temporary)
        runtime.prepare_game(building, game, ue4ss, signatures,
                             cpp_runtime=cpp_runtime or ue4ss / "ue4ss/UE4SS.dll", cpp_addon=cpp_addon)
        result = _activate(building, source, addon, verified)
        (building / "stage").rename(stage)
    result["stage"] = str(stage)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="private workspace for the stage and co-op saves")
    parser.add_argument("--embedded-addon", type=Path, required=True, help="embedded runtime main.dll")
    parser.add_argument("--embedded-runtime", type=Path, required=True, help="matching unpacked runtime payload")
    parser.add_argument("--game", type=Path, help="stock Steam game to copy when creating a stage")
    parser.add_argument("--ue4ss", type=Path, help="official UE4SS release root for a new stage")
    parser.add_argument("--signatures", type=Path, help="Project Silverfish UE4SS_Signatures folder")
    parser.add_argument("--cpp-addon", type=Path, help="native gameplay add-on for a new stage")
    parser.add_argument("--cpp-runtime", type=Path, help="override the UE4SS DLL paired with the gameplay add-on")
    args = parser.parse_args()
    try:
        result = install(args.work, args.embedded_runtime, args.embedded_addon, game=args.game,
                         ue4ss=args.ue4ss, signatures=args.signatures, cpp_addon=args.cpp_addon,
                         cpp_runtime=args.cpp_runtime)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Stage installation failed: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
