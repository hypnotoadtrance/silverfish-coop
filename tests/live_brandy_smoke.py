"""Verify the exact City Paladin Brandy pickup and Black Market Fine Vintage contract."""

import argparse
import asyncio
from pathlib import Path

from live_coup_report_smoke import CollectionSmoke, exercise

from tools.silverfish.native_computer_board import BRANDY
from tools.silverfish.travel import BLACK_MARKET, BOROUGH, CITY, LAKE


FINE_VINTAGE = CollectionSmoke(
    "brandy", BRANDY, (BOROUGH, CITY, LAKE, BLACK_MARKET), {"8": ("Rep_Freelancer", 15)},
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--board-input", action="store_true", help="use the guest's stock Interact entry at Black Market")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), board_input=args.board_input, plan=FINE_VINTAGE))
