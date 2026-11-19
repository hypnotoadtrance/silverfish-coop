local broker = {}

local map = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
local level = map .. ".Shop_BlackMarket_01:PersistentLevel."
local npc_name = "NPC_Broker_C " .. level .. "NPC_Broker_C_1"
local dialogue_actor_name = "Dialogue_Controler_BP_C " .. level .. "Dialogue_Controler_BP_C_3"
local station_name = "ItemRequest_TradeStation_BP_C " .. level .. "ItemRequest_TradeStation_BP_C_1"
local dialogue_table = "/Game/Classes/Libraries/Tables_Dialogue/Dialogue_Broker_DT.Dialogue_Broker_DT"
local dialogue_schema = "/Game/Classes/Libraries/Structs/Conversation_Struct.Conversation_Struct"
local quest_table = "/Game/Classes/Libraries/DataTables/Quests_DT.Quests_DT"
local quest_schema = "/Game/Classes/Libraries/Structs/Quest_Struct.Quest_Struct"
local quest_library = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local manifest_path = "/Game/Classes/Items/Books_Items/Item_EtrCartManifest.Item_EtrCartManifest_C"
local quest_id, previous_quest, issue_row = "Clct_EManifest", "Clt_DrillHead_Brkr", "Broker_Questline_02"
local stock_ping = "BrokerQuest_03"
local reward_cash, reward_xp, buyer = 10000, 1, 1
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"

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
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_quantity = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"

local responses_field = "PlayerResponces_6_DA452ED342AF8EEEC7BD299C134AF0F3"
local activate_field = "Activate_Quests_22_323EA585468EFFDA9A12A188EBDF5022"
local terminal_field = "Terminate_Convo_5_BAA128604982D7FC877B498C6500026C"
local response_arrays = {
    "Check_TurnIn_Quests_20_4B851D164A4A4164B7B74D80DE4EED23",
    "Add_JournalEntries_17_E77934214DFD7B85991F729C59678191",
    "GlobalPings_38_DB12D37348CDE946D7BA20BD3DE97CDD",
}
local response_maps = {
    "Add_SaveFlags_13_0FEB9B204B1F6F013B6309BF12311EF7",
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

local function qualify_recipe(quest, expected_objective)
    if not quest or quest[final_field] ~= 2 or quest[cash_field] ~= reward_cash or quest[xp_field] ~= reward_xp
        or quest[redeem_field] ~= buyer or quest[objective_consumed] ~= true
        or not quest[prereq_field] or #quest[prereq_field] ~= 0
        or count(quest[rewards_field], 32) ~= 0 or count(quest[objective_items], 32) ~= 1 then
        error("stock_broker_quest_recipe_changed", 0)
    end
    local objective, ping = quest[objective_items][1], quest[ping_field]
    if type(ping) == "userdata" then ping = ping:ToString() end
    if not valid(objective) or objective:GetFullName() ~= "BlueprintGeneratedClass " .. manifest_path
        or (expected_objective and not same(objective, expected_objective)) or ping ~= stock_ping then
        error("stock_broker_quest_objective_changed", 0)
    end
    return objective
end

local function stock_flag(instance, name)
    local out = {}
    instance:Check_SaveFlag(fname(name), out, out)
    if type(out.Found) ~= "boolean" or not int32(out.Value) then error("stock_broker_player_stat_unqualified", 0) end
    return out.Found and out.Value or 0
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

local function describe(context, key, objective)
    if not same(context.instance.SaveSlot_Ref, context.save) or not same(context.host.Save, context.save) then
        error("broker_canonical_save_changed", 0)
    end
    local details = live_quest(context.save, key)
    local step = details and details[step_field] or nil
    if details then
        qualify_recipe(details, objective)
        if step ~= 1 and step ~= 2 and step ~= -10 then error("stock_broker_quest_step_unqualified", 0) end
    end
    return { quest_id = quest_id, state = not details and "missing" or step == -10 and "completed"
            or step == 2 and "ready" or "active",
        step = step or "-", final_step = 2, cash = reward_cash, xp = reward_xp, changed = false,
        manifest_guid = stored_manifest(context.save, objective),
        native_cash = stock_flag(context.instance, "Player_Cash"),
        native_xp = stock_flag(context.instance, "Player_Xp"),
    }
end

local function quest_objects()
    local quests = data_table(quest_table, quest_schema)
    local template = quests:FindRow(quest_id)
    local objective = qualify_recipe(template)
    if template[step_field] ~= 1 then error("stock_broker_quest_template_changed", 0) end
    return quests, fname(quest_id), objective
end

function broker.inspect(state, phase, pawn_name)
    local context = host_context(state, phase, pawn_name)
    local _, key, objective = quest_objects()
    return describe(context, key, objective)
end

local function qualify_issue(context, quests, expected_npc)
    if expected_npc ~= npc_name then error("broker_issue_actor_unqualified", 0) end
    require_reach(context.interactor, actor(context.world, "NPC_Broker_C", npc_name))
    local controller = actor(context.world, "Dialogue_Controler_BP_C", dialogue_actor_name)
    local dialogue = data_table(dialogue_table, dialogue_schema)
    local pointer = controller.ConversationPointer
    local key = fname(issue_row)
    if controller.Prioroty ~= true or not pointer or not same(pointer.DataTable, dialogue)
        or not same_name(pointer.RowName, key) then error("stock_broker_dialogue_pointer_changed", 0) end
    local row = dialogue:FindRow(issue_row)
    local responses = row and row[responses_field]
    if count(responses, 64) ~= 1 then error("stock_broker_dialogue_response_unqualified", 0) end
    local response = responses[1]
    local activate = response[activate_field]
    if response[terminal_field] ~= true or count(activate, 32) ~= 1
        or not same(activate[1].DataTable, quests) or not same_name(activate[1].RowName, fname(quest_id)) then
        error("stock_broker_dialogue_response_changed", 0)
    end
    for _, name in ipairs(response_arrays) do
        if count(response[name], 32) ~= 0 then error("stock_broker_dialogue_effects_changed", 0) end
    end
    for _, name in ipairs(response_maps) do
        if not response[name] or #response[name] ~= 0 then error("stock_broker_dialogue_flags_changed", 0) end
    end
end

local function issue(state, phase, pawn_name, expected_npc, use_fixture)
    local context = host_context(state, phase, pawn_name)
    local quests, key, objective = quest_objects()
    qualify_issue(context, quests, expected_npc)
    local before = describe(context, key, objective)
    if before.state ~= "missing" then return before end
    if not use_fixture then
        local previous = live_quest(context.save, fname(previous_quest))
        if not previous or previous[step_field] ~= -10 then error("broker_predecessor_incomplete", 0) end
    end

    -- This response has one quest activation and no save flags, reputation, journal, or event side effects.
    context.instance:AddQuest_ByName(key)
    local after = describe(context, key, objective)
    if after.step ~= 1 or after.manifest_guid ~= before.manifest_guid
        or after.native_cash ~= before.native_cash or after.native_xp ~= before.native_xp then
        error("stock_broker_issue_readback_failed", 0)
    end
    after.changed = true
    return after
end

function broker.issue(state, phase, pawn_name, expected_npc)
    return issue(state, phase, pawn_name, expected_npc, false)
end

function broker.issue_fixture(state, phase, pawn_name, expected_npc)
    if not fixtures then error("broker_fixtures_disabled", 0) end
    return issue(state, phase, pawn_name, expected_npc, true)
end

function broker.turn_in(state, phase, pawn_name, expected_station, expected_guid)
    local context = host_context(state, phase, pawn_name)
    if expected_station ~= station_name then error("broker_redemption_station_unqualified", 0) end
    local station = actor(context.world, "ItemRequest_TradeStation_BP_C", station_name)
    if station.Buyer ~= buyer then error("stock_broker_redemption_buyer_changed", 0) end
    require_reach(context.interactor, station)

    local _, key, objective = quest_objects()
    local before = describe(context, key, objective)
    if before.state == "completed" then return before end
    if before.state == "missing" then error("broker_quest_not_started", 0) end
    if type(expected_guid) ~= "string" or #expected_guid ~= 32 or expected_guid:find("[^0-9A-F]")
        or before.manifest_guid ~= expected_guid then error("broker_manifest_guid_mismatch", 0) end
    if not int32(before.native_cash + reward_cash) or not int32(before.native_xp + reward_xp) then
        error("broker_stock_reward_out_of_range", 0)
    end
    local library = StaticFindObject(quest_library)
    if not valid(library) then error("stock_broker_quest_library_unavailable", 0) end

    -- Stock turn-in owns the completion, item debit, cash/XP, and authored BrokerQuest_03 world ping.
    local out = {}
    library:TurnIn_Quest(key, context.instance, true, context.host, out, out)
    local after = describe(context, key, objective)
    if out.Success ~= true or valid(out.ConfirmationPopup) or after.step ~= -10 or after.manifest_guid ~= "-"
        or after.native_cash ~= before.native_cash + reward_cash or after.native_xp ~= before.native_xp + reward_xp then
        error("stock_broker_turnin_readback_failed", 0)
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
    if found == true and hit.bBlockingHit == true and hit.bStartPenetrating == false
        and vector_qualified(hit.ImpactPoint) and vector_qualified(hit.ImpactNormal) and hit.ImpactNormal.Z >= 0.7 then
        return hit.ImpactPoint.Z
    end
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
    for _, offset in ipairs(offsets) do
        local xy = { anchor.X + offset[1], anchor.Y + offset[2] }
        for _, height in ipairs({ 180, 320, 450 }) do
            local floor = find_floor(library, context, pawn, target, xy, anchor.Z + height, anchor.Z - 375)
            if floor then
                local place = { X = xy[1], Y = xy[2], Z = floor + half_height + 2 }
                if within(place, center, reach) and pawn:K2_TeleportTo(place, aim) then
                    local actual = pawn:K2_GetActorLocation()
                    if same(pawn:GetWorld(), context.world) and vector_qualified(actual)
                        and within(actual, place, 25) and within(actual, target:K2_GetActorLocation(), reach) then
                        pawn:ForceNetUpdate()
                        return { map = state.map, pawn = pawn_name, target = target:GetFullName(), point = point,
                            x = actual.X, y = actual.Y, z = actual.Z }
                    end
                end
            end
        end
    end
    error("broker_fixture_no_safe_stand_point", 0)
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
    if valid(source) and not source:IsActorBeingDestroyed() then source:K2_DestroyActor() end
    if valid(source) and not source:IsActorBeingDestroyed() then error("broker_fixture_manifest_source_still_live", 0) end
end

function broker.fixture_grant_manifest(state, phase, expected_guid)
    if not fixtures then error("broker_fixtures_disabled", 0) end
    local parts = guid_parts(expected_guid)
    local context = host_context(state, phase)
    local _, key, objective = quest_objects()
    local before = describe(context, key, objective)
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
    local after = describe(context, key, objective)
    if after.manifest_guid ~= expected_guid or after.step ~= before.step
        or after.native_cash ~= before.native_cash or after.native_xp ~= before.native_xp then
        error("broker_fixture_manifest_guid_readback_failed", 0)
    end
    after.changed = true
    return after
end

return broker
