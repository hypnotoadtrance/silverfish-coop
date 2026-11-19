local cubes = {}
local transport = require("native_transport")

local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local class_suffixes = { blue = "Blue", red = "Red", green = "Green", purple = "Purple" }
local entries = { action_use = true, quick_move = true, use_held_item = true }
local accepted_results = {
    inspect = { present = true, absent = true },
    debit = { removed = true, absent = true },
    seed = { seeded = true, present = true },
}

local function snapshot(state, host)
    return { map = state.map, pawn = host.name, address = host.pawn:GetAddress(), world = state.world:GetAddress() }
end

local function same_host(record, state, host)
    return record and record.map == state.map and record.pawn == host.name
        and record.address == host.pawn:GetAddress() and record.world == state.world:GetAddress()
end

local function append_event(path, line)
    local file = path and io.open(path, "ab")
    if not file then return false end
    local written = file:write(line)
    local closed = file:close()
    return written and closed
end

function cubes.new(name)
    local suffix = class_suffixes[name]
    if not suffix then error("An implemented co-op cube is required", 0) end

    local cube = {}
    local label = suffix .. " Cube"
    local prefix = "native-" .. name
    local event_path = bridge and bridge .. "/" .. prefix .. "-events"
    local event_name = name .. "-request"
    local class_name = "Art_Tesseract_" .. suffix
    local class_path = "/Game/Classes/Items/Artifacts/" .. class_name .. "." .. class_name .. "_C"
    local take_name = "SilverfishTakeNative" .. suffix .. "TransportRequest"
    local actions = {
        [prefix .. "-inspect"] = "inspect",
        [prefix .. "-debit"] = "debit",
        [prefix .. "-seed"] = "seed",
        [prefix .. "-use-probe"] = "use-probe",
    }
    local operations = {
        inspect = "SilverfishInspectNative" .. suffix .. "Transport",
        debit = "SilverfishCommitNative" .. suffix .. "Transport",
        seed = "SilverfishSeedNative" .. suffix .. "Transport",
        ["use-probe"] = "SilverfishProbeNative" .. suffix .. "Transport",
    }
    local sequence = 0
    local pending
    local last_report

    function cube.sync(state, phase)
        local take = _G[take_name]
        if type(take) ~= "function" then return end
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
            elseif append_event(event_path, pending.line) then
                pending = nil
            else
                return
            end
        end

        local observed, guid, entry = take(host.pawn:GetAddress())
        if not observed or not fresh or not transport.valid_guid(guid) or not entries[entry] then return end
        sequence = sequence + 1
        pending = snapshot(state, host)
        pending.expires = os.time() + 2
        pending.line = table.concat({ event_name, tostring(sequence), state.map, host.name, guid, entry,
            string.format("%.17g", host.x), string.format("%.17g", host.y), string.format("%.17g", host.z) }, "\t") .. "\n"
        if append_event(event_path, pending.line) then pending = nil end
    end

    function cube.command(fields, state, phase)
        local action = actions[fields[2]]
        if not action then return end
        last_report = nil
        local host, fresh = transport.scoped_host(state, phase)
        local guid = fields[4]
        if #fields ~= 4 or not fresh or not host or host.name ~= fields[3] or not transport.valid_guid(guid) then
            error(label .. " operations require the exact active host pawn and item GUID", 0)
        end
        if (action == "seed" or action == "use-probe") and not fixtures then
            error("This " .. label .. " command requires the isolated fixture host", 0)
        end

        transport.load_class(class_path)
        local invoke = _G[operations[action]]
        if type(invoke) ~= "function" then error("The qualified native " .. label .. " bridge is unavailable", 0) end

        local address = host.pawn:GetAddress()
        local ok, detail
        if action == "use-probe" then
            local manager = transport.scratch_manager(state)
            ok, detail = invoke(address, manager:GetAddress(), guid)
            if ok then detail = "requested" end
        else
            ok, detail = invoke(address, guid)
            ok = ok and accepted_results[action][detail]
        end
        if not ok then error(label .. " " .. action .. " failed (" .. tostring(detail) .. ")", 0) end

        last_report = snapshot(state, host)
        last_report.action, last_report.result, last_report.guid = action, detail, guid
        return name .. " cube " .. action .. " " .. detail
    end

    function cube.authorize_travel(guid, state, phase)
        local host, fresh = transport.scoped_host(state, phase)
        if not host or not fresh or not transport.valid_guid(guid) or not same_host(last_report, state, host)
            or last_report.action ~= "debit" or last_report.guid ~= guid or not accepted_results.debit[last_report.result] then
            return false
        end
        last_report = nil
        return true
    end

    function cube.rows(state, phase, status_row)
        if not last_report then return {} end
        local host = transport.scoped_host(state, phase)
        if not host or not same_host(last_report, state, host) then return {} end
        return { status_row(prefix, last_report.action, last_report.result, last_report.map,
            last_report.pawn, last_report.guid) }
    end

    return cube
end

return cubes
