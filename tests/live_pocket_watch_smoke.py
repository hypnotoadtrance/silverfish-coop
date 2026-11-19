"""Verify the original Scav Bar Pocket Watch contract and lone Crossroads pickup."""

import argparse
import asyncio
from pathlib import Path

from live_coup_report_smoke import CollectionSmoke, exercise

from tools.silverfish.native_computer_board import POCKET_WATCH
from tools.silverfish.travel import CROSSROADS, RAIL_YARD, SCAV_BAR, SLUMS


WATCH = CollectionSmoke(
    "pocket-watch", POCKET_WATCH, (SCAV_BAR, SLUMS, RAIL_YARD, CROSSROADS, RAIL_YARD, SLUMS, SCAV_BAR), {},
    accept_before_pickup=True, stock_pickup=True, stackable=True,
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), board_input=True, plan=WATCH))
