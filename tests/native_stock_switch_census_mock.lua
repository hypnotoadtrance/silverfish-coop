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
assert(#result == 1 and result[1][3] == switch_name and scans[wall_class] == 1)
switches.rows(state, "hosting", row)
assert(scans[wall_class] == 1, "Steady states must reuse the device census")

local radio_name = "Prop_Radio01_C " .. actor_prefix .. "Prop_Radio01_C_3"
local radio_path = actor_prefix .. "Prop_Radio01_C_3"
paths[radio], paths[radio_path] = radio_class, device(world, radio_class, radio_name)
world.actors[radio_class] = {paths[radio_path]}
callbacks[radio]()
result = switches.rows(state, "hosting", row)
assert(#result == 2 and scans[radio_class] == 1 and scans[wall_class] == 1, "Only the newly available class needs discovery")
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
assert(#switches.rows(state, "hosting", row) == 0, "A previous world's devices must not leak across travel")
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
assert(#target_rows == 1 and target_rows[1][3] == child_name, "Only the inherited stock class qualifies")
local pawn = object({Dead = false, HasAuthority = function() return false end, GetWorld = function() return state.world end})
assert(switches.focused_target(pawn, child_path .. ".Box") == "stock-switch:" .. child_name)
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
state.server = true
assert(switches.command({"nonce", "native-stock-switch-project", child_name, state.map, "0", "0", "0", "0"},
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
assert(has_stream_row() and switches.focused_target(pawn, stream_path .. ".Box") == "stock-switch:" .. stream_name)
level.bIsVisible = false
assert(not has_stream_row() and not switches.focused_target(pawn, stream_path .. ".Box"))
assert(not pcall(switches.command,
    {"nonce", "native-stock-switch-project", stream_name, state.map, "1", "0", "0", "0"}, state, "connected"))
now, level.bIsVisible = now + 1, true
assert(has_stream_row())
assert(switches.command({"nonce", "native-stock-switch-project", stream_name, state.map, "1", "0", "0", "0"},
    state, "connected") == "stock switch state projected" and radio_in_stream.Powered)
print("Streamed device census follows visibility and rejects projection against a hidden level")
