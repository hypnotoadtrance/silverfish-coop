local lantern = {}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local world_path = map .. ".Observitory_P"
local path = world_path .. ":PersistentLevel.Prop_Lantern_01_C_1"
local name = "Prop_Lantern_01_C " .. path
local class = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_Lantern_01.Prop_Lantern_01_C"
local mesh_path = "/Script/Engine.StaticMeshComponent"
local sphere_path = "/Script/Engine.SphereComponent"
local host, guest

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

function lantern.sync(state, phase)
    if host and not same(host.world, state.world) then host = nil end
    if guest and not same(guest.world, state.world) then guest = nil end
    if not enabled or state.map ~= map then return end
    local ok, actor, sphere = pcall(scene, state, phase)
    if not ok or not actor then return end
    if phase == "hosting" and actor:HasAuthority() then
        if not host or not same(host.actor, actor) then
            host = replicate(actor)
        elseif host.held ~= actor.BeingHeld then
            actor:ForceNetUpdate()
            host.held = actor.BeingHeld
        end
    elseif phase == "connected" then
        -- The stock light setter enables a fire sensor. Only the host can apply its fire overlaps.
        if sphere:GetGenerateOverlapEvents() then sphere:SetGenerateOverlapEvents(false) end
        if sphere:GetGenerateOverlapEvents() == false then guest = {actor = actor, world = state.world} end
    end
end

function lantern.command(fields, state, phase)
    if fields[2] == "native-observatory-lantern-fixture" then
        local actor = scene(state, phase)
        if #fields ~= 3 or phase ~= "hosting" or not host or not same(host.actor, actor)
            or not actor:HasAuthority() or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
            or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" then
            error("native_observatory_lantern_fixture_unavailable", 0)
        end
        if fields[3] == "toggle" then
            actor:SetLitUnlit(not actor.Lit, false)
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
