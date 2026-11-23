local drill = {}
local wire = require("native_wire_quest")
local mosby = require("native_dialogue_mosby")
local guest_save = require("guest_save")
local loot_escrow = require("native_loot_actions")
local zones = require("zone_maps")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local expanded = os.getenv("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES") == "1"
local area, world = mosby.map, mosby.world
local level = world .. ":PersistentLevel."
local quest = "Clt_DrillHead"
local board_path = level .. "BountyBoard_BP_C_2"
local board = {map = area, world_path = world, board_path = board_path, board_name = "BountyBoard_BP_C " .. board_path,
    board_id = 4, quest_id = quest}
local station_path = level .. "DataTape_DownloadStation_BP_C_1"
local station_name = "DataTape_DownloadStation_BP_C " .. station_path
local station_class = "/Game/Classes/Actors/DataTape_DownloadStation_BP.DataTape_DownloadStation_BP_C"
local tape_path = "/Game/Classes/Items/DataTapes/DataTape_DrillTestResults.DataTape_DrillTestResults_C"
local objective_path = "/Game/Classes/Items/Artifacts/Art_DrillHead.Art_DrillHead_C"
local armor_path = "/Game/Classes/Armor_Items/Armor_HazzardSuit.Armor_HazzardSuit_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local code_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local character_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local item_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local factions = {2, 3, 4}
local reputation_change = {[2] = 300, [3] = -200, [4] = -200}
local cash_reward, xp_reward = 10000, 3
local last

local function valid(value) return value and value:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function refuse(reason) error("native_prototype_drill_" .. reason, 0) end
local function int32(value) return type(value) == "number" and math.tointeger(value) and value >= -2147483648 and value <= 2147483647 end
local function guid(value) return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]") end
local function slot(value) return type(value) == "string" and #value == 39 and value:match("^SFCoop_[0-9a-f]+$") end

local function identity(row)
    local id = row[guid_field]
    if not id or not int32(id.A) or not int32(id.B) or not int32(id.C) or not int32(id.D) then return end
    local result = string.format("%08X%08X%08X%08X", id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
    return guid(result) and result or nil
end

local function owned(save, class, expected)
    local result = {}
    local function read(row, storage)
        if not row then refuse("stock_inventory_row_unavailable") end
        if same(row[item_field], class) then
            local id = identity(row)
            if not id or result[id] then refuse("stock_inventory_identity_unqualified") end
            result[id] = storage
        elseif expected and identity(row) == expected then
            refuse("stock_inventory_guid_class_changed")
        end
    end
    local function read_array(rows, storage)
        local size = rows and rows:GetArrayNum()
        if not int32(size) or size < 0 or size > 4096 then refuse("stock_inventory_extent_unqualified") end
        for index = 1, size do read(rows[index], storage) end
    end
    read_array(save.PlayerBackpack, "backpack")
    read_array(save.PlayerEquipment, "equipment")
    read(save.PlayerArmor, "armor")
    read(save.PlayerHelmet, "helmet")
    return result
end

local function scope(state, phase)
    local route = zones[state.map]
    local name = state.map and state.map:match("/([^/]+)$")
    local driver = valid(state.world) and state.world.NetDriver
    if not enabled or not route or route.expanded and not expanded or not name or phase ~= "hosting"
        or not state.listening or state.server or not valid(driver) or valid(driver.ServerConnection)
        or not same(driver.World, state.world) or state.world:GetFullName() ~= "World " .. state.map .. "." .. name then
        refuse("listen_scope_unavailable")
    end
    return mosby.progress(state)
end

local function stock_station(context, state)
    local station = StaticFindObject(station_path)
    local script = state.world.PersistentLevel.LevelScriptActor
    local tape = StaticFindObject(tape_path)
    if not exact(station, station_class) or station:GetFullName() ~= station_name or not station:HasAuthority()
        or not same(station:GetWorld(), state.world) or station:IsActorBeingDestroyed()
        or not exact(script, world .. "_C") or not script:HasAuthority() or not same(script:GetWorld(), state.world)
        or not same(script["As Game Instance FP"], context.instance) or script.PlayerIsWelcome ~= true
        or type(script.DrillTested) ~= "boolean" or not valid(tape) then
        refuse("stock_scientist_station_unqualified")
    end
    return station, script, tape
end

local function stock_tapes(station, tape)
    local stock = station.Tapes_DataBase
    local size = stock and stock:GetArrayNum()
    if not int32(size) or size < 0 or size > 128 then refuse("stock_scientist_station_tapes_unqualified") end
    local result, matches = {}, 0
    for index = 1, size do
        local entry = stock[index]
        if not valid(entry) then refuse("stock_scientist_station_tape_unavailable") end
        if same(entry, tape) then matches = matches + 1 end
        result[index] = entry:GetAddress()
    end
    if matches > 1 then refuse("stock_scientist_station_duplicate_result") end
    return result, matches
end

local function faction_state(context)
    local library = StaticFindObject(character_path)
    if not valid(library) or library:GetFullName() ~= "FL_Characters_C " .. character_path then
        refuse("stock_reputation_library_unavailable")
    end
    local result = {}
    for _, faction in ipairs(factions) do
        local rep, adv = {}, {}
        library:Compose_FactionRep_FlagName(faction, context.host, rep)
        library:Compose_FactionAdvantage_FlagName(faction, context.host, adv)
        if not rep.Name or not adv.Name or rep.Name:GetComparisonIndex() == 0 or adv.Name:GetComparisonIndex() == 0 then
            refuse("stock_reputation_flags_unavailable")
        end
        local amount, found = wire.stock_flag(context.instance, rep.Name)
        local credit, credit_found = wire.stock_flag(context.instance, adv.Name)
        result[faction] = {amount = amount, found = found, credit = credit, credit_found = credit_found}
    end
    return result
end

local function anchor(context, state, pawn)
    if not same(context.world, state.world) or not same(context.interactor.Save, context.receiving_save)
        or not same(context.instance.SaveSlot_Ref, context.save) or not same(context.host.Save, context.save)
        or not same(context.instance["As Player FP"], context.host) then return false end
    for _, detail in ipairs(state.pawns or {}) do
        if detail.name == pawn and same(detail.pawn, context.interactor) then return true end
    end
    return false
end

local function readiness(before, tested, tape)
    return (before.prototype == 1 or before.prototype == 2) and before.step == -10 and before.Mosby_SM_Readings >= 1
        and before.Topic_MicroDrill >= 1 and before.Mosby_DrillTest == 0 and tested == false and tape == 0
end

local function board_delegate(context, script, operation)
    if type(SilverfishPrototypeDrillBoard) ~= "function" then refuse("stock_board_delegate_unavailable") end
    local ok, result = SilverfishPrototypeDrillBoard(context.host:GetAddress(), context.save:GetAddress(),
        context.board:GetAddress(), script:GetAddress(), operation)
    if ok ~= true or result ~= (operation == "check" and "verified" or "broadcast") then
        error(result or "native_prototype_drill_stock_board_delegate_unqualified", 0)
    end
end

local function redeem(state, phase, name, operation, objective)
    local context = wire.stock_board_context(state, phase, name, false, board)
    local before, canonical = scope(state, phase)
    if not same(context.save, canonical.save) or not anchor(context, state, name) then refuse("stock_board_canonical_changed") end
    local item, armor = StaticFindObject(objective_path), StaticFindObject(armor_path)
    if not valid(item) or not valid(armor) then refuse("stock_item_classes_unavailable") end
    local station, script, tape = stock_station(context, state)
    local old_tapes, old_tape = stock_tapes(station, tape)
    local old_tested = script.DrillTested
    local active = readiness(before, old_tested, old_tape)
    local objective_rows = owned(context.receiving_save, item)
    local selected, storage = next(objective_rows)
    if storage ~= "backpack" or next(objective_rows, selected) then selected = nil end
    local host_choice = same(context.receiving_save, context.save)
    if not host_choice and next(owned(context.save, item)) then selected = nil end
    if active then board_delegate(context, script, "check") end

    local rep, new_rep = faction_state(context), nil
    local after, award, new_tape, new_tested = before, "-", old_tape, old_tested
    if operation == "native-prototype-drill-turn-in" then
        if not active or not guid(objective) or objective ~= selected then refuse("exact_objective_required") end
        if type(SilverfishStagePrototypeDrillObjective) ~= "function" or type(SilverfishCapturePrototypeDrillReward) ~= "function"
            or type(SilverfishVerifyPrototypeDrillReward) ~= "function" or type(SilverfishGrantPrototypeDrillReward) ~= "function" then
            refuse("stock_reward_capability_unavailable")
        end
        local library = StaticFindObject(code_path)
        local personality = FName("IntensePersonality", EFindName.FNAME_Find)
        if not valid(library) or library:GetFullName() ~= "FL_CodeLib01_C " .. code_path or personality:GetComparisonIndex() == 0 then
            refuse("stock_reward_library_unavailable")
        end
        local effect = {}
        context.instance:FindPassiveEffect(personality, true, effect, effect)
        if type(effect.EffectActive) ~= "boolean" or not int32(effect.Instances) or effect.Instances < 0
            or not int32(before.cash + cash_reward) or not int32(before.xp + xp_reward) then refuse("stock_reward_range_unqualified") end
        local expected = {}
        for _, faction in ipairs(factions) do
            local previous = rep[faction]
            expected[faction] = previous.amount + reputation_change[faction] * (previous.found and 1 + effect.Instances or 1)
            if not int32(expected[faction]) then refuse("stock_reputation_range_unqualified") end
        end
        local old_armor = owned(context.save, armor)
        if not host_choice then
            local ok, reason = SilverfishStagePrototypeDrillObjective(context.interactor:GetAddress(), context.receiving_save:GetAddress(),
                context.save:GetAddress(), objective)
            if ok ~= true then error(reason or "native_prototype_drill_stage_refused", 0) end
        end

        local checked, observed, updated, reward = pcall(function()
            local out = {}
            library:TurnIn_Quest(context.key, context.instance, true, context.host, out, out)
            local progress = scope(state, phase)
            if out.Success ~= true or valid(out.ConfirmationPopup) or not anchor(context, state, name)
                or progress.prototype ~= -10 or progress.Mosby_DrillTest ~= 1
                or progress.cash ~= before.cash + cash_reward or progress.xp ~= before.xp + xp_reward
                or owned(context.save, item, objective)[objective] ~= nil
                or owned(context.receiving_save, item, objective)[objective] ~= nil then return end
            for key, previous in pairs(before) do
                if key ~= "prototype" and key ~= "Mosby_DrillTest" and key ~= "cash" and key ~= "xp"
                    and progress[key] ~= previous then return end
            end
            local amounts = faction_state(context)
            for _, faction in ipairs(factions) do
                local old, current = rep[faction], amounts[faction]
                if current.amount ~= expected[faction] or current.found ~= true or current.credit ~= old.credit
                    or current.credit_found ~= old.credit_found then return end
            end
            local current, added = owned(context.save, armor), nil
            for id, storage in pairs(current) do
                if not old_armor[id] then
                    if added or storage ~= "backpack" or id == objective then return end
                    added = id
                end
            end
            for id, storage in pairs(old_armor) do if current[id] ~= storage then return end end
            if not added or script.DrillTested ~= false then return end
            -- This is the same success tail as Quest_TurnIn_Widget with no UI or second reward call.
            context.board:Configure_Stock()
            board_delegate(context, script, "broadcast")
            local new_station, level_script, tape_class = stock_station(context, state)
            if not same(new_station, station) or not same(level_script, script) or not same(tape_class, tape) then return end
            local tapes, matches = stock_tapes(station, tape)
            if matches ~= 1 or #tapes ~= #old_tapes + 1 or tapes[#tapes] ~= tape:GetAddress()
                or script.DrillTested ~= true then return end
            for index, old in ipairs(old_tapes) do if tapes[index] ~= old then return end end
            local final = scope(state, phase)
            for key, value in pairs(progress) do if final[key] ~= value then return end end
            local final_rep, final_armor = faction_state(context), owned(context.save, armor)
            for _, faction in ipairs(factions) do
                for key, value in pairs(amounts[faction]) do if final_rep[faction][key] ~= value then return end end
            end
            for id, storage in pairs(current) do if final_armor[id] ~= storage then return end end
            for id in pairs(final_armor) do if not current[id] then return end end
            if not anchor(context, state, name) or owned(context.save, item, objective)[objective] ~= nil
                or owned(context.receiving_save, item, objective)[objective] ~= nil then return end
            return final, final_rep, added
        end)
        if not checked or not observed or not reward then
            error("mutation_unverified_prototype_drill_redemption", 0)
        end
        after, new_rep, award, new_tape, new_tested = observed, updated, reward, 1, true
    end
    last = {operation = operation, map = area, world = state.world, pawn = name, context = context,
        before = before, after = after, objective = selected or "-", owner = selected and (host_choice and "host" or "guest") or "-",
        award = award, rep = rep, new_rep = new_rep or rep, old_tape = old_tape, new_tape = new_tape,
        old_tested = old_tested, new_tested = new_tested, eligible = active}
    return "exact stock Prototype Drill state verified"
end

local function escrow(state, phase, mode, name, template, pawn_name, assigned, memory)
    if not slot(name) or not guid(template) then refuse("reward_identity_unqualified") end
    local progress, canonical = scope(state, phase)
    local statics, class = StaticFindObject(statics_path), StaticFindObject(save_path)
    if progress.prototype ~= -10 or progress.Mosby_DrillTest < 1 or not valid(statics) or not valid(class)
        or type(SilverfishVerifyPrototypeDrillReward) ~= "function" then refuse("reward_scope_unavailable") end
    local function verify(loaded, source)
        if not exact(loaded, save_path) or same(loaded, canonical.save) or same(loaded, source) then return false end
        local args = {loaded:GetAddress(), template}
        if source then args[3] = source:GetAddress() end
        local ok, result = SilverfishVerifyPrototypeDrillReward(table.unpack(args))
        return ok == true and result == "verified"
    end
    local scratch, loaded
    if mode == "capture" then
        if state.map ~= area or type(SilverfishCapturePrototypeDrillReward) ~= "function"
            or not memory and statics:DoesSaveGameExist(name, 0) ~= false then refuse("reward_capture_unavailable") end
        if memory then
            scratch = loot_escrow.create_pending_escrow(canonical.save, canonical.instance, state.world, name,
                function(source, restored) return verify(restored, source) end)
        else
            scratch = statics:CreateSaveGameObject(class)
        end
        if not exact(scratch, save_path) or same(scratch, canonical.save) then refuse("reward_scratch_unavailable") end
        local ok, result = SilverfishCapturePrototypeDrillReward(canonical.host:GetAddress(), canonical.save:GetAddress(),
            scratch:GetAddress(), template)
        if ok ~= true or result ~= "captured" or not memory and statics:SaveGameToSlot(scratch, name, 0) ~= true then
            refuse("reward_capture_unverified")
        end
    end
    if memory then
        loaded = loot_escrow.pending_escrow(state.world, name)
        if not verify(loaded) then refuse("reward_payload_unverified") end
    else
        loaded = statics:LoadGameFromSlot(name, 0)
        if not verify(loaded, scratch) then refuse("reward_payload_unverified") end
    end
    local status, recipient, awarded = "captured", canonical.host:GetFullName(), template
    if mode == "grant" then
        if not guid(assigned) or assigned == template or type(SilverfishGrantPrototypeDrillReward) ~= "function" then
            refuse("reward_grant_unavailable")
        end
        local pawn, detached = guest_save.exact_item_context(state, phase, pawn_name)
        if not valid(pawn) or not exact(detached, save_path) or same(detached, canonical.save) or not same(pawn.Save, detached) then
            refuse("reward_detached_guest_unqualified")
        end
        local granted, proof = SilverfishGrantPrototypeDrillReward(pawn:GetAddress(), detached:GetAddress(),
            canonical.save:GetAddress(), loaded:GetAddress(), template, assigned)
        if granted ~= true then error(proof or "native_prototype_drill_reward_grant_refused", 0) end
        if proof ~= "granted" and proof ~= "already_granted" then error("mutation_unverified_prototype_drill_reward_status", 0) end
        status, recipient, awarded = proof, pawn_name, assigned
    end
    last = {map = state.map, world = state.world, pawn = recipient, status = status, slot = name, template = template, award = awarded}
    return "exact stock Prototype Drill armor escrow verified"
end

function drill.command(fields, state, phase)
    local action = fields[2]
    if action == "native-prototype-drill-preflight" and #fields == 3
        or action == "native-prototype-drill-turn-in" and #fields == 4 then
        last = nil
        return redeem(state, phase, fields[3], action, fields[4])
    end
    if action == "native-prototype-drill-reward-capture" and (#fields == 4 or #fields == 5 and fields[5] == "memory") then
        last = nil
        return escrow(state, phase, "capture", fields[3], fields[4], nil, nil, fields[5] == "memory")
    end
    if action == "native-prototype-drill-reward-grant" and (#fields == 6 or #fields == 7 and fields[7] == "memory") then
        last = nil
        return escrow(state, phase, "grant", fields[3], fields[4], fields[5], fields[6], fields[7] == "memory")
    end
end

local function bit(value) return value and 1 or 0 end

function drill.rows(state, phase, row)
    if not last or phase ~= "hosting" or state.map ~= last.map or not same(state.world, last.world) then return {} end
    if not last.operation then
        return {row("native-prototype-drill-reward", last.map, last.pawn, last.status, last.slot, last.template, last.award)}
    end
    if not anchor(last.context, state, last.pawn) then return {} end
    local before, after = last.before, last.after
    local result = {row("native-prototype-drill", last.operation, last.map, last.pawn, board.board_name, quest,
        last.objective, last.owner, last.award, before.prototype, after.prototype, before.cash, after.cash, before.xp, after.xp,
        before.Mosby_DrillTest, after.Mosby_DrillTest, bit(last.old_tested), bit(last.new_tested),
        station_name, last.old_tape, last.new_tape, bit(last.eligible))}
    for _, faction in ipairs(factions) do
        local old, new = last.rep[faction], last.new_rep[faction]
        result[#result + 1] = row("native-prototype-drill-rep", faction, old.amount, bit(old.found),
            new.amount, bit(new.found), old.credit, bit(old.credit_found), new.credit, bit(new.credit_found))
    end
    return result
end

return drill
