"""Cooked placements qualified for the stock loose-supply pickup adapter."""

from dataclasses import dataclass
import hashlib

from .guest_weapon_profiles import FORTY_FIVE_BALL, StockAmmo
from .travel import CITY, CROSSROADS


@dataclass(frozen=True)
class Placement:
    area: str
    name: str
    position: tuple[float, float, float]
    physics: bool
    ammo: StockAmmo
    units: int
    weight: float
    value: int

    @property
    def world(self) -> str:
        return f"{self.area}.{self.area.rsplit('/', 1)[-1]}"

    @property
    def path(self) -> str:
        return f"{self.world}:PersistentLevel.{self.name}"

    @property
    def actor(self) -> str:
        return f"{self.ammo.item}_C {self.path}"

    @property
    def source(self) -> str:
        identity = f"{self.path}|{self.ammo.item_path}".encode("ascii")
        return "supply-" + hashlib.sha256(identity).hexdigest()[:24]


# Exact PersistentLevel actor and owned root Box exports from the cooked maps.
# Weight and value are the placed stack totals. The CDO supplies 10 rounds.
PLACEMENTS = (
    Placement(CITY, "Ammo_Magnum_BP_C_1", (5424.624698003305, -6504.258364840004, 933.0565996312665),
              False, FORTY_FIVE_BALL, 10, 0.13, 50),
    Placement(CROSSROADS, "Ammo_Magnum_BP_C_1", (6033.949367709363, 10806.238708443801, 940.5129757964019),
              True, FORTY_FIVE_BALL, 10, 0.13, 50),
)

BY_SOURCE = {row.source: row for row in PLACEMENTS}
