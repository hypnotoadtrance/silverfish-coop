package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local first_map = "/Game/Classes/Maps/CrossRoads_P"
local next_map = "/Game/Maps/Destination"
local function world(id, package)
    local world = {
        IsValid = function() return true end,
        GetAddress = function() return id end,
        GetFullName = function() return "World " .. package .. "." .. package:match("([^/]+)$") end,
        streams = {},
    }
    world.PersistentLevel = {IsValid = world.IsValid, GetAddress = function() return id + 10 end}
    return world
end
local source, target = world(1, first_map), world(2, next_map)
local function path(map, name)
    return map .. "." .. map:match("([^/]+)$") .. ":PersistentLevel." .. name
end
local expired = path(first_map, "RemovedPlank_C_1")
local intact = path(first_map, "IntactPlank_C_2")
local destination = path(next_map, "NewPlank_C_3")
local function plank(id, level)
    return {
        IsValid = function() return true end, IsActorBeingDestroyed = function() return false end,
        GetAddress = function() return id end, GetWorld = function() return level end,
        GetOuter = function(self) return self.outer or level.PersistentLevel end,
        HasAuthority = function() return true end, bReplicates = false,
        GetActorEnableCollision = function(self) return not self.no_collision end,
        IsActorTickEnabled = function() return false end,
        SetActorHiddenInGame = function(self, hidden) self.bHidden = hidden end,
        SetActorEnableCollision = function(self, collision) self.no_collision = not collision end,
        SetActorTickEnabled = function() end,
    }
end
local actors = {[expired] = plank(3, source), [intact] = plank(4, source), [destination] = plank(5, target)}
local lookups, collections = {}, 0
local streamed_paths = {}
function StaticFindObject(name)
    if name == "/Script/Engine.Default__GameplayStatics" then
        return {IsValid = function() return true end,
            GetAllActorsOfClass = function() error("The delayed scan used borrowed actor parameters") end}
    end
    lookups[#lookups + 1] = name
    return actors[name]
end
function SilverfishSnapshotNativeWorldActorPaths(address, name, kind)
    assert(kind == "destructible")
    collections = collections + 1
    if address == 1 and name == first_map .. ".CrossRoads_P" then return expired .. "\n" .. intact .. "\n" end
    if address == 2 and name == next_map .. ".Destination" then return destination .. "\n" end
    return streamed_paths[address] or ""
end
function SilverfishQualifyNativePlacedDestructible(name, address)
    assert(name ~= expired)
    return true, address == 7
end

local world_path = require("world_path")
world_path.visible_streams = function(world) return world.streams end
world_path.visible_actor_level = function(actor, world)
    if not actor then return end
    local level = actor:GetOuter()
    if level == world.PersistentLevel or world.streams[level:GetAddress()] == level then return level end
end

local stock = require("native_destructibles")
local initial, arrived = {map = first_map, world = source}, {map = next_map, world = target}
assert(stock.rows(initial, "hosting", table.pack) == nil and #lookups == 0)
actors[expired] = nil
stock.update(function() return source end)
assert(stock.rows(initial, "hosting", table.pack)[3] == "IntactPlank_C_2")
stock.clear()
assert(stock.rows(initial, "hosting", table.pack) == nil)
stock.update(function() return target end)
assert(#lookups == 2 and stock.rows(arrived, "hosting", table.pack) == nil)
stock.update(function() return target end)
assert(stock.rows(arrived, "hosting", table.pack)[3] == "NewPlank_C_3" and collections == 3)

local stream = "/CoopPack/Maps/Dock.RedDock:PersistentLevel"
local streamed_plank = stream .. ".SharedPlank_C_1"
local function mounted_level(address)
    return {IsValid = function() return true end, GetAddress = function() return address end,
        GetFullName = function() return "Level " .. stream end}
end
local asset_world = world(5, "/CoopPack/Maps/Dock")
local host, guest = world(6, first_map), world(7, first_map)
local host_state, guest_state = {map = first_map, world = host}, {map = first_map, world = guest}
local main_plank = path(first_map, "SharedPlank_C_1")
local host_main, guest_main = plank(14, host), plank(15, guest)
local host_plank, guest_plank = plank(16, asset_world), plank(17, asset_world)
host_plank.outer, guest_plank.outer = mounted_level(28), mounted_level(29)
streamed_paths = {[6] = main_plank .. "\n" .. streamed_plank, [7] = main_plank .. "\n" .. streamed_plank}

actors[main_plank], actors[streamed_plank] = host_main, host_plank
assert(not stock.rows(host_state, "hosting", table.pack))
stock.update(function() return host end)
assert(stock.rows(host_state, "hosting", table.pack)[3] == "SharedPlank_C_1")
host.streams = {[28] = host_plank.outer}
assert(not stock.rows(host_state, "hosting", table.pack))
stock.update(function() return host end)
assert(stock.rows(host_state, "hosting", table.pack)[3] == stream .. "|" .. streamed_plank .. "|SharedPlank_C_1")
stock.ended(16, true)
assert(stock.rows(host_state, "hosting", table.pack)[3] == stream .. "|SharedPlank_C_1")

actors[main_plank], actors[streamed_plank] = guest_main, guest_plank
guest.streams = {[29] = guest_plank.outer}
assert(not stock.rows(guest_state, "connected", table.pack))
stock.update(function() return guest end)
local epoch = string.rep("a", 32)
local function apply(...)
    return stock.command({"ticket", "native-destructible-apply", first_map, epoch, ...}, guest_state, "connected")
end
assert(apply("SharedPlank_C_1"))
assert(not guest_main.bHidden and not guest_plank.bHidden)
assert(apply(stream, "SharedPlank_C_1"))
assert(not guest_main.bHidden and guest_plank.bHidden and guest_plank.no_collision)
assert(not pcall(apply, streamed_plank, "SharedPlank_C_1"))
assert(apply("SharedPlank_C_1"))
assert(not guest_plank.bHidden and not guest_plank.no_collision)
assert(stock.rows(guest_state, "connected", table.pack)[3] == epoch)
guest.streams = {}
assert(not stock.rows(guest_state, "connected", table.pack))
stock.update(function() return guest end)
assert(stock.rows(guest_state, "connected", table.pack)[3] == "-")

print("Destructibles preserve independent same-name stream scope and restore when coverage ends")
