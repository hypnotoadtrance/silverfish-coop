local broker_input = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_BROKER_INPUT") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local map = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
local world_name = "World " .. map .. ".Shop_BlackMarket_01"
local station_path = map .. ".Shop_BlackMarket_01:PersistentLevel.ItemRequest_TradeStation_BP_C_1"
local station_name = "ItemRequest_TradeStation_BP_C " .. station_path
local station_class = "/Game/Classes/Actors/ItemRequest_TradeStation_BP.ItemRequest_TradeStation_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local events, heartbeat, is_downed, binding, observation

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class)
    return valid(object) and same(object:GetClass(), StaticFindObject(class))
end

local function current_context(state, phase)
    if not enabled or not events or phase ~= "connected" or state.map ~= map or not state.listening
        or state.server ~= true or not valid(state.world) or state.world:GetFullName() ~= world_name then return end
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_class) or not exact(controller, controller_class)
        or not same(controller, detail.controller_object) or pawn:HasAuthority() or not pawn:IsLocallyControlled()
        or not controller:IsLocalController() or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed()
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world)
        or type(detail.name) ~= "string" or #detail.name > 512 or detail.name:find("%c")
        or detail.name ~= pawn:GetFullName() then return end
    local station = StaticFindObject(station_path)
    if not exact(station, station_class) or station:GetFullName() ~= station_name or station:IsActorBeingDestroyed()
        or station.Buyer ~= 1 or not same(station:GetWorld(), state.world)
        or not exact(station.Box, "/Script/Engine.BoxComponent") or not same(station.Box:GetOwner(), station) then return end
    return { world = state.world, pawn = pawn, controller = controller, name = detail.name, station = station }
end

local function same_context(first, second)
    return first and second and first.name == second.name and same(first.world, second.world)
        and same(first.pawn, second.pawn) and same(first.controller, second.controller)
        and same(first.station, second.station)
end

local function fresh()
    local file = io.open(heartbeat, "rb")
    if not file then return false end
    local raw = file:read(32)
    file:close()
    local stamp = raw and raw:match("^(%d+)%s*$")
    local timestamp = stamp and #stamp <= 12 and tonumber(stamp)
    local now = os.time()
    return timestamp and timestamp <= now and now <= timestamp + 2 or false
end

local function distance_squared(a, b)
    if not a or not b then return math.huge end
    local squared = (a.X - b.X) ^ 2 + (a.Y - b.Y) ^ 2 + (a.Z - b.Z) ^ 2
    return squared == squared and squared or math.huge
end

local function in_reach(current)
    local pawn, station = current.pawn, current.station
    local range = pawn.InteractionRange
    if type(range) ~= "number" or range ~= range or range <= 0 or range > 1000 then return false end
    local here = pawn:K2_GetActorLocation()
    return distance_squared(here, station:K2_GetActorLocation()) <= range ^ 2
        and distance_squared(here, station.Box:K2_GetComponentLocation()) <= range ^ 2
end

local function accepting(current)
    local stack = current.controller.MenuStack
    return current.controller.Loading == false and stack and stack:GetArrayNum() == 0
        and current.pawn.Dead == false and type(is_downed) == "function" and is_downed(current.pawn) == false
        and fresh() and in_reach(current)
end

function broker_input.clear()
    if binding and type(SilverfishClearNativeBrokerInputScope) == "function" then SilverfishClearNativeBrokerInputScope() end
    binding, observation = nil, nil
end

local function drain(current, label)
    local sequence = SilverfishTakeNativeBrokerInputPress(current.pawn:GetAddress())
    if type(sequence) ~= "number" or not math.tointeger(sequence) or sequence < 1 or sequence >= 2 ^ 53
        or not accepting(current) then return false end
    local file = io.open(events, "ab")
    if not file then return false end
    local written = file:write(table.concat({"native-broker-input", sequence, map, current.name, station_name}, "\t"), "\n")
    local closed = file:close()
    observation = { context = current, status = written and closed and label or "write_failed", sequence = sequence }
    return written and closed and true or false
end

function broker_input.sync(state, phase)
    local current = current_context(state, phase)
    if not current or type(SilverfishSetNativeBrokerInputScope) ~= "function"
        or type(SilverfishTakeNativeBrokerInputPress) ~= "function" then
        broker_input.clear()
        return
    end
    if not same_context(current, binding) then broker_input.clear() end
    local allow = accepting(current) == true
    if SilverfishSetNativeBrokerInputScope(current.pawn:GetAddress(), allow) ~= true then
        binding = nil
        observation = {context = current, status = "scope_rejected", sequence = 0}
        return
    end
    binding = current
    if not observation or observation.sequence == 0 then
        observation = {context = current, status = allow and "armed" or "waiting", sequence = 0}
    end
    if allow then drain(current, "queued") end
end

function broker_input.command(fields, state, phase)
    if fields[2] ~= "native-broker-input-fixture" then return end
    local current = current_context(state, phase)
    if not headless or #fields ~= 3 or not current or not same_context(current, binding)
        or fields[3] ~= current.name or not accepting(current)
        or type(SilverfishProbeNativeInteractInput) ~= "function" then
        error("The stock Broker input fixture requires the exact standing guest in range", 0)
    end
    local collision = current.station.Box:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then error("The stock Broker station Box is not interactable", 0) end
    local pawn, controller = current.pawn, current.controller
    local before = pawn.InteractionTarget_Component
    local cursor = controller.bShowMouseCursor
    pawn.InteractionTarget_Component = current.station.Box
    local called, ok, status = pcall(SilverfishProbeNativeInteractInput, pawn:GetAddress(), true)
    pawn.InteractionTarget_Component = valid(before) and before or nil
    if not called or ok ~= true or status ~= "stock_started_consumed" or controller.MenuStack:GetArrayNum() ~= 0
        or controller.bShowMouseCursor ~= cursor or not drain(current, "fixture_queued") then
        error("The stock Broker input did not publish one isolated press (" .. tostring(status or ok) .. ")", 0)
    end
    return "stock Broker interaction forwarded"
end

function broker_input.rows(state, phase, status_row)
    local current = current_context(state, phase)
    if not observation or not same_context(current, observation.context) then return {} end
    return { status_row("native-broker-input", map, current.name, station_name, observation.status, observation.sequence) }
end

function broker_input.start(bridge, downed)
    if not enabled or not bridge or bridge == "" then return end
    events, heartbeat = bridge .. "/native-broker-input-events", bridge .. "/native-broker-input-heartbeat"
    is_downed = downed
end

return broker_input
