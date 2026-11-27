package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local time, searches, played = 10, 0, {}
local created
local objects, ambient = {}, {}
local function object(properties)
    properties.IsValid = function(self) return not self.removed end
    properties.GetAddress = function(self) return self.address end
    return properties
end

local world = object({address = 1})
local npc_class = object({address = 2})
local marker_class = object({address = 3})
local skeleton = object({address = 4})
local stock_sound = object({address = 5})
local clip = "/Game/Animations/TestSwing.TestSwing"
objects["/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"] = npc_class
objects["/Script/Engine.AmbientSound"] = marker_class
objects["/Script/Engine.Default__GameplayStatics"] = object({GetRealTimeSeconds = function() return time end})
objects[clip] = object({
    Skeleton = skeleton,
    GetFullName = function() return "AnimSequence " .. clip end,
    GetPlayLength = function() return 1 end,
})
function StaticFindObject(path) return objects[path] end
function FindAllOf(class)
    assert(class == "AmbientSound")
    searches = searches + 1
    return ambient
end
function NotifyOnNewObject(path, callback)
    assert(path == "/Script/Engine.AmbientSound")
    created = callback
end
function FName(name) return name end
EObjectFlags = {RF_WasLoaded = 1}

local owner = object({
    address = 6, Alive = true, bReplicates = true,
    IsA = function(_, class) return class == npc_class end,
    IsActorBeingDestroyed = function() return false end,
    GetWorld = function() return world end,
    GetLocalRole = function() return 1 end,
})
local animator = object({
    PlaySlotAnimationAsDynamicMontage = function(_, animation, slot, _, _, rate, _, _, offset)
        assert(animation == objects[clip] and slot == "DefaultSlot" and rate == 1 and offset >= 0)
        played[#played + 1] = offset
        return object({})
    end,
})
owner.Mesh = object({
    SkinnedAsset = object({Skeleton = skeleton}),
    GetOwner = function() return owner end,
    GetAnimInstance = function() return animator end,
})

local function marker(id, actor, sound, placed)
    local item = object({
        address = 100 + #ambient,
        marker_id = id,
        owner = actor,
        AudioComponent = object({Sound = sound}),
        GetClass = function() return marker_class end,
        GetWorld = function() return world end,
        GetLocalRole = function() return 1 end,
        GetOwner = function(self) return self.owner end,
        HasAnyFlags = function(_, flags) assert(flags == EObjectFlags.RF_WasLoaded); return placed end,
        K2_GetActorRotation = function(self) return {Yaw = self.marker_id * 360 / 256, Roll = 0} end,
    })
    ambient[#ambient + 1] = item
    return item
end

local melee = require("native_npc_melee_view")
local state = {world = world, server = true}
local function cue(id) assert(melee.offer(world, id, clip, 1, .1, .1)) end
local function advance(seconds)
    time = time + seconds
    melee.update(function() return world end)
end

melee.sync(state, "connected")
assert(created)
cue(7)
local late = marker(0, nil)
created(late)
advance(.01)
assert(searches == 1 and #played == 0)
late.owner = owner
advance(.06)
assert(searches == 1 and #played == 0)
late.marker_id = 7
advance(.06)
assert(searches == 1 and #played == 1)
cue(7)
assert(searches == 1 and #played == 2, "the next cue should use the cached marker")

local early = marker(8, owner)
created(early)
cue(8)
advance(.06)
assert(searches == 1 and #played == 3)

marker(9, owner, stock_sound)
marker(9, owner, nil, true)
cue(9)
for _ = 1, 5 do advance(.06) end
assert(searches == 3 and #played == 3, "unqualified ambient actors cannot satisfy the cue")
marker(9, owner)
advance(.06)
advance(.06)
advance(.06)
assert(#played == 4 and searches == 4, "a missed creation notification must still recover")

cue(10)
created(marker(10, owner))
advance(.61)
assert(#played == 4, "an expired cue may never play")

package.loaded.native_npc_melee_view = nil
NotifyOnNewObject = nil
melee = require("native_npc_melee_view")
melee.sync(state, "connected")
cue(11)
advance(.01)
local first_fallback = searches
marker(11, owner)
advance(.06)
assert(searches == first_fallback + 1 and #played == 5, "the legacy UE4SS search cadence is preserved")
