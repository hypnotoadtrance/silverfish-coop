local playtest_1 = "/Game/Classes/Maps/PlaytestMap_01"
local playtest_2 = "/Game/Classes/Maps/PlaytestMap_02"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local dead_bridge = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local cistern = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"

return {
    [playtest_1] = { fixture = "Level_Exit_BP_C_0", destination = playtest_2 },
    [playtest_2] = { fixture = "Level_Exit_BP_C_0", destination = playtest_1 },
    [crossroads] = {
        fixture = "Level_Exit_BP_C_275", destination = dead_bridge,
        entrance = "DB_CR", arrival = "LevelExit_Spawn_BP_C_275",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_1", destination = cistern, entrance = "CR_Cstrn" },
    },
    [dead_bridge] = {
        fixture = "Level_Exit_BP_C_3", destination = crossroads,
        entrance = "DB_CR", arrival = "LevelExit_Spawn_BP_C_4",
    },
    [cistern] = {
        entrance = "CR_Cstrn",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_1", destination = crossroads, entrance = "CR_Cstrn" },
    },
}
