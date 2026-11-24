local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local name = "Player_FP_C " .. map .. ".Observitory_P:PersistentLevel.Player_FP_C_0"
local draft = string.rep("a", 32)
local now, downed, poll = 100, true
local events, addresses = {}, 0

local function object(class)
    addresses = addresses + 1
    local address = addresses
    return {
        IsValid = function() return true end,
        GetAddress = function() return address end,
        GetClass = function() return class end,
    }
end

local function array()
    return {GetArrayNum = function(self) return #self end}
end

local controller_class, pawn_class = object(), object()
local stock_class, pause_class, journal_class, unrelated_class = object(), object(), object(), object()
local world, controller, pawn = object(), object(controller_class), object(pawn_class)
world.NetDriver = object()
world.GetFullName = function() return "World " .. map .. ".Observitory_P" end
controller.MenuStack, controller.Loading, controller.Pawn = array(), false, pawn
controller.IsLocalController = function() return true end
controller.GetWorld, pawn.GetWorld = function() return world end, function() return world end
pawn.Controller = controller
pawn.IsActorBeingDestroyed = function() return false end
pawn.HasAuthority, pawn.IsLocallyControlled = function() return true end, function() return true end
pawn.GetFullName = function() return name end

local function control()
    return {enabled = true, SetIsEnabled = function(self, value) self.enabled = value end}
end

local function menu(class)
    local widget = object(class)
    widget.GetOwningPlayer = function() return controller end
    widget.IsInViewport = function(self) return self.mounted end
    widget.IsVisible = function(self) return self.visible end
    widget.RemoveFromParent = function(self) self.mounted = false end
    widget.PlayerInventory, widget.ItemsToKeep, widget.KeptItemSlots = array(), array(), array()
    widget.InventoryPanel, widget.ItemManager, widget.Button_Continue = control(), control(), control()
    widget.InventoryPanel.Cards = array()
    widget.InventoryPanel.FullRefresh = function() end
    widget.InventoryPanel.SetQuickMoveTargets = function() end
    widget.CreateKeepSlot = function(self, index) self.KeptItemSlots[index + 1] = control() end
    return widget
end

function controller:AddMenu(widget)
    local stack = self.MenuStack
    if stack[#stack] then stack[#stack].visible = false end
    stack[#stack + 1] = widget
    widget.mounted, widget.visible = true, true
end

function controller:CloseMenu(result)
    local stack = self.MenuStack
    local widget = stack[#stack]
    assert(widget and not widget["Is Unclosable"])
    table.remove(stack):RemoveFromParent()
    if stack[#stack] then stack[#stack].visible = true end
    result.Success = true
end

local paths = {
    ["/Game/Classes/Player/Player_FP.Player_FP_C"] = pawn_class,
    ["/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"] = controller_class,
    ["/Game/Classes/GUI/GameOver_RespawnPenalties_Widget.GameOver_RespawnPenalties_Widget_C"] = stock_class,
    ["/Game/Classes/GUI/Menu_Pause_Widget.Menu_Pause_Widget_C"] = pause_class,
    ["/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C"] = journal_class,
    ["/Script/Engine.Default__GameplayStatics"] = {IsValid = function() return true end,
        GetRealTimeSeconds = function() return now end},
    ["/Script/UMG.Default__WidgetBlueprintLibrary"] = {IsValid = function() return true end,
        Create = function(_, owner, class, player)
            assert(owner == pawn and player == controller and class == stock_class)
            return menu(class)
        end},
}
local pending_actions = 2
local env = setmetatable({
    os = {getenv = function() return "1" end, time = function() return now end},
    io = {open = function(_, mode)
        assert(mode == "ab")
        return {write = function(_, text) events[#events + 1] = text return true end,
                close = function() return true end}
    end},
    FText = function(value) return value end,
    StaticFindObject = function(path) return paths[path] end,
    SilverfishArmNativeWipeScreen = function() pending_actions = 2 return true end,
    SilverfishTakeNativeWipeScreenActions = function() local result = pending_actions; pending_actions = 0; return result end,
    LoopInGameThreadWithDelay = function(_, callback) poll = callback end,
}, {__index = _G})
local view = assert(loadfile("mod/SilverfishCoop/Scripts/native_wipe_view.lua", "t", env))()
view.start("private", function(owner) assert(owner == pawn); return downed end)
local state = {map = map, world = world, controller = controller, listening = true, server = false,
    local_pawn = {pawn = pawn, controller_object = controller, name = name}}

local function command(action, ...)
    return view.command({"1", "native-wipe-view-" .. action, draft, map, name, "host", ...}, state, "hosting")
end

local function status()
    local rows = view.rows(state, "hosting", function(...) return {...} end)
    return rows[1] and rows[1][2] or nil
end

local pause, journal, modal = menu(pause_class), menu(journal_class), menu(unrelated_class)
controller:AddMenu(pause)
controller:AddMenu(journal)
controller:AddMenu(modal)
command("render", "0", "1", "0", "0", "0", "-")
assert(status() == "opening" and controller.MenuStack[3] == modal)
controller:CloseMenu({})
now = now + .3
poll()
assert(status() == "selecting" and not pause.mounted and not journal.mounted)
local screen = controller.MenuStack[1]
pending_actions = 1
poll()
assert(status() == "submitting" and #events == 1)
command("result", "accepted")
assert(status() == "accepted" and screen:IsInViewport() and not screen.Button_Continue.enabled
    and not screen.InventoryPanel.enabled and not screen.ItemManager.enabled and not screen.KeptItemSlots[1].enabled)
pending_actions = 1
poll()
assert(status() == "accepted" and #events == 1)
command("lease", "0")
now = now + 2
poll()
assert(not status() and #controller.MenuStack == 0 and not screen.mounted)
controller:AddMenu(menu(journal_class))
command("render", "0", "1", "0", "0", "0", "0")
assert(status() == "accepted" and controller.MenuStack[1]:GetClass() == stock_class)
downed = false
view.sync(state, "hosting")
assert(not status() and #controller.MenuStack == 0)
