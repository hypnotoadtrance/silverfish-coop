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
