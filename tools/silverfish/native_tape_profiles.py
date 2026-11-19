"""Exact stock download stations and their distinct typed inventory products."""

from dataclasses import dataclass

from .travel import UTILITY_TUNNELS


BLANK_CLASS = "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"
STONE_MAZE = "/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P"


@dataclass(frozen=True)
class TapeProfile:
    map: str
    product: str
    prefix: str
    ledger: str
    effect: str
    namespace: str
    requires_active_quest: bool = False

    @property
    def station(self):
        level = self.map.rsplit("/", 1)[-1]
        return f"DataTape_DownloadStation_BP_C {self.map}.{level}:PersistentLevel.DataTape_DownloadStation_BP_C_1"

    @property
    def key(self):
        return self.prefix.replace("-", "_")

    def verb(self, action):
        return f"{self.prefix}-{action}"


STONE = TapeProfile(
    STONE_MAZE, "/Game/Classes/Items/DataTapes/DataTape_StoneMaze_01.DataTape_StoneMaze_01_C",
    "native-mosby-tape", "native_mosby_tape_exchanges", "stone_maze_tape", "silverfish-stone-maze", True,
)
PUMP = TapeProfile(
    UTILITY_TUNNELS, "/Game/Classes/Items/DataTapes/DataTape_EatherPumps.DataTape_EatherPumps_C",
    "native-pump-tape", "native_pump_tape_exchanges", "eather_pump_tape", "silverfish-eather-pump",
)
BY_MAP = {profile.map: profile for profile in (STONE, PUMP)}
BY_STATION = {profile.station: profile for profile in BY_MAP.values()}
BY_PREFIX = {profile.prefix: profile for profile in BY_MAP.values()}
PREFLIGHTS = {profile.verb("preflight"): profile for profile in BY_MAP.values()}
CONVERSIONS = {profile.verb("convert"): profile for profile in BY_MAP.values()}
APPROACHES = {profile.verb("approach"): profile for profile in BY_MAP.values()}
