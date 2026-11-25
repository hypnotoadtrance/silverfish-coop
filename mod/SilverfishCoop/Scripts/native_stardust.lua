local stardust = {}
local transport = require("native_transport")

local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local embedded = os.getenv("SILVERFISH_COOP_EMBEDDED_ENGINE") == "1"
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local kinds = {
    stardust = { label = "Stardust", asset = "Art_DreamDust", payload = "stock-default-v1", sequence = 0 },
    ["fruiting-stardust"] = {
        label = "FruitingStardust", asset = "Art_FruitingDreamDust", payload = "stock-fruiting-default-v1", sequence = 0,
    },
}
local entries = { action_use = true, quick_move = true, use_held_item = true }
local actions = {
    inspect = 4, debit = 5, restore = 6, seed = 5, ["use-probe"] = 4,
}
local report

local function identity(state, host)
    return { map = state.map, pawn = host.name, address = host.pawn:GetAddress(), world = state.world:GetAddress() }
end

local function matches(record, state, host)
    return record and host and record.map == state.map and record.pawn == host.name
        and record.address == host.pawn:GetAddress() and record.world == state.world:GetAddress()
end

local function emit(kind, pending)
    if embedded then
        return type(SilverfishEnqueueLocalBridgeInput) == "function"
            and SilverfishEnqueueLocalBridgeInput(pending.line) == true
    end
    local file = bridge and io.open(bridge .. "/native-" .. kind .. "-events", "ab")
    if not file then return false end
    local written = file:write(pending.line)
    local closed = file:close()
    return written and closed
end

local function transfer_requests(kind, spec, state, host, fresh)
    local take = _G["SilverfishTakeNative" .. spec.label .. "TransportRequest"]
    if type(take) ~= "function" then return end
    if not host then
        spec.pending = nil
        return
    end
    local pending = spec.pending
    if pending then
        if not fresh or not matches(pending, state, host) or os.time() > pending.expires or emit(kind, pending) then
            spec.pending = nil
        else
            return
        end
    end
    local ok, guid, entry = take(host.pawn:GetAddress())
    if not ok or not fresh or not transport.valid_guid(guid) or not entries[entry] then return end
    spec.sequence = spec.sequence + 1
    pending = identity(state, host)
    pending.expires = os.time() + 2
    pending.line = table.concat({ kind .. "-request", tostring(spec.sequence), state.map, host.name, guid, entry,
        string.format("%.17g", host.x), string.format("%.17g", host.y), string.format("%.17g", host.z) }, "\t") .. "\n"
    if not emit(kind, pending) then spec.pending = pending end
end

function stardust.sync(state, phase)
    local host, fresh = transport.scoped_host(state, phase)
    if not matches(report, state, host) then report = nil end
    for kind, spec in pairs(kinds) do transfer_requests(kind, spec, state, host, fresh) end
end

function stardust.command(fields, state, phase)
    local action = fields[2]:match("^native%-stardust%-(.+)$")
    local kind = "stardust"
    if not action then
        action = fields[2]:match("^native%-fruiting%-stardust%-(.+)$")
        kind = "fruiting-stardust"
    end
    local count = actions[action]
    if not count then return end
    local spec = kinds[kind]
    report = nil
    local host, fresh = transport.scoped_host(state, phase)
    local guid = fields[4]
    if #fields ~= count or not host or not fresh or host.name ~= fields[3] or not transport.valid_guid(guid) then
        error("Stardust operations require the exact active host pawn and stack GUID", 0)
    end
    if (action == "seed" or action == "use-probe") and not fixtures then
        error("This Stardust command requires the isolated fixture host", 0)
    end
    local units
    if fields[5] then
        units = math.tointeger(tonumber(fields[5]))
        if not units or units < 1 or units > 2147483647 then error("Invalid Stardust stack quantity", 0) end
    end
    local api_name = ({ inspect = "Inspect", debit = "Commit", restore = "Restore", seed = "Seed",
        ["use-probe"] = "Probe" })[action]
    local operate = _G["Silverfish" .. api_name .. "Native" .. spec.label .. "Transport"]
    if type(operate) ~= "function" then error("The qualified native Stardust bridge is unavailable", 0) end
    transport.load_class("/Game/Classes/Items/Artifacts/" .. spec.asset .. "." .. spec.asset .. "_C")
    local address = host.pawn:GetAddress()
    local ok, result, quantity, payload
    if action == "inspect" then
        ok, result, quantity, payload = operate(address, guid)
    elseif action == "restore" then
        ok, result, quantity = operate(address, guid, units, fields[6])
    elseif action == "use-probe" then
        local manager = transport.scratch_manager(state)
        ok, result = operate(address, manager:GetAddress(), guid)
        if ok then result, quantity = "requested", 0 end
    else
        ok, result, quantity = operate(address, guid, units)
    end
    if not ok or type(result) ~= "string" or type(quantity) ~= "number" or quantity < 0 then
        error("Stardust " .. action .. " failed (" .. tostring(result) .. ")", 0)
    end
    report = identity(state, host)
    report.kind, report.action, report.result, report.guid, report.quantity = kind, action, result, guid, quantity
    report.payload = payload == spec.payload and payload or ""
    return kind .. " " .. action .. " " .. result
end

function stardust.authorize_travel(kind, guid, state, phase)
    local host, fresh = transport.scoped_host(state, phase)
    if not fresh or not matches(report, state, host) or report.kind ~= kind or report.action ~= "debit" or report.guid ~= guid
        or (report.result ~= "removed" and report.result ~= "decremented" and report.result ~= "already_debited") then
        return false
    end
    report = nil
    return true
end

function stardust.rows(state, phase, status_row)
    if not report then return {} end
    local host = transport.scoped_host(state, phase)
    if not matches(report, state, host) then return {} end
    return { status_row("native-" .. report.kind, report.action, report.result, report.map, report.pawn, report.guid,
        report.quantity, report.payload) }
end

return stardust
