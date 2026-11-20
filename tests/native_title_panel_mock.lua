local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local descriptor, request = "bridge/title-handoff-session.tsv", "bridge/title-handoff-request.tsv"
local generation = string.rep("a", 32)
local files, buttons, classes, flags = {}, {}, {}, {}
local now, id, callback, display, writes = 100, 0, nil, nil, 0
local refuse_rename = false
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
local editing
local editor = {
    mount = function(frame, phase, field, caption, initial)
        assert(not editing and frame.qualified and phase == "ready" and caption:find(field == "bind" and "Listening IP"
            or field == "campaign" and "Campaign" or "UDP port", 1, true))
        editing = {field = field, text = initial}
        return editing
    end,
    read = function(handle, frame, phase)
        assert(handle == editing and frame.qualified and phase == "ready")
        return handle.text
    end,
    write = function(handle, frame, phase, value)
        assert(handle == editing and frame.qualified and phase == "ready")
        handle.text = value
        return value
    end,
    focus = function(handle) assert(handle == editing); return false end,
    remove = function(handle) assert(handle == editing); editing = nil; return true end,
}
local env
env = setmetatable({
    require = function(name)
        if name == "native_title_display" then
            return assert(loadfile("mod/SilverfishCoop/Scripts/native_title_display.lua", "t", env))()
        end
        if name == "native_title_host_form" then
            return assert(loadfile("mod/SilverfishCoop/Scripts/native_title_host_form.lua", "t", env))()
        end
        if name == "native_title_settings_widget" then return editor end
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
            if refuse_rename or files[target] then return false end
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
assert(not files[request] and editing.field == "campaign" and editing.text == "")
callback()
assert(not files[request]) -- A held F7 cannot start the new form.
press("F9")
assert(not files[request] and not editing and display.TextBlock_Notes.text:find("Host a campaign", 1, true))
assert(fixture("host"))
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

local function form_fixture(operation, value)
    return pcall(panel.command, {"test", "native-title-host-form-fixture", operation, value}, state, "ready")
end

files[request] = nil
local edited_generation = string.rep("f", 32)
offer("host", 108, edited_generation)
flags.SILVERFISH_COOP_TITLE_FIXTURES = nil
allowed, reason = form_fixture("open")
assert(not allowed and reason == "title_host_form_fixture_unavailable")
flags.SILVERFISH_COOP_TITLE_FIXTURES = "1"
assert(form_fixture("open"))
assert(form_fixture("campaign", "title-host-settings-1234abcd"))
assert(form_fixture("port", "0"))
allowed, reason = form_fixture("submit")
assert(not allowed and reason == "title_host_form_invalid_port" and editing.text == "0" and not files[request])
assert(form_fixture("cancel") and not editing and not files[request])
assert(form_fixture("open"))
assert(form_fixture("campaign", "title-host-settings-1234abcd"))
assert(form_fixture("port", "28181"))
assert(form_fixture("submit") and not editing and report()[3] == "pending")
assert(files[request] == "2\t" .. edited_generation
    .. "\t00000001ffffffff0000000200000003\thost\ttitle-host-settings-1234abcd\t\t28181\n")

files[request] = nil
local physical_generation = string.rep("9", 32)
offer("host", 108, physical_generation)
press("F7")
press("F8")
assert(editing.field == "port" and editing.text == "")
press("F8")
assert(editing.field == "bind" and editing.text == "")
editing.text = "::ffff:500.0.0.1"
press("F7")
assert(not files[request] and editing.text == "::ffff:500.0.0.1")
editing.text = "2001:db8::1"
press("F7")
assert(files[request] == "2\t" .. physical_generation
    .. "\t00000001ffffffff0000000200000003\thost\t\t2001:db8::1\t\n")

files[request] = nil
offer("host", 108, string.rep("8", 32))
assert(form_fixture("open"))
refuse_rename = true
allowed, reason = form_fixture("submit")
assert(not allowed and reason == "title_request_write_failed" and not editing and not files[request])
assert(not files[request .. ".00000001ffffffff0000000200000003.tmp"])
refuse_rename = false
assert(form_fixture("open"))
assert(form_fixture("cancel") and not files[request])

local native_keys = {}
env.Key = {F7 = "F7", F8 = "F8", F9 = "F9"}
env.RegisterKeyBind = function(key, handler) native_keys[key] = handler end
local focused = false
env.SilverfishNativeGameWindowFocused = function() return focused end
flags.SILVERFISH_COOP_HEADLESS = nil
panel = assert(loadfile("mod/SilverfishCoop/Scripts/native_title_panel.lua", "t", env))()
panel.start("bridge")
local event_generation = string.rep("7", 32)
offer("host", 108, event_generation)
native_keys.F7()
callback()
assert(not editing and not files[request])
focused = true
native_keys.F7()
callback()
assert(editing.field == "campaign" and not files[request])
callback()
assert(not files[request])
state.qualified = false
native_keys.F7()
callback()
assert(not editing and not files[request])
state.qualified = true
offer("host", 108, event_generation)
native_keys.F7()
callback()
native_keys.F7()
callback()
assert(files[request] == "2\t" .. event_generation .. "\t00000001ffffffff0000000200000003\thost\t\t\t\n")
