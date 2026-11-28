-- Run from the repository root with the pinned Lua CLI.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local map, first, second = "/Game/Classes/Maps/Test", "Level_Exit_BP_C_1", "Level_Exit_BP_C_2"
local auth, receipts, walls = false, {}, {}
local now, shape_reads, lua_position_reads, native_position_reads = 10, 0, 0, 0
os.clock = function() return now end
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

local world = object({GetFullName = function() return "World " .. map .. ".Test" end})
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
StaticFindObject = function(path)
    if path == "/Script/Engine.TriggerBox" then return object({}) end
    for _, placed in ipairs(exits) do
        if placed:GetFullName() == "Level_Exit_BP_C " .. path then return placed end
    end
end
local function track_source(placed)
    local tag = placed["Entrance Tag"]
    placed["Entrance Tag"] = nil
    setmetatable(placed, {__index = function(_, key)
        if key == "Entrance Tag" then
            shape_reads = shape_reads + 1
            return tag
        end
    end})
end
for _, placed in ipairs(exits) do track_source(placed) end
SilverfishSetNativeExitApproach = function(exit_name)
    receipts[#receipts + 1] = exit_name
    return auth and exit_name ~= ""
end

local function pawn(x)
    local player = actor()
    player.at, player.ignores = point(x), {}
    player.K2_GetActorLocation = function(self)
        lua_position_reads = lua_position_reads + 1
        return self.at
    end
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
local position_scope = "current"
local function read_approach_position(world_address, pawn_address)
    assert(world_address == world:GetAddress() and pawn_address == host:GetAddress())
    native_position_reads = native_position_reads + 1
    if position_scope == "stale" then return end
    if position_scope == "invalid" then return 0 / 0, 0, 0 end
    return host.at.X, host.at.Y, host.at.Z
end
SilverfishReadNativeExitApproachPosition = read_approach_position
local wall = require("zone_wall")
local function tick(x)
    host.at = point(x)
    wall.update(function() return world end)
end

wall.sync(state, "hosting", nil, true)
local after_host_setup = shape_reads
wall.sync(state, "hosting", nil, true)
assert(shape_reads == after_host_setup, "A settled host should reuse verified geometry")

local certificates, certified = 0, true
SilverfishCertifyNativeAuthoredOverlapWall = function(world_address, actor_address, name, entrance)
    assert(world_address == world:GetAddress())
    local expected = name == first and exits[1] or name == second and exits[2]
    assert(expected and actor_address == expected:GetAddress()
        and entrance == (name == first and "One" or "Two"))
    certificates = certificates + 1
    if certified then return true end
end
local before_clearance = lua_position_reads
now = now + .6
wall.sync(state, "hosting", nil, true, nil, true)
assert(certificates == 2 and shape_reads == after_host_setup and lua_position_reads == before_clearance,
    "Certified source and party observations skip Lua tag and position reads")
certified = false
now = now + .6
wall.sync(state, "hosting", nil, true)
assert(certificates == 4 and shape_reads > after_host_setup and lua_position_reads == before_clearance + 2,
    "A declined source or roster observation uses the exact Lua checks")
SilverfishCertifyNativeAuthoredOverlapWall = nil

auth = true
local stock_reads_before = lua_position_reads
tick(900)
assert(#receipts == 0 and not host.ignores[walls[0]] and not host.ignores[walls[2000]], "distant walls remain closed")
tick(-140)
assert(receipts[#receipts] == first and host.ignores[walls[0]] and not host.ignores[walls[2000]], "only exact wall opens")
assert(native_position_reads == 2 and lua_position_reads == stock_reads_before, "native polls avoid reflected position reads")
position_scope = "stale"
tick(-35)
tick(-120)
assert(receipts[#receipts] == "" and not host.ignores[walls[0]], "a clear retreat closes the wall")
assert(lua_position_reads == stock_reads_before + 2, "stale native scope uses the stock position")
position_scope = "invalid"
tick(1850)
assert(receipts[#receipts] == second and host.ignores[walls[2000]], "next wall requires its own authorization")
assert(lua_position_reads == stock_reads_before + 3, "invalid native coordinates use the stock position")
auth = false
SilverfishReadNativeExitApproachPosition = nil
tick(1870)
assert(not host.ignores[walls[2000]], "native refusal restores collision")
assert(lua_position_reads == stock_reads_before + 4, "an unavailable native reader preserves the stock path")
position_scope, SilverfishReadNativeExitApproachPosition = "current", read_approach_position
local approve_approach = SilverfishSetNativeExitApproach
SilverfishSetNativeExitApproach = nil
wall.sync(state, "hosting", nil, true)
assert(host.ignores[walls[2000]], "Legacy scope opens for a nearby guest")
after_host_setup = shape_reads
guest.at = point(4000)
wall.sync(state, "hosting", nil, true)
assert(shape_reads == after_host_setup and not host.ignores[walls[2000]],
    "Host wall closes immediately without rechecking unrelated exit geometry")
guest.at = point(1400)
wall.sync(state, "hosting", nil, true)
assert(shape_reads == after_host_setup and host.ignores[walls[2000]],
    "Host permission reopens a still-qualified wall without scanning the route")
local previous_second = walls[2000]
exits[2].Box = shape(exits[2], point(2000))
guest.at = point(4000)
wall.sync(state, "hosting", nil, true)
assert(shape_reads > after_host_setup and not host.ignores[walls[2000]] and walls[2000] ~= previous_second,
    "A replaced stock box must requalify before setting the new permission")
guest.at = point(1400)
SilverfishSetNativeExitApproach = approve_approach
wall.sync(state, "connected", nil, true)
local after_guest_setup = shape_reads
wall.sync(state, "connected", nil, true)
assert(shape_reads == after_guest_setup, "A settled guest should reuse the verified walls")
state.local_pawn.pawn = guest
wall.sync(state, "connected", nil, true)
assert(shape_reads > after_guest_setup, "A different guest pawn should reverify the walls immediately")
after_guest_setup = shape_reads
local old_wall = walls[0]
old_wall.destroyed = true
wall.sync(state, "connected", nil, true)
assert(shape_reads > after_guest_setup and walls[0] ~= old_wall, "A destroyed wall should be rebuilt immediately")
after_guest_setup = shape_reads
now = now + .6
wall.sync(state, "connected", nil, true)
assert(shape_reads > after_guest_setup, "Settled guest walls should still be rechecked periodically")
local previous = #receipts
tick(-35)
assert(#receipts == previous and next(guest.ignores) == nil, "guest never requests or receives an open wall")
wall.clear()

local seconds, batch_calls, legacy_calls, spawns = 300, 0, 0, 0
os.time = function() return seconds end
local spawn = world.SpawnActor
world.SpawnActor = function(...)
    spawns = spawns + 1
    return spawn(...)
end
world.PersistentLevel = object({})
local sublevel = object({bIsVisible = true})
local streamed = exit(first, "Streamed", 4000)
streamed.GetOuter = function() return sublevel end
streamed.GetParentComponent = function() end
streamed.Box.GetGenerateOverlapEvents = function() return true end
local stream_path = "/Game/Classes/Maps/Stream.Stream:PersistentLevel." .. first
local placed_path = map .. ".Test:PersistentLevel." .. first
local lookup = StaticFindObject
StaticFindObject = function(path)
    return path == stream_path and streamed or lookup(path)
end
SilverfishSnapshotDiscoveredOverlaps = function(address)
    assert(address == world)
    legacy_calls = legacy_calls + 1
    return stream_path .. "\tStreamed\n"
end
SilverfishSnapshotNativeWallOverlapSources = function(address, discover)
    assert(address == world)
    batch_calls = batch_calls + 1
    return {[first] = exits[1]}, discover and {
        [placed_path] = {fixture = exits[1], tag = "One", name = first, persistent = true, kind = "overlap", discovered = true},
        [stream_path] = {fixture = streamed, tag = "Streamed", name = first, kind = "overlap", discovered = true},
    } or nil
end
wall.sync(state, "connected", nil, true)
assert(batch_calls == 1 and legacy_calls == 0 and spawns == 3 and not walls[4000].destroyed,
    "A qualified batch uses two authored walls and one same-named streamed wall without duplicating the persistent source")
local original_stream_wall = walls[4000]
sublevel.bIsVisible = false
wall.sync(state, "connected", nil, true)
assert(original_stream_wall.destroyed and legacy_calls == 0,
    "The wall revokes as soon as its streamed source hides, even inside the discovery cache window")
sublevel.bIsVisible, seconds = true, 301
SilverfishSnapshotNativeWallOverlapSources = function() return {[first] = exits[1]} end
wall.sync(state, "connected", nil, true)
assert(legacy_calls == 1 and walls[4000] ~= original_stream_wall and not walls[4000].destroyed,
    "A declined native discovery uses the stock Lua route for the streamed source")
wall.clear()
