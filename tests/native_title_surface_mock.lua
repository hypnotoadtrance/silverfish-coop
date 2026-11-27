-- Exercise navigation on the real title surface with the stock widget calls stubbed.
local types, focus = {}, nil
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
    function value:SetContent(content) content.parent = self end
    function value:SetUserFocus() focus = self end
    function value:HasUserFocusedDescendants() return focus ~= nil end
    function value:SetNavigationRuleExplicit(direction, destination) self.rules[direction] = destination end
    function value:SetNavigationRuleBase(direction, rule) self.rules[direction] = rule end
    function value:AddChildToCanvas(child)
        child.parent = self
        return object("/Script/UMG.CanvasPanelSlot", nil, self)
    end
    for _, method in ipairs({"SetFont", "SetColorAndOpacity", "SetAutoWrapText", "SetVisibility", "SetBrushColor",
        "SetPosition", "SetSize", "SetZOrder", "SetForegroundColor", "SetHintText", "SetIsReadOnly", "SetIsPassword",
        "SetClipping"}) do
        value[method] = function() end
    end
    value.WidgetStyle = {TextStyle = {Font = {}}}
    return value
end

local names = {"WidgetTree", "EditableTextBox", "TextBlock", "CheckBox", "CanvasPanel", "CanvasPanelSlot", "Border",
    "Widget:SetNavigationRuleExplicit", "Widget:SetNavigationRuleBase"}
for _, name in ipairs(names) do types["/Script/UMG." .. name] = object() end

local display = {}
function display.mount()
    local tree = object("/Script/UMG.WidgetTree")
    tree.RootWidget = object("/Script/UMG.CanvasPanel", nil, tree)
    local widget = object()
    widget.WidgetTree = tree
    return widget, {}
end
function display.remove() focus = nil end

local controls = {}
function controls.title_reference() end
function controls.remove() end
function controls.create(_, canvas, _, callback)
    return {inner = object(), slot = canvas:AddChildToCanvas(object()), callback = callback}
end

local env = setmetatable({
    StaticFindObject = function(path) return types[path] end,
    StaticConstructObject = function(class, tree)
        for path, matching in pairs(types) do
            if matching == class then return object(path, nil, tree) end
        end
    end,
    FText = function(value) return value end,
    require = function(name)
        return assert(({native_title_display = display, native_menu_controls = controls})[name])
    end,
}, {__index = _G})
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
assert(surface.set_checked(view, "use_password", false) and surface.sync(view))
assert(not password.enabled and toggle.rules[4] == start and start.rules[5] == toggle)

focus = nil
view.buttons.back.callback(view.buttons.back)
assert(dispatched == "back" and focus == back)
surface.remove(view)
view.buttons.start.callback(view.buttons.start)
assert(dispatched == "back" and focus == nil)
