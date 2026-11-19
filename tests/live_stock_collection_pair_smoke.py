"""Verify Get Molasses or Hall Pass through the exact stock board and placed pickup."""

import argparse
import asyncio
from pathlib import Path

from live_coup_report_smoke import CollectionSmoke, exercise

from tools.silverfish.native_authored_pickup import TRENCH
from tools.silverfish.native_computer_board import MOLASSES, SAVLO_KEYCARD
from tools.silverfish.native_dust_cabinet import MOLASSES as MOLASSES_CABINET
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, NORTHEAST_RAIL, OBSERVATORY, SHOPKEEP


ANNEX = "/Game/Classes/Maps/Maps_Dungeons/Anex_01_P"
STONE_MAZE = "/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P"
MOLASSES_RUN = CollectionSmoke(
    "molasses", MOLASSES, (SHOPKEEP, NORTHEAST_RAIL, SHOPKEEP), {},
    accept_before_pickup=True, stock_pickup=True, cabinet=MOLASSES_CABINET,
)
SAVLO_KEYCARD_RUN = CollectionSmoke(
    "savlo-keycard", SAVLO_KEYCARD,
    (OBSERVATORY, CROSSROADS, DEAD_BRIDGE, STONE_MAZE, ANNEX, TRENCH, ANNEX, STONE_MAZE, DEAD_BRIDGE, CROSSROADS, OBSERVATORY),
    {"8": ("Rep_Freelancer", 100)}, accept_before_pickup=True, stock_pickup=True,
    advantage=(("Adv_Freelancer", 300),),
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--quest", choices=("molasses", "savlo-keycard"), required=True)
    args = parser.parse_args()
    plan = MOLASSES_RUN if args.quest == "molasses" else SAVLO_KEYCARD_RUN
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), board_input=True, plan=plan))
