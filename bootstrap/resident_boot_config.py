"""Configure the embedded title runtime in an installed Project Silverfish game."""

import argparse
from hashlib import sha256
from ipaddress import ip_address
import json
from pathlib import Path

from tools.silverfish import runtime, service
from tools.silverfish.control import NAME
from tools.silverfish.embedded_engine import assert_private_saved_tree
from tools.silverfish.embedded_startup import _DEVICE

from . import install_stage, install_stock


DESCRIPTOR = "resident-install.json"
DESCRIPTOR_LIMIT = 8192


def _name(value: str) -> bool:
    return isinstance(value, str) and NAME.fullmatch(value) is not None and _DEVICE.fullmatch(value) is None


def _verify_game(game: Path, work: Path) -> None:
    marker = game / "silverfish-coop-stage.json"
    install_stock._ordinary(marker)
    metadata = runtime.read_json(marker)
    if type(metadata.get("schema")) is not int or metadata["schema"] != 1:
        raise ValueError("The installed game has no supported co-op content manifest")
    digest = sha256()
    for relative in (runtime.SHIPPING, *(runtime.PAKS / name for name in runtime.STOCK_PACKAGES)):
        path = game / relative
        install_stock._ordinary(path)
        digest.update(bytes.fromhex(runtime._file_sha256(path)))
    if digest.hexdigest() != metadata.get("game_build"):
        raise ValueError("The installed game build differs from its co-op content manifest")

    binaries = game / runtime.SHIPPING.parent
    mods = binaries / "ue4ss" / "Mods"
    activation = install_stock._read(mods / "mods.txt", 1024 * 1024)
    entries: dict[str, list[str]] = {}
    for row in activation.decode("utf-8-sig").splitlines():
        if ";" in row:
            continue
        name, separator, enabled = row.partition(":")
        if separator:
            entries.setdefault(name.strip().casefold(), []).append(enabled.strip())
    for name in ("SilverfishCoop", runtime.CPP_MOD_NAME, install_stage.MOD):
        if entries.get(name.casefold()) != ["1"]:
            raise ValueError(f"Enable the matched {name} module before configuring the resident title")
    if "1" in entries.get(install_stock.MOD.casefold(), []):
        raise ValueError("Disable the legacy co-op title bootstrap before enabling the resident title")

    scripts = binaries / runtime.MOD_SCRIPTS
    install_stock._ordinary(scripts, directory=True)
    install_stock._files(scripts)
    if (not runtime.qualified_authored_routes(work)
            or runtime._code_fingerprint(scripts, ".lua") != runtime._code_fingerprint(
                runtime.REPO / "mod/SilverfishCoop/Scripts", ".lua")):
        raise ValueError("The installed co-op gameplay files do not match this installer")
    embedded = mods / install_stage.MOD
    install_stage._verify(embedded / "runtime", embedded / "dlls" / "main.dll")


def configure(game: Path, work: Path, *, seat: str = "local", campaign: str = "default",
              bind: str = "0.0.0.0", port: int = 7777, isolated: bool = False) -> Path:
    """Publish the native startup descriptor without replacing an existing configuration."""
    game, work = map(install_stock._absolute, (game, work))
    for directory in (game, work):
        install_stock._ordinary(directory, directory=True)
    if game.is_relative_to(work) or work.is_relative_to(game):
        raise ValueError("The installed game and co-op workspace must be separate")
    if not _name(seat) or not _name(campaign):
        raise ValueError("Use a valid local seat and campaign name")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Use a UDP port from 1 through 65535")
    if type(isolated) is not bool:
        raise ValueError("Invalid isolated title setting")
    try:
        if not isinstance(bind, str) or "%" in bind:
            raise ValueError
        address = str(ip_address(bind))
    except ValueError:
        raise ValueError("Use a numeric host IP address") from None

    with runtime.installed_content(work, game):
        _verify_game(game, work)
        environment = runtime.game_environment(
            work, headless=isolated, embedded_engine=True, embedded_startup="1" * 32,
            resident_title=True, resident_direct_join=True, unified_title=True,
            title_fixture=isolated, pause_fixture=isolated, base_env={},
            **service.unified_title_options(work),
        )
    features = {key: value for key, value in environment.items()
                if key.startswith("SILVERFISH_COOP_") and key not in {
                    "SILVERFISH_COOP_EMBEDDED_STARTUP", "SILVERFISH_COOP_BRIDGE",
                }}
    plan = {"schema": 1, "work": str(work), "seat": seat, "campaign": campaign,
            "bind": address, "port": port, "isolated": isolated, "features": features}
    encoded = (json.dumps(plan, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    if len(encoded) > DESCRIPTOR_LIMIT:
        raise ValueError("The resident startup descriptor exceeds its size limit")
    descriptor = game / runtime.SHIPPING.parent / "ue4ss" / "Mods" / install_stage.MOD / DESCRIPTOR

    if (previous := install_stock._read(descriptor, DESCRIPTOR_LIMIT, missing=True)) is not None:
        _verify_existing(previous, plan)

    title = work / "titles" / seat
    bridge = title / "run" / "game-bridge"
    directories = (bridge, title / "game-user") if isolated else (bridge,)
    if isolated:
        assert_private_saved_tree(title / "game-user")
    for directory in directories:
        install_stock._ordinary(directory, directory=True, missing=True)
        directory.mkdir(parents=True, exist_ok=True)
        install_stock._ordinary(directory, directory=True)
    if previous is None:
        try:
            install_stock._atomic(descriptor, encoded)
        except FileExistsError:
            _verify_existing(install_stock._read(descriptor, DESCRIPTOR_LIMIT), plan)
    return descriptor


def _verify_existing(previous: bytes, plan: dict) -> None:
    if json.dumps(json.loads(previous), sort_keys=True) != json.dumps(plan, sort_keys=True):
        raise ValueError("A different resident startup configuration already exists")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--seat", default="local")
    parser.add_argument("--campaign", default="default")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--isolated", action="store_true", help="use an isolated headless test profile")
    try:
        print(configure(**vars(parser.parse_args())))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Resident title configuration failed: {error}\n")


if __name__ == "__main__":
    main()
