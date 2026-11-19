"""Select and commit one uniform draw from the stock Purple Cube levels."""

from secrets import choice

from .native_cube_draw import CubeDraw
from .travel import (
    BEACH,
    CISTERN,
    CITY,
    CROSSROADS,
    DEAD_BRIDGE,
    DOCKS,
    FOREST,
    LAKE,
    NORTHEAST_RAIL,
    OBSERVATORY,
    RAIL_YARD,
    REFINERY,
    RUIN_FIELDS,
    SLUMS,
    TRASH_PIT,
)


ARRIVAL = "Bed"
DESTINATIONS = (
    CITY, CROSSROADS, OBSERVATORY, RAIL_YARD, RUIN_FIELDS, BEACH, DEAD_BRIDGE, DOCKS,
    FOREST, LAKE, NORTHEAST_RAIL, SLUMS, TRASH_PIT, CISTERN, REFINERY,
)
_journal = CubeDraw("purple", DESTINATIONS)
ledger = _journal.ledger
begin = _journal.begin
arrive = _journal.arrive


def draw() -> str:
    return choice(DESTINATIONS)
