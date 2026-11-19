"""Verify Stolen Goods at the Black Market board through the actual Forest pickup."""

import argparse
import asyncio
from pathlib import Path

from live_coup_report_smoke import CollectionSmoke, exercise

from tools.silverfish.native_computer_board import STOLEN_BAG
from tools.silverfish.travel import BLACK_MARKET, CROSSROADS, FOREST, LAKE


PLAN = CollectionSmoke(
    "stolen-goods", STOLEN_BAG, (BLACK_MARKET, LAKE, CROSSROADS, FOREST, CROSSROADS, LAKE, BLACK_MARKET),
    {"8": ("Rep_Freelancer", 100), "5": ("Rep_Coup", -150)}, accept_before_pickup=True, stock_pickup=True,
    advantage=(("Adv_Freelancer", 0),), pickup_location=(-8042.335612722819, 25100.7873570329, 898.6314940223217),
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), board_input=True, plan=PLAN))
