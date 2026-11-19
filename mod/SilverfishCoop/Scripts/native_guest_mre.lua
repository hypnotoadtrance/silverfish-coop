local mre = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_MRE") == "1"
local observation, fixture, ready

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function exact_guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]")
end
local function bounded(number, minimum, maximum)
    return type(number) == "number" and number >= minimum and number <= maximum
end

function mre.sync(state, phase)
    if not ready and enabled and phase == "hosting" and state.server == false and state.listening
        and type(SilverfishPrepareNativeGuestMre) == "function" then
        ready = SilverfishPrepareNativeGuestMre() == true
    end
end

function mre.reset() observation, fixture = nil, nil end

function mre.command(fields, state, phase)
    local test = fields[2] == "native-guest-mre-fixture"
    if fields[2] ~= "native-guest-mre" and not test then return end
    if test then fixture = nil else observation = nil end
    local name, guid = fields[3], fields[4]
    if not enabled or phase ~= "hosting" or state.server ~= false or not state.listening
        or (test and (#fields ~= 3 or type(SilverfishSeedNativeGuestMreFixture) ~= "function"))
        or (not test and (#fields ~= 4 or not exact_guid(guid) or type(SilverfishUseNativeGuestMre) ~= "function")) then
        error("guest_mre_refused_scope", 0)
    end
    local qualified, pawn, detached = pcall(guest_save.exact_item_context, state, phase, name)
    local host = state.local_pawn and state.local_pawn.pawn
    local stock = valid(host) and host.Save
    if not qualified or not valid(pawn) or pawn:GetFullName() ~= name or not valid(detached)
        or not valid(stock) or detached:GetAddress() == stock:GetAddress() then
        error("guest_mre_refused_exact_detached_save", 0)
    end
    if test then
        local invoked, ok, status, food, intox, stock_intox = pcall(
            SilverfishSeedNativeGuestMreFixture, pawn:GetAddress(), detached:GetAddress(), stock:GetAddress())
        if invoked and ok == false and type(status) == "string" and status:match("^guest_mre_refused_[a-z0-9_]+$") then
            error(status, 0)
        end
        if not invoked or ok ~= true or status ~= "verified" or food ~= 17 or intox ~= 42
            or math.type(stock_intox) ~= "integer" or not bounded(stock_intox, 0, 100) then
            error("mutation_unverified_guest_mre_fixture", 0)
        end
        fixture = {map = state.map, name = name, food = food, intox = intox, stock_intox = stock_intox}
        return "exact detached MRE test baseline verified"
    end
    local called, used, status, before, after, food_before, food_after, regen_before, regen_after,
        health, maximum, intox_before, intox_after, host_intox, trait =
        pcall(SilverfishUseNativeGuestMre, pawn:GetAddress(), detached:GetAddress(), stock:GetAddress(), guid)
    if not called then error("mutation_unverified_guest_mre_call", 0) end
    if used == false and type(status) == "string" and (status:match("^guest_mre_refused_[a-z0-9_]+$")
        or status:match("^mutation_unverified_guest_mre_[a-z0-9_]+$")) then error(status, 0) end
    if used ~= true or status ~= "used" or math.type(before) ~= "integer" or before < 1
        or math.type(after) ~= "integer" or after ~= before - 1 or not bounded(maximum, 25, 10000)
        or not bounded(health, 0, maximum) or health == 0 or not bounded(food_before, 0, 200)
        or food_after ~= math.min(food_before + 30, 200) or not bounded(regen_before, 0, maximum)
        or type(trait) ~= "boolean" or regen_after ~= math.min(math.max(regen_before + (trait and 6 or 10), 25), maximum)
        or math.type(intox_before) ~= "integer" or not bounded(intox_before, 0, 100)
        or math.type(intox_after) ~= "integer" or intox_after ~= math.max(intox_before - 3, 0)
        or math.type(host_intox) ~= "integer" or not bounded(host_intox, 0, 100) then
        error("mutation_unverified_guest_mre_proof", 0)
    end
    observation = {map = state.map, name = name, guid = guid, before = before, after = after,
        food_before = food_before, food_after = food_after, regen_before = regen_before, regen_after = regen_after,
        health = health, maximum = maximum, intox_before = intox_before, intox_after = intox_after,
        host_intox = host_intox, trait = trait}
    return "exact stock guest MRE effects verified"
end

function mre.rows(state, phase, status_row)
    local row = observation
    if not enabled or phase ~= "hosting" or state.server ~= false or not state.listening then return {} end
    local rows = {}
    if fixture and fixture.map == state.map then
        rows[#rows + 1] = status_row("native-guest-mre-fixture", fixture.map, fixture.name, fixture.food, fixture.intox, fixture.stock_intox)
    end
    local function real(value) return string.format("%.17g", value) end
    if row and row.map == state.map then
        rows[#rows + 1] = status_row("native-guest-mre", "used", row.map, row.name, row.guid, row.before, row.after,
            real(row.food_before), real(row.food_after), real(row.regen_before), real(row.regen_after),
            real(row.health), real(row.maximum), row.intox_before, row.intox_after, row.host_intox, row.trait and 1 or 0)
    end
    return rows
end

return mre
