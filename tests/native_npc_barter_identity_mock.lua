package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

EObjectFlags = {RF_WasLoaded = 1}
package.loaded.replicated_actor_reference = {class = "target-point", limit = 0xFFFF}
local identity = require("native_npc_barter_identity")

local begin_play
function RegisterHook() end
function RegisterBeginPlayPostHook(callback) begin_play = callback end
function RegisterEndPlayPreHook() end

local function object(fields)
    function fields:IsValid() return true end
    function fields:GetAddress() return self end
    return fields
end

local world = object({})
local marker_class = object({})
local statics = object({})
local class_lookups = 0
function StaticFindObject(path)
    if path == "target-point" then return marker_class end
    if path == "/Script/Engine.Default__GameplayStatics" then return statics end
    class_lookups = class_lookups + 1
end

local function actor_class(name, parent)
    return object({GetFullName = function() return name end, GetSuperStruct = function() return parent end})
end

local npc_base = actor_class("BlueprintGeneratedClass /Game/Classes/NPCs/NPC_Drones/NPC_Drone.NPC_Drone_C")
local drone_class = actor_class("BlueprintGeneratedClass /Example/LateDrone.LateDrone_C", npc_base)
local late_drone = object({
    Alive = true, bReplicates = true,
    GetClass = function() return drone_class end,
    GetWorld = function() return world end,
    HasAuthority = function() return true end,
    HasAnyFlags = function() return false end,
    IsActorBeingDestroyed = function() return false end,
})
local unrelated_class = actor_class("Class /Script/Engine.Actor")
local unrelated = object({GetClass = function() return unrelated_class end})

local spawned = 0
package.loaded.replicated_actor_reference.spawn = function(in_world, class, kind, owner, number)
    assert(in_world == world and class == marker_class and kind == "barter" and owner == late_drone and number == 1)
    spawned = spawned + 1
    return object({
        GetWorld = function() return world end,
        GetOwner = function() return owner end,
        IsActorBeingDestroyed = function() return false end,
    })
end

local state = {world = world}
identity.sync(state, "hosting")
assert(class_lookups == 3 and spawned == 0)
local function notify(actor)
    begin_play({get = function() return actor end})
end
notify(unrelated)
notify(late_drone)
identity.sync(state, "hosting")
assert(class_lookups == 3 and spawned == 1)
assert(identity.key(late_drone) == "5346434F4F5042415254455200000001")
assert(identity.key(unrelated) == nil)
