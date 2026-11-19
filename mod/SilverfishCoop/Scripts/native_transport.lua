local transport = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_TRANSPORT") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local event_path = bridge and bridge .. "/native-transport-events"
local heartbeat_path = bridge and bridge .. "/native-zone-heartbeat"
local scoped = false
local sequence = 0
local pending_gold
local last_gold
local artifacts = {
    stardust = "Art_DreamDust",
    green = "Art_Tesseract_Green",
    blue = "Art_Tesseract_Blue",
    red = "Art_Tesseract_Red",
    gold = "Art_Tesseract_Gold",
}
local manager_path = "/Game/Classes/GUI/New/Components/ItemManager.ItemManager_C"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local gold_path = "/Game/Classes/Items/Artifacts/Art_Tesseract_Gold.Art_Tesseract_Gold_C"
local gold_source = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local gold_entries = { action_use = true, quick_move = true, use_held_item = true }
local gold_actions = {
    ["native-gold-inspect"] = "inspect",
    ["native-gold-debit"] = "debit",
    ["native-gold-seed"] = "seed",
    ["native-gold-use-probe"] = "use-probe",
}

local function valid(value)
    return value ~= nil and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact_guid(value)
    return type(value) == "string" and #value == 32 and not value:find("[^0-9A-F]") and value:find("[^0]") ~= nil
end

local function finite_position(value)
    return type(value) == "number" and value == value and math.abs(value) <= 100000000
end

local function safe_field(value)
    return type(value) == "string" and #value > 0 and #value <= 512 and not value:find("[%c]")
end

local function heartbeat_fresh()
    if not heartbeat_path then return false end
    local file = io.open(heartbeat_path, "rb")
    if not file then return false end
    local text = file:read(32)
    file:close()
    local timestamp = text and text:match("^(%d+)%s*$")
    if not timestamp or #timestamp > 12 then return false end
    local age = os.time() - tonumber(timestamp)
    return age >= 0 and age <= 2
end

local function host_snapshot(state, phase)
    local details = state.local_pawn
    local pawn = details and details.pawn
    local controller = details and details.controller_object
    if phase ~= "hosting" or not state.listening or state.server or not valid(pawn) or not pawn:HasAuthority()
        or not valid(controller) or not controller:IsLocalController() or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world)
        or not safe_field(state.map) or not state.map:match("^/Game/Classes/Maps/[%w_/]+$")
        or not safe_field(details.name) or pawn:GetFullName() ~= details.name
        or not finite_position(details.x) or not finite_position(details.y) or not finite_position(details.z) then
        return
    end
    return details
end

local function load_class(path)
    local class = StaticFindObject(path)
    if not valid(class) then
        LoadAsset(path)
        class = StaticFindObject(path)
    end
    if not valid(class) then error("The staged transport class could not be loaded", 0) end
    return class
end

local function emit_pending_gold()
    local file = event_path and io.open(event_path, "ab")
    if not file then return false end
    local written = file:write(pending_gold.line)
    local closed = file:close()
    return written and closed
end

local function collect_gold(state, phase)
    if type(SilverfishTakeNativeGoldTransportRequest) ~= "function" then return end
    local host = host_snapshot(state, phase)
    if not host then
        pending_gold = nil
        return
    end
    local fresh = heartbeat_fresh()
    if pending_gold then
        if not fresh or pending_gold.map ~= state.map or pending_gold.pawn ~= host.name
            or os.time() > pending_gold.expires then
            pending_gold = nil
        elseif emit_pending_gold() then
            pending_gold = nil
        else
            return
        end
    end

    local observed, guid, entry = SilverfishTakeNativeGoldTransportRequest(host.pawn:GetAddress())
    if not observed or not fresh or not exact_guid(guid) or not gold_entries[entry] then return end
    sequence = sequence + 1
    pending_gold = {
        map = state.map, pawn = host.name, expires = os.time() + 2,
        line = table.concat({ "gold-request", tostring(sequence), state.map, host.name, guid, entry,
            string.format("%.17g", host.x), string.format("%.17g", host.y), string.format("%.17g", host.z) }, "\t") .. "\n",
    }
    if emit_pending_gold() then pending_gold = nil end
end

function transport.sync(state, phase)
    if not enabled or type(SilverfishSetNativeTransportScope) ~= "function" then return end
    local local_player = state.local_pawn and state.local_pawn.pawn
    local host = phase == "hosting"
    if (host or phase == "connected") and local_player and local_player:IsValid() then
        scoped = SilverfishSetNativeTransportScope(local_player:GetAddress(), host) == true
        if scoped and host then collect_gold(state, phase) end
    elseif scoped then
        SilverfishClearNativeTransportScope()
        scoped = false
    end
    if not scoped or not host then pending_gold = nil end
end

function transport.rows(state, phase, status_row)
    if not enabled then return {} end
    if type(SilverfishReadNativeTransportStatus) ~= "function" then
        return {status_row("native-transport", "arming", 0, "", "", "", "")}
    end
    local ready, denied, role, item, entry, reason = SilverfishReadNativeTransportStatus()
    local rows = {status_row("native-transport", ready and "ready" or "arming", denied or 0, role or "", item or "",
        entry or "", reason or "")}
    local host = state.local_pawn
    if last_gold and phase == "hosting" and state.map == last_gold.map and host and host.name == last_gold.pawn then
        rows[#rows + 1] = status_row("native-gold", last_gold.action, last_gold.result, last_gold.map,
            last_gold.pawn, last_gold.guid)
    end
    return rows
end

local function scratch_manager(state)
    local widgets = StaticFindObject(widgets_path)
    local details = state.local_pawn
    local pawn = details and details.pawn
    local controller = details and details.controller_object
    if not valid(widgets) or not valid(pawn) or not valid(controller) or not controller:IsLocalController() then
        error("The connected local player is unavailable", 0)
    end
    -- The scratch widget stays offscreen. Its item carries no real stack or GUID.
    local manager = widgets:Create(pawn, load_class(manager_path), controller)
    if not valid(manager) then error("Could not create the isolated stock item manager", 0) end
    manager.Player_FP = pawn
    return manager
end

local function gold_command(fields, state, phase, action)
    last_gold = nil
    local host = host_snapshot(state, phase)
    local guid = fields[4]
    if #fields ~= 4 or not enabled or not scoped or not heartbeat_fresh()
        or not host or host.name ~= fields[3] or not exact_guid(guid) then
        error("Golden Cube operations require the exact active host pawn and item GUID", 0)
    end
    if (action == "seed" or action == "use-probe") and not fixtures then
        error("This Golden Cube command requires the isolated fixture host", 0)
    end

    local address = host.pawn:GetAddress()
    load_class(gold_path)
    local ok, detail
    if action == "inspect" and type(SilverfishInspectNativeGoldTransport) == "function" then
        ok, detail = SilverfishInspectNativeGoldTransport(address, guid)
        ok = ok and (detail == "present" or detail == "absent")
    elseif action == "debit" and type(SilverfishCommitNativeGoldTransport) == "function" then
        ok, detail = SilverfishCommitNativeGoldTransport(address, guid)
        ok = ok and (detail == "removed" or detail == "absent")
    elseif action == "seed" and type(SilverfishSeedNativeGoldTransport) == "function" then
        ok, detail = SilverfishSeedNativeGoldTransport(address, guid)
        ok = ok and (detail == "seeded" or detail == "present")
    elseif action == "use-probe" and type(SilverfishProbeNativeGoldTransport) == "function" then
        local manager = scratch_manager(state)
        ok, detail = SilverfishProbeNativeGoldTransport(address, manager:GetAddress(), guid)
        if ok then detail = "requested" end
    else
        error("The qualified native Golden Cube bridge is unavailable", 0)
    end
    if not ok then error("Golden Cube " .. action .. " failed (" .. tostring(detail) .. ")", 0) end
    last_gold = { action = action, result = detail, map = state.map, pawn = host.name, guid = guid }
    return "golden cube " .. action .. " " .. detail
end

function transport.command(fields, state, phase)
    local action = gold_actions[fields[2]]
    if action then return gold_command(fields, state, phase, action) end
    if fields[2] ~= "native-transport-probe" then return end
    local stem = artifacts[fields[3]]
    if #fields ~= 3 or not enabled or not scoped or not stem
        or type(SilverfishProbeNativeTransportHeldItem) ~= "function" then
        error("An isolated native transport guard is required", 0)
    end
    load_class("/Game/Classes/Items/Artifacts/" .. stem .. "." .. stem .. "_C")
    local manager = scratch_manager(state)
    local _, previous = SilverfishReadNativeTransportStatus()
    local verified, detail = SilverfishProbeNativeTransportHeldItem(manager:GetAddress(), fields[3])
    if not verified then error("Stock transport use probe failed (" .. tostring(detail) .. ")", 0) end
    local ready, count, role, item, entry = SilverfishReadNativeTransportStatus()
    if not ready or count ~= previous + 1 or role ~= (phase == "hosting" and "host" or "guest")
        or item ~= fields[3] or entry ~= "use_held_item" then
        error("The staged transport guard did not confirm the exact local use", 0)
    end
    return "stock transport use stopped before consumption"
end

function transport.authorize_gold_travel(guid, state, phase)
    local host = host_snapshot(state, phase)
    if not enabled or not scoped or not heartbeat_fresh() or not host or state.map ~= gold_source
        or not exact_guid(guid) or not last_gold or last_gold.action ~= "debit" or last_gold.guid ~= guid
        or last_gold.map ~= state.map or last_gold.pawn ~= host.name then
        return false
    end
    last_gold = nil
    return true
end

return transport
