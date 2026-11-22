"""Assemble the historical staged co-op ZIP and verify shared release inputs."""

import argparse
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
from subprocess import CalledProcessError
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from . import install_stage, install_stock, release_setup as layout
from .embedded_runtime_build import git, load_lock


REPO = Path(__file__).resolve().parents[1]
PYINSTALLER_VERSION = "6.22.3"
SOURCE_LIMIT = 2 * 1024**2
INSTALL = br"""@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup.ps1" %*
set "result=%errorlevel%"
pause
exit /b %result%
""".replace(b"\n", b"\r\n")
START_HERE = br"""Project Silverfish Co-op | Windows Direct IP trial

1. Both players close Project Silverfish. Extract this ZIP and run Install.cmd
   once on each PC. The first install needs about 5.2 GB for the co-op profile.
2. Launch through Steam. Press F7 for Open Co-op on the game title screen.
3. The host presses F7 for Host. Keep the defaults or choose a UDP port.
   In the host's campaign, open the Journal (J by default) and press F7 to
   create a private invitation. Enter the address the guest can reach.
4. On the other PC, choose Join (F8), paste the invitation, and pick a
   character. Share the invitation only with the person you want to join.

Both PCs need the same Steam game build and co-op ZIP. Use the host's LAN
address on the same network. Across networks use a reachable host address
and allow or forward the host's chosen UDP port (default 7777) if needed.
If the connection briefly drops, leave both games running to let it recover.

This is an in-development build. Keep a backup of co-op campaigns you want
to retain. Co-op data lives under %LOCALAPPDATA%\SilverfishCoop\Trials.
The title menu opens the private co-op profile automatically.

Includes the matched UE4SS v4.0.0 loader and Project Silverfish signature.
https://github.com/UE4SS-RE/RE-UE4SS/releases/tag/v4.0.0
"""
SETUP = b"""[CmdletBinding()]
param(
    [string]$Game,
    [string]$Work,
    [string]$Ue4ss,
    [string]$Signatures,
    [string]$HostCampaign = 'coop',
    [string]$Bind = '0.0.0.0',
    [ValidateRange(1,65535)][int]$Port = 7777,
    [string]$JoinProfile = 'guest'
)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name 'SilverFish-Win64-Shipping' -ErrorAction SilentlyContinue) {
    Write-Host 'Close Project Silverfish, then run Install.cmd again.'
    exit 1
}
$python = Join-Path $PSScriptRoot 'payload/runtime/python.exe'
$setup = Join-Path $PSScriptRoot 'bootstrap/release_setup.py'
$setupArguments = @(
    '--host-campaign', $HostCampaign, '--bind', $Bind, '--port', $Port, '--join-profile', $JoinProfile
)
if ($Game) { $setupArguments += @('--game', $Game) }
if ($Work) { $setupArguments += @('--work', $Work) }
if ($Ue4ss) { $setupArguments += @('--ue4ss', $Ue4ss) }
if ($Signatures) { $setupArguments += @('--signatures', $Signatures) }
& $python -I -B $setup @setupArguments
$result = $LASTEXITCODE
if ($result -eq 0) {
    Write-Host 'Installed. Launch Project Silverfish through Steam and press F7 on the title screen.'
}
exit $result
"""


def _read(path: Path, maximum: int) -> bytes:
    layout._ordinary(path, directory=False)
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if not payload or len(payload) > maximum:
        raise ValueError(f"Invalid input length for {path.name}")
    return payload


def _sources(repo: Path, revision: str, *, fixed_sources: frozenset[str] = layout.FIXED_SOURCES) -> dict[str, bytes]:
    if not layout.REVISION.fullmatch(revision) or git(repo, "cat-file", "-t", revision).strip() != b"commit":
        raise ValueError("Supply the full Git commit for the release sources")
    tree = git(repo, "ls-tree", "--full-tree", "-r", "-z", revision, "--", "bootstrap", "tools", "mod/SilverfishCoop")
    sources = {}
    for row in tree.rstrip(b"\0").split(b"\0"):
        if not row:
            continue
        metadata, name_bytes = row.split(b"\t", 1)
        name = name_bytes.decode("utf-8")
        if not layout.permitted_source(name, fixed_sources):
            continue
        layout.release_name(name)
        mode, kind, object_id = metadata.decode("ascii").split()
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError(f"Release source {name} is not a Git file")
        contents = git(repo, "cat-file", "blob", object_id)
        if len(contents) > SOURCE_LIMIT:
            raise ValueError(f"Release source exceeds its size limit at {name}")
        sources[name] = contents
    if not fixed_sources.issubset(sources):
        raise ValueError("Release commit is missing required setup or mod sources")
    return sources


def _pe(data: bytes, *, dll: bool, name: str) -> None:
    offset = int.from_bytes(data[60:64], "little")
    if (not data.startswith(b"MZ") or not 64 <= offset <= len(data) - 26 or data[offset:offset + 6] != b"PE\0\0\x64\x86"
            or bool(data[offset + 23] & 0x20) != dll or data[offset + 24:offset + 26] != b"\x0b\x02"):
        raise ValueError(f"Expected a Windows x64 {'DLL' if dll else 'executable'} for {name}")


def _normalized(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _app_sources(sources: dict[str, bytes]) -> dict[str, bytes]:
    return {path: data for path, data in sources.items() if path.startswith("tools/silverfish/")}


def _check_worker_sources(reader, sources: dict[str, bytes]) -> None:
    entries = {}
    for member, record in reader.toc.items():
        name = member.replace("\\", "/")
        if not name.startswith("tools/silverfish/") or not name.endswith(".py"):
            continue
        if name in entries or record[2] > SOURCE_LIMIT:
            raise ValueError("Coordinator has conflicting or oversized Python sources")
        entries[name] = member
    expected = _app_sources(sources)
    if entries.keys() != expected.keys():
        raise ValueError("Coordinator Python source list differs from the release revision")
    if any(_normalized(reader.extract(member)) != _normalized(expected[path]) for path, member in entries.items()):
        raise ValueError("Coordinator Python source bytes differ from the release revision")


def _check_coordinator(worker: bytes, sources: dict[str, bytes], temporary: Path) -> None:
    try:
        import PyInstaller
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as error:
        raise RuntimeError(f"Release assembly requires PyInstaller {PYINSTALLER_VERSION}") from error
    if PyInstaller.__version__ != PYINSTALLER_VERSION:
        raise RuntimeError(f"Release assembly requires PyInstaller {PYINSTALLER_VERSION}")
    worker_file = temporary / "coordinator.exe"
    worker_file.write_bytes(worker)
    _check_worker_sources(CArchiveReader(worker_file), sources)


def _stock_bootstrap(path: Path, sources: dict[str, bytes], temporary: Path) -> bytes:
    outer = _read(path, layout.FILE_LIMIT)
    archive = temporary / "stock-bootstrap.zip"
    archive.write_bytes(outer)
    bundle = install_stock._bootstrap_payload(archive)
    for relative, local_source in install_stock.BOOTSTRAP_SCRIPTS.items():
        path = local_source.relative_to(REPO).as_posix()
        if _normalized(bundle[relative]) != _normalized(sources[path]):
            raise ValueError(f"Stock bootstrap differs from the release at {path}")
    bridge, worker = bundle[install_stock.TITLE_DLL], bundle[install_stock.WORKER]
    _pe(bridge, dll=True, name="stock bootstrap")
    _pe(worker, dll=False, name="coordinator")
    _check_coordinator(worker, sources, temporary)
    return outer


def _runtime(source: Path, addon: Path, sources: dict[str, bytes]) -> tuple[dict[str, bytes], bytes]:
    manifest_hash, addon_hash, package = install_stage._manifest(source, addon)
    lock = load_lock()
    locked_wheels = {row["name"]: (row["version"], row["source_sha256"]) for row in lock["wheels"]}
    wheels = package.get("wheels")
    if (package.get("python_lock") != lock["python"] or package["python_version"] != lock["python"]["version"]
            or not isinstance(wheels, list) or len(wheels) != len(locked_wheels)
            or any(not isinstance(row, dict) or not isinstance(row.get("name"), str)
                   or locked_wheels.get(row.get("name")) != (row.get("version"), row.get("source_sha256"))
                   for row in wheels)
            or {row["name"] for row in wheels} != locked_wheels.keys()):
        raise ValueError("Embedded runtime does not match the versioned dependency lock")
    manifest_files = package["files"]
    size, files = 0, {}
    for relative, path, length in layout.members(source):
        bound = 512 * 1024 if relative == install_stage.MANIFEST else install_stage.PAYLOAD_LIMIT
        if not layout.permitted_member(f"{layout.RUNTIME}/{relative}"):
            raise ValueError(f"Unexpected embedded runtime payload {relative}")
        if relative != install_stage.MANIFEST:
            if relative not in manifest_files or size + length > package["payload_bytes"]:
                raise ValueError(f"Unexpected embedded runtime payload {relative}")
            size += length
        with path.open("rb") as stream:
            contents = stream.read(min(length, bound) + 1)
        digest = manifest_hash if relative == install_stage.MANIFEST else manifest_files[relative]
        if len(contents) != length or length > bound or sha256(contents).hexdigest() != digest:
            raise ValueError(f"Embedded runtime bytes differ at {relative}")
        files[relative] = contents
    if size != package["payload_bytes"] or set(files) != manifest_files.keys() | {install_stage.MANIFEST}:
        raise ValueError("Embedded runtime has missing manifest bytes")
    if files["python314._pth"] != ("\n".join(install_stage.MODULE_PATHS) + "\n").encode("ascii"):
        raise ValueError("Embedded runtime has different isolated Python module paths")
    app = {name.removeprefix("app/"): data for name, data in files.items() if name.startswith("app/")}
    expected = _app_sources(sources) | {"tools/__init__.py": sources["tools/__init__.py"]}
    if app.keys() != expected.keys() or any(_normalized(data) != _normalized(expected[path]) for path, data in app.items()):
        raise ValueError("Embedded runtime Python sources differ from the release revision")
    _pe(files.get("python.exe", b""), dll=False, name="embedded Python")
    image = _read(addon, 16 * 1024**2)
    if sha256(image).hexdigest() != addon_hash:
        raise ValueError("Embedded add-on changed during assembly")
    _pe(image, dll=True, name="embedded add-on")
    return {f"{layout.RUNTIME}/{name}": data for name, data in files.items()}, image


def _loader(ue4ss: Path, signatures: Path) -> dict[str, bytes]:
    files = {f"{layout.LOADER}/{relative.as_posix()}": install_stock._pinned(ue4ss / relative, digest)
             for relative, digest in install_stock.UE4SS_OFFICIAL.items()}
    license_name = "ue4ss/LICENSE"
    files[f"{layout.LOADER}/{license_name}"] = _read(ue4ss / license_name, 32 * 1024)
    signature = install_stock.SIGNATURE.name
    files[f"{layout.SIGNATURES}/{signature}"] = install_stock._pinned(
        signatures / signature, install_stock.SIGNATURE_SHA256,
    )
    return files


def _write_archive(output: Path, version: str, revision: str, files: dict[str, bytes]) -> None:
    records, names, total = {}, set(), 0
    for name, data in sorted(files.items()):
        relative = layout.release_name(name)
        if not layout.permitted_member(name):
            raise ValueError(f"Unexpected release member {name}")
        if len(data) > layout.FILE_LIMIT:
            raise ValueError(f"Release file exceeds its size limit at {name}")
        for component in (relative, *relative.parents[:-1]):
            names.add(component.as_posix())
        records[name] = {"bytes": len(data), "sha256": sha256(data).hexdigest()}
        total += len(data)
    if (len(names) >= layout.ENTRY_LIMIT or len({name.casefold() for name in names}) != len(names)
            or total > layout.TOTAL_LIMIT or not layout.SETUP_FILES.issubset(records)):
        raise ValueError("Release archive has conflicting, oversized or missing contents")
    descriptor = json.dumps({"schema": 1, "version": version, "revision": revision, "files": records},
                            ensure_ascii=True, indent=2).encode("ascii") + b"\n"
    if len(descriptor) > layout.MANIFEST_LIMIT:
        raise ValueError("Release manifest exceeds its size limit")
    with ZipFile(output, "x") as archive:
        for path, data in sorted({**files, layout.MANIFEST: descriptor}.items()):
            member = ZipInfo(path)
            member.compress_type = ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            archive.writestr(member, data)


def assemble(output: Path, *, version: str, revision: str, bootstrap: Path, cpp_addon: Path,
             embedded_addon: Path, embedded_runtime: Path, ue4ss: Path, signatures: Path,
             cpp_addon_sha256: str | None = None) -> dict:
    """Create a new, verified release archive for the selected Git revision."""
    if not layout.VERSION.fullmatch(version) or version.endswith("."):
        raise ValueError("Supply a version made from letters, numbers, dots, underscores, or hyphens")
    output = Path(os.path.abspath(output))
    if output.suffix.lower() != ".zip" or os.path.lexists(output):
        raise ValueError("Output must name a new ZIP")
    layout._ordinary(output.parent, directory=True)
    bootstrap, cpp_addon, embedded_addon, embedded_runtime = (
        Path(os.path.abspath(path)) for path in (bootstrap, cpp_addon, embedded_addon, embedded_runtime)
    )
    sources = _sources(REPO, revision)
    runtime, addon = _runtime(embedded_runtime, embedded_addon, sources)
    gameplay = _read(cpp_addon, layout.FILE_LIMIT)
    _pe(gameplay, dll=True, name="gameplay add-on")
    if cpp_addon_sha256 is not None and (not layout.SHA256.fullmatch(cpp_addon_sha256)
                                        or sha256(gameplay).hexdigest() != cpp_addon_sha256):
        raise ValueError("Gameplay add-on differs from the supplied build checksum")
    with TemporaryDirectory(prefix=".coop-release-", dir=output.parent) as temporary:
        folder = Path(temporary)
        outer = _stock_bootstrap(bootstrap, sources, folder)
        files = {**sources, **runtime, **_loader(ue4ss.absolute(), signatures.absolute()),
                 "Install.cmd": INSTALL, "Setup.ps1": SETUP, "START-HERE.txt": START_HERE,
                 layout.STOCK_BOOTSTRAP: outer, layout.GAMEPLAY_ADDON: gameplay, layout.EMBEDDED_ADDON: addon}
        archive = folder / "release.zip"
        _write_archive(archive, version, revision, files)
        with archive.open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()
        if os.name == "nt":
            archive.rename(output)
        else:
            os.link(archive, output)
    return {"release": version, "revision": revision, "file": str(output), "sha256": digest, "entries": len(files) + 1}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("output", "bootstrap", "cpp-addon", "embedded-addon", "embedded-runtime", "ue4ss", "signatures"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--cpp-addon-sha256")
    try:
        result = assemble(**vars(parser.parse_args()))
    except (OSError, ValueError, RuntimeError, BadZipFile, CalledProcessError) as error:
        parser.exit(1, f"Co-op release assembly failed: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
