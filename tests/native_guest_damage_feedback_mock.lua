package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local next_address = 0
local function object(properties)
    local result = properties or {}
    next_address = next_address + 1
    local address = next_address
    function result:IsValid() return true end
    function result:GetAddress() return address end
    return result
end

local function param(value)
    return {get = function(self) return self.value end, set = function(self, changed) self.value = changed end,
        value = value}
end

local function name(value)
    return object({GetFullName = function() return value end})
end

local area, hooks = object(), {}
local save = object({PlayerHealth = 100})
local guest = name("Player_FP_C /Game/Fixture.Guest")
guest.Save = save
function guest:GetWorld() return area end
function guest:IsLocallyControlled() return false end

local host = name("Player_FP_C /Game/Fixture.Host")
local host_save = object({PlayerHealth = 99})
host.Save = host_save
function host:GetWorld() return area end
function host:IsLocallyControlled() return true end

local other_guest = name("Player_FP_C /Game/Fixture.OtherGuest")
other_guest.Save = object({PlayerHealth = 100})
other_guest.GetWorld = guest.GetWorld
other_guest.IsLocallyControlled = guest.IsLocallyControlled

local effect = object({Player = guest})
local class = name("BlueprintGeneratedClass /Game/Classes/Player/PlayerAssets/PPActor_Damage_BP.PPActor_Damage_BP_C")
function effect:GetClass() return class end
function effect:GetWorld() return area end
effect.PostProcess_FX = object({bEnabled = true, GetOwner = function() return effect end})
effect.PostProcessDirection = object({bEnabled = true, GetOwner = function() return effect end})
guest.DmgFX = effect

package.loaded.guest_save = {matches = function(pawn) return pawn == guest or pawn == other_guest end}
package.loaded.native_save_scope, package.loaded.native_voice = {}, {}
function StaticFindObject() return object() end
function RegisterHook(path, callback) hooks[path] = callback end
function RegisterBeginPlayPreHook() end
function RegisterBeginPlayPostHook() end

local feedback = require("native_damage_feedback")
local hosting = true
feedback.start(function() return hosting end)
local state = {world = area, guests = {{pawn = guest}, {pawn = other_guest}}}
feedback.sync(state, "hosting")
local hit_sound = hooks["/Script/Engine.GameplayStatics:PlaySound2D"]
local pulse = hooks["/Game/Classes/Player/Player_FP.Player_FP_C:Pulse_Dmg_FX"]
local burn = param(name("SoundCue /Game/Assets/Sounds/CUEs/PhysImpact_Cues/BurnDmg_Cue.BurnDmg_Cue"))
local other = param(name("SoundCue /Game/Fixture.Other"))
local function volume(pawn, sound)
    local value = param(1)
    hit_sound(nil, param(pawn), sound, value)
    return value:get()
end
assert(volume(guest, burn) == 0 and volume(host, burn) == 1 and volume(guest, other) == 1)

local fire = param({Fire_12_2B415A724C533C075B6BB390B76BF247 = 10})
local direction = param({X = 72.5, Y = -24, Z = 8})
save.PlayerHealth = 90
pulse(param(guest), param(10), direction, nil, fire)
pulse(param(guest), param(10), direction, nil, fire)
local function rows() return feedback.rows(state, "hosting", function(...) return {...} end) end
assert(#rows() == 1 and rows()[1][3] == guest:GetFullName() and rows()[1][8] == 4)
local first = rows()[1]
assert(first[5] == 72.5 and first[6] == -24 and first[7] == 8)
assert(not effect.PostProcess_FX.bEnabled and not effect.PostProcessDirection.bEnabled)
save.PlayerHealth = 80
pulse(param(guest), param(10), direction, nil, fire)
assert(#rows() == 2 and host_save.PlayerHealth == 99)

for damage = 1, 13 do
    other_guest.Save.PlayerHealth = 100 - damage
    pulse(param(other_guest), param(1), direction, nil, fire)
end
local by_victim = {}
for _, row in ipairs(rows()) do
    by_victim[row[3]] = (by_victim[row[3]] or 0) + 1
end
assert(by_victim[guest:GetFullName()] == 2 and by_victim[other_guest:GetFullName()] == 12)
state.guests = {{pawn = guest}}
feedback.sync(state, "hosting")
assert(#rows() == 2)

hosting = false
feedback.sync(state, "ready")
assert(volume(guest, burn) == 1 and #rows() == 0)
