package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local stock = require("native_storm_ambience_stock")
local ambience = require("native_storm_ambience_host")
local function variant(name)
    return ("/Game/Classes/Weather/Storms/%s.%s_C"):format(name, name)
end

local function object(name, class, owner)
    local value = {name = name, class = class, owner = owner}
    function value:IsValid() return true end
    function value:GetFullName() return self.name end
    function value:GetClass() return object(self.class) end
    function value:GetOwner() return self.owner end
    function value:GetAddress() return self end
    return value
end

FName = function(name) return name end
EFindName = {FNAME_Find = 0}
local world = object("World stock")
local current = {world = world, day = {}}
local function capture()
    local rows = {}
    ambience.append(rows, ambience.capture(current), table.pack)
    return rows
end

assert(table.concat(capture()[1], " ") == "native-weather-ambience idle")
local function storm_for(name, level)
    local path = variant(name)
    local profile = assert(stock[path])
    local storm = object("Storm", "BlueprintGeneratedClass " .. path)
    storm.StormLevel, storm.BlendAlpha, storm.Fading, storm.FirstActivation = level, .75, false, true
    storm["Fade in Duration"] = 4
    storm.HasAuthority, storm.IsActorBeingDestroyed = function() return true end, function() return false end
    storm.GetWorld = function() return world end
    if profile.particle then
        storm.Ambiant_PFX = object("Storm Niagara", "Class /Script/Niagara.NiagaraComponent", storm)
        storm.Ambiant_PFX.Asset = object("NiagaraSystem " .. profile.particle)
    end
    if profile.sound then
        storm.Ambiant_SFX = object("Storm audio", "Class /Script/Engine.AudioComponent", storm)
        storm.Ambiant_SFX.Sound = object("SoundWave " .. profile.sound)
        storm.Ambiant_SFX.VolumeMultiplier = .8
    end
    return storm, profile
end

local storm, profile = storm_for("LightningStorm_BP", 0)
current.day.LocalStorm = storm
local rows = capture()
assert(#rows == 1 and rows[1].n == 9 and rows[1][2] == "active" and rows[1][3] == variant("LightningStorm_BP"))
assert(rows[1][4] == 0 and rows[1][5] == .75 and rows[1][6] == 4)
assert(rows[1][7] == profile.particle and rows[1][8] == profile.sound and rows[1][9] == .8)
storm.StormLevel = 2
rows = capture()
assert(rows[1][7] == "-" and rows[1][9] == .4)
storm.FirstActivation = false
rows = capture()
assert(rows[1][6] == .3 and rows[1][9] == .8)
storm.StormLevel, storm.Fading = 0, true
rows = capture()
assert(rows[1][2] == "fading" and rows[1][6] == 4 and rows[1][7] == "-")
storm.Ambiant_SFX.owner = object("another actor's audio")
rows = capture()
assert(rows[1][8] == "-" and rows[1][9] == 0)

local flare, flare_profile = storm_for("EatherFlare_BP", 0)
current.day.LocalStorm = flare
local fog = object("runtime plume material", "Class /Script/Engine.MaterialInstanceDynamic")
local original = object("Material " .. flare_profile.fog)
fog.Parent = original
function fog:K2_GetScalarParameterValue(name) return assert(({Density = .073, StartDistance = 50000})[name]) end
function fog:K2_GetVectorParameterValue(name) assert(name == "Color"); return {R = 1, G = 0, B = .017, A = 1} end
flare.CustomFogMat, flare.CustomFogMaterial = fog, original
flare.PostProcess = object("Storm post effect", "Class /Script/Engine.PostProcessComponent", flare)
flare.PostProcess.bEnabled, flare.PostProcess.bUnbound = true, true
local blendable = {Weight = 1, Object = fog}
flare.PostProcess.Settings = {WeightedBlendables = {Array = {blendable, GetArrayNum = function() return 1 end}}}
rows = capture()
assert(#rows == 2 and rows[2].n == 9 and rows[2][1] == "native-weather-ambience-fog")
assert(rows[2][2] == variant("EatherFlare_BP") and rows[2][3] == flare_profile.fog and rows[2][4] == .073
    and rows[2][5] == 50000 and rows[2][6] == 1 and rows[2][7] == 0 and rows[2][8] == .017 and rows[2][9] == 1)
blendable.Object = object("foreign material")
assert(#capture() == 1)
current.world = object("World next visit")
assert(table.concat(capture()[1], " ") == "native-weather-ambience idle")
