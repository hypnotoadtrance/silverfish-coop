package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
package.loaded.guest_save = {}

local function object(fields)
    fields.IsValid = function() return true end
    fields.GetAddress = function(self) return self end
    return fields
end

local callbacks, paths, scans = {}, {}, {}
function NotifyOnNewObject(path, callback)
    callbacks[path] = callback
end
function StaticFindObject(path)
    return paths[path]
end

local function stock_class(path, kind)
    return object({
        GetFName = function() return {ToString = function() return kind end} end,
        GetFullName = function() return "BlueprintGeneratedClass " .. path end,
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
assert(unpowered[4] == "0" and radio_readback() == unpowered, "An unchanged radio reuses its status row")
paths[radio_path].Powered = true
local powered = radio_readback()
assert(powered[4] == "1" and powered ~= unpowered, "A power change immediately replaces the cached row")
paths[radio_path].IsActorBeingDestroyed = function() return true end
assert(not radio_readback(), "A destroyed radio cannot publish a cached row")
state.world = object({PersistentLevel = object({}), actors = {}})
assert(#switches.rows(state, "hosting", row) == 0, "A previous world's devices must not leak across travel")
print("Stock switch discovery enrolls both wall switch profiles and refreshes only the late class")
