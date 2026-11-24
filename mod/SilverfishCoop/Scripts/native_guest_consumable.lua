local consumable = {}
local guest_save = require("guest_save")
local local_save = require("native_save_scope")
local use_presentation = require("native_weapon_fire_view")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local starter_bag = "native-guest-starter-bag"
local personal_consumable = "native-guest-personal-consumable"
local cooked_meal = "native-guest-cooked-meal"
local observation

local function valid(value)
    return value ~= nil and value:IsValid()
end

local function exact_guid(value)
    return type(value) == "string" and #value == 32 and not value:find("[^0-9A-F]") and value:find("[^0]")
end

local function finite(value)
    return type(value) == "number" and value == value and value > -math.huge and value < math.huge
end

local function settle_personal(fields, state, pawn, detached, stock)
    local class, quantity = fields[5], math.tointeger(tonumber(fields[6]))
    local food, health, regen, stim = tonumber(fields[7]), tonumber(fields[8]), tonumber(fields[9]), tonumber(fields[10])
    if type(SilverfishSettleNativeGuestPersonalConsumable) ~= "function" or type(class) ~= "string"
        or #class > 256 or not class:match("^/[%w_/%-]+%.[%w_%-]+_C$")
        or not quantity or quantity < 1 or quantity >= 2^31 or tostring(quantity) ~= fields[6]
        or not finite(food) or not finite(health) or not finite(regen) or not finite(stim) then
        error("guest_personal_consumable_refused_arguments", 0)
    end
    local called, used, status, before, after, remaining_food, remaining_health, remaining_regen, remaining_stim =
        pcall(SilverfishSettleNativeGuestPersonalConsumable, pawn:GetAddress(), detached:GetAddress(),
            stock:GetAddress(), fields[4], class, quantity, food, health, regen, stim)
    if called and used == false and type(status) == "string"
        and (status:match("^guest_personal_consumable_refused_[a-z0-9_]+$")
            or status:match("^mutation_unverified_guest_personal_consumable_[a-z0-9_]+$")) then error(status, 0) end
    if not called or used ~= true or status ~= "used" or before ~= quantity or after ~= quantity - 1
        or not finite(remaining_food) or not finite(remaining_health) or not finite(remaining_regen) or not finite(remaining_stim) then
        error("mutation_unverified_guest_personal_consumable_proof", 0)
    end
    observation = {map = state.map, pawn = fields[3], guid = fields[4], before = before, after = after,
        personal_class = class, food = remaining_food, health = remaining_health, regen = remaining_regen, stim = remaining_stim}
    pcall(use_presentation.consumed, pawn, class, false)
    return "native guest personal consumable used"
end

function consumable.command(fields, state, phase)
    local action = fields[2]
    if action == cooked_meal then
        local name = fields[3]
        if not enabled or phase ~= "hosting" or not state.listening or #fields ~= 8
            or type(SilverfishSettleNativeGuestCookedMeal) ~= "function" then error("guest_cooked_meal_refused_scope", 0) end
        local pawn, detached = guest_save.exact_item_context(state, phase, name)
        local stock = state.local_pawn and state.local_pawn.pawn.Save
        if not valid(pawn) or not valid(detached) or not valid(stock) then error("guest_cooked_meal_refused_scope", 0) end
        local applied, food, health, regen, fatigue, intox = SilverfishSettleNativeGuestCookedMeal(
            pawn:GetAddress(), detached:GetAddress(), stock:GetAddress(), tonumber(fields[4]),
            tonumber(fields[5]), tonumber(fields[6]), math.tointeger(tonumber(fields[7])), math.tointeger(tonumber(fields[8])))
        if not applied then error("guest_cooked_meal_refused_scope", 0) end
        return ("cooked meal %.17g %.17g %.17g %d %d"):format(food, health, regen, fatigue, intox)
    end
    if action ~= "native-guest-bandage" and action ~= starter_bag and action ~= personal_consumable then return end
    observation = nil
    local name, guid = fields[3], fields[4]
    local bag = action == starter_bag
    local personal = action == personal_consumable
    local prefix = personal and "guest_personal_consumable_refused_"
        or bag and "guest_starter_bag_refused_" or "guest_bandage_refused_"
    if not enabled or #fields ~= (personal and 10 or 4) or phase ~= "hosting" or not state.listening or not exact_guid(guid) then
        error(prefix .. "scope", 0)
    end

    local qualified, pawn, detached = pcall(guest_save.exact_item_context, state, phase, name)
    local host = state.local_pawn and state.local_pawn.pawn
    local stock = valid(host) and host.Save
    if not qualified or not valid(pawn) or not valid(detached) or not stock or not valid(stock)
        or detached:GetAddress() == stock:GetAddress() then
        error(prefix .. "exact_detached_save", 0)
    end
    if personal then return settle_personal(fields, state, pawn, detached, stock) end

    local native_use = bag and SilverfishOpenNativeGuestStarterBag or SilverfishUseNativeGuestBandage
    if type(native_use) ~= "function" or bag and native_use ~= SilverfishOpenNativeGuestStarterBag then
        error(prefix .. "addon_unavailable", 0)
    end
    local called, used, status, before, after, regen_before, regen_after, hp_before, hp_after, maximum, trait =
        pcall(native_use, pawn:GetAddress(), detached:GetAddress(), stock:GetAddress(), guid)
    if bag then
        if called and used == false and type(status) == "string"
            and (status:match("^guest_starter_bag_refused_[a-z0-9_]+$") or
                status:match("^mutation_unverified_guest_starter_bag_[a-z0-9_]+$")) then error(status, 0) end
        if not called or used ~= true or status ~= "used" or before ~= "random" and before ~= "hyperrandom"
            or type(after) ~= "string" or #after > 527 or after:sub(-1) == "," then
            error("mutation_unverified_guest_starter_bag_proof", 0)
        end
        local seen, count = {[guid] = true}, 0
        for reward in (after .. ","):gmatch("([^,]*),") do
            if not exact_guid(reward) or seen[reward] then error("mutation_unverified_guest_starter_bag_rewards", 0) end
            seen[reward], count = true, count + 1
        end
        if count < 1 or count > 16 then error("mutation_unverified_guest_starter_bag_rewards", 0) end
        observation = {map = state.map, pawn = name, guid = guid, kind = before, awards = after}
        return "native guest starter bag opened"
    end
    if not called then error("mutation_unverified_guest_bandage_call", 0) end
    if used == false and type(status) == "string"
        and (status:match("^guest_bandage_refused_[a-z0-9_]+$") or
            status:match("^mutation_unverified_guest_bandage_[a-z0-9_]+$")) then
        error(status, 0)
    end
    if used ~= true or status ~= "used" or math.type(before) ~= "integer" or before < 1
        or math.type(after) ~= "integer" or after ~= before - 1 or not finite(maximum) or maximum < 25
        or not finite(regen_before) or regen_before < 0 or regen_before > maximum
        or not finite(regen_after) or regen_after < 25 or regen_after > maximum
        or not finite(hp_before) or hp_before <= 0 or hp_before > maximum or hp_after ~= hp_before
        or type(trait) ~= "boolean" then
        error("mutation_unverified_guest_bandage_proof", 0)
    end
    observation = {
        map = state.map, pawn = name, guid = guid, before = before, after = after,
        regen_before = regen_before, regen_after = regen_after, hp_before = hp_before, hp_after = hp_after,
        max_health = maximum, hemophilia = trait,
    }
    pcall(use_presentation.consumed, pawn, "/Game/Classes/Items/Item_Bandage.Item_Bandage_C", false)
    return "native guest Bandage used"
end

function consumable.sync(state, phase)
    if not enabled or type(SilverfishPollNativeGuestCookedMeal) ~= "function"
        or type(SilverfishEnqueueLocalBridgeInput) ~= "function" then return end
    if local_save.guest_vitals(state, phase) == nil then
        SilverfishPollNativeGuestCookedMeal()
        return
    end
    local owner = state.local_pawn
    for _ = 1, 8 do
        local sequence, food, health, regen, fatigue, intox =
            SilverfishPollNativeGuestCookedMeal(owner.pawn:GetAddress(), owner.pawn.Save:GetAddress())
        if not sequence then return end
        local record = ("%s\t%d\t%s\t%s\t%.17g,%.17g,%.17g,%d,%d\n"):format(
            cooked_meal, sequence, state.map, owner.name, food, health, regen, fatigue, intox)
        if not SilverfishEnqueueLocalBridgeInput(record) then return end
    end
end

function consumable.reset()
    observation = nil
end

function consumable.rows(state, phase, status_row)
    local row = observation
    if not enabled or phase ~= "hosting" or not state.listening or not row or row.map ~= state.map then return {} end
    if row.personal_class then
        return {status_row(personal_consumable, "used", row.map, row.pawn, row.guid, row.personal_class, row.before, row.after,
            string.format("%.17g", row.food), string.format("%.17g", row.health),
            string.format("%.17g", row.regen), string.format("%.17g", row.stim))}
    end
    if row.kind then
        return {status_row(starter_bag, "used", row.map, row.pawn, row.guid, row.kind, row.awards)}
    end
    return {
        status_row("native-guest-bandage", "used", row.map, row.pawn, row.guid, row.before, row.after,
            string.format("%.17g", row.regen_before), string.format("%.17g", row.regen_after),
            string.format("%.17g", row.hp_before), string.format("%.17g", row.hp_after),
            string.format("%.17g", row.max_health), row.hemophilia and 1 or 0),
    }
end

return consumable
