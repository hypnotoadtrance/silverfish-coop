local fixed = {}
local effects = require("native_anomaly_effects")
local fixture = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and require("native_canister_fixture")
local profiles = {
    ["BlueprintGeneratedClass /Game/Assets/StaticMeshes/Destructable/Explosive_Prop_Master_BP.Explosive_Prop_Master_BP_C"] = "fixedtank",
    ["BlueprintGeneratedClass /Game/Assets/StaticMeshes/Mesh_Props/Prop_Explosive_PowerBox.Prop_Explosive_PowerBox_C"] = "powerbox",
    ["BlueprintGeneratedClass /Game/Assets/StaticMeshes/Mesh_Props/Z_TestExplosiveObject.Z_TestExplosiveObject_C"] = "fixedfire",
}
local pending, active = {}, {}
local world, area, hosting, hooked
local blast_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_GameEffects.FL_GameEffects_C:SpawnExplosion"

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function resolve(path, state)
    local actor = path and #path <= 512 and StaticFindObject(path)
    local entry = valid(actor) and active[actor:GetAddress()]
    if entry and entry.path == path and same(actor:GetWorld(), state.world) and not actor:IsActorBeingDestroyed()
        and same(actor.StaticMesh, entry.mesh) and same(entry.mesh:GetOwner(), actor) then return entry end
end

function fixed.created(actor, class, seat)
    local cue = profiles[class]
    if not cue or type(SilverfishQualifyNativePlacedFixedExplosive) ~= "function" then return end
    local path = actor:GetFullName():match("^%S+ (.+)$")
    if not path then return end
    local placed, guest = SilverfishQualifyNativePlacedFixedExplosive(path)
    if not placed or (seat == "client" and not guest or seat == "host" and not actor:HasAuthority()) then return end
    local mesh, center = actor.StaticMesh, actor.FX_Center
    if not valid(mesh) or not same(mesh, actor.RootComponent) or not same(mesh:GetOwner(), actor)
        or not valid(center) or not same(center:GetOwner(), actor) or not valid(actor.DestroyedMesh) then return end
    local intact, audio = mesh.StaticMesh, actor.Audio
    local hum = seat == "client" and valid(audio) and same(audio:GetOwner(), actor) and audio:IsPlaying()
    pending[actor:GetAddress()] = {actor = actor, path = path, mesh = mesh, center = center, cue = cue, intact = intact,
        hum = hum}
end

function fixed.ended(address)
    active[address], pending[address] = nil, nil
end

local function observe_blast(location, pfx, scale, sfx, volume, ignored, attenuation, context)
    local actor, point = context:get(), location:get()
    local entry = valid(actor) and active[actor:GetAddress()]
    local sources = ignored:get()
    if not entry or not actor:HasAuthority() or not same(actor:GetWorld(), world)
        or not valid(entry.center) or not same(entry.center:GetOwner(), actor)
        or not sources or sources:GetArrayNum() ~= 1 or not same(sources[1], actor) then return end
    local at = entry.center:K2_GetComponentLocation()
    if (at.X - point.X)^2 + (at.Y - point.Y)^2 + (at.Z - point.Z)^2 < 9 then
        effects.observe_fixed_blast(entry.cue, point, pfx:get(), sfx:get(), scale:get(), volume:get(), attenuation:get())
    end
end

function fixed.rows(state, phase, row)
    hosting = phase == "hosting"
    if not same(world, state.world) or area ~= state.map then
        world, area, active = state.world, state.map, {}
    end
    for address, entry in pairs(pending) do
        if valid(entry.actor) and same(world, entry.actor:GetWorld()) and not entry.actor:IsActorBeingDestroyed() then
            active[address] = entry
        end
        pending[address] = nil
    end
    if hosting and not hooked and next(active) and type(RegisterHook) == "function" and valid(StaticFindObject(blast_path)) then
        -- The stock fixed prop calls this library directly rather than spawning an ExplosionActor.
        RegisterHook(blast_path, function(_, location, _, _, pfx, scale, sfx, volume, ignored, _, _, _, _, attenuation, context)
            if hosting then observe_blast(location, pfx, scale, sfx, volume, ignored, attenuation, context) end
        end)
        hooked = true
    end
    local rows = {}
    for address, entry in pairs(active) do
        local actor = entry.actor
        if valid(actor) and not actor:IsActorBeingDestroyed() and valid(entry.mesh) then
            local mesh = entry.mesh.StaticMesh
            local damaged_mesh = same(mesh, actor.DestroyedMesh)
            local name = valid(mesh) and mesh:GetFullName():match("^StaticMesh (.+)$")
            rows[#rows + 1] = row("native-fixed-explosive", entry.path, actor.Destroyed and 1 or 0,
                damaged_mesh and 1 or 0, name or "-", actor.Health)
        else
            active[address] = nil
        end
    end
    return rows
end

local function apply(entry, broken)
    local actor = entry.actor
    local target = broken and actor.DestroyedMesh or entry.intact
    if not valid(target) then return end
    if not same(entry.mesh.StaticMesh, target) then entry.mesh:SetStaticMesh(target) end
    if not same(entry.mesh.StaticMesh, target) then return end
    if actor.Destroyed ~= broken then
        actor.Destroyed = broken
        local audio = actor.Audio
        if valid(audio) and same(audio:GetOwner(), actor) then
            if broken then
                audio:Stop()
            elseif entry.hum then
                audio:Play(0)
            end
        end
    end
end

function fixed.command(args, state, phase)
    if args[2] ~= "native-fixed-explosives-apply" then
        if fixture and args[2]:match("^native%-fixed%-explosive%-.+%-fixture$") then
            local entry = resolve(args[3], state)
            return fixture.command(args, state, phase, entry and entry.actor, entry and entry.mesh,
                entry and valid(entry.center) and entry.center:K2_GetComponentLocation())
        end
        return
    end
    local epoch = args[4]
    if phase ~= "connected" or state.map ~= args[3] or area ~= state.map or not same(state.world, world)
        or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$") or #args < 5 or #args > 36 then
        error("Fixed explosive state is outside the current guest visit", 0)
    end
    for index = 5, #args do
        local path, bit = args[index]:match("^([^|]+)|([01])$")
        local entry = resolve(path, state)
        if entry then apply(entry, bit == "1") end
    end
    return "Applied the host's fixed explosive meshes"
end

return fixed
