local sickness = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SICKNESS") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local watching, fixture = {}, nil

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function host_stock(state, phase)
    local host = state.local_pawn and state.local_pawn.pawn
    local shadows = type(SilverfishIsNativeShadowFeedbackTarget) == "function"
    if not (enabled or shadows) or phase ~= "hosting" or not state.listening or state.server == true
        or not valid(host) or not valid(state.world) or not valid(host.Save) then return end
    return host.Save
end

local function release(entry)
    if entry and entry.address and type(SilverfishClearNativeGuestSickness) == "function" then
        SilverfishClearNativeGuestSickness(entry.address)
    end
end

function sickness.clear()
    if next(watching) and type(SilverfishClearNativeGuestSickness) == "function" then
        SilverfishClearNativeGuestSickness()
    end
    watching, fixture = {}, nil
end

local function observe(entry)
    local status, infections, infection_writes, ticks, tick_writes, guest, host, previous, current =
        SilverfishReadNativeGuestSickness(entry.address)
    if type(status) ~= "string" or not status:match("^[a-z0-9_]+$") then return end
    for _, value in ipairs({ infections, infection_writes, ticks, tick_writes }) do
        if math.type(value) ~= "integer" or value < 0 then return end
    end
    if math.type(infections) ~= "integer" or math.type(infection_writes) ~= "integer"
        or math.type(ticks) ~= "integer" or math.type(tick_writes) ~= "integer"
        or math.type(guest) ~= "integer" or math.type(host) ~= "integer"
        or math.type(previous) ~= "integer" or math.type(current) ~= "integer" then return end
    return {status, infections, infection_writes, ticks, tick_writes, guest, host, previous, current}
end

function sickness.sync(state, phase, exact)
    local stock = host_stock(state, phase)
    if not stock or type(SilverfishSetNativeGuestSickness) ~= "function"
        or type(SilverfishReadNativeGuestSickness) ~= "function" then sickness.clear() return end
    local active = {}
    local qualify = exact and guest_save.exact_item_context or guest_save.observed_item_context
    for _, detail in ipairs(state.guests or {}) do
        local qualified, pawn, detached = pcall(qualify, state, phase, exact and detail.name or detail)
        if qualified and valid(pawn) and valid(detached) and detached:GetAddress() ~= stock:GetAddress() then
            local address = pawn:GetAddress()
            local old = watching[detail.name]
            if old and (old.address ~= address or old.map ~= state.map) then release(old) end
            local accepted, status = SilverfishSetNativeGuestSickness(address, detached:GetAddress(), stock:GetAddress())
            local entry = {map = state.map, address = address, status = status}
            if accepted == true then entry.proof = observe(entry) end
            active[detail.name] = entry
        end
    end
    for name, entry in pairs(watching) do
        if not active[name] then release(entry) end
    end
    watching = active
    if fixture and not watching[fixture.name] then fixture = nil end
end

function sickness.rows(state, phase, status_row)
    local rows = {}
    if not host_stock(state, phase) then return rows end
    for _, detail in ipairs(state.guests or {}) do
        local entry = watching[detail.name]
        if entry and entry.map == state.map then
            if entry.proof then
                rows[#rows + 1] = status_row("native-guest-sickness", detail.name, table.unpack(entry.proof))
            elseif type(entry.status) == "string" and entry.status:match("^[a-z0-9_]+$") then
                rows[#rows + 1] = status_row("native-guest-sickness-status", detail.name, entry.status)
            end
        end
    end
    if fixture and fixture.map == state.map then
        rows[#rows + 1] = status_row("native-guest-sickness-fixture", fixture.map, fixture.name, fixture.amount, fixture.status)
    end
    return rows
end

function sickness.command(fields, state, phase)
    if fields[2] ~= "native-guest-sickness-fixture" then return end
    fixture = nil
    local name, amount = fields[3], fields[4]
    amount = type(amount) == "string" and amount:match("^%-?%d+$") and math.tointeger(tonumber(amount))
    if not fixtures or #fields ~= 4 or not amount or amount < -120 or amount > 120 or amount == 0
        or not host_stock(state, phase) or type(SilverfishProbeNativeGuestSickness) ~= "function" then
        error("The stock sickness fixture requires an enrolled guest and a bounded nonzero amount", 0)
    end
    sickness.sync(state, phase, true)
    local entry = watching[name]
    if not entry or not entry.proof then error("The exact detached guest sickness scope is unavailable", 0) end
    local ok, status = SilverfishProbeNativeGuestSickness(entry.address, amount)
    if ok ~= true or status ~= "stock_infection_verified" then error(tostring(status), 0) end
    entry.proof = observe(entry)
    if not entry.proof then error("mutation_unverified_stock_sickness_fixture_proof", 0) end
    fixture = {map = state.map, name = name, amount = amount, status = status}
    return "stock guest infection verified"
end

return sickness
