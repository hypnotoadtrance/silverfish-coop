local wire = require("native_wire_quest")

local area = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local world = area .. ".Shop_Scientist_01"
local level = world .. ":PersistentLevel."
local topic_eather, topic_data, completed = "Topic_EatherDrill", "Topic_DrillData", "Mosby_SM_Readings"
local topic_micro, tested, topic_lab = "Topic_MicroDrill", "Mosby_DrillTest", "Topic_DrillLab"
local quest, prototype = "Clt_Tape_StoneMaze_01", "Clt_DrillHead"
local greet, questions, anomalies, drill, offer =
    "Mosby_Greet", "Mosby_Questions", "Mosby_Anomalies", "Mosby_EatherDrill", "Mosby_DrillQuest_01"
local readings, equipment = "Mosby_EatherReadings_01", "Mosby_DrillQuest_02"
local readings_controller = level .. "Dialogue_Controler_BP_C_3"
local controller_class = "/Game/Classes/ScriptingActors/Dialogue_Controler_BP.Dialogue_Controler_BP_C"
local level_class = world .. "_C"
local profile = {
    id = "mosby", title = "Dr. Mosby", map = area, world = world, root = greet, readings = readings,
    npc = level .. "NPC_DrMosby_C_1", npc_kind = "NPC_DrMosby_C",
    npc_class = "/Game/Classes/NPCs/NPC_Characters/NPC_DrMosby.NPC_DrMosby_C",
    target = level .. "Generic_Interact_BP_C_1", target_kind = "Generic_Interact_BP_C",
    target_class = "/Game/Classes/ScriptingActors/Generic_Interact_BP.Generic_Interact_BP_C",
    component = "Box", component_class = "/Script/Engine.BoxComponent",
    controller = level .. "Dialogue_Controler_BP_C_1",
    source = "/Game/Classes/Libraries/Tables_Dialogue/Dialogue_DrMosby_DT.Dialogue_DrMosby_DT",
    row_order = {greet, questions, anomalies, drill, offer, readings, equipment},
    rows = {
        [greet] = {lines = 1, count = 4, choices = {1, 4}, next = {[1] = false, [4] = questions}},
        [questions] = {lines = 1, count = 8, choices = {1, 3, 5, 6, 7},
            next = {[1] = greet, [3] = anomalies, [5] = drill, [6] = offer, [7] = equipment}},
        [anomalies] = {lines = 4, count = 1, choices = {1}, next = {[1] = questions}},
        [drill] = {lines = 3, count = 1, choices = {1}, next = {[1] = questions}},
        [offer] = {lines = 4, count = 2, choices = {1, 2}, next = {[1] = false, [2] = false}, pointer = {[1] = questions}},
        [readings] = {lines = 6, count = 2, choices = {1, 2}, next = {[1] = equipment, [2] = greet}},
        [equipment] = {lines = 5, count = 2, choices = {1, 2}, next = {[1] = questions, [2] = false}},
    },
    effects = {
        [questions] = {[3] = {add = topic_eather, effect = "topic_eather_drill"},
            [5] = {add = topic_data, require_flag = topic_eather, effect = "topic_drill_data"},
            [6] = {require_flag = topic_data, hide_flag = completed},
            [7] = {require_flag = topic_micro, hide_flag = tested}},
        [offer] = {[2] = {quest = quest, effect = "stone_maze_issued"}},
        [equipment] = {[1] = {add = topic_lab, effect = "topic_drill_lab", prototype = true},
            [2] = {quest = prototype, effect = "prototype_drill_issued", prototype = true}},
    },
}

local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local tape = "/Game/Classes/Items/DataTapes/DataTape_StoneMaze_01.DataTape_StoneMaze_01_C"
local armor = "/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C"
local drill_head = "/Game/Classes/Items/Artifacts/Art_DrillHead.Art_DrillHead_C"
local hazard_suit = "/Game/Classes/Armor_Items/Armor_HazzardSuit.Armor_HazzardSuit_C"
local progress_fields = {topic_eather, topic_data, completed, "step", "cash", "xp", topic_micro, tested, topic_lab, "prototype"}
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

function profile.table_and_key(id)
    return wire.stock_quest_table(), fname(id or quest)
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

local function prototype_recipe(row)
    local boards = row and row.AppearOn_Boards_95_2FDB27AE4695B9213302E5897144A531
    local flags = row and row.Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F
    local prereq = row and row.PreReq_Flags_105_3FA9C82A48BE1675F41E15A54570DBAD
    local follow_up = row and row.Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9
    local reputation = row and row.ReputationGain_86_DA02B32344A59A6C236B848B22520EEC
    local advantage = row and row.Advantage_Faction_100_F1B0B60547EEC0A936CFECB9BE005CE2
    local ping = row and row.GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F
    local track = row and row.TrackFlag_ForStep_111_31112EA54B887C1D869E1291BC819774
    local key = fname(tested)
    if not row or row.QuestType_47_1031681D471E9929E30B17985E174BBE ~= 1
        or row.Difficulty_12_9F1A01AD46151E4F5F152B82C99B5D85 ~= 20
        or row.Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B ~= 2
        or row.Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0 ~= 10000
        or row.Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797 ~= 3
        or row.FailPenalty_36_53777B2F407AD61DE139B3815A07F47F ~= 100
        or row.Fail_OnDeath_9_BAADF6C6450D1EB972F6E8A33214ACE4 ~= false
        or row.AppearOn_GlobalBountyBoards_78_B538291F4816D684913EAA8852507698 ~= false
        or row.RedeemAt_83_FAE34B6C471D2019508A4898E9A0080F ~= 4
        or row.ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333 ~= true
        or not boards or boards:GetArrayNum() ~= 1 or boards[1] ~= 4
        or not prereq or #prereq ~= 0 or not follow_up or follow_up:GetArrayNum() ~= 0
        or not reputation or #reputation ~= 3
        or not advantage or #advantage ~= 1 or not advantage:Contains(2) or advantage:Find(2):get() ~= 300
        or (type(ping) == "userdata" and ping:ToString() or ping) ~= "None"
        or not track or track:ToString() ~= "None"
        or not one_class(row.Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083, drill_head)
        or not one_class(row.Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A, hazard_suit)
        or not flags or #flags ~= 1 or not flags:Contains(key) or flags:Find(key):get() ~= 1 then
        refuse("stock_prototype_quest_recipe_unqualified")
    end
    for faction, amount in pairs({[2] = 300, [4] = -200, [3] = -200}) do
        if not reputation:Contains(faction) or reputation:Find(faction):get() ~= amount then
            refuse("stock_prototype_faction_recipe_unqualified")
        end
    end
end

local function stock_stage(row)
    local step = row and row[step_field] or "-"
    if step ~= "-" and step ~= 1 and step ~= 2 and step ~= -10 then refuse("stock_quest_step_unqualified") end
    return step
end

local function read(context)
    if not same(context.instance.SaveSlot_Ref, context.save) or not same(context.host.Save, context.save)
        or not same(context.instance["As Player FP"], context.host) then
        refuse("canonical_save_changed")
    end
    local row = wire.stock_row(context.save.ActiveQuests, context.key)
    if row then recipe(row) end
    local prototype_row = wire.stock_row(context.save.ActiveQuests, context.prototype)
    if prototype_row then prototype_recipe(prototype_row) end
    return {step = stock_stage(row), prototype = stock_stage(prototype_row), cash = wire.stock_flag(context.instance, "Player_Cash"),
        xp = wire.stock_flag(context.instance, "Player_Xp"),
        [topic_eather] = wire.stock_flag(context.instance, topic_eather),
        [topic_data] = wire.stock_flag(context.instance, topic_data),
        [completed] = wire.stock_flag(context.instance, completed),
        [topic_micro] = wire.stock_flag(context.instance, topic_micro),
        [tested] = wire.stock_flag(context.instance, tested),
        [topic_lab] = wire.stock_flag(context.instance, topic_lab)}
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
    local next_recipe = table_asset:FindRow(prototype)
    prototype_recipe(next_recipe)
    if row[step_field] ~= 1 or next_recipe[step_field] ~= 1 then refuse("stock_quest_template_unqualified") end
    local context = {instance = instance, save = save, host = host, key = key, prototype = fname(prototype)}
    return read(context), context
end

function profile.available(effect, progress)
    return (not effect.require_flag or progress[effect.require_flag] >= 1)
        and (not effect.hide_flag or progress[effect.hide_flag] < 1)
end

function profile.accepts_progress_fields(progress, fields, start)
    local size = #fields - start + 1
    if size ~= 6 and size ~= #progress_fields then return false end
    for index = 1, size do
        if fields[start + index - 1] ~= tostring(progress[progress_fields[index]]) then return false end
    end
    return true
end

function profile.commit(context, effect, before)
    local step = effect.prototype and "prototype" or "step"
    local can_issue = effect.prototype and before[topic_micro] >= 1 and before[tested] < 1
        or not effect.prototype and before[topic_eather] >= 1 and before[topic_data] >= 1 and before[completed] < 1
    if effect.quest and (before[step] ~= "-" or not can_issue)
        or effect.add and before[effect.add] >= 1
        or effect.prototype and (before[topic_micro] < 1 or before[tested] >= 1) then refuse("no_mutation_expected") end
    local success, after = pcall(function()
        if effect.add then context.instance:Add_PointTo_SaveFlag(fname(effect.add), 1, true, false)
        else context.instance:AddQuest_ByName(effect.prototype and context.prototype or context.key) end
        return read(context)
    end)
    if not success then error("mutation_unverified_native_dialogue_mosby:" .. tostring(after), 0) end
    for name, value in pairs(before) do
        local expected = (name == effect.add or effect.quest and name == step) and 1 or value
        if after[name] ~= expected then error("mutation_unverified_native_dialogue_mosby:" .. name, 0) end
    end
    return after
end

function profile.open_readings(state, context, before)
    if before.step ~= -10 or before[completed] ~= 1 or before[tested] >= 1 then refuse("readings_phase_unqualified") end
    local dialogue = StaticFindObject(readings_controller)
    local source = StaticFindObject(profile.source)
    local script = state.world.PersistentLevel.LevelScriptActor
    if not exact(dialogue, controller_class) or not same(dialogue:GetWorld(), state.world) or not dialogue:HasAuthority()
        or not exact(script, level_class) or not same(script:GetWorld(), state.world) or not script:HasAuthority()
        or script.PlayerIsWelcome ~= true or script.DrillTested ~= false
        or not same(script["As Game Instance FP"], context.instance)
        or not same(dialogue.ConversationPointer.DataTable, source) or dialogue.ConversationPointer.RowName:ToString() ~= readings then
        refuse("readings_stock_scope_unqualified")
    end
    local success, after = pcall(function()
        -- These are the two save effects issued by the stock Scientist Player_Talk branch.
        -- The controller's single-player widget is represented by the requesting guest's session.
        context.instance:Add_PointTo_SaveFlag(fname(completed), 2, true, true)
        context.instance:Add_PointTo_SaveFlag(fname(topic_micro), 1, true, true)
        return read(context)
    end)
    if not success then error("mutation_unverified_native_dialogue_mosby_readings:" .. tostring(after), 0) end
    for key, value in pairs(before) do
        local expected = key == completed and 2 or key == topic_micro and 1 or value
        if after[key] ~= expected then error("mutation_unverified_native_dialogue_mosby_readings:" .. key, 0) end
    end
    return after
end

function profile.progress_row(name, state, effect, status_row)
    return status_row("native-dialogue-progress", area, name, state[topic_eather], state[topic_data], state[completed],
        state.step, state.cash, state.xp, state[topic_micro], state[tested], state[topic_lab], state.prototype, effect)
end

return profile
