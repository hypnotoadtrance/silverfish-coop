local tape = {}
local guest_save = require("guest_save")
local mosby = require("native_dialogue_mosby")
local players = require("players")
local wire = require("native_wire_quest")
local zones = require("zone_maps")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local expanded = os.getenv("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES") == "1"
local scientist, maze = mosby.map, "/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P"
local station_class = "/Game/Classes/Actors/DataTape_DownloadStation_BP.DataTape_DownloadStation_BP_C"
local station_path = maze .. ".StoneMaze_P:PersistentLevel.DataTape_DownloadStation_BP_C_1"
local station_name = "DataTape_DownloadStation_BP_C " .. station_path
local quest = "Clt_Tape_StoneMaze_01"
local board_path = mosby.world .. ":PersistentLevel.BountyBoard_BP_C_2"
local board_name = "BountyBoard_BP_C " .. board_path
local board = {map = scientist, world_path = mosby.world, board_path = board_path, board_name = board_name, board_id = 4, quest_id = quest}
local blank_path = "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"
local product_path = "/Game/Classes/Items/DataTapes/DataTape_StoneMaze_01.DataTape_StoneMaze_01_C"
local armor_path = "/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local code_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local character_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local offer_fields = {
    RemoveAfterTrade_1_B26933FA4BBC62F03E1EE481B5414BA2 = false,
    ChanceShown_4_18C5CA7A480470CDF14CAD8FBF228FF7 = 1,
    Cash_Give_10_7D278BDA423E1A8C582C23B5C1A91B6F = 0,
    Cash_Recive_12_FE560CBF4176840FDE3480B7FFB4C4F2 = 0,
    Hide_IfCantTrade_24_4698C1544884DAA7621280A142208174 = false,
}
local factions, rep_change = {2, 4, 5}, {[2] = 100, [4] = -40, [5] = -50}
local last

local function valid(object) return object and object:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end
local function exact(object, path) return valid(object) and same(object:GetClass(), StaticFindObject(path)) end
local function refuse(reason) error("native_mosby_tape_" .. reason, 0) end
local function int32(value) return type(value) == "number" and math.tointeger(value) and value >= -2147483648 and value <= 2147483647 end
local function guid(value) return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]") end
local function slot(value) return type(value) == "string" and #value == 39 and value:match("^SFCoop_[0-9a-f]+$") end
local function count(array) return array and array:GetArrayNum() end

local function identity(row)
    local id = row and row[guid_field]
    if not id or not int32(id.A) or not int32(id.B) or not int32(id.C) or not int32(id.D) then return end
    local value = string.format("%08X%08X%08X%08X", id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
    return guid(value) and value or nil
end

local function owned(save, class, expected)
    local result = {}
    for index, array in ipairs({save.PlayerBackpack, save.PlayerEquipment}) do
        local size = count(array)
        if not int32(size) or size < 0 or size > 4096 then refuse("inventory_unqualified") end
        for cursor = 1, size do
            local row = array[cursor]
            if same(row[class_field], class) then
                local id = identity(row)
                if not id or result[id] then refuse("inventory_identity_unqualified") end
                result[id] = index == 1
            elseif expected and identity(row) == expected then refuse("inventory_guid_class_changed") end
        end
    end
    return result
end

local function singleton(rows)
    local id, backpack = next(rows)
    if id and not next(rows, id) and backpack then return id end
end

local function scope(state, phase, required_map)
    local route = zones[state.map]
    local leaf = state.map:match("/([^/]+)$")
    local driver = valid(state.world) and state.world.NetDriver
    if not enabled or not route or route.expanded and not expanded or not leaf or phase ~= "hosting" or not state.listening
        or state.server or not valid(driver) or valid(driver.ServerConnection) or not same(driver.World, state.world)
        or state.world:GetFullName() ~= "World " .. state.map .. "." .. leaf or required_map and state.map ~= required_map then
        refuse("listen_scope_unavailable")
    end
    local progress, host = mosby.progress(state)
    if not same(host.instance["As Player FP"], host.host) then refuse("canonical_host_changed") end
    return progress, host
end

local function receiver(state, phase, name, host, host_allowed)
    local pawn, save
    if host_allowed and name == host.host:GetFullName() and players.is_downed(host.host) == false then pawn, save = host.host, host.save
    else
        pawn, save = guest_save.exact_item_context(state, phase, name)
        if not valid(pawn) or not exact(save, save_path) or same(save, host.save)
            or not players.is_standing_guest(pawn, name) then refuse("detached_guest_unavailable") end
    end
    if not same(pawn.Save, save) or pawn:GetFullName() ~= name or not pawn:HasAuthority() or pawn.Dead ~= false
        or not same(pawn:GetWorld(), state.world) then refuse("interactor_unavailable") end
    return pawn, save
end

local function within(a, b, range)
    return (a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2 <= range^2
end

local function reaches(pawn, station, box)
    local range = pawn.InteractionRange
    if type(range) ~= "number" or range <= 0 or range > 1000 then return false end
    local point = pawn:K2_GetActorLocation()
    return within(point, station:K2_GetActorLocation(), range) and within(point, box:K2_GetComponentLocation(), range)
end

local function target_station(state)
    local station = StaticFindObject(station_path)
    local box = valid(station) and station.Box
    if not exact(station, station_class) or station:GetFullName() ~= station_name or not same(station:GetWorld(), state.world)
        or not station:HasAuthority() or station:IsActorBeingDestroyed() or not exact(box, "/Script/Engine.BoxComponent")
        or not same(box:GetOwner(), station) then refuse("exact_station_unavailable") end
    local anchor = station:K2_GetActorLocation()
    if not within(anchor, {X = 24873.440989, Y = -11925.632572, Z = 19.047219}, 3) then refuse("station_placement_changed") end
    return station, box
end

local function require_one(map, class)
    return map and #map == 1 and map:Contains(class) and map:Find(class):get() == 1
end

local function qualify_offer(station, blank, product)
    local database = station.Tapes_DataBase
    if not valid(blank) or not valid(product) or count(database) ~= 1 or not same(database[1], product) then refuse("station_database_changed") end
    station:Configure_Stock()
    local inventory = station.ShopInv
    local offers = inventory and inventory.TradeOffers_6_AAB7177E4D8899D984DE8F8674A7990F
    local row = count(offers) == 1 and offers[1]
    if not row then refuse("station_offer_unavailable") end
    for field, expected in pairs(offer_fields) do
        if row[field] ~= expected then refuse("station_offer_changed:" .. field) end
    end
    if not require_one(row.Items_Give_20_B4929C76420B737D08B6E7A56384E8EC, blank)
        or not require_one(row.Items_Recive_22_BF8DA3D74F10EFDF07693DB9957DFFCD, product) then refuse("station_offer_item_changed") end
end

local function approach(state, phase, name)
    if not fixtures then refuse("approach_unavailable") end
    if state.map == scientist then return wire.stock_board_approach(state, phase, name, board) end
    local _, host = scope(state, phase, maze)
    local pawn = receiver(state, phase, name, host, false)
    local station, box = target_station(state)
    local library, origin = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary"), box:K2_GetComponentLocation()
    local capsule = pawn.CapsuleComponent
    local height = valid(capsule) and capsule:GetScaledCapsuleHalfHeight()
    if not valid(library) or type(height) ~= "number" or not (height > 20 and height < 150) then refuse("approach_capsule_unavailable") end
    local color = {R = 0, G = 0, B = 0, A = 0}
    for _, delta in ipairs({{0, -140}, {0, 140}, {-140, 0}, {140, 0}, {-100, -100}, {100, -100}}) do
        local x, y = origin.X + delta[1], origin.Y + delta[2]
        local hit = {}
        local found = library:LineTraceSingle(pawn, {X = x, Y = y, Z = origin.Z + 90}, {X = x, Y = y, Z = origin.Z - 300},
            0, false, {pawn, host.host, station}, 0, hit, true, color, color, 0)
        hit = hit.OutHit or hit
        local point, normal = hit.ImpactPoint, hit.ImpactNormal
        if found == true and point and normal and normal.Z >= 0.7 then
            local place = {X = x, Y = y, Z = point.Z + height + 4}
            if within(place, origin, pawn.InteractionRange) and pawn:K2_TeleportTo(place, pawn:K2_GetActorRotation())
                and reaches(pawn, station, box) then pawn:ForceNetUpdate(); return "exact Stone Maze station approach verified" end
        end
    end
    refuse("approach_no_safe_floor")
end

local function exchange(state, phase, name, operation, blank_guid, replacement_guid)
    local before, host = scope(state, phase, maze)
    if before.step ~= 1 and before.step ~= 2 then refuse("stock_quest_not_active") end
    local pawn, save = receiver(state, phase, name, host, false)
    local station, box = target_station(state)
    if not reaches(pawn, station, box) then refuse("station_out_of_reach") end
    local blank, product = StaticFindObject(blank_path), StaticFindObject(product_path)
    qualify_offer(station, blank, product)
    local rows = owned(save, blank, blank_guid ~= "-" and blank_guid or nil)
    local held = blank_guid == "-" and singleton(rows) or rows[blank_guid] and blank_guid or nil
    if blank_guid ~= "-" and not guid(blank_guid) then refuse("blank_guid_unqualified") end
    local status, filled = "offer", "-"
    local checked, after = true, before
    if operation == "native-mosby-tape-convert" then
        if not guid(replacement_guid) or replacement_guid == blank_guid or type(SilverfishExchangeStoneMazeTape) ~= "function" then
            refuse("typed_conversion_unavailable") end
        local ok, reason, output = SilverfishExchangeStoneMazeTape(pawn:GetAddress(), save:GetAddress(), host.save:GetAddress(),
            blank_guid, replacement_guid)
        if ok ~= true then error(reason or "native_mosby_tape_conversion_refused", 0) end
        checked, after = pcall(function()
            local verified, current = scope(state, phase, maze)
            if not same(current.save, host.save) or not same(pawn.Save, save) or not same(target_station(state), station)
                or (reason ~= "exchanged" and reason ~= "already_exchanged") or output ~= replacement_guid
                or owned(save, blank, blank_guid)[blank_guid] ~= nil or owned(save, product, output)[output] ~= true then return end
            for key, value in pairs(before) do if verified[key] ~= value then return end end
            return verified
        end)
        if not checked or not after then error("mutation_unverified_stone_maze_tape_readback", 0) end
        status, filled, held = reason, output, blank_guid
    end
    last = {type = "exchange", operation = operation, map = maze, pawn = name, station = station_name, status = status,
        blank = held or "-", filled = filled, step = after.step, cash = after.cash, xp = after.xp, world = state.world}
    return "exact live Stone Maze station trade verified"
end

local function faction_state(context)
    local library = StaticFindObject(character_path)
    if not valid(library) or library:GetFullName() ~= "FL_Characters_C " .. character_path then refuse("reputation_library_unavailable") end
    local states = {}
    for _, faction in ipairs(factions) do
        local rep, advantage = {}, {}
        library:Compose_FactionRep_FlagName(faction, context.host, rep)
        library:Compose_FactionAdvantage_FlagName(faction, context.host, advantage)
        if not rep.Name or not advantage.Name or rep.Name:GetComparisonIndex() == 0 or advantage.Name:GetComparisonIndex() == 0 then
            refuse("reputation_flags_unavailable")
        end
        local amount, found = wire.stock_flag(context.instance, rep.Name)
        local credit, credit_found = wire.stock_flag(context.instance, advantage.Name)
        states[faction] = {rep = amount, found = found, advantage = credit, advantage_found = credit_found}
    end
    return states
end

local function qualify_settlement_row(source)
    local reputation = source and source.ReputationGain_86_DA02B32344A59A6C236B848B22520EEC
    local advantage = source and source.Advantage_Faction_100_F1B0B60547EEC0A936CFECB9BE005CE2
    local ping = source and source.GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F
    if type(ping) == "userdata" then ping = ping:ToString() end
    if not reputation or #reputation ~= #factions or not advantage or #advantage ~= 1 or not advantage:Contains(2)
        or advantage:Find(2):get() ~= 150 or ping ~= "None"
        or count(source.Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9) ~= 0 then
        refuse("quest_settlement_recipe_changed")
    end
    for _, faction in ipairs(factions) do
        if not reputation:Contains(faction) or reputation:Find(faction):get() ~= rep_change[faction] then
            refuse("quest_settlement_reputation_changed")
        end
    end
end

local function redeem(state, phase, name, operation, objective)
    local context = wire.stock_board_context(state, phase, name, false, board)
    local before, canonical = scope(state, phase, scientist)
    if not same(context.save, canonical.save) then refuse("board_canonical_save_changed") end
    if before.step ~= "-" then qualify_settlement_row(wire.stock_row(context.save.ActiveQuests, context.key)) end
    local product, armor = StaticFindObject(product_path), StaticFindObject(armor_path)
    if not valid(product) or not valid(armor) then refuse("typed_item_classes_unavailable") end
    local owned_objective = singleton(owned(context.receiving_save, product))
    local rep, after_rep = faction_state(context), nil
    local host_choice = same(context.receiving_save, context.save)
    local after, reward = before, "-"
    if operation == "native-mosby-readings-turn-in" then
        if type(SilverfishStageStoneMazeObjective) ~= "function" or type(SilverfishCaptureStoneMazeReward) ~= "function"
            or type(SilverfishGrantStoneMazeReward) ~= "function" then refuse("reward_capability_unavailable") end
        if (before.step ~= 1 and before.step ~= 2) or before.Mosby_SM_Readings >= 1 or not guid(objective)
            or objective ~= owned_objective then refuse("exact_objective_required") end
        local previous = owned(context.save, armor)
        local library, trait = StaticFindObject(code_path), {}
        local skill = FName("IntensePersonality", EFindName.FNAME_Find)
        if not valid(library) or library:GetFullName() ~= "FL_CodeLib01_C " .. code_path or skill:GetComparisonIndex() == 0 then
            refuse("stock_reward_library_unavailable") end
        context.instance:FindPassiveEffect(skill, true, trait, trait)
        if type(trait.EffectActive) ~= "boolean" or not int32(trait.Instances) or trait.Instances < 0
            or not int32(before.cash + 7500) or not int32(before.xp + 3) then refuse("reward_range_unavailable") end
        local expected = {}
        for _, faction in ipairs(factions) do
            expected[faction] = rep[faction].rep + rep_change[faction] * (rep[faction].found and 1 + trait.Instances or 1)
            if not int32(expected[faction]) then refuse("reputation_range_unavailable") end
        end
        if not host_choice then
            local ok, reason = SilverfishStageStoneMazeObjective(context.interactor:GetAddress(), context.receiving_save:GetAddress(),
                context.save:GetAddress(), objective)
            if ok ~= true then error(reason or "native_mosby_tape_objective_refused", 0) end
        end
        local checked, observed, verified_rep, gained = pcall(function()
            local output = {}
            library:TurnIn_Quest(context.key, context.instance, true, context.host, output, output)
            local verified, host = scope(state, phase, scientist)
            local updated_rep, current = faction_state(context), owned(context.save, armor)
            if output.Success ~= true or valid(output.ConfirmationPopup) or not same(host.save, context.save)
                or not same(context.interactor.Save, context.receiving_save) or verified.step ~= -10 or verified.Mosby_SM_Readings ~= 1
                or verified.Topic_EatherDrill ~= before.Topic_EatherDrill or verified.Topic_DrillData ~= before.Topic_DrillData
                or verified.cash ~= before.cash + 7500 or verified.xp ~= before.xp + 3
                or owned(context.save, product, objective)[objective] ~= nil
                or owned(context.receiving_save, product, objective)[objective] ~= nil then return end
            for _, faction in ipairs(factions) do
                local old, new = rep[faction], updated_rep[faction]
                if not new.found or new.rep ~= expected[faction] or new.advantage ~= old.advantage
                    or new.advantage_found ~= old.advantage_found then return end
            end
            local new
            for id, in_backpack in pairs(current) do
                if previous[id] == nil then if new or not in_backpack then return end; new = id end
            end
            for id, backpack in pairs(previous) do if current[id] ~= backpack then return end end
            return verified, updated_rep, new
        end)
        if not checked or not observed or not gained then error("mutation_unverified_stone_maze_redemption", 0) end
        after, after_rep, reward = observed, verified_rep, gained
    end
    last = {type = "redemption", operation = operation, map = scientist, pawn = name, board = board_name, before = before,
        after = after, rep = rep, after_rep = after_rep or rep, objective = owned_objective or "-",
        owner = owned_objective and (host_choice and "host" or "guest") or "-", reward = reward, world = state.world}
    return "exact Stone Maze Readings stock quest verified"
end

local function escrow(state, phase, operation, slot_name, template, name, assigned)
    if not slot(slot_name) or not guid(template) then refuse("escrow_identity_unavailable") end
    local progress, host = scope(state, phase, operation == "capture" and scientist or nil)
    if progress.step ~= -10 then refuse("stock_quest_not_completed") end
    local statics = StaticFindObject(statics_path)
    local class = StaticFindObject(save_path)
    if not valid(statics) or not valid(class) or type(SilverfishVerifyStoneMazeReward) ~= "function" then refuse("escrow_backend_unavailable") end
    local scratch
    if operation == "capture" then
        if type(SilverfishCaptureStoneMazeReward) ~= "function" or statics:DoesSaveGameExist(slot_name, 0) ~= false then
            refuse("escrow_capture_unavailable") end
        scratch = statics:CreateSaveGameObject(class)
        if not exact(scratch, save_path) or same(scratch, host.save) then refuse("escrow_create_unverified") end
        local ok, reason = SilverfishCaptureStoneMazeReward(host.host:GetAddress(), host.save:GetAddress(), scratch:GetAddress(), template)
        if ok ~= true or reason ~= "captured" or statics:SaveGameToSlot(scratch, slot_name, 0) ~= true then refuse("escrow_capture_unverified") end
    end
    local loaded = statics:LoadGameFromSlot(slot_name, 0)
    if not exact(loaded, save_path) or same(loaded, host.save) or same(loaded, scratch) then refuse("escrow_load_unverified") end
    local args = {loaded:GetAddress(), template}
    if scratch then args[3] = scratch:GetAddress() end
    local ok, reason = SilverfishVerifyStoneMazeReward(table.unpack(args))
    if ok ~= true or reason ~= "verified" then refuse("escrow_payload_unverified") end
    local status, recipient, id = "captured", host.host:GetFullName(), template
    if operation == "grant" then
        if not guid(assigned) or assigned == template or type(SilverfishGrantStoneMazeReward) ~= "function" then refuse("reward_grant_unavailable") end
        local pawn, detached = receiver(state, phase, name, host, false)
        local accepted, result = SilverfishGrantStoneMazeReward(pawn:GetAddress(), detached:GetAddress(), host.save:GetAddress(),
            loaded:GetAddress(), template, assigned)
        if accepted ~= true then error(result or "native_mosby_tape_reward_grant_refused", 0) end
        if result ~= "granted" and result ~= "already_granted" then error("mutation_unverified_stone_maze_reward_status", 0) end
        status, recipient, id = result, name, assigned
    end
    last = {type = "reward", status = status, map = state.map, pawn = recipient, slot = slot_name, template = template,
        id = id, world = state.world}
    return "exact Stone Maze HazOp Gear escrow verified"
end

function tape.command(fields, state, phase)
    local action = fields[2]
    if action == "native-mosby-tape-approach" and #fields == 3 then last = nil; return approach(state, phase, fields[3]) end
    if action == "native-mosby-tape-preflight" and #fields == 4 or action == "native-mosby-tape-convert" and #fields == 5 then
        last = nil; return exchange(state, phase, fields[3], action, fields[4], fields[5])
    end
    if action == "native-mosby-readings-preflight" and #fields == 3 or action == "native-mosby-readings-turn-in" and #fields == 4 then
        last = nil; return redeem(state, phase, fields[3], action, fields[4])
    end
    if action == "native-mosby-reward-capture" and #fields == 4 then last = nil; return escrow(state, phase, "capture", fields[3], fields[4]) end
    if action == "native-mosby-reward-grant" and #fields == 6 then
        last = nil; return escrow(state, phase, "grant", fields[3], fields[4], fields[5], fields[6])
    end
end

function tape.rows(state, phase, row)
    if not last or phase ~= "hosting" or last.map ~= state.map or not same(last.world, state.world) then return {} end
    if last.type == "exchange" then
        return {row("native-mosby-tape", last.operation, last.map, last.pawn, last.station, last.status, last.blank, last.filled,
            last.step, last.cash, last.xp, 1, 1, 0, 0)}
    end
    if last.type == "redemption" then
        local before, after = last.before, last.after
        local proof = {row("native-mosby-readings", last.operation, last.map, last.pawn, last.board, quest, last.objective, last.owner,
            last.reward, before.step, after.step, before.cash, after.cash, before.xp, after.xp, before.Mosby_SM_Readings, after.Mosby_SM_Readings)}
        for _, faction in ipairs(factions) do
            local old, updated = last.rep[faction], last.after_rep[faction]
            proof[#proof + 1] = row("native-mosby-readings-rep", faction, old.rep, old.found and 1 or 0, updated.rep,
                updated.found and 1 or 0, old.advantage, updated.advantage)
        end
        return proof
    end
    return {row("native-mosby-reward", last.map, last.pawn, last.status, last.slot, last.template, last.id)}
end

return tape
