"""Build the installer-based resident ZIP used by older Windows test layouts."""

import argparse
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
from subprocess import CalledProcessError
from tempfile import TemporaryDirectory

from . import release_bundle, release_setup as layout


_RESIDENT_SOURCES = layout.GAMEPLAY_SOURCES | layout.INSTALLER_SOURCES | {
    "bootstrap/embedded_runtime_build.py",
    "bootstrap/release_bundle.py",
    "bootstrap/resident_boot_config.py",
    "bootstrap/resident_install.py",
}
_SETUP = b"""[CmdletBinding()]
param(
    [string]$Game,
    [string]$Work,
    [string]$Seat = 'local',
    [string]$Campaign = 'default',
    [string]$Bind = '0.0.0.0',
    [ValidateRange(1,65535)][int]$Port = 7777
)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name 'SilverFish', 'SilverFish-Win64-Shipping' -ErrorAction SilentlyContinue) {
    Write-Host 'Close Project Silverfish, then run Install.cmd again.'
    exit 1
}
$python = Join-Path $PSScriptRoot 'payload/runtime/python.exe'
$setup = Join-Path $PSScriptRoot 'ResidentSetup.py'
$setupArguments = @('--seat', $Seat, '--campaign', $Campaign, '--bind', $Bind, '--port', $Port)
if ($Game) { $setupArguments += @('--game', $Game) }
if ($Work) { $setupArguments += @('--work', $Work) }
& $python -I -B $setup @setupArguments
$result = $LASTEXITCODE
if ($result -eq 0) {
    Write-Host 'Installed. Launch Project Silverfish from Steam and use the Direct IP co-op controls on the title screen.'
}
exit $result
"""
_SETUP_PYTHON = b"""from pathlib import Path
import sys

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
from bootstrap.resident_install import main

sys.argv[1:1] = [
    "--ue4ss", str(root / "payload/loader"),
    "--signatures", str(root / "payload/signatures"),
    "--cpp-addon", str(root / "payload/native-gameplay.dll"),
    "--embedded-addon", str(root / "payload/embedded-addon.dll"),
    "--embedded-runtime", str(root / "payload/runtime"),
]
main()
"""
_START = br"""Project Silverfish Co-op | Windows Direct IP trial

1. Both players close Project Silverfish, extract this ZIP, and run Install.cmd.
   The setup looks for the game in your Steam libraries. To specify a folder,
   use Install.cmd -Game "C:\path\to\Project Silverfish".
2. Launch Project Silverfish through Steam and choose Multiplayer.
   Under Host, choose New Game to create a save or Load Game to restore
   one. The Host form shows the configured UDP port. Select Require
   password to protect the session. Guests choose Join by Direct IP and
   enter the host IP and UDP port. Select Use password if required.
3. On your home network use the host's LAN IP. Across networks the guest must
   be able to reach the host's chosen UDP port (7777 by default).

You can remove the extracted ZIP files after setup. The installed game starts
the mod directly. Co-op campaigns and local profiles live under
%LOCALAPPDATA%\SilverfishCoop\Resident unless you used Install.cmd -Work to
choose a different folder. Back up co-op campaigns you want to keep.

Both players need the same version of this in-development mod and the matching
Steam game build. This ZIP includes UE4SS v4.0.0, its Project Silverfish
signature, and the embedded Python runtime and its dependency notices.
https://github.com/UE4SS-RE/RE-UE4SS/releases/tag/v4.0.0
"""


def assemble(output: Path, *, version: str, revision: str, cpp_addon: Path, embedded_addon: Path,
             embedded_runtime: Path, ue4ss: Path, signatures: Path,
             cpp_addon_sha256: str | None = None) -> dict:
    """Assemble a new Windows ZIP from a committed source tree and the corresponding binaries."""
    if not layout.VERSION.fullmatch(version) or version.endswith("."):
        raise ValueError("Supply a version made from letters, numbers, dots, underscores, or hyphens")
    output = Path(os.path.abspath(output))
    if output.suffix.lower() != ".zip" or os.path.lexists(output):
        raise ValueError("Output must name a new ZIP")
    layout._ordinary(output.parent, directory=True)
    cpp_addon, embedded_addon, embedded_runtime, ue4ss, signatures = (
        Path(os.path.abspath(path)) for path in (cpp_addon, embedded_addon, embedded_runtime, ue4ss, signatures)
    )
    sources = release_bundle._sources(release_bundle.REPO, revision, fixed_sources=_RESIDENT_SOURCES)
    runtime, addon = release_bundle._runtime(embedded_runtime, embedded_addon, sources)
    gameplay = release_bundle._read(cpp_addon, layout.FILE_LIMIT)
    release_bundle._pe(gameplay, dll=True, name="gameplay add-on")
    gameplay_sha = sha256(gameplay).hexdigest()
    if cpp_addon_sha256 is not None and gameplay_sha != cpp_addon_sha256:
        raise ValueError("Gameplay add-on differs from the supplied build checksum")

    summary = {"schema": 1, "version": version, "revision": revision, "gameplay_addon_sha256": gameplay_sha,
               "embedded_addon_sha256": sha256(addon).hexdigest(),
               "runtime_manifest_sha256": sha256(runtime[layout.RUNTIME + "/runtime-manifest.json"]).hexdigest()}
    files = {**sources, **runtime, **release_bundle._loader(ue4ss, signatures),
             "Install.cmd": release_bundle.INSTALL, "Setup.ps1": _SETUP, "START-HERE.txt": _START,
             "ResidentSetup.py": _SETUP_PYTHON, layout.GAMEPLAY_ADDON: gameplay, layout.EMBEDDED_ADDON: addon,
             layout.MANIFEST: (json.dumps(summary, indent=2) + "\n").encode("ascii")}
    with TemporaryDirectory(prefix=".resident-release-", dir=output.parent) as temporary:
        archive = Path(temporary) / "release.zip"
        layout.write_zip(archive, files)
        with archive.open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()
        if os.name == "nt":
            archive.rename(output)
        else:
            os.link(archive, output)
    return {"release": version, "revision": revision, "file": str(output), "sha256": digest, "entries": len(files)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    for artifact in ("ue4ss", "signatures", "cpp-addon", "embedded-addon", "embedded-runtime"):
        parser.add_argument("--" + artifact, type=Path, required=True)
    parser.add_argument("--cpp-addon-sha256")
    try:
        result = assemble(**vars(parser.parse_args()))
    except (CalledProcessError, OSError, ValueError) as error:
        parser.exit(1, f"Resident co-op package assembly failed: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
