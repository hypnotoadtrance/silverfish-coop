local board_scope = require("native_wire_quest")
local rewards = require("native_wire_rewards")
local contract = {}

local map = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
local world_path = map .. ".Shop_BlackMarket_01"
local board_path = world_path .. ":PersistentLevel.BountyBoard_BP_C_0"
local quest_id = "MeetBroker"
local profile = {map = map, world_path = world_path, board_path = board_path,
    board_name = "BountyBoard_BP_C " .. board_path, board_id = 1, quest_id = quest_id, global_contracts = true}
local weapon_path = "/Game/Classes/Weapons/Wep_Pistol_Auto.Wep_Pistol_Auto_C"
local ammo_path = "/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
local library_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local preflight, start, turn_in = "native-meet-broker-preflight", "native-meet-broker-start", "native-meet-broker-turn-in"
local approach = "native-meet-broker-approach"
local capture, grant = "native-meet-broker-capture", "native-meet-broker-grant"
local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_quantity = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local last, last_reward

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end

local function refuse(reason)
    error("stock_meet_broker_" .. reason, 0)
end

local function board_check(callback, ...)
    local ok, value, extra = pcall(callback, ...)
    if ok then return value, extra end
    local label = type(value) == "string" and value:match("^stock_wire_quest_([%w_]+)$")
    if label then refuse(label) end
    error(value, 0)
end

local function count(array)
    return array and array:GetArrayNum()
end

local function recipe(row)
    local weapon, ammo = StaticFindObject(weapon_path), StaticFindObject(ammo_path)
    if not valid(weapon) or not valid(ammo) or weapon:GetFullName() ~= "BlueprintGeneratedClass " .. weapon_path
        or ammo:GetFullName() ~= "BlueprintGeneratedClass " .. ammo_path or not row then refuse("stock_recipe_unavailable") end
    local offers = row.AppearOn_Boards_95_2FDB27AE4695B9213302E5897144A531
    local items = row.Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A
    local ping = row.GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F
    if type(ping) ~= "string" then ping = ping and ping:ToString() end
    if row.Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B ~= 1
        or row.ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333 ~= false
        or count(row.Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083) ~= 0
        or row.Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0 ~= 0
        or row.Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797 ~= 1
        or count(items) ~= 2 or not same(items[1], weapon) or not same(items[2], ammo)
        or row.QuestType_47_1031681D471E9929E30B17985E174BBE ~= 1
        or row.Difficulty_12_9F1A01AD46151E4F5F152B82C99B5D85 ~= 0
        or row.AppearOn_GlobalBountyBoards_78_B538291F4816D684913EAA8852507698 ~= false
        or count(offers) ~= 2 or offers[1] ~= 1 or offers[2] ~= 3
        or row.RedeemAt_83_FAE34B6C471D2019508A4898E9A0080F ~= 1
        or not row.PreReq_Flags_105_3FA9C82A48BE1675F41E15A54570DBAD
        or #row.PreReq_Flags_105_3FA9C82A48BE1675F41E15A54570DBAD ~= 0
        or not row.Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F
        or #row.Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F ~= 0
        or not row.ReputationGain_86_DA02B32344A59A6C236B848B22520EEC
        or #row.ReputationGain_86_DA02B32344A59A6C236B848B22520EEC ~= 0
        or count(row.Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9) ~= 0
        or (ping ~= nil and ping ~= "" and ping ~= "None") then refuse("stock_recipe_changed") end
    return weapon, ammo
end

local function progress(context)
    local row = board_check(board_scope.stock_row, context.save.ActiveQuests, context.key)
    local step = row and row[step_field] or "-"
    if row then recipe(row) end
    if step ~= "-" and step ~= 1 and step ~= -10 then refuse("quest_step_unqualified") end
    return {step = step,
        cash = board_check(board_scope.stock_flag, context.instance, "Player_Cash"),
        xp = board_check(board_scope.stock_flag, context.instance, "Player_Xp")}
end

local function unchanged(context)
    return valid(context.board) and same(context.instance.SaveSlot_Ref, context.save)
        and same(context.host.Save, context.save) and same(context.interactor.Save, context.receiving_save)
end

local function guid(item)
    local words = item[item_guid]
    if not words then return end
    local a, b, c, d = math.tointeger(words.A), math.tointeger(words.B), math.tointeger(words.C), math.tointeger(words.D)
    if not a or not b or not c or not d then return end
    local text = string.format("%08X%08X%08X%08X", a & 0xffffffff, b & 0xffffffff, c & 0xffffffff, d & 0xffffffff)
    return text:find("[^0]") and text or nil
end

local function inventory(save, weapon, ammo)
    local weapons, units, identities = {}, 0, {}
    for _, array in ipairs({save.PlayerBackpack, save.PlayerEquipment}) do
        local length = count(array)
        if not length or length < 0 or length > 4096 then refuse("inventory_extent") end
        for index = 1, length do
            local row = array[index]
            local class = row and row[item_class]
            if valid(class) then
                local id = guid(row)
                if id then identities[id] = (identities[id] or 0) + 1 end
                if same(class, weapon) then
                    if not id or weapons[id] then refuse("weapon_identity") end
                    weapons[id] = true
                elseif same(class, ammo) then
                    local quantity = math.tointeger(row[item_quantity])
                    if not id or not quantity or quantity <= 0 or units + quantity > 2147483647 then
                        refuse("reserve_unqualified")
                    end
                    units = units + quantity
                end
            end
        end
    end
    return weapons, units, identities
end

local function redeem(context, before, weapon, ammo)
    if type(SilverfishCaptureMeetBrokerRewards) ~= "function"
        or type(SilverfishVerifyMeetBrokerRewards) ~= "function"
        or type(SilverfishGrantMeetBrokerRewards) ~= "function" then refuse("typed_rewards_unavailable") end
    if before.step ~= 1 or before.xp >= 2147483647 then refuse("quest_not_ready") end
    local library = StaticFindObject(library_path)
    if not valid(library) or library:GetFullName() ~= "FL_CodeLib01_C " .. library_path then
        refuse("turn_in_library_unavailable")
    end
    local old_weapons, old_units, old_ids = inventory(context.save, weapon, ammo)
    local output = {}
    local checked, after, identity = pcall(function()
        library:TurnIn_Quest(context.key, context.instance, true, context.host, output, output)
        local observed = progress(context)
        if output.Success ~= true or valid(output.ConfirmationPopup) or not unchanged(context)
            or observed.step ~= -10 or observed.cash ~= before.cash or observed.xp ~= before.xp + 1 then return end
        local weapons, units, ids = inventory(context.save, weapon, ammo)
        local added
        for id in pairs(weapons) do
            if not old_weapons[id] then
                if added or old_ids[id] or ids[id] ~= 1 then return end
                added = id
            end
        end
        for id in pairs(old_weapons) do if not weapons[id] then return end end
        if added and units - old_units == 9 then return observed, added end
    end)
    if not checked or not after or not identity then error("mutation_unverified_stock_meet_broker_turn_in", 0) end
    return after, identity
end

function contract.command(fields, state, phase)
    local operation = fields[2]
    if operation == capture or operation == grant then
        last_reward = nil
        if #fields ~= (operation == capture and 4 or 8) then refuse("reward_arguments") end
        if operation == capture then
            last_reward = rewards.capture(state, phase, fields[3], fields[4], "meet_broker")
        else
            last_reward = rewards.grant(state, phase, fields[3], fields[4], fields[5], fields[6], fields[7], fields[8], "meet_broker")
        end
        return "stock MeetBroker reward verified"
    end
    if operation ~= preflight and operation ~= start and operation ~= turn_in and operation ~= approach then return end
    last = nil
    if #fields ~= 3 then refuse("arguments_unqualified") end
    local name = fields[3]
    if operation == approach then return board_check(board_scope.stock_board_approach, state, phase, name, profile) end
    local context = board_check(board_scope.stock_board_context, state, phase, name, false, profile)
    local source = board_check(board_scope.stock_quest_table):FindRow(quest_id)
    local weapon, ammo = recipe(source)
    if source[step_field] ~= 1 then refuse("template_step_unqualified") end
    local before = progress(context)
    if before.step == "-" then
        context.board:Configure_Stock()
        local offered = board_check(board_scope.stock_row, context.board.Contracts, context.key)
        if not offered or offered[step_field] ~= 1 then refuse("quest_not_offered") end
        recipe(offered)
    end
    local after, identity = before, "-"
    if operation == start and before.step == "-" then
        local checked, observed = pcall(function()
            context.instance:AddQuest_ByName(context.key)
            return progress(context)
        end)
        if not checked or not unchanged(context) or observed.step ~= 1
            or observed.cash ~= before.cash or observed.xp ~= before.xp then
            error("mutation_unverified_stock_meet_broker_start", 0)
        end
        after = observed
    elseif operation == turn_in then
        after, identity = redeem(context, before, weapon, ammo)
    end
    last = {operation = operation, pawn = name, context = context, before = before, after = after, weapon = identity}
    return "stock MeetBroker contract verified"
end

function contract.rows(state, phase, status_row)
    local rows = {}
    if last_reward and phase == "hosting" and last_reward.map == state.map then
        rows[#rows + 1] = rewards.row(last_reward, status_row, "native-meet-broker-reward")
    else
        last_reward = nil
    end
    local proof = last
    if not proof or phase ~= "hosting" or state.map ~= map or not same(state.world, proof.context.world)
        or not unchanged(proof.context) then
        last = nil
        return rows
    end
    local before, after = proof.before, proof.after
    rows[#rows + 1] = status_row("native-meet-broker", proof.operation, map, proof.pawn, profile.board_name, quest_id,
        before.step, after.step, before.cash, after.cash, before.xp, after.xp, proof.weapon)
    return rows
end

return contract
