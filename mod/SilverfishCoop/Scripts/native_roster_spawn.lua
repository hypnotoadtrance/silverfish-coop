local spawn = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_EVENTS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_DEATH") == "cpp"
local operation = "native-roster-restart-player"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local game_mode_class = "/Game/Classes/Player/GameMode_FP.GameMode_FP_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local game_instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local connection_class = "/Script/Engine.NetConnection"
local player_state_class = "/Script/Engine.PlayerState"
local capsule_class = "/Script/Engine.CapsuleComponent"
local restart_function = "/Script/Engine.GameModeBase:RestartPlayerAtTransform"
local trace_color = {R = 0, G = 0, B = 0, A = 0}
local max_spawn_distance, max_spawn_adjustment = 1200, 65
local offsets = {{120, 0}, {-120, 0}, {0, 120}, {0, -120},
    {110, 110}, {-110, 110}, {110, -110}, {-110, -110},
    {185, 0}, {-185, 0}, {0, 185}, {0, -185}}
local fallback_rings = {{240, 16}, {360, 16}, {510, 20}, {690, 24}, {870, 24}, {1060, 28}, {1130, 28}}
-- Stagger adjacent rings while keeping the original, closest approaches first.
for ring_index, ring in ipairs(fallback_rings) do
    local radius, samples = ring[1], ring[2]
    local stagger = ring_index % 2 == 0 and 0.5 or 0
    for index = 0, samples - 1 do
        local angle = 2 * math.pi * (index + stagger) / samples
        offsets[#offsets + 1] = {radius * math.cos(angle), radius * math.sin(angle)}
    end
end
local last

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class)
    return valid(object) and not object:GetFullName():find("Default__", 1, true)
        and same(object:GetClass(), class)
end

local function ruled_out(reason)
    error("stock_roster_spawn_" .. reason, 0)
end

local function stock_name(name, prefix)
    return type(name) == "string" and #name <= 512 and name:sub(1, #prefix) == prefix
        and not name:find("%c") and not name:find("Default__", 1, true)
end

local function stable_actor(actor, class, world)
    return exact(actor, class) and actor:HasAuthority() and not actor:IsActorBeingDestroyed()
        and same(actor:GetWorld(), world)
end

local function matching_pawn(pawn, controller, world, class, local_player)
    return stable_actor(pawn, class, world)
        and pawn:IsLocallyControlled() == local_player and same(pawn.Controller, controller)
        and same(controller.Pawn, pawn)
end

local function host_context(state, phase)
    local world = state.world
    if not enabled or phase ~= "hosting" or state.listening ~= true or state.server ~= false
        or not valid(world) or world:GetFullName():match("^World (/[^%.:]+)%.") ~= state.map then return end
    local pawn_type, controller_type = StaticFindObject(pawn_class), StaticFindObject(controller_class)
    local host, controller = state.local_pawn, state.controller
    if not host or not same(controller, host.controller_object)
        or not stable_actor(controller, controller_type, world) or not controller:IsLocalController()
        or not matching_pawn(host.pawn, controller, world, pawn_type, true)
        or host.pawn.Dead ~= false or host.name ~= host.pawn:GetFullName() then return end

    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local mode = valid(statics) and statics:GetGameMode(host.pawn)
    local instance = valid(statics) and statics:GetGameInstance(host.pawn)
    if not stable_actor(mode, StaticFindObject(game_mode_class), world)
        or not same(mode.DefaultPawnClass, pawn_type) or not same(mode.PlayerControllerClass, controller_type)
        or mode.bStartPlayersAsSpectators ~= false
        or not exact(instance, StaticFindObject(game_instance_class)) then return end
    local restart = mode.RestartPlayerAtTransform
    if not valid(restart) or restart:GetFullName() ~= "Function " .. restart_function then return end
    local save = instance.SaveSlot_Ref
    if not exact(save, StaticFindObject(save_class)) or not same(host.pawn.Save, save) then return end

    local driver = world.NetDriver
    if not valid(driver) or not same(driver.World, world) or valid(driver.ServerConnection) then return end
    return {world = world, driver = driver, host = host.pawn, controller = controller, mode = mode,
        instance = instance, save = save, pawn_type = pawn_type, controller_type = controller_type}
end

local function remote_controller(connection, context)
    if not valid(connection) or not same(connection.Driver, context.driver) then return end
    local controller = connection.PlayerController
    if not stable_actor(controller, context.controller_type, context.world)
        or controller:IsLocalController() or not same(controller.Player, connection)
        or not same(connection.OwningActor, controller)
        or (valid(controller.NetConnection) and not same(controller.NetConnection, connection)) then return end
    return controller
end

local function connection_member(context, connection, name)
    if not valid(connection) or connection:GetFullName() ~= name then return false end
    local entries = context.driver.ClientConnections
    local count = entries and math.tointeger(entries:GetArrayNum())
    if not count or count < 1 or count > 4 then return false end
    local matches = 0
    for index = 1, count do
        if same(entries[index], connection) then matches = matches + 1 end
    end
    return matches == 1
end

local function exact_join(state, context, connection_name, controller_name)
    local connections = context.driver.ClientConnections
    local count = connections and math.tointeger(connections:GetArrayNum())
    local type_class = StaticFindObject(connection_class)
    if not count or count < 1 or count > 4 or count ~= state.clients or not valid(type_class) then
        ruled_out("connection_census_unavailable")
    end
    local joined_connection, joining_controller
    local occupied, names, controllers = {context.host}, {}, {}
    for index = 1, count do
        local connection = connections[index]
        if not valid(connection) or not connection:IsA(type_class) or not same(connection.Driver, context.driver) then
            ruled_out("connection_identity_unqualified")
        end
        local name = connection:GetFullName()
        local controller = remote_controller(connection, context)
        if names[name] or not controller or controllers[controller:GetAddress()] then
            ruled_out("connection_identity_unqualified")
        end
        names[name], controllers[controller:GetAddress()] = true, true
        if name == connection_name and controller:GetFullName() == controller_name then
            local player_state = controller.PlayerState
            if valid(controller.Pawn) or not exact(player_state, StaticFindObject(player_state_class))
                or player_state.bOnlySpectator ~= false then ruled_out("controller_not_waiting_for_pawn") end
            joined_connection, joining_controller = connection, controller
        else
            local pawn = controller.Pawn
            if not matching_pawn(pawn, controller, context.world, context.pawn_type, false) then
                ruled_out("another_connection_waiting_for_pawn")
            end
            occupied[#occupied + 1] = pawn
        end
    end
    if not joined_connection then ruled_out("joining_identity_unavailable") end
    return joined_connection, joining_controller, occupied
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) <= 100000000
end

local function vector(point)
    return point and finite(point.X) and finite(point.Y) and finite(point.Z)
end

local function distance_squared(first, second)
    return (first.X - second.X) ^ 2 + (first.Y - second.Y) ^ 2 + (first.Z - second.Z) ^ 2
end

local function safe_transform(context, occupied)
    local host = context.host
    local origin, aim = host:K2_GetActorLocation(), host:K2_GetActorRotation()
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local cdo = context.pawn_type:GetCDO()
    local capsule = valid(cdo) and cdo.CapsuleComponent
    local native_capsule_class = StaticFindObject(capsule_class)
    local radius = valid(capsule) and same(capsule:GetClass(), native_capsule_class)
        and capsule:GetUnscaledCapsuleRadius()
    local height = valid(capsule) and capsule:GetUnscaledCapsuleHalfHeight()
    if not vector(origin) or not aim or not finite(aim.Yaw) or not finite(radius) or radius <= 0 or radius > 65
        or not finite(height) or height < radius or height > 150 or not valid(trace) then
        ruled_out("stock_spawn_geometry_unavailable")
    end

    local occupancy = {}
    for _, pawn in ipairs(occupied) do
        local center, body = pawn:K2_GetActorLocation(), pawn.CapsuleComponent
        if not vector(center) or not exact(body, native_capsule_class) or not same(body:GetOwner(), pawn) then
            ruled_out("pawn_clearance_unavailable")
        end
        local width, half_height = body:GetScaledCapsuleRadius(), body:GetScaledCapsuleHalfHeight()
        if not finite(width) or width <= 0 or width > 100 or not finite(half_height)
            or half_height < width or half_height > 200 then ruled_out("pawn_clearance_unavailable") end
        occupancy[#occupancy + 1] = {center = center, radius = width, height = half_height}
    end

    local function free_from_pawns(position)
        for _, entry in ipairs(occupancy) do
            local dx, dy, dz = position.X - entry.center.X, position.Y - entry.center.Y,
                position.Z - entry.center.Z
            if math.abs(dz) < height + entry.height and dx * dx + dy * dy < (radius + entry.radius + 16) ^ 2 then
                return false
            end
        end
        return true
    end

    for _, offset in ipairs(offsets) do
        local x, y = origin.X + offset[1], origin.Y + offset[2]
        local from, to = {X = x, Y = y, Z = origin.Z + 100}, {X = x, Y = y, Z = origin.Z - 250}
        local hit = {}
        local floor = trace:LineTraceSingle(host, from, to, 0, false, occupied, 0, hit, true, trace_color, trace_color, 0)
        hit = hit.OutHit or hit
        if floor == true and vector(hit.ImpactPoint) and vector(hit.ImpactNormal)
            and hit.ImpactNormal.Z >= 0.7 and hit.ImpactPoint.Z < from.Z - 1 and hit.ImpactPoint.Z >= to.Z then
            local point = {X = x, Y = y, Z = hit.ImpactPoint.Z + height + 4}
            -- Keep room for Unreal's stock adjustment so its final pawn stays near the host.
            if distance_squared(point, origin) <= (max_spawn_distance - max_spawn_adjustment) ^ 2
                and free_from_pawns(point) then
                local above = {X = x, Y = y, Z = point.Z + 2}
                local obstruction = trace:CapsuleTraceSingle(host, point, above, radius, height, 0, false,
                    occupied, 0, {}, true, trace_color, trace_color, 0)
                if obstruction == false then
                    local yaw = math.rad(aim.Yaw) / 2
                    return {Translation = point, Rotation = {X = 0, Y = 0, Z = math.sin(yaw), W = math.cos(yaw)},
                        Scale3D = {X = 1, Y = 1, Z = 1}}, origin
                end
            end
        end
    end
    ruled_out("safe_floor_unavailable")
end

function spawn.command(fields, state, phase)
    if fields[2] ~= operation then return end
    last = nil
    local connection_name, controller_name = fields[3], fields[4]
    if #fields ~= 4 or not stock_name(connection_name, "IpConnection ")
        or not stock_name(controller_name, "PlayerControler_FP_C ") then ruled_out("exact_joining_identity_required") end
    local context = host_context(state, phase)
    if not context then ruled_out("stock_listen_host_unavailable") end
    local connection, controller, occupied = exact_join(state, context, connection_name, controller_name)
    local transform, origin = safe_transform(context, occupied)

    -- Let the stock GameMode spawn and possess its default pawn for this controller.
    local invoked = pcall(function() context.mode:RestartPlayerAtTransform(controller, transform) end)
    local pawn = controller.Pawn
    local after = host_context(state, phase)
    if not invoked or not after or not same(context.mode, after.mode) or not same(context.world, after.world)
        or not same(context.driver, after.driver) or not same(context.host, after.host)
        or not same(context.save, after.save) or not same(context.instance, after.instance)
        or not connection_member(after, connection, connection_name)
        or not same(remote_controller(connection, after), controller) then
        error("mutation_unverified_stock_roster_spawn_scope", 0)
    end
    if not valid(pawn) then ruled_out("stock_pawn_not_spawned") end
    local point = pawn:K2_GetActorLocation()
    if not matching_pawn(pawn, controller, context.world, context.pawn_type, false)
        or not stock_name(pawn:GetFullName(), "Player_FP_C ") or pawn.Dead ~= false or not vector(point)
        or distance_squared(point, transform.Translation) > max_spawn_adjustment ^ 2
        or distance_squared(point, origin) > max_spawn_distance ^ 2 then
        error("mutation_unverified_stock_roster_spawn_pawn", 0)
    end

    last = {map = state.map, world = context.world, connection = connection, controller = controller,
        connection_name = connection_name, controller_name = controller_name, pawn = pawn, point = point}
    return "exact stock remote player restarted"
end

function spawn.rows(state, phase, status_row)
    local checked, context = pcall(host_context, state, phase)
    if not checked or not context then
        last = nil
        return {}
    end
    local rows = {status_row("native-roster-spawn-capability", 1)}
    if last and same(last.world, context.world) and connection_member(context, last.connection, last.connection_name)
        and same(remote_controller(last.connection, context), last.controller)
        and matching_pawn(last.pawn, last.controller, context.world, context.pawn_type, false) then
        rows[#rows + 1] = status_row("native-roster-spawn", "restarted", last.map, last.connection_name,
            last.controller_name, last.pawn:GetFullName(), last.point.X, last.point.Y, last.point.Z)
    else
        last = nil
    end
    return rows
end

return spawn
