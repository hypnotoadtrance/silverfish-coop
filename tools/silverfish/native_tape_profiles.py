"""Exact stock download stations and their distinct typed inventory products."""

from dataclasses import dataclass

from .travel import SCIENTIST_SHOP, UTILITY_TUNNELS


BLANK_CLASS = "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"
STONE_MAZE = "/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P"
DRILL_QUEST = "Clt_DrillHead"
DATA_SILO = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"


@dataclass(frozen=True)
class TapeProfile:
    map: str
    product: str
    prefix: str
    ledger: str
    effect: str
    namespace: str
    requires_active_quest: bool = False
    allows_host: bool = False

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
DRILL = TapeProfile(
    SCIENTIST_SHOP, "/Game/Classes/Items/DataTapes/DataTape_DrillTestResults.DataTape_DrillTestResults_C",
    "native-drill-test-results", "native_drill_test_results_exchanges", "drill_test_results_tape",
    "silverfish-drill-test-results", allows_host=True,
)
INSTRUCTIONS = TapeProfile(
    DATA_SILO, "/Game/Classes/Items/DataTapes/DataTape_DrillInstructions.DataTape_DrillInstructions_C",
    "native-drill-instructions", "native_drill_instructions_exchanges", "drill_instructions_tape", "silverfish-drill-instructions",
)
BY_MAP = {profile.map: profile for profile in (STONE, PUMP, DRILL, INSTRUCTIONS)}
BY_STATION = {profile.station: profile for profile in BY_MAP.values()}
BY_PREFIX = {profile.prefix: profile for profile in BY_MAP.values()}
PREFLIGHTS = {profile.verb("preflight"): profile for profile in BY_MAP.values()}
CONVERSIONS = {profile.verb("convert"): profile for profile in BY_MAP.values()}
APPROACHES = {profile.verb("approach"): profile for profile in BY_MAP.values()}


def drill_download_unlocked(party: dict) -> bool:
    quests = party.get("native_quests")
    quest = quests.get(DRILL_QUEST) if isinstance(quests, dict) else None
    return (isinstance(quest, dict) and (quest.get("quest_id"), quest.get("step")) == (DRILL_QUEST, -10)
            and type(quest.get("mosby_drill_test")) is int and quest["mosby_drill_test"] == 1
            and quest.get("test_results") == {"station": DRILL.station, "class_path": DRILL.product})


def instructions_download_unlocked(party: dict) -> bool:
    from . import native_mindcore

    return native_mindcore.LEDGER in party and native_mindcore.valid_receipt(party, party.get("players", {}))
