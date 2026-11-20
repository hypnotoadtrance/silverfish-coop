"""Create an opt-in UE4SS mod archive for the stock title bootstrap."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


BOOTSTRAP = Path(__file__).resolve().parent
MOD = Path("SilverfishCoopBootstrap/Scripts")
SHARED = BOOTSTRAP.parent / "mod" / "SilverfishCoop" / "Scripts"


def package(output: Path) -> None:
    scripts = {
        "main.lua": BOOTSTRAP / MOD / "main.lua",
        "native_title_entry.lua": SHARED / "native_title_entry.lua",
        "native_title_display.lua": SHARED / "native_title_display.lua",
    }
    sources = {name: path.read_bytes() for name, path in scripts.items()}
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for name, source in sources.items():
            archive.writestr((MOD / name).as_posix(), source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new archive path in the approved package workspace")
    package(parser.parse_args().output)


if __name__ == "__main__":
    main()
