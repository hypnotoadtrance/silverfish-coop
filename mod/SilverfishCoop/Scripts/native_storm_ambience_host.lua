local ambience = {}
local stock = require("native_storm_ambience_stock")

local niagara = "Class /Script/Niagara.NiagaraComponent"
local audio = "Class /Script/Engine.AudioComponent"
local post_process = "Class /Script/Engine.PostProcessComponent"
local material_instance = "Class /Script/Engine.MaterialInstanceDynamic"

local function valid(object)
    return object and object:IsValid()
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

local function asset_path(object)
    return valid(object) and object:GetFullName():match("^%S+ (%S+)$") or nil
end

local function bounded(value, maximum)
    return type(value) == "number" and value >= 0 and value <= maximum
end

local function custom_fog(storm, expected)
    if not expected then return end
    local post, material, authored = storm.PostProcess, storm.CustomFogMat, storm.CustomFogMaterial
    if not owned(post, storm, post_process) or not post.bEnabled or not post.bUnbound
        or not exact(material, material_instance) or asset_path(authored) ~= expected or not same(material.Parent, authored) then
        return
    end
    local blendables = post.Settings.WeightedBlendables.Array
    if blendables:GetArrayNum() ~= 1 then return end
    local blendable = blendables[1]
    if blendable.Weight ~= 1 or not same(blendable.Object, material) then return end

    local density = material:K2_GetScalarParameterValue(FName("Density", EFindName.FNAME_Find))
    local distance = material:K2_GetScalarParameterValue(FName("StartDistance", EFindName.FNAME_Find))
    local color = material:K2_GetVectorParameterValue(FName("Color", EFindName.FNAME_Find))
    if not bounded(density, 1) or not bounded(distance, 100000) or not color
        or not bounded(color.R, 1) or not bounded(color.G, 1) or not bounded(color.B, 1) or not bounded(color.A, 1) then return end
    return {material = expected, density = density, distance = distance, r = color.R, g = color.G, b = color.B, a = color.A}
end

function ambience.capture(current)
    local storm = current.day.LocalStorm
    if not valid(storm) or not storm:HasAuthority() or storm:IsActorBeingDestroyed()
        or not same(storm:GetWorld(), current.world) then return {phase = "idle"} end
    local variant = asset_path(storm:GetClass())
    local profile = stock[variant]
    local level, blend, fade = math.tointeger(storm.StormLevel), storm.BlendAlpha, storm["Fade in Duration"]
    local fading, first = storm.Fading, storm.FirstActivation
    if not profile or not bounded(level, 3) or not bounded(blend, 1) or not bounded(fade, 60)
        or type(fading) ~= "boolean" or type(first) ~= "boolean" then return {phase = "idle"} end

    local particle, sound, volume = "-", "-", 0
    if profile.particle and not fading and level ~= 2 then
        local emitter = storm.Ambiant_PFX
        -- StormMaster enables this emitter on start, except underground, and disables it as the storm fades.
        if owned(emitter, storm, niagara) and asset_path(emitter.Asset) == profile.particle then
            particle = profile.particle
        end
    end
    if profile.sound then
        local source = storm.Ambiant_SFX
        -- The observer follows the stock playback envelope even if the host has audio disabled.
        if owned(source, storm, audio) and asset_path(source.Sound) == profile.sound
            and bounded(source.VolumeMultiplier, 2) then
            sound, volume = profile.sound, source.VolumeMultiplier * (first and level == 2 and .5 or 1)
        end
    end
    local fog_ok, fog = pcall(custom_fog, storm, profile.fog)
    return {phase = fading and "fading" or "active", variant = variant, level = level, blend = blend,
        fade = (fading or first) and fade or .3, particle = particle, sound = sound, volume = volume,
        fog = fog_ok and fog or nil}
end

function ambience.append(rows, report, row)
    if not report then return end
    if report.phase == "idle" then
        rows[#rows + 1] = row("native-weather-ambience", "idle")
        return
    end
    rows[#rows + 1] = row("native-weather-ambience", report.phase, report.variant, report.level,
        report.blend, report.fade, report.particle, report.sound, report.volume)
    local fog = report.fog
    if fog then
        rows[#rows + 1] = row("native-weather-ambience-fog", report.variant, fog.material,
            fog.density, fog.distance, fog.r, fog.g, fog.b, fog.a)
    end
end

return ambience
