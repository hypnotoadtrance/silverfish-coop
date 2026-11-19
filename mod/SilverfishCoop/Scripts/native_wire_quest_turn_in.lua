local meet = require("native_wire_quest")
local rewards = require("native_wire_rewards")
local redemption = {}
local verb = "native-wire-quest-turn-in"
local library_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_quantity = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local last
local last_reward

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function int32(value)
    return type(value) == "number" and math.tointeger(value) and value >= -2147483648 and value <= 2147483647
end

local function guid(item)
    local id = item[item_guid]
    if not id or not int32(id.A) or not int32(id.B) or not int32(id.C) or not int32(id.D) then return end
    local text = string.format("%08X%08X%08X%08X", id.A & 0xffffffff, id.B & 0xffffffff,
        id.C & 0xffffffff, id.D & 0xffffffff)
    if text:find("[^0]") then return text end
end

local function inventory(context)
    local weapons, ammo, identities = {}, 0, {}
    for _, array in ipairs({context.save.PlayerBackpack, context.save.PlayerEquipment}) do
        local count = array:GetArrayNum()
        if not int32(count) or count < 0 or count > 4096 then error("stock_wire_quest_inventory_extent", 0) end
        for index = 1, count do
            local item = array[index]
            local class = item and item[item_class]
            local identity = item and valid(class) and guid(item)
            if identity then identities[identity] = (identities[identity] or 0) + 1 end
            if same(class, context.rewards[1]) then
                if not identity or weapons[identity] then error("stock_wire_quest_weapon_identity", 0) end
                weapons[identity] = true
            elseif same(class, context.rewards[2]) then
                local units = item[item_quantity]
                if not identity or not int32(units) or units <= 0 or not int32(ammo + units) then
                    error("stock_wire_quest_reserve_unqualified", 0)
                end
                ammo = ammo + units
            end
        end
    end
    return weapons, ammo, identities
end

local function expected_rep(context, proof)
    local effect, out = FName("IntensePersonality", EFindName.FNAME_Find), {}
    if effect:GetComparisonIndex() == 0 then error("stock_wire_quest_reputation_trait_unavailable", 0) end
    context.instance:FindPassiveEffect(effect, true, out, out)
    if type(out.EffectActive) ~= "boolean" or not int32(out.Instances) or out.Instances < 0 then
        error("stock_wire_quest_reputation_trait_unqualified", 0)
    end
    local delta = meet.commune_reward * (proof.commune_rep_found and 1 + out.Instances or 1)
    local target = proof.native_commune_rep + delta
    if not int32(delta) or not int32(target) or not int32(proof.native_xp + 1) then
        error("stock_wire_quest_reward_range", 0)
    end
    return target
end

function redemption.command(fields, state, phase)
    if fields[2] == "native-wire-quest-capture" then
        last_reward = nil
        if #fields ~= 4 then error("stock_wire_quest_capture_arguments", 0) end
        last_reward = rewards.capture(state, phase, fields[3], fields[4])
        return "stock Copper Wire reward escrow verified"
    elseif fields[2] == "native-wire-quest-grant" then
        last_reward = nil
        if #fields ~= 8 then error("stock_wire_quest_grant_arguments", 0) end
        last_reward = rewards.grant(state, phase, table.unpack(fields, 3))
        return "stock Copper Wire guest rewards verified"
    end
    if fields[2] ~= verb then return end
    last = nil
    if #fields ~= 5 or type(SilverfishCaptureWireRewards) ~= "function"
        or type(SilverfishGrantWireRewards) ~= "function" then error("stock_wire_quest_redemption_unavailable", 0) end
    local context, before = meet.preflight(state, phase, fields[3])
    if before.state ~= "active" and before.state ~= "ready" then error("stock_wire_quest_not_ready", 0) end
    if before.objective_guid == "-" or before.objective_guid ~= fields[4] or before.objective_owner ~= fields[5] then
        error("stock_wire_quest_objective_changed", 0)
    end
    local before_weapons, before_ammo, before_ids = inventory(context)
    local reputation = expected_rep(context, before)
    local library = StaticFindObject(library_path)
    if not valid(library) or library:GetFullName() ~= "FL_CodeLib01_C " .. library_path then
        error("stock_wire_quest_turn_in_library_unavailable", 0)
    end
    if before.objective_owner == "guest" then
        if type(SilverfishStageWireQuestObjective) ~= "function" then error("stock_wire_quest_guest_objective_unavailable", 0) end
        local accepted, reason = SilverfishStageWireQuestObjective(context.interactor:GetAddress(),
            context.receiving_save:GetAddress(), context.save:GetAddress(), before.objective_guid)
        if accepted ~= true then error(reason or "stock_wire_quest_guest_objective_refused", 0) end
    end
    local output = {}
    library:TurnIn_Quest(context.key, context.instance, true, context.host, output, output)
    local after_context, after = meet.preflight(state, phase, fields[3])
    if output.Success ~= true or valid(output.ConfirmationPopup) or after.state ~= "completed"
        or not same(after_context.save, context.save) or not same(after_context.instance, context.instance)
        or not same(after_context.receiving_save, context.receiving_save)
        or after.native_cash ~= before.native_cash or after.native_xp ~= before.native_xp + 1
        or not after.commune_rep_found or after.native_commune_rep ~= reputation
        or meet.objective_guid(context.save, context.objective)
        or before.objective_owner == "guest" and meet.objective_guid(context.receiving_save, context.objective) then
        error("mutation_unverified_stock_wire_quest_completion", 0)
    end
    local weapons, ammo, identities = inventory(context)
    local new
    for identity in pairs(weapons) do
        if not before_weapons[identity] then
            if new or before_ids[identity] or identities[identity] ~= 1 then
                error("mutation_unverified_stock_wire_quest_weapon_identity", 0)
            end
            new = identity
        end
    end
    for identity in pairs(before_weapons) do
        if not weapons[identity] then error("mutation_unverified_stock_wire_quest_weapon_removed", 0) end
    end
    if not new or ammo - before_ammo ~= 14 then error("mutation_unverified_stock_wire_quest_inventory", 0) end
    last = {name = fields[3], world = context.world, save = context.save, host = context.host,
        kit = new, ammo = ammo - before_ammo, cash = after.native_cash, xp = after.native_xp,
        rep_before = before.native_commune_rep, rep_after = reputation, objective_guid = before.objective_guid,
        objective_owner = before.objective_owner}
    return "stock Copper Wire completion and host items verified"
end

function redemption.rows(state, phase, status_row)
    if phase ~= "hosting" then
        last, last_reward = nil, nil
        return {}
    end
    local rows = {}
    if last and state.map == meet.map and same(last.world, state.world) and valid(last.host) and same(last.host.Save, last.save) then
        rows[#rows + 1] = status_row("native-wire-quest-redemption", meet.map, last.name, meet.board_name, meet.quest_id,
            last.kit, last.ammo, last.cash, last.xp, last.rep_before, last.rep_after, last.objective_guid, last.objective_owner)
    else
        last = nil
    end
    if last_reward and last_reward.map == state.map then
        rows[#rows + 1] = rewards.row(last_reward, status_row)
    else
        last_reward = nil
    end
    return rows
end

return redemption
