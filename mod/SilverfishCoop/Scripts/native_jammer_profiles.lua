local function profile(suffix, quest_id, area, level, board_area, board_level, board_suffix, board_id,
    board_order, display_global, cash, penalty, faction, board_region)
    local map = "/Game/Classes/Maps/Maps_OverWorld/" .. area
    local world = map .. "." .. level
    local scope = world .. ":PersistentLevel."
    local board_map = "/Game/Classes/Maps/" .. (board_region or "Maps_SafeZones") .. "/" .. board_area
    local board_world = board_map .. "." .. board_level
    local board_path = board_world .. ":PersistentLevel.BountyBoard_BP_C_" .. board_suffix
    return {
        tag = "native-" .. suffix .. "-jammer", error_tag = "stock_" .. suffix .. "_jammer_",
        quest_id = quest_id, objective_map = map, objective_world = world,
        jammer_path = scope .. "SignalJammer_BP_C_1", jammer_name = "SignalJammer_BP_C " .. scope .. "SignalJammer_BP_C_1",
        trigger_name = "Quest_Trigger_BP_C " .. scope .. "Quest_Trigger_BP_C_1",
        map = board_map, world_path = board_world, board_path = board_path,
        board_name = "BountyBoard_BP_C " .. board_path, board_id = board_id, global_contracts = display_global,
        board_order = board_order, cash = cash, penalty = penalty,
        factions = {faction, 5}, reward_rep = {[faction] = 50, [5] = -40},
    }
end

local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local simmi_world = observatory .. ".Observitory_P"
local simmi_board = simmi_world .. ":PersistentLevel.BountyBoard_BP_C_0"
local simmi = {path = simmi_world .. ":PersistentLevel.NPC_Simmi_C_1",
    class = "/Game/Classes/NPCs/NPC_Characters/NPC_Simmi.NPC_Simmi_C"}
local function at_simmi(contract)
    local result = {}
    for key, value in pairs(contract) do result[key] = value end
    result.map, result.world_path = observatory, simmi_world
    result.board_path, result.board_name = simmi_board, "BountyBoard_BP_C " .. simmi_board
    result.board_id, result.global_contracts = 3, true
    result.speaker = simmi
    return result
end

-- Runtime UEnum values, in the exact authored row order.
local flood = profile("flood", "Jmr_FloodPlains", "RuinFeilds_P", "RuinFeilds_P",
    "Borough", "Borough", "3", 5, {0, 1, 2, 4, 5, 3}, false, 350, 60, 2)
local lake = profile("lake", "Jmr_Lake", "Lake_01_P", "Lake_01_P",
    "Shop_BlackMarket_01", "Shop_BlackMarket_01", "0", 1, {0, 1, 3}, true, 400, 80, 8)
lake.speaker = {path = lake.world_path .. ":PersistentLevel.NPC_Broker_C_1",
    class = "/Game/Classes/NPCs/NPC_Characters/NPC_Broker.NPC_Broker_C"}
local crossroads = profile("crossroads", "Jmr_CrossRoads", "CrossRoads_P", "CrossRoads_P",
    "Observitory_P", "Observitory_P", "0", 3, {0, 1, 2, 4, 3}, true, 200, 50, 8, "Maps_OverWorld")
crossroads.reward_rep = {[8] = 30, [5] = -30}
crossroads.speaker = simmi

local lenny = {
    tag = "native-lenny", error_tag = "stock_lenny_", quest_id = "Asn_Lenny_01",
    map = lake.map, world_path = lake.world_path, board_path = lake.board_path, board_name = lake.board_name,
    board_id = lake.board_id, global_contracts = true, board_order = {0, 1, 2, 7},
    cash = 2500, penalty = 300, difficulty = 4, factions = {8, 5},
    reward_rep = {[8] = 15, [5] = 50}, completion_flag = "LennyDead",
}
local spratt = {
    tag = "native-spratt", error_tag = "stock_spratt_", quest_id = "Asn_Spratt", title = "Kill Spratt",
    map = lake.map, world_path = lake.world_path, board_path = lake.board_path, board_name = lake.board_name,
    board_id = lake.board_id, global_contracts = true, board_order = {0, 1, 2, 7},
    cash = 5000, penalty = 500, difficulty = 7, xp_fixture = 7, factions = {8, 5},
    reward_rep = {[8] = 30, [5] = 50},
}

return {
    by_tag = {[flood.tag] = flood, [lake.tag] = lake, [crossroads.tag] = crossroads, [lenny.tag] = lenny, [spratt.tag] = spratt},
    by_map = {[flood.objective_map] = flood, [lake.objective_map] = lake, [crossroads.objective_map] = crossroads},
    alternate = {[flood.tag] = at_simmi(flood), [lake.tag] = at_simmi(lake)},
    observatory_bounty_board = "BountyBoard_BP_C " .. simmi_world .. ":PersistentLevel.BountyBoard_BP_C_2",
}
