"""Assemble a drag-and-drop Silverfish co-op trial ZIP."""

import argparse
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
from subprocess import CalledProcessError
from tempfile import TemporaryDirectory

from tools.silverfish import runtime, service

from . import install_stage, install_stock, release_bundle, release_setup as layout, resident_release
from .resident_install import _GAME_BUILD


_SHIPPING_SHA256 = "1d7a6642748f8682798828d649d0fd8b855b92608f30e7428d12bdc6a290a454"
_BINARIES = runtime.SHIPPING.parent
_MODS = _BINARIES / "ue4ss" / "Mods"
_EMBEDDED = _MODS / install_stage.MOD
_SCRIPTS = _BINARIES / runtime.MOD_SCRIPTS
_STAGE = runtime.PORTABLE_STAGE_MANIFEST
_README = Path("SilverFish/Silverfish-Coop-README.txt")
_PORTABLE = "resident-portable.json"
_SCRIPT_SOURCE = "mod/SilverfishCoop/Scripts/"
_RUNTIME_SOURCE = layout.RUNTIME + "/"
_RUNTIME_MANIFEST = _RUNTIME_SOURCE + install_stage.MANIFEST

_START = r"""Project Silverfish Co-op | Windows Direct IP trial {version}

{variant}

1. Close Project Silverfish. In the Steam library, right-click the game and
   select Manage > Browse local files. This opens the folder with SilverFish.exe.
2. Copy the SilverFish folder from this ZIP into the open game folder.
   Merge it with the existing SilverFish folder and replace the mod
   files when Windows asks. Use the same steps for future updates.
3. Launch the game through Steam and choose Multiplayer. Choose Host to
   start or load a campaign, or Join by Direct IP to enter the host's IP,
   UDP port, and password if the host uses one.

Both players need the same co-op release and matching Steam game build.
Co-op saves live under %LOCALAPPDATA%\SilverfishCoop\Resident by default.
On the same network, use the host's LAN IP. Across networks the guest
must be able to reach the host's chosen UDP port (7777 by default).

Each campaign has its own guest characters. The host's identity belongs to
the co-op installation and stays the same when the host changes campaigns.
If the guest sees Verify host identity, the host can share the fingerprint
from %LOCALAPPDATA%\SilverfishCoop\Resident\host-fingerprint.txt while hosting.
Paste that fingerprint in the guest's verification screen.
"""
_FULL_INSTALL = ("Use this ZIP for a first install or an update. It includes the pinned\n"
                 "UE4SS v4.0.0 loader and Project Silverfish signature. Updating with this\n"
                 "ZIP replaces bundled UE4SS files, including UE4SS-settings.ini.")
_UPDATE = ("This archive is for a game with the supported UE4SS v4.0.0 loader and\n"
           "Project Silverfish signature already in place. If you need those\n"
           "files, use the matching archive that includes UE4SS.")


def _json(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def _shipping_hash(shipping: Path) -> str:
    layout._ordinary(shipping, directory=False)
    with shipping.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    if digest != _SHIPPING_SHA256:
        raise ValueError("Shipping.exe does not match the qualified Project Silverfish build")
    return digest


def _features(work: Path, stage: dict, payload: dict[Path, bytes], loader: bytes) -> tuple[dict[str, str], dict[str, str]]:
    root = work / "stage"
    inputs = {
        _STAGE: _json(stage),
        _BINARIES / runtime.CPP_RUNTIME_DLL: loader,
        _BINARIES / runtime.CPP_ADDON_DLL: payload[_BINARIES / runtime.CPP_ADDON_DLL],
    }
    inputs.update((name, data) for name, data in payload.items() if name.is_relative_to(_SCRIPTS))
    for name, data in inputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if not runtime.qualified_authored_routes(work):
        raise ValueError("The packaged native gameplay files do not qualify for resident co-op")
    options = service.unified_title_options(work)

    def environment(headless: bool) -> dict[str, str]:
        configured = runtime.game_environment(
            work, headless=headless, embedded_engine=True, embedded_startup="1" * 32,
            resident_title=True, resident_direct_join=True, unified_title=True, base_env={},
            title_fixture=headless, pause_fixture=headless, **options,
        )
        return {key: value for key, value in configured.items()
                if key.startswith("SILVERFISH_COOP_") and key not in
                {"SILVERFISH_COOP_EMBEDDED_STARTUP", "SILVERFISH_COOP_BRIDGE"}}

    normal, headless = environment(False), environment(True)
    if any(headless.get(key) != value for key, value in normal.items()):
        raise ValueError("Isolated testing must keep the release gameplay features")
    return normal, {key: value for key, value in headless.items() if key not in normal}


def assemble(output: Path, *, version: str, revision: str, shipping: Path, cpp_addon: Path,
             embedded_addon: Path, embedded_runtime: Path, ue4ss: Path, signatures: Path | None = None,
             include_loader: bool = False, cpp_addon_sha256: str | None = None) -> dict:
    """Package an exact committed Lua and embedded runtime pair for a qualified stock executable."""
    if not layout.VERSION.fullmatch(version) or version.endswith("."):
        raise ValueError("Supply a version made from letters, numbers, dots, underscores, or hyphens")
    output = Path(os.path.abspath(output))
    if output.suffix.lower() != ".zip" or os.path.lexists(output):
        raise ValueError("Output must name a new ZIP")
    layout._ordinary(output.parent, directory=True)
    if include_loader and signatures is None:
        raise ValueError("Supply the approved signature folder with --include-loader")

    def absolute(path: Path) -> Path:
        return Path(os.path.abspath(path))

    shipping_sha = _shipping_hash(absolute(shipping))
    sources = release_bundle._sources(release_bundle.REPO, revision)
    package, embedded = release_bundle._runtime(absolute(embedded_runtime), absolute(embedded_addon), sources)
    if _PORTABLE.encode("utf-16-le") not in embedded:
        raise ValueError("The embedded add-on does not support portable startup")
    manifest_sha = sha256(package[_RUNTIME_MANIFEST]).hexdigest()
    runtime_folder = _EMBEDDED / f"runtime-{manifest_sha[:16]}"
    gameplay = release_bundle._read(absolute(cpp_addon), layout.FILE_LIMIT)
    release_bundle._pe(gameplay, dll=True, name="gameplay add-on")
    gameplay_sha = sha256(gameplay).hexdigest()
    if cpp_addon_sha256 is not None and cpp_addon_sha256 != gameplay_sha:
        raise ValueError("Gameplay add-on differs from the supplied build checksum")
    ue4ss = absolute(ue4ss)
    loader = install_stock._pinned(ue4ss / runtime.CPP_RUNTIME_DLL, install_stock.UE4SS_OFFICIAL[runtime.CPP_RUNTIME_DLL])

    scripts = {Path(name.removeprefix(_SCRIPT_SOURCE)): data for name, data in sources.items()
               if name.startswith(_SCRIPT_SOURCE)}
    stage = {
        "schema": 1, "game_build": _GAME_BUILD, "stock_files": 1 + len(runtime.STOCK_PACKAGES),
        "cpp_death_lab": {"runtime": sha256(loader).hexdigest(), "addon": gameplay_sha},
        "lua_files": {name.as_posix(): sha256(release_bundle._normalized(data)).hexdigest()
                      for name, data in scripts.items()},
    }
    payload = {_SCRIPTS / name: data for name, data in scripts.items()}
    payload.update((runtime_folder / name.removeprefix(_RUNTIME_SOURCE), data) for name, data in package.items())
    payload.update({
        _BINARIES / runtime.CPP_ADDON_DLL: gameplay,
        _EMBEDDED / "dlls" / "main.dll": embedded,
        _STAGE: _json(stage),
        _README: _START.format(version=version, variant=_FULL_INSTALL if include_loader else _UPDATE).encode("ascii"),
    })
    for module in ("SilverfishCoop", runtime.CPP_MOD_NAME, install_stage.MOD):
        payload[_MODS / module / "enabled.txt"] = b""
    if include_loader:
        proxy, signature, license_name = install_stock.PROXY, install_stock.SIGNATURE, Path("ue4ss/LICENSE")
        settings = install_stock.SETTINGS
        payload.update({
            _BINARIES / runtime.CPP_RUNTIME_DLL: loader,
            _BINARIES / proxy: install_stock._pinned(ue4ss / proxy, install_stock.UE4SS_OFFICIAL[proxy]),
            _BINARIES / settings: install_stock._pinned(ue4ss / settings, install_stock.UE4SS_OFFICIAL[settings]),
            _BINARIES / license_name: release_bundle._read(ue4ss / license_name, 32 * 1024),
            _BINARIES / "ue4ss" / signature: install_stock._pinned(absolute(signatures) / signature.name,
                                                                  install_stock.SIGNATURE_SHA256),
        })

    with TemporaryDirectory(prefix=".resident-drop-", dir=output.parent) as temporary:
        scratch = Path(temporary)
        features, isolated = _features(scratch, stage, payload, loader)
        payload[_EMBEDDED / _PORTABLE] = _json({"schema": 1, "shipping_sha256": shipping_sha,
                                                "features": features, "isolated_features": isolated})
        archive = scratch / "release.zip"
        resident_release._archive(archive, {path.as_posix(): data for path, data in payload.items()})
        with archive.open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()
        if os.name == "nt":
            archive.rename(output)
        else:
            os.link(archive, output)
    return {"release": version, "revision": revision, "file": str(output), "sha256": digest, "entries": len(payload),
            "include_loader": include_loader, "shipping_sha256": shipping_sha, "runtime_manifest_sha256": manifest_sha}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    for artifact in ("shipping", "ue4ss", "cpp-addon", "embedded-addon", "embedded-runtime"):
        parser.add_argument("--" + artifact, type=Path, required=True)
    parser.add_argument("--signatures", type=Path)
    parser.add_argument("--include-loader", action="store_true", help="include the pinned UE4SS loader and game signature")
    parser.add_argument("--cpp-addon-sha256")
    try:
        result = assemble(**vars(parser.parse_args()))
    except (CalledProcessError, OSError, ValueError) as error:
        parser.exit(1, f"Drag-and-drop co-op package assembly failed: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
