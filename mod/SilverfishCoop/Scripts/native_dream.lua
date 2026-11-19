local dream = {}
local bed = require("native_bed")
local transport = require("native_transport")
local routes = require("zone_maps")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_TRANSPORT") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local exchange = "/Game/Classes/Maps/Maps_Dungeons/Exchange_01_P"
local exchange_world = exchange .. ".Exchange_01_P"
local exchange_level = exchange_world .. ":PersistentLevel."
local countdown_name = "Dream_CountDown_BP_C_1"
local gameplay_statics = "/Script/Engine.Default__GameplayStatics"
local tarot_flags = { "Tarot_Obelisk", "Tarot_Night", "Tarot_BlackStag", "Tarot_Rook" }
local current

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) <= 100000000
end

local function exact_world(world, map)
    local package_name = map:match("/([^/]+)$")
    return package_name and valid(world) and world:GetFullName() == "World " .. map .. "." .. package_name
end

local function host_context(state, phase, map, require_fresh)
    local host, fresh = transport.scoped_host(state, phase)
    if not enabled or not host or (require_fresh and not fresh) or state.map ~= map
        or not exact_world(state.world, map) or not host.pawn:GetFullName():match("^Player_FP_C ") then
        error("An authoritative native Stardust host is required in the qualified map", 0)
    end

    local statics = StaticFindObject(gameplay_statics)
    local instance = valid(statics) and statics:GetGameInstance(host.pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not valid(instance) or not instance:GetFullName():match("^GameInstance_FP_C ")
        or not same(host.pawn.Save, save) or not save:GetFullName():match("^SaveGame_BP_C ") then
        error("The Stardust route requires the canonical native host save", 0)
    end
    return host, instance
end

local function exchange_actor(world, class, name, only)
    local expected = class .. " " .. exchange_level .. name
    local match
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if valid(actor) and same(actor:GetWorld(), world) then
            if actor:GetFullName() == expected then
                if match then error("The qualified stock Exchange actor is ambiguous", 0) end
                match = actor
            elseif only then
                error("The native Exchange world contains an unqualified stock countdown", 0)
            end
        end
    end
    if not match then error("The qualified stock Exchange actor is unavailable: " .. name, 0) end
    return match
end

local function inspect(world)
    if not valid(current.actor) then
        current.actor = exchange_actor(world, "Dream_CountDown_BP_C", countdown_name, true)
    end
    if type(SilverfishInspectNativeExchangeCountdown) ~= "function"
        or type(SilverfishTakeNativeExchangeReturn) ~= "function" then
        error("The installed native Exchange countdown guard is unavailable", 0)
    end
    return SilverfishInspectNativeExchangeCountdown(current.actor:GetAddress())
end

function dream.sync(state, phase)
    if not enabled or (phase ~= "hosting" and phase ~= "connected") or state.map ~= exchange
        or not exact_world(state.world, exchange) then
        current = nil
        return
    end

    if not current or not same(current.world, state.world) then
        current = { world = state.world, early_requested = false }
    end
    local ok, ready, detail = pcall(inspect, state.world)
    current.status = ok and ready == true and "ready" or "blocked"
    current.detail = ok and (type(detail) == "string" and detail or "") or tostring(ready)
    if current.status == "ready" and phase == "hosting" then
        local took, requested = pcall(SilverfishTakeNativeExchangeReturn)
        if took then
            current.early_requested = current.early_requested or requested == true
        else
            current.status, current.detail = "blocked", tostring(requested)
        end
    end
end

function dream.rows(status_row)
    if not enabled then return {} end
    if not current then return { status_row("native-dream-isolation", "inactive", "", "", "", 0) } end
    local actor = valid(current.actor) and countdown_name or ""
    return { status_row("native-dream-isolation", current.status, exchange, actor, current.detail,
        current.early_requested and 1 or 0) }
end

function dream.source(state, phase)
    local _, instance = host_context(state, phase, crossroads, true)
    if type(SilverfishInspectNativeExchangeCountdown) ~= "function"
        or type(SilverfishTakeNativeExchangeReturn) ~= "function" then
        error("The native Exchange countdown guard must be installed before using Stardust", 0)
    end

    local active_tarot
    for _, flag in ipairs(tarot_flags) do
        local out = {}
        instance:Check_SaveFlag(FName(flag), out, out)
        if type(out.Found) ~= "boolean" or type(out.Value) ~= "number" or not math.tointeger(out.Value)
            or out.Value < -2147483648 or out.Value > 2147483647 then
            error("The host's stock Stardust Tarot flag could not be verified: " .. flag, 0)
        end
        if out.Found and out.Value >= 1 then active_tarot = active_tarot or flag end
    end
    if active_tarot then error("The host has an active Tarot route outside the qualified Exchange lane: " .. active_tarot, 0) end

    return { source = crossroads, destination = exchange, arrival = routes[exchange].player_start,
        visit_seconds = 90, startup_seconds = 3, fade_seconds = 3, return_plan = bed.source(state, phase) }
end

function dream.arrival(state, phase)
    local host = host_context(state, phase, exchange, false)
    dream.sync(state, phase)
    if not current or current.status ~= "ready" then error("The stock Exchange countdown guard is not ready", 0) end

    local name = routes[exchange].player_start
    local marker = exchange_actor(state.world, "PlayerStart", name, false)
    local target, rotation = marker:K2_GetActorLocation(), marker:K2_GetActorRotation()
    if not finite(target.X) or not finite(target.Y) or not finite(target.Z)
        or not finite(rotation.Pitch) or not finite(rotation.Yaw) or not finite(rotation.Roll) then
        error("The stock Exchange PlayerStart transform is invalid", 0)
    end
    local pawn, controller = host.pawn, host.controller_object
    local aim = { Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll }
    if not pawn:K2_TeleportTo({ X = target.X, Y = target.Y, Z = target.Z }, aim) then
        error("The stock Exchange PlayerStart has no safe host placement", 0)
    end
    controller:SetControlRotation(aim)
    pawn:ForceNetUpdate()
    local location = pawn:K2_GetActorLocation()
    if not same(pawn:GetWorld(), state.world) or not same(controller.Pawn, pawn)
        or not finite(location.X) or not finite(location.Y) or not finite(location.Z) then
        error("The stock Exchange arrival position could not be read back", 0)
    end
    return { destination = exchange, actor = name, x = location.X, y = location.Y, z = location.Z }
end

function dream.power_fixture(state, phase)
    if not fixtures then error("The stock Exchange power test requires the staged fixtures", 0) end
    local host = host_context(state, phase, exchange, true)
    dream.sync(state, phase)
    if not current or current.status ~= "ready" or current.early_requested then
        error("The stock Exchange power test requires an armed visit without a pending return", 0)
    end
    current.actor:Send_PowerSignal(true, host.pawn)
    dream.sync(state, phase)
    if not current or current.status ~= "ready" or not current.early_requested then
        error("The native guard did not collect the staged stock power signal", 0)
    end
    return "stock Exchange countdown power signal routed to the co-op host"
end

return dream
