"""Verify Utility Tunnels' stock blank-tape exchange and Black Market redemption."""

import argparse
import asyncio
from pathlib import Path

from live_coup_report_smoke import CollectionSmoke, exercise

from tools.silverfish.native_computer_board import PUMP_ROOM
from tools.silverfish.travel import BLACK_MARKET, CROSSROADS, LAKE, RAIL_YARD, RUIN_FIELDS, SCIENTIST_SHOP, UTILITY_TUNNELS


PUMP = CollectionSmoke(
    "pump-room", PUMP_ROOM,
    (SCIENTIST_SHOP, RUIN_FIELDS, CROSSROADS, RAIL_YARD, UTILITY_TUNNELS, RAIL_YARD, LAKE, BLACK_MARKET),
    {"8": ("Rep_Freelancer", 50), "3": ("Rep_Fae", -100)},
    stock_pickup=True,
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), board_input=True, plan=PUMP))
