local broker = {}

local map = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
local level = map .. ".Shop_BlackMarket_01:PersistentLevel."
local npc_name = "NPC_Broker_C " .. level .. "NPC_Broker_C_1"
local station_name = "ItemRequest_TradeStation_BP_C " .. level .. "ItemRequest_TradeStation_BP_C_1"
local dialogue_table = "/Game/Classes/Libraries/Tables_Dialogue/Dialogue_Broker_DT.Dialogue_Broker_DT"
local dialogue_schema = "/Game/Classes/Libraries/Structs/Conversation_Struct.Conversation_Struct"
local quest_table = "/Game/Classes/Libraries/DataTables/Quests_DT.Quests_DT"
local quest_schema = "/Game/Classes/Libraries/Structs/Quest_Struct.Quest_Struct"
local quest_library = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local character_library = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local manifest = {
    id = "Clct_EManifest", previous = "Clt_DrillHead_Brkr", issue_row = "Broker_Questline_02",
    dialogue = "Dialogue_Controler_BP_C_3", responses = 1, cash = 10000, ping = "BrokerQuest_03",
    objective = "/Game/Classes/Items/Books_Items/Item_EtrCartManifest.Item_EtrCartManifest_C",
}
local drill = {
    id = "Clt_DrillHead_Brkr", previous = "Clct_ECart_Chem_01", issue_row = "Broker_Questline_01",
    dialogue = "Dialogue_Controler_BP_C_2", responses = 2, cash = 0, ping = "BrokerQuest_02",
    objective = "/Game/Classes/Items/Artifacts/Art_DrillHead.Art_DrillHead_C",
    reward = "/Game/Classes/Items/Key_Items/Item_DebtVoucher_1Mil.Item_DebtVoucher_1Mil_C",
    debt = 1000000, flags = { "Topic_EPC", "Topic_DrillLab" },
    -- The cooked enum stores Freelancer (NewEnumerator9) as 8 and Fae (NewEnumerator7) as 3.
    reputation = { freelancer = { id = 8, gain = 1200 }, fae = { id = 3, gain = -200 } },
}
local reward_xp, buyer = 1, 1
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local drill_fixtures = fixtures and headless

local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local final_field = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B"
local cash_field = "Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0"
local xp_field = "Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797"
local rewards_field = "Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A"
local objective_items = "Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083"
local objective_consumed = "ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333"
local redeem_field = "RedeemAt_83_FAE34B6C471D2019508A4898E9A0080F"
local ping_field = "GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F"
local prereq_field = "PreReq_Flags_105_3FA9C82A48BE1675F41E15A54570DBAD"
local reputation_field = "ReputationGain_86_DA02B32344A59A6C236B848B22520EEC"
local completion_flags = "Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F"
local completion_quests = "Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_quantity = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"

local responses_field = "PlayerResponces_6_DA452ED342AF8EEEC7BD299C134AF0F3"
local activate_field = "Activate_Quests_22_323EA585468EFFDA9A12A188EBDF5022"
local terminal_field = "Terminate_Convo_5_BAA128604982D7FC877B498C6500026C"
local add_flags_field = "Add_SaveFlags_13_0FEB9B204B1F6F013B6309BF12311EF7"
local response_arrays = {
    "Check_TurnIn_Quests_20_4B851D164A4A4164B7B74D80DE4EED23",
    "Add_JournalEntries_17_E77934214DFD7B85991F729C59678191",
    "GlobalPings_38_DB12D37348CDE946D7BA20BD3DE97CDD",
}
local response_maps = {
    "Flags_PreReq_26_50B864DB4BE3226CB3721BB63C4D74DE",
    "Flags_HideIfMet_28_6501CD9247D8EA6D695B5C9F5FD8C639",
    "Rep_Effect_33_CA3AB131482DEE2637F973B7480AA04F",
}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end

local function int32(n)
    return type(n) == "number" and math.tointeger(n) and n >= -2147483648 and n <= 2147483647
end

local function count(array, maximum)
    local n = array and array:GetArrayNum()
    if not int32(n) or n < 0 or n > maximum then error("stock_broker_array_unqualified", 0) end
    return n
end

local function exact(object, path)
    return valid(object) and not object:GetFullName():find("Default__", 1, true)
        and same(object:GetClass(), StaticFindObject(path))
end

local function controlled(pawn, controller, world, local_player)
    return valid(pawn) and valid(controller) and pawn:HasAuthority() and controller:HasAuthority()
        and not pawn:IsActorBeingDestroyed() and not controller:IsActorBeingDestroyed()
        and controller:IsLocalController() == local_player and same(controller.Pawn, pawn)
        and same(pawn.Controller, controller) and same(pawn:GetWorld(), world) and same(controller:GetWorld(), world)
        and pawn:GetFullName():match("^Player_FP_C ") and controller:GetFullName():match("^PlayerControler_FP_C ")
end

local function remote_guest(state, full_name, driver)
    if type(full_name) ~= "string" or #full_name > 512 then error("broker_guest_identity_unqualified", 0) end
    local pawn
    for _, details in ipairs(state.pawns) do
        if details.kind == "guest" and details.name == full_name
            and controlled(details.pawn, details.controller_object, state.world, false)
            and details.pawn:GetFullName() == full_name then
            if pawn then error("broker_guest_identity_ambiguous", 0) end
            local controller, connection = details.controller_object, details.controller_object.Player
            if not valid(connection) or not same(connection.Driver, driver)
                or not same(connection.PlayerController, controller) or not same(connection.OwningActor, controller) then
                error("broker_guest_connection_unqualified", 0)
            end
            local connections = driver.ClientConnections
            local membership = 0
            for index = 1, count(connections, 16) do
                if same(connections[index], connection) then membership = membership + 1 end
            end
            if membership ~= 1 then error("broker_guest_connection_unqualified", 0) end
            pawn = details.pawn
        end
    end
    if not pawn then error("broker_guest_identity_unqualified", 0) end
    return pawn
end

local function host_context(state, phase, name)
    local host, world = state.local_pawn, state.world
    if phase ~= "hosting" or not state.listening or state.server or state.map ~= map or not valid(world)
        or world:GetFullName() ~= "World " .. map .. ".Shop_BlackMarket_01"
        or not host or not same(state.controller, host.controller_object)
        or not controlled(host.pawn, host.controller_object, world, true) then
        error("broker_authoritative_host_unavailable", 0)
    end
    local driver = world.NetDriver
    if not valid(driver) or valid(driver.ServerConnection) or not same(driver.World, world) then
        error("broker_listen_driver_unqualified", 0)
    end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(host.pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
        or not exact(save, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C") or not same(host.pawn.Save, save) then
        error("broker_canonical_save_unavailable", 0)
    end
    local interactor
    if name then
        interactor = name == host.name and host.pawn or remote_guest(state, name, driver)
    end
    return { world = world, host = host.pawn, instance = instance, save = save, interactor = interactor }
end

local function actor(world, class, name)
    local match
    for _, candidate in ipairs(FindAllOf(class) or {}) do
        if valid(candidate) and candidate:GetFullName() == name and same(candidate:GetWorld(), world)
            and candidate:HasAuthority() and not candidate:IsActorBeingDestroyed() then
            if match then error("stock_broker_actor_ambiguous", 0) end
            match = candidate
        end
    end
    if not match then error("stock_broker_actor_unavailable", 0) end
    return match
end

local function finite_axis(value)
    return type(value) == "number" and value == value and math.abs(value) <= 100000000
end

local function require_reach(pawn, target)
    if not valid(pawn) then error("broker_guest_identity_required", 0) end
    local range = pawn.InteractionRange
    if not finite_axis(range) or range <= 0 then error("broker_stock_reach_unavailable", 0) end
    local first, second = pawn:K2_GetActorLocation(), target:K2_GetActorLocation()
    local squared = 0
    for _, axis in ipairs({ "X", "Y", "Z" }) do
        if not finite_axis(first[axis]) or not finite_axis(second[axis]) then
            error("broker_interaction_position_unqualified", 0)
        end
        squared = squared + (first[axis] - second[axis]) ^ 2
    end
    if squared > range ^ 2 then error("broker_target_outside_reach", 0) end
end

local function data_table(path, expected_schema)
    local object = StaticFindObject(path)
    local row_type = valid(object) and object:GetRowStruct()
    if not valid(object) or object:GetFullName() ~= "DataTable " .. path
        or not valid(row_type) or row_type:GetFullName() ~= "UserDefinedStruct " .. expected_schema then
        error("stock_broker_table_unqualified", 0)
    end
    return object
end

local function fname(id)
    local name = FName(id, EFindName.FNAME_Find)
    if name:GetComparisonIndex() == 0 or name:ToString() ~= id then error("stock_broker_fname_unavailable", 0) end
    return name
end

local function same_name(first, second)
    return first ~= nil and second ~= nil and first:GetComparisonIndex() == second:GetComparisonIndex()
        and first:ToString() == second:ToString()
end

local function live_quest(save, key)
    local found = save.ActiveQuests:Contains(key)
    if type(found) ~= "boolean" then error("stock_broker_quest_map_unqualified", 0) end
    return found and save.ActiveQuests:Find(key):get() or nil
end

local function qualify_recipe(profile, quest, expected_objective)
    if not quest or quest[final_field] ~= 2 or quest[cash_field] ~= profile.cash or quest[xp_field] ~= reward_xp
        or quest[redeem_field] ~= buyer or quest[objective_consumed] ~= true
        or not quest[prereq_field] or #quest[prereq_field] ~= 0
        or count(quest[rewards_field], 32) ~= (profile.reward and 1 or 0) or count(quest[objective_items], 32) ~= 1 then
        error("stock_broker_quest_recipe_changed", 0)
    end
    local objective, ping = quest[objective_items][1], quest[ping_field]
    if type(ping) == "userdata" then ping = ping:ToString() end
    if not valid(objective) or objective:GetFullName() ~= "BlueprintGeneratedClass " .. profile.objective
        or (expected_objective and not same(objective, expected_objective)) or ping ~= profile.ping then
        error("stock_broker_quest_objective_changed", 0)
    end
    local reward
    if profile.reward then
        reward = quest[rewards_field][1]
        local reputation = quest[reputation_field]
        if not valid(reward) or reward:GetFullName() ~= "BlueprintGeneratedClass " .. profile.reward
            or not reputation or #reputation ~= 2 or #quest[completion_flags] ~= 0
            or count(quest[completion_quests], 32) ~= 0 then error("stock_broker_drill_reward_changed", 0) end
        for _, faction in pairs(profile.reputation) do
            if not reputation:Contains(faction.id) or reputation:Find(faction.id):get() ~= faction.gain then
                error("stock_broker_drill_reputation_changed", 0)
            end
        end
        local default = reward:GetCDO()
        if not valid(default) or not same(default:GetClass(), reward) or default.Ammo ~= profile.debt then
            error("stock_broker_drill_voucher_changed", 0)
        end
    end
    return objective, reward
end

local function stock_flag(instance, name)
    local out = {}
    instance:Check_SaveFlag(type(name) == "string" and fname(name) or name, out, out)
    if type(out.Found) ~= "boolean" or not int32(out.Value) then error("stock_broker_player_stat_unqualified", 0) end
    return out.Found and out.Value or 0, out.Found
end

local function raw_reputation(context, faction)
    local library = StaticFindObject(character_library)
    local out = {}
    if not valid(library) then error("stock_broker_reputation_library_unavailable", 0) end
    library:Compose_FactionRep_FlagName(faction.id, context.host, out)
    local name = out.Name
    if not name or type(name) ~= "userdata" or name:GetComparisonIndex() == 0
        or not name:ToString():match("^Rep_") then error("stock_broker_reputation_name_unqualified", 0) end
    return stock_flag(context.instance, name)
end

local function class_rows(array, class, limit)
    local found = 0
    for index = 1, count(array, limit) do
        local row = array[index]
        if row and same(row[item_class], class) then found = found + 1 end
    end
    return found
end

local function guid(value, permit_empty)
    if not value or not int32(value.A) or not int32(value.B) or not int32(value.C) or not int32(value.D) then
        error("stock_manifest_guid_unqualified", 0)
    end
    local result = string.format("%08X%08X%08X%08X", value.A & 0xffffffff, value.B & 0xffffffff,
        value.C & 0xffffffff, value.D & 0xffffffff)
    if result == string.rep("0", 32) then
        if permit_empty then return end
        error("stock_manifest_guid_unqualified", 0)
    end
    return result
end

local function stored_manifest(save, class)
    local id, slot
    for index = 1, count(save.PlayerBackpack, 1024) do
        local item = save.PlayerBackpack[index]
        if item and same(item[item_class], class) then
            local quantity = item[item_quantity]
            if id or not int32(quantity) or quantity < 0 or quantity > 1 then
                error("stock_manifest_ambiguous", 0)
            end
            id, slot = guid(item[item_guid]), index
        end
    end
    for index = 1, count(save.PlayerEquipment, 128) do
        local item = save.PlayerEquipment[index]
        if item and same(item[item_class], class) then error("stock_manifest_equipped", 0) end
    end
    return id or "-", slot
end

local function describe(profile, context, key, objective, reward)
    if not same(context.instance.SaveSlot_Ref, context.save) or not same(context.host.Save, context.save) then
        error("broker_canonical_save_changed", 0)
    end
    local details = live_quest(context.save, key)
    local step = details and details[step_field] or nil
    if details then
        qualify_recipe(profile, details, objective)
        if step ~= 1 and step ~= 2 and step ~= -10 then error("stock_broker_quest_step_unqualified", 0) end
    end
    local result = { quest_id = profile.id, state = not details and "missing" or step == -10 and "completed"
            or step == 2 and "ready" or "active",
        step = step or "-", final_step = 2, cash = profile.cash, xp = reward_xp, changed = false,
        manifest_guid = stored_manifest(context.save, objective),
        native_cash = stock_flag(context.instance, "Player_Cash"),
        native_xp = stock_flag(context.instance, "Player_Xp"),
    }
    if profile.debt then
        result.objective_guid = result.manifest_guid
        result.native_debt = stock_flag(context.instance, "Player_Debt")
        result.topic_epc = stock_flag(context.instance, "Topic_EPC")
        result.topic_drill_lab = stock_flag(context.instance, "Topic_DrillLab")
        result.topic_micro_drill = stock_flag(context.instance, "Topic_MicroDrill")
        result.freelancer_rep, result.freelancer_rep_found = raw_reputation(context, profile.reputation.freelancer)
        result.fae_rep, result.fae_rep_found = raw_reputation(context, profile.reputation.fae)
        result.debt_reward = profile.debt
        result.voucher_rows = class_rows(context.save.PlayerBackpack, reward, 1024)
            + class_rows(context.save.PlayerEquipment, reward, 128)
        local out = {}
        context.instance:FindPassiveEffect(fname("IntensePersonality"), true, out, out)
        if type(out.EffectActive) ~= "boolean" or not int32(out.Instances) or out.Instances < 0 then
            error("stock_broker_reputation_trait_unqualified", 0)
        end
        result.intense_personality = out.Instances
        if headless and type(SilverfishReadNativeBrokerMenuGuard) == "function" then
            result.menu_guard_armed, result.guarded_ping, result.guarded_menu, result.guarded_gameplay =
                SilverfishReadNativeBrokerMenuGuard()
        end
    end
    return result
end

local function quest_objects(profile)
    local quests = data_table(quest_table, quest_schema)
    local template = quests:FindRow(profile.id)
    local objective, reward = qualify_recipe(profile, template)
    if template[step_field] ~= 1 then error("stock_broker_quest_template_changed", 0) end
    return quests, fname(profile.id), objective, reward
end

local function inspect(profile, state, phase, pawn_name)
    local context = host_context(state, phase, pawn_name)
    local _, key, objective, reward = quest_objects(profile)
    return describe(profile, context, key, objective, reward)
end

local function qualify_issue(profile, context, quests, expected_npc)
    if expected_npc ~= npc_name then error("broker_issue_actor_unqualified", 0) end
    require_reach(context.interactor, actor(context.world, "NPC_Broker_C", npc_name))
    local controller = actor(context.world, "Dialogue_Controler_BP_C", "Dialogue_Controler_BP_C " .. level .. profile.dialogue)
    local dialogue = data_table(dialogue_table, dialogue_schema)
    local pointer = controller.ConversationPointer
    local key = fname(profile.issue_row)
    if controller.Prioroty ~= true or not pointer or not same(pointer.DataTable, dialogue)
        or not same_name(pointer.RowName, key) then error("stock_broker_dialogue_pointer_changed", 0) end
    local row = dialogue:FindRow(profile.issue_row)
    local responses = row and row[responses_field]
    if count(responses, 64) ~= profile.responses then error("stock_broker_dialogue_response_unqualified", 0) end
    local response = responses[1]
    local activate = response[activate_field]
    if response[terminal_field] ~= true or count(activate, 32) ~= 1
        or not same(activate[1].DataTable, quests) or not same_name(activate[1].RowName, fname(profile.id)) then
        error("stock_broker_dialogue_response_changed", 0)
    end
    for _, name in ipairs(response_arrays) do
        if count(response[name], 32) ~= 0 then error("stock_broker_dialogue_effects_changed", 0) end
    end
    for _, name in ipairs(response_maps) do
        if not response[name] or #response[name] ~= 0 then error("stock_broker_dialogue_flags_changed", 0) end
    end
    local flags = response[add_flags_field]
    if not flags or #flags ~= (profile.flags and #profile.flags or 0) then
        error("stock_broker_dialogue_flags_changed", 0)
    end
    for _, name in ipairs(profile.flags or {}) do
        local flag = fname(name)
        if not flags:Contains(flag) or flags:Find(flag):get() ~= 1 then
            error("stock_broker_dialogue_flags_changed", 0)
        end
    end
end

local function issue(profile, state, phase, pawn_name, expected_npc, use_fixture)
    local context = host_context(state, phase, pawn_name)
    local quests, key, objective, reward = quest_objects(profile)
    qualify_issue(profile, context, quests, expected_npc)
    local before = describe(profile, context, key, objective, reward)
    if before.state ~= "missing" then return before end
    if not use_fixture then
        local previous = live_quest(context.save, fname(profile.previous))
        if not previous or previous[step_field] ~= -10 then error("broker_predecessor_incomplete", 0) end
    end

    -- PlayerResponce_Widget applies its authored save flags before activating the quest.
    for _, name in ipairs(profile.flags or {}) do
        context.instance:Add_PointTo_SaveFlag(fname(name), 1, true, false)
    end
    context.instance:AddQuest_ByName(key)
    local after = describe(profile, context, key, objective, reward)
    if after.step ~= 1 or after.manifest_guid ~= before.manifest_guid
        or after.native_cash ~= before.native_cash or after.native_xp ~= before.native_xp then
        error("mutation_unverified_stock_broker_issue", 0)
    end
    if profile.flags and (after.topic_epc ~= math.max(1, before.topic_epc)
        or after.topic_drill_lab ~= math.max(1, before.topic_drill_lab)
        or after.topic_micro_drill ~= before.topic_micro_drill or after.native_debt ~= before.native_debt
        or after.freelancer_rep ~= before.freelancer_rep or after.fae_rep ~= before.fae_rep
        or after.voucher_rows ~= before.voucher_rows) then
        error("mutation_unverified_stock_broker_drill_issue_flags", 0)
    end
    after.changed = true
    return after
end

local function qualify_drill_ping(context)
    local receiver = actor(context.world, "GlobalPing_ScriptingReciver_BP_C",
        "GlobalPing_ScriptingReciver_BP_C " .. level .. "GlobalPing_ScriptingReciver_BP_C_5")
    local controller = actor(context.world, "Dialogue_Controler_BP_C", "Dialogue_Controler_BP_C " .. level .. manifest.dialogue)
    local pointer = controller.ConversationPointer
    local ping = receiver.Ping_ID
    if type(ping) == "userdata" then ping = ping:ToString() end
    if ping ~= drill.ping or count(receiver.ActorsToPower, 32) ~= 1
        or not same(receiver.ActorsToPower[1], controller) or controller.Prioroty ~= true or not pointer
        or not same(pointer.DataTable, data_table(dialogue_table, dialogue_schema))
        or not same_name(pointer.RowName, fname(manifest.issue_row)) then
        error("stock_broker_drill_next_dialogue_unqualified", 0)
    end
    return controller
end

local function next_reputation(before, key, change)
    local multiplier = before[key .. "_rep_found"] and (1 + before.intense_personality) or 1
    if not int32(change * multiplier) then error("stock_broker_reputation_out_of_range", 0) end
    return before[key .. "_rep"] + change * multiplier
end

local function turn_in(profile, state, phase, pawn_name, expected_station, expected_guid)
    local context = host_context(state, phase, pawn_name)
    if expected_station ~= station_name then error("broker_redemption_station_unqualified", 0) end
    local station = actor(context.world, "ItemRequest_TradeStation_BP_C", station_name)
    if station.Buyer ~= buyer then error("stock_broker_redemption_buyer_changed", 0) end
    require_reach(context.interactor, station)

    local _, key, objective, reward = quest_objects(profile)
    local before = describe(profile, context, key, objective, reward)
    if before.state == "completed" then return before end
    if before.state == "missing" then error("broker_quest_not_started", 0) end
    if type(expected_guid) ~= "string" or #expected_guid ~= 32 or expected_guid:find("[^0-9A-F]")
        or before.manifest_guid ~= expected_guid then error("broker_manifest_guid_mismatch", 0) end
    if not int32(before.native_cash + profile.cash) or not int32(before.native_xp + reward_xp) then
        error("broker_stock_reward_out_of_range", 0)
    end
    local expected_debt, expected_freelancer, expected_fae, next_dialogue
    if profile.debt then
        next_dialogue = qualify_drill_ping(context)
        expected_debt = before.native_debt - profile.debt
        expected_freelancer = next_reputation(before, "freelancer", profile.reputation.freelancer.gain)
        expected_fae = next_reputation(before, "fae", profile.reputation.fae.gain)
        if not int32(expected_debt) or not int32(expected_freelancer) or not int32(expected_fae) then
            error("stock_broker_drill_reward_out_of_range", 0)
        end
    end
    local library = StaticFindObject(quest_library)
    if not valid(library) then error("stock_broker_quest_library_unavailable", 0) end
    if next_dialogue and headless then
        if type(SilverfishArmNativeBrokerMenuGuard) ~= "function" then
            error("stock_broker_menu_guard_unavailable", 0)
        end
        local armed, reason = SilverfishArmNativeBrokerMenuGuard(state.controller:GetAddress(), next_dialogue:GetAddress())
        if not armed then error(reason or "stock_broker_menu_guard_unavailable", 0) end
    end

    -- Let stock consume the objective and run the authored ping and rewards once.
    -- SpawnItemForPlayer invokes the voucher immediately against the canonical save.
    local out = {}
    library:TurnIn_Quest(key, context.instance, true, context.host, out, out)
    local after = describe(profile, context, key, objective, reward)
    if out.Success ~= true or valid(out.ConfirmationPopup) or after.step ~= -10 or after.manifest_guid ~= "-"
        or after.native_cash ~= before.native_cash + profile.cash or after.native_xp ~= before.native_xp + reward_xp then
        error("mutation_unverified_stock_broker_turnin", 0)
    end
    if profile.debt and (after.native_debt ~= expected_debt or after.freelancer_rep ~= expected_freelancer
        or not after.freelancer_rep_found or after.fae_rep ~= expected_fae or not after.fae_rep_found
        or after.voucher_rows ~= before.voucher_rows or after.topic_epc ~= before.topic_epc
        or after.topic_drill_lab ~= before.topic_drill_lab or after.topic_micro_drill ~= before.topic_micro_drill
        or (headless and after.guarded_ping ~= 1)) then
        error("mutation_unverified_stock_broker_drill_reward", 0)
    end
    after.changed, after.consumed_guid = true, expected_guid
    return after
end

local floor_trace_color = { R = 0, G = 0, B = 0, A = 0 }
local fixture_offsets = {
    issue = { { 0, -155 }, { -155, 0 }, { 155, 0 }, { 0, 155 }, { -105, -105 }, { 105, -105 } },
    redeem = { { 0, 140 }, { 0, -140 }, { 140, 0 }, { -140, 0 }, { 100, 100 }, { -100, -100 } },
}

local function vector_qualified(vector)
    return vector and finite_axis(vector.X) and finite_axis(vector.Y) and finite_axis(vector.Z)
end

local function within(first, second, range)
    return (first.X - second.X) ^ 2 + (first.Y - second.Y) ^ 2 + (first.Z - second.Z) ^ 2 <= range ^ 2
end

local function find_floor(library, context, pawn, target, xy, top, bottom)
    local hit = {}
    local found = library:LineTraceSingle(pawn,
        { X = xy[1], Y = xy[2], Z = top }, { X = xy[1], Y = xy[2], Z = bottom },
        0, false, { context.host, pawn, target }, 0, hit, true, floor_trace_color, floor_trace_color, 0)
    hit = hit.OutHit or hit
    if found == true and vector_qualified(hit.ImpactPoint) and vector_qualified(hit.ImpactNormal)
        and hit.ImpactNormal.Z >= 0.7 and hit.ImpactPoint.Z < top - 1 and hit.ImpactPoint.Z >= bottom then
        return hit.ImpactPoint.Z
    end
    return nil, found == true and ("unqualified_floor_" .. tostring(hit.ImpactPoint ~= nil)
        .. "_" .. tostring(hit.ImpactNormal and hit.ImpactNormal.Z)) or "no_trace_hit"
end

function broker.fixture_position(state, phase, pawn_name, point)
    if not fixtures then error("broker_fixtures_disabled", 0) end
    local offsets = fixture_offsets[point]
    if not offsets then error("broker_fixture_position_unqualified", 0) end
    local context = host_context(state, phase, pawn_name)
    local pawn = context.interactor
    if not valid(pawn) then error("broker_guest_identity_required", 0) end

    local target = point == "issue" and actor(context.world, "NPC_Broker_C", npc_name)
        or actor(context.world, "ItemRequest_TradeStation_BP_C", station_name)
    local anchor = target:K2_GetActorLocation()
    if point == "redeem" then
        if target.Buyer ~= buyer or not valid(target.Box) or not same(target.Box:GetOwner(), target) then
            error("stock_broker_redemption_buyer_changed", 0)
        end
        anchor = target.Box:K2_GetComponentLocation()
    end
    local center = target:K2_GetActorLocation()
    local rotation = pawn:K2_GetActorRotation()
    local radius = pawn.InteractionRange
    local capsule = pawn.CapsuleComponent
    local half_height = valid(capsule) and capsule:GetScaledCapsuleHalfHeight()
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not valid(library) or not vector_qualified(anchor) or not vector_qualified(center)
        or not finite_axis(radius) or radius <= 0 or not finite_axis(half_height) or half_height <= 0
        or not finite_axis(rotation.Pitch) or not finite_axis(rotation.Yaw) or not finite_axis(rotation.Roll) then
        error("broker_fixture_geometry_unqualified", 0)
    end
    local aim = { Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll }
    local reach = radius
    local last_probe = "no_floor"
    for _, offset in ipairs(offsets) do
        local xy = { anchor.X + offset[1], anchor.Y + offset[2] }
        for _, height in ipairs({ 180, 320, 450 }) do
            local floor, reason = find_floor(library, context, pawn, target, xy, anchor.Z + height, anchor.Z - 375)
            if floor then
                local place = { X = xy[1], Y = xy[2], Z = floor + half_height + 2 }
                last_probe = string.format("floor=%.1f capsule=%.1f in_reach=%s", floor, half_height,
                    tostring(within(place, center, reach)))
                if within(place, center, reach) and pawn:K2_TeleportTo(place, aim) then
                    local actual = pawn:K2_GetActorLocation()
                    if same(pawn:GetWorld(), context.world) and vector_qualified(actual)
                        and within(actual, place, 25) and within(actual, target:K2_GetActorLocation(), reach) then
                        pawn:ForceNetUpdate()
                        return { map = state.map, pawn = pawn_name, target = target:GetFullName(), point = point,
                            x = actual.X, y = actual.Y, z = actual.Z }
                    end
                end
            elseif not last_probe:find("floor=", 1, true) and reason then
                last_probe = reason
            end
        end
    end
    error(string.format("broker_fixture_no_safe_stand_point (%s at %.1f,%.1f,%.1f reach=%.1f)",
        last_probe, center.X, center.Y, center.Z, reach), 0)
end

local function guid_parts(id)
    if type(id) ~= "string" or #id ~= 32 or id:find("[^0-9A-F]") or not id:find("[^0]") then
        error("broker_fixture_manifest_guid_unqualified", 0)
    end
    local parts = {}
    for index, name in ipairs({ "A", "B", "C", "D" }) do
        local word = tonumber(id:sub((index - 1) * 8 + 1, index * 8), 16)
        parts[name] = word >= 2147483648 and word - 4294967296 or word
    end
    return parts
end

local function count_guid(array, limit, expected)
    local matches = 0
    for index = 1, count(array, limit) do
        local item = array[index]
        if item and guid(item[item_guid], true) == expected then matches = matches + 1 end
    end
    return matches
end

local function retire_source(source)
    if not valid(source) or source:IsActorBeingDestroyed() then return end
    source:SetActorEnableCollision(false)
    source:SetActorHiddenInGame(true)
    source:K2_DestroyActor()
end

local function fixture_grant(profile, state, phase, pawn_name, expected_guid)
    if not fixtures or (profile.debt and not drill_fixtures) then error("broker_fixtures_disabled", 0) end
    local parts = guid_parts(expected_guid)
    local context = host_context(state, phase, pawn_name)
    local _, key, objective, reward = quest_objects(profile)
    local before = describe(profile, context, key, objective, reward)
    if before.state == "completed" then error("broker_fixture_quest_completed", 0) end
    local backpack = context.save.PlayerBackpack
    local matches = count_guid(backpack, 1024, expected_guid)
        + count_guid(context.save.PlayerEquipment, 128, expected_guid)
    if before.manifest_guid == expected_guid and matches == 1 then return before end
    if before.manifest_guid ~= "-" or matches > 0 then
        error("broker_fixture_manifest_already_present", 0)
    end
    local before_count = count(backpack, 1023)
    local position = context.host:K2_GetActorLocation()
    if not vector_qualified(position) then error("broker_fixture_host_position_unqualified", 0) end

    local source = context.world:SpawnActor(objective, { X = position.X + 125, Y = position.Y, Z = position.Z + 10 },
        { Pitch = 0, Yaw = 0, Roll = 0 })
    if not valid(source) then error("broker_fixture_manifest_spawn_failed", 0) end
    local ok, failure = pcall(function()
        if not same(source:GetWorld(), context.world) or not same(source:GetClass(), objective)
            or not source:HasAuthority() or source:IsActorBeingDestroyed() then
            error("broker_fixture_manifest_source_unqualified", 0)
        end
        -- Item_Master_BP.PutInInventory delegates to this stock actor-to-backpack path.
        context.instance:AddItemToInventory(source, false)
    end)
    retire_source(source)
    if not ok then error(failure, 0) end

    local native_guid, slot = stored_manifest(context.save, objective)
    backpack = context.save.PlayerBackpack
    if not same(context.instance.SaveSlot_Ref, context.save) or not same(context.host.Save, context.save)
        or count(backpack, 1024) ~= before_count + 1 or native_guid == "-" or slot ~= before_count + 1 then
        error("broker_fixture_manifest_stock_pickup_unverified", 0)
    end
    -- Stock conversion makes a fresh GUID. Replace only the new row's GUID with the fixture identity.
    backpack[slot][item_guid] = parts
    local after = describe(profile, context, key, objective, reward)
    if after.manifest_guid ~= expected_guid or after.step ~= before.step
        or after.native_cash ~= before.native_cash or after.native_xp ~= before.native_xp then
        error("broker_fixture_manifest_guid_readback_failed", 0)
    end
    if profile.debt and (after.topic_micro_drill ~= math.max(1, before.topic_micro_drill)
        or after.native_debt ~= before.native_debt or after.voucher_rows ~= before.voucher_rows
        or after.topic_epc ~= before.topic_epc or after.topic_drill_lab ~= before.topic_drill_lab
        or after.freelancer_rep ~= before.freelancer_rep or after.fae_rep ~= before.fae_rep) then
        error("mutation_unverified_broker_fixture_drill_pickup", 0)
    end
    after.changed = true
    return after
end

local function bind(profile, fixture_allowed)
    return {
        inspect = function(state, phase, pawn_name) return inspect(profile, state, phase, pawn_name) end,
        issue = function(state, phase, pawn_name, target) return issue(profile, state, phase, pawn_name, target, false) end,
        issue_fixture = function(state, phase, pawn_name, target)
            if not fixture_allowed then error("broker_fixtures_disabled", 0) end
            return issue(profile, state, phase, pawn_name, target, true)
        end,
        turn_in = function(state, phase, pawn_name, target, id)
            return turn_in(profile, state, phase, pawn_name, target, id)
        end,
        seed_fixture = function(state, phase, pawn_name, id)
            return fixture_grant(profile, state, phase, pawn_name, id)
        end,
        fixture_position = broker.fixture_position,
    }
end

local legacy = bind(manifest, fixtures)
broker.inspect = legacy.inspect
broker.issue = legacy.issue
broker.issue_fixture = legacy.issue_fixture
broker.turn_in = legacy.turn_in
function broker.fixture_grant_manifest(state, phase, expected_guid)
    return fixture_grant(manifest, state, phase, nil, expected_guid)
end
broker.drill = bind(drill, drill_fixtures)

return broker
