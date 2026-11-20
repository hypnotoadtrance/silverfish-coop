"""Create an opt-in UE4SS mod archive for the stock title bootstrap."""

import argparse
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


BOOTSTRAP = Path(__file__).resolve().parent
MOD = Path("SilverfishCoopBootstrap/Scripts")
SHARED = BOOTSTRAP.parent / "mod" / "SilverfishCoop" / "Scripts"


def package(output: Path, *, native_dll: Path | None = None, coordinator: Path | None = None) -> None:
    scripts = {
        "main.lua": BOOTSTRAP / MOD / "main.lua",
        "native_title_entry.lua": SHARED / "native_title_entry.lua",
        "native_title_display.lua": SHARED / "native_title_display.lua",
    }
    sources = {MOD / name: path.read_bytes() for name, path in scripts.items()}
    if (native_dll is None) != (coordinator is None):
        raise ValueError("Package the matching native title bridge and coordinator together")
    if native_dll is not None:
        bridge, worker = native_dll.read_bytes(), coordinator.read_bytes()
        if not bridge.startswith(b"MZ") or not worker.startswith(b"MZ") or sha256(worker).hexdigest().encode("ascii") not in bridge:
            raise ValueError("The native title bridge does not contain the packaged coordinator's SHA-256 pin")
        dlls = MOD.parent / "dlls"
        sources.update({dlls / "main.dll": bridge, dlls / "SilverfishCoopCoordinator.exe": worker})
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for relative, source in sources.items():
            archive.writestr(relative.as_posix(), source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new archive path in the approved package workspace")
    parser.add_argument("--native-dll", type=Path, help="native title bridge linked with the packaged worker SHA-256")
    parser.add_argument("--coordinator", type=Path, help="matching windowless coordinator executable")
    args = parser.parse_args()
    package(args.output, native_dll=args.native_dll, coordinator=args.coordinator)


if __name__ == "__main__":
    main()
