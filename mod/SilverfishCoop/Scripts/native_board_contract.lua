local wire = require("native_wire_quest")
local board_contract = {}

local keys = {
    step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A",
    final = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B",
    cash = "Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0",
    xp = "Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797",
    items = "Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A",
    objective = "Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083",
    consumed = "ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333",
    redeem = "RedeemAt_83_FAE34B6C471D2019508A4898E9A0080F",
    kind = "QuestType_47_1031681D471E9929E30B17985E174BBE",
    flags = "Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F",
    chained = "Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9",
    rep = "ReputationGain_86_DA02B32344A59A6C236B848B22520EEC",
    ping = "GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F",
    track = "TrackFlag_ForStep_111_31112EA54B887C1D869E1291BC819774",
}
local quest_library_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local character_library_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local class_key = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local units_key = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local guid_key = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local completed_step = -10
local operation = "native-board-contract"
local catalog_operation = "native-board-contract-catalog"
local last

local function refuse(detail) error("stock_board_contract_" .. detail, 0) end
local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function int(value) return math.type(value) == "integer" and value >= -2147483648 and value <= 2147483647 end
local function len(values) return values and values:GetArrayNum() end
local function name(value) return type(value) == "userdata" and value:ToString() or value end
local function empty_name(value)
    value = name(value)
    return value == "" or value == "None"
end

local function board_accepts(board, key, row)
    local redeemable = board.RedeemableQuests
    local size = len(redeemable)
    if not int(size) or size < 0 or size > 128 then refuse("redeemable_list_unqualified") end
    for index = 1, size do
        if name(redeemable[index]) == name(key) then return true end
    end
    if type(board.BountyBoard_RedeemableHere) ~= "boolean" or type(board.AcceptAllGlobalContracts) ~= "boolean"
        or not int(board.TurnIn_ID) then refuse("redeemable_scope_unqualified") end
    local redeem = row[keys.redeem]
    return board.BountyBoard_RedeemableHere and
        (redeem == board.TurnIn_ID or redeem == 0 and board.AcceptAllGlobalContracts)
end

board_contract.board_accepts = board_accepts

local function offer_step(row)
    local step, final = row[keys.step], row[keys.final]
    if int(step) and int(final) and step >= 1 and step <= final then return step end
end

local function shape(row)
    local step, final, cash, xp = row[keys.step], row[keys.final], row[keys.cash], row[keys.xp]
    local quantity, consumed = len(row[keys.objective]), row[keys.consumed]
    if row[keys.kind] ~= 2 or not int(step) or not int(final) or final < 1 or not int(cash) or cash < 0
        or not int(xp) or xp < 0 or not int(row[keys.redeem]) or len(row[keys.items]) ~= 0
        or len(row[keys.chained]) ~= 0 or not empty_name(row[keys.ping]) or not empty_name(row[keys.track])
        or not ((quantity == 0 and consumed == false) or (quantity == 1 and consumed == true))
        or not row[keys.flags] or #row[keys.flags] > 16 or not row[keys.rep] or #row[keys.rep] > 16 then
        refuse("specialized_recipe")
    end
    local result = {step = step, final = final, cash = cash, xp = xp, objective = quantity == 1 and row[keys.objective][1] or nil}
    if quantity == 1 then
        if not valid(result.objective) then refuse("objective_class_unqualified") end
        result.path = result.objective:GetFullName():match("^BlueprintGeneratedClass (/Game/[%w_./%-]+)$")
        local defaults = result.path and result.objective:GetCDO()
        if not valid(defaults) or not same(defaults:GetClass(), result.objective) or type(defaults.Stacks) ~= "boolean" then
            refuse("objective_class_unqualified")
        end
        result.stacks = defaults.Stacks
    end
    return result
end

local function backpack_objectives(save, contract, allow_multiple)
    local result, seen = {}, {}
    for slot, rows in ipairs({save.PlayerBackpack, save.PlayerEquipment}) do
        local size = len(rows)
        if not int(size) or size < 0 or size > 4096 then refuse("inventory_unqualified") end
        for index = 1, size do
            local row = rows[index]
            if row and same(row[class_key], contract.objective) then
                local units, id = row[units_key], row[guid_key]
                if slot ~= 1 or (#result > 0 and not allow_multiple) or not int(units) or units < (contract.stacks and 1 or 0)
                    or not id or not int(id.A) or not int(id.B) or not int(id.C) or not int(id.D) then
                    refuse("objective_requires_one_unambiguous_backpack_stack")
                end
                local guid = ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
                if not guid:find("[^0]") or seen[guid] then refuse("objective_guid_unqualified") end
                seen[guid] = true
                result[#result + 1] = {guid = guid, units = units}
            end
        end
    end
    return result
end

local function balances(context)
    return wire.stock_flag(context.instance, "Player_Cash"), wire.stock_flag(context.instance, "Player_Xp")
end

local function expected_effects(context, row)
    local effects, keys_seen = {}, {}
    local function add(key, value, increment, multiplier)
        local text = name(key)
        if type(text) ~= "string" or #text > 96 or not text:match("^[A-Za-z0-9_]+$")
            or keys_seen[text] or text == "Player_Cash" or text == "Player_Xp" or not int(value) then
            refuse("effect_unqualified")
        end
        local prior, found = wire.stock_flag(context.instance, key)
        local expected = increment and prior + value * (found and multiplier or 1) or value
        if not int(expected) then refuse("effect_range") end
        keys_seen[text] = true
        effects[#effects + 1] = {key = key, value = expected}
    end

    row[keys.flags]:ForEach(function(key, value) add(key:get(), value:get(), false) end)
    if #row[keys.rep] > 0 then
        local library = StaticFindObject(character_library_path)
        if not valid(library) or library:GetFullName() ~= "FL_Characters_C " .. character_library_path then
            refuse("reputation_library_unavailable")
        end
        local trait = {}
        context.instance:FindPassiveEffect(FName("IntensePersonality", EFindName.FNAME_Find), true, trait, trait)
        if type(trait.EffectActive) ~= "boolean" or not int(trait.Instances) or trait.Instances < 0 then
            refuse("reputation_trait_unqualified")
        end
        row[keys.rep]:ForEach(function(faction, delta)
            local result = {}
            library:Compose_FactionRep_FlagName(faction:get(), context.host, result)
            if type(name(result.Name)) ~= "string" or not name(result.Name):match("^Rep_") then
                refuse("reputation_flag_unqualified")
            end
            add(result.Name, delta:get(), true, 1 + trait.Instances)
        end)
    end
    return effects
end

local function stock_context(state, phase, pawn, board, quest)
    local ok, context = pcall(wire.live_stock_board_context, state, phase, pawn, board, quest)
    if not ok then refuse("interaction_unqualified") end
    return context
end

local function unchanged(context, state, phase, pawn, board, quest)
    local after = stock_context(state, phase, pawn, board, quest)
    return same(context.world, after.world) and same(context.board, after.board) and same(context.host, after.host)
        and same(context.save, after.save) and same(context.receiving_save, after.receiving_save)
        and same(context.interactor, after.interactor)
end

local function specialist_ready(context, row)
    local step, final = row[keys.step], row[keys.final]
    if not int(step) or step <= 0 or not int(final) or final < 1 then return false end
    local objectives = row[keys.objective]
    local count = len(objectives)
    if not int(count) or count < 0 or count > 16 then return false end
    if count == 0 then return step >= final end
    local backpack = context.receiving_save.PlayerBackpack
    local size = len(backpack)
    if not int(size) or size < 0 or size > 4096 then return false end
    for index = 1, count do
        local wanted, present = objectives[index], false
        if not valid(wanted) then return false end
        for item = 1, size do
            local entry = backpack[item]
            if entry and same(entry[class_key], wanted) then present = true; break end
        end
        if not present then return false end
    end
    return true
end

local function catalog(fields, state, phase)
    local pawn, board_name = fields[3], fields[4]
    if #fields < 4 or #fields > 6 or type(board_name) ~= "string" or #board_name > 512 then refuse("arguments") end
    local function policies(raw)
        local ids = {}
        if raw == nil or raw == "-" then return ids end
        if #raw > 2048 then refuse("arguments") end
        for id in (raw .. ","):gmatch("([^,]*),") do
            if #id > 96 or not id:match("^[A-Za-z0-9_]+$") or ids[id] then refuse("arguments") end
            ids[id] = true
        end
        return ids
    end
    local special, excluded = policies(fields[5]), policies(fields[6])
    local context = stock_context(state, phase, pawn, board_name)
    context.board:Configure_Stock()
    local offers, ready = {}, {}
    local active, contracts = context.save.ActiveQuests, context.board.Contracts
    if not active or not contracts or #active > 128 or #contracts > 128 then refuse("catalog_extent") end
    local function descriptor(raw_key, raw_row, target, accepting)
        local key, row = raw_key:get(), raw_row:get()
        local id = name(key)
        if type(id) ~= "string" or #id > 96 or not id:match("^[A-Za-z0-9_]+$") then return end
        if accepting then
            -- Configure_Stock has already checked the board's eligibility rules.
            -- Accepting a quest only needs its starting step, not a payout recipe.
            if offer_step(row) and not wire.stock_row(active, key) then target[#target + 1] = id end
            return
        end
        if excluded[id] or not board_accepts(context.board, key, row) then return end
        if special[id] then
            if specialist_ready(context, row) then target[#target + 1] = id end
            return
        end
        local supported, contract = pcall(shape, row)
        if not supported or contract.step <= 0 then return end
        if contract.objective then
            local guest = not same(context.save, context.receiving_save)
            local permitted, owned = pcall(backpack_objectives, context.receiving_save, contract, guest)
            if not permitted or not owned[1] then return end
            if guest then
                local known, other = pcall(backpack_objectives, context.save, contract)
                if not known or other[1] then return end
            end
        elseif contract.step < contract.final then
            return
        end
        if pcall(expected_effects, context, row) then target[#target + 1] = id end
    end
    contracts:ForEach(function(key, row) descriptor(key, row, offers, true) end)
    active:ForEach(function(key, row) descriptor(key, row, ready, false) end)
    if not unchanged(context, state, phase, pawn, board_name) then refuse("catalog_scope_changed") end
    table.sort(offers)
    table.sort(ready)
    last = {mode = "catalog", pawn = pawn, board = board_name, context = context, offers = offers, ready = ready}
    return "Verified the current stock board options"
end

function board_contract.command(fields, state, phase)
    if fields[2] == catalog_operation then
        last = nil
        return catalog(fields, state, phase)
    end
    if fields[2] ~= operation then return end
    last = nil
    local verb, mode, pawn, board, quest = fields[3], fields[4], fields[5], fields[6], fields[7]
    if (verb ~= "start" and verb ~= "turnin") or (mode ~= "inspect" and mode ~= "apply")
        or type(quest) ~= "string" or #quest > 96 or not quest:match("^[A-Za-z0-9_]+$")
        or type(board) ~= "string" or #board > 512 or #fields ~= (mode == "apply" and 9 or 7) then
        refuse("arguments")
    end

    local context = stock_context(state, phase, pawn, board, quest)
    local active = wire.stock_row(context.save.ActiveQuests, context.key)
    local cash_before, xp_before = balances(context)
    local step = active and active[keys.step] or nil
    if step ~= nil and not int(step) then refuse("active_step_unqualified") end
    local outcome = {
        verb = verb, mode = mode, pawn = pawn, board = board, quest = quest, context = context,
        before = step or "-", after = step or "-", cash_before = cash_before, cash_after = cash_before,
        xp_before = xp_before, xp_after = xp_before, cash = 0, xp = 0, guid = "-", consumed = "-", state = "already",
    }
    if (verb == "start" and active) or (verb == "turnin" and step == completed_step) then
        last = outcome
        return "Stock board contract already recorded"
    end

    local contract, effects, library, objective_before
    local starting_step
    if verb == "start" then
        context.board:Configure_Stock()
        local offer = wire.stock_row(context.board.Contracts, context.key)
        if not offer then refuse("not_offered") end
        starting_step = offer_step(offer)
        if not starting_step then refuse("offer_step_unqualified") end
    else
        if not active or step <= 0 or not board_accepts(context.board, context.key, active) then refuse("wrong_turnin") end
        contract = shape(active)
        outcome.cash, outcome.xp = contract.cash, contract.xp
        if contract.objective then
            local guest = not same(context.save, context.receiving_save)
            objective_before = backpack_objectives(context.receiving_save, contract, guest)
            local selected = objective_before[1]
            if not selected then refuse("missing_objective") end
            outcome.guid, outcome.units = selected.guid, selected.units
            outcome.remaining = contract.stacks and outcome.units - 1 or 0
            if guest and backpack_objectives(context.save, contract)[1] then
                refuse("canonical_objective_conflict")
            end
        elseif step < contract.final then
            refuse("objective_not_complete")
        end
        effects = expected_effects(context, active)
        library = StaticFindObject(quest_library_path)
        if not valid(library) or library:GetFullName() ~= "FL_CodeLib01_C " .. quest_library_path
            or not int(cash_before + contract.cash) or not int(xp_before + contract.xp) then
            refuse("turnin_unqualified")
        end
    end
    outcome.expected_step = starting_step or completed_step
    outcome.state = "qualified"
    if mode == "inspect" then
        last = outcome
        return "Qualified current stock board contract"
    end
    if fields[8] ~= outcome.guid or fields[9] ~= tostring(outcome.units or "-") then refuse("objective_changed") end

    local has_deposit = outcome.guid ~= "-"
    local guest_deposit = has_deposit and not same(context.save, context.receiving_save)
    if guest_deposit then
        if type(SilverfishStageBoardObjective) ~= "function" then refuse("native_objective_transfer_unavailable") end
        local staged, reason, moved = SilverfishStageBoardObjective(context.interactor:GetAddress(),
            context.receiving_save:GetAddress(), context.save:GetAddress(), outcome.guid, quest, contract.path,
            contract.final, contract.cash, contract.xp, outcome.units)
        if staged ~= true then
            if type(reason) == "string" and reason:match("^mutation_unverified_") then error(reason, 0) end
            refuse("objective_transfer_rejected")
        end
        if type(moved) ~= "string" or #moved ~= 32 or moved:find("[^0-9A-F]") or not moved:find("[^0]")
            or (moved == outcome.guid) ~= (outcome.remaining == 0) then
            error("mutation_unverified_stock_board_objective_guid", 0)
        end
        outcome.consumed = moved
    elseif has_deposit and outcome.remaining == 0 then
        outcome.consumed = outcome.guid
    end

    local applied = pcall(function()
        if verb == "start" then
            context.instance:AddQuest_ByName(context.key)
            context.board:Configure_Stock()
        else
            local out = {}
            library:TurnIn_Quest(context.key, context.instance, true, context.host, out, out)
            if out.Success ~= true or valid(out.ConfirmationPopup) then return end
            if has_deposit then
                if guest_deposit and backpack_objectives(context.save, contract)[1] then return end
                local observed = backpack_objectives(context.receiving_save, contract, guest_deposit)
                if outcome.remaining > 0 then
                    objective_before[1].units = outcome.remaining
                else
                    table.remove(objective_before, 1)
                end
                if #observed ~= #objective_before then return end
                for index, row in ipairs(observed) do
                    local expected = objective_before[index]
                    if row.guid ~= expected.guid or row.units ~= expected.units then return end
                end
            end
            for _, effect in ipairs(effects) do
                local observed, found = wire.stock_flag(context.instance, effect.key)
                if not found or observed ~= effect.value then return end
            end
        end
        if not unchanged(context, state, phase, pawn, board, quest) then return end
        local after = wire.stock_row(context.save.ActiveQuests, context.key)
        local cash_after, xp_after = balances(context)
        if not after or after[keys.step] ~= outcome.expected_step or cash_after ~= cash_before + outcome.cash
            or xp_after ~= xp_before + outcome.xp then return end
        outcome.after, outcome.cash_after, outcome.xp_after, outcome.state = after[keys.step], cash_after, xp_after, "applied"
    end)
    if not applied or outcome.state ~= "applied" then error("mutation_unverified_stock_board_contract", 0) end
    last = outcome
    return "Verified stock board contract action"
end

function board_contract.rows(state, phase, status_row)
    if not last then return {} end
    if phase ~= "hosting" or not same(state.world, last.context.world)
        or not same(last.context.instance.SaveSlot_Ref, last.context.save)
        or not same(last.context.interactor.Save, last.context.receiving_save) then
        last = nil
        return {}
    end
    if last.mode == "catalog" then
        return {status_row(catalog_operation, state.map, last.pawn, last.board, #last.offers, #last.ready,
            #last.offers > 0 and table.concat(last.offers, ",") or "-",
            #last.ready > 0 and table.concat(last.ready, ",") or "-")}
    end
    return {status_row(operation, last.verb, last.mode, state.map, last.pawn, last.board, last.quest,
        last.state, last.before, last.after, last.expected_step or "-", last.cash_before, last.cash_after,
        last.xp_before, last.xp_after, last.cash, last.xp, last.guid,
        last.units or "-", last.remaining or "-", last.consumed)}
end

return board_contract
