local bolt = {}

local mesh_path = "/Game/Assets/StaticMeshes/Mesh_Misc/SM_Bolt.SM_Bolt"
local mesh_class = "/Script/Engine.StaticMeshComponent"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local max_flight_time, visual_time = .15, .16
local identity = {Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Translation = {X = 0, Y = 0, Z = 0},
    Scale3D = {X = 1, Y = 1, Z = 1}}
local starts, replicas, visuals = {}, {}, {}

local function valid(object) return object and object:IsValid() end

local function point(value)
    return {X = value.X, Y = value.Y, Z = value.Z}
end

local function distance_squared(a, b)
    return (a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2
end

function bolt.started(actor, client)
    local clock = StaticFindObject(statics_path)
    if not valid(clock) then return end
    local world = actor:GetWorld():GetAddress()
    local at = point(actor:K2_GetActorLocation())
    local now = clock:GetRealTimeSeconds(actor)
    local entry = {actor = actor, world = world, point = at, time = now}
    if client then
        replicas[#replicas + 1] = entry
        if #replicas > 16 then table.remove(replicas, 1) end
    else
        local rotation = actor:K2_GetActorRotation()
        entry.rotation = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll}
        local shooter = actor.Instigator
        entry.from_guest = valid(shooter) and not shooter:IsLocallyControlled()
        starts[actor:GetAddress()] = entry
    end
end

function bolt.ended(actor, destroyed)
    local key = actor:GetAddress()
    local entry = starts[key]
    starts[key] = nil
    if not destroyed or not entry then return end
    local world, clock = actor:GetWorld(), StaticFindObject(statics_path)
    if not valid(world) or world:GetAddress() ~= entry.world or not valid(clock)
        or clock:GetRealTimeSeconds(world) - entry.time > max_flight_time then return end
    local finish = valid(actor.RootComponent) and point(actor:K2_GetActorLocation()) or entry.point
    if distance_squared(entry.point, finish) > 600^2 then finish = entry.point end
    return world, entry.point, finish, entry.rotation, entry.from_guest
end

function bolt.play(world, pawn, origin, finish, rotation)
    local clock = StaticFindObject(statics_path)
    if not valid(clock) or distance_squared(pawn:K2_GetActorLocation(), origin) > 5000^2
        or distance_squared(origin, finish) > 600^2 or #visuals >= 12 then return false end
    local scope, now = world:GetAddress(), clock:GetRealTimeSeconds(world)
    for index, entry in ipairs(replicas) do
        if entry.observed and entry.world == scope and now - entry.time <= .75
            and (distance_squared(entry.point, origin) <= 35^2 or distance_squared(entry.point, finish) <= 35^2) then
            table.remove(replicas, index)
            return false
        end
    end
    local class, mesh = StaticFindObject(mesh_class), StaticFindObject(mesh_path)
    if not valid(mesh) then LoadAsset(mesh_path); mesh = StaticFindObject(mesh_path) end
    if not valid(class) or not valid(mesh) then return false end
    local transform = {Rotation = identity.Rotation, Translation = origin, Scale3D = identity.Scale3D}
    local component = pawn:AddComponentByClass(class, false, transform, true)
    if not valid(component) then return false end
    component:SetCollisionEnabled(0)
    component:SetGenerateOverlapEvents(false)
    component:SetMobility(2)
    component:SetAbsolute(true, true, true)
    pawn:FinishAddComponent(component, false, transform)
    component:SetStaticMesh(mesh)
    component:SetOnlyOwnerSee(false)
    component:SetOwnerNoSee(false)
    component:SetVisibility(true, false)
    component:SetHiddenInGame(false, false)
    component:K2_SetWorldLocationAndRotation(origin, rotation, false, {}, true)
    visuals[#visuals + 1] = {pawn = pawn, component = component, world = scope,
        start = now, origin = origin, finish = finish}
    return valid(component.StaticMesh) and component:IsVisible()
end

function bolt.animate(get_world)
    if #replicas == 0 and #visuals == 0 then return end
    local world = get_world and get_world()
    local clock = StaticFindObject(statics_path)
    local scope = valid(world) and world:GetAddress()
    local now = scope and valid(clock) and clock:GetRealTimeSeconds(world)
    for index = #replicas, 1, -1 do
        local entry = replicas[index]
        if entry.world ~= scope or not now or now - entry.time > .75 then
            table.remove(replicas, index)
        elseif not entry.observed and now - entry.time >= .04 and valid(entry.actor)
            and valid(entry.actor.RootComponent) and not entry.actor:IsActorBeingDestroyed() then
            local mesh = entry.actor.DisplayMesh
            entry.observed = valid(mesh) and mesh:IsVisible() and not mesh.bHiddenInGame and valid(mesh.StaticMesh)
                and distance_squared(entry.point, entry.actor:K2_GetActorLocation()) > 8^2
        end
    end
    for index = #visuals, 1, -1 do
        local entry = visuals[index]
        local component = entry.component
        if entry.world ~= scope or not now or now - entry.start >= visual_time
            or not valid(component) or not valid(entry.pawn) then
            if valid(component) and valid(entry.pawn) then component:K2_DestroyComponent(entry.pawn) end
            table.remove(visuals, index)
        else
            local alpha = math.max(0, (now - entry.start) / visual_time)
            local origin, finish = entry.origin, entry.finish
            component:K2_SetWorldLocation({
                X = origin.X + (finish.X - origin.X) * alpha,
                Y = origin.Y + (finish.Y - origin.Y) * alpha,
                Z = origin.Z + (finish.Z - origin.Z) * alpha,
            }, false, {}, true)
        end
    end
end

return bolt
