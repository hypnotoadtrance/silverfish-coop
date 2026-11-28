package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
package.loaded.native_transport = {}
package.loaded.guest_save = {}
package.loaded.native_authored_pickup_input_fixture = {}

local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local level_path = map .. ".CrossRoads_P:PersistentLevel."
local patch_path = level_path .. "MushroomPatch_BP_C_1"
local child_path = level_path .. "Item_BrownMushroom_C_2"
local farther_path = level_path .. "Item_BrownMushroom_C_3"
local item_path = "/Game/Classes/Items/Item_BrownMushroom.Item_BrownMushroom_C"
local function object()
    return {IsValid = function(self) return not self.invalid end, GetAddress = function(self) return self.address end}
end

local world, level, patch_type, patch, child, pawn = object(), object(), object(), object(), object(), object()
local item_type, farther = object(), object()
world.address, level.address, patch_type.address = 1, 2, 3
patch.address, child.address, pawn.address = 4, 5, 6
item_type.address, farther.address = 7, 8
item_type.GetFullName = function() return "BlueprintGeneratedClass " .. item_path end
world.PersistentLevel = level
patch.GetFullName = function() return "MushroomPatch_BP_C " .. patch_path end
patch.GetClass = function() return patch_type end
patch.IsA = function(_, class) return class == patch_type end
patch.HasAuthority = function() return true end
patch.IsActorBeingDestroyed = function() return false end
patch.GetWorld = function() return world end
patch.GetOuter = function() return level end
for path, actor in pairs({[child_path] = child, [farther_path] = farther}) do
    actor.GetFullName = function() return "Item_BrownMushroom_C " .. path end
    actor.GetClass = function() return item_type end
    actor.IsA = function(_, class) return class == item_type end
    actor.HasAuthority = function() return true end
    actor.IsActorBeingDestroyed = function(self) return self.destroying == true end
    actor.GetWorld = function() return world end
    actor.GetOuter = function() return level end
    actor.K2_GetActorLocation = function() return {X = actor == child and 10 or 15, Y = 20, Z = 30} end
    actor.K2_GetActorRotation = function() return {Pitch = 0, Yaw = 0, Roll = 0} end
    actor.GetActorScale3D = function() return {X = 1, Y = 1, Z = 1} end
    actor.Ammo = 1
end
local first_id = ("11111111"):rep(4)
local far_id = ("22222222"):rep(4)
local function parameter(value) return {get = function() return value end} end
patch.SpawnedShrooms = {ForEach = function(_, visit)
    visit(parameter(child), parameter({A = 0x11111111, B = 0x11111111, C = 0x11111111, D = 0x11111111}))
end}
pawn.Dead = false
local state = {world = world, map = map, listening = true, server = false,
    pawns = {{pawn = pawn, x = 10, y = 20, z = 30}}}

local scans, sweeps, formats = 0, 0, 0
local clock = 10
os.clock = function() return clock end
local statics = object()
function statics:GetAllActorsOfClass(current_world, class, result)
    assert(current_world == world and class == patch_type)
    scans = scans + 1
    result.OutActors = {patch}
end
StaticFindObject = function(path)
    if path == child_path then return child end
    if path == farther_path then return farther end
    if path == "/Script/Engine.Default__GameplayStatics" then return statics end
    if path:match("MushroomPatch_BP_C$") then return patch_type end
    if path:match("Item_Master_BP_C$") then return item_type end
end

SilverfishCaptureNativeMushroom = function() error("Read path must never capture an item") end
local function expected(guid, path, x)
    return table.concat({"native-mushroom", map, patch_path, guid, path, item_path, 1, x, 20, 30, 0, 0, 0, 1, 1, 1}, "\t")
end
local native = "rows"
SilverfishSampleNativeMushroomRows = function(address, path, map_name, radius_squared, x, y, z)
    assert(address == world.address and path == patch_path and map_name == map and radius_squared == 2300^2)
    assert(x == 10 and y == 20 and z == 30)
    sweeps = sweeps + 1
    if native == "rows" then return "25\t" .. expected(far_id, farther_path, 15) .. "\n0\t"
        .. expected(first_id, child_path, 10) .. "\n" end
    if native == "empty" then return "" end
end

local mushrooms = require("native_mushrooms")
mushrooms.start(function(...)
    formats = formats + 1
    return table.concat({...}, "\t")
end)
mushrooms.sync(state, "hosting")
local function rows()
    return mushrooms.rows(state, "hosting", function() error("Host publication must reuse formatted rows") end)
end
assert(#rows() == 1 and scans == 0 and sweeps == 0 and formats == 1)
mushrooms.update(function() return world end)
assert(#rows() == 1 and scans == 1 and sweeps == 0 and formats == 1)
mushrooms.update(function() return world end)
local snapshot = rows()
assert(#snapshot == 3 and scans == 1 and sweeps == 1 and formats == 1)
assert(snapshot[2] == expected(first_id, child_path, 10) and snapshot[3] == expected(far_id, farther_path, 15))
assert(rows()[2] == snapshot[2] and formats == 1)
child.destroying = true
assert(#rows() == 2 and rows()[2] == snapshot[3] and formats == 1,
    "Destroyed mushrooms must disappear before the next sweep")
child.destroying = false
native, clock = "empty", 11
mushrooms.update(function() return world end)
assert(#rows() == 1 and sweeps == 2 and formats == 1, "An empty native result must not trigger Lua fallback")
native, clock = "refused", 12
mushrooms.update(function() return world end)
assert(#rows() == 2 and rows()[2] == snapshot[2] and sweeps == 3 and formats == 2,
    "An unavailable native sample must retain the exact stock Lua fallback")
mushrooms.sync(state, "inactive")
assert(#rows() == 0)
print("Mushroom rows retain ordering, exact formatting, immediate removal and Lua fallback.")
