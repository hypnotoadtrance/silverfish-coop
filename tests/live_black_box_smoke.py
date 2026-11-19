"""Verify the Gorge recorder and Simmi's Observatory Black Box contract."""

import argparse
import asyncio
from pathlib import Path

from live_coup_report_smoke import CollectionSmoke, exercise
from tools.silverfish import native_black_box
from tools.silverfish import native_computer_board as collections
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, OBSERVATORY


STONE_MAZE = "/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P"
SWAMP = "/Game/Classes/Maps/Maps_OverWorld/Swamp_01_P"
TO_GORGE = (OBSERVATORY, CROSSROADS, DEAD_BRIDGE, STONE_MAZE, SWAMP, native_black_box.MAP)
PLAN = CollectionSmoke(
    "black-box", collections.BLACK_BOX, (*TO_GORGE, *reversed(TO_GORGE[:-1])),
    {"8": ("Rep_Freelancer", 50)}, accept_before_pickup=True, stock_pickup=True,
    advantage=(("Adv_Freelancer", 0),), pickup_location=(3656.771352378591, 2466.248841434704, 3590.8622264942896),
    completion_flag=native_black_box.COMPLETION_FLAG,
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="Isolated staged co-op workspace")
    parser.add_argument("--vanilla-saved", type=Path, required=True, help="Original single-player Saved directory to hash")
    parser.add_argument("--stock-board-input", action="store_true", help="Accept and redeem with stock Interact")
    args = parser.parse_args()
    asyncio.run(exercise(args.work, args.vanilla_saved, board_input=args.stock_board_input, plan=PLAN))
