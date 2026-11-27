-- Exercise navigation on the real title surface with the stock widget calls stubbed.
local types, focus = {}, nil
local clock, viewport = 0, {X = 1280, Y = 720}
local prompt = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local function object(path, parent, outer)
    local value = {path = path, parent = parent, outer = outer, enabled = true, rules = {}, Font = {}}
    function value:IsValid() return true end
    function value:GetAddress() return self end
    function value:GetClass() return types[self.path] end
    function value:GetParent() return self.parent end
    function value:GetOuter() return self.outer end
    function value:GetIsEnabled() return self.enabled end
    function value:SetIsEnabled(enabled) self.enabled = enabled end
    function value:SetIsChecked(checked) self.checked = checked end
    function value:IsChecked() return self.checked end
    function value:SetText(text) self.text = text end
    function value:GetText() return {ToString = function() return self.text end} end
    function value:SetContent(content) content.parent = self end
    function value:SetUserFocus() focus = self end
    function value:HasUserFocus() return focus == self end
    function value:HasUserFocusedDescendants() return self.path == prompt and focus ~= nil end
    function value:SetNavigationRuleExplicit(direction, destination) self.rules[direction] = destination end
    function value:SetNavigationRuleBase(direction, rule) self.rules[direction] = rule end
    function value:AddChildToCanvas(child)
        child.parent = self
        local slot = object("/Script/UMG.CanvasPanelSlot", nil, self)
        function slot:SetPosition(position) self.position = position end
        function slot:SetSize(size) self.size = size end
        return slot
    end
    for _, method in ipairs({"SetFont", "SetColorAndOpacity", "SetAutoWrapText", "SetVisibility", "SetBrushColor",
        "SetPosition", "SetSize", "SetZOrder", "SetForegroundColor", "SetHintText", "SetIsReadOnly", "SetIsPassword",
        "SetClipping", "SetJustification"}) do
        value[method] = function() end
    end
    value.WidgetStyle = {TextStyle = {Font = {}}}
    return value
end

local names = {"WidgetTree", "EditableTextBox", "TextBlock", "CheckBox", "CanvasPanel", "CanvasPanelSlot", "Border",
    "Widget:SetNavigationRuleExplicit", "Widget:SetNavigationRuleBase"}
for _, name in ipairs(names) do types["/Script/UMG." .. name] = object() end

types[prompt] = object()
local widgets, layout = object(), object()
types["/Script/UMG.Default__WidgetBlueprintLibrary"] = widgets
types["/Script/UMG.Default__WidgetLayoutLibrary"] = layout
function layout:GetViewportSize() return viewport end
function layout:GetViewportScale() return 1 end
function widgets:Create(_, _, controller)
    local tree = object("/Script/UMG.WidgetTree")
    tree.RootWidget = object("/Script/UMG.CanvasPanel", nil, tree)
    local widget = object(prompt)
    widget.WidgetTree = tree
    widget.TextBlock_Notes = object("/Script/UMG.TextBlock")
    function widget:GetOwningPlayer() return controller end
    function widget:AddToViewport() self.mounted = true end
    function widget:IsInViewport() return self.mounted end
    function widget:RemoveFromParent() focus = nil; self.mounted = false end
    function widget:SetDesiredSizeInViewport(size) self.size = size end
    function widget:SetPositionInViewport(position, remove_dpi)
        assert(remove_dpi == false)
        self.position = position
    end
    function widget:SetRenderTransformPivot(pivot) self.pivot = pivot end
    function widget:SetRenderScale(scale) self.scale = scale.X end
    return widget
end

local controls = {}
function controls.title_reference() end
function controls.remove() end
function controls.create(_, canvas, _, callback)
    return {inner = object(), slot = canvas:AddChildToCanvas(object()), callback = callback}
end

local display
local env = setmetatable({
    StaticFindObject = function(path) return types[path] end,
    StaticConstructObject = function(class, tree)
        for path, matching in pairs(types) do
            if matching == class then return object(path, nil, tree) end
        end
    end,
    FText = function(value) return value end,
    os = {clock = function() return clock end},
    require = function(name)
        return assert(({native_title_display = display, native_menu_controls = controls})[name])
    end,
}, {__index = _G})
display = assert(loadfile("mod/SilverfishCoop/Scripts/native_title_display.lua", "t", env))()
local surface = assert(loadfile("mod/SilverfishCoop/Scripts/native_title_surface.lua", "t", env))()
local spec = {title = "Host settings", focus = "use_password", rows = {
    {field = {key = "campaign", label = "Campaign name"}},
    {toggle = {key = "use_password", label = "Use password", target = "password"}},
    {field = {key = "password", label = "Password", secret = true}},
    {{key = "start", label = "Start"}, {key = "back", label = "Back"}},
}}
local dispatched
local view = surface.mount({controller = object()}, spec, function(action) dispatched = action end)
local toggle = view.toggles.use_password.widget
local password = view.inputs.password.widget
local campaign = view.inputs.campaign.widget
local start, back = view.buttons.start.inner, view.buttons.back.inner
assert(focus == toggle and password.enabled == false)
assert(toggle.rules[3] == start and toggle.rules[4] == start and start.rules[5] == toggle)
assert(start.rules[1] == back and back.rules[0] == start and start.rules[0] == 3)
assert(back.rules[4] == campaign and campaign.rules[5] == back)

assert(surface.set_checked(view, "use_password", true) and surface.sync(view))
assert(password.enabled and toggle.rules[4] == password and password.rules[4] == start)
password:SetUserFocus(view.context.controller)
assert(surface.set_checked(view, "use_password", false) and surface.sync(view))
assert(focus == toggle, "Disabling the active field must leave focus on its password toggle")
assert(not password.enabled and toggle.rules[4] == start and start.rules[5] == toggle)

viewport, clock = {X = 360, Y = 245}, 1
assert(surface.sync(view) and focus == toggle and view.buttons.back.inner == back)
local position, size, fit = view.widget.position, view.widget.size, view.widget.scale
assert(fit < 1 and view.widget.pivot.X == 0 and view.widget.pivot.Y == 0)
assert(position.X >= 12 and position.X + size.X * fit <= viewport.X - 12)
assert(position.Y >= 12 and position.Y + size.Y * fit <= viewport.Y - 12)
viewport, clock = {X = 0, Y = 0}, 2
assert(surface.sync(view) and view.widget.position == position)
viewport, clock = {X = 1280, Y = 720}, 3
assert(surface.sync(view) and view.widget.scale == 1 and focus == toggle)

focus = nil
view.buttons.back.callback(view.buttons.back)
assert(dispatched == "back" and focus == back)
surface.remove(view)
view.buttons.start.callback(view.buttons.start)
assert(dispatched == "back" and focus == nil)

viewport, clock = {X = 300, Y = 200}, 4
local rail = surface.mount({controller = object()}, {title = "Project Silverfish", navigation = true,
    rows = {{{key = "singleplayer", label = "Singleplayer"}}, {{key = "multiplayer", label = "Multiplayer"}},
        {{key = "quit", label = "Quit"}}}}, function() end)
local function rail_fills_left_edge()
    local frame = rail.frame
    assert(frame.x + rail.rail.position.X * frame.fit == 0)
    assert(frame.y + rail.rail.position.Y * frame.fit == 0)
    assert(math.abs(rail.rail.size.Y * frame.fit - viewport.Y) < 0.001)
end
assert(rail.frame.fit < 1)
rail_fills_left_edge()
viewport, clock = {X = 1280, Y = 720}, 5
assert(surface.sync(rail) and rail.frame.fit == 1)
rail_fills_left_edge()
