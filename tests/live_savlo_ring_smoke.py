"""Verify the original Scav Bar contract for the placed ring at Savlo Estate."""

import argparse
import asyncio
from pathlib import Path

from live_coup_report_smoke import CollectionSmoke, exercise

from tools.silverfish.native_authored_pickup import SAVLO_ESTATE
from tools.silverfish.native_computer_board import SAVLO_RING
from tools.silverfish.travel import CROSSROADS, FOREST, RAIL_YARD, SCAV_BAR, SLUMS


OUTBOUND = (SCAV_BAR, SLUMS, RAIL_YARD, CROSSROADS, FOREST, SAVLO_ESTATE)
RING = CollectionSmoke(
    "savlo-ring", SAVLO_RING, (*OUTBOUND, *OUTBOUND[-2::-1]), {"5": ("Rep_Coup", -200)},
    accept_before_pickup=True, stock_pickup=True,
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), board_input=True, plan=RING))
