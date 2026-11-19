"""Cooked placements qualified for the stock loose-supply pickup adapter."""

from dataclasses import dataclass
import hashlib

from .guest_weapon_profiles import FORTY_FIVE_BALL, TWELVE_GAUGE_BUCK, StockAmmo
from .travel import CITY, CROSSROADS, SLUMS


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
CITY_AMMO = Placement(
    CITY, "Ammo_Magnum_BP_C_1", (5424.624698003305, -6504.258364840004, 933.0565996312665),
    False, FORTY_FIVE_BALL, 10, 0.13, 50,
)
CROSSROADS_AMMO = Placement(
    CROSSROADS, "Ammo_Magnum_BP_C_1", (6033.949367709363, 10806.238708443801, 940.5129757964019),
    True, FORTY_FIVE_BALL, 10, 0.13, 50,
)
SLUMS_BUCKSHOT = Placement(
    SLUMS, "Ammo_ShotgunShells_BP_C_1", (-1803.0074007604399, -5600.062070282763, -313.7406382956242),
    True, TWELVE_GAUGE_BUCK, 14, 0.47600000000000003, 112,
)
FORT_LETO_BUCKSHOT = Placement(
    "/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P", "Ammo_ShotgunShells_BP_C_1",
    (-9828.90113131438, -30484.624521705286, -215.50793747703088),
    True, TWELVE_GAUGE_BUCK, 5, 0.17, 40,
)

PLACEMENTS = (CITY_AMMO, CROSSROADS_AMMO, SLUMS_BUCKSHOT, FORT_LETO_BUCKSHOT)

BY_SOURCE = {row.source: row for row in PLACEMENTS}
