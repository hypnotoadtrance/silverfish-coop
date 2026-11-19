"""Verify the RailYard Depot Documents pickup and Scav Bar contract on loopback."""

import argparse
import asyncio
from pathlib import Path

from live_coup_report_smoke import CollectionSmoke, exercise

from tools.silverfish.native_computer_board import DEPOT_DOCUMENTS
from tools.silverfish.travel import BOROUGH, CITY, RAIL_YARD, SCAV_BAR, SLUMS


DEPOT = CollectionSmoke(
    "depot-documents", DEPOT_DOCUMENTS, (BOROUGH, CITY, RAIL_YARD, SLUMS, SCAV_BAR),
    {"8": ("Rep_Freelancer", 25)},
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--board-input", action="store_true", help="use the guest's stock Interact entry at Scav Bar")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), board_input=args.board_input, plan=DEPOT))
