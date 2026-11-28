local map = "/Game/Classes/Maps/MainMenu_P"
local world_path = map .. ".MainMenu_P"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local mode_path = "/Game/Classes/Player/GameMode_Menu.GameMode_Menu_C"
local widget_path = "/Game/Classes/GUI/MainMenu_Widget.MainMenu_Widget_C"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local viewport_path = "/Script/Engine.GameViewportClient"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local classes, address = {}, 0

local function object(name, class, outer)
    address = address + 1
    local id = address
    return {
        GetAddress = function() return id end,
        GetClass = function() return classes[class] end,
        GetFullName = function() return name end,
        GetOuter = function() return outer end,
        IsValid = function() return true end,
    }
end

for _, class in ipairs({controller_path, mode_path, widget_path, instance_path, viewport_path}) do
    classes[class] = object("Class " .. class)
end
local world = object("World " .. world_path)
local engine = object("GameEngine /Engine/Transient.GameEngine_1")
local instance = object("GameInstance_FP_C /Engine/Transient.GameEngine_1:GameInstance_FP_C_2", instance_path, engine)
local viewport = object("GameViewportClient /Engine/Transient.GameEngine_1:GameViewportClient_3", viewport_path, engine)
viewport.World = world
local controller = object("PlayerControler_FP_C " .. world_path .. ":PersistentLevel.PlayerControler_FP_C_4", controller_path)
controller.GetWorld = function() return world end
controller.IsLocalController = function() return true end
controller.HasAuthority = function() return true end
controller.Loading = true -- The stock title controller defaults to true.
local mode = object("GameMode_Menu_C " .. world_path .. ":PersistentLevel.GameMode_Menu_C_5", mode_path)
mode.GetWorld = function() return world end
local statics = object("stock gameplay statics")
classes[statics_path] = statics
function statics:GetGameMode(player) assert(player == controller); return mode end
function statics:GetGameInstance(player) assert(player == controller); return instance end
local menu = object("MainMenu_Widget_C /Engine/Transient.GameEngine_1:GameInstance_FP_C_2.MainMenu_Widget_C_6",
    widget_path, instance)
local visible, mounted, owner = true, true, controller
menu.GetOwningPlayer = function() return owner end
menu.IsInViewport = function() return mounted end
menu.IsVisible = function() return visible end
local stack = {}
stack.GetArrayNum = function() return #stack end
controller.MenuStack = stack
local environment = setmetatable({
    StaticFindObject = function(key) return classes[key] end,
    FindFirstOf = function(kind) assert(kind == "GameViewportClient"); return viewport end,
}, {__index = _G})
local title = assert(loadfile("mod/SilverfishCoop/Scripts/native_title_entry.lua", "t", environment))()
local state = {map = map, world = world, controller = controller, server = false, clients = 0}

local function status(phase)
    return assert(title.rows(state, phase or "ready", table.pack)[1])
end

assert(status()[11] == "stock_menu_not_created")
stack[1] = menu
local ready = status()
assert(ready.n == 11 and ready[1] == "native-title-entry" and ready[3] == "ready")
assert(ready[4] == controller:GetFullName() and ready[5] == menu:GetFullName() and ready[6] == viewport:GetFullName())
assert(ready[7] == 1 and ready[8] == 1 and ready[9] == 1 and ready[10] == 1 and ready[11] == "-")
assert(title.probe(state, "ready").menu == menu and status("opening")[11] == "stock_title_opening")

stack[2], visible = object("other stock modal"), false
local covered = status()
assert(covered[8] == 0 and covered[9] == 1 and covered[10] == 0 and covered[11] == "stock_menu_below_dialog")
assert(title.probe(state, "ready") == nil)
stack[2], mounted = nil, false
assert(status()[11] == "stock_menu_not_mounted")
mounted = true
assert(status()[11] == "stock_menu_hidden")
visible, owner = true, object("wrong controller")
assert(status()[11] == "stock_menu_owner_unqualified")
owner, stack[2] = controller, menu
assert(status()[11] == "stock_menu_ambiguous")
stack[2], state.listening = nil, true
assert(status()[11] == "standalone_title_required")
state.listening, viewport.World = nil, object("another map")
assert(status()[11] == "stock_viewport_unavailable")
state.map = "/Game/Classes/Maps/Other"
assert(#title.rows(state, "ready", table.pack) == 0 and not title.probe(state, "ready"))

state.map, viewport.World = map, world
-- A qualified native refusal must stay authoritative even if the Lua fallback sees a ready title.
environment.SilverfishObserveNativeTitle = function(current_world, current_controller, phase)
    assert(current_world == world and current_controller == controller and phase == "ready")
    return "waiting", "stock_menu_below_dialog", menu, viewport, 2, false, true, false
end
local covered_row = "native-title-entry\t" .. map .. "\twaiting"
environment.SilverfishNativeTitleRows = function() return covered_row end
assert(not title.probe(state, "ready") and not title.starting(state, "ready"))
local native = title.rows(state, "ready", table.pack)
assert(#native == 1 and native[1] == covered_row)

environment.SilverfishObserveNativeTitle = function() end
environment.SilverfishNativeTitleRows = function() end
assert(title.probe(state, "ready").menu == menu and status()[3] == "ready")
