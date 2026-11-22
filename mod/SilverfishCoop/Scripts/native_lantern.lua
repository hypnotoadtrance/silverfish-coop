local lantern = {}
local carry = require("native_prop_carry")
local guest_save = require("guest_save")
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local world_path = map .. ".Observitory_P"
local path = world_path .. ":PersistentLevel.Prop_Lantern_01_C_1"
local name = "Prop_Lantern_01_C " .. path
local class = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_Lantern_01.Prop_Lantern_01_C"
local mesh_path = "/Script/Engine.StaticMeshComponent"
local sphere_path = "/Script/Engine.SphereComponent"
local interact_channel, blocking = 16, 2 -- ECC_GameTraceChannel3, the game's Interactable trace.
local host, guest, surface
local trace_color = {R = 0, G = 0, B = 0, A = 0}

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, kind) return valid(object) and same(object:GetClass(), StaticFindObject(kind)) end
local function finite(value) return type(value) == "number" and math.abs(value) <= 1e7 end

local function scene(state, phase)
    local hosting = phase == "hosting"
    local world = state.world
    if not enabled or state.map ~= map or (not hosting and phase ~= "connected")
        or not state.listening or state.server ~= (not hosting)
        or not valid(world) or world:GetFullName() ~= "World " .. world_path then return end
    local driver = world.NetDriver
    local connection = valid(driver) and driver.ServerConnection
    if not valid(driver) or not same(driver.World, world)
        or (hosting and valid(connection))
        or (not hosting and (not valid(connection) or not same(connection.Driver, driver))) then return end
    local actor = StaticFindObject(path)
    if not exact(actor, class) or actor:GetFullName() ~= name or actor:IsActorBeingDestroyed()
        or not same(actor:GetWorld(), world) or not same(actor:GetOuter(), world.PersistentLevel) then return end
    local mesh, sphere = actor.PropMesh, actor.Sphere
    if not exact(mesh, mesh_path) or not same(mesh:GetOwner(), actor) or not same(mesh, actor.RootComponent)
        or not exact(sphere, sphere_path) or not same(sphere:GetOwner(), actor) then return end
    return actor, sphere
end

local function replicate(actor)
    local replicates, movement = actor.bReplicates, actor.bReplicateMovement
    if actor.bNetLoadOnClient ~= true or type(replicates) ~= "boolean" or type(movement) ~= "boolean" then return end
    local applied = pcall(function()
        if not replicates then actor:SetReplicates(true) end
        if not movement then actor:SetReplicateMovement(true) end
    end)
    if not applied or actor.bReplicates ~= true or actor.bReplicateMovement ~= true then
        pcall(function()
            if actor.bReplicateMovement ~= movement then actor:SetReplicateMovement(movement) end
            if actor.bReplicates ~= replicates then actor:SetReplicates(replicates) end
        end)
        return
    end
    actor:ForceNetUpdate()
    return {actor = actor, world = actor:GetWorld(), held = actor.BeingHeld}
end

local function clear_surface()
    if surface and valid(surface.mesh) and surface.mesh:GetCollisionResponseToChannel(interact_channel) == blocking then
        surface.mesh:SetCollisionResponseToChannel(interact_channel, surface.response)
    end
    surface = nil
end

local function expose_flame(actor)
    local mesh = actor.PropMesh
    if surface and (not same(surface.mesh, mesh) or actor.Lit ~= true) then clear_surface() end
    if actor.Lit ~= true or surface then return end
    -- The stock visibility ray stops at the visible body in front of the fire sphere.
    local original = mesh:GetCollisionResponseToChannel(interact_channel)
    if original ~= 0 and original ~= 1 then return end
    mesh:SetCollisionResponseToChannel(interact_channel, blocking)
    if mesh:GetCollisionResponseToChannel(interact_channel) == blocking then
        surface = {mesh = mesh, response = original}
    end
end

function lantern.sync(state, phase)
    if host and not same(host.world, state.world) then host = nil end
    if guest and not same(guest.world, state.world) then guest = nil end
    if guest and phase ~= "connected" and not state.listening then
        if guest.overlaps and valid(guest.sphere) then guest.sphere:SetGenerateOverlapEvents(true) end
        guest = nil
    end
    if not enabled or state.map ~= map then clear_surface(); return end
    local ok, actor, sphere = pcall(scene, state, phase)
    if not ok or not actor then clear_surface(); return end
    if phase == "hosting" and actor:HasAuthority() then
        if not host or not same(host.actor, actor) then
            host = replicate(actor)
        elseif host.held ~= actor.BeingHeld then
            actor:ForceNetUpdate()
            host.held = actor.BeingHeld
        end
        if host and not host.carrying then host.carrying = carry.enroll(actor) end
    elseif phase == "connected" then
        -- The stock light setter enables a fire sensor. Only the host can apply its fire overlaps.
        local overlaps = sphere:GetGenerateOverlapEvents()
        if overlaps then sphere:SetGenerateOverlapEvents(false) end
        if sphere:GetGenerateOverlapEvents() == false and (not guest or not same(guest.actor, actor)) then
            guest = {actor = actor, world = state.world, sphere = sphere, overlaps = overlaps == true}
        end
    end
    if phase == "hosting" and host or phase == "connected" and guest then expose_flame(actor) end
end

local function stock_hit(pawn, actor, pitch, yaw)
    local camera = pawn.Camera
    local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local reach = pawn.InteractionRange
    if not valid(camera) or not same(camera:GetOwner(), pawn) or not valid(api)
        or not finite(reach) or reach <= 0 or reach > 1000 then return end
    local origin, body = camera:K2_GetComponentLocation(), pawn:K2_GetActorLocation()
    local function squared(a, b) return (a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2 end
    if not origin or not body or squared(origin, body) > 150^2 then return end
    local p, y = math.rad(pitch), math.rad(yaw)
    local function hit(range, channel, complex)
        local out = {}
        local endpoint = {X = origin.X + math.cos(p) * math.cos(y) * range,
            Y = origin.Y + math.cos(p) * math.sin(y) * range, Z = origin.Z + math.sin(p) * range}
        if api:LineTraceSingle(pawn, origin, endpoint, channel, complex, {pawn}, 0, out, true, trace_color, trace_color, 0) then
            return out.OutHit or out
        end
    end
    local visibility = hit(reach, 1, true)
    if visibility and visibility.ImpactPoint then
        reach = math.min(reach, math.sqrt(squared(origin, visibility.ImpactPoint)))
    end
    local interaction = hit(reach, 4, false)
    local component = interaction and interaction.Component and interaction.Component:Get()
    if valid(component) and same(component:GetOwner(), actor) then return component end
end

function lantern.command(fields, state, phase)
    if fields[2] == "native-observatory-lantern-extinguish" then
        local actor = scene(state, phase)
        local pitch, yaw = tonumber(fields[4]), tonumber(fields[5])
        if #fields ~= 5 or phase ~= "hosting" or not host or not same(host.actor, actor) or not actor:HasAuthority()
            or not finite(pitch) or math.abs(pitch) > 89.9 or not finite(yaw) or math.abs(yaw) > 180 then
            error("native_observatory_lantern_extinguish_unavailable", 0)
        end
        local ok, pawn = pcall(guest_save.exact_item_context, state, phase, fields[3])
        local component = ok and valid(pawn) and pawn.Dead == false and stock_hit(pawn, actor, pitch, yaw)
        if not component then error("native_observatory_lantern_out_of_reach", 0) end
        if actor.Lit then actor:SendInteractionToActor(pawn, component) end
        if actor.Lit ~= false then error("native_observatory_lantern_extinguish_unverified", 0) end
        return "stock lantern interaction extinguished the flame"
    end
    if fields[2] == "native-observatory-lantern-fixture" then
        local actor = scene(state, phase)
        if #fields ~= 3 or phase ~= "hosting" or not host or not same(host.actor, actor)
            or not actor:HasAuthority() or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
            or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" then
            error("native_observatory_lantern_fixture_unavailable", 0)
        end
        if fields[3] == "toggle" then
            actor:SetLitUnlit(not actor.Lit, false)
        elseif fields[3] == "ignite" then
            actor:SetOnFire(5, 0.2, state.local_pawn and state.local_pawn.pawn)
            if actor.Lit ~= true then error("native_observatory_lantern_fixture_ignite_unverified", 0) end
        elseif fields[3] == "impulse" then
            local mesh, bone = actor.PropMesh, FName("None", EFindName.FNAME_Find)
            if mesh:IsSimulatingPhysics(bone) ~= true or actor.BeingHeld ~= false then
                error("native_observatory_lantern_fixture_body_unavailable", 0)
            end
            mesh:AddImpulse({X = 0, Y = 0, Z = 350}, bone, true)
            actor:ForceNetUpdate()
        else
            error("native_observatory_lantern_fixture_argument", 0)
        end
        return "Observatory lantern fixture dispatched"
    end
    if fields[2] ~= "native-observatory-lantern-light" then return end
    local actor, sphere = scene(state, phase)
    if #fields ~= 5 or phase ~= "connected" or fields[3] ~= map or fields[4] ~= name
        or (fields[5] ~= "0" and fields[5] ~= "1") or not guest or not same(guest.actor, actor)
        or sphere:GetGenerateOverlapEvents() ~= false or type(actor.Lit) ~= "boolean" then
        error("native_observatory_lantern_light_unavailable", 0)
    end
    local lit = fields[5] == "1"
    if actor.Lit ~= lit then actor:SetLitUnlit(lit, false) end
    if actor.Lit ~= lit or sphere:GetGenerateOverlapEvents() ~= false then
        error("native_observatory_lantern_light_unverified", 0)
    end
    return "Observatory lantern light applied"
end

function lantern.status(state, phase, row)
    local ok, actor = pcall(scene, state, phase)
    local current = phase == "hosting" and host or phase == "connected" and guest
    if not ok or not current or not same(current.actor, actor)
        or type(actor.Lit) ~= "boolean" or type(actor.BeingHeld) ~= "boolean" then return end
    local pos = actor:K2_GetActorLocation()
    if not pos or not finite(pos.X) or not finite(pos.Y) or not finite(pos.Z) then return end
    return row("native-observatory-lantern", map, name, actor.Lit and 1 or 0, actor.BeingHeld and 1 or 0,
        actor.bReplicateMovement == true and 1 or 0,
        string.format("%.2f", pos.X), string.format("%.2f", pos.Y), string.format("%.2f", pos.Z))
end

return lantern
