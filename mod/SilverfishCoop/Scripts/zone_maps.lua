local playtest_1 = "/Game/Classes/Maps/PlaytestMap_01"
local playtest_2 = "/Game/Classes/Maps/PlaytestMap_02"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local dead_bridge = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local cistern = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local gold_nexus = "/Game/Classes/Maps/Dreams/GoldNexus_P"
local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local exchange = "/Game/Classes/Maps/Maps_Dungeons/Exchange_01_P"
local lab_e206 = "/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P"
local lab_east_wing = "/Game/Classes/Maps/Maps_Dungeons/Lab_EastWing_P"
local old_pipes = "/Game/Classes/Maps/Maps_Dungeons/OldPipes_01_P"
local slums_tunnel = "/Game/Classes/Maps/Maps_Dungeons/Slums_Tunnel_01_P"
local science_tunnel = "/Game/Classes/Maps/Maps_Dungeons/Sci_Tunnel_01_P"
local subway = "/Game/Classes/Maps/Maps_Dungeons/Subway_01_P"
local utility_tunnels = "/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P"
local trash_pit = "/Game/Classes/Maps/Maps_OverWorld/TrashPit_P"
local rail_yard = "/Game/Classes/Maps/Maps_OverWorld/RailYard_P"
local ruin_fields = "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P"
local northeast_rail = "/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P"
local slums = "/Game/Classes/Maps/Maps_OverWorld/Slums_P"
local city = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
local scav_bar = "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P"
local shopkeep = "/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P"
local borough = "/Game/Classes/Maps/Maps_SafeZones/Borough"
local black_market = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
local scientist_shop = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local forest = "/Game/Classes/Maps/Maps_OverWorld/Forest_01_P"
local lake = "/Game/Classes/Maps/Maps_OverWorld/Lake_01_P"

local maps = {
    [playtest_1] = { fixture = "Level_Exit_BP_C_0", destination = playtest_2 },
    [playtest_2] = { fixture = "Level_Exit_BP_C_0", destination = playtest_1 },
    [crossroads] = {
        fixture = "Level_Exit_BP_C_275", destination = dead_bridge,
        entrance = "DB_CR", arrival = "LevelExit_Spawn_BP_C_275",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_1", destination = cistern, entrance = "CR_Cstrn" },
        door_arrivals = { CR_Cstrn = "Dungeon_TransitionDoor_BP_C_1" },
        other_arrivals = {
            Nexus_CrossRoads = "LevelExit_Spawn_BP_C_278", Obsv_Etr = "LevelExit_Spawn_BP_C_2",
            Depot_Cross_Rail = "LevelExit_Spawn_BP_C_1", CR_RF = "LevelExit_Spawn_BP_C_273",
            Cross_NE = "LevelExit_Spawn_BP_C_276",
            CR_Forest = "LevelExit_Spawn_BP_C_277", Lake_Cave = "LevelExit_Spawn_BP_C_274",
        },
        secondary = {
            fixture = "Level_Exit_BP_C_2", destination = observatory, entrance = "Obsv_Etr",
            probe_arrival = "LevelExit_Spawn_BP_C_2", probe_approach = -1,
        },
    },
    [dead_bridge] = {
        fixture = "Level_Exit_BP_C_3", destination = crossroads,
        entrance = "DB_CR", arrival = "LevelExit_Spawn_BP_C_4",
    },
    [cistern] = {
        entrance = "CR_Cstrn",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_1", destination = crossroads, entrance = "CR_Cstrn" },
        door_arrivals = { CR_Cstrn = "Dungeon_TransitionDoor_BP_C_1", Cistern_Sci = "Dungeon_TransitionDoor_BP_C_2" },
        other_arrivals = { Red_Tes = "LevelExit_Spawn_BP_C_353" },
    },
    [gold_nexus] = {
        fixture = "Level_Exit_BP_C_178", destination = crossroads,
        entrance = "Nexus_CrossRoads", player_start = "PlayerStart_1",
    },
    [observatory] = {
        fixture = "Level_Exit_BP_C_1", destination = crossroads,
        entrance = "Obsv_Etr", arrival = "LevelExit_Spawn_BP_C_1",
        probe_arrival = "LevelExit_Spawn_BP_C_1", probe_approach = -1,
    },
    [exchange] = { entrance = "PlayerStart_1", player_start = "PlayerStart_1" },
    ["/Game/Classes/Maps/Dreams/TheRook_P"] = { entrance = "PlayerStart_1", player_start = "PlayerStart_1" },
    ["/Game/Classes/Maps/Dreams/Pyroclast_P"] = { entrance = "PlayerStart_1", player_start = "PlayerStart_1" },
    ["/Game/Classes/Maps/Dreams/Slate_P"] = { entrance = "PlayerStart_1", player_start = "PlayerStart_1" },
    ["/Game/Classes/Maps/Dreams/BlackOcean_P"] = { entrance = "PlayerStart_1", player_start = "PlayerStart_1" },
    ["/Game/Classes/Maps/Dreams/Nothing_P"] = { entrance = "PlayerStart_1", player_start = "PlayerStart_1" },
    [lab_e206] = {
        entrance = "RY_Lab206",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_1", destination = rail_yard, entrance = "RY_Lab206" },
        other_arrivals = { Red_Tes = "LevelExit_Spawn_BP_C_181" },
    },
    [lab_east_wing] = {
        entrance = "RgEast_Flood",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_0", destination = ruin_fields, entrance = "RgEast_Flood" },
        other_arrivals = { Red_Tes = "LevelExit_Spawn_BP_C_8" },
    },
    [old_pipes] = {
        entrance = "NE_Pipe",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_0", destination = northeast_rail, entrance = "NE_Pipe" },
        other_arrivals = { Red_Tes = "LevelExit_Spawn_BP_C_5" },
    },
    [slums_tunnel] = {
        entrance = "Slums_GreenLine",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_1", destination = slums, entrance = "Slums_GreenLine" },
        other_arrivals = { Red_Tes = "LevelExit_Spawn_BP_C_98" },
    },
    [science_tunnel] = {
        entrance = "Cistern_Sci",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_0", destination = cistern, entrance = "Cistern_Sci" },
        other_arrivals = { Red_Tes = "LevelExit_Spawn_BP_C_5" },
    },
    [subway] = {
        entrance = "Subway",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_1", destination = city, entrance = "Subway" },
        other_arrivals = { Red_Tes = "LevelExit_Spawn_BP_C_202" },
    },
    [utility_tunnels] = {
        entrance = "Rail_Utility",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_0", destination = rail_yard, entrance = "Rail_Utility" },
        other_arrivals = { Red_Tes = "LevelExit_Spawn_BP_C_5" },
    },
    [trash_pit] = {
        fixture = "Level_Exit_BP_C_169", destination = northeast_rail, entrance = "M35_Maw",
        other_arrivals = { Red_Tes = "LevelExit_Spawn_BP_C_303" },
    },
    [rail_yard] = {
        fixture = "Level_Exit_BP_C_0", destination = crossroads, entrance = "Depot_Cross_Rail",
        door_arrivals = { RY_Lab206 = "Dungeon_TransitionDoor_BP_C_2", Rail_Utility = "Dungeon_TransitionDoor_BP_C_1" },
        other_arrivals = { Rail_City = "LevelExit_Spawn_BP_C_220", Slum_Depot = "LevelExit_Spawn_BP_C_611" },
    },
    [ruin_fields] = {
        fixture = "Level_Exit_BP_C_1", destination = crossroads, entrance = "CR_RF",
        door_arrivals = { RgEast_Flood = "Dungeon_TransitionDoor_BP_C_1", Market_Flood = "Dungeon_TransitionDoor_BP_C_2" },
        other_arrivals = { Flood_Beach = "LevelExit_Spawn_BP_C_198" },
    },
    [northeast_rail] = {
        fixture = "Level_Exit_BP_C_269", destination = crossroads, entrance = "Cross_NE",
        door_arrivals = { NE_Pipe = "Dungeon_TransitionDoor_BP_C_3", ShopKeep = "Dungeon_TransitionDoor_BP_C_2" },
        other_arrivals = { M35_Maw = "LevelExit_Spawn_BP_C_704" },
    },
    [slums] = {
        fixture = "Level_Exit_BP_C_0", destination = rail_yard, entrance = "Slum_Depot",
        door_arrivals = { Slums_GreenLine = "Dungeon_TransitionDoor_BP_C_1", Bar_Slums = "Dungeon_TransitionDoor_BP_C_2" },
        other_arrivals = { Slum_Refine = "LevelExit_Spawn_BP_C_1" },
    },
    [city] = {
        fixture = "Level_Exit_BP_C_1", destination = rail_yard, entrance = "Rail_City",
        door_arrivals = { Subway = "Dungeon_TransitionDoor_BP_C_2" },
        other_arrivals = { City_Borough = "LevelExit_Spawn_BP_C_388", Metro_Dock = "LevelExit_Spawn_BP_C_391" },
        secondary = {
            fixture = "Level_Exit_BP_C_388", destination = borough, entrance = "City_Borough",
            probe_arrival = "LevelExit_Spawn_BP_C_388", probe_approach = -1,
        },
    },
    [scav_bar] = {
        entrance = "Bar_Slums",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_2", destination = slums, entrance = "Bar_Slums" },
    },
    [shopkeep] = {
        entrance = "ShopKeep",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_0", destination = northeast_rail, entrance = "ShopKeep" },
    },
    [borough] = {
        fixture = "Level_Exit_BP_C_1", destination = city, entrance = "City_Borough",
        probe_arrival = "LevelExit_Spawn_BP_C_1", probe_approach = 1,
    },
    [black_market] = {
        entrance = "BlackMarket",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_0", destination = lake, entrance = "BlackMarket" },
    },
    [scientist_shop] = {
        entrance = "Market_Flood",
        door = { fixture = "Dungeon_TransitionDoor_BP_C_2", destination = ruin_fields, entrance = "Market_Flood" },
    },
    [forest] = {
        fixture = "Level_Exit_BP_C_1", destination = crossroads, entrance = "CR_Forest",
        probe_arrival = "LevelExit_Spawn_BP_C_1", probe_approach = 1, probe_right_axis = true,
    },
    [lake] = {
        fixture = "Level_Exit_BP_C_2", destination = crossroads, entrance = "Lake_Cave",
        door_arrivals = { BlackMarket = "Dungeon_TransitionDoor_BP_C_1" },
    },
    ["/Game/Classes/Maps/Maps_OverWorld/Beach_01_P"] = {
        fixture = "Level_Exit_BP_C_0", destination = ruin_fields, entrance = "Flood_Beach",
    },
    ["/Game/Classes/Maps/Maps_OverWorld/Docks_01_P"] = {
        fixture = "Level_Exit_BP_C_2", destination = city, entrance = "Metro_Dock",
    },
    ["/Game/Classes/Maps/Maps_Dungeons/Refinery_01_P"] = {
        fixture = "Level_Exit_BP_C_242", destination = slums, entrance = "Slum_Refine",
    },
}

for _, package in ipairs(require("authored_zone_maps")) do
    maps[package] = maps[package] or { expanded = true }
end

return maps
