local consumable = {}
local guest_save = require("guest_save")
local use_presentation = require("native_weapon_fire_view")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local starter_bag = "native-guest-starter-bag"
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

function consumable.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-guest-bandage" and action ~= starter_bag then return end
    observation = nil
    local name, guid = fields[3], fields[4]
    local bag = action == starter_bag
    local prefix = bag and "guest_starter_bag_refused_" or "guest_bandage_refused_"
    if not enabled or #fields ~= 4 or phase ~= "hosting" or not state.listening or not exact_guid(guid) then
        error(prefix .. "scope", 0)
    end
    local native_use = bag and SilverfishOpenNativeGuestStarterBag or SilverfishUseNativeGuestBandage
    if type(native_use) ~= "function" or bag and native_use ~= SilverfishOpenNativeGuestStarterBag then
        error(prefix .. "addon_unavailable", 0)
    end

    local qualified, pawn, detached = pcall(guest_save.exact_item_context, state, phase, name)
    local host = state.local_pawn and state.local_pawn.pawn
    local stock = valid(host) and host.Save
    if not qualified or not valid(pawn) or not valid(detached) or not stock or not valid(stock)
        or detached:GetAddress() == stock:GetAddress() then
        error(prefix .. "exact_detached_save", 0)
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

function consumable.reset()
    observation = nil
end

function consumable.rows(state, phase, status_row)
    local row = observation
    if not enabled or phase ~= "hosting" or not state.listening or not row or row.map ~= state.map then return {} end
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
