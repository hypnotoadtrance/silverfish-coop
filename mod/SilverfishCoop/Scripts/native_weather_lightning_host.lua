local lightning = {}

local bolt_class = "BlueprintGeneratedClass /Game/Classes/Actors/LightningBolt_BP.LightningBolt_BP_C"
local light_class = "Class /Script/Engine.DirectionalLightComponent"
local niagara_class = "Class /Script/Niagara.NiagaraComponent"
local strike_fx = "NiagaraSystem /Game/Assets/PFX/NI_Lightning_Strike.NI_Lightning_Strike"
local blast_fx = "NiagaraSystem /Game/Assets/PFX/FX_Blast_PulseGrenade_Converted.FX_Blast_PulseGrenade_Converted"
local strike_sound = "MetaSoundSource /Game/Assets/Sounds/MetaSounds/MS_LightningStrike.MS_LightningStrike"
local blast_function = "/Game/Classes/Libraries/Fuctions_Lib/FL_GameEffects.FL_GameEffects_C:SpawnExplosion"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local lifetime, max_strikes, max_serial = 3, 4, 0x7fffffff
local stock_world, map, statics, hooked
local active, next_attempt, serial = {}, 0, 0

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function named(object, name)
    return valid(object) and object:GetFullName() == name
end

local function class_is(object, name)
    return valid(object) and named(object:GetClass(), name)
end

local function bounded(number, limit)
    return type(number) == "number" and math.abs(number) <= limit
end

local function bounded_position(point)
    return point and bounded(point.X, 1e7) and bounded(point.Y, 1e7) and bounded(point.Z, 1e7)
end

local function same_position(first, second)
    return bounded_position(first) and bounded_position(second) and math.abs(first.X - second.X) <= .02
        and math.abs(first.Y - second.Y) <= .02 and math.abs(first.Z - second.Z) <= .02
end

local function prune(now)
    while active[1] and active[1].deadline <= now do table.remove(active, 1) end
end

local function observe(actor, causer, point, particle, sound, scale, volume)
    if not stock_world or serial >= max_serial or scale ~= 1 or volume ~= 1
        or not class_is(actor, bolt_class) or not same(actor, causer) or actor:IsActorBeingDestroyed()
        or not actor:HasAuthority() or not same(actor:GetWorld(), stock_world)
        or not named(particle, blast_fx) or not named(sound, strike_sound)
        or not same_position(point, actor["Impact Point"]) then return end

    local light, bolt = actor.DirectionalLight, actor.NI_Lightning_Strike
    if not class_is(light, light_class) or not same(light:GetOwner(), actor)
        or not class_is(bolt, niagara_class) or not same(bolt:GetOwner(), actor) or not named(bolt.Asset, strike_fx)
        or not same_position(point, bolt:K2_GetComponentLocation()) then return end
    local rotation = light:K2_GetComponentRotation()
    if not rotation or not bounded(rotation.Pitch, 360) or not bounded(rotation.Yaw, 360)
        or not bounded(rotation.Roll, 360) then return end

    local now = statics:GetRealTimeSeconds(stock_world)
    local source = actor:GetFullName()
    prune(now)
    for _, strike in ipairs(active) do
        if strike.source == source then return end
    end
    serial = serial + 1
    active[#active + 1] = { serial = serial, source = source, deadline = now + lifetime,
        x = point.X, y = point.Y, z = point.Z, pitch = rotation.Pitch, yaw = rotation.Yaw, roll = rotation.Roll }
    if #active > max_strikes then table.remove(active, 1) end
end

function lightning.sync(state, phase)
    local host = phase == "hosting" and state.listening and not state.server and valid(state.world)
    if not host or not same(state.world, stock_world) or state.map ~= map then
        stock_world, map, active, next_attempt = nil, nil, {}, 0
        if not host then return end
        statics = StaticFindObject(statics_path)
        if not valid(statics) then return end
        stock_world, map = state.world, state.map
    end
    if hooked then return end
    local now = statics:GetRealTimeSeconds(stock_world)
    if now < next_attempt then return end
    next_attempt = now + 1
    if not valid(StaticFindObject(blast_function)) then return end
    RegisterHook(blast_function, function(_, location, _, _, particle, scale, sound, volume, _, causer, _, _, _, _, context)
        if not stock_world then return end
        pcall(function()
            observe(context:get(), causer:get(), location:get(), particle:get(), sound:get(), scale:get(), volume:get())
        end)
    end)
    hooked = true
end

function lightning.rows(state, phase, row)
    if phase ~= "hosting" or not state.listening or state.server or map ~= state.map or not same(stock_world, state.world)
        or #active == 0 then return {} end
    local now = statics:GetRealTimeSeconds(stock_world)
    prune(now)
    local rows = {}
    for _, strike in ipairs(active) do
        local remaining = strike.deadline - now
        if remaining >= .001 then
            rows[#rows + 1] = row("native-weather-lightning", strike.serial, string.format("%.3f", remaining),
                string.format("%.6f", strike.x), string.format("%.6f", strike.y), string.format("%.6f", strike.z),
                string.format("%.6f", strike.pitch), string.format("%.6f", strike.yaw), string.format("%.6f", strike.roll))
        end
    end
    return rows
end

return lightning
