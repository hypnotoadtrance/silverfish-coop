"""Draw uniformly among every destination allowed by stock Green Cube reputation."""

from secrets import choice

from .native_cube_draw import CubeDraw
from .travel import BLACK_MARKET, BOROUGH, DEAD_BRIDGE, FOREST, OBSERVATORY, SCAV_BAR, SCIENTIST_SHOP, SHOPKEEP


ARRIVAL = "Bed"
ALWAYS = (SCAV_BAR, SHOPKEEP)
REPUTATION_LEVELS = ((BOROUGH, 2), (BLACK_MARKET, 8), (OBSERVATORY, 8), (SCIENTIST_SHOP, 2), (DEAD_BRIDGE, 4), (FOREST, 5))
DESTINATIONS = ALWAYS + tuple(destination for destination, _ in REPUTATION_LEVELS)
_journal = CubeDraw("green", DESTINATIONS)
ledger = _journal.ledger
begin = _journal.begin
arrive = _journal.arrive


def candidates(reputation: dict[int, int]) -> tuple[str, ...]:
    factions = {faction for _, faction in REPUTATION_LEVELS}
    if set(reputation) != factions or any(type(rank) is not int or not 0 <= rank <= 255 for rank in reputation.values()):
        raise ValueError("The exact stock faction relationship set is required")
    return ALWAYS + tuple(destination for destination, faction in REPUTATION_LEVELS if reputation[faction] in (0, 1, 2))


def draw(eligible: tuple[str, ...]) -> str:
    return choice(eligible)
