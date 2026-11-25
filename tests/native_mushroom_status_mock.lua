package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
package.loaded.native_transport = {}
package.loaded.guest_save = {}
package.loaded.native_authored_pickup_input_fixture = {}

local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local level_path = map .. ".CrossRoads_P:PersistentLevel."
local patch_path = level_path .. "MushroomPatch_BP_C_1"
local child_path = level_path .. "Item_BrownMushroom_C_2"
local item_path = "/Game/Classes/Items/Item_BrownMushroom.Item_BrownMushroom_C"
local function object()
    return {IsValid = function(self) return not self.invalid end, GetAddress = function(self) return self.address end}
end

local world, level, patch_type, patch, child, pawn = object(), object(), object(), object(), object(), object()
world.address, level.address, patch_type.address = 1, 2, 3
patch.address, child.address, pawn.address = 4, 5, 6
world.PersistentLevel = level
patch.GetFullName = function() return "MushroomPatch_BP_C " .. patch_path end
patch.GetClass = function() return patch_type end
patch.IsA = function(_, class) return class == patch_type end
patch.HasAuthority = function() return true end
patch.IsActorBeingDestroyed = function() return false end
patch.GetWorld = function() return world end
patch.GetOuter = function() return level end
child.IsActorBeingDestroyed = function(self) return self.destroying == true end
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
    if path == "/Script/Engine.Default__GameplayStatics" then return statics end
    if path:match("MushroomPatch_BP_C$") then return patch_type end
    if path:match("Item_Master_BP_C$") then return object() end
end

SilverfishCaptureNativeMushroom = function() error("Read path must never capture an item") end
SilverfishSampleNativeMushroomPatch = function(address, path, radius_squared, x, y, z)
    assert(address == world.address and path == patch_path and radius_squared == 2300^2)
    assert(x == 10 and y == 20 and z == 30)
    sweeps = sweeps + 1
    return table.concat({0, ("1"):rep(32), child_path, item_path, 1, 10, 20, 30, 0, 0, 0, 1, 1, 1}, "\t") .. "\n"
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
assert(#snapshot == 2 and scans == 1 and sweeps == 1 and formats == 2)
assert(snapshot[2]:find(item_path, 1, true))
assert(rows()[2] == snapshot[2] and formats == 2)
child.destroying = true
assert(#rows() == 1 and formats == 2, "Destroyed mushrooms must disappear before the next sweep")
mushrooms.sync(state, "inactive")
assert(#rows() == 0)
print("Stock mushroom rows reuse paced samples and immediately hide destroyed mushrooms.")
