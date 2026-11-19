"""Cooked normal-campaign routes for later live qualification and activation.

Each entry has one uniquely matched stock arrival. A door child export records
cooked identity. Live qualification must verify the named parent door's child
component and pointer, since runtime child names can differ.
"""

from dataclasses import dataclass
from typing import Literal

from .travel import GOLD_NEXUS


DOOR_TRAVEL_COMPONENT = "Level_Exit_BP"
DOOR_ARRIVAL_COMPONENT = "LevelExit_Spawn_BP"


@dataclass(frozen=True, slots=True)
class AuthoredRoute:
    """Identify a placed exit and its stock arrival in Unreal package names.

    On a door route, both actor fields identify placed doors. The child fields
    identify their corresponding cooked travel and arrival actors.
    """

    source: str
    destination: str
    entrance: str
    kind: Literal["overlap", "door"]
    actor: str
    arrival_actor: str
    travel_time_step: int | None = None
    child: str | None = None
    arrival_child: str | None = None
    arrival_entrance: str | None = None


_PACKAGES = {
    name: f"/Game/Classes/Maps/{folder}/{name}"
    for folder, names in (
        ("Maps_Dungeons", (
            "Anex_01_P", "Anom_Maze_01", "Cistern_P", "CrashSite_01_P", "DataSilo_01_P", "DryDock_P",
            "Factory_SB_P", "FortLeto_01_P", "Funnel_01_P", "Lab_E206_P", "Lab_EastWing_P", "OldPipes_01_P",
            "Perdition_01_P", "Refinery_01_P", "Sci_Tunnel_01_P", "Slums_Tunnel_01_P", "Subway_01_P", "TheThroat_P",
            "TutTnl_01_P", "Tut_Dock_P", "UtilityTunnels_01_P",
        )),
        ("Maps_OverWorld", (
            "Beach_01_P", "City_01_P", "CrossRoads_P", "DeadBridge_P", "DeadEnd_01_P", "Docks_01_P",
            "Forest_01_P", "Gorge_01_P", "Lake_01_P", "NorthEast_Rail_P", "Observitory_P", "PineRidge_P",
            "Quarry_01_P", "RailYard_P", "RuinFeilds_P", "SavloEstate_P", "Slums_P", "SouthEast_Road_P",
            "StoneMaze_P", "Swamp_01_P", "TheWall_01_P", "TrashPit_P", "Trench_01_P",
        )),
        ("Maps_SafeZones", (
            "ArtifactForge_P", "Borough", "Rev_NorthBase_P", "Shop_BlackMarket_01", "Shop_Craftsman_01",
            "Shop_Gunsmith_P", "Shop_ScavBar_P", "Shop_Scientist_01", "Shop_ShopKeep_P", "Shop_Smuggler_P",
        )),
    )
    for name in names
}
MAPS = frozenset(_PACKAGES.values())
ROUTE_MAPS = MAPS | {GOLD_NEXUS}


# Source and destination package names, entrance, exit actor, arrival marker,
# and the serialized Travel_TimeStep override if present.
_OVERLAPS = (
    ("Beach_01_P", "RuinFeilds_P", "Flood_Beach", "Level_Exit_BP_C_0", "LevelExit_Spawn_BP_C_198", 15),
    ("Beach_01_P", "Refinery_01_P", "Refine_Beach", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_241", 12),
    ("Borough", "City_01_P", "City_Borough", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_388", 2),
    ("Borough", "Rev_NorthBase_P", "RevTrain", "Level_Exit_BP_C_2", "LevelExit_Spawn_BP_C_2", 10),
    ("City_01_P", "RailYard_P", "Rail_City", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_220", 5),
    ("City_01_P", "Lake_01_P", "City_Lake", "Level_Exit_BP_C_387", "LevelExit_Spawn_BP_C_3", None),
    ("City_01_P", "Borough", "City_Borough", "Level_Exit_BP_C_388", "LevelExit_Spawn_BP_C_1", 2),
    ("City_01_P", "Docks_01_P", "Metro_Dock", "Level_Exit_BP_C_389", "LevelExit_Spawn_BP_C_2", 7),
    ("CrossRoads_P", "RailYard_P", "Depot_Cross_Rail", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_0", 20),
    ("CrossRoads_P", "Observitory_P", "Obsv_Etr", "Level_Exit_BP_C_2", "LevelExit_Spawn_BP_C_1", 15),
    ("CrossRoads_P", "RuinFeilds_P", "CR_RF", "Level_Exit_BP_C_273", "LevelExit_Spawn_BP_C_1", 20),
    ("CrossRoads_P", "NorthEast_Rail_P", "Cross_NE", "Level_Exit_BP_C_274", "LevelExit_Spawn_BP_C_269", 20),
    ("CrossRoads_P", "DeadBridge_P", "DB_CR", "Level_Exit_BP_C_275", "LevelExit_Spawn_BP_C_4", 15),
    ("CrossRoads_P", "Forest_01_P", "CR_Forest", "Level_Exit_BP_C_276", "LevelExit_Spawn_BP_C_1", 8),
    ("CrossRoads_P", "Lake_01_P", "Lake_Cave", "Level_Exit_BP_C_277", "LevelExit_Spawn_BP_C_2", 7),
    ("DeadBridge_P", "Lake_01_P", "DB_Lake", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_5", 2),
    ("DeadBridge_P", "StoneMaze_P", "DB_SM", "Level_Exit_BP_C_2", "LevelExit_Spawn_BP_C_720", 10),
    ("DeadBridge_P", "CrossRoads_P", "DB_CR", "Level_Exit_BP_C_3", "LevelExit_Spawn_BP_C_275", 15),
    ("DeadEnd_01_P", "NorthEast_Rail_P", "M35_End", "Level_Exit_BP_C_194", "LevelExit_Spawn_BP_C_706", 10),
    ("Docks_01_P", "Slums_P", "Slum_Dock", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_3", 7),
    ("Docks_01_P", "City_01_P", "Metro_Dock", "Level_Exit_BP_C_2", "LevelExit_Spawn_BP_C_391", 7),
    ("Forest_01_P", "CrossRoads_P", "CR_Forest", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_277", 8),
    ("Forest_01_P", "NorthEast_Rail_P", "M34_Forest", "Level_Exit_BP_C_2", "LevelExit_Spawn_BP_C_703", 12),
    ("Forest_01_P", "SouthEast_Road_P", "Goldwood_South", "Level_Exit_BP_C_3", "LevelExit_Spawn_BP_C_178", 3),
    ("Forest_01_P", "SavloEstate_P", "Manor_Forest", "Level_Exit_BP_C_4", "LevelExit_Spawn_BP_C_233", 10),
    ("Gorge_01_P", "Swamp_01_P", "Swamp_Scar", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_109", 15),
    ("Lake_01_P", "RailYard_P", "Depot_Lake", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_610", 2),
    ("Lake_01_P", "CrossRoads_P", "Lake_Cave", "Level_Exit_BP_C_2", "LevelExit_Spawn_BP_C_274", 7),
    ("Lake_01_P", "City_01_P", "City_Lake", "Level_Exit_BP_C_3", "LevelExit_Spawn_BP_C_387", None),
    ("Lake_01_P", "DeadBridge_P", "DB_Lake", "Level_Exit_BP_C_5", "LevelExit_Spawn_BP_C_2", 2),
    ("NorthEast_Rail_P", "CrossRoads_P", "Cross_NE", "Level_Exit_BP_C_269", "LevelExit_Spawn_BP_C_276", 20),
    ("NorthEast_Rail_P", "Forest_01_P", "M34_Forest", "Level_Exit_BP_C_703", "LevelExit_Spawn_BP_C_2", 12),
    ("NorthEast_Rail_P", "TrashPit_P", "M35_Maw", "Level_Exit_BP_C_704", "LevelExit_Spawn_BP_C_169", 8),
    ("NorthEast_Rail_P", "DeadEnd_01_P", "M35_End", "Level_Exit_BP_C_705", "LevelExit_Spawn_BP_C_194", 10),
    ("NorthEast_Rail_P", "SouthEast_Road_P", "M35_South", "Level_Exit_BP_C_706", "LevelExit_Spawn_BP_C_177", 3),
    ("Observitory_P", "CrossRoads_P", "Obsv_Etr", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_2", 15),
    ("Observitory_P", "Tut_Dock_P", "Obs_Dock", "Level_Exit_BP_C_213", "LevelExit_Spawn_BP_C_1", 10),
    ("PineRidge_P", "StoneMaze_P", "Stone_Pine", "Level_Exit_BP_C_245", "LevelExit_Spawn_BP_C_760", None),
    ("PineRidge_P", "Quarry_01_P", "Pine_Quarry", "Level_Exit_BP_C_246", "LevelExit_Spawn_BP_C_1", 10),
    ("Quarry_01_P", "PineRidge_P", "Pine_Quarry", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_246", 10),
    ("RailYard_P", "CrossRoads_P", "Depot_Cross_Rail", "Level_Exit_BP_C_0", "LevelExit_Spawn_BP_C_1", 20),
    ("RailYard_P", "City_01_P", "Rail_City", "Level_Exit_BP_C_220", "LevelExit_Spawn_BP_C_1", 5),
    ("RailYard_P", "RuinFeilds_P", "Ry_Rf", "Level_Exit_BP_C_221", "LevelExit_Spawn_BP_C_2", 8),
    ("RailYard_P", "Lake_01_P", "Depot_Lake", "Level_Exit_BP_C_610", "LevelExit_Spawn_BP_C_1", 2),
    ("RailYard_P", "Slums_P", "Slum_Depot", "Level_Exit_BP_C_611", "LevelExit_Spawn_BP_C_0", 10),
    ("Refinery_01_P", "Beach_01_P", "Refine_Beach", "Level_Exit_BP_C_241", "LevelExit_Spawn_BP_C_1", 12),
    ("Refinery_01_P", "Slums_P", "Slum_Refine", "Level_Exit_BP_C_242", "LevelExit_Spawn_BP_C_1", 8),
    ("Rev_NorthBase_P", "Swamp_01_P", "Swamp_Rev", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_2", 15),
    ("Rev_NorthBase_P", "Borough", "RevTrain", "Level_Exit_BP_C_4", "LevelExit_Spawn_BP_C_2", 10),
    ("RuinFeilds_P", "CrossRoads_P", "CR_RF", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_273", 20),
    ("RuinFeilds_P", "Beach_01_P", "Flood_Beach", "Level_Exit_BP_C_198", "LevelExit_Spawn_BP_C_2", 15),
    ("RuinFeilds_P", "RailYard_P", "Ry_Rf", "Level_Exit_BP_C_2", "LevelExit_Spawn_BP_C_221", 8),
    ("SavloEstate_P", "Forest_01_P", "Manor_Forest", "Level_Exit_BP_C_231", "LevelExit_Spawn_BP_C_4", 10),
    ("SavloEstate_P", "SouthEast_Road_P", "Manor_SB", "Level_Exit_BP_C_232", "LevelExit_Spawn_BP_C_179", 10),
    ("Slums_P", "RailYard_P", "Slum_Depot", "Level_Exit_BP_C_0", "LevelExit_Spawn_BP_C_611", 10),
    ("Slums_P", "Refinery_01_P", "Slum_Refine", "Level_Exit_BP_C_3", "LevelExit_Spawn_BP_C_242", 8),
    ("Slums_P", "Docks_01_P", "Slum_Dock", "Level_Exit_BP_C_5", "LevelExit_Spawn_BP_C_1", 7),
    ("Slums_Tunnel_01_P", "Subway_01_P", "Subway_Slums", "Level_Exit_BP_C_97", "LevelExit_Spawn_BP_C_201", 20),
    ("SouthEast_Road_P", "NorthEast_Rail_P", "M35_South", "Level_Exit_BP_C_177", "LevelExit_Spawn_BP_C_707", 3),
    ("SouthEast_Road_P", "Forest_01_P", "Goldwood_South", "Level_Exit_BP_C_178", "LevelExit_Spawn_BP_C_3", 3),
    ("SouthEast_Road_P", "SavloEstate_P", "Manor_SB", "Level_Exit_BP_C_179", "LevelExit_Spawn_BP_C_231", 10),
    ("StoneMaze_P", "DeadBridge_P", "DB_SM", "Level_Exit_BP_C_720", "LevelExit_Spawn_BP_C_1", 10),
    ("StoneMaze_P", "Swamp_01_P", "SM_Swamp", "Level_Exit_BP_C_722", "LevelExit_Spawn_BP_C_1", 12),
    ("StoneMaze_P", "PineRidge_P", "Stone_Pine", "Level_Exit_BP_C_760", "LevelExit_Spawn_BP_C_245", None),
    ("Subway_01_P", "Slums_Tunnel_01_P", "Subway_Slums", "Level_Exit_BP_C_201", "LevelExit_Spawn_BP_C_97", None),
    ("Swamp_01_P", "StoneMaze_P", "SM_Swamp", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_722", 12),
    ("Swamp_01_P", "Gorge_01_P", "Swamp_Scar", "Level_Exit_BP_C_109", "LevelExit_Spawn_BP_C_1", 15),
    ("Swamp_01_P", "Rev_NorthBase_P", "Swamp_Rev", "Level_Exit_BP_C_2", "LevelExit_Spawn_BP_C_1", 15),
    ("TrashPit_P", "NorthEast_Rail_P", "M35_Maw", "Level_Exit_BP_C_169", "LevelExit_Spawn_BP_C_704", 8),
)


# The Golden Cube reaches this hub through its own inventory transaction.
# These stock pads qualify departures only, leaving ordinary campaign starts unchanged.
_GOLD_NEXUS_PADS = (
    ("CrossRoads_P", "Nexus_CrossRoads", "Level_Exit_BP_C_178", "LevelExit_Spawn_BP_C_278"),
    ("NorthEast_Rail_P", "Nexus_M35", "Level_Exit_BP_C_179", "LevelExit_Spawn_BP_C_705"),
    ("Swamp_01_P", "Nexus_Swamp", "Level_Exit_BP_C_180", "LevelExit_Spawn_BP_C_3"),
    ("CrossRoads_P", "Nexus_CrossRoads", "Level_Exit_BP_C_181", "LevelExit_Spawn_BP_C_278"),
    ("CrossRoads_P", "Nexus_CrossRoads", "Level_Exit_BP_C_182", "LevelExit_Spawn_BP_C_278"),
    ("Slums_P", "Nexus_Slums", "Level_Exit_BP_C_183", "LevelExit_Spawn_BP_C_287"),
    ("Beach_01_P", "Nexus_Beach", "Level_Exit_BP_C_184", "LevelExit_Spawn_BP_C_139"),
)


# Source and destination package names, entrance, source door, destination door,
# and the exact cooked travel and arrival child export names, respectively.
_DOORS = (
    ("Anex_01_P", "TheWall_01_P", "Wall_Annex", "Dungeon_TransitionDoor_BP_C_0", "Dungeon_TransitionDoor_BP_C_3",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_364"),
    ("Anex_01_P", "StoneMaze_P", "Maze_Annex", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_4",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_1", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_750"),
    ("Anex_01_P", "Trench_01_P", "Annex_Trench", "Dungeon_TransitionDoor_BP_C_3", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_93", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_10"),
    ("Anex_01_P", "ArtifactForge_P", "Anex_Forge", "Dungeon_TransitionDoor_BP_C_4", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_638", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_188"),
    ("Anom_Maze_01", "NorthEast_Rail_P", "M35_Warren", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_158", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_264"),
    ("ArtifactForge_P", "Anex_01_P", "Anex_Forge", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_4",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_188", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_638"),
    ("Beach_01_P", "FortLeto_01_P", "Beach_Leto", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_134", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_221"),
    ("Cistern_P", "CrossRoads_P", "CR_Cstrn", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_178", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_268"),
    ("Cistern_P", "Sci_Tunnel_01_P", "Cistern_Sci", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_0",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_346", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("City_01_P", "Subway_01_P", "Subway", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_380", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_196"),
    ("CrashSite_01_P", "TutTnl_01_P", "Crash_Tut", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_150", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_186"),
    ("CrossRoads_P", "Cistern_P", "CR_Cstrn", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_268", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_178"),
    ("DataSilo_01_P", "TheWall_01_P", "Wall_Data", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_0",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_286", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_246"),
    ("DeadEnd_01_P", "Perdition_01_P", "Haunt_DeadEnd", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_190", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_142"),
    ("Docks_01_P", "DryDock_P", "Docks_DD", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_134", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_4"),
    ("Docks_01_P", "Shop_ScavBar_P", "Bar_Docks", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_536", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("DryDock_P", "Refinery_01_P", "Refine_DD", "Dungeon_TransitionDoor_BP_C_0", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_236"),
    ("DryDock_P", "Docks_01_P", "Docks_DD", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_4", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_134"),
    ("Factory_SB_P", "SouthEast_Road_P", "SB_Factory", "Dungeon_TransitionDoor_BP_C_0", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_52"),
    ("FortLeto_01_P", "Beach_01_P", "Beach_Leto", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_221", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_134"),
    ("Funnel_01_P", "Trench_01_P", "Trench_Funnel", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_4",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_358", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_126"),
    ("Lab_E206_P", "RailYard_P", "RY_Lab206", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_176", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_601"),
    ("Lab_EastWing_P", "RuinFeilds_P", "RgEast_Flood", "Dungeon_TransitionDoor_BP_C_0", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_136"),
    ("Lab_EastWing_P", "Shop_Scientist_01", "RgIn", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_4",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_1", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_588"),
    ("Lake_01_P", "Shop_BlackMarket_01", "BlackMarket", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_0",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_50", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_284"),
    ("NorthEast_Rail_P", "Anom_Maze_01", "M35_Warren", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_264", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_158"),
    ("NorthEast_Rail_P", "Shop_ShopKeep_P", "ShopKeep", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_0",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_476", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("NorthEast_Rail_P", "OldPipes_01_P", "NE_Pipe", "Dungeon_TransitionDoor_BP_C_3", "Dungeon_TransitionDoor_BP_C_0",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_694", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("Observitory_P", "TutTnl_01_P", "Tut_Obsrv", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_208", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_402"),
    ("OldPipes_01_P", "NorthEast_Rail_P", "NE_Pipe", "Dungeon_TransitionDoor_BP_C_0", "Dungeon_TransitionDoor_BP_C_3",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_694"),
    ("Perdition_01_P", "DeadEnd_01_P", "Haunt_DeadEnd", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_142", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_190"),
    ("Perdition_01_P", "StoneMaze_P", "Haunt_Stone", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_238", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_138"),
    ("PineRidge_P", "Shop_Smuggler_P", "Pine_Fence", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_240", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("RailYard_P", "UtilityTunnels_01_P", "Rail_Utility", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_0",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_212", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("RailYard_P", "Lab_E206_P", "RY_Lab206", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_601", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_176"),
    ("Refinery_01_P", "DryDock_P", "Refine_DD", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_0",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_236", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("RuinFeilds_P", "Lab_EastWing_P", "RgEast_Flood", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_0",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_136", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("RuinFeilds_P", "Shop_Scientist_01", "Market_Flood", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_191", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_5"),
    ("Sci_Tunnel_01_P", "Cistern_P", "Cistern_Sci", "Dungeon_TransitionDoor_BP_C_0", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_346"),
    ("Shop_BlackMarket_01", "Lake_01_P", "BlackMarket", "Dungeon_TransitionDoor_BP_C_0", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_284", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_50"),
    ("Shop_Craftsman_01", "StoneMaze_P", "ShopCraft", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_711"),
    ("Shop_Gunsmith_P", "SouthEast_Road_P", "South_Gunsmith", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_270", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_170"),
    ("Shop_ScavBar_P", "Docks_01_P", "Bar_Docks", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_536"),
    ("Shop_ScavBar_P", "Slums_P", "Bar_Slums", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_186", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_280"),
    ("Shop_Scientist_01", "RuinFeilds_P", "Market_Flood", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_5", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_191"),
    ("Shop_Scientist_01", "Lab_EastWing_P", "RgIn", "Dungeon_TransitionDoor_BP_C_4", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_588", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_1"),
    ("Shop_ShopKeep_P", "NorthEast_Rail_P", "ShopKeep", "Dungeon_TransitionDoor_BP_C_0", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_476"),
    ("Shop_Smuggler_P", "PineRidge_P", "Pine_Fence", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_240"),
    ("Slums_P", "Slums_Tunnel_01_P", "Slums_GreenLine", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_152", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_92"),
    ("Slums_P", "Shop_ScavBar_P", "Bar_Slums", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_280", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_186"),
    ("Slums_Tunnel_01_P", "Slums_P", "Slums_GreenLine", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_92", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_152"),
    ("SouthEast_Road_P", "Factory_SB_P", "SB_Factory", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_0",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_52", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("SouthEast_Road_P", "Shop_Gunsmith_P", "South_Gunsmith", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_170", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_270"),
    ("StoneMaze_P", "Perdition_01_P", "Haunt_Stone", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_138", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_238"),
    ("StoneMaze_P", "Shop_Craftsman_01", "ShopCraft", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_711", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("StoneMaze_P", "Anex_01_P", "Maze_Annex", "Dungeon_TransitionDoor_BP_C_4", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_750", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_1"),
    ("Subway_01_P", "City_01_P", "Subway", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_196", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_380"),
    ("Swamp_01_P", "TheWall_01_P", "Swamp_Wall", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_104", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_238"),
    ("TheThroat_P", "TrashPit_P", "Maw_Throat", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_188", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_296"),
    ("TheWall_01_P", "DataSilo_01_P", "Wall_Data", "Dungeon_TransitionDoor_BP_C_0", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_246", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_286"),
    ("TheWall_01_P", "Swamp_01_P", "Swamp_Wall", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_238", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_104"),
    ("TheWall_01_P", "Anex_01_P", "Wall_Annex", "Dungeon_TransitionDoor_BP_C_3", "Dungeon_TransitionDoor_BP_C_0",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_364", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0"),
    ("TrashPit_P", "TheThroat_P", "Maw_Throat", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_296", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_188"),
    ("Trench_01_P", "Anex_01_P", "Annex_Trench", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_3",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_10", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_93"),
    ("Trench_01_P", "Funnel_01_P", "Trench_Funnel", "Dungeon_TransitionDoor_BP_C_4", "Dungeon_TransitionDoor_BP_C_2",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_126", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_358"),
    ("TutTnl_01_P", "CrashSite_01_P", "Crash_Tut", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_186", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_150"),
    ("TutTnl_01_P", "Observitory_P", "Tut_Obsrv", "Dungeon_TransitionDoor_BP_C_2", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_402", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_208"),
    ("UtilityTunnels_01_P", "RailYard_P", "Rail_Utility", "Dungeon_TransitionDoor_BP_C_0", "Dungeon_TransitionDoor_BP_C_1",
     "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0", "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_212"),
)


AUTHORED_ROUTES = (
    *(
        AuthoredRoute(_PACKAGES[source], _PACKAGES[destination], entrance, "overlap", actor, marker, time_step)
        for source, destination, entrance, actor, marker, time_step in _OVERLAPS
    ),
    *(
        AuthoredRoute(GOLD_NEXUS, _PACKAGES[destination], entrance, "overlap", actor, marker, travel_time_step=1)
        for destination, entrance, actor, marker in _GOLD_NEXUS_PADS
    ),
    # The stock Observatory marker adds a trailing e to the dock's tag.
    AuthoredRoute(_PACKAGES["Tut_Dock_P"], _PACKAGES["Observitory_P"], "Obs_Dock", "overlap",
                  "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_213", travel_time_step=10, arrival_entrance="Obs_Docke"),
    *(
        AuthoredRoute(_PACKAGES[source], _PACKAGES[destination], entrance, "door", actor, marker,
                      child=child, arrival_child=arrival_child)
        for source, destination, entrance, actor, marker, child, arrival_child in _DOORS
    ),
)
