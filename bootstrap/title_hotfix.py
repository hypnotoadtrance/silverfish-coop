"""Package a stock-title repair for an installed trial-4917824 pair."""

import argparse
from hashlib import file_digest, sha256
import json
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from . import install_stock, release_bundle


OLD = {
    "main.dll": "955c2e5447b90d3755ae1ac5d69298ab0a7059c2b8a9b41bc322c07956200c7f",
    "SilverfishCoopCoordinator.exe": "a4913357b01ef9b3e1aed91af9ca1309005cf4c481621776e9d6529dd00dacd8",
}
SCRIPT = Path(__file__).with_name("title_hotfix.ps1")
INSTRUCTIONS = b"""Project Silverfish Co-op | Title repair for trial-4917824

Close the game, extract this ZIP, and run Update.cmd on each affected PC.
Setup can ask for the game folder. In Steam, use Manage > Browse local files.
When the repair finishes, launch through Steam and press F7 on the title.

This update replaces the two installed files that open the co-op title.
It keeps your existing co-op profile and single-player and co-op saves.
The original pair is backed up in SilverfishCoopTitleRepair-4917824
under the Steam game's folder.
"""


def assemble(output: Path, *, bootstrap: Path) -> dict:
    payload = install_stock._bootstrap_payload(bootstrap.resolve(strict=True))
    pair = {}
    files = {}
    for relative in (install_stock.TITLE_DLL, install_stock.WORKER):
        name, body = relative.name, payload[relative]
        release_bundle._pe(body, dll=relative == install_stock.TITLE_DLL, name=name)
        new = sha256(body).hexdigest()
        if new == OLD[name]:
            raise ValueError(f"{name} must come from the repaired pair")
        pair[name] = {"old": OLD[name], "new": new}
        files["payload/" + name] = body
    template = SCRIPT.read_text(encoding="ascii")
    marker = "__REPAIR_PAIR_JSON__"
    if template.count(marker) != 1:
        raise ValueError("The repair script must have one pair manifest")
    setup = template.replace(marker, json.dumps(pair, separators=(",", ":"))).encode("ascii")
    wrapper = release_bundle.INSTALL.replace(b"Setup.ps1", b"Update.ps1")
    files.update({"Update.cmd": wrapper, "Update.ps1": setup, "START-HERE.txt": INSTRUCTIONS})
    output = output.absolute()
    if output.suffix.lower() != ".zip":
        raise ValueError("Name the repair archive with a .zip extension")
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    with output.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    return {"file": str(output), "sha256": digest, "pair": pair}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap", type=Path, required=True, help="Repaired stock bootstrap archive")
    try:
        result = assemble(**vars(parser.parse_args()))
    except (BadZipFile, OSError, ValueError) as error:
        parser.exit(1, f"Title repair assembly failed: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
