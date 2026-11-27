package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
package.loaded.guest_save = {}

local now = 0
os.clock = function() return now end
local function object(fields)
    fields = fields or {}
    fields.IsValid = function() return true end
    fields.GetAddress = function(self) return self end
    return fields
end

local world = object()
local foreign_world = object()
local critter_class, marker_class = object(), object()
local paths = {
    ["/Script/Engine.TargetPoint"] = marker_class,
    ["/Script/Engine.Default__GameplayStatics"] = object(),
    ["/Game/Classes/Ambiant_FX/Critter_Master_BP.Critter_Master_BP_C"] = critter_class,
}
function StaticFindObject(path) return paths[path] end
local candidates = {}
function FindAllOf(name) assert(name == "TargetPoint"); return candidates end
local owner_changed, ended
function RegisterHook(path, callback)
    assert(path == "/Script/Engine.Actor:OnRep_Owner")
    owner_changed = callback
end
function RegisterBeginPlayPostHook() end
function RegisterEndPlayPreHook(callback) ended = callback end

local function critter(path)
    local source = object({world = world, role = 1, Health = 10})
    source.GetWorld = function(self) return self.world end
    source.GetLocalRole = function(self) return self.role end
    source.IsActorBeingDestroyed = function() return false end
    source.IsA = function(_, type) return type == critter_class end
    source.GetClass = function()
        return {GetFName = function() return {ToString = function() return "Critter_Frog_BP_C" end} end}
    end
    paths[path] = object({GetOwner = function() return source end})
    return source
end

local function marker(owner, number)
    return object({
        world = world, role = 1, owner = owner, number = number,
        GetWorld = function(self) return self.world end,
        GetLocalRole = function(self) return self.role end,
        HasAuthority = function() return false end,
        IsActorBeingDestroyed = function() return false end,
        GetClass = function() return marker_class end,
        GetOwner = function(self) return self.owner end,
        K2_GetActorLocation = function(self) return {Z = self.number and 327680 + self.number or 0} end,
    })
end

local stock = require("native_critters")
local state = {world = world}
local pawn = object({GetWorld = function() return state.world end})
local function sync(at)
    now = at
    stock.sync(state, "connected")
end
local function event(callback, actor)
    callback({get = function() return actor end})
end
local function focused(path)
    return stock.focused_target(pawn, path)
end

-- The initial census may see the ID before the owner NetGUID is available.
local first = critter("first-collision")
first.role, first.Health = 0, nil
local existing = marker(nil, 17)
candidates = {existing}
sync(0)
assert(not focused("first-collision"))
existing.owner = first
sync(.14)
assert(not focused("first-collision"))
first.role, first.Health = 1, 10
sync(.28)
assert(focused("first-collision") == "stock-critter:17")

-- An owner change can arrive while the marker still reports its default pose.
local second = critter("second-collision")
local replicated = marker(second)
replicated.role = 0
event(owner_changed, replicated)
sync(.3)
assert(not focused("second-collision"))
replicated.role, replicated.number = 1, 23
sync(.44)
assert(focused("second-collision") == "stock-critter:23")

local third = critter("expired-collision")
local expired = marker(third)
event(owner_changed, expired)
sync(.5)
expired.number = 31
sync(13)
assert(not focused("expired-collision"))

local fourth = critter("ended-collision")
local removed = marker(fourth)
event(owner_changed, removed)
sync(13.1)
event(ended, removed)
removed.number = 37
sync(13.3)
assert(not focused("ended-collision"))

local fifth = critter("foreign-collision")
local outside = marker(fifth, 41)
outside.world = foreign_world
event(owner_changed, outside)
sync(13.4)
assert(not focused("foreign-collision"))

-- An old marker with a valid numeric payload cannot bind in the next world.
local sixth = critter("travel-collision")
local departed = marker(sixth)
event(owner_changed, departed)
sync(13.6)
candidates = {departed}
state.world = foreign_world
departed.number = 47
sync(13.8)
assert(not focused("travel-collision"))

print("Critter marker discovery survives delayed owner, role and movement, and expires outside its visit")
