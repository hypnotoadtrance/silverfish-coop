local add_component = require("native_preview_component")

local view = {}
local preview, clock, math_api

local function valid(object) return object and object:IsValid() end

local function clear()
    local prior = preview
    preview = nil
    if not prior or not valid(prior.actor) or prior.actor:IsActorBeingDestroyed() then return end
    for _, part in ipairs(prior.parts) do
        if part.hidden and valid(part.source) then pcall(part.source.SetVisibility, part.source, true, false) end
        if valid(part.mesh) then pcall(part.mesh.K2_DestroyComponent, part.mesh, prior.actor) end
        if valid(part.pivot) then pcall(part.pivot.K2_DestroyComponent, part.pivot, prior.actor) end
    end
end

view.clear = clear

function view.resolve(name, sequence)
    if preview and preview.name == name and (not sequence or preview.sequence == sequence) then clear() end
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
    if preview and preview.name == profile.name then
        preview.sequence = sequence
        return true
    end
    clear()
    if not valid(clock) then clock = StaticFindObject("/Script/Engine.Default__GameplayStatics") end
    if not valid(math_api) then math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary") end
    if not valid(clock) or not valid(math_api) then return end
    local world = actor:GetWorld()
    local start_at = clock:GetRealTimeSeconds(world)
    local entry = {actor = actor, world = world, name = profile.name, sequence = sequence, start_at = start_at, parts = {}}
    preview = entry
    for index, panel in ipairs(panels) do
        local mesh_source = profile.hinge_arrow and actor.SM_Lid or panel
        if not add_part(entry, actor, panel, mesh_source, opened[index], locations[index]) then return end
    end
    return true
end

function view.start(current, sequence, panels, opened, locations)
    local ok, shown = pcall(start, current, sequence, panels, opened, locations)
    if not ok or not shown then clear() end
end

local function animate(world)
    local entry = preview
    if not valid(world) or not valid(entry.world) or world:GetAddress() ~= entry.world:GetAddress()
        or not valid(entry.actor) or entry.actor:IsActorBeingDestroyed() or entry.actor.Looted ~= false then return false end
    local elapsed = clock:GetRealTimeSeconds(world) - entry.start_at
    if elapsed >= .75 then return false end
    if entry.paused then return true end
    -- Begin the lid movement locally and leave the final pose to the confirmed stock open.
    local alpha = math.min(1, math.max(0, elapsed / .12))
    local fraction = .32 * alpha * (2 - alpha)
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
    return true
end

function view.tick(world)
    if not preview then return end
    local ok, alive = pcall(animate, world)
    if not ok or not alive then clear() end
end

return view
