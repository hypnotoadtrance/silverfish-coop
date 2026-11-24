package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local function object(name)
    local value = {IsValid = function() return true end, GetFullName = function() return name end}
    value.GetAddress = function() return value end
    return value
end

local time, blast = 4, nil
local statics = {IsValid = function() return true end, GetRealTimeSeconds = function() return time end}
local blast_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_GameEffects.FL_GameEffects_C:SpawnExplosion"
function StaticFindObject(path)
    if path == blast_path then return object("Function " .. blast_path) end
    if path == "/Script/Engine.Default__GameplayStatics" then return statics end
end
function RegisterHook(path, callback)
    assert(path == blast_path and not blast)
    blast = callback
end

local world, other_world = object("World Dock"), object("World Observatory")
local state = {world = world, map = "/Game/Classes/Maps/Dock", listening = true}
local position = {X = 123, Y = -54.125, Z = 6.5}
local fx = object("NiagaraSystem /Game/Assets/PFX/FX_Blast_PulseGrenade_Converted.FX_Blast_PulseGrenade_Converted")
local thunder = object("MetaSoundSource /Game/Assets/Sounds/MetaSounds/MS_LightningStrike.MS_LightningStrike")
local function bolt(index, authority, level)
    local actor = object("LightningBolt_BP_C Dock.LightningBolt_BP_C_" .. index)
    actor.GetClass = function()
        return object("BlueprintGeneratedClass /Game/Classes/Actors/LightningBolt_BP.LightningBolt_BP_C")
    end
    actor.HasAuthority = function() return authority end
    actor.GetWorld = function() return level end
    actor.IsActorBeingDestroyed = function() return false end
    actor["Impact Point"] = position
    local light, particle = object("Light"), object("Bolt effect")
    light.GetClass = function() return object("Class /Script/Engine.DirectionalLightComponent") end
    particle.GetClass = function() return object("Class /Script/Niagara.NiagaraComponent") end
    light.GetOwner, particle.GetOwner = function() return actor end, function() return actor end
    light.K2_GetComponentRotation = function() return {Pitch = -4, Yaw = 90.5, Roll = 0} end
    particle.K2_GetComponentLocation = function() return position end
    particle.Asset = object("NiagaraSystem /Game/Assets/PFX/NI_Lightning_Strike.NI_Lightning_Strike")
    actor.DirectionalLight, actor.NI_Lightning_Strike = light, particle
    return actor
end

local function wrapped(value) return {get = function() return value end} end
local function fire(actor, instigator, impact, sound)
    blast(nil, wrapped(impact or position), nil, nil, wrapped(fx), wrapped(1), wrapped(sound or thunder),
        wrapped(1), nil, wrapped(instigator or actor), nil, nil, nil, nil, wrapped(actor))
end

local lightning = require("native_weather_lightning_host")
local function rows() return lightning.rows(state, "hosting", table.pack) end
lightning.sync(state, "connected")
assert(not blast)
lightning.sync(state, "hosting")

local source = bolt(1, true, world)
fire(bolt(2, false, world))
fire(bolt(3, true, other_world))
fire(source, bolt(4, true, world))
fire(source, nil, {X = 124, Y = position.Y, Z = position.Z})
fire(source, nil, nil, object("MetaSoundSource unrelated"))
assert(#rows() == 0)
fire(source)
fire(source)
time = 4.5
local captured = rows()
assert(#captured == 1 and table.concat(captured[1], " ") ==
    "native-weather-lightning 1 2.500 123.000000 -54.125000 6.500000 -4.000000 90.500000 0.000000")

for index = 5, 8 do fire(bolt(index, true, world)) end
captured = rows()
assert(#captured == 4 and captured[1][2] == 2 and captured[4][2] == 5)
time = 8
assert(#rows() == 0)
fire(source)
assert(#rows() == 1)
state.map, state.world = "/Game/Classes/Maps/Observatory", other_world
assert(#rows() == 0)
lightning.sync(state, "hosting")
fire(source)
assert(#rows() == 0)
lightning.sync(state, "connected")
fire(bolt(9, true, other_world))
assert(#rows() == 0)
