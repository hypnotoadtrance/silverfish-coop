package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local first_map = "/Game/Classes/Maps/CrossRoads_P"
local next_map = "/Game/Maps/Destination"
local function world(id, package)
    return {
        IsValid = function() return true end,
        GetAddress = function() return id end,
        GetFullName = function() return "World " .. package .. "." .. package:match("([^/]+)$") end,
    }
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
        HasAuthority = function() return true end, bReplicates = false,
        GetActorEnableCollision = function() return true end, IsActorTickEnabled = function() return false end,
    }
end
local actors = {[expired] = plank(3, source), [intact] = plank(4, source), [destination] = plank(5, target)}
local lookups, collections = {}, 0
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
    assert(address == 2 and name == next_map .. ".Destination")
    return destination .. "\n"
end
function SilverfishQualifyNativePlacedDestructible(name)
    assert(name ~= expired)
    return true, false
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
print("Delayed destructible reads skip removed actors and retire the outgoing world")
