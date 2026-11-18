-- A single collisionless Unreal prop representing the host's test-map loot stack.
local loot = {}

local playtest_map = "/Game/Classes/Maps/PlaytestMap_01"
local marker_class = "/Script/Engine.StaticMeshActor"
local marker_mesh = "/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Prop_ToyRobot_01.SM_Prop_ToyRobot_01"
local movable = 2
local no_collision = 0
local max_coordinate = 10000000
local location_tolerance = 2

local host_marker
local client_marker
local client_tracking

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function in_world(actor, world)
    if not valid(actor) or not valid(world) or actor:IsActorBeingDestroyed() then return false end
    local owner_world = actor:GetWorld()
    return valid(owner_world) and owner_world:GetAddress() == world:GetAddress()
end

local function fixture_id(value)
    if type(value) ~= "string" or #value > 64 or not value:match("^[A-Za-z0-9][A-Za-z0-9_%-]*$") then
        error("Invalid loot fixture ID", 0)
    end
    return value
end

local function coordinate(value)
    local number = tonumber(value)
    if not number or number ~= number or math.abs(number) > max_coordinate then
        error("Invalid loot marker coordinate", 0)
    end
    return number
end

local function location_matches(position, x, y, z)
    return math.abs(position.X - x) <= location_tolerance
        and math.abs(position.Y - y) <= location_tolerance
        and math.abs(position.Z - z) <= location_tolerance
end

local function require_host(state, phase)
    local pawn = state.local_pawn and state.local_pawn.pawn
    if phase ~= "hosting" or state.map ~= playtest_map or not valid(pawn) or not pawn:HasAuthority() then
        error("Only the test-map host can manage a loot marker", 0)
    end
    local world = pawn:GetWorld()
    if not valid(world) then error("Loot marker world is unavailable", 0) end
    return world
end

local function disable_collision(actor, component)
    actor:SetActorEnableCollision(false)
    component:SetCollisionEnabled(no_collision)
end

local function spawn(world, id, x, y, z)
    if host_marker and in_world(host_marker.actor, world) then
        if host_marker.id == id and location_matches(host_marker.actor:K2_GetActorLocation(), x, y, z) then
            return "loot marker exists"
        end
        error("A loot marker already exists", 0)
    end

    local class = StaticFindObject(marker_class)
    local mesh = StaticFindObject(marker_mesh)
    if not valid(mesh) then
        LoadAsset(marker_mesh)
        mesh = StaticFindObject(marker_mesh)
    end
    if not valid(class) or not valid(mesh) then error("Loot marker assets are unavailable", 0) end
    local actor = world:SpawnActor(class, { X = x, Y = y, Z = z }, { Pitch = 0, Yaw = 0, Roll = 0 })
    if not valid(actor) then error("Loot marker spawn failed", 0) end

    local ok, message = pcall(function()
        local component = actor.StaticMeshComponent
        if not valid(component) then error("Loot marker has no mesh component", 0) end
        component:SetMobility(movable)
        if not component:SetStaticMesh(mesh) then error("Loot marker mesh assignment failed", 0) end
        disable_collision(actor, component)
        component:SetIsReplicated(true)
        actor.bCanBeDamaged = false
        actor.bAlwaysRelevant = true
        actor:SetReplicates(true)
        actor:SetReplicateMovement(true)
        actor:ForceNetUpdate()
    end)
    if not ok then
        actor:K2_DestroyActor()
        error(tostring(message), 0)
    end
    host_marker = { id = id, actor = actor }
    return "loot marker spawned"
end

local function track(fields, state, phase)
    local pawn = state.local_pawn and state.local_pawn.pawn
    if phase ~= "connected" or state.map ~= playtest_map or not valid(pawn) or pawn:HasAuthority() then
        error("Only the connected test-map guest can track a loot marker", 0)
    end
    if #fields ~= 6 then error("Invalid loot marker tracking command", 0) end
    local id = fixture_id(fields[3])
    local x, y, z = coordinate(fields[4]), coordinate(fields[5]), coordinate(fields[6])
    if not client_tracking or client_tracking.id ~= id
        or client_tracking.x ~= x or client_tracking.y ~= y or client_tracking.z ~= z then
        client_marker = nil
        client_tracking = { id = id, x = x, y = y, z = z }
    end
    return "loot marker tracking set"
end

function loot.command(fields, state, phase)
    local action = fields[2]
    if action == "loot-track" then return track(fields, state, phase) end
    if action ~= "loot-spawn" and action ~= "loot-clear" then return nil end
    local world = require_host(state, phase)
    if action == "loot-spawn" and #fields == 6 then
        local id = fixture_id(fields[3])
        local x, y, z = coordinate(fields[4]), coordinate(fields[5]), coordinate(fields[6])
        return spawn(world, id, x, y, z)
    end
    if action == "loot-clear" and #fields == 3 then
        local id = fixture_id(fields[3])
        if not host_marker or not in_world(host_marker.actor, world) then
            host_marker = nil
            return "loot marker absent"
        end
        if host_marker.id ~= id then error("Unknown loot marker", 0) end
        host_marker.actor:K2_DestroyActor()
        host_marker = nil
        return "loot marker removed"
    end
    error("Invalid loot marker command", 0)
end

local function is_client_marker(actor, world)
    if not client_tracking or not in_world(actor, world) or actor:HasAuthority() or actor.bCanBeDamaged then
        return false
    end
    local component = actor.StaticMeshComponent
    local mesh = valid(component) and component.StaticMesh
    if not mesh or not valid(mesh) or mesh:GetFullName() ~= "StaticMesh " .. marker_mesh then return false end
    return location_matches(actor:K2_GetActorLocation(), client_tracking.x, client_tracking.y, client_tracking.z)
end

function loot.rows(state, phase, status_row)
    local pawn = state.local_pawn and state.local_pawn.pawn
    if state.map ~= playtest_map or not valid(pawn) then
        client_marker = nil
        return {}
    end
    local world = pawn:GetWorld()
    if phase == "hosting" and host_marker and in_world(host_marker.actor, world) then
        local actor = host_marker.actor
        local position = actor:K2_GetActorLocation()
        return { status_row("loot", host_marker.id, position.X, position.Y, position.Z, actor:GetFullName()) }
    end
    if phase ~= "connected" or not client_tracking then
        client_marker = nil
        return {}
    end
    if not is_client_marker(client_marker, world) then
        client_marker = nil
        for _, actor in ipairs(FindAllOf("StaticMeshActor") or {}) do
            if is_client_marker(actor, world) then
                disable_collision(actor, actor.StaticMeshComponent)
                client_marker = actor
                break
            end
        end
    end
    if not client_marker then return {} end
    local position = client_marker:K2_GetActorLocation()
    return { status_row("loot", client_tracking.id, position.X, position.Y, position.Z, client_marker:GetFullName()) }
end

return loot
