package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
package.loaded.guest_save = {}

local function object(fields)
    fields.IsValid = function() return true end
    fields.GetAddress = function(self) return self end
    return fields
end

local callbacks, paths, scans = {}, {}, {}
local began
function RegisterBeginPlayPostHook(callback) began = callback end
local actor_lookups = 0
function NotifyOnNewObject(path, callback)
    callbacks[path] = callback
end
function StaticFindObject(path)
    if path:find(":PersistentLevel", 1, true) then actor_lookups = actor_lookups + 1 end
    return paths[path]
end

local function stock_class(path, kind, parent)
    return object({
        GetFName = function() return {ToString = function() return kind end} end,
        GetFullName = function() return "BlueprintGeneratedClass " .. path end,
        GetSuperStruct = function() return parent end,
    })
end

local function device(world, class, name)
    local actor = object({Powered = false})
    actor.GetClass = function() return class end
    actor.GetWorld = function() return world end
    actor.GetOuter = function() return world.PersistentLevel end
    actor.HasAuthority = function() return true end
    actor.IsActorBeingDestroyed = function() return false end
    actor.GetFullName = function() return name end
    actor.Box = object({GetOwner = function() return actor end})
    actor.Switch_Mesh = object({
        GetOwner = actor.Box.GetOwner,
        RelativeRotation = {Pitch = 0, Yaw = 0, Roll = 0},
    })
    actor.SwitchPanel = object({GetOwner = actor.Box.GetOwner, GetMaterial = function() return nil end})
    return actor
end

paths["/Script/Engine.Default__GameplayStatics"] = object({
    GetAllActorsOfClass = function(_, world, class, out)
        scans[class] = (scans[class] or 0) + 1
        out.OutActors = world.actors[class] or {}
    end,
})

local wall = "/Game/Classes/ScriptingActors/LightSwitch_02_BP.LightSwitch_02_BP_C"
local radio = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_Radio01.Prop_Radio01_C"
local wall_class = stock_class(wall, "LightSwitch_02_BP_C")
local radio_class = stock_class(radio, "Prop_Radio01_C")
paths[wall] = wall_class

local world = object({PersistentLevel = object({}), actors = {}})
local function row(...) return {...} end
local state = {map = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P", world = world, listening = true, server = false}
local actor_prefix = state.map .. ".Tut_Dock_P:PersistentLevel."
local switch_name = "LightSwitch_02_BP_C " .. actor_prefix .. "LightSwitch_02_BP_C_1"
world.actors[wall_class] = {device(world, wall_class, switch_name)}

local switches = require("native_stock_switch")
switches.start()
local result = switches.rows(state, "hosting", row)
assert(#result == 2 and result[1][3] == switch_name and scans[wall_class] == 1)
switches.rows(state, "hosting", row)
assert(scans[wall_class] == 1, "Steady states must reuse the device census")

local radio_name = "Prop_Radio01_C " .. actor_prefix .. "Prop_Radio01_C_3"
local radio_path = actor_prefix .. "Prop_Radio01_C_3"
paths[radio], paths[radio_path] = radio_class, device(world, radio_class, radio_name)
world.actors[radio_class] = {paths[radio_path]}
callbacks[radio]()
result = switches.rows(state, "hosting", row)
assert(#result == 3 and scans[radio_class] == 1 and scans[wall_class] == 1, "Only the newly available class needs discovery")
local function radio_readback()
    for _, device_row in ipairs(switches.rows(state, "hosting", row)) do
        if device_row[3] == radio_name then return device_row end
    end
end
local unpowered = radio_readback()
assert(unpowered[4] == "0" and radio_readback() == unpowered and actor_lookups == 0,
    "An unchanged radio reuses the live actor and status row")
paths[radio_path].Powered = true
local powered = radio_readback()
assert(powered[4] == "1" and powered ~= unpowered, "A power change immediately replaces the cached row")
paths[radio_path].IsActorBeingDestroyed = function() return true end
assert(not radio_readback(), "A destroyed radio cannot publish a cached row")
paths[radio_path] = device(world, wall_class, radio_name)
assert(not radio_readback(), "The same path cannot bind an actor of a different class")
paths[radio_path] = device(world, radio_class, radio_name)
local replacement = radio_readback()
local replacement_lookups = actor_lookups
assert(replacement[4] == "0" and radio_readback() == replacement and actor_lookups == replacement_lookups,
    "A qualified replacement should publish its own state and be reused")
state.world = object({PersistentLevel = object({}), actors = {}})
assert(#switches.rows(state, "hosting", row) == 1, "A previous world's devices must not leak across travel")
print("Stock switch discovery enrolls both wall switch profiles and refreshes only the late class")

state.map = "/CommunityMaps/Depot"
local radio_child = stock_class("/CommunityProps/SolarRadio.SolarRadio_C", "SolarRadio_C", radio_class)
local fake = stock_class("/CommunityProps/Prop_Radio01.Prop_Radio01_C", "Prop_Radio01_C")
local child_path = state.map .. ".Depot:PersistentLevel.SolarRadio_C_2"
local child_name = "SolarRadio_C " .. child_path
local custom = device(state.world, radio_child, child_name)
paths[child_path], paths[child_path .. ".Box"] = custom, custom.Box
state.world.actors[radio_class] = {
    custom, device(state.world, fake, "Prop_Radio01_C " .. state.map .. ".Depot:PersistentLevel.Prop_Radio01_C_1"),
}
callbacks[radio]()
local target_rows = switches.rows(state, "hosting", row)
assert(#target_rows == 2 and target_rows[1][3] == child_name, "Only the inherited stock class qualifies")
local pawn = object({Dead = false, HasAuthority = function() return false end, GetWorld = function() return state.world end})
assert(switches.focused_target(pawn, child_path .. ".Box") == "stock-prop-switch:" .. child_name)
package.loaded.guest_save.exact_item_context = function(_, _, name)
    assert(name == "qualified guest")
    return pawn
end
custom.SendInteractionToActor = function(self, source, box)
    assert(source == pawn and box == self.Box)
    self.Powered = not self.Powered
end
custom.Toggle_Power = function(self, powered) self.Powered = powered end
assert(switches.command({"nonce", "native-stock-switch-interact", child_name, "qualified guest"}, state, "hosting")
    == "stock switch interaction dispatched" and custom.Powered)
local function encode(...)
    return table.concat({...}, "\t")
end
local first = switches.rows(state, "hosting", encode)
local version = tonumber(first[1]:match("\tv=(%d+)$"))
assert(version and switches.rows(state, "hosting", encode)[1] == first[1])
custom.Powered = false
assert(tonumber(switches.rows(state, "hosting", encode)[1]:match("\tv=(%d+)$")) > version)
state.server = true
assert(switches.command({"nonce", "native-stock-switch-project", child_name, state.map, "1", "0", "0", "0", "v=5"},
    state, "connected") == "stock switch state projected" and custom.Powered)
assert(switches.command({"nonce", "native-stock-switch-project", child_name, state.map, "0", "0", "0", "0", "v=4"},
    state, "connected") == "stock switch newer state retained" and custom.Powered)
assert(switches.command({"nonce", "native-stock-switch-project", child_name, state.map, "0", "0", "0", "0", "v=6"},
    state, "connected") == "stock switch state projected" and not custom.Powered)
print("Inherited radio on a mounted map passed stock focus, host dispatch, and guest projection")

local now = os.clock() + 1
os.clock = function() return now end
function FName(value) return value end
local stream_package = "/CommunityMaps/CoopHarbor_1"
local stream_path = stream_package .. ".Harbor:PersistentLevel.SolarRadio_C_1"
local stream_name = "SolarRadio_C " .. stream_path
local package = object({GetFullName = function() return "Package " .. stream_package end})
local subworld = object({GetOuter = function() return package end})
local level = object({bIsVisible = true, GetOuter = function() return subworld end})
local stream = object({GetLoadedLevel = function() return level end, IsLevelVisible = function() return level.bIsVisible end})
local statics = paths["/Script/Engine.Default__GameplayStatics"]
statics.GetStreamingLevel = function(_, scope, name)
    assert(scope == state.world and name == stream_package)
    return stream
end
local radio_in_stream = device(state.world, radio_child, stream_name)
radio_in_stream.GetOuter = function() return level end
radio_in_stream.GetWorld = function() return subworld end
radio_in_stream.Toggle_Power = custom.Toggle_Power
paths[stream_path] = radio_in_stream
paths[stream_path .. ".Box"] = radio_in_stream.Box
state.world.actors[radio_class][#state.world.actors[radio_class] + 1] = radio_in_stream
began({get = function() return radio_in_stream end})
local function has_stream_row()
    for _, device_row in ipairs(switches.rows(state, "connected", row)) do
        if device_row[3] == stream_name then return true end
    end
end
assert(has_stream_row() and switches.focused_target(pawn, stream_path .. ".Box") == "stock-prop-switch:" .. stream_name)
level.bIsVisible = false
assert(not has_stream_row() and not switches.focused_target(pawn, stream_path .. ".Box"))
assert(not pcall(switches.command,
    {"nonce", "native-stock-switch-project", stream_name, state.map, "1", "0", "0", "0"}, state, "connected"))
now, level.bIsVisible = now + 1, true
assert(has_stream_row())
assert(switches.command({"nonce", "native-stock-switch-project", stream_name, state.map, "1", "0", "0", "0"},
    state, "connected") == "stock switch state projected" and radio_in_stream.Powered)
print("Streamed device census follows visibility and rejects projection against a hidden level")

local lamp_path = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_Lantern_01.Prop_Lantern_01_C"
local lamp_class = stock_class(lamp_path, "Prop_Lantern_01_C")
local lamp_child = stock_class("/CommunityProps/FieldLamp.FieldLamp_C", "FieldLamp_C", lamp_class)
local lamp_name = "FieldLamp_C " .. stream_package .. ".Harbor:PersistentLevel.FieldLamp_C_1"
local lamp = device(state.world, lamp_child, lamp_name)
lamp.GetOuter = function() return level end
lamp.GetWorld = function() return subworld end
lamp.Lit, lamp.Broken_0 = false, false
local owner = lamp.Box.GetOwner
lamp.Sphere = object({GetOwner = owner, GetGenerateOverlapEvents = function() return false end})
lamp.PointLight = object({GetOwner = owner, IsVisible = function() return lamp.Lit end})
lamp.FX_CandleFlame_01_Converted = object({GetOwner = owner})
lamp.PropMesh = object({
    GetOwner = owner, response = 1,
    GetCollisionResponseToChannel = function(self, channel) assert(channel == 16) return self.response end,
    SetCollisionResponseToChannel = function(self, channel, response)
        assert(channel == 16)
        self.response = response
    end,
})
lamp.RootComponent = lamp.PropMesh
paths[lamp_path], state.world.actors[lamp_class] = lamp_class, {lamp}
callbacks[lamp_path]()
switches.rows(state, "connected", row)
assert(lamp.PropMesh.response == 1, "Unlit lamps retain the pickup collision response")
lamp.Lit = true
switches.rows(state, "connected", row)
assert(lamp.PropMesh.response == 2, "A derived lit lamp exposes its body to Interact")
lamp.Lit = false
switches.rows(state, "connected", row)
assert(lamp.PropMesh.response == 1, "Extinguishing restores the stock collision response")
lamp.Lit = true
switches.rows(state, "connected", row)
lamp.Broken_0 = true
switches.rows(state, "connected", row)
assert(lamp.PropMesh.response == 1, "The body of a broken lamp stops intercepting Interact")
lamp.Broken_0 = false
switches.rows(state, "connected", row)
level.bIsVisible = false
switches.rows(state, "connected", row)
assert(lamp.PropMesh.response == 1, "The hidden streamed lamp releases its collision override")
level.bIsVisible = true
print("Inherited oil lantern collision follows the stock light, broken state, and stream visibility")

switches.rows(state, "connected", row)
local overlap, native_overlap, stock_overlap_reads = true, true, 0
lamp.Sphere.GetGenerateOverlapEvents = function()
    stock_overlap_reads = stock_overlap_reads + 1
    return overlap
end
lamp.Sphere.SetGenerateOverlapEvents = function(_, value) overlap = value end
SilverfishReadNativeSwitchPower = function(scope, actor, _, field, lantern, want_overlap)
    if actor ~= lamp then return end
    assert(scope == state.world and field == "Lit" and lantern and want_overlap)
    return lamp.Lit, lamp.Broken_0, native_overlap
end
switches.rows(state, "connected", row)
assert(not overlap and stock_overlap_reads == 1, "A native enabled overlap still runs stock suppression")
native_overlap = false
switches.rows(state, "connected", row)
assert(stock_overlap_reads == 1, "A native disabled overlap skips the reflected component")
native_overlap, overlap = nil, true
switches.rows(state, "connected", row)
assert(not overlap and stock_overlap_reads == 2, "An unqualified component retains the stock fallback")
switches.release_candles(state, "ready")
assert(overlap, "Fixture exit restores the overlap through the original stock component")
SilverfishReadNativeSwitchPower = nil

local bulb_class = object({})
paths["/Game/Classes/DynamicLights/DynamicLight_Master_BP.DynamicLight_Master_BP_C"] = bulb_class
local light_prefix = "DynamicLight_Master_BP_C " .. state.map .. ".Depot:PersistentLevel.Lamp_"
local function bulb(world, suffix)
    local actor = object({Powered = false})
    actor.GetOuter = function() return world.PersistentLevel end
    actor.GetWorld = function() return world end
    actor.IsActorBeingDestroyed = function() return false end
    actor.GetFullName = function() return light_prefix .. suffix end
    actor.IsA = function(_, class) return class == bulb_class end
    actor.TogglePower = function(self) self.toggles = (self.toggles or 0) + 1 end
    actor.PointLight = object({
        visible = false, GetOwner = function() return actor end,
        IsVisible = function(self) return self.visible end,
        SetVisibility = function(self, visible) self.visible = visible end,
    })
    return actor
end

local connected_world = state.world
local panel_name = "LightSwitch_02_BP_C " .. state.map .. ".Depot:PersistentLevel.DelayedSwitch"
local panel = device(connected_world, wall_class, panel_name)
panel.Toggle_Visuals = function() end
panel.Switch_Mesh.K2_SetRelativeRotation = function(self, angles) self.RelativeRotation = angles end
connected_world.actors[wall_class] = {panel}
local links = {}
links.GetArrayNum = function(self) return #self end
panel.ActorsToPower = links
callbacks[wall]()
local function switch_row(phase)
    for _, device_row in ipairs(switches.rows(state, phase, row)) do
        if device_row[3] == panel_name then return device_row end
    end
end

state.server = false
assert(switch_row("hosting")[9] == "-")
local searches = scans[wall_class]
local earlier, changed = bulb(connected_world, "1"), bulb(connected_world, "2")
links[1] = earlier
assert(switch_row("hosting")[9] == earlier:GetFullName() .. "|0|0")
links[1] = changed
changed.Powered, changed.PointLight.visible = true, true
now = now + 1
assert(switch_row("hosting")[9] == changed:GetFullName() .. "|1|1",
    "A stock switch must observe a replacement target even if its power and link count stay the same")
links[1], panel.Powered = earlier, true
assert(switch_row("hosting")[9] == earlier:GetFullName() .. "|0|0", "A power edge refreshes the links immediately")
links[1], panel.Powered = changed, false
assert(switch_row("hosting")[9] == changed:GetFullName() .. "|1|1")
assert(scans[wall_class] == searches, "Rebinding a light link must not rescan switches")

state.server = true
local later = bulb(connected_world, "3")
local foreign = bulb(object({PersistentLevel = object({})}), "4")
foreign.GetOuter = function() return level end
local statics_lookup = statics.GetStreamingLevel
statics.GetStreamingLevel = function() return nil end
links[1], links[2] = later, foreign
local request = {"nonce", "native-stock-switch-project", panel_name, state.map, "1", "0", "0", "0",
    earlier:GetFullName() .. "|1|1", later:GetFullName() .. "|1|1", foreign:GetFullName() .. "|1|1"}
assert(switches.command(request, state, "connected") == "stock switch state projected")
assert(later.Powered and later.PointLight.visible and later.toggles == 1)
assert(not earlier.Powered and not earlier.PointLight.visible and not foreign.Powered and not foreign.PointLight.visible)
statics.GetStreamingLevel = statics_lookup
print("Live switch links refresh without a device rescan and projection uses only current owned lights")

local latch_path = "/Game/Assets/StaticMeshes/Doors/DoorLatch_BP.DoorLatch_BP_C"
local door_path = "/Game/Assets/StaticMeshes/Doors/BasicDoor_Master.BasicDoor_Master_C"
local latch_class = stock_class(latch_path, "DoorLatch_BP_C")
local door_class = stock_class(door_path, "BasicDoor_Master_C")
paths[latch_path], paths[door_path] = latch_class, door_class
local placed_latch = state.map .. ".Depot:PersistentLevel.DoorLatch_BP_C_2"
local latch_name = "DoorLatch_BP_C " .. placed_latch
local latch = device(connected_world, latch_class, latch_name)
local linked_door = device(connected_world, door_class,
    "BasicDoor_Master_C " .. state.map .. ".Depot:PersistentLevel.LinkedDoor")
latch.Door, latch.Box_Knob, latch.SM_Prop_Kitchen_Grill_01_Dial_01 = linked_door, latch.Box, latch.Switch_Mesh
linked_door.IsA = function(_, class) return class == door_class end
linked_door.Locked, linked_door.Open, linked_door.LockBroken = false, false, false
latch.Switch_Mesh.K2_SetRelativeRotation = panel.Switch_Mesh.K2_SetRelativeRotation
paths[placed_latch] = latch
latch.SendInteractionToActor = function(self, source, box)
    assert(source == pawn and box == self.Box_Knob)
    self.Door.Locked = not self.Door.Locked
end
state.server = false
assert(switches.command({"nonce", "native-stock-switch-interact", latch_name, "qualified guest"}, state, "hosting")
    == "stock switch interaction dispatched" and linked_door.Locked)
local native_latch_calls = 0
SilverfishReadNativeDoorLatch = function(scope, actor, path, door, dial)
    assert(scope == state.world and actor == latch and path == placed_latch and door == linked_door and dial == latch.Switch_Mesh)
    native_latch_calls = native_latch_calls + 1
    return linked_door.Locked, 12, -4, 45, linked_door:GetFullName()
end
local function latch_row(phase)
    for _, reading in ipairs(switches.rows(state, phase, row)) do
        if reading[3] == latch_name then return reading end
    end
end
local sampled = assert(latch_row("hosting"))
assert(sampled[4] == "1" and sampled[5] == 12 and sampled[6] == -4 and sampled[7] == 45
    and sampled[10] == "door=" .. linked_door:GetFullName() and native_latch_calls == 1)
SilverfishReadNativeDoorLatch = function() end
sampled = assert(latch_row("hosting"))
assert(sampled[4] == "1" and sampled[5] == 0 and sampled[7] == 0, "A declined native read uses stock dial rotation")
SilverfishReadNativeDoorLatch = nil
state.server = true
local projection = {"nonce", "native-stock-switch-project", latch_name, state.map, "0", "0", "0", "45", "door=wrong"}
assert(not pcall(switches.command, projection, state, "connected"))
assert(linked_door.Locked and latch.Switch_Mesh.RelativeRotation.Roll == 0)
projection[9] = "door=" .. linked_door:GetFullName()
assert(switches.command(projection, state, "connected") == "stock switch state projected")
assert(not linked_door.Locked and latch.Switch_Mesh.RelativeRotation.Roll == 45)
latch.SendInteractionToActor = function() end
state.server = false
assert(switches.command({"nonce", "native-stock-switch-interact", latch_name, "qualified guest"}, state, "hosting")
    == "stock latch interaction dispatched" and not linked_door.Locked)
print("Exact latch dispatch reaches a live actor outside the census and projects both dial and lock")
