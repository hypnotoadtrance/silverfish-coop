"""Cooked placements qualified for the stock loose-supply pickup adapter."""

from dataclasses import dataclass
import hashlib

from .guest_weapon_profiles import FORTY_FIVE_BALL, TWELVE_GAUGE_BUCK, StockAmmo
from .travel import CITY, CROSSROADS, LAKE, RAIL_YARD, SLUMS


BANDAGE_CLASS = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
CRASH_SITE = "/Game/Classes/Maps/Maps_Dungeons/CrashSite_01_P"


@dataclass(frozen=True)
class Placement:
    area: str
    name: str
    position: tuple[float, float, float]
    physics: bool
    ammo: StockAmmo | None
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
        return f"{self.item_name}_C {self.path}"

    @property
    def item_name(self) -> str:
        return self.ammo.item if self.ammo else "Item_Bandage"

    @property
    def class_path(self) -> str:
        return self.ammo.item_path if self.ammo else BANDAGE_CLASS

    @property
    def source(self) -> str:
        identity = f"{self.path}|{self.class_path}".encode("ascii")
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

# The normal campaign Bandage placements inherit stock mode 2, one unit, and
# the stock class value and weight. The tutorial has different depletion rules.
RAIL_YARD_BANDAGE = Placement(
    RAIL_YARD, "Item_Bandage_C_1", (-7189.73851918272, -36461.79354376452, 160.51892140605574),
    False, None, 1, 0.05, 15,
)
SLUMS_BANDAGE = Placement(
    SLUMS, "Item_Bandage_C_1", (-1893.335326600245, -5427.637673340628, -292.6253999462081),
    True, None, 1, 0.05, 15,
)
BANDAGE_PLACEMENTS = (
    RAIL_YARD_BANDAGE,
    SLUMS_BANDAGE,
    Placement(LAKE, "Item_Bandage_C_1", (-5260.440244190542, 20110.94109353449, -86.63647534294618),
              True, None, 1, 0.05, 15),
    Placement(CRASH_SITE, "Item_Bandage_C_1", (572.6393573095291, -130.9838304340619, 238.39423903764475),
              False, None, 1, 0.05, 15),
    Placement(CRASH_SITE, "Item_Bandage_C_2", (393.88334018581713, 195.40449749580102, 203.01003643195543),
              False, None, 1, 0.05, 15),
)

BY_SOURCE = {row.source: row for row in (*PLACEMENTS, *BANDAGE_PLACEMENTS)}
