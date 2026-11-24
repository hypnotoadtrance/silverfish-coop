local fixed = {}
local effects = require("native_anomaly_effects")
local fixture = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and require("native_canister_fixture")
local aim_fixture = fixture and require("native_flood_jammer_aim_fixture")
local standing_class = "BlueprintGeneratedClass /Game/Assets/StaticMeshes/Mesh_Props/Prop_Standing_FuelTank.Prop_Standing_FuelTank_C"
local wreck_path = "/Game/Assets/StaticMeshes/Mesh_Misc/SM_FuelTank_Exploaded.SM_FuelTank_Exploaded"
local profiles = {
    ["BlueprintGeneratedClass /Game/Assets/StaticMeshes/Destructable/Explosive_Prop_Master_BP.Explosive_Prop_Master_BP_C"] = "fixedtank",
    ["BlueprintGeneratedClass /Game/Assets/StaticMeshes/Mesh_Props/Prop_Explosive_PowerBox.Prop_Explosive_PowerBox_C"] = "powerbox",
    ["BlueprintGeneratedClass /Game/Assets/StaticMeshes/Mesh_Props/Z_TestExplosiveObject.Z_TestExplosiveObject_C"] = "fixedfire",
    [standing_class] = "fixedtank",
}
local pending, active = {}, {}
local world, area, hosting, hooked
local blast_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_GameEffects.FL_GameEffects_C:SpawnExplosion"

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function root_mesh(actor, standing)
    return standing and actor.PropMesh or actor.StaticMesh
end

local function destroyed_mesh(entry)
    if not entry.standing then return entry.actor.DestroyedMesh end
    if not valid(entry.wreck) then entry.wreck = StaticFindObject(wreck_path) end
    return entry.wreck
end

local function resolve(path, state)
    local actor = path and #path <= 512 and StaticFindObject(path)
    local entry = valid(actor) and active[actor:GetAddress()]
    if entry and entry.path == path and same(actor:GetWorld(), state.world) and not actor:IsActorBeingDestroyed()
        and same(root_mesh(actor, entry.standing), entry.mesh) and same(entry.mesh:GetOwner(), actor) then return entry end
end

function fixed.created(actor, class, seat)
    local cue = profiles[class]
    if not cue or type(SilverfishQualifyNativePlacedFixedExplosive) ~= "function" then return end
    local path = actor:GetFullName():match("^%S+ (.+)$")
    if not path then return end
    local placed, guest = SilverfishQualifyNativePlacedFixedExplosive(path)
    if not placed or (seat == "client" and not guest or seat == "host" and not actor:HasAuthority()) then return end
    local standing = class == standing_class
    local mesh, center = root_mesh(actor, standing), standing and actor.Arrow or actor.FX_Center
    if not valid(mesh) or not same(mesh, actor.RootComponent) or not same(mesh:GetOwner(), actor)
        or not valid(center) or not same(center:GetOwner(), actor) then return end
    if standing then
        if not same(actor:GetOuter(), actor:GetWorld().PersistentLevel) then return end
    elseif not valid(actor.DestroyedMesh) then return end
    local intact, audio = mesh.StaticMesh, actor.Audio
    if standing then
        -- A campaign can load with the wreck already fitted. The intact mesh
        -- still comes from the authored tank's stock mesh pool.
        intact = actor.MeshPool[1]
        if not valid(intact) then return end
        audio = nil
    end
    local hum = seat == "client" and valid(audio) and same(audio:GetOwner(), actor) and audio:IsPlaying()
    pending[actor:GetAddress()] = {actor = actor, path = path, mesh = mesh, center = center, cue = cue, intact = intact,
        hum = hum, standing = standing}
end

function fixed.observe_spawned_blast(explosion)
    if not hosting or not explosion:HasAuthority() then return end
    local point, source = explosion:K2_GetActorLocation()
    for _, entry in pairs(active) do
        local actor = entry.actor
        if entry.standing and valid(actor) and actor:HasAuthority() and same(actor:GetWorld(), world)
            and (actor.Destroyed or actor.Health <= 0) and valid(entry.center) then
            local at = entry.center:K2_GetComponentLocation()
            if (at.X - point.X)^2 + (at.Y - point.Y)^2 + (at.Z - point.Z)^2 < 9 then
                if source then return end
                source = entry
            end
        end
    end
    if source then
        return effects.observe_fixed_blast(source.cue, point, explosion.PFX, explosion.SFX,
            explosion["PFX Scale"], explosion["SFX Vol"], explosion["Attenuation Settings"])
    end
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
            local damaged_mesh = same(mesh, destroyed_mesh(entry))
            local name = valid(mesh) and mesh:GetFullName():match("^StaticMesh (.+)$")
            local burning
            if entry.standing then
                local particles = actor.NI_FireSmall
                burning = not actor.Destroyed and
                    (hosting and actor.Burning_0 == true or not hosting and valid(particles) and particles:IsActive())
            end
            rows[#rows + 1] = row("native-fixed-explosive", entry.path, actor.Destroyed and 1 or 0,
                damaged_mesh and 1 or 0, name or "-", actor.Health, burning == nil and "-" or (burning and 1 or 0))
        else
            active[address] = nil
        end
    end
    return rows
end

local function apply(entry, broken, burning)
    local actor = entry.actor
    local target = entry.intact
    if broken then
        target = destroyed_mesh(entry)
        if entry.standing and not valid(target) then
            LoadAsset(wreck_path)
            target = destroyed_mesh(entry)
        end
    end
    if not valid(target) then return end
    if not same(entry.mesh.StaticMesh, target) then entry.mesh:SetStaticMesh(target) end
    if not same(entry.mesh.StaticMesh, target) then return end
    if actor.Destroyed ~= broken then
        actor.Destroyed = broken
        local audio = not entry.standing and actor.Audio
        if valid(audio) and same(audio:GetOwner(), actor) then
            if broken then
                audio:Stop()
            elseif entry.hum then
                audio:Play(0)
            end
        end
    end
    if entry.standing and (burning ~= nil or broken) then
        local particles, sound = actor.NI_FireSmall, actor.Fire01_Cue
        if valid(particles) and valid(sound) and same(particles:GetOwner(), actor) and same(sound:GetOwner(), actor) then
            local lit = not broken and burning == true
            actor.Burning_0 = lit
            if particles:IsActive() ~= lit then
                if lit then particles:Activate(false) else particles:Deactivate() end
            end
            if sound:IsPlaying() ~= lit then
                if lit then sound:Activate(false) else sound:Stop() end
            end
        end
    end
end

function fixed.aim_guest_fixture(path, state, phase)
    local entry = aim_fixture and resolve(path, state)
    return entry and (entry.standing or entry.cue == "powerbox") and aim_fixture.aim_fixed_guest(state, phase, entry.mesh)
end

function fixed.command(args, state, phase)
    if args[2] ~= "native-fixed-explosives-apply" then
        if fixture and args[2]:match("^native%-fixed%-explosive%-.+%-fixture$") then
            local entry = resolve(args[3], state)
            if args[2] == "native-fixed-explosive-aim-fixture" and entry and #args == 5 then
                return aim_fixture.aim_target(state, phase, args[4], args[5], "native-fixed-explosive",
                    entry.actor, nil, entry.mesh)
            end
            if args[2] == "native-fixed-explosive-flag-fixture" and entry and #args == 3 and phase == "hosting"
                and (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1") then
                local targets = entry.actor.Activate_OnDestroyed
                for index = 1, targets:GetArrayNum() do
                    local actor = targets[index]
                    if valid(actor) and same(actor:GetWorld(), world)
                        and actor:GetClass():GetFullName() == "BlueprintGeneratedClass /Game/Classes/ScriptingActors/SaveFlag_Updater_BP.SaveFlag_Updater_BP_C" then
                        local result = {}
                        world.OwningGameInstance:Check_SaveFlag(actor["Key ID"], result, result)
                        if result.Found and math.type(result.Value) == "integer" then
                            return actor["Key ID"]:ToString() .. "|" .. result.Value
                        end
                    end
                end
                error("No stock sabotage counter on the selected prop", 0)
            end
            return fixture.command(args, state, phase, entry and entry.actor, entry and entry.mesh,
                entry and valid(entry.center) and entry.center:K2_GetComponentLocation(), entry and entry.standing and "standing")
        end
        return
    end
    local epoch = args[4]
    if phase ~= "connected" or state.map ~= args[3] or area ~= state.map or not same(state.world, world)
        or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$") or #args < 5 or #args > 36 then
        error("Fixed explosive state is outside the current guest visit", 0)
    end
    for index = 5, #args do
        local path, bit, fire = args[index]:match("^([^|]+)|([01])|([01])$")
        if not path then path, bit = args[index]:match("^([^|]+)|([01])$") end
        local entry = resolve(path, state)
        if entry then
            local burning
            if fire then burning = fire == "1" end
            apply(entry, bit == "1", burning)
        end
    end
    return "Applied the host's fixed explosive meshes"
end

return fixed
