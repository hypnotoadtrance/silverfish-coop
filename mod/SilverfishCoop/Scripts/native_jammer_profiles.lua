local function profile(suffix, quest_id, area, level, board_area, board_level, board_suffix, board_id,
    board_order, display_global, cash, penalty, faction)
    local map = "/Game/Classes/Maps/Maps_OverWorld/" .. area
    local world = map .. "." .. level
    local scope = world .. ":PersistentLevel."
    local board_map = "/Game/Classes/Maps/Maps_SafeZones/" .. board_area
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

-- Runtime UEnum values, in the exact authored row order.
local flood = profile("flood", "Jmr_FloodPlains", "RuinFeilds_P", "RuinFeilds_P",
    "Borough", "Borough", "3", 5, {0, 1, 2, 4, 5, 3}, false, 350, 60, 2)
local lake = profile("lake", "Jmr_Lake", "Lake_01_P", "Lake_01_P",
    "Shop_BlackMarket_01", "Shop_BlackMarket_01", "0", 1, {0, 1, 3}, true, 400, 80, 8)

local lenny = {
    tag = "native-lenny", error_tag = "stock_lenny_", quest_id = "Asn_Lenny_01",
    map = lake.map, world_path = lake.world_path, board_path = lake.board_path, board_name = lake.board_name,
    board_id = lake.board_id, global_contracts = true, board_order = {0, 1, 2, 7},
    cash = 2500, penalty = 300, difficulty = 4, factions = {8, 5},
    reward_rep = {[8] = 15, [5] = 50}, completion_flag = "LennyDead",
}

return {
    by_tag = {[flood.tag] = flood, [lake.tag] = lake, [lenny.tag] = lenny},
    by_map = {[flood.objective_map] = flood, [lake.objective_map] = lake},
}
