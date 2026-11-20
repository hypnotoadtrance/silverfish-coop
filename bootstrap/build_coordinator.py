"""Build the windowless Windows co-op coordinator from a matched checkout."""

import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="fresh build directory")
    output = parser.parse_args().output.resolve()
    output.mkdir()
    source = Path(__file__).resolve().parents[1]
    os.environ["PYINSTALLER_CONFIG_DIR"] = str(output / "cache")

    from PyInstaller.__main__ import run

    # Invite compatibility fingerprints the coordinator's exact normalized sources.
    sources = source / "tools" / "silverfish"
    data = [option for path in sorted(sources.rglob("*.py"))
            for option in ("--add-data", f"{path}:{path.parent.relative_to(source).as_posix()}")]
    run(["--onefile", "--windowed", "--name", "SilverfishCoopCoordinator",
         "--workpath", str(output / "build"), "--distpath", str(output / "dist"),
         "--specpath", str(output), "--paths", str(source),
         *data, str(source / "bootstrap" / "coordinator.py")])


if __name__ == "__main__":
    main()
