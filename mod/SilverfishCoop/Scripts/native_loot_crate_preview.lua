local add_component = require("native_preview_component")

local view = {}
local previews, clock, math_api = {}, nil, nil
local open_seconds, max_previews = .28, 4

local function valid(object) return object and object:IsValid() end

local function clear(name)
    local prior = previews[name]
    previews[name] = nil
    if not prior or not valid(prior.actor) or prior.actor:IsActorBeingDestroyed() then return end
    for _, part in ipairs(prior.parts) do
        if part.hidden and valid(part.source) then pcall(part.source.SetVisibility, part.source, true, false) end
        if valid(part.mesh) then pcall(part.mesh.K2_DestroyComponent, part.mesh, prior.actor) end
        if valid(part.pivot) then pcall(part.pivot.K2_DestroyComponent, part.pivot, prior.actor) end
    end
end

function view.clear()
    for name in pairs(previews) do clear(name) end
end

function view.resolve(name, sequence)
    local entry = previews[name]
    if entry and (not sequence or entry.sequence == sequence) then clear(name) end
end

local function angle(start, finish, fraction)
    return start + ((finish - start + 180) % 360 - 180) * fraction
end

local function add_part(entry, actor, panel, mesh_source, goal, end_location)
    if not valid(mesh_source) or not valid(mesh_source.StaticMesh) or not mesh_source:IsVisible() or mesh_source.bHiddenInGame then
        return
    end
    local parent = panel:GetAttachParent()
    if not valid(parent) then return end
    local rotation, location = panel.RelativeRotation, panel.RelativeLocation
    if not rotation or not location then return end

    local part = {
        actor = actor, source = mesh_source, goal = goal, end_location = end_location,
        rotation = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll},
        location = {X = location.X, Y = location.Y, Z = location.Z},
    }
    entry.parts[#entry.parts + 1] = part
    if panel ~= mesh_source then
        local pivot = add_component(part, "pivot", StaticFindObject("/Script/Engine.SceneComponent"), parent,
            math_api:MakeRelativeTransform(panel:K2_GetComponentToWorld(), parent:K2_GetComponentToWorld()))
        if not pivot then return end
        parent = pivot
    end
    local mesh = add_component(part, "mesh", StaticFindObject("/Script/Engine.StaticMeshComponent"), parent,
        math_api:MakeRelativeTransform(mesh_source:K2_GetComponentToWorld(), panel ~= mesh_source
            and panel:K2_GetComponentToWorld() or parent:K2_GetComponentToWorld()), mesh_source)
    if not mesh then return end
    part.moving = part.pivot or mesh
    part.hidden = true
    mesh_source:SetVisibility(false, false)
    return true
end

local function start(current, sequence, panels, opened, locations)
    local actor, profile = current.crate, current.profile
    local existing = previews[profile.name]
    if existing then
        existing.sequence = sequence or existing.sequence
        return true
    end
    local count = 0
    for _ in pairs(previews) do count = count + 1 end
    if count >= max_previews then return end
    if not valid(clock) then clock = StaticFindObject("/Script/Engine.Default__GameplayStatics") end
    if not valid(math_api) then math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary") end
    if not valid(clock) or not valid(math_api) then return end
    local world = actor:GetWorld()
    -- Start one short step into the easing curve before the request can settle.
    local start_at = clock:GetRealTimeSeconds(world) - .008
    local entry = {actor = actor, world = world, sequence = sequence, start_at = start_at, parts = {}}
    previews[profile.name] = entry
    for index, panel in ipairs(panels) do
        local mesh_source = profile.hinge_arrow and actor.SM_Lid or panel
        if not add_part(entry, actor, panel, mesh_source, opened[index], locations[index]) then return end
    end
    return true
end

function view.start(current, sequence, panels, opened, locations)
    local ok, shown = pcall(start, current, sequence, panels, opened, locations)
    if ok and shown then view.tick(current.crate:GetWorld()) else clear(current.profile.name) end
end

function view.confirm(name, opened, locations)
    local entry = previews[name]
    if not entry or entry.confirmed then return end
    for index, part in ipairs(entry.parts) do
        if not valid(part.moving) or not opened[index] then return clear(name) end
        local rotation, point = part.moving.RelativeRotation, part.moving.RelativeLocation
        if not rotation or (locations[index] and not point) then return clear(name) end
        part.rotation = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll}
        part.goal = opened[index]
        part.end_location = locations[index]
        if part.end_location then part.location = {X = point.X, Y = point.Y, Z = point.Z} end
    end
    entry.start_at = clock:GetRealTimeSeconds(entry.world)
    entry.confirmed, entry.paused = true, false
end

local function animate(entry, world)
    if not valid(world) or not valid(entry.world) or world:GetAddress() ~= entry.world:GetAddress()
        or not valid(entry.actor) or entry.actor:IsActorBeingDestroyed()
        or entry.actor.Looted ~= (entry.confirmed == true) then return false end
    local elapsed = clock:GetRealTimeSeconds(world) - entry.start_at
    if elapsed >= .75 then return false end
    if entry.paused then return true end
    local alpha = math.min(1, math.max(0, elapsed / open_seconds))
    local fraction = (entry.confirmed and 1 or .32) * alpha * alpha * (3 - 2 * alpha)
    for _, part in ipairs(entry.parts) do
        if not valid(part.moving) then return false end
        local start, stop = part.rotation, part.goal
        local rotation = {
            Pitch = angle(start.Pitch, stop.Pitch, fraction),
            Yaw = angle(start.Yaw, stop.Yaw, fraction),
            Roll = angle(start.Roll, stop.Roll, fraction),
        }
        if part.end_location then
            local from, to = part.location, part.end_location
            local location = {X = from.X + (to.X - from.X) * fraction,
                Y = from.Y + (to.Y - from.Y) * fraction, Z = from.Z + (to.Z - from.Z) * fraction}
            part.moving:K2_SetRelativeLocationAndRotation(location, rotation, false, {}, false)
        else
            part.moving:K2_SetRelativeRotation(rotation, false, {}, false)
        end
    end
    entry.paused = alpha == 1
    return not (entry.confirmed and entry.paused)
end

function view.tick(world)
    for name, entry in pairs(previews) do
        local ok, alive = pcall(animate, entry, world)
        if not ok or not alive then clear(name) end
    end
end

return view
