"""Verify stock randomized Dead Drop activation, pickup and turn-in on loopback."""

import argparse
import asyncio
from pathlib import Path

from live_coup_report_smoke import CollectionSmoke, exercise
from tools.silverfish.native_computer_board import DEAD_DROP, METRO_DEAD_DROP
from tools.silverfish.travel import BLACK_MARKET, CITY, CROSSROADS, LAKE


PLAN = CollectionSmoke(
    "crossroads-dead-drop", DEAD_DROP, (CROSSROADS, LAKE, BLACK_MARKET, LAKE, CROSSROADS, LAKE, BLACK_MARKET),
    {"8": ("Rep_Freelancer", 40), "4": ("Rep_State", -5)}, accept_before_pickup=True, stock_pickup=True,
    advantage=(("Adv_Freelancer", 0),), conditional_pickup=True,
)


METRO_PLAN = CollectionSmoke(
    "metro-dead-drop", METRO_DEAD_DROP, (CITY, LAKE, BLACK_MARKET, LAKE, CITY, LAKE, BLACK_MARKET),
    {"8": ("Rep_Freelancer", 50), "4": ("Rep_State", -5)}, accept_before_pickup=True, stock_pickup=True,
    advantage=(("Adv_Freelancer", 0),), conditional_pickup=True,
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--metro", action="store_true", help="exercise the authored Metro Dead Drop")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), board_input=True,
                         plan=METRO_PLAN if args.metro else PLAN))
