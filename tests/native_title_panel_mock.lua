local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local descriptor, request = "bridge/title-handoff-session.tsv", "bridge/title-handoff-request.tsv"
local generation = string.rep("a", 32)
local files, buttons, classes, flags = {}, {}, {}, {}
local now, id, callback, display, writes = 100, 0, nil, nil, 0
local function object(class)
    id = id + 1
    local address = id
    return {IsValid = function() return true end, GetAddress = function() return address end,
        GetClass = function() return classes[class] end}
end
classes[widget_path], classes["/Script/UMG.TextBlock"] = object(), object()
local context = {world = object(), viewport = object(), menu = object(), controller = object()}
function context.controller:IsInputKeyDown(key) return buttons[key.KeyName] == true end
local statics = object()
function statics:Create(controller, class, owner)
    assert(controller == context.controller and owner == controller and class == classes[widget_path])
    local widget = object(widget_path)
    local note = object("/Script/UMG.TextBlock")
    widget.TextBlock_Notes = note
    function note:SetText(value) self.text = value end
    function note:GetText() return {ToString = function() return self.text end} end
    function note:SetColorAndOpacity(color) assert(color.SpecifiedColor.B == 1) end
    function widget:GetOwningPlayer() return owner end
    function widget:SetVisibility(mode) assert(mode == 3) end
    function widget:AddToPlayerScreen(z) assert(z == 110); self.mounted = true; return true end
    function widget:IsInViewport() return self.mounted == true end
    function widget:SetDesiredSizeInViewport() end
    function widget:SetPositionInViewport() end
    function widget:RemoveFromParent() self.mounted = false end
    display = widget
    return widget
end
local layout, guid = object(), object()
layout.GetViewportSize = function() return {X = 1280, Y = 720} end
layout.GetViewportScale = function() return 1 end
guid.NewGuid = function() return {A = 1, B = -1, C = 2, D = 3} end
classes["/Script/UMG.Default__WidgetBlueprintLibrary"] = statics
classes["/Script/UMG.Default__WidgetLayoutLibrary"] = layout
classes["/Script/Engine.Default__KismetGuidLibrary"] = guid
local env = setmetatable({
    require = function(name)
        assert(name == "native_title_entry")
        return {probe = function(state, phase) if state.qualified and phase == "ready" then return context end end}
    end,
    StaticFindObject = function(path) return classes[path] end,
    io = {open = function(path, mode)
        if mode == "rb" then
            if not files[path] then return end
            return {read = function(_, size) return files[path]:sub(1, size) end, close = function() return true end}
        end
        assert(mode == "wb" and path:sub(1, #request) == request)
        return {write = function(_, ...) files[path] = table.concat({...}); writes = writes + 1; return true end,
            close = function() return true end}
    end},
    os = {time = function() return now end, getenv = function(name) return flags[name] end,
        remove = function(path) files[path] = nil end,
        rename = function(source, target)
            assert(target == request)
            if files[target] then return false end
            files[target], files[source] = files[source], nil
            return true
        end},
    FName = function(name) return setmetatable({GetComparisonIndex = function() return 1 end}, {
        __eq = function() return false end, __tostring = function() return name end}) end,
    EFindName = {FNAME_Find = 0},
    FText = function(text) return text end,
    LoopInGameThreadWithDelay = function(_, tick) callback = tick; return 1 end,
}, {__index = _G})
local old_input = context.controller.IsInputKeyDown
context.controller.IsInputKeyDown = function(self, key) return old_input(self, {KeyName = tostring(key.KeyName)}) end
local panel = assert(loadfile("mod/SilverfishCoop/Scripts/native_title_panel.lua", "t", env))()
panel.start("bridge")
local state = {qualified = true, map = "/Game/Classes/Maps/MainMenu_P"}
local function sync() panel.sync(state, "ready") end
local function report() return panel.rows(state, "ready", table.pack)[1] end
local function offer(choices, until_time, value)
    files[descriptor] = ("1\t%s\t%s\t%d\n"):format(value or generation, choices, until_time)
    sync()
end
local function press(key)
    buttons[key] = false
    callback()
    buttons[key] = true
    callback()
end
local function fixture(action, phase)
    return pcall(panel.command, {"test", "native-title-panel-press-fixture", action}, state, phase or "ready")
end

sync()
assert(not display and not report())
offer("host,join", 111)
assert(not display)
buttons.F7 = true
offer("host", 108)
assert(report().n == 6 and report()[3] == "ready" and report()[4] == "host" and report()[5] == 1)
assert(display.TextBlock_Notes.text:find("F7", 1, true) and not display.TextBlock_Notes.text:find("F8", 1, true))
local allowed, reason = fixture("host")
assert(not allowed and reason == "title_panel_fixture_unavailable")
flags.SILVERFISH_COOP_HEADLESS = "1"
allowed, reason = fixture("host")
assert(not allowed and reason == "title_panel_fixture_unavailable")
flags.SILVERFISH_COOP_TITLE_FIXTURES = "1"
allowed, reason = fixture("join")
assert(not allowed and reason == "title_action_not_offered")
allowed, reason = fixture("host", "opening")
assert(not allowed and reason == "title_panel_not_ready")
callback()
assert(not files[request]) -- Held before mount.
press("F8")
assert(not files[request]) -- Unadvertised.
press("F7")
assert(files[request] == "1\t" .. generation .. "\t00000001ffffffff0000000200000003\thost\n")
assert(report()[3] == "pending" and writes == 1)
allowed, reason = fixture("host")
assert(not allowed and reason == "title_request_already_pending")
press("F7")
assert(writes == 1)

offer("join", 108, string.rep("b", 32))
press("F8")
assert(report()[3] == "unavailable" and writes == 1) -- Existing request was never overwritten.
press("F9")
assert(report()[3] == "hidden" and report()[5] == 0 and not display.mounted)
sync()
assert(report()[3] == "hidden" and not display.mounted)

files[request] = nil
offer("host,join", 101, string.rep("c", 32))
callback()
now = 102
callback()
assert(not display.mounted and not files[request])
now = 100
offer("join", 108, string.rep("d", 32))
callback()
state.qualified = false
press("F8")
assert(not display.mounted and not files[request] and not report())
state.qualified = true
offer("join", 108, string.rep("e", 32))
assert(fixture("join") and report()[3] == "pending" and writes == 2)
assert(files[request]:match("\tjoin\n$"))
