"""Select and commit one draw from the stock Red Cube destinations."""

from secrets import choice

from .native_cube_draw import CubeDraw
from .travel import RED_DESTINATIONS


ARRIVAL = "Red_Tes"
_journal = CubeDraw("red", RED_DESTINATIONS)
ledger = _journal.ledger
begin = _journal.begin
arrive = _journal.arrive


def draw() -> str:
    return choice(RED_DESTINATIONS)
