local root = arg[1] or "."
local scripts = root .. "/mod/SilverfishCoop/Scripts/"
local entry = root .. "/bootstrap/SilverfishCoopBootstrap/Scripts/main.lua"
local map = "/Game/Classes/Maps/MainMenu_P"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local title_path = "/Game/Classes/GUI/MainMenu_Widget.MainMenu_Widget_C"
local prompt_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local mode_path = "/Game/Classes/Player/GameMode_Menu.GameMode_Menu_C"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local viewport_path = "/Script/Engine.GameViewportClient"
local classes, callbacks, requests, notices = {}, {}, {}, {}
local widget, next_guid, held, bridge = nil, 0, false, nil

local function object(class, outer)
    local item = {}
    function item:IsValid() return true end
    function item:GetAddress() return self end
    function item:GetClass() return classes[class] end
    function item:GetOuter() return outer end
    return item
end

for _, path in ipairs({controller_path, title_path, prompt_path, mode_path, instance_path, viewport_path, "/Script/UMG.TextBlock"}) do
    classes[path] = object()
end
local world, engine = object(), object()
function world:GetFullName() return "World " .. map .. ".MainMenu_P" end
local controller, mode = object(controller_path), object(mode_path)
function controller:GetWorld() return world end
function controller:IsLocalController() return true end
function controller:HasAuthority() return true end
function controller:IsInputKeyDown(key) assert(key.KeyName.name == "F7"); return held end
function mode:GetWorld() return world end
local instance, viewport = object(instance_path, engine), object(viewport_path, engine)
viewport.World = world
local stock_menu = object(title_path, instance)
function stock_menu:GetOwningPlayer() return controller end
function stock_menu:IsInViewport() return true end
function stock_menu:IsVisible() return true end
local stack = {stock_menu}
function stack:GetArrayNum() return #self end
controller.MenuStack = stack

local statics, widgets, layout, guids = object(), object(), object(), object()
classes["/Script/Engine.Default__GameplayStatics"] = statics
classes["/Script/UMG.Default__WidgetBlueprintLibrary"] = widgets
classes["/Script/UMG.Default__WidgetLayoutLibrary"] = layout
classes["/Script/Engine.Default__KismetGuidLibrary"] = guids
function statics:GetPlayerController(target_world, index) assert(target_world == world and index == 0); return controller end
function statics:GetGameMode(owner) assert(owner == controller); return mode end
function statics:GetGameInstance(owner) assert(owner == controller); return instance end
function layout:GetViewportSize() return {X = 1280, Y = 720} end
function layout:GetViewportScale() return 1 end
function guids:NewGuid()
    next_guid = next_guid + 1
    return {A = next_guid, B = -1, C = 7, D = 14}
end
function widgets:Create(context, class, owner)
    assert(context == controller and owner == controller and class == classes[prompt_path])
    widget = object(prompt_path, instance)
    local notes = object("/Script/UMG.TextBlock")
    widget.TextBlock_Notes = notes
    function notes:SetText(text) self.text = text end
    function notes:GetText() return {ToString = function() return self.text end} end
    function notes:SetColorAndOpacity() end
    function widget:GetOwningPlayer() return owner end
    function widget:SetVisibility(mode) assert(mode == 3) end
    function widget:AddToPlayerScreen(z) assert(z == 110); self.mounted = true; return true end
    function widget:IsInViewport() return self.mounted == true end
    function widget:SetDesiredSizeInViewport() end
    function widget:SetPositionInViewport() end
    function widget:RemoveFromParent() self.mounted = false end
    return widget
end

local env, imports = nil, {}
local function forbidden() error("bootstrap must not access a file or process", 0) end
env = setmetatable({
    print = function(line)
        notices[#notices + 1] = assert(line:match("^%[SilverfishCoopBootstrap%] state=([a-z_]+)\n$"))
    end,
    io = setmetatable({}, {__index = forbidden}),
    os = {getenv = function(name) if name == "SILVERFISH_COOP_BRIDGE" then return bridge end end,
        execute = forbidden, remove = forbidden, rename = forbidden},
    require = function(name)
        assert(name == "native_title_entry" or name == "native_title_display")
        imports[name] = imports[name] or assert(loadfile(scripts .. name .. ".lua", "t", env))()
        return imports[name]
    end,
    FindFirstOf = function(name) assert(name == "GameViewportClient"); return viewport end,
    StaticFindObject = function(path) return classes[path] end,
    FName = function(name) return {name = name, GetComparisonIndex = function() return 1 end} end,
    FText = function(text) return text end,
    EFindName = {FNAME_Find = 0},
    LoopInGameThreadWithDelay = function(delay, callback)
        assert(delay == 33 and not callbacks[1])
        callbacks[1] = callback
    end,
}, {__index = _G})

bridge = "an active gameplay bridge"
assert(loadfile(entry, "t", env))()
assert(not callbacks[1] and not next(imports))
bridge = nil
assert(loadfile(entry, "t", env))()
local tick = assert(callbacks[1])
local function text()
    assert(widget and widget:IsInViewport())
    return widget.TextBlock_Notes.text
end
local function press()
    held = false
    tick()
    held = true
    tick()
end

press()
assert(text() == "Open Co-op\nCoordinator unavailable" and #requests == 0)
assert(notices[#notices] == "unavailable")
local version, generation = 2, ("a"):rep(32)
env.SilverfishReadCoopBootstrapOffer = function() return version, generation end
env.SilverfishRequestCoopBootstrapOpen = function(offer, request)
    assert(offer == generation and #request == 32 and request:match("^[0-9a-f]+$"))
    requests[#requests + 1] = {offer, request}
    return true
end
press()
assert(text():find("unavailable", 1, true) and #requests == 0)
version, generation = 1, ("0"):rep(32)
press()
assert(text():find("unavailable", 1, true) and #requests == 0)
generation = ("a"):rep(32)
tick() -- F7 was held when the offer arrived.
assert(text() == "F7   Open Co-op" and #requests == 0)
press()
assert(requests[1][2] == "00000001ffffffff000000070000000e")
assert(text():find("Opening Co-op", 1, true))
press()
assert(#requests == 1)
generation = nil
press()
assert(text():find("Opening Co-op", 1, true) and #requests == 1)
stack[2] = object()
press()
assert(not widget:IsInViewport() and #requests == 1)
stack[2], world.NetDriver = nil, object()
press()
assert(not widget:IsInViewport() and #requests == 1)
world.NetDriver = nil
press()
assert(text():find("Opening Co-op", 1, true) and #requests == 1)

generation = ("b"):rep(32)
env.SilverfishRequestCoopBootstrapOpen = function(...)
    requests[#requests + 1] = {...}
    error("sensitive coordinator error", 0)
end
press()
assert(text():find("could not accept", 1, true) and #requests == 2)
press()
assert(#requests == 2)
stack[2] = object()
tick()
assert(notices[#notices] == "waiting_title")
stack[2] = nil
function layout:GetViewportScale() return 0 end
tick()
assert(notices[#notices] == "error" and not widget:IsInViewport())
