-- Run with Lua 5.4 from the repository root.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
local effects = require("native_anomaly_effects")

local function asset(name)
    return {IsValid = function() return true end, GetFullName = function() return name end}
end

local point = {X = -8994.248, Y = 8083.980, Z = 67.953}
local starburst = {IsValid = function() return true end, GetAddress = function() return 1 end}
local blast = {
    HasAuthority = function() return true end,
    K2_GetActorLocation = function() return point end,
    PFX = asset("NiagaraSystem /Game/Assets/PFX/FX_Blast_FragGrenade_Converted.FX_Blast_FragGrenade_Converted"),
    SFX = asset("MetaSoundSource /Game/Assets/Sounds/MetaSounds/MS_Grenade01.MS_Grenade01"),
    ["Attenuation Settings"] = asset("SoundAttenuation /Game/Assets/Sounds/SoundClasses/Blast_Attenuation.Blast_Attenuation"),
    ["PFX Scale"] = 1,
    ["SFX Vol"] = 1,
    ["Actors to Ignore"] = {GetArrayNum = function() return 1 end},
    ImpulseStrength = 800,
}

local function row(...) return {...} end
local function source(location, cue)
    return location == point and cue == "fueldrum"
end

effects.observe_blast(blast, {}, source)
local rows = effects.rows(row)
assert(rows[1][7] == 1 and rows[3][3] == "fueldrum", "One stock ignored slot does not exclude a drum blast")

effects.clear()
effects.observe_blast(blast, {}, function() return false end)
local pfx = blast.PFX
blast.PFX = asset("NiagaraSystem /Other/Particle.Particle")
effects.observe_blast(blast, {}, source)
blast.PFX = pfx
assert(effects.rows(row)[1][7] == 0, "The stock impulse alone cannot identify a drum")

blast.ImpulseStrength = 1200
blast["Actors to Ignore"][1] = starburst
local active = {[1] = {kind = "starburst", actor = starburst}}
effects.observe_blast(blast, active, source)
rows = effects.rows(row)
assert(rows[1][7] == 0 and rows[1][3] == 1 and rows[3][3] == "blast",
    "A Starburst sharing the same assets retains its own cue")
