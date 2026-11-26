local wire = require("native_wire_quest")
local quest_catalog = require("native_quest_catalog")
local content_paths = require("content_paths")
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
local function board_accepts(board, key, row)
    local redeemable = board.RedeemableQuests
    local size = len(redeemable)
    if not int(size) or size < 0 or size > 128 then refuse("redeemable_list_unqualified") end
    local id = wire.stock_quest_id(name(key))
    for index = 1, size do
        if wire.stock_quest_id(name(redeemable[index])) == id then return true end
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

local function shape(save, row)
    local step = quest_catalog.tracked_step(save, row)
    local final, cash, xp = row[keys.final], row[keys.cash], row[keys.xp]
    local quantity, consumed = len(row[keys.objective]), row[keys.consumed]
    local reward_count = len(row[keys.items])
    local ping = name(row[keys.ping])
    -- The normal stock turn-in sends follow-up pings in the host world.
    -- Item deposits use the native inventory transaction and have separate constraints.
    local ping_supported = ping == "" or ping == "None"
        or quantity == 0 and reward_count == 0 and type(ping) == "string" and #ping <= 96 and ping:match("^[A-Za-z0-9_-]+$")
    if row[keys.kind] ~= 2 or not int(step) or not int(final) or final < 1 or not int(cash) or cash < 0
        or not int(xp) or xp < 0 or not int(row[keys.redeem])
        or not int(reward_count) or reward_count < 0 or reward_count > 8
        or len(row[keys.chained]) ~= 0 or not ping_supported
        or not int(quantity) or quantity < 0 or quantity > 16
        or not (quantity == 0 and type(consumed) == "boolean" or quantity > 0 and consumed == true)
        or not row[keys.flags] or #row[keys.flags] > 16 or not row[keys.rep] or #row[keys.rep] > 16 then
        refuse("specialized_recipe")
    end
    local result = {step = step, final = final, cash = cash, xp = xp, objectives = {}, by_class = {},
        empty_consumption = quantity == 0 and consumed, rewards = {}, reward_paths = {}}
    for index = 1, quantity do
        local class = row[keys.objective][index]
        if not valid(class) then refuse("objective_class_unqualified") end
        local path = class:GetFullName():match("^BlueprintGeneratedClass (/%S+)$")
        local defaults = content_paths.item_class(path) and class:GetCDO()
        if not valid(defaults) or not same(defaults:GetClass(), class) or type(defaults.Stacks) ~= "boolean" then
            refuse("objective_class_unqualified")
        end
        local address = class:GetAddress()
        if not result.by_class[address] then
            local objective = {address = address, path = path, stacks = defaults.Stacks}
            result.by_class[address] = objective
            result.objectives[#result.objectives + 1] = objective
        end
    end
    for index = 1, reward_count do
        local class = row[keys.items][index]
        if not valid(class) then refuse("reward_class_unqualified") end
        local path = class:GetFullName():match("^BlueprintGeneratedClass (/%S+)$")
        local defaults = content_paths.item_class(path) and class:GetCDO()
        local address = class:GetAddress()
        if not valid(defaults) or not same(defaults:GetClass(), class) or type(defaults.Stacks) ~= "boolean"
            or result.rewards[address] ~= nil or result.by_class[address] then
            refuse("reward_class_unqualified")
        end
        result.rewards[address] = defaults.Stacks
        result.reward_paths[index] = path
    end
    return result
end

local function reward_quantities(save, contract)
    local totals = {}
    for address in pairs(contract.rewards) do totals[address] = 0 end
    local rows = save.PlayerBackpack
    local size = len(rows)
    if not int(size) or size < 0 or size > 4096 then refuse("inventory_unqualified") end
    for index = 1, size do
        local row = rows[index]
        local class = row and row[class_key]
        local address = valid(class) and class:GetAddress()
        local stacks = contract.rewards[address]
        if stacks ~= nil then
            local units = stacks and row[units_key] or 1
            if not int(units) or units < 1 or not int(totals[address] + units) then refuse("reward_quantity_unqualified") end
            totals[address] = totals[address] + units
        end
    end
    return totals
end

local function backpack_objectives(save, contract)
    local result, seen = {}, {}
    for slot, rows in ipairs({save.PlayerBackpack, save.PlayerEquipment}) do
        local size = len(rows)
        if not int(size) or size < 0 or size > 4096 then refuse("inventory_unqualified") end
        for index = 1, size do
            local row = rows[index]
            local class = row and row[class_key]
            local objective = valid(class) and contract.by_class[class:GetAddress()]
            if objective then
                local units, id = row[units_key], row[guid_key]
                if slot ~= 1 or not int(units) or units < (objective.stacks and 1 or -1)
                    or not id or not int(id.A) or not int(id.B) or not int(id.C) or not int(id.D) then
                    refuse("objective_requires_unambiguous_backpack_stack")
                end
                local guid = ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
                if not guid:find("[^0]") or seen[guid] then refuse("objective_guid_unqualified") end
                seen[guid] = true
                result[#result + 1] = {guid = guid, units = units, objective = objective}
            end
        end
    end
    return result
end

local function required_items(contract, owned)
    local first, selected = {}, {}
    for _, item in ipairs(owned) do
        first[item.objective.address] = first[item.objective.address] or item
    end
    for _, objective in ipairs(contract.objectives) do
        local item = first[objective.address]
        if not item then return end
        selected[#selected + 1] = {guid = item.guid, units = item.units, path = objective.path,
            remaining = objective.stacks and item.units - 1 or 0, consumed = "-"}
    end
    return selected
end

local function additional_deposits(outcome, include_consumed)
    local rows = {}
    for index = 2, #outcome.deposits do
        local part = outcome.deposits[index]
        local fields = {part.guid, part.units, part.remaining}
        if include_consumed then fields[4] = part.consumed end
        rows[#rows + 1] = table.concat(fields, ":")
    end
    return #rows > 0 and table.concat(rows, ",") or "-"
end

local function balances(context)
    return wire.stock_flag(context.instance, "Player_Cash"), wire.stock_flag(context.instance, "Player_Xp")
end

local function stock_payout(context, active, empty_consumption, apply)
    if empty_consumption then
        -- An empty list has nothing to consume. Stock's item check returns false for it.
        -- Use the normal stock payout and restore the original field on the active row.
        active[keys.consumed] = false
    end
    local ok, applied, reason, consumed = pcall(apply)
    if empty_consumption then
        local after = wire.stock_row(context.save.ActiveQuests, context.key)
        if not after then error("mutation_unverified_stock_board_consumption_restore", 0) end
        after[keys.consumed] = true
        if wire.stock_row(context.save.ActiveQuests, context.key)[keys.consumed] ~= true then
            error("mutation_unverified_stock_board_consumption_restore", 0)
        end
    end
    if not ok then error(applied, 0) end
    return applied, reason, consumed
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
    local step, final = quest_catalog.tracked_step(context.save, row), row[keys.final]
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
        local id = wire.stock_quest_id(name(key))
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
        local supported, contract = pcall(shape, context.save, row)
        if not supported or contract.step <= 0 then return end
        if #contract.objectives > 0 then
            local permitted, owned = pcall(backpack_objectives, context.receiving_save, contract)
            if not permitted or not required_items(contract, owned) then return end
        elseif contract.step < contract.final then
            return
        end
        if #contract.reward_paths > 0 and not pcall(reward_quantities, context.receiving_save, contract) then return end
        if pcall(expected_effects, context, row) then target[#target + 1] = id end
    end
    contracts:ForEach(function(key, row) descriptor(key, row, offers, true) end)
    active:ForEach(function(key, row) descriptor(key, row, ready, false) end)
    if not unchanged(context, state, phase, pawn, board_name) then refuse("catalog_scope_changed") end
    table.sort(offers)
    table.sort(ready)
    return {mode = "catalog", pawn = pawn, board = board_name, context = context, offers = offers, ready = ready}
end

local function catalog_row(state, result, status_row)
    return status_row(catalog_operation, state.map, result.pawn, result.board, #result.offers, #result.ready,
        #result.offers > 0 and table.concat(result.offers, ",") or "-",
        #result.ready > 0 and table.concat(result.ready, ",") or "-")
end

function board_contract.catalog_reply(fields, state, phase, status_row)
    local result = catalog(fields, state, phase)
    return "Verified the current stock board options", catalog_row(state, result, status_row)
end

function board_contract.command(fields, state, phase)
    if fields[2] == catalog_operation then
        last = nil
        last = catalog(fields, state, phase)
        return "Verified the current stock board options"
    end
    if fields[2] ~= operation then return end
    last = nil
    local verb, mode, pawn, board, quest = fields[3], fields[4], fields[5], fields[6], fields[7]
    if (verb ~= "start" and verb ~= "turnin") or (mode ~= "inspect" and mode ~= "apply")
        or type(quest) ~= "string" or #quest > 96 or not quest:match("^[A-Za-z0-9_]+$")
        or type(board) ~= "string" or #board > 512 or
        (mode == "apply" and #fields ~= 10 and #fields ~= 11) or (mode == "inspect" and #fields ~= 7) then
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
        deposits = {}, rewards = "-",
    }
    if (verb == "start" and active) or (verb == "turnin" and step == completed_step) then
        last = outcome
        return "Stock board contract already recorded"
    end

    local contract, effects, library, objective_before, rewards_before
    local starting_step
    if verb == "start" then
        context.board:Configure_Stock()
        local offer = wire.stock_row(context.board.Contracts, context.key)
        if not offer then refuse("not_offered") end
        starting_step = offer_step(offer)
        if not starting_step then refuse("offer_step_unqualified") end
    else
        if not active or step <= 0 or not board_accepts(context.board, context.key, active) then refuse("wrong_turnin") end
        contract = shape(context.save, active)
        if contract.step <= 0 then refuse("objective_not_complete") end
        outcome.cash, outcome.xp = contract.cash, contract.xp
        if #contract.reward_paths > 0 then
            if not same(context.save, context.receiving_save) and type(SilverfishRedeemBoardObjectives) ~= "function" then
                refuse("native_reward_redemption_unavailable")
            end
            outcome.rewards = table.concat(contract.reward_paths, ",")
            rewards_before = reward_quantities(context.receiving_save, contract)
        end
        if #contract.objectives > 0 then
            objective_before = backpack_objectives(context.receiving_save, contract)
            outcome.deposits = required_items(contract, objective_before)
            if not outcome.deposits then refuse("missing_objective") end
            local first = outcome.deposits[1]
            outcome.guid, outcome.units, outcome.remaining = first.guid, first.units, first.remaining
        elseif contract.step < contract.final then
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
    local inspected = table.concat({outcome.before, outcome.expected_step, outcome.cash, outcome.xp,
        outcome.remaining or "-"}, "|")
    if #outcome.deposits > 1 then inspected = inspected .. "|" .. additional_deposits(outcome, false) end
    if fields[10] ~= inspected or (fields[11] or "-") ~= outcome.rewards then refuse("inspection_changed") end

    local has_deposit = outcome.guid ~= "-"
    local native_redemption = (has_deposit or rewards_before) and not same(context.save, context.receiving_save)
    if native_redemption then
        if type(SilverfishRedeemBoardObjectives) ~= "function" then refuse("native_objective_redemption_unavailable") end
        local requested = {}
        for _, item in ipairs(outcome.deposits) do
            requested[#requested + 1] = table.concat({item.guid, item.units, item.path}, "|")
        end
        local redeemed, reason, moved = stock_payout(context, active, contract.empty_consumption, function()
            local arguments = {context.interactor:GetAddress(), context.receiving_save:GetAddress(),
                context.save:GetAddress(), quest, contract.final, contract.cash, contract.xp,
                #requested > 0 and table.concat(requested, ",") or "-"}
            if outcome.rewards ~= "-" then arguments[9] = outcome.rewards end
            return SilverfishRedeemBoardObjectives(table.unpack(arguments))
        end)
        if redeemed ~= true then
            if type(reason) == "string" and reason:match("^mutation_unverified_") then error(reason, 0) end
            -- Keep the native preflight reason so a refused board action can be diagnosed.
            refuse(type(reason) == "string" and reason or "objective_redemption_rejected")
        end
        local index, seen = 0, {}
        if type(moved) ~= "string" or #moved ~= math.max(0, #outcome.deposits * 33 - 1) then
            error("mutation_unverified_stock_board_objective_guids", 0)
        end
        for guid in moved:gmatch("([^,]+)") do
            index = index + 1
            local item = outcome.deposits[index]
            if not item or #guid ~= 32 or guid:find("[^0-9A-F]") or not guid:find("[^0]") or seen[guid]
                or (guid == item.guid) ~= (item.remaining == 0) then
                error("mutation_unverified_stock_board_objective_guid", 0)
            end
            seen[guid], item.consumed = true, guid
        end
        if index ~= #outcome.deposits then error("mutation_unverified_stock_board_objective_guids", 0) end
    elseif has_deposit then
        for _, item in ipairs(outcome.deposits) do
            if item.remaining == 0 then item.consumed = item.guid end
        end
    end
    if has_deposit then outcome.consumed = outcome.deposits[1].consumed end

    local applied = pcall(function()
        if verb == "start" then
            context.instance:AddQuest_ByName(context.key)
            context.board:Configure_Stock()
        else
            if not native_redemption and not stock_payout(context, active, contract.empty_consumption, function()
                local out = {}
                library:TurnIn_Quest(context.key, context.instance, true, context.host, out, out)
                return out.Success == true and not valid(out.ConfirmationPopup)
            end) then return end
            if rewards_before then
                local awarded = reward_quantities(context.receiving_save, contract)
                for address, count in pairs(rewards_before) do
                    if awarded[address] <= count then return end
                end
            end
            if has_deposit then
                local observed = backpack_objectives(context.receiving_save, contract)
                local remaining = {}
                for _, item in ipairs(outcome.deposits) do remaining[item.guid] = item.remaining end
                for index = #objective_before, 1, -1 do
                    local item = objective_before[index]
                    local left = remaining[item.guid]
                    if left == 0 then table.remove(objective_before, index)
                    elseif left then item.units = left end
                end
                if #observed ~= #objective_before then return end
                for index, row in ipairs(observed) do
                    local expected = objective_before[index]
                    if row.guid ~= expected.guid or row.units ~= expected.units or row.objective ~= expected.objective then return end
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
        return {catalog_row(state, last, status_row)}
    end
    return {status_row(operation, last.verb, last.mode, state.map, last.pawn, last.board, last.quest,
        last.state, last.before, last.after, last.expected_step or "-", last.cash_before, last.cash_after,
        last.xp_before, last.xp_after, last.cash, last.xp, last.guid,
        last.units or "-", last.remaining or "-", last.consumed, additional_deposits(last, true), last.rewards)}
end

return board_contract
