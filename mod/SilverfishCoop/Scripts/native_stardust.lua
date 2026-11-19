local stardust = {}
local transport = require("native_transport")

local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local event_path = bridge and bridge .. "/native-stardust-events"
local stardust_path = "/Game/Classes/Items/Artifacts/Art_DreamDust.Art_DreamDust_C"
local entries = { action_use = true, quick_move = true, use_held_item = true }
local actions = {
    ["native-stardust-inspect"] = { "inspect", 4 },
    ["native-stardust-debit"] = { "debit", 5 },
    ["native-stardust-restore"] = { "restore", 6 },
    ["native-stardust-seed"] = { "seed", 5 },
    ["native-stardust-use-probe"] = { "use-probe", 4 },
}
local sequence = 0
local pending
local report

local function identity(state, host)
    return { map = state.map, pawn = host.name, address = host.pawn:GetAddress(), world = state.world:GetAddress() }
end

local function matches(record, state, host)
    return record and host and record.map == state.map and record.pawn == host.name
        and record.address == host.pawn:GetAddress() and record.world == state.world:GetAddress()
end

local function emit()
    local file = event_path and io.open(event_path, "ab")
    if not file then return false end
    local written = file:write(pending.line)
    local closed = file:close()
    return written and closed
end

function stardust.sync(state, phase)
    if type(SilverfishTakeNativeStardustTransportRequest) ~= "function" then return end
    local host, fresh = transport.scoped_host(state, phase)
    if not matches(report, state, host) then report = nil end
    if not host then
        pending = nil
        return
    end
    if pending then
        if not fresh or not matches(pending, state, host) or os.time() > pending.expires or emit() then
            pending = nil
        else
            return
        end
    end
    local ok, guid, entry = SilverfishTakeNativeStardustTransportRequest(host.pawn:GetAddress())
    if not ok or not fresh or not transport.valid_guid(guid) or not entries[entry] then return end
    sequence = sequence + 1
    pending = identity(state, host)
    pending.expires = os.time() + 2
    pending.line = table.concat({ "stardust-request", tostring(sequence), state.map, host.name, guid, entry,
        string.format("%.17g", host.x), string.format("%.17g", host.y), string.format("%.17g", host.z) }, "\t") .. "\n"
    if emit() then pending = nil end
end

function stardust.command(fields, state, phase)
    local spec = actions[fields[2]]
    if not spec then return end
    report = nil
    local action, count = spec[1], spec[2]
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
    transport.load_class(stardust_path)
    local address = host.pawn:GetAddress()
    local ok, result, quantity, payload
    if action == "inspect" and type(SilverfishInspectNativeStardustTransport) == "function" then
        ok, result, quantity, payload = SilverfishInspectNativeStardustTransport(address, guid)
    elseif action == "debit" and type(SilverfishCommitNativeStardustTransport) == "function" then
        ok, result, quantity = SilverfishCommitNativeStardustTransport(address, guid, units)
    elseif action == "restore" and type(SilverfishRestoreNativeStardustTransport) == "function" then
        ok, result, quantity = SilverfishRestoreNativeStardustTransport(address, guid, units, fields[6])
    elseif action == "seed" and type(SilverfishSeedNativeStardustTransport) == "function" then
        ok, result, quantity = SilverfishSeedNativeStardustTransport(address, guid, units)
    elseif action == "use-probe" and type(SilverfishProbeNativeStardustTransport) == "function" then
        local manager = transport.scratch_manager(state)
        ok, result = SilverfishProbeNativeStardustTransport(address, manager:GetAddress(), guid)
        if ok then result, quantity = "requested", 0 end
    else
        error("The qualified native Stardust bridge is unavailable", 0)
    end
    if not ok or type(result) ~= "string" or type(quantity) ~= "number" or quantity < 0 then
        error("Stardust " .. action .. " failed (" .. tostring(result) .. ")", 0)
    end
    report = identity(state, host)
    report.action, report.result, report.guid, report.quantity = action, result, guid, quantity
    report.payload = payload == "stock-default-v1" and payload or ""
    return "stardust " .. action .. " " .. result
end

function stardust.authorize_travel(guid, state, phase)
    local host, fresh = transport.scoped_host(state, phase)
    if not fresh or not matches(report, state, host) or report.action ~= "debit" or report.guid ~= guid
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
    return { status_row("native-stardust", report.action, report.result, report.map, report.pawn, report.guid,
        report.quantity, report.payload) }
end

return stardust
