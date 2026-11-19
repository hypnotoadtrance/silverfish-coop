-- Run with Lua 5.4 from the repository root.
local flags = {
    SILVERFISH_COOP_HEADLESS = "1",
    SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES = "1",
    SILVERFISH_COOP_NATIVE_LOOT = "1",
}
os.getenv = function(name) return flags[name] end

local function object(address, name)
    return {IsValid = function() return true end, GetAddress = function() return address end, GetFullName = function() return name end}
end

local world = object(1, "world")
local pawn = object(2, "local guest")
local area = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
local path = area .. ".City_01_P:PersistentLevel.LootCrate_Small_BP_C_5"
local profile = {index = 4, map = area, path = path, name = "LootCrate_Small_BP_C " .. path}
local crate = object(3, profile.name)
local camera_type, box_type, mesh_type = object(4, "camera class"), object(5, "box class"), object(6, "mesh class")
local camera, box, door = object(7, "camera"), object(8, "exact crate box"), object(9, "door")
local sibling = object(10, "another crate box")
pawn.GetWorld, crate.GetWorld = function() return world end, function() return world end
pawn.Camera, crate.Box, crate.SM_Prop_Cabinet_Small_Door_01, crate.Looted = camera, box, door, false
camera.GetClass, box.GetClass, door.GetClass = function() return camera_type end, function() return box_type end, function() return mesh_type end
camera.GetOwner, box.GetOwner, door.GetOwner = function() return pawn end, function() return crate end, function() return crate end
box.GetCollisionEnabled = function() return 1 end
box.GetScaledBoxExtent = function() return {X = 24, Y = 20, Z = 30} end
box.GetRightVector = function() return {X = 0, Y = 1, Z = 0} end
box.GetUpVector = function() return {X = 0, Y = 0, Z = 1} end
camera.K2_GetComponentLocation = function() return {X = 0, Y = 0, Z = 40} end
box.K2_GetComponentLocation = function() return {X = 160, Y = 0, Z = 40} end
camera.RelativeRotation = {Pitch = 0, Yaw = 90, Roll = 0}
camera.K2_SetWorldRotation = function(self, aim) self.RelativeRotation = aim end
camera.K2_SetRelativeRotation = camera.K2_SetWorldRotation

local objects = { [path] = crate, ["/Script/Engine.CameraComponent"] = camera_type,
    ["/Script/Engine.BoxComponent"] = box_type, ["/Script/Engine.StaticMeshComponent"] = mesh_type }
StaticFindObject = function(name) return objects[name] end
package.loaded.native_loot = {profile = function(map, target) if map == area and target == profile.name then return profile end end}

local chosen, sequence = box, 0
pawn.LookTrace = function(self)
    self.InteractionTarget_Component = math.abs(camera.RelativeRotation.Yaw) < 15 and chosen or nil
    self.CanInteract = self.InteractionTarget_Component ~= nil
end
SilverfishSetNativeLootCrateInputScope = function(pawn_address, host, index)
    return pawn_address == pawn:GetAddress() and not host and index == profile.index
end
SilverfishReadNativeLootCrateInput = function(index)
    assert(index == profile.index)
    return true, sequence, "live", box:GetAddress()
end
SilverfishSetNativeInteractScope = function(pawn_address, host) return pawn_address == pawn:GetAddress() and not host end
SilverfishProbeNativeInteractInput = function(address, stock)
    assert(stock == true and address == pawn:GetAddress())
    assert(pawn.CanInteract and pawn.InteractionTarget_Component == box and math.abs(camera.RelativeRotation.Yaw) < 15)
    sequence = sequence + 1
    return true, "stock_started_consumed"
end

local input = assert(loadfile("mod/SilverfishCoop/Scripts/native_loot_crate_input.lua"))()
local state = {map = area, world = world, server = true, listening = true, local_pawn = {pawn = pawn, name = pawn:GetFullName()}}
local request = {"request", "native-loot-crate-fixture", profile.name}
assert(input.command(request, state, "connected") == "stock crate interaction intercepted")
assert(sequence == 1 and camera.RelativeRotation.Yaw == 90 and pawn.InteractionTarget_Component == nil)

chosen = sibling
local accepted, failure = pcall(input.command, request, state, "connected")
assert(not accepted and failure == "native_loot_crate_stock_fixture_focus_unavailable:another crate box")
assert(sequence == 1 and camera.RelativeRotation.Yaw == 90 and pawn.InteractionTarget_Component == nil)
print("New crate fixture requires exact stock LookTrace and restores the camera")
