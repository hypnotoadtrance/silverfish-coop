local nothing = {}
local transport = require("native_transport")

local map = "/Game/Classes/Maps/Dreams/Nothing_P"
local world_name = "World " .. map .. ".Nothing_P"
local actor_name = "KillVolume_BP_C_0"
local volume_name = "KillVolume_BP_C " .. map .. ".Nothing_P:PersistentLevel." .. actor_name
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_TRANSPORT") == "1"
local current

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function active(state, phase)
    return enabled and state.map == map and (phase == "hosting" or phase == "connected") and valid(state.world)
        and state.world:GetFullName() == world_name
end

local function placed(state)
    local match
    for _, candidate in ipairs(FindAllOf("KillVolume_BP_C") or {}) do
        if valid(candidate) and candidate:GetFullName() == volume_name and same(candidate:GetWorld(), state.world) then
            if match then return end
            match = candidate
        end
    end
    return match
end

function nothing.sync(state, phase)
    if not active(state, phase) then
        if current and type(SilverfishClearNativeNothingScope) == "function" then SilverfishClearNativeNothingScope() end
        current = nil
        return
    end

    if not current or not same(state.world, current.world) then
        if type(SilverfishClearNativeNothingScope) == "function" then SilverfishClearNativeNothingScope() end
        current = { world = state.world }
    end
    local local_detail = state.local_pawn
    local pawn = local_detail and local_detail.pawn
    if not valid(pawn) or not same(pawn:GetWorld(), state.world) or pawn:GetFullName() ~= local_detail.name
        or type(SilverfishSetNativeNothingScope) ~= "function" then return end
    if not valid(current.volume) then current.volume = placed(state) end
    if not valid(current.volume) then return end

    local host = phase == "hosting"
    local details, fresh = transport.scoped_host(state, phase)
    local accepting = host and fresh == true and details ~= nil and same(details.pawn, pawn)
    local armed = SilverfishSetNativeNothingScope(pawn:GetAddress(), current.volume:GetAddress(), host, accepting)
    if armed and host and type(SilverfishTakeNativeNothingReturn) == "function" then
        local ok, selected_pawn, selected_volume = SilverfishTakeNativeNothingReturn()
        if ok and selected_pawn == pawn:GetAddress() and selected_volume == current.volume:GetAddress() then
            current.intent = { pawn = pawn:GetFullName(), volume = current.volume:GetFullName() }
        end
    end
end

local function status(state, phase)
    if not active(state, phase) or not current or not same(current.world, state.world)
        or type(SilverfishReadNativeNothingStatus) ~= "function" then
        return false, false, false, "nothing_guard_unavailable"
    end
    return SilverfishReadNativeNothingStatus()
end

function nothing.guard(state, phase)
    local installed, armed, fresh, detail = status(state, phase)
    return installed == true and armed == true and fresh == true, detail, actor_name
end

function nothing.rows(state, phase, status_row)
    if not active(state, phase) then return {} end
    local installed, armed, fresh, detail = status(state, phase)
    local intent = phase == "hosting" and current and current.intent
    return { status_row("native-nothing-return", map, installed and 1 or 0, armed and 1 or 0, fresh and 1 or 0,
        detail, intent and intent.pawn or "-", intent and intent.volume or "-") }
end

return nothing
