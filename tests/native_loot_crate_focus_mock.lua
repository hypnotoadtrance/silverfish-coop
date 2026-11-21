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
assert(not accepted and failure:find("native_loot_crate_stock_fixture_focus_unavailable:another crate box", 1, true) == 1)
assert(sequence == 1 and camera.RelativeRotation.Yaw == 90 and pawn.InteractionTarget_Component == nil)
print("New crate fixture requires exact stock LookTrace and restores the camera")

profile.small_cabinet = true
local frame, second_door = object(11, "exact cabinet frame"), object(12, "exact second cabinet door")
crate.SM_Prop_Cabinet_Small_02, crate.SM_Prop_Cabinet_Small_Door_02 = frame, second_door
frame.GetClass, second_door.GetClass = door.GetClass, door.GetClass
frame.GetOwner, second_door.GetOwner = door.GetOwner, door.GetOwner
door.GetAttachParent = function() return frame end
box.GetAttachParent, second_door.GetAttachParent = door.GetAttachParent, door.GetAttachParent
local function set_rotation(component, angle) component.RelativeRotation = angle end
door.RelativeRotation, second_door.RelativeRotation = {Pitch = 0, Yaw = 0, Roll = 0}, {Pitch = 0, Yaw = 0, Roll = 0}
door.K2_SetRelativeRotation, second_door.K2_SetRelativeRotation = set_rotation, set_rotation
local query_collision = 1
box.GetCollisionEnabled = function() return query_collision end
box.SetCollisionEnabled = function(_, value) query_collision = value end
local project = {"request", "native-loot-crate-project", "exhausted", profile.name}
second_door.GetAttachParent = function() return sibling end
assert(not pcall(input.command, project, state, "connected"))
assert(query_collision == 1 and crate.Looted == false)
second_door.GetAttachParent = door.GetAttachParent
assert(input.command(project, state, "connected") == "stock crate exhausted projection verified")
assert(query_collision == 0 and crate.Looted and door.RelativeRotation.Yaw == 95 and second_door.RelativeRotation.Yaw == -95)
assert(input.command(project, state, "connected") == "stock crate exhausted projection verified" and sequence == 1)
print("Per-cabinet projection proves both exact doors and never replays stock Interact")

profile.door_locations = {{X = -49, Y = 23, Z = 68}, {X = 49, Y = 23, Z = 68}}
door.RelativeLocation, second_door.RelativeLocation = {X = -47, Y = 20, Z = 68}, {X = 47, Y = 20, Z = 68}
local function set_pose(component, location, angle)
    component.RelativeLocation, component.RelativeRotation = location, angle
end
door.K2_SetRelativeLocationAndRotation, second_door.K2_SetRelativeLocationAndRotation = set_pose, set_pose
assert(input.command(project, state, "connected") == "stock crate exhausted projection verified")
assert(door.RelativeLocation.X == -49 and second_door.RelativeLocation.X == 49)
assert(door.RelativeLocation.Y == 23 and second_door.RelativeLocation.Y == 23 and sequence == 1)
print("A cabinet with fixed door locations projects the full stock pose")

profile.small_cabinet, profile.door_locations = nil, nil
profile.random_pitch, profile.lid_ignores_channel = {95, 105}, 5
crate.Looted, query_collision = false, 1
door.RelativeRotation = {Pitch = 0, Yaw = 0, Roll = 0}
local response = 2
door.GetCollisionResponseToChannel = function(_, channel) assert(channel == 5); return response end
door.SetCollisionResponseToChannel = function(_, channel, value) assert(channel == 5); response = value end
door.K2_SetRelativeRotation = function(self, angle)
    self.RelativeRotation = {Pitch = 180 - angle.Pitch, Yaw = 180, Roll = 180}
end
assert(not pcall(input.command, project, state, "connected"))
project[5] = "106"
assert(not pcall(input.command, project, state, "connected"))
assert(response == 2 and query_collision == 1 and not crate.Looted)
project[5] = "99"
assert(input.command(project, state, "connected") == "stock crate exhausted projection verified")
assert(response == 0 and query_collision == 0 and crate.Looted and door.RelativeRotation.Pitch == 81)
package.loaded.native_loot.profiles = function() return {profile} end
local rows = input.rows(state, "connected", function(...) return {...} end)
assert(rows[2][1] == "native-loot-crate-input" and rows[2][4] == "exhausted" and rows[2][5] == 99)
print("An authored random lid pose keeps the stock collision response and equivalent rotation")
