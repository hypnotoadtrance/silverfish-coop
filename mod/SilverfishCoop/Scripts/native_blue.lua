local blue = {}
local transport = require("native_transport")

local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local event_path = bridge and bridge .. "/native-blue-events"
local blue_path = "/Game/Classes/Items/Artifacts/Art_Tesseract_Blue.Art_Tesseract_Blue_C"
local entries = { action_use = true, quick_move = true, use_held_item = true }
local actions = {
    ["native-blue-inspect"] = "inspect",
    ["native-blue-debit"] = "debit",
    ["native-blue-seed"] = "seed",
    ["native-blue-use-probe"] = "use-probe",
}
local sequence = 0
local pending
local last_report

local function snapshot(state, host)
    return { map = state.map, pawn = host.name, address = host.pawn:GetAddress(), world = state.world:GetAddress() }
end

local function same_host(record, state, host)
    return record and record.map == state.map and record.pawn == host.name
        and record.address == host.pawn:GetAddress() and record.world == state.world:GetAddress()
end

local function append_event(line)
    local file = event_path and io.open(event_path, "ab")
    if not file then return false end
    local written = file:write(line)
    local closed = file:close()
    return written and closed
end

function blue.sync(state, phase)
    if type(SilverfishTakeNativeBlueTransportRequest) ~= "function" then return end
    local host, fresh = transport.scoped_host(state, phase)
    if not host then
        pending = nil
        last_report = nil
        return
    end
    if last_report and not same_host(last_report, state, host) then last_report = nil end
    if pending then
        if not fresh or not same_host(pending, state, host) or os.time() > pending.expires then
            pending = nil
        elseif append_event(pending.line) then
            pending = nil
        else
            return
        end
    end

    local observed, guid, entry = SilverfishTakeNativeBlueTransportRequest(host.pawn:GetAddress())
    if not observed or not fresh or not transport.valid_guid(guid) or not entries[entry] then return end
    sequence = sequence + 1
    pending = snapshot(state, host)
    pending.expires = os.time() + 2
    pending.line = table.concat({ "blue-request", tostring(sequence), state.map, host.name, guid, entry,
        string.format("%.17g", host.x), string.format("%.17g", host.y), string.format("%.17g", host.z) }, "\t") .. "\n"
    if append_event(pending.line) then pending = nil end
end

function blue.command(fields, state, phase)
    local action = actions[fields[2]]
    if not action then return end
    last_report = nil
    local host, fresh = transport.scoped_host(state, phase)
    local guid = fields[4]
    if #fields ~= 4 or not fresh or not host or host.name ~= fields[3] or not transport.valid_guid(guid) then
        error("Blue Cube operations require the exact active host pawn and item GUID", 0)
    end
    if (action == "seed" or action == "use-probe") and not fixtures then
        error("This Blue Cube command requires the isolated fixture host", 0)
    end

    local address = host.pawn:GetAddress()
    transport.load_class(blue_path)
    local ok, detail
    if action == "inspect" and type(SilverfishInspectNativeBlueTransport) == "function" then
        ok, detail = SilverfishInspectNativeBlueTransport(address, guid)
        ok = ok and (detail == "present" or detail == "absent")
    elseif action == "debit" and type(SilverfishCommitNativeBlueTransport) == "function" then
        ok, detail = SilverfishCommitNativeBlueTransport(address, guid)
        ok = ok and (detail == "removed" or detail == "absent")
    elseif action == "seed" and type(SilverfishSeedNativeBlueTransport) == "function" then
        ok, detail = SilverfishSeedNativeBlueTransport(address, guid)
        ok = ok and (detail == "seeded" or detail == "present")
    elseif action == "use-probe" and type(SilverfishProbeNativeBlueTransport) == "function" then
        local manager = transport.scratch_manager(state)
        ok, detail = SilverfishProbeNativeBlueTransport(address, manager:GetAddress(), guid)
        if ok then detail = "requested" end
    else
        error("The qualified native Blue Cube bridge is unavailable", 0)
    end
    if not ok then error("Blue Cube " .. action .. " failed (" .. tostring(detail) .. ")", 0) end

    last_report = snapshot(state, host)
    last_report.action, last_report.result, last_report.guid = action, detail, guid
    return "blue cube " .. action .. " " .. detail
end

function blue.authorize_travel(guid, state, phase)
    local host, fresh = transport.scoped_host(state, phase)
    if not host or not fresh or not transport.valid_guid(guid) or not same_host(last_report, state, host)
        or last_report.action ~= "debit" or last_report.guid ~= guid
        or (last_report.result ~= "removed" and last_report.result ~= "absent") then
        return false
    end
    last_report = nil
    return true
end

function blue.rows(state, phase, status_row)
    if not last_report then return {} end
    local host = transport.scoped_host(state, phase)
    if not host or not same_host(last_report, state, host) then return {} end
    return { status_row("native-blue", last_report.action, last_report.result, last_report.map,
        last_report.pawn, last_report.guid) }
end

return blue
