-- Run from the repository root with the pinned Lua CLI.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local map, first, second = "/Game/Classes/Maps/Test", "Level_Exit_BP_C_1", "Level_Exit_BP_C_2"
local auth, receipts, walls = false, {}, {}
local function point(x, y, z) return {X = x or 0, Y = y or 0, Z = z or 0} end
local function object(methods)
    methods.IsValid = function(self) return not self.destroyed end
    methods.GetAddress = function(self) return self end
    return methods
end

os.getenv = function(name)
    if name == "SILVERFISH_COOP_NATIVE_ZONE_WALLS" or name == "SILVERFISH_COOP_NATIVE_EXPANDED_ZONES" then return "1" end
end
package.loaded.zone_maps = {[map] = {}}
package.loaded.authored_wall_routes = {[map] = {
    {actor = first, entrance = "One", kind = "overlap"}, {actor = second, entrance = "Two", kind = "overlap"},
}}
package.loaded.zone_regroup = {clear = function() end, show = function() end, rows = function(rows) return rows end}

local world = object({})
local function shape(actor, center)
    return object({
        extent = point(20, 100, 100),
        GetOwner = function() return actor end,
        K2_GetComponentLocation = function() return center end,
        K2_GetComponentRotation = function() return {Pitch = 0, Yaw = 0, Roll = 0} end,
        GetForwardVector = function() return point(1) end,
        GetRightVector = function() return point(0, 1) end,
        GetUpVector = function() return point(0, 0, 1) end,
        GetScaledBoxExtent = function(self) return self.extent end,
        SetBoxExtent = function(self, extent) self.extent = extent end,
        SetCollisionEnabled = function() end,
        SetGenerateOverlapEvents = function() end,
        SetCollisionObjectType = function() end,
        SetCollisionResponseToAllChannels = function() end,
        SetCollisionResponseToChannel = function() end,
        GetCollisionEnabled = function() return 1 end,
        GetCollisionObjectType = function() return 0 end,
        GetCollisionResponseToChannel = function() return 2 end,
    })
end

local function actor()
    return object({
        GetWorld = function() return world end,
        IsActorBeingDestroyed = function() return false end,
        HasAuthority = function() return true end,
    })
end

function world:SpawnActor(_, center)
    local clone = actor()
    clone.bReplicates = false
    clone.CollisionComponent = shape(clone, center)
    clone.RootComponent = clone.CollisionComponent
    clone.SetActorEnableCollision = function() end
    clone.SetActorHiddenInGame = function() end
    clone.K2_DestroyActor = function(self) self.destroyed = true end
    walls[center.X] = clone
    return clone
end

local function exit(name, tag, x)
    local placed = actor()
    placed["Entrance Tag"] = tag
    placed.GetFullName = function() return "Level_Exit_BP_C " .. map .. ".Test:PersistentLevel." .. name end
    placed.Box = shape(placed, point(x))
    return placed
end
local exits = {exit(first, "One", 0), exit(second, "Two", 2000)}
FindAllOf = function(class) assert(class == "Level_Exit_BP_C"); return exits end
StaticFindObject = function(class) assert(class == "/Script/Engine.TriggerBox"); return object({}) end
SilverfishSetNativeExitApproach = function(exit_name)
    receipts[#receipts + 1] = exit_name
    return auth and exit_name ~= ""
end

local function pawn(x)
    local player = actor()
    player.at, player.ignores = point(x), {}
    player.K2_GetActorLocation = function(self) return self.at end
    player.CapsuleComponent = object({
        GetCollisionObjectType = function() return 2 end,
        GetCollisionResponseToChannel = function() return 2 end,
        IgnoreActorWhenMoving = function(_, wall, ignore) player.ignores[wall] = ignore end,
    })
    return player
end

local host, guest = pawn(900), pawn(1400)
local state = {world = world, map = map, local_pawn = {pawn = host}, clients = 1,
    pawns = {{kind = "guest", pawn = guest}}}
local wall = require("zone_wall")
local function tick(x)
    host.at = point(x)
    wall.update(function() return world end)
end

wall.sync(state, "hosting", nil, true)
auth = true
tick(900)
assert(#receipts == 0 and not host.ignores[walls[0]] and not host.ignores[walls[2000]], "distant walls remain closed")
tick(-140)
assert(receipts[#receipts] == first and host.ignores[walls[0]] and not host.ignores[walls[2000]], "only exact wall opens")
tick(-35)
tick(-120)
assert(receipts[#receipts] == "" and not host.ignores[walls[0]], "a clear retreat closes the wall")
tick(1850)
assert(receipts[#receipts] == second and host.ignores[walls[2000]], "next wall requires its own authorization")
auth = false
tick(1870)
assert(not host.ignores[walls[2000]], "native refusal restores collision")
wall.sync(state, "connected", nil, true)
state.local_pawn.pawn = guest
wall.sync(state, "connected", nil, true)
local previous = #receipts
tick(-35)
assert(#receipts == previous and next(guest.ignores) == nil, "guest never requests or receives an open wall")
wall.clear()
