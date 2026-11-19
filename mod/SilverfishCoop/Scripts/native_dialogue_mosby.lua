local wire = require("native_wire_quest")

local area = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local world = area .. ".Shop_Scientist_01"
local level = world .. ":PersistentLevel."
local topic_eather, topic_data, completed = "Topic_EatherDrill", "Topic_DrillData", "Mosby_SM_Readings"
local quest = "Clt_Tape_StoneMaze_01"
local greet, questions, anomalies, drill, offer = "Mosby_Greet", "Mosby_Questions", "Mosby_Anomalies",
    "Mosby_EatherDrill", "Mosby_DrillQuest_01"
local profile = {
    id = "mosby", title = "Dr. Mosby", map = area, world = world, root = greet,
    npc = level .. "NPC_DrMosby_C_1", npc_kind = "NPC_DrMosby_C",
    npc_class = "/Game/Classes/NPCs/NPC_Characters/NPC_DrMosby.NPC_DrMosby_C",
    target = level .. "Generic_Interact_BP_C_1", target_kind = "Generic_Interact_BP_C",
    target_class = "/Game/Classes/ScriptingActors/Generic_Interact_BP.Generic_Interact_BP_C",
    component = "Box", component_class = "/Script/Engine.BoxComponent",
    controller = level .. "Dialogue_Controler_BP_C_1",
    source = "/Game/Classes/Libraries/Tables_Dialogue/Dialogue_DrMosby_DT.Dialogue_DrMosby_DT",
    row_order = {greet, questions, anomalies, drill, offer},
    rows = {
        [greet] = {lines = 1, count = 4, choices = {1, 4}, next = {[1] = false, [4] = questions}},
        [questions] = {lines = 1, count = 8, choices = {1, 3, 5, 6}, next = {[1] = greet, [3] = anomalies, [5] = drill, [6] = offer}},
        [anomalies] = {lines = 4, count = 1, choices = {1}, next = {[1] = questions}},
        [drill] = {lines = 3, count = 1, choices = {1}, next = {[1] = questions}},
        [offer] = {lines = 4, count = 2, choices = {1, 2}, next = {[1] = false, [2] = false}, pointer = {[1] = questions}},
    },
    effects = {
        [questions] = {[3] = {add = topic_eather, effect = "topic_eather_drill"},
            [5] = {add = topic_data, require_flag = topic_eather, effect = "topic_drill_data"},
            [6] = {require_flag = topic_data, hide_flag = completed}},
        [offer] = {[2] = {quest = quest, effect = "stone_maze_issued"}},
    },
}

local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local tape = "/Game/Classes/Items/DataTapes/DataTape_StoneMaze_01.DataTape_StoneMaze_01_C"
local armor = "/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C"
local quest_fields = {
    Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B = 2,
    QuestType_47_1031681D471E9929E30B17985E174BBE = 1,
    Difficulty_12_9F1A01AD46151E4F5F152B82C99B5D85 = 15,
    Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0 = 7500,
    Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797 = 3,
    RedeemAt_83_FAE34B6C471D2019508A4898E9A0080F = 4,
    AppearOn_GlobalBountyBoards_78_B538291F4816D684913EAA8852507698 = false,
    Fail_OnDeath_9_BAADF6C6450D1EB972F6E8A33214ACE4 = false,
    ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333 = true,
}

local function valid(object) return object and object:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function refuse(reason) error("native_dialogue_mosby_" .. reason, 0) end

local function fname(value)
    local key = FName(value, EFindName.FNAME_Find)
    if key:GetComparisonIndex() == 0 or key:ToString() ~= value then refuse("stock_name_unqualified") end
    return key
end

function profile.table_and_key()
    return wire.stock_quest_table(), fname(quest)
end

local function one_class(array, path)
    return array and array:GetArrayNum() == 1 and same(array[1], StaticFindObject(path))
end

local function recipe(row)
    if not row then refuse("stock_quest_row_unavailable") end
    for key, value in pairs(quest_fields) do
        if row[key] ~= value then refuse("stock_quest_recipe_unqualified:" .. key) end
    end
    local boards = row.AppearOn_Boards_95_2FDB27AE4695B9213302E5897144A531
    local flags = row.Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F
    local key = fname(completed)
    if not one_class(row.Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083, tape)
        or not one_class(row.Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A, armor)
        or not boards or boards:GetArrayNum() ~= 1 or boards[1] ~= 4
        or not flags or #flags ~= 1 or not flags:Contains(key) or flags:Find(key):get() ~= 1 then
        refuse("stock_quest_items_or_board_unqualified")
    end
end

local function read(context)
    if not same(context.instance.SaveSlot_Ref, context.save) or not same(context.host.Save, context.save) then
        refuse("canonical_save_changed")
    end
    local row = wire.stock_row(context.save.ActiveQuests, context.key)
    if row then recipe(row) end
    local step = row and row[step_field] or "-"
    if step ~= "-" and step ~= 1 and step ~= 2 and step ~= -10 then refuse("stock_quest_step_unqualified") end
    return {step = step, cash = wire.stock_flag(context.instance, "Player_Cash"),
        xp = wire.stock_flag(context.instance, "Player_Xp"),
        [topic_eather] = wire.stock_flag(context.instance, topic_eather),
        [topic_data] = wire.stock_flag(context.instance, topic_data),
        [completed] = wire.stock_flag(context.instance, completed)}
end

function profile.progress(state)
    local host, controller = state.local_pawn and state.local_pawn.pawn, state.controller
    local library = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(host) or not exact(host, "/Game/Classes/Player/Player_FP.Player_FP_C") or not valid(controller)
        or not host:HasAuthority() or not controller:IsLocalController() or not same(controller.Pawn, host)
        or not same(host.Controller, controller) or not same(host:GetWorld(), state.world) or not valid(library) then
        refuse("canonical_host_unavailable")
    end
    local instance = library:GetGameInstance(host)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
        or not exact(save, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C") then refuse("canonical_save_unavailable") end
    local table_asset, key = profile.table_and_key()
    local row = table_asset:FindRow(quest)
    recipe(row)
    if row[step_field] ~= 1 then refuse("stock_quest_template_unqualified") end
    local context = {instance = instance, save = save, host = host, key = key}
    return read(context), context
end

function profile.available(effect, progress)
    return (not effect.require_flag or progress[effect.require_flag] >= 1)
        and (not effect.hide_flag or progress[effect.hide_flag] < 1)
end

function profile.accepts_progress_fields(progress, fields, start)
    for index, field in ipairs({topic_eather, topic_data, completed, "step", "cash", "xp"}) do
        if fields[start + index - 1] ~= tostring(progress[field]) then return false end
    end
    return true
end

function profile.commit(context, effect, before)
    if effect.quest and (before.step ~= "-" or before[topic_eather] < 1 or before[topic_data] < 1 or before[completed] >= 1)
        or effect.add and before[effect.add] >= 1 then refuse("no_mutation_expected") end
    local success, after = pcall(function()
        if effect.add then context.instance:Add_PointTo_SaveFlag(fname(effect.add), 1, true, false)
        else context.instance:AddQuest_ByName(context.key) end
        return read(context)
    end)
    if not success then error("mutation_unverified_native_dialogue_mosby:" .. tostring(after), 0) end
    for name, value in pairs(before) do
        local expected = (name == effect.add or effect.quest and name == "step") and 1 or value
        if after[name] ~= expected then error("mutation_unverified_native_dialogue_mosby:" .. name, 0) end
    end
    return after
end

function profile.progress_row(name, state, effect, status_row)
    return status_row("native-dialogue-progress", area, name, state[topic_eather], state[topic_data], state[completed],
        state.step, state.cash, state.xp, effect)
end

return profile
