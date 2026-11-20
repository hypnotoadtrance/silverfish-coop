local zone = {}
local wall = require("zone_wall")
local routes = require("zone_maps")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local expanded = os.getenv("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES") == "1"
local fixture_mode = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local expanded_fixtures = expanded and fixture_mode and require("authored_wall_routes") or {}
local nexus_portals = {
    Level_Exit_BP_C_178 = "Lantern_Portal_BP_C_1",
    Level_Exit_BP_C_179 = "Lantern_Portal_BP_C_2",
    Level_Exit_BP_C_180 = "Lantern_Portal_BP_C_9",
    Level_Exit_BP_C_181 = "Lantern_Portal_BP_C_7",
    Level_Exit_BP_C_182 = "Lantern_Portal_BP_C_3",
    Level_Exit_BP_C_183 = "Lantern_Portal_BP_C_10",
    Level_Exit_BP_C_184 = "Lantern_Portal_BP_C_8",
}
local event_path
local expanded_event_path
local heartbeat_path
local armed
local fixture
local secondary_fixture
local sequence = 0
local requests = 0
local guest_denied = 0
local too_far = 0
local decision = ""
local door_decision = ""
local door_detail
local door_denied = 0
local door_requests = 0
local status = "inactive"
local failure = ""

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function placed_actor(class, name, world)
    if not name or not valid(world) then return end
    local match
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if valid(actor) and actor:GetFullName():match(":PersistentLevel%.([^%.]+)$") == name
            and same(actor:GetWorld(), world) then
            if match then return end
            match = actor
        end
    end
    return match
end

local function find_fixture(local_pawn, route)
    local world = local_pawn:GetWorld()
    if valid(fixture) and same(fixture:GetWorld(), world) then return fixture end
    fixture = placed_actor("Level_Exit_BP_C", route.fixture, world)
    return fixture
end

local function heartbeat_fresh()
    local file = io.open(heartbeat_path, "rb")
    if not file then return false end
    local text = file:read(32)
    file:close()
    local timestamp = text and text:match("^(%d+)%s*$")
    if not timestamp or #timestamp > 12 then return false end
    local age = os.time() - tonumber(timestamp)
    return age >= 0 and age <= 2
end

local function clear()
    wall.clear()
    if armed and type(SilverfishClearNativeZoneScope) == "function" then
        pcall(SilverfishClearNativeZoneScope)
    end
    armed = nil
    fixture = nil
    secondary_fixture = nil
end

local function wait_for_scope(detail)
    if armed and type(SilverfishPauseNativeZoneScope) == "function" then
        SilverfishPauseNativeZoneScope()
    end
    status, failure = "arming", detail
end

local function emit(map, route)
    if not route or not route.destination then return false end
    local next_sequence = sequence + 1
    local file = io.open(event_path, "ab")
    if not file then return false end
    local tag = route.entrance and "\t" .. route.entrance or ""
    local written = file:write("request\t", tostring(next_sequence), "\t", map, "\t", route.destination, tag, "\n")
    local closed = file:close()
    if not written or not closed then return false end
    sequence = next_sequence
    requests = requests + 1
    return true
end

local function handle_event(map, hosting)
    local value = SilverfishTakeNativeZoneEvent()
    if value == nil then return true end
    decision = value
    if value == "guest_denied" then
        guest_denied = guest_denied + 1
    elseif value == "too_far" then
        too_far = too_far + 1
    elseif value == "request" or value == "door_request" or value == "secondary_request" then
        local route = routes[map]
        if value == "door_request" then route = route.door end
        if value == "secondary_request" then
            route = route.secondary
            decision = "request"
        end
        if not hosting or not heartbeat_fresh() or not emit(map, route) then
            SilverfishReleaseNativeZoneRequest()
            decision = "request_unavailable"
            return false
        end
    end
    return true
end

local function handle_door_event()
    if type(SilverfishTakeNativeDoorEvent) ~= "function" then return end
    local value, detail = SilverfishTakeNativeDoorEvent()
    if not value then return end
    door_decision = value
    door_detail = detail
    if value == "guest_denied" then door_denied = door_denied + 1 end
    if value == "travel_requested" then door_requests = door_requests + 1 end
end

local function handle_expanded_event(map, host)
    if not expanded or type(SilverfishTakeNativeAuthoredRoute) ~= "function" then return true end
    local source, destination, tag, actor, kind, travel_time = SilverfishTakeNativeAuthoredRoute()
    if not source then return true end
    if host and source == map and heartbeat_fresh() and destination and tag and actor
        and (kind == "overlap" or kind == "door") then
        local next_sequence = sequence + 1
        local file = io.open(expanded_event_path, "ab")
        if file then
            local written = file:write(table.concat({ "authored-request", tostring(next_sequence), source,
                destination, tag, actor, kind, travel_time and tostring(travel_time) or "-" }, "\t"), "\n")
            local closed = file:close()
            if written and closed then
                sequence = next_sequence
                requests = requests + 1
                decision = "request"
                return true
            end
        end
    end
    SilverfishReleaseNativeZoneRequest()
    decision = "request_unavailable"
    return false
end

function zone.start(bridge)
    if not enabled then return end
    event_path = bridge .. "/native-zone-events"
    expanded_event_path = bridge .. "/native-authored-exit-events"
    heartbeat_path = bridge .. "/native-zone-heartbeat"
    wall.start(bridge)
end

function zone.sync(state, phase)
    if not enabled or not event_path then return end
    local host = phase == "hosting"
    local route = routes[state.map]
    if not route or route.expanded and not expanded or (not host and phase ~= "connected") then
        clear()
        status, failure = "inactive", ""
        return
    end

    if type(SilverfishSetNativeZoneScope) ~= "function" or type(SilverfishTakeNativeZoneEvent) ~= "function" then
        wall.sync(state, phase, fixture, false, secondary_fixture)
        wait_for_scope("Waiting for native zone hooks")
        return
    end
    local local_pawn = state.local_pawn and state.local_pawn.pawn
    local remote = {}
    if host then
        for _, details in ipairs(state.guests) do
            if valid(details.pawn) then remote[#remote + 1] = details.pawn:GetAddress() end
        end
    end
    if not valid(local_pawn) or #remote > 4 or (host and state.clients ~= #remote) then
        wall.sync(state, phase, fixture, false, secondary_fixture)
        wait_for_scope("Waiting for the native player roster")
        return
    end
    local function qualify(exit_address, accepting)
        local address = local_pawn:GetAddress()
        if type(SilverfishSetNativeZoneRoster) == "function" then
            return SilverfishSetNativeZoneRoster(address, exit_address, host, accepting, table.unpack(remote))
        end
        return #remote <= 1 and SilverfishSetNativeZoneScope(address, remote[1] or 0, exit_address, host, accepting)
    end
    local exit = find_fixture(local_pawn, route)
    if route.fixture and not valid(exit) then
        wall.sync(state, phase, nil, false, secondary_fixture)
        if qualify(0, false) then armed = state.map end
        wait_for_scope("Waiting for the placed " .. route.fixture)
        return
    end

    local accepting = host and heartbeat_fresh()
    local world = local_pawn:GetWorld()
    if not route.secondary then
        secondary_fixture = nil
    elseif not valid(secondary_fixture) or not same(secondary_fixture:GetWorld(), world) then
        secondary_fixture = placed_actor("Level_Exit_BP_C", route.secondary.fixture, world)
    end
    local refreshed = qualify(valid(exit) and exit:GetAddress() or 0, accepting)
    wall.sync(state, phase, exit, accepting and refreshed, secondary_fixture)
    if not refreshed then
        wait_for_scope("Native zone hooks declined the scope")
        return
    end
    armed = state.map
    handle_door_event()
    if not handle_event(state.map, host) or not handle_expanded_event(state.map, host) then
        status, failure = "error", "The native zone request could not be written"
        return
    end
    status, failure = "ready", ""
end

local function player_start(route, world)
    local marker = placed_actor("PlayerStart", route.player_start, world)
    if valid(marker) and marker:GetFullName():match("^PlayerStart ") then return marker end
end

local function arrival_arrow(world, tag, name, door_name)
    local match
    for _, marker in ipairs(FindAllOf("LevelExit_Spawn_BP_C") or {}) do
        if valid(marker) and same(marker:GetWorld(), world) then
            local marker_tag = marker.EntranceTag
            if marker_tag and marker_tag:ToString() == tag then
                if match then error("The destination contains multiple matching arrival tags", 0) end
                match = marker
            end
        end
    end
    local qualified = valid(match) and name and match:GetFullName():match(":PersistentLevel%.([^%.]+)$") == name
    if door_name then
        local door = placed_actor("Dungeon_TransitionDoor_BP_C", door_name, world)
        local component = valid(door) and door.LevelExit_Spawn_BP
        qualified = valid(component) and same(component.ChildActor, match)
        name = door_name .. ".LevelExit_Spawn_BP"
    end
    if not qualified or not valid(match.Arrow) then
        error("The authored arrival marker is unavailable", 0)
    end
    return match.Arrow, name
end

function zone.position_arrival(state, phase, tag)
    local route = routes[state.map]
    local door_name = route and route.door_arrivals and route.door_arrivals[tag]
    local use_player_start = route and route.player_start and route.player_start == tag
    local name = route and (route.entrance == tag and route.arrival or route.other_arrivals and route.other_arrivals[tag])
    if not enabled or phase ~= "hosting" or not route or (not door_name and not use_player_start and not name) then
        error("An authored host arrival for the current zone is required", 0)
    end
    local details = state.local_pawn
    local pawn = details and details.pawn
    local controller = details and details.controller_object
    if not valid(pawn) or not pawn:HasAuthority() or not valid(controller) or not controller:IsLocalController()
        or not same(controller.Pawn, pawn) then
        error("The local authoritative arrival pawn is unavailable", 0)
    end

    local world = pawn:GetWorld()
    if not same(world, state.world) or not same(controller:GetWorld(), world) then
        error("The authored arrival is in a different world", 0)
    end
    local target, rotation
    if use_player_start then
        local marker = player_start(route, world)
        if not valid(marker) then error("The qualified stock player start is unavailable", 0) end
        name = route.player_start
        target, rotation = marker:K2_GetActorLocation(), marker:K2_GetActorRotation()
    else
        local arrow
        arrow, name = arrival_arrow(world, tag, name, door_name)
        target, rotation = arrow:K2_GetComponentLocation(), arrow:K2_GetComponentRotation()
    end
    if not pawn:K2_TeleportTo({ X = target.X, Y = target.Y, Z = target.Z },
        { Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll }) then
        error("The authored arrival marker has no safe pawn placement", 0)
    end
    controller:SetControlRotation({ Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll })
    pawn:ForceNetUpdate()
    return "native arrival " .. tag .. " at " .. name
end

local function probe_player(state, selector)
    local match
    for _, details in ipairs(state.pawns) do
        if ((details.slot or details.kind) == selector or details.name == selector)
            and valid(details.pawn) and details.pawn:HasAuthority() and same(details.pawn:GetWorld(), state.world) then
            if match then error("The requested authoritative pawn is ambiguous", 0) end
            match = details
        end
    end
    if not match then error("The requested authoritative pawn is unavailable", 0) end
    return match
end

local function expanded_overlap(map, tag, actor)
    if not expanded or not fixture_mode or not headless or not tag then return end
    local selected
    for _, route in ipairs(expanded_fixtures[map] or {}) do
        if route.kind == "overlap" and route.entrance == tag and (not actor or route.actor == actor) then
            if selected then error("The staged entrance selects multiple authored overlap exits", 0) end
            selected = route
        end
    end
    return selected
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < 1e8
end

local function vector(point)
    return point and finite(point.X) and finite(point.Y) and finite(point.Z)
end

local function side_of(point, center, axis)
    return (point.X - center.X) * axis.X + (point.Y - center.Y) * axis.Y + (point.Z - center.Z) * axis.Z
end

local function approach_anchor(state, tag, center, extent)
    local anchor
    for _, marker in ipairs(FindAllOf("LevelExit_Spawn_BP_C") or {}) do
        if valid(marker) and same(marker:GetWorld(), state.world) and marker.EntranceTag
            and marker.EntranceTag:ToString() == tag and valid(marker.Arrow) and same(marker.Arrow:GetOwner(), marker) then
            if anchor then error("The authored exit has multiple approach references", 0) end
            anchor = marker.Arrow:K2_GetComponentLocation()
        end
    end
    local host = state.local_pawn and state.local_pawn.pawn
    if not vector(anchor) and valid(host) then anchor = host:K2_GetActorLocation() end
    local radius = math.max(extent.X, extent.Y) + 2500
    if vector(anchor) and (anchor.X - center.X)^2 + (anchor.Y - center.Y)^2 <= radius^2 then return anchor end
    return center
end

local function nexus_fixture_floor(world, world_name)
    local actor = StaticFindObject(world_name .. ":PersistentLevel.StaticMeshActor_0")
    local component = valid(actor) and actor.StaticMeshComponent
    local mesh = StaticFindObject("/Game/Assets/StaticMeshes/Mesh_Environment/SM_Env_Ground_Mound_Large_01.SM_Env_Ground_Mound_Large_01")
    local trace = StaticFindObject("/Script/Engine.PrimitiveComponent:K2_LineTraceComponent")
    if not valid(actor) or not same(actor:GetClass(), StaticFindObject("/Script/Engine.StaticMeshActor"))
        or not same(actor:GetWorld(), world) or not actor:HasAuthority() or actor:IsActorBeingDestroyed()
        or not actor:GetActorEnableCollision() or not valid(component) or not same(component, actor.RootComponent)
        or not same(component:GetOwner(), actor) or component:GetFullName() ~= "StaticMeshComponent "
            .. world_name .. ":PersistentLevel.StaticMeshActor_0.StaticMeshComponent0"
        or not same(component:GetClass(), StaticFindObject("/Script/Engine.StaticMeshComponent"))
        or not same(component.StaticMesh, mesh) or not valid(trace) then
        error("The exact stock Gold Nexus fixture floor is unavailable", 0)
    end
    local collision = component:GetCollisionEnabled()
    if (collision ~= 1 and collision ~= 3) or component:GetCollisionObjectType() ~= 0
        or component:GetCollisionResponseToChannel(2) ~= 2 then
        error("The stock Gold Nexus fixture floor does not block player capsules", 0)
    end
    return component
end

local function nexus_crypt_pocket(world, world_name, exit_name, center, normal, sign)
    local portal_name = nexus_portals[exit_name]
    if not portal_name then error("The selected Gold Nexus pad has no qualified stock crypt", 0) end
    local label = "Gold Nexus " .. portal_name .. " crypt"
    local path = world_name .. ":PersistentLevel." .. portal_name
    local portal = placed_actor("Lantern_Portal_BP_C", portal_name, world)
    if not valid(portal) then error(label .. " actor missing in the current world", 0) end
    if portal:GetFullName() ~= "Lantern_Portal_BP_C " .. path then
        error(label .. " actor path changed: " .. portal:GetFullName(), 0)
    end
    local class = portal:GetClass()
    if not valid(class) or class:GetFullName() ~= "BlueprintGeneratedClass /Game/Classes/Actors/Lantern_Portal_BP.Lantern_Portal_BP_C" then
        error(label .. " actor class changed: " .. (valid(class) and class:GetFullName() or "missing"), 0)
    end
    if not portal:HasAuthority() or portal:IsActorBeingDestroyed() then
        error(label .. " lacks current stock actor authority", 0)
    end

    local root = portal.RootComponent
    if not valid(root) or root:GetFullName() ~= "SceneComponent " .. path .. ".DefaultSceneRoot"
        or not same(root:GetOwner(), portal) then
        error(label .. " stock root changed: " .. (valid(root) and root:GetFullName() or "missing"), 0)
    end
    local component_class = StaticFindObject("/Script/Engine.StaticMeshComponent")
    if not valid(component_class) then error(label .. " static mesh component class missing", 0) end
    local components = portal:K2_GetComponentsByClass(component_class)
    local representation = type(components)
    if representation == "userdata" and type(components.type) == "function" then representation = components:type() end
    -- UE4SS UFunction array returns are one-based Lua tables; direct TArray userdata also implements length.
    local count = (representation == "table" or representation == "TArray") and #components or nil
    if not finite(count) or count < 1 or count > 24 then
        error(string.format("%s component census unavailable (type %s, count %s)",
            label, representation, tostring(count)), 0)
    end
    local crypt
    local names = {}
    for index = 1, count do
        local component = components[index]
        if type(component) == "userdata" and type(component.type) == "function" and component:type() == "RemoteUnrealParam" then
            component = component:get()
        end
        if valid(component) then
            local name = component:GetFullName()
            names[#names + 1] = name:match("[^.]+$") or name
            if name == "StaticMeshComponent " .. path .. ".SM_Env_Entrance_Crypt_01" then
                if crypt then error(label .. " entrance mesh is ambiguous", 0) end
                crypt = component
            end
        end
    end
    if not valid(crypt) then
        error(label .. " entrance mesh missing from stock census: " .. table.concat(names, ", "), 0)
    end
    if not same(crypt:GetClass(), component_class) then error(label .. " entrance mesh class changed", 0) end
    if not same(crypt:GetOwner(), portal) then error(label .. " entrance mesh owner changed", 0) end
    if not same(crypt:GetAttachParent(), root) then error(label .. " entrance mesh root changed", 0) end
    local collision = crypt:GetCollisionEnabled()
    if (collision ~= 1 and collision ~= 3) or crypt:GetCollisionResponseToChannel(2) ~= 2 then
        error(label .. " stock entrance mesh does not block player capsules", 0)
    end
    local mesh = crypt.StaticMesh
    if not valid(mesh) or mesh:GetFullName() ~= "StaticMesh /Game/Assets/StaticMeshes/Mesh_BuildingSets/Tunnels_Dungeons/SM_Env_Entrance_Crypt_01.SM_Env_Entrance_Crypt_01" then
        error(label .. " entrance asset changed: " .. (valid(mesh) and mesh:GetFullName() or "missing"), 0)
    end
    local origin, right = crypt:K2_GetComponentLocation(), crypt:GetRightVector()
    if not vector(origin) or not vector(right) then
        error(label .. " stock component geometry is unavailable", 0)
    end
    local offset = math.sqrt((origin.X - center.X)^2 + (origin.Y - center.Y)^2)
    local facing = (right.X * normal.X + right.Y * normal.Y) * sign
    if offset > 3 or facing < .99 then
        error(string.format("%s geometry changed (offset %.2f, facing %.3f)", label, offset, facing), 0)
    end
end

local alternate_lanes = {-400, 400, -750, 750, -1100, 1100, -1500, 1500}

local function place_expanded_overlap(state, details, route, position, alternate)
    local pawn = details.pawn
    local exit = placed_actor("Level_Exit_BP_C", route.actor, state.world)
    local exit_class = StaticFindObject("/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C")
    local box_class = StaticFindObject("/Script/Engine.BoxComponent")
    local world_name = state.map .. "." .. state.map:match("[^/]+$")
    local box = valid(exit) and exit.Box
    local tag = valid(exit) and exit["Entrance Tag"]
    if type(tag) == "userdata" then tag = tag:ToString() end
    if state.server ~= false or state.listening ~= true or state.world:GetFullName() ~= "World " .. world_name
        or not valid(exit) or not same(exit:GetClass(), exit_class) or not exit:HasAuthority() or exit:IsActorBeingDestroyed()
        or exit:GetFullName() ~= "Level_Exit_BP_C " .. world_name .. ":PersistentLevel." .. route.actor
        or tag ~= route.entrance or not valid(box) or not same(box:GetClass(), box_class) or not same(box:GetOwner(), exit)
        or not exit:GetActorEnableCollision() or not box:GetGenerateOverlapEvents() then
        error("The exact expanded stock overlap or its collision box is unavailable", 0)
    end
    local collision = box:GetCollisionEnabled()
    if (collision ~= 1 and collision ~= 3) or box:GetCollisionResponseToChannel(2) ~= 1 then
        error("The authored exit box does not overlap player capsules", 0)
    end
    local center, extent = box:K2_GetComponentLocation(), box:GetScaledBoxExtent()
    local first, second, up = box:GetForwardVector(), box:GetRightVector(), box:GetUpVector()
    local original, facing = pawn:K2_GetActorLocation(), pawn:K2_GetActorRotation()
    local capsule = pawn.CapsuleComponent
    local height = valid(capsule) and same(capsule:GetOwner(), pawn) and capsule:GetScaledCapsuleHalfHeight()
    local radius = valid(capsule) and capsule:GetScaledCapsuleRadius()
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not vector(center) or not vector(extent) or not vector(first) or not vector(second) or not vector(up)
        or not vector(original) or not facing or not finite(facing.Pitch) or not finite(facing.Yaw) or not finite(facing.Roll)
        or not finite(height) or height < 20 or height > 150 or not finite(radius) or radius < 10 or radius > 100
        or not valid(trace) then error("The expanded exit fixture could not read the stock box or pawn geometry", 0) end
    if math.min(extent.X, extent.Y, extent.Z) < 1 or math.max(extent.X, extent.Y, extent.Z) > 50000
        or math.abs(up.Z) < .7 then
        error(string.format("The stock exit box is outside the staging bounds (half extents %.1f, %.1f, %.1f, up %.3f)",
            extent.X, extent.Y, extent.Z, up.Z), 0)
    end
    local normal, tangent, width, length = first, second, extent.X, extent.Y
    if extent.Y < extent.X then normal, tangent, width, length = second, first, extent.Y, extent.X end
    local anchor = approach_anchor(state, route.entrance, center, extent)
    local floor
    if state.map == "/Game/Classes/Maps/Dreams/GoldNexus_P" then
        floor = nexus_fixture_floor(state.world, world_name)
        anchor = floor:K2_GetComponentLocation()
        if not vector(anchor) or math.abs(side_of(anchor, center, normal)) < 600 then
            error("The stock Gold Nexus pad does not face its authored floor", 0)
        end
    end
    local sign = side_of(anchor, center, normal) < 0 and -1 or 1
    local lane_limit = math.max(0, length - radius - 35)
    local lane = math.max(-lane_limit, math.min(lane_limit, side_of(anchor, center, tangent)))
    local inside = position == "inside"
    local depth = inside and math.max(0, width - radius - 45) or width + radius + (position == "far" and 2250 or 130)
    local index = details.kind == "guest" and (tonumber((details.slot or ""):match("^guest(%d+)$")) or 1) or 0
    local assigned = ({[0] = 0, 180, -180, 360, -360})[index] or 0
    local top = math.max(anchor.Z, center.Z - math.max(0, extent.Z - height)) + 325
    local bottom = math.min(anchor.Z, center.Z - extent.Z) - 400
    local ignored, color = {exit}, {R = 0, G = 0, B = 0, A = 0}
    for _, detail in ipairs(state.pawns) do
        if valid(detail.pawn) then ignored[#ignored + 1] = detail.pawn end
    end
    local candidates = {}
    local nexus_pocket = floor and position == "near"
    local function add_point(distance, offset)
        local side = math.max(-lane_limit, math.min(lane_limit, lane + offset))
        candidates[#candidates + 1] = {center.X + distance * sign * normal.X + side * tangent.X,
            center.Y + distance * sign * normal.Y + side * tangent.Y}
    end
    if floor and position == "far" then
        local marker = player_start(routes[state.map], state.world)
        local start = valid(marker) and marker:K2_GetActorLocation()
        if vector(start) and marker:GetFullName() == "PlayerStart " .. world_name .. ":PersistentLevel.PlayerStart_1"
            and (start.X - center.X)^2 + (start.Y - center.Y)^2 > 1700^2 then
            candidates[1] = {start.X + assigned, start.Y}
        end
        -- The cooked mound extends beyond the court wall. Use the outward side of the selected pad.
        for _, offset in ipairs({assigned, 0, 125, -125}) do add_point(-depth, offset) end
    elseif nexus_pocket then
        -- The narrow inward opening and the rear stone both block a capsule. The pocket lies inside the crypt.
        nexus_crypt_pocket(state.world, world_name, route.actor, center, normal, sign)
        local lane_edge = math.min(lane_limit, 55, 120 - radius)
        local side = (index % 2 == 0 and -1 or 1) * lane_edge
        for _, clearance in ipairs({30, 45, 20}) do
            local distance = width + radius + clearance
            -- The rear crypt stone begins around 229 cm out. Keep extra clearance for the optional portal cube.
            if distance + radius < 160 then
                for _, offset in ipairs({side, side * .75}) do add_point(-distance, offset) end
            end
        end
    elseif floor then
        for _, offset in ipairs({assigned, 0, 125, -125}) do add_point(0, offset) end
    elseif alternate then
        add_point(depth, alternate_lanes[alternate] + assigned)
    else
        for _, offset in ipairs({assigned, assigned + 125, assigned - 125, 0, 250, -250}) do add_point(depth, offset) end
    end
    local moved, hits, qualified = false, 0, 0
    for _, candidate in ipairs(candidates) do
        local x, y = candidate[1], candidate[2]
        local above, below = {X = x, Y = y, Z = top}, {X = x, Y = y, Z = bottom}
        local hit = {}
        local found
        if floor then
            -- The authored BodySetup uses complex static collision for the walkable surface.
            found = floor:K2_LineTraceComponent(above, below, true, false, false, hit, hit, hit, hit)
        else
            found = trace:LineTraceSingle(pawn, above, below, 0, false, ignored, 0, hit, true, color, color, 0)
        end
        hit = hit.OutHit or hit
        local surface = hit.Component and hit.Component:Get()
        if found == true and vector(hit.ImpactPoint) and vector(hit.ImpactNormal) and hit.ImpactNormal.Z >= .7
            and hit.ImpactPoint.Z < top - 1 and hit.ImpactPoint.Z >= bottom and valid(surface)
            and (not floor or same(surface, floor)) and surface:GetCollisionObjectType() == 0 then
            hits = hits + 1
            local point = {X = x, Y = y, Z = hit.ImpactPoint.Z + height + 3}
            local in_box = math.abs(side_of(point, center, first)) <= extent.X
                and math.abs(side_of(point, center, second)) <= extent.Y and math.abs(side_of(point, center, up)) <= extent.Z
            if in_box == inside then
                qualified = qualified + 1
                if pawn:K2_TeleportTo(point, facing) then
                    moved = true
                    local actual = pawn:K2_GetActorLocation()
                    local tolerance = nexus_pocket and 10 or 70
                    if vector(actual) and (actual.X - x)^2 + (actual.Y - y)^2 + (actual.Z - point.Z)^2 <= tolerance^2
                        and box:IsOverlappingActor(pawn) == inside then
                        pawn:ForceNetUpdate()
                        return "positioned authoritative pawn " .. position .. " the stock " .. route.actor
                    end
                end
            end
        end
    end
    if moved then
        if not pawn:K2_TeleportTo(original, facing) then error("The expanded exit fixture could not restore the pawn", 0) end
        local actual = pawn:K2_GetActorLocation()
        if not vector(actual) or (actual.X - original.X)^2 + (actual.Y - original.Y)^2 + (actual.Z - original.Z)^2 > 70^2 then
            error("The expanded exit fixture could not verify the restored pawn", 0)
        end
        pawn:ForceNetUpdate()
    end
    if floor then
        error(string.format("No verified stock Gold Nexus floor at %s %s (%d probes, %d floor hits, %d qualified points)",
            route.actor, position, #candidates, hits, qualified), 0)
    end
    error("No verified stock floor at the requested expanded overlap position", 0)
end

local covered_door_fixtures = {
    ["/Game/Classes/Maps/Maps_OverWorld/Beach_01_P"] = {
        map_name = "Beach_01_P", tag = "Beach_Leto", door = "Dungeon_TransitionDoor_BP_C_1",
        floor = "StaticMeshActor_881", face = 1, floor_offset = { 242.25, 681.25, -11.90 }, floor_z = .414,
        mesh = "/Game/Assets/StaticMeshes/Mesh_BuildingSets/Set_Bunker/SM_Bld_Bunker_03.SM_Bld_Bunker_03",
    },
    ["/Game/Classes/Maps/Maps_OverWorld/Trench_01_P"] = {
        map_name = "Trench_01_P", tag = "Annex_Trench", door = "Dungeon_TransitionDoor_BP_C_2",
        floor = "StaticMeshActor_185", face = -1, floor_offset = { -750, -600, 0 }, floor_z = .125,
        mesh = "/Game/Assets/StaticMeshes/Mesh_BuildingSets/Set_Prim/SM_Buildings_Floor_5x5_01.SM_Buildings_Floor_5x5_01",
    },
}

local function place_at_covered_door(state, pawn, door, library, ignore, color, origin, direction, rotation, height, profile)
    local level = state.map .. "." .. profile.map_name .. ":PersistentLevel."
    local floor_actor = StaticFindObject(level .. profile.floor)
    local floor = valid(floor_actor) and floor_actor.StaticMeshComponent
    local mesh = StaticFindObject(profile.mesh)
    local box, skin, camera = door.Box, door.SK_DungeonDoor_01, pawn.Camera
    if door:GetFullName() ~= "Dungeon_TransitionDoor_BP_C " .. level .. profile.door
        or math.abs(direction.X) > .01 or math.abs(direction.Y - profile.face) > .01 or math.abs(direction.Z) > .01
        or not valid(floor_actor) or not same(floor_actor:GetWorld(), state.world) or not floor_actor:HasAuthority()
        or floor_actor:IsActorBeingDestroyed() or not floor_actor:GetActorEnableCollision()
        or not same(floor_actor:GetClass(), StaticFindObject("/Script/Engine.StaticMeshActor"))
        or not same(floor, floor_actor.RootComponent) or not same(floor:GetOwner(), floor_actor)
        or floor:GetFullName() ~= "StaticMeshComponent " .. level .. profile.floor .. ".StaticMeshComponent0"
        or not same(floor:GetClass(), StaticFindObject("/Script/Engine.StaticMeshComponent"))
        or not same(floor.StaticMesh, mesh) or floor:GetCollisionObjectType() ~= 0
        or floor:GetCollisionResponseToChannel(2) ~= 2
        or not valid(box) or not same(box:GetOwner(), door) or not valid(camera) or not same(camera:GetOwner(), pawn) then
        error("The exact " .. profile.tag .. " door, floor, or pawn camera is unavailable", 0)
    end
    local base, target, initial = floor:K2_GetComponentLocation(), box:K2_GetComponentLocation(), pawn:K2_GetActorLocation()
    local interaction = pawn.InteractionRange
    local collision = floor:GetCollisionEnabled()
    if not vector(base) or not vector(target) or not vector(initial)
        or math.abs(base.X - origin.X - profile.floor_offset[1]) > 2
        or math.abs(base.Y - origin.Y - profile.floor_offset[2]) > 2
        or math.abs(base.Z - origin.Z - profile.floor_offset[3]) > 2 or (collision ~= 1 and collision ~= 3)
        or not finite(interaction) or interaction < 150 or interaction > 2000 then
        error("The exact " .. profile.tag .. " floor geometry or interaction range changed", 0)
    end

    local function trace(start, stop, channel, complex)
        local hit = {}
        if library:LineTraceSingle(pawn, start, stop, channel, complex, ignore, 0, hit, true, color, color, 0) == true then
            return hit.OutHit or hit
        end
    end
    local function distance_squared(first, second)
        return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
    end
    local function can_see_door()
        local eye = camera:K2_GetComponentLocation()
        if not vector(eye) then return false end
        local distance = math.sqrt(distance_squared(eye, target))
        if distance < 1 or distance >= interaction then return false end
        local function endpoint(length)
            local scale = length / distance
            return { X = eye.X + (target.X - eye.X) * scale, Y = eye.Y + (target.Y - eye.Y) * scale,
                Z = eye.Z + (target.Z - eye.Z) * scale }
        end
        -- Match stock LookTrace: complex Visibility trims the simple Interactable ray.
        local visibility = trace(eye, endpoint(interaction), 1, true)
        local reach = interaction
        if visibility then
            if not vector(visibility.ImpactPoint) then return false end
            reach = math.min(reach, math.sqrt(distance_squared(eye, visibility.ImpactPoint)))
        end
        local hit = reach > 1 and trace(eye, endpoint(reach), 4, false)
        local surface = hit and hit.Component and hit.Component:Get()
        return same(surface, box) or valid(skin) and same(skin:GetOwner(), door) and same(surface, skin)
    end

    local local_pawn = state.local_pawn and state.local_pawn.pawn
    local side = same(local_pawn, pawn) and 1 or -1
    local hits, reached = 0, 0
    -- The generic arrow-based ray starts above these two authored roofs.
    -- Begin below the roof on the side of the floor that faces the door.
    for _, offset in ipairs({{180, 70}, {220, 85}, {145, 90}, {245, 35}, {200, 0}}) do
        local x, y = origin.X + side * offset[2], origin.Y + profile.face * offset[1]
        local hit = trace({ X = x, Y = y, Z = origin.Z + 210 }, { X = x, Y = y, Z = origin.Z - 35 }, 0, false)
        local surface = hit and hit.Component and hit.Component:Get()
        if same(surface, floor) and vector(hit.ImpactPoint) and vector(hit.ImpactNormal) and hit.ImpactNormal.Z >= .9
            and math.abs(hit.ImpactPoint.Z - origin.Z - profile.floor_z) <= 4 then
            hits = hits + 1
            local point = { X = x, Y = y, Z = hit.ImpactPoint.Z + height + 2 }
            if distance_squared(point, origin) <= 320^2 then
                local moved = pawn:K2_TeleportTo(point, rotation)
                local actual = pawn:K2_GetActorLocation()
                if moved and vector(actual) and distance_squared(actual, point) <= 10^2
                    and distance_squared(actual, origin) <= 320^2 then
                    reached = reached + 1
                    if can_see_door() then
                        pawn:ForceNetUpdate()
                        return true
                    end
                end
                if moved or not vector(actual) or distance_squared(actual, initial) > 10^2 then
                    local restored = pawn:K2_TeleportTo(initial, rotation)
                    actual = pawn:K2_GetActorLocation()
                    if not restored or not vector(actual) or distance_squared(actual, initial) > 10^2 then
                        error("The " .. profile.tag .. " door fixture could not restore the pawn", 0)
                    end
                    pawn:ForceNetUpdate()
                end
            end
        end
    end
    error(string.format("No verified %s door approach (%d stock floor hits, %d safe positions)", profile.tag, hits, reached), 0)
end

local indoor_door_fixtures = {
    ["/Game/Classes/Maps/Maps_Dungeons/DryDock_P"] = {
        map_name = "DryDock_P", spawn = {-6800, 4850, 442},
        near = {tag = "Refine_DD", actor = "Dungeon_TransitionDoor_BP_C_0", range = 250, forward = {-1, 0}},
        far = {tag = "Docks_DD", actor = "Dungeon_TransitionDoor_BP_C_1"},
    },
    ["/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P"] = {
        map_name = "Shop_Gunsmith_P", spawn = {0, 200, 142},
        near = {tag = "South_Gunsmith", actor = "Dungeon_TransitionDoor_BP_C_1", range = 200, forward = {0, 1}},
        far = {tag = "South_Gunsmith", actor = "Dungeon_TransitionDoor_BP_C_1",
            floor = "StaticMeshActor_124", position = {-1250, 2250, -300}},
    },
}

local function place_at_named_door(state, pawn, door, direction, rotation, distance, tag)
    local marker = valid(door.LevelExit_Spawn_BP) and door.LevelExit_Spawn_BP.ChildActor
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not valid(marker) or not valid(marker.Arrow) or not same(marker:GetWorld(), state.world)
        or not same(marker:GetParentComponent(), door.LevelExit_Spawn_BP)
        or not valid(library) or not valid(pawn.CapsuleComponent) then
        error("The named door has no qualified stock staging geometry", 0)
    end
    local anchor, origin = marker.Arrow:K2_GetComponentLocation(), door:K2_GetActorLocation()
    local extents = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    if not vector(anchor) or not vector(origin) or not vector(direction)
        or not finite(extents) or extents < 20 or extents > 150 then
        error("The named door or pawn has invalid staging geometry", 0)
    end
    local color = { R = 0, G = 0, B = 0, A = 1 }
    local ignore = {}
    for _, details in ipairs(state.pawns) do
        if valid(details.pawn) then ignore[#ignore + 1] = details.pawn end
    end
    local lateral_x, lateral_y = -direction.Y, direction.X
    local far = distance > 1000
    local profile = covered_door_fixtures[state.map]
    if headless and not far and profile and tag == profile.tag then
        return place_at_covered_door(state, pawn, door, library, ignore, color, origin, direction, rotation, extents, profile)
    end
    table.insert(ignore, 1, door)
    local candidates = {}
    local indoor_top, indoor_bottom, indoor_floor
    local indoor = headless and indoor_door_fixtures[state.map]
    local approach = indoor and indoor[far and "far" or "near"]
    if approach and tag == approach.tag then
        local level = state.map .. "." .. indoor.map_name .. ":PersistentLevel."
        local start = StaticFindObject(level .. "PlayerStart_1")
        local point = valid(start) and start:K2_GetActorLocation()
        if door:GetFullName() ~= "Dungeon_TransitionDoor_BP_C " .. level .. approach.actor
            or not valid(start) or not same(start:GetWorld(), state.world)
            or not same(start:GetClass(), StaticFindObject("/Script/Engine.PlayerStart"))
            or not vector(point) or math.abs(point.X - indoor.spawn[1]) > 2
            or math.abs(point.Y - indoor.spawn[2]) > 2 or math.abs(point.Z - indoor.spawn[3]) > 2 then
            error("The exact indoor door or stock spawn changed", 0)
        end
        -- Start the floor ray inside the room at the stock spawn height.
        indoor_top, indoor_bottom = point.Z + 40, point.Z - 200
        if far then
            if approach.floor then
                local floor = StaticFindObject(level .. approach.floor)
                local component = valid(floor) and floor.StaticMeshComponent
                local base = valid(component) and component:K2_GetComponentLocation()
                if not valid(floor) or not same(floor:GetWorld(), state.world)
                    or not same(floor:GetClass(), StaticFindObject("/Script/Engine.StaticMeshActor"))
                    or not same(floor.RootComponent, component) or not same(component:GetOwner(), floor)
                    or not vector(base) or math.abs(base.X - approach.position[1]) > 2
                    or math.abs(base.Y - approach.position[2]) > 2 or math.abs(base.Z - approach.position[3]) > 2 then
                    error("The stock interior floor for the distant guest moved", 0)
                end
                point, indoor_floor = base, component
                indoor_top, indoor_bottom = base.Z + 180, base.Z - 65
            end
            local dx, dy = point.X - origin.X, point.Y - origin.Y
            local forward, across = dx * direction.X + dy * direction.Y, dx * lateral_x + dy * lateral_y
            candidates = {{forward, across}, {forward, across + 70}, {forward, across - 70},
                {forward + 70, across + 70}, {forward - 70, across + 70},
                {forward + 70, across - 70}, {forward - 70, across - 70}}
        else
            local span, face = approach.range, approach.forward
            if math.abs(point.X - origin.X - span * face[1]) > 2
                or math.abs(point.Y - origin.Y - span * face[2]) > 2
                or math.abs(point.Z - origin.Z - 142) > 2 or math.abs(direction.X - face[1]) > .01
                or math.abs(direction.Y - face[2]) > .01 or math.abs(direction.Z) > .01 then
                error("The stock spawn near the indoor door moved", 0)
            end
            local local_pawn = state.local_pawn and state.local_pawn.pawn
            local side = same(local_pawn, pawn) and 1 or -1
            candidates = {{span, side * 65}, {span - 32, side * 75}, {span, 0}}
        end
    end
    for _, length in ipairs(far and { distance, -distance } or { 170, -170, 235, -235, 0 }) do
        for _, offset in ipairs({ 0, 90, -90 }) do
            candidates[#candidates + 1] = {length, offset}
        end
    end
    if far and headless then
        -- Small shops can have walkable space across the door's axis but none along it.
        for _, radius in ipairs({distance, 1950, 2700}) do
            local diagonal = radius / math.sqrt(2)
            for _, pair in ipairs({{0, radius}, {0, -radius}, {diagonal, diagonal},
                {diagonal, -diagonal}, {-diagonal, diagonal}, {-diagonal, -diagonal}}) do
                candidates[#candidates + 1] = pair
            end
        end
    end
    local top = indoor_top or math.max(anchor.Z, origin.Z) + 185
    local bottom = indoor_bottom or math.min(anchor.Z, origin.Z) - (far and 550 or 325)
    for _, offset in ipairs(candidates) do
        local x = origin.X + offset[1] * direction.X + offset[2] * lateral_x
        local y = origin.Y + offset[1] * direction.Y + offset[2] * lateral_y
        local hit = {}
        local found = library:LineTraceSingle(pawn, { X = x, Y = y, Z = top }, { X = x, Y = y, Z = bottom },
            0, false, ignore, 0, hit, true, color, color, 0)
        local impact = hit.OutHit or hit
        local surface = impact.Component and impact.Component:Get()
        if found == true and vector(impact.ImpactPoint) and vector(impact.ImpactNormal) and impact.ImpactNormal.Z >= .7
            and impact.ImpactPoint.Z < top - 1 and impact.ImpactPoint.Z >= bottom and valid(surface)
            and surface:GetCollisionObjectType() == 0 and (not indoor_floor or same(surface, indoor_floor)) then
            local z = impact.ImpactPoint.Z + extents + 2
            local squared = (x - origin.X)^2 + (y - origin.Y)^2 + (z - origin.Z)^2
            if (far and squared > 1900^2 or not far and squared <= 320^2)
                and pawn:K2_TeleportTo({ X = x, Y = y, Z = z }, rotation) then
                pawn:ForceNetUpdate()
                return true
            end
        end
    end
    return false
end

local function door_command(fields, state, phase, command)
    local route = routes[state.map]
    local slot = fields[3]
    local positioning = command == "native-door-position"
    local basic_count = positioning and 4 or 3
    local tag = fields[basic_count + 1]
    local spec = route and route.door
    local actor = spec and spec.fixture
    if tag then
        actor = nil
        for _, configured in ipairs(expanded_fixtures[state.map] or {}) do
            if configured.entrance == tag and configured.kind == "door" then
                if actor then error("The staged entrance selects multiple doors", 0) end
                actor = configured.actor
            end
        end
        if not actor and spec and spec.entrance == tag then actor = spec.fixture end
    end
    if not enabled or not fixture_mode or phase ~= "hosting" or armed ~= state.map or status ~= "ready"
        or not actor or #fields ~= (tag and basic_count + 1 or basic_count) then
        error("A qualified authored transition door and authoritative pawn are required", 0)
    end
    local pawn = probe_player(state, slot).pawn
    local door = valid(pawn) and placed_actor("Dungeon_TransitionDoor_BP_C", actor, pawn:GetWorld())
    if not valid(door) or not valid(door.Box) then error("The authored transition door is unavailable", 0) end

    if not positioning then
        door:SendInteractionToActor(pawn, door.Box)
        handle_door_event()
        return "native door " .. door_decision
    end

    local position = fields[4]
    if position ~= "near" and position ~= "far" then
        error("Expected native-door-position local|guest near|far", 0)
    end
    local origin = door:K2_GetActorLocation()
    local direction = door:GetActorForwardVector()
    local rotation = pawn:K2_GetActorRotation()
    local distance = position == "near" and 180 or 2250
    if tag then
        if place_at_named_door(state, pawn, door, direction, rotation, distance, tag) then
            return "positioned " .. slot .. " " .. position .. " the exact authored door"
        end
        error("No verified stock floor near the named transition door", 0)
    end
    for _, sign in ipairs({-1, 1}) do
        local destination = { X = origin.X + sign * distance * direction.X,
            Y = origin.Y + sign * distance * direction.Y, Z = origin.Z + 100 }
        if pawn:K2_TeleportTo(destination, rotation) then
            pawn:ForceNetUpdate()
            return "positioned " .. slot .. " " .. position .. " the authored door"
        end
    end
    error("No safe pawn location near the authored door", 0)
end

local cistern = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local keypad_proof

local function cistern_keypad(fields, state, phase)
    keypad_proof = nil
    local key = math.tointeger(tonumber(fields[3]))
    if not enabled or not expanded or not fixture_mode or phase ~= "hosting" or state.map ~= cistern
        or armed ~= cistern or #fields ~= 3 or not key or key < 0 or key > 11 then
        error("The staged Cistern keypad requires one stock key index", 0)
    end
    local pawn = probe_player(state, "local").pawn
    local keypad = placed_actor("KeyCode_Lock_BP_C", "KeyCode_Lock_BP_C_2", state.world)
    local door = placed_actor("Dungeon_TransitionDoor_BP_C", "Dungeon_TransitionDoor_BP_C_2", state.world)
    local targets = valid(keypad) and keypad.ActorsToUnlock
    local keys = valid(keypad) and keypad.Keys
    local component = keys and keys:GetArrayNum() == 12 and keys[key + 1]
    if not valid(door) or not valid(keypad) or not keypad:HasAuthority() or not targets or targets:GetArrayNum() ~= 1
        or not same(targets[1], door) or not valid(component) or not same(component:GetOwner(), keypad) then
        error("The stock keypad is not linked to the exact Cistern door", 0)
    end
    local from, at = pawn:K2_GetActorLocation(), component:K2_GetComponentLocation()
    local distance = math.sqrt((from.X - at.X)^2 + (from.Y - at.Y)^2 + (from.Z - at.Z)^2)
    if distance ~= distance or distance > pawn.InteractionRange then
        error("The exact stock keypad is outside the host interaction range", 0)
    end
    keypad:SendInteractionToActor(pawn, component)
    keypad_proof = { key = key, locked = door.Locked, unlocked = keypad.Unlocked,
        map = state.map, pawn = pawn:GetFullName(), target = door:GetFullName() }
    return "exact stock Cistern keypad action verified"
end

function zone.command(fields, state, phase)
    local name = fields[2]
    if name == "native-exit-wall-probe" then return wall.command(fields, state, phase) end
    if name == "native-cistern-keypad" then return cistern_keypad(fields, state, phase) end
    if name == "native-door-position" or name == "native-door-probe" then
        return door_command(fields, state, phase, name)
    end
    if name == "native-exit-arrival" then
        if #fields ~= 3 then error("Expected native-exit-arrival <tag>", 0) end
        return zone.position_arrival(state, phase, fields[3])
    end
    if name == "native-exit-release" then
        if #fields < 2 or #fields > 3 then error("Expected native-exit-release with an optional source map", 0) end
        if not enabled or phase ~= "hosting" or not routes[state.map] or armed ~= state.map
            or (fields[3] and fields[3] ~= state.map)
            or type(SilverfishReleaseNativeZoneRequest) ~= "function" then
            error("The source exit scope is unavailable", 0)
        end
        -- A paused current scope can still hold the rejected stock request.
        SilverfishReleaseNativeZoneRequest()
        return "native exit request released"
    end
    if name ~= "native-exit-position" then return end
    if not enabled or phase ~= "hosting" or not routes[state.map] or armed ~= state.map or status ~= "ready" then
        error("An active staged native zone host is required", 0)
    end
    local slot, position, entrance, actor = fields[3], fields[4], fields[5], fields[6]
    if #fields < 4 or #fields > 7 or (position ~= "near" and position ~= "inside" and position ~= "far") then
        error("Expected native-exit-position with a pawn and near, inside, or far", 0)
    end
    local route = routes[state.map]
    if actor and (#actor > 80 or not actor:match("^Level_Exit_BP_C_%d+$") or not entrance or entrance == "") then
        error("Choose an exact stock overlap actor with its entrance tag", 0)
    end
    local alternate = tonumber(fields[7])
    if fields[7] and (not fixture_mode or not headless or not alternate_lanes[alternate] or not actor or position ~= "near"
        or state.map == "/Game/Classes/Maps/Dreams/GoldNexus_P") then
        error("An alternate lane requires an exact stock mainland overlap approached from nearby", 0)
    end
    local authored = expanded_overlap(state.map, entrance, actor)
    if actor then
        if not authored then error("The exact authored overlap and entrance pair is unavailable", 0) end
        return place_expanded_overlap(state, probe_player(state, slot), authored, position, alternate)
    end
    local legacy = route.fixture and route.entrance == entrance and route
        or (route.secondary and route.secondary.entrance == entrance and route.secondary)
    if authored and (not legacy or legacy.fixture ~= authored.actor) then
        return place_expanded_overlap(state, probe_player(state, slot), authored, position)
    end
    if entrance and route.entrance ~= entrance then
        if not route.secondary or route.secondary.entrance ~= entrance then
            error("The requested entrance does not select an authored exit in this map", 0)
        end
        route = route.secondary
    end
    local details = probe_player(state, slot)
    local pawn = details.pawn
    local world = pawn:GetWorld()
    local exit = route == routes[state.map] and fixture or secondary_fixture
    if not valid(exit) or not valid(exit.Box) or not same(world, exit:GetWorld()) then
        error("The authoritative pawn or exit geometry is unavailable", 0)
    end

    local center = exit.Box:K2_GetComponentLocation()
    local offsets = position == "inside" and {{0, 0}} or position == "far" and
        {{2250, 0}, {-2250, 0}, {0, 2250}, {0, -2250}} or {{-350, 0}, {350, 0}, {0, -350}, {0, 350}}
    if position == "near" and state.map == "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P" then
        offsets[1], offsets[2] = offsets[2], offsets[1]
    end
    local rotation = pawn:K2_GetActorRotation()
    if route.player_start then
        if position == "near" then offsets[1], offsets[4] = offsets[4], offsets[1] end
        if position == "far" then
            local marker = player_start(route, world)
            if not valid(marker) then error("The Nexus PlayerStart_1 is unavailable", 0) end
            local target = marker:K2_GetActorLocation()
            if not pawn:K2_TeleportTo({ X = target.X, Y = target.Y, Z = target.Z }, rotation) then
                error("No safe far placement at the Nexus PlayerStart_1", 0)
            end
            pawn:ForceNetUpdate()
            return "positioned " .. slot .. " far from the native exit"
        end
    end
    local height = center.Z
    if route.probe_arrival then
        local marker = placed_actor("LevelExit_Spawn_BP_C", route.probe_arrival, world)
        if not valid(marker) or not valid(marker.Arrow) or not marker.EntranceTag
            or marker.EntranceTag:ToString() ~= route.entrance then
            error("The authored exit ground reference is unavailable", 0)
        end
        local marker_location = marker.Arrow:K2_GetComponentLocation()
        local forward = route.probe_right_axis and exit:GetActorRightVector() or exit:GetActorForwardVector()
        height = route.probe_right_axis and center.Z or marker_location.Z
        local tangent_x, tangent_y = -forward.Y, forward.X
        local lane = (marker_location.X - center.X) * tangent_x + (marker_location.Y - center.Y) * tangent_y
        local extent = exit.Box:GetScaledBoxExtent()
        local clearance = math.max(0, (route.probe_right_axis and extent.X or extent.Y) - 250)
        lane = math.max(-clearance, math.min(lane, clearance))
        center.X, center.Y = center.X + lane * tangent_x, center.Y + lane * tangent_y
        if position ~= "inside" then
            local near = route.probe_right_axis and math.max(350, extent.Y + 150) or 350
            local distance = (position == "near" and near or 2250) * route.probe_approach
            local x, y = distance * forward.X, distance * forward.Y
            local separation = 0
            if details.kind == "guest" and position == "near" then
                local index = tonumber((details.slot or ""):match("^guest(%d+)$")) or 1
                separation = (index % 2 == 0 and -1 or 1) * (index < 3 and 200 or 400)
            end
            offsets = {{x + separation * tangent_x, y + separation * tangent_y},
                {x - (separation + 150) * tangent_x, y - (separation + 150) * tangent_y},
                {-x + separation * tangent_x, -y + separation * tangent_y}}
        end
    end
    for _, offset in ipairs(offsets) do
        local destination = {X = center.X + offset[1], Y = center.Y + offset[2], Z = height}
        if pawn:K2_TeleportTo(destination, rotation) then
            pawn:ForceNetUpdate()
            return "positioned " .. slot .. " " .. position .. " the native exit"
        end
    end
    error("No safe pawn location near the staged exit box", 0)
end

function zone.rows(state, phase, status_row)
    if not enabled then return {} end
    local rows = wall.rows(status_row)
    rows[#rows + 1] = status_row("native-zone", status, decision, requests, guest_denied, too_far, failure)
    rows[#rows + 1] = status_row("native-door", door_decision, door_denied, door_requests, door_detail)
    if keypad_proof and keypad_proof.map == state.map and phase == "hosting" then
        local proof = keypad_proof
        rows[#rows + 1] = status_row("native-cistern-keypad", proof.map, proof.pawn, proof.target,
            proof.key, proof.locked and 1 or 0, proof.unlocked and 1 or 0)
    end
    return rows
end

return zone
