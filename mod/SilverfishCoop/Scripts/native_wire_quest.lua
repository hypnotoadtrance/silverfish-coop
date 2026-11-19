local guest_save = require("guest_save")
local players = require("players")

local meet = {
    map = "/Game/Classes/Maps/Maps_SafeZones/Borough",
    quest_id = "Clt_Wire",
    objective_class = "/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C",
    reward_classes = {
        "/Game/Classes/Weapons/Wep_Nagant_Scoped.Wep_Nagant_Scoped_C",
        "/Game/Classes/Items/Ammo_30-06_Incendiary_BP.Ammo_30-06_Incendiary_BP_C",
    },
    commune_id = 2,
    commune_reward = 20,
}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_EVENTS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_DEATH") == "cpp"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local world_path = meet.map .. ".Borough"
local board_path = world_path .. ":PersistentLevel.BountyBoard_BP_C_3"
meet.board_name = "BountyBoard_BP_C " .. board_path

local board_class = "/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local quest_table = "/Game/Classes/Libraries/DataTables/Quests_DT.Quests_DT"
local quest_schema = "/Game/Classes/Libraries/Structs/Quest_Struct.Quest_Struct"
local character_library = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"

local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local final_field = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B"
local cash_field = "Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0"
local xp_field = "Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797"
local items_field = "Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A"
local objective_items = "Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083"
local objective_consumed = "ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333"
local redeem_field = "RedeemAt_83_FAE34B6C471D2019508A4898E9A0080F"
local ping_field = "GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F"
local prereq_field = "PreReq_Flags_105_3FA9C82A48BE1675F41E15A54570DBAD"
local reputation_field = "ReputationGain_86_DA02B32344A59A6C236B848B22520EEC"
local completion_flags = "Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F"
local completion_quests = "Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9"
local boards_field = "AppearOn_Boards_95_2FDB27AE4695B9213302E5897144A531"
local global_board_field = "AppearOn_GlobalBountyBoards_78_B538291F4816D684913EAA8852507698"
local type_field = "QuestType_47_1031681D471E9929E30B17985E174BBE"
local difficulty_field = "Difficulty_12_9F1A01AD46151E4F5F152B82C99B5D85"
local fail_field = "Fail_OnDeath_9_BAADF6C6450D1EB972F6E8A33214ACE4"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_quantity = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"

local board_id, xp_reward, cash_reward, final_step = 5, 1, 0, 2
local preflight_op = "native-wire-quest-preflight"
local start_op = "native-wire-quest-start"
local approach_op = "native-wire-quest-approach"
local last

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
        and not object:GetFullName():find("Default__", 1, true)
end

local function refused(reason)
    error("stock_wire_quest_" .. reason, 0)
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function int32(value)
    return type(value) == "number" and math.tointeger(value) ~= nil
        and value >= -2147483648 and value <= 2147483647
end

local function fname(text)
    local value = FName(text, EFindName.FNAME_Find)
    if value:GetComparisonIndex() == 0 or value:ToString() ~= text then refused("name_unavailable") end
    return value
end

local function flag(instance, key)
    local result = {}
    instance:Check_SaveFlag(type(key) == "string" and fname(key) or key, result, result)
    if type(result.Found) ~= "boolean" or not int32(result.Value) then refused("flag_unqualified") end
    return result.Found and result.Value or 0, result.Found
end

local function commune_flag(context)
    local library = StaticFindObject(character_library)
    if not valid(library) then refused("reputation_library_unavailable") end
    local result = {}
    library:Compose_FactionRep_FlagName(meet.commune_id, context.host, result)
    local key = result.Name
    if type(key) ~= "userdata" or key:GetComparisonIndex() == 0 or not key:ToString():match("^Rep_") then
        refused("reputation_flag_unqualified")
    end
    return flag(context.instance, key)
end

local function controlled(detail, world, local_player)
    local pawn, controller = detail and detail.pawn, detail and detail.controller_object
    return exact(pawn, pawn_class) and exact(controller, controller_class)
        and pawn:GetFullName() == detail.name and not pawn:IsActorBeingDestroyed()
        and not controller:IsActorBeingDestroyed() and pawn:HasAuthority() and controller:HasAuthority()
        and pawn:IsLocallyControlled() == local_player and controller:IsLocalController() == local_player
        and same(pawn.Controller, controller) and same(controller.Pawn, pawn)
        and same(pawn:GetWorld(), world) and same(controller:GetWorld(), world)
end

local function live_board(world, instance)
    local board = StaticFindObject(board_path)
    if not exact(board, board_class) or board:GetFullName() ~= meet.board_name
        or not same(board:GetWorld(), world) or not board:HasAuthority() or board:IsActorBeingDestroyed()
        or not same(board["As Game Instance FP"], instance)
        or board.TurnIn_ID ~= board_id or board.BountyBoard_RedeemableHere ~= true
        or board.AcceptAllGlobalContracts ~= true or board.Display_GlobalContracts ~= false
        or board.ShowAllContracts ~= false then
        refused("board_unqualified")
    end
    local box = board.Box
    if not exact(box, "/Script/Engine.BoxComponent") or not same(box:GetOwner(), board) then
        refused("board_box_unqualified")
    end
    local collision = box:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then refused("board_not_interactable") end
    return board, box
end

local function vector(point)
    return point and finite(point.X) and finite(point.Y) and finite(point.Z)
end

local function distance_squared(first, second)
    return (first.X - second.X) ^ 2 + (first.Y - second.Y) ^ 2 + (first.Z - second.Z) ^ 2
end

local function interaction_geometry(pawn, board, box)
    local reach = pawn.InteractionRange
    if not finite(reach) or reach <= 0 or reach > 1000 then refused("interaction_range_unqualified") end
    local origin, anchor = board:K2_GetActorLocation(), box:K2_GetComponentLocation()
    if not vector(origin) or not vector(anchor) then refused("position_unqualified") end
    return origin, anchor, reach
end

local function in_reach(point, origin, anchor, reach)
    return vector(point) and distance_squared(point, origin) <= reach ^ 2
        and distance_squared(point, anchor) <= reach ^ 2
end

local function context_for(state, phase, name, approach)
    if type(name) ~= "string" or #name > 512 or name:find("%c") or not name:match("^Player_FP_C ") then
        refused("pawn_name_unqualified")
    end
    local host, world = state.local_pawn, state.world
    if not enabled or phase ~= "hosting" or state.map ~= meet.map or state.listening ~= true or state.server ~= false
        or not valid(world) or world:GetFullName() ~= "World " .. world_path
        or not controlled(host, world, true) or not same(state.controller, host.controller_object) then
        refused("listen_host_unavailable")
    end
    local driver = world.NetDriver
    if not valid(driver) or not same(driver.World, world) or valid(driver.ServerConnection) then
        refused("listen_driver_unqualified")
    end

    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(host.pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, instance_class) or not exact(save, save_class)
        or not same(host.pawn.Save, save) or not same(instance["As Player FP"], host.pawn) then
        refused("canonical_save_unqualified")
    end

    local pawn, receiving_save
    if name == host.name then
        if players.is_downed(host.pawn) ~= false then refused("actor_unavailable") end
        pawn, receiving_save = host.pawn, save
    else
        pawn, receiving_save = guest_save.exact_item_context(state, phase, name)
        if not exact(pawn, pawn_class) or not exact(receiving_save, save_class) or same(receiving_save, save)
            or not same(pawn.Save, receiving_save) or not players.is_standing_guest(pawn, name) then
            refused("guest_save_unqualified")
        end
    end
    if pawn.Dead ~= false or pawn:GetFullName() ~= name then refused("actor_unavailable") end

    local board, box = live_board(world, instance)
    local origin, anchor, reach = interaction_geometry(pawn, board, box)
    if not approach and not in_reach(pawn:K2_GetActorLocation(), origin, anchor, reach) then
        refused("board_out_of_reach")
    end
    return {world = world, host = host.pawn, instance = instance, save = save, interactor = pawn,
        receiving_save = receiving_save, board = board, key = fname(meet.quest_id),
        origin = origin, anchor = anchor, reach = reach}
end

local function count(array)
    return array and array:GetArrayNum()
end

function meet.objective_guid(save, class)
    local result
    for _, array in ipairs({save.PlayerBackpack, save.PlayerEquipment}) do
        local size = count(array)
        if not int32(size) or size < 0 or size > 4096 then refused("inventory_extent") end
        for row = 1, size do
            local item = array[row]
            if item and same(item[item_class], class) then
                local id, units = item[item_guid_field], item[item_quantity]
                if result or not id or not int32(id.A) or not int32(id.B) or not int32(id.C) or not int32(id.D)
                    or not int32(units) or units < 0 or units > 1 then refused("objective_requires_exact_single_wire") end
                local guid = string.format("%08X%08X%08X%08X", id.A & 0xffffffff, id.B & 0xffffffff,
                    id.C & 0xffffffff, id.D & 0xffffffff)
                if not guid:find("[^0]") then refused("objective_guid_unavailable") end
                result = guid
            end
        end
    end
    return result
end

local function recipe(row)
    if not row then refused("quest_row_unavailable") end
    local items, boards = row[items_field], row[boards_field]
    local objectives = row[objective_items]
    local rep, prereqs, flags = row[reputation_field], row[prereq_field], row[completion_flags]
    local ping = row[ping_field]
    if type(ping) == "userdata" then ping = ping:ToString() end
    if row[final_field] ~= final_step or row[cash_field] ~= cash_reward or row[xp_field] ~= xp_reward
        or row[redeem_field] ~= board_id or row[type_field] ~= 2 or row[difficulty_field] ~= 0
        or row[fail_field] ~= false or row[global_board_field] ~= true
        or row[objective_consumed] ~= true or count(objectives) ~= 1 or not valid(objectives[1])
        or objectives[1]:GetFullName() ~= "BlueprintGeneratedClass " .. meet.objective_class
        or count(row[completion_quests]) ~= 0 or not prereqs or #prereqs ~= 0
        or not flags or #flags ~= 0 or ping ~= "None"
        or not rep or #rep ~= 1 or not rep:Contains(meet.commune_id)
        or rep:Find(meet.commune_id):get() ~= meet.commune_reward
        or count(boards) ~= 1 or boards[1] ~= board_id
        or count(items) ~= #meet.reward_classes then refused("quest_recipe_unqualified") end
    for index, path in ipairs(meet.reward_classes) do
        if not valid(items[index]) or items[index]:GetFullName() ~= "BlueprintGeneratedClass " .. path then
            refused("reward_class_unqualified")
        end
    end
    return {items[1], items[2]}
end

local function row_for(rows, key)
    if not rows then refused("quest_map_unavailable") end
    local present = rows:Contains(key)
    if type(present) ~= "boolean" then refused("quest_map_unqualified") end
    if not present then return nil end
    local row = rows:Find(key):get()
    if not row then refused("quest_row_unavailable") end
    return row
end

function meet.preflight(state, phase, name)
    local context = context_for(state, phase, name)
    local source = StaticFindObject(quest_table)
    local schema = valid(source) and source:GetRowStruct()
    if not valid(source) or source:GetFullName() ~= "DataTable " .. quest_table
        or not valid(schema) or schema:GetFullName() ~= "UserDefinedStruct " .. quest_schema then
        refused("quest_table_unqualified")
    end
    context.template = source:FindRow(meet.quest_id)
    context.rewards = recipe(context.template)
    context.objective = context.template[objective_items][1]
    if context.template[step_field] ~= 1 then refused("quest_template_step_unqualified") end

    local dead = flag(context.instance, "PlayerKill_InBorough")
    local cash = flag(context.instance, "Player_Cash")
    local xp = flag(context.instance, "Player_Xp")
    local reputation, reputation_found = commune_flag(context)
    if dead > 0 or xp < context.template[difficulty_field] then refused("quest_unavailable") end

    local active = row_for(context.save.ActiveQuests, context.key)
    local step = active and active[step_field]
    if active then
        recipe(active)
        if step ~= 1 and step ~= final_step and step ~= -10 then refused("quest_step_unqualified") end
    else
        -- The stock board populates this transient catalog when someone opens it.
        local offered = row_for(context.board.Contracts, context.key)
        if not offered then
            context.board:Configure_Stock()
            offered = row_for(context.board.Contracts, context.key)
        end
        if not offered or offered[step_field] ~= 1 then refused("quest_not_offered") end
        recipe(offered)
    end

    local objective, owner
    if step and step ~= -10 then
        objective = meet.objective_guid(context.save, context.objective)
        if objective then
            owner = "host"
        elseif not same(context.receiving_save, context.save) then
            objective = meet.objective_guid(context.receiving_save, context.objective)
            if objective then owner = "guest" end
        end
    end
    return context, {
        state = not active and "missing" or step == 1 and "active" or step == final_step and "ready" or "completed",
        step = step or "-", native_cash = cash, native_xp = xp,
        native_commune_rep = reputation, commune_rep_found = reputation_found,
        objective_guid = objective or "-", objective_owner = owner or "-",
        changed = false,
    }
end

local floor_offsets = {{0, 110}, {110, 0}, {0, -110}, {-110, 0}, {85, 85}, {-85, 85}, {85, -85}, {-85, -85}}
local trace_color = {R = 0, G = 0, B = 0, A = 0}

local function approach_board(state, phase, name)
    if not fixtures then refused("fixture_disabled") end
    local context = context_for(state, phase, name, true)
    local pawn, board, anchor = context.interactor, context.board, context.anchor
    local capsule = pawn.CapsuleComponent
    local half_height = valid(capsule) and capsule:GetScaledCapsuleHalfHeight()
    local original, rotation = pawn:K2_GetActorLocation(), pawn:K2_GetActorRotation()
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not vector(original) or not rotation or not finite(rotation.Pitch) or not finite(rotation.Yaw)
        or not finite(rotation.Roll) or not finite(half_height) or half_height <= 0
        or not valid(trace) then refused("fixture_geometry_unqualified") end
    local original_location = {X = original.X, Y = original.Y, Z = original.Z}
    local aim = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll}
    local ignored = {pawn, board}
    if not same(context.host, pawn) then ignored[#ignored + 1] = context.host end
    local moved = false
    for _, offset in ipairs(floor_offsets) do
        local x, y = anchor.X + offset[1], anchor.Y + offset[2]
        for _, height in ipairs({150, 250, 400}) do
            local from, to = {X = x, Y = y, Z = anchor.Z + height}, {X = x, Y = y, Z = anchor.Z - 380}
            local hit = {}
            local found = trace:LineTraceSingle(pawn, from, to, 0, false, ignored, 0, hit, true,
                trace_color, trace_color, 0)
            hit = hit.OutHit or hit
            if found == true and vector(hit.ImpactPoint) and vector(hit.ImpactNormal)
                and hit.ImpactNormal.Z >= 0.7 and hit.ImpactPoint.Z < from.Z - 1 and hit.ImpactPoint.Z >= to.Z then
                local target = {X = x, Y = y, Z = hit.ImpactPoint.Z + half_height + 2}
                if in_reach(target, context.origin, anchor, context.reach) and pawn:K2_TeleportTo(target, aim) then
                    moved = true
                    local actual = pawn:K2_GetActorLocation()
                    if same(pawn:GetWorld(), context.world) and in_reach(actual, context.origin, anchor, context.reach)
                        and distance_squared(actual, target) <= 625 then
                        pawn:ForceNetUpdate()
                        return "exact stock Copper Wire board approach verified"
                    end
                end
            end
        end
    end
    if moved then
        if not pawn:K2_TeleportTo(original_location, aim) then
            error("mutation_unverified_stock_wire_quest_approach", 0)
        end
        local actual = pawn:K2_GetActorLocation()
        if not vector(actual) or distance_squared(actual, original_location) > 625 then
            error("mutation_unverified_stock_wire_quest_approach", 0)
        end
        pawn:ForceNetUpdate()
    end
    refused("fixture_no_safe_stand_point")
end

function meet.command(fields, state, phase)
    local operation = fields[2]
    if operation ~= preflight_op and operation ~= start_op and operation ~= approach_op then return end
    last = nil
    if #fields ~= 3 then refused("exact_pawn_required") end
    local name = fields[3]
    if operation == approach_op then return approach_board(state, phase, name) end
    local context, proof = meet.preflight(state, phase, name)
    if operation == start_op and proof.state == "missing" then
        local function start_and_read()
            context.instance:AddQuest_ByName(context.key)
            return meet.preflight(state, phase, name)
        end
        local checked, after_context, after = pcall(start_and_read)
        if not checked or after.state ~= "active" or after.step ~= 1
            or not same(context.world, after_context.world) or not same(context.board, after_context.board)
            or not same(context.save, after_context.save) or not same(context.instance, after_context.instance)
            or not same(context.host, after_context.host) or not same(context.interactor, after_context.interactor)
            or not same(context.receiving_save, after_context.receiving_save)
            or proof.native_cash ~= after.native_cash or proof.native_xp ~= after.native_xp
            or proof.native_commune_rep ~= after.native_commune_rep
            or proof.commune_rep_found ~= after.commune_rep_found then
            error("mutation_unverified_stock_wire_quest_start", 0)
        end
        proof = after
        proof.changed = true
    end
    last = {operation = operation, name = name, context = context, proof = proof}
    return "exact stock Copper Wire state verified"
end

function meet.rows(state, phase, status_row)
    if not last or phase ~= "hosting" or state.map ~= meet.map or not same(state.world, last.context.world) then
        last = nil
        return {}
    end
    local context, proof = last.context, last.proof
    local found = false
    for _, detail in ipairs(state.pawns) do
        if detail.name == last.name and same(detail.pawn, context.interactor) then found = true end
    end
    if not found or not valid(context.board) or not valid(context.instance) or not valid(context.host)
        or not same(context.instance.SaveSlot_Ref, context.save) or not same(context.host.Save, context.save)
        or not same(context.interactor.Save, context.receiving_save) then
        last = nil
        return {}
    end
    return {status_row("native-wire-quest", last.operation, meet.map, last.name, meet.board_name,
        meet.quest_id, proof.state, proof.step, final_step, cash_reward, xp_reward, meet.commune_reward,
        proof.changed and 1 or 0, proof.native_cash, proof.native_xp, proof.native_commune_rep,
        proof.objective_guid, proof.objective_owner)}
end

return meet
