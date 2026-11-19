-- Run with Lua 5.4 from the repository root.
local map = "/Game/Classes/Maps/Maps_Dungeons/Subway_01_P"
local asset = map .. ".Subway_01_P"
local root = asset .. ":PersistentLevel."
local valve_class = "/Game/Classes/ScriptingActors/ValveSwitch_BP.ValveSwitch_BP_C"
local sludge_class = "/Game/Classes/Actors/ToxicSludge_Plane_BP.ToxicSludge_Plane_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local audio_actor_class = "/Script/Engine.AmbientSound"
local audio_class = "/Script/Engine.AudioComponent"
local box_class = "/Script/Engine.BoxComponent"
local epoch = ("1a"):rep(16)
local objects = {}
local function object(name, class, lookup)
    local value = {GetFullName = function() return name end, GetClass = function() return objects[class] end,
        IsValid = function() return true end, IsActorBeingDestroyed = function() return false end}
    value.GetAddress = function() return value end
    objects[lookup or name] = value
    return value
end
for _, class in ipairs({valve_class, sludge_class, pawn_class, controller_class, box_class, audio_actor_class, audio_class}) do
    object("Class " .. class, nil, class)
end
object("Function Lock_Valve", nil, valve_class .. ":Lock_Valve")
local world = object("World " .. asset)
world.PersistentLevel = object("level")
world.NetDriver = object("driver")
local connection = object("server connection")
connection.Driver, world.NetDriver.World, world.NetDriver.ServerConnection = world.NetDriver, world, connection
local function actor(display, suffix, class)
    local value = object(display .. " " .. root .. suffix, class, root .. suffix)
    value.bReplicates, value.bHidden, value.collision, value.ticking = false, false, true, true
    function value:GetWorld() return world end
    function value:GetOuter() return world.PersistentLevel end
    function value:HasAuthority() return false end
    function value:SetActorEnableCollision(enabled) self.collision = enabled end
    function value:SetActorTickEnabled(enabled) self.ticking = enabled end
    function value:SetActorHiddenInGame(hidden) self.bHidden = hidden end
    function value:GetActorEnableCollision() return self.collision end
    function value:IsActorTickEnabled() return self.ticking end
    function value:K2_DestroyActor() self.destroy_calls = (self.destroy_calls or 0) + 1 end
    return value
end
local valve = actor("ValveSwitch_BP_C", "ValveSwitch_BP_C_1", valve_class)
valve.Lock_AfterUse, valve.SaveState_Mode, valve.Powered = true, 2, false
valve.Box = object("BoxComponent " .. root .. "ValveSwitch_BP_C_1.Box", box_class)
function valve.Box:GetOwner() return valve end
function valve:K2_GetActorLocation() return {X = -10650, Y = -8328.04707757, Z = 286.86104472} end
function valve:K2_GetComponentsByClass() return self.Box and {self.Box} or {} end
function valve:Lock_Valve() self.Box = nil end
local sludge = actor("ToxicSludge_Plane_BP_C", "ToxicSludge_Plane_BP_C_1", sludge_class)
local sound = actor("AmbientSound", "AmbientSound_9", audio_actor_class)
local original = object("stock audio component", audio_class)
sound.AudioComponent = original
original.VolumeMultiplier = 1
function original:GetOwner() return sound end
function original:SetVolumeMultiplier(value) self.VolumeMultiplier = value end
function original:Stop() self.stopped = true end
function original:IsPlaying() return true end -- A lingering headless mixer handle is not a source of audible sound.
local foreign = object("same-class replacement", audio_class)
function foreign:GetOwner() return sound end
foreign.VolumeMultiplier = 0

local pawn = actor("Player_FP_C", "Player_FP_C_4", pawn_class)
local control = actor("PlayerControler_FP_C", "PlayerControler_FP_C_5", controller_class)
pawn.Controller, control.Pawn = control, pawn
function pawn:IsLocallyControlled() return true end
function control:IsLocalController() return true end
local state = {world = world, map = map, listening = true, server = true, controller = control,
    local_pawn = {name = pawn:GetFullName(), pawn = pawn}}
local require_fake = function(name)
    return name == "native_wire_quest" and {map = "/Game/Borough", board_name = "BountyBoard_BP_C /Game/Borough"} or {}
end
local env = setmetatable({require = require_fake, os = {getenv = function() return "1" end},
    StaticFindObject = function(path) return objects[path] end}, {__index = _G})
local flood = assert(loadfile("mod/SilverfishCoop/Scripts/native_subway_valve.lua", "t", env))()
local function scene() return flood.rows(state, "connected", table.pack)[1] end
assert(scene()[3] == "present" and scene()[10] == "receipt_unavailable")
local input = {"nonce", "native-subway-valve-project", epoch, pawn:GetFullName()}
assert(flood.command(input, state, "connected"))
local result = scene()
assert(result[3] == "closed" and result[5] == epoch and result[6] == "absent" and result[7] == "retired" and result[8] == "retired")
assert(result[10] == "component_muted")
assert(original.stopped and original.VolumeMultiplier == 0 and original:IsPlaying() and sound.destroy_calls == 1 and sludge.destroy_calls == 1)
sound.AudioComponent = nil
assert(scene()[3] == "closed" and scene()[8] == "retired" and scene()[10] == "component_absent_after_mute")
sound.AudioComponent = foreign
assert(scene()[3] == "settling" and scene()[8] == "present" and scene()[10] == "component_replaced")
