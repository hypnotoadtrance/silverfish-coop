"""Exact cooked placed cash for the shared-wallet pickup adapter."""

from dataclasses import dataclass

from .travel import CITY, CROSSROADS


@dataclass(frozen=True)
class CashType:
    asset: str
    slug: str
    minimum: int
    maximum: int

    @property
    def class_name(self) -> str:
        return f"Item_Money_{self.asset}_C"

    @property
    def class_path(self) -> str:
        return f"/Game/Classes/Items/Item_Money_{self.asset}.{self.class_name}"


COUPLE_BILLS = CashType("CoupleBills", "couple-bills", 6, 10)
SMALL_STACK = CashType("SmallStack", "small-stack", 30, 50)
LARGE_STACK = CashType("LargeStack", "large-stack", 100, 200)
MASSIVE_PILE = CashType("MassivePile", "massive-pile", 3000, 5000)
CASH_TYPES = (COUPLE_BILLS, SMALL_STACK, LARGE_STACK, MASSIVE_PILE)
CASH_RANGES = {item.class_path: (item.minimum, item.maximum) for item in CASH_TYPES}
MAP_SLUGS = {CITY: "city", CROSSROADS: "crossroads"}


@dataclass(frozen=True)
class Placement:
    area: str
    item: CashType
    index: int
    position: tuple[float, float, float]
    physics: bool = True

    @property
    def short_map(self) -> str:
        return self.area.rsplit("/", 1)[-1]

    @property
    def world(self) -> str:
        return f"{self.area}.{self.short_map}"

    @property
    def name(self) -> str:
        return f"{self.item.class_name}_{self.index}"

    @property
    def path(self) -> str:
        return f"{self.world}:PersistentLevel.{self.name}"

    @property
    def actor(self) -> str:
        return f"{self.item.class_name} {self.path}"

    @property
    def stock_flag(self) -> str:
        return f"{self.name}-{self.short_map}-"

    @property
    def source(self) -> str:
        return f"{MAP_SLUGS[self.area]}-{self.item.slug}-{self.index}"

    def accepts(self, amount: object) -> bool:
        return type(amount) is int and self.item.minimum <= amount <= self.item.maximum


# Exact PersistentLevel actor and owned root Box exports. Physics=False is an
# authored override; remaining placements inherit the stock simulated Box.
PLACEMENTS = (
    Placement(CITY, COUPLE_BILLS, 1, (-1688.8408770404424, 1951.168040369893, 14.259371912756677)),
    Placement(CITY, COUPLE_BILLS, 2, (1973.169684362166, -384.4255761921981, 383.89994906314615)),
    Placement(CITY, COUPLE_BILLS, 3, (-1137.290149913368, 1170.5409029653736, -82.42728157169304)),
    Placement(CITY, COUPLE_BILLS, 4, (-1062.8368797369826, 1187.0482170620103, -82.4272819999999)),
    Placement(CITY, COUPLE_BILLS, 5, (-1198.4671328924414, -7190.603404008802, 681.827886661801)),
    Placement(CITY, COUPLE_BILLS, 6, (1218.652389739344, -3059.7953606194465, -12.403579212423011)),
    Placement(CITY, COUPLE_BILLS, 7, (-685.8361208569721, 4674.064705473622, 12.734350318836803)),
    Placement(CITY, COUPLE_BILLS, 8, (-727.2304047599307, 4674.064705, 12.734349193875403)),
    Placement(CITY, LARGE_STACK, 1, (5687.370872483183, -7166.022152962464, 379.74175825434787), False),
    Placement(CITY, LARGE_STACK, 3, (5312.29072597758, -6406.225403730188, -279.08132380123016)),
    Placement(CITY, LARGE_STACK, 4, (5275.169078754232, -6412.1580766213965, -279.0679575576436)),
    Placement(CITY, LARGE_STACK, 5, (5258.725306561679, -6417.7383758895, -279.067957974552)),
    Placement(CITY, LARGE_STACK, 6, (5296.013517545263, -6823.3805474880655, -279.08132339358104)),
    Placement(CITY, LARGE_STACK, 7, (5258.891870545263, -6829.313220488066, -279.067957393581)),
    Placement(CITY, LARGE_STACK, 8, (5242.448098545263, -6834.893519488066, -279.067957393581)),
    Placement(CITY, LARGE_STACK, 9, (5117.010701729354, -6462.7662770510915, -279.08132409315004)),
    Placement(CITY, LARGE_STACK, 10, (5146.192655907372, -6439.06760967781, -279.06795809315)),
    Placement(CITY, LARGE_STACK, 11, (5157.6432314347, -6426.013043633303, -279.06795809315)),
    Placement(CITY, LARGE_STACK, 12, (5265.533806327054, -6029.528289519862, -283.8636811246121)),
    Placement(CITY, LARGE_STACK, 13, (5231.212537473922, -6044.866600679456, -283.8503151246121)),
    Placement(CITY, LARGE_STACK, 14, (5216.773361036954, -6054.51271691033, -283.8503151246121)),
    Placement(CITY, LARGE_STACK, 15, (5487.679012823478, -8178.054237946729, 88.29315341652205), False),
    Placement(CITY, LARGE_STACK, 16, (5305.866354028056, -5659.508304541247, -284.6588322982928)),
    Placement(CITY, LARGE_STACK, 17, (5453.188415494115, -8186.440435025947, 88.29315300000002), False),
    Placement(CITY, MASSIVE_PILE, 1, (5275.344941568866, -6411.8685672669335, -208.96230675732383), False),
    Placement(CITY, MASSIVE_PILE, 3, (5132.368756458066, -6445.749121895525, -208.95252488273914), False),
    Placement(CITY, MASSIVE_PILE, 4, (5231.307477387081, -6044.541438800144, -213.74466412461214), False),
    Placement(CITY, MASSIVE_PILE, 5, (5266.958284154461, -5667.566300027263, -213.7446630988166), False),
    Placement(CITY, MASSIVE_PILE, 6, (5284.8608110380455, -5667.566299769902, -244.85432534806088), False),
    Placement(CITY, SMALL_STACK, 1, (5687.187900441295, -7077.066931410926, 379.46229019744527), False),
    Placement(CITY, SMALL_STACK, 3, (5232.62543577637, -6042.100610259222, -254.1819944376411)),
    Placement(CITY, SMALL_STACK, 4, (5499.201852154408, -6075.311174368379, 87.29315025765385)),
    Placement(CROSSROADS, SMALL_STACK, 1, (6061.128635763426, 10794.260062797128, 1100.845260643065)),
)
BY_SOURCE = {placement.source: placement for placement in PLACEMENTS}
