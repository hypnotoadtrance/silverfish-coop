package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local actor_class = "/Game/Classes/Actors/Barter_BP.Barter_BP_C"
local npc_class = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local data_class = "/Game/Classes/Libraries/DataAssets/Merchants/MerchantData.MerchantData_C"
local screen_class = "/Game/Classes/GUI/New/BarterScreen.BarterScreen_C"
local add_menu = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C:AddMenu"
local world, opened_hook
local assets = {}
local function stock(path)
    if not assets[path] then assets[path] = {IsValid = function() return true end, GetAddress = function(self) return self end} end
    return assets[path]
end
local function object(class)
    return {
        IsValid = function() return true end, GetAddress = function(self) return self end,
        GetClass = function() return stock(class) end, GetWorld = function() return world end,
        IsActorBeingDestroyed = function() return false end,
        K2_GetActorLocation = function() return {X = 0, Y = 0, Z = 0} end,
    }
end
world = object()
local function array()
    return {
        GetArrayNum = function(self) return #self end,
        Empty = function(self) for i = #self, 1, -1 do self[i] = nil end end,
        ForEach = function() end,
    }
end
local function make_trader(number)
    local actor, trader = object(npc_class), object(actor_class)
    actor.BarterRef, actor.Alive, actor.IsA = trader, true, function() return true end
    trader.LinkedNPC, trader.MerchantData = actor, object(data_class)
    trader.Inventory, trader.SpecificPreferences, trader.RngSeed = array(), array(), 10
    trader.TraderID = {A = number, B = 0, C = 0, D = 0}
    trader.Save, trader.MinWeaponValue, trader.AcceptCash = true, 1, true
    return trader
end
local pawn, controller = object(), object()
pawn.Dead, pawn.InteractionRange = false, 180
pawn.GetFullName = function() return "Player_FP_C Guest" end
controller.Pawn, controller.Loading, controller.MenuStack = pawn, false, array()
controller.IsLocalController = function() return true end
function controller:AddMenu(menu)
    self.MenuStack[1] = menu
    opened_hook({get = function() return self end})
end
function controller:CloseMenu()
    self.MenuStack[1] = nil
end
local function screen(trader)
    local menu = object(screen_class)
    menu.Trader, menu.opacity, menu.enabled = trader, 1, true
    function menu:GetOwningPlayer() return controller end
    function menu:SetRenderOpacity(value) self.opacity = value end
    function menu:SetIsEnabled(value) self.enabled = value end
    function menu:IsInViewport() return controller.MenuStack[1] == self end
    return menu
end

StaticFindObject = stock
FName = function(name) return name end
RegisterHook = function(path, fn)
    assert(path == add_menu)
    opened_hook = fn
    return 1
end
stock("/Script/Engine.Default__GameplayStatics").CreateSaveGameObject = function()
    local frame = object()
    frame.PlayerBackpack = array()
    return frame
end
stock("/Script/UMG.Default__WidgetBlueprintLibrary").Create = function() return screen() end
local save = object()
save.PlayerBackpack = array()
save.Flags = {
    Contains = function(self, name) return self[name] ~= nil end,
    Remove = function(self, name) self[name] = nil end,
    Add = function(self, name, value) self[name] = value end,
    Find = function(self, name) return {get = function() return self[name] end} end,
}
pawn.Save = save
package.loaded.guest_save, package.loaded.native_wire_quest = {}, {}
package.loaded.native_save_scope = {bind_guest = function() return save, pawn end}
SilverfishSetNativeNpcBarter = function() return true end
SilverfishSetNativeNpcBarterScreen = function() return true end
local sequence = 0
SilverfishCaptureNativeNpcBarterMenu = function() sequence = sequence + 1; return sequence end
local barter = require("native_npc_barter")
local scene = {controller = controller, local_pawn = {pawn = pawn}, world = world, server = true, map = "/Game/Test"}
local function project(trader, cash)
    local identifier = ("%08X"):format(trader.TraderID.A) .. string.rep("0", 24)
    local command = {"request", "native-npc-barter-project", tostring(sequence), identifier, tostring(cash), "31", "2", "1"}
    assert(barter.command(command, scene, "connected") == "exact stock NPC barter screen shown")
    local verified = controller.MenuStack[1]
    assert(verified.Trader == trader and verified.opacity == 1 and verified.enabled and save.Flags.Player_Cash == cash)
    return verified
end
local first, second = make_trader(1), make_trader(2)
barter.sync(scene, "connected")
local initial = screen(first)
controller:AddMenu(initial)
assert(initial.opacity == 0 and not initial.enabled)
barter.sync(scene, "connected")
local verified = project(first, 1200)
controller:AddMenu(verified)
assert(verified.opacity == 1 and verified.enabled)

controller:CloseMenu()
local reopened = screen(first)
assert(first.Save == false, "the prior projected view must still retain its stock")
controller:AddMenu(reopened)
assert(reopened.opacity == 0 and not reopened.enabled, "reopening the same trader exposes provisional stock")
barter.sync(scene, "connected")
project(first, 1300)

controller:CloseMenu()
local switched = screen(second)
controller:AddMenu(switched)
assert(switched.opacity == 0 and not switched.enabled, "opening a different qualified trader exposes local stock")
barter.sync(scene, "connected")
barter.sync(scene, "connected")
project(second, 1800)
assert(first.Save == true and second.Save == false, "only the current trader retains a host projection")

controller:CloseMenu()
local unrelated = screen(make_trader(3))
unrelated.Trader.LinkedShop = object()
controller:AddMenu(unrelated)
assert(unrelated.opacity == 1 and unrelated.enabled)
controller:CloseMenu()
barter.sync(scene, "singleplayer")
local singleplayer = screen(first)
controller:AddMenu(singleplayer)
assert(singleplayer.opacity == 1 and singleplayer.enabled)
print("native NPC barter replacement view verified")
