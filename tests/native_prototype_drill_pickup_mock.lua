package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local function object(class)
    local value = {}
    function value:IsValid() return true end
    function value:GetClass() return class end
    function value:GetAddress() return self end
    return value
end

local drill = require("native_prototype_drill_pickup")
local classes = {}
local function stock(path)
    if not classes[path] then classes[path] = object() end
    return classes[path]
end
StaticFindObject = stock
local world = object()
function world:GetFullName() return "World " .. drill.profile.map .. ".Sci_Tunnel_01_P" end
local pawn = object(stock("/Game/Classes/Player/Player_FP.Player_FP_C"))
local controller = object(stock("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"))
local instance = object(stock("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"))
local save = object(stock("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"))
local statics = stock("/Script/Engine.Default__GameplayStatics")
function statics:GetGameInstance(user) assert(user == pawn); return instance end
function pawn:GetWorld() return world end
function pawn:HasAuthority() return true end
function controller:IsLocalController() return true end
function controller:HasAuthority() return true end
pawn.Save, controller.Pawn, instance.SaveSlot_Ref, instance["As Player FP"] = save, pawn, save, pawn

EFindName = {FNAME_Find = 0}
FName = function(key) return {ToString = function() return key end, GetComparisonIndex = function() return 1 end} end
local step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local objective = "Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083"
local items = {stock(drill.profile.class), GetArrayNum = function() return 1 end}
local quest = {[step] = 1, [objective] = items}
save.ActiveQuests = {[drill.quest] = quest}
local topic, present = 2, true
package.loaded.native_wire_quest = {
    stock_flag = function(actual, flag) assert(actual == instance and flag == drill.flag); return topic, present end,
    stock_row = function(quests, key) return quests[key:ToString()] end,
}

local scope = {map = drill.profile.map, world = world, listening = true, server = false, local_pawn = {pawn = pawn}, controller = controller}
local before = assert(drill.read(scope, "hosting", true))
assert(before.topic == 2 and drill.unchanged(before, drill.read(scope, "hosting", true)))
topic = 1
assert(not drill.unchanged(before, drill.read(scope, "hosting", true)))
topic = 0
assert(not drill.read(scope, "hosting", true))
topic, present = 1, false
assert(not drill.read(scope, "hosting", true))
present = true
quest[step] = 2
assert(not drill.read(scope, "hosting", true) and drill.read(scope, "hosting", false))
quest[step], items[1] = 1, stock("/other/item")
assert(not drill.read(scope, "hosting", true))
assert(not drill.read(scope, "connected", false))
pawn.Save = object(stock("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"))
assert(not drill.read(scope, "hosting", false))

local original_getenv = os.getenv
local function approach_fixture(staged)
    os.getenv = function(name)
        if name == "SILVERFISH_COOP_HEADLESS" or name == "SILVERFISH_COOP_NATIVE_FIXTURES" then return "1" end
        if name == "SILVERFISH_COOP_DRILL_HEAD_CHAMBER_FIXTURE" then return staged and "1" or nil end
        return original_getenv(name)
    end
    package.loaded.native_authored_pickup_input_fixture = nil
    local fixture = require("native_authored_pickup_input_fixture")
    os.getenv = original_getenv
    return fixture
end

local center = {X = 57700, Y = -10650, Z = -3036.265}
local origin = {X = 10350, Y = 260, Z = -2758}
local actor = stock(drill.profile.path)
local placement = stock(drill.profile.map .. ".Sci_Tunnel_01_P:PersistentLevel.StaticMeshActor_1001")
local deck = object(stock("/Script/Engine.StaticMeshComponent"))
local box = object(stock("/Script/Engine.BoxComponent"))
local capsule = object(stock("/Script/Engine.CapsuleComponent"))
local camera = object(stock("/Script/Engine.CameraComponent"))
function actor:GetFullName() return drill.profile.name .. " " .. drill.profile.path end
function actor:GetWorld() return world end
function actor:HasAuthority() return true end
function placement:GetWorld() return world end
function deck:GetOwner() return placement end
function box:GetOwner() return actor end
function box:K2_GetComponentLocation() return center end
function box:GetScaledBoxExtent() return {X = 18, Y = 18, Z = 18} end
function box:GetUpVector() return {X = 0, Y = 0, Z = 1} end
function box:GetRightVector() return {X = 0, Y = 1, Z = 0} end
function capsule:GetOwner() return pawn end
function capsule:GetScaledCapsuleRadius() return 33 end
function capsule:GetScaledCapsuleHalfHeight() return 92 end
function camera:GetOwner() return pawn end
function camera:K2_GetComponentLocation() return {X = origin.X, Y = origin.Y, Z = origin.Z + 50} end
function pawn:K2_GetActorLocation() return origin end
function pawn:K2_GetActorRotation() return {Pitch = 0, Yaw = 0, Roll = 0} end
function pawn:ForceNetUpdate() end
local teleports = 0
function pawn:K2_TeleportTo(point)
    assert(point.Z == -3204 and point.X >= 57800 and point.X <= 57912)
    teleports = teleports + 1
    origin = point
    return true
end
placement.RootComponent = deck
actor.Box, actor.RootComponent = box, box
pawn.Camera, pawn.CapsuleComponent, pawn.InteractionRange, pawn.Dead = camera, capsule, 250, false
scope.pawns = {{pawn = pawn}}

local trace = stock("/Script/Engine.Default__KismetSystemLibrary")
local floor_component, capsule_blocked, sight_blocked = deck, false, false
local queries, swept_from = 0, nil
function trace:LineTraceSingle(_pawn, from, to, channel, _complex, _ignore, _draw, hit)
    queries = queries + 1
    if channel == 4 and not sight_blocked then
        hit.Component = {Get = function() return box end}
        return true
    end
    if channel == 0 and from.X >= 57800 and from.X <= 58400 and from.Y >= -10800 and from.Y <= -10500
        and from.Z > -3300 and to.Z < -3300 then
        hit.ImpactPoint = {X = from.X, Y = from.Y, Z = -3300}
        hit.ImpactNormal = {X = 0, Y = 0, Z = 1}
        hit.Component = {Get = function() return floor_component end}
        return true
    end
    return false
end
function trace:CapsuleTraceSingle(_pawn, from, to, radius, height)
    assert(radius == 33 and height == 92 and to.Z == -3202)
    swept_from = from
    return capsule_blocked
end

local fixture = approach_fixture(false)
local function denied(message)
    local ok, reason = pcall(fixture.precise_approach, scope, "hosting", drill.profile, pawn, actor)
    assert(not ok and reason:find(message, 1, true), tostring(reason))
    assert(teleports == 0)
end

denied("_requires_local_catwalk")
assert(queries == 0)
origin = {X = 57985, Y = -10650, Z = -3208}
floor_component = object()
denied("_requires_local_catwalk")
floor_component, capsule_blocked = deck, true
denied("catwalk_capsule_blocked")
capsule_blocked, sight_blocked = false, true
denied("_no_clear_sight")
sight_blocked = false
assert(fixture.precise_approach(scope, "hosting", drill.profile, pawn, actor) == "stock pickup floor and interaction sight verified")
assert(teleports == 1 and swept_from.X == 57985)
assert(fixture.authority_sight(scope, "hosting", drill.profile, pawn, actor))
sight_blocked = true
assert(not fixture.authority_sight(scope, "hosting", drill.profile, pawn, actor))
assert(not fixture.authority_sight(scope, "hosting", {source = drill.profile.source, map = drill.profile.map}, pawn, actor))

origin, teleports, sight_blocked = {X = 10350, Y = 260, Z = -2758}, 0, false
fixture = approach_fixture(true)
assert(fixture.precise_approach(scope, "hosting", drill.profile, pawn, actor) == "headless Drill Head catwalk staged with stock sight")
assert(teleports == 1 and swept_from.X == origin.X and swept_from.Z == -3204)
