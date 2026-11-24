local ambience = {}
local profiles = require("native_storm_ambience_stock")

local audio_class = "Class /Script/Engine.AudioComponent"
local particles_class = "Class /Script/Niagara.NiagaraComponent"
local post_class = "Class /Script/Engine.PostProcessComponent"
local post_path = "/Script/Engine.PostProcessComponent"
local zero = {X = 0, Y = 0, Z = 0}
local one = {X = 1, Y = 1, Z = 1}
local no_rotation = {Pitch = 0, Yaw = 0, Roll = 0}
local transform = {Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Translation = zero, Scale3D = one}
local duration = 3
local creation_attempts = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and 1 or 3
local active

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class)
    return valid(object) and object:GetClass():GetFullName() == class
end

local function owned(component, actor, class)
    return exact(component, class) and same(component:GetOwner(), actor)
end

local function bounded(value, minimum, maximum)
    return type(value) == "number" and value >= minimum and value <= maximum
end

local function asset(path, class)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    if valid(object) and object:GetFullName() == class .. " " .. path then return object end
end

local function restore_stock(stock)
    local actor = stock and stock.actor
    if not valid(actor) or actor:IsActorBeingDestroyed() or not same(actor:GetWorld(), stock.world) then return end
    if same(stock.audio, actor.Ambiant_SFX) then pcall(stock.audio.SetVolumeMultiplier, stock.audio, stock.volume) end
    if same(stock.particle, actor.Ambiant_PFX) then pcall(stock.particle.SetVisibility, stock.particle, stock.visible, false) end
    if same(stock.post, actor.PostProcess) then pcall(function() stock.post.bEnabled = stock.enabled end) end
end

local function suppress_stock(entry)
    local scope, actor = entry.scope, entry.scope.day.LocalStorm
    local qualified = valid(actor) and not actor:IsActorBeingDestroyed() and actor:HasAuthority()
        and same(actor:GetWorld(), scope.world) and same(actor.DayControler_Ref, scope.day)
        and profiles[actor:GetClass():GetFullName():match("^BlueprintGeneratedClass (.+)$")]
    local stock = entry.stock
    if stock and (not qualified or not same(actor, stock.actor)) then
        restore_stock(stock)
        stock, entry.stock = nil, nil
    end
    if not qualified then return end
    if not stock then
        local audio = owned(actor.Ambiant_SFX, actor, audio_class) and actor.Ambiant_SFX or nil
        local particle = owned(actor.Ambiant_PFX, actor, particles_class) and actor.Ambiant_PFX or nil
        local post = owned(actor.PostProcess, actor, post_class) and actor.PostProcess or nil
        stock = {actor = actor, world = scope.world, audio = audio, volume = audio and audio.VolumeMultiplier,
            particle = particle, visible = particle and particle:IsVisible(), post = post, enabled = post and post.bEnabled}
        entry.stock = stock
    end
    if same(stock.audio, actor.Ambiant_SFX) and stock.audio.VolumeMultiplier ~= 0 then stock.audio:SetVolumeMultiplier(0) end
    if same(stock.particle, actor.Ambiant_PFX) and stock.particle:IsVisible() then stock.particle:SetVisibility(false, false) end
    if same(stock.post, actor.PostProcess) and stock.post.bEnabled then stock.post.bEnabled = false end
end

local function destroy(component)
    pcall(function()
        if not valid(component) then return end
        local owner = component:GetOwner()
        component:K2_DestroyComponent(valid(owner) and owner or nil)
    end)
end

local function clear_projection(entry)
    if valid(entry.audio) then pcall(entry.audio.Stop, entry.audio) end
    if valid(entry.particle) then pcall(entry.particle.Deactivate, entry.particle) end
    if valid(entry.post) then pcall(function() entry.post.bEnabled = false end) end
    destroy(entry.audio)
    destroy(entry.particle)
    destroy(entry.post)
    entry.particle, entry.audio, entry.post, entry.material, entry.fog_readback = nil, nil, nil, nil, nil
    entry.particle_path, entry.sound_path, entry.volume = nil, nil, nil
    entry.particle_attempts, entry.sound_attempts = nil, nil
end

function ambience.clear()
    local previous = active
    active = nil
    if not previous then return end
    pcall(clear_projection, previous)
    pcall(restore_stock, previous.stock)
end

local function valid_report(report, profile)
    if report.phase == "idle" then return true end
    if not profile or report.phase ~= "active" and report.phase ~= "fading"
        or not math.tointeger(report.level) or not bounded(report.level, 0, 3)
        or not bounded(report.blend, 0, 1) or not bounded(report.fade, 0, 60)
        or not bounded(report.volume, 0, 2)
        or report.sound == "-" and report.volume ~= 0
        or report.particle ~= "-" and report.particle ~= profile.particle
        or report.sound ~= "-" and report.sound ~= profile.sound then return false end
    local fog = report.fog
    return not fog or fog.material == profile.fog and bounded(fog.density, 0, 1) and bounded(fog.start, 0, 100000)
        and bounded(fog.r, 0, 1) and bounded(fog.g, 0, 1) and bounded(fog.b, 0, 1) and bounded(fog.a, 0, 1)
end

local function project_particle(entry, path)
    if path ~= entry.particle_path then
        if valid(entry.particle) then pcall(entry.particle.Deactivate, entry.particle) end
        destroy(entry.particle)
        entry.particle, entry.particle_path, entry.particle_attempts = nil, path, 0
    end
    if path == "-" or valid(entry.particle) or entry.particle_attempts >= creation_attempts then return end
    entry.particle_attempts = entry.particle_attempts + 1
    local api, particle = StaticFindObject("/Script/Niagara.Default__NiagaraFunctionLibrary"), asset(path, "NiagaraSystem")
    if not valid(api) or not particle then return end
    -- Stock DayCycle spawns LocalStorm at world origin, and the inherited
    -- ambient Niagara component is 100 m above that actor.
    entry.particle = api:SpawnSystemAtLocation(entry.scope.pawn, particle, {X = 0, Y = 0, Z = 10000},
        no_rotation, one, false, true, 0, true)
    if not exact(entry.particle, particles_class) or not same(entry.particle.Asset, particle) then
        destroy(entry.particle)
        entry.particle = nil
    end
end

local function project_sound(entry, report, pitch)
    local volume = report.phase == "fading" and report.volume * report.blend or report.volume
    local scope = entry.scope
    if report.sound ~= entry.sound_path then
        if valid(entry.audio) then pcall(entry.audio.Stop, entry.audio) end
        destroy(entry.audio)
        entry.audio, entry.sound_path, entry.volume, entry.sound_attempts = nil, report.sound, nil, 0
    end
    if report.sound ~= "-" and not valid(entry.audio) and entry.sound_attempts < creation_attempts then
        entry.sound_attempts = entry.sound_attempts + 1
        local root, sound = scope.pawn.RootComponent, asset(report.sound, "SoundWave")
        if not valid(root) or not same(root:GetOwner(), scope.pawn) or not sound then return end
        entry.audio = scope.statics:SpawnSoundAttached(sound, root, FName("None"), zero, no_rotation,
            0, true, volume, pitch, 0, nil, nil, false)
        if not exact(entry.audio, audio_class) or not same(entry.audio.Sound, sound) then
            destroy(entry.audio)
            entry.audio = nil
        elseif report.phase == "active" and report.fade > 0 then
            entry.audio:FadeIn(report.fade, 1, 0, 0)
        end
    end
    if valid(entry.audio) and entry.volume ~= volume then entry.audio:SetVolumeMultiplier(volume) end
    entry.volume = volume
end

local function project_fog(entry, fog)
    local last = entry.fog_readback
    if fog and last and last.requested == fog and valid(entry.post) and entry.post.bEnabled then return end
    entry.fog_readback = nil
    if valid(entry.post) then entry.post.bEnabled = false end
    if not fog then
        return
    end
    local pawn = entry.scope.pawn
    if not valid(entry.post) then
        local class = StaticFindObject(post_path)
        if not valid(class) then return end
        entry.post = pawn:AddComponentByClass(class, false, transform, true)
        if not owned(entry.post, pawn, post_class) then return end
        entry.post.bEnabled, entry.post.bUnbound, entry.post.BlendWeight = false, true, 1
        pawn:FinishAddComponent(entry.post, false, transform)
    end
    if not valid(entry.material) or entry.material_path ~= fog.material then
        local library = StaticFindObject("/Script/Engine.Default__KismetMaterialLibrary")
        local source = asset(fog.material, "Material")
        if not valid(library) or not source then return end
        entry.material = library:CreateDynamicMaterialInstance(pawn, source, FName("None"), 0)
        if not exact(entry.material, "Class /Script/Engine.MaterialInstanceDynamic") then return end
        entry.post.Settings.WeightedBlendables.Array[1] = {Weight = 1, Object = entry.material}
        entry.material_path = fog.material
    end
    local material = entry.material
    local density, start, tint = FName("Density"), FName("StartDistance"), FName("Color")
    material:SetScalarParameterValue(density, fog.density)
    material:SetScalarParameterValue(start, fog.start)
    material:SetVectorParameterValue(tint, {R = fog.r, G = fog.g, B = fog.b, A = fog.a})
    local color = material:K2_GetVectorParameterValue(tint)
    local blend = entry.post.Settings.WeightedBlendables.Array[1]
    if not blend or not same(blend.Object, material) then return end
    local readback = {material = fog.material, requested = fog, density = material:K2_GetScalarParameterValue(density),
        start = material:K2_GetScalarParameterValue(start), r = color.R, g = color.G, b = color.B, a = color.A}
    for _, field in ipairs({"density", "start", "r", "g", "b", "a"}) do
        if not bounded(readback[field], fog[field] - .005, fog[field] + .005) then return end
    end
    entry.post.bEnabled = true
    entry.fog_readback = readback
end

local function current(entry, scope, epoch)
    return entry and scope and entry.epoch == epoch and same(entry.scope.world, scope.world)
        and same(entry.scope.pawn, scope.pawn) and same(entry.scope.day, scope.day)
        and same(entry.scope.connection, scope.connection)
end

function ambience.apply(scope, epoch, report)
    local profile = profiles[report.variant or ""]
    if not valid_report(report, profile) then error("guest_weather_ambience_payload_unavailable", 0) end
    if not current(active, scope, epoch) then
        ambience.clear()
        active = {scope = scope, epoch = epoch}
    end
    local previous = active.report
    if previous and previous.variant ~= report.variant then clear_projection(active) end
    if report.fog and previous and previous.fog then
        local last, next_fog = previous.fog, report.fog
        if last.material == next_fog.material and last.density == next_fog.density and last.start == next_fog.start
            and last.r == next_fog.r and last.g == next_fog.g and last.b == next_fog.b and last.a == next_fog.a then
            report.fog = last
        end
    end
    active.report, active.expires = report, scope.statics:GetRealTimeSeconds(scope.world) + duration
    suppress_stock(active)
    if report.phase == "idle" then return "cleared" end
    pcall(project_particle, active, report.particle)
    pcall(project_sound, active, report, profile.pitch or 1)
    pcall(project_fog, active, report.fog)
    return "projected"
end

function ambience.sync(scope, epoch)
    if not active then return end
    if not current(active, scope, epoch) or scope.statics:GetRealTimeSeconds(scope.world) >= active.expires then
        ambience.clear()
    else
        pcall(suppress_stock, active)
    end
end

function ambience.rows(row)
    if not active then return {} end
    local report, fog = active.report, active.fog_readback
    if report.phase == "idle" then return {row("native-weather-ambience-guest", active.epoch, "idle")} end
    local result = {row("native-weather-ambience-guest", active.epoch, report.phase, report.variant, report.level, report.blend,
        report.particle, report.sound, report.volume, valid(active.particle) and 1 or 0, valid(active.audio) and 1 or 0,
        fog and valid(active.post) and active.post.bEnabled and 1 or 0)}
    if fog and valid(active.post) and active.post.bEnabled then
        result[#result + 1] = row("native-weather-ambience-guest-fog", active.epoch, report.variant, fog.material,
            fog.density, fog.start, fog.r, fog.g, fog.b, fog.a)
    end
    return result
end

return ambience
