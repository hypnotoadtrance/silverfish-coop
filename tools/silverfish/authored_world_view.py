"""Exact cooked outdoor day-cycle placements and their generated Lua table."""

import argparse
import json
from pathlib import Path

from .authored_routes import MAPS


# Each scene was checked for one full day controller and one stock cloud,
# plus the controller's exact linked stock sky and height fog.
_DEFAULT_PLACEMENTS = (
    "Beach_01_P",
    "Borough",
    "City_01_P",
    "CrashSite_01_P",
    "DeadBridge_P",
    "DeadEnd_01_P",
    "Docks_01_P",
    "DryDock_P",
    "Factory_SB_P",
    "Gorge_01_P",
    "Lake_01_P",
    "NorthEast_Rail_P",
    "Observitory_P",
    "PineRidge_P",
    "Quarry_01_P",
    "RailYard_P",
    "Refinery_01_P",
    "Rev_NorthBase_P",
    "RuinFeilds_P",
    "SavloEstate_P",
    "Slums_P",
    "SouthEast_Road_P",
    "StoneMaze_P",
    "Swamp_01_P",
    "TheWall_01_P",
    "TrashPit_P",
    "Trench_01_P",
    "Tut_Dock_P",
)

_ACTOR_CLASSES = (
    ("day", "DayCycle_SunControler_BP_C"),
    ("sky", "BP_Sky_Sphere_Custom_C"),
    ("fog", "ExponentialHeightFog"),
    ("cloud", "VolumetricCloud"),
)

# Suffixes appear in the same order as the named actors above.
_PLACEMENTS = {
    **dict.fromkeys(_DEFAULT_PLACEMENTS, (0, 0, 0, 0)),
    "CrossRoads_P": (1, 1, 0, 1),
    "Forest_01_P": (0, 0, 2, 0),
}


def _profiles() -> dict[str, dict[str, str]]:
    packages = {package.rsplit("/", 1)[-1]: package for package in MAPS}
    profiles = {}
    for name, indices in sorted(_PLACEMENTS.items()):
        area = packages[name]
        world = f"{area}.{name}"
        profile = {"map": area, "world": f"World {world}"}
        for (field, actor), index in zip(_ACTOR_CLASSES, indices, strict=True):
            profile[field] = f"{actor} {world}:PersistentLevel.{actor}_{index}"
        profiles[area] = profile
    return profiles


WORLD_VIEW_PROFILES = _profiles()
WORLD_VIEW_ACTORS = {area: profile["day"] for area, profile in WORLD_VIEW_PROFILES.items()}


def render_lua() -> str:
    lines = [
        "-- Generated from tools/silverfish/authored_world_view.py.",
        "-- Regenerate with: python -m tools.silverfish.authored_world_view",
        "return {",
    ]
    for area, profile in sorted(WORLD_VIEW_PROFILES.items()):
        lines.append(f"    [{json.dumps(area)}] = {{")
        lines.extend(f"        {field} = {json.dumps(value)}," for field, value in profile.items())
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the checked-in Lua table")
    arguments = parser.parse_args()
    target = Path(__file__).resolve().parents[2] / "mod" / "SilverfishCoop" / "Scripts" / "authored_world_view.lua"
    expected = render_lua()
    if arguments.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != expected:
            parser.exit(1, f"{target} differs from the cooked placement table\n")
    else:
        target.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
