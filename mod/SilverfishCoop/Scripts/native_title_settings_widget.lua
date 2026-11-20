local editor = {}
local title = require("native_title_entry")
local display = require("native_title_display")

local fields = {
    campaign = {limit = 64, hint = "Campaign name"},
    bind = {limit = 45, hint = "Listening IP address"},
    port = {limit = 5, hint = "UDP port"},
    profile = {limit = 64, hint = "Local profile"},
    invite = {limit = 32768, hint = "Paste a one-line co-op invitation", secret = true},
    character = {limit = 256, hint = "Character name"},
}
local tree_class = "/Script/UMG.WidgetTree"
local canvas_class = "/Script/UMG.CanvasPanel"
local slot_class = "/Script/UMG.CanvasPanelSlot"
local input_class = "/Script/UMG.EditableTextBox"
local active

local function reject(reason)
    error("title_settings_" .. reason, 0)
end

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class)
    return valid(object) and same(object:GetClass(), StaticFindObject(class))
end

local function same_title(first, second)
    return first and second and same(first.world, second.world) and same(first.controller, second.controller)
        and same(first.menu, second.menu) and same(first.viewport, second.viewport)
end

local function validate(text, limit, allow_newlines)
    if type(text) ~= "string" or #text > limit or not utf8.len(text) then reject("invalid_text") end
    local tested = allow_newlines and text:gsub("\n", "") or text
    if tested:find("[%z\1-\31\127]") then reject("invalid_text") end
    return text
end

local function canvas_for(widget, tree)
    if not exact(tree, tree_class) or not same(tree:GetOuter(), widget) then reject("tree_unqualified") end
    local root = tree.RootWidget
    local object = widget.TextBlock_Notes
    local canvas
    for _ = 1, 8 do
        if not valid(object) or not same(object:GetOuter(), tree) then break end
        if exact(object, canvas_class) then
            if canvas then reject("canvas_ambiguous") end
            canvas = object
        end
        if same(object, root) then
            if canvas then return canvas end
            break
        end
        object = object:GetParent()
    end
    reject("canvas_unqualified")
end

local function held(session, context)
    return same_title(session.context, context) and valid(session.widget) and session.widget:IsInViewport()
        and session.widget:IsVisible() and same(session.widget.WidgetTree, session.tree)
        and same(session.widget:GetOwningPlayer(), context.controller) and exact(session.input, input_class)
        and same(session.input:GetOuter(), session.tree) and same(session.input:GetParent(), session.canvas)
        and session.input:IsVisible() and session.input:GetIsEnabled() == true
end

local function current(handle, state, phase)
    if not active or handle ~= active.handle then reject("editor_unavailable") end
    local context = title.probe(state, phase)
    if not held(active, context) then reject("editor_scope_changed") end
    return active, context
end

local function read(session)
    local value = session.input:GetText()
    return validate(value and value:ToString(), session.field.limit)
end

local function write(session, text)
    validate(text, session.field.limit)
    session.input:SetText(FText(text))
    local observed = read(session)
    if observed ~= text then reject("text_readback_unverified") end
    return observed
end

local function remove(session)
    if exact(session.input, input_class) and same(session.input:GetOuter(), session.tree) then
        pcall(function() session.input:SetText(FText("")) end)
        if same(session.input:GetParent(), session.canvas) then
            pcall(function() session.canvas:RemoveChild(session.input) end)
        end
    end
    local ok, mounted = pcall(function()
        display.remove(session.widget)
        return valid(session.widget) and session.widget:IsInViewport()
    end)
    return ok and not mounted
end

function editor.mount(state, phase, field, caption, initial)
    if active then reject("editor_already_mounted") end
    local profile = fields[field]
    if not profile then reject("unknown_field") end
    validate(caption, 256, true)
    initial = validate(initial == nil and "" or initial, profile.limit)
    local context = title.probe(state, phase)
    if not context then reject("title_unavailable") end
    local widget = display.mount(context, caption)
    local session = {handle = {}, context = context, widget = widget, field = profile}
    local ok, failure = pcall(function()
        session.tree = widget.WidgetTree
        session.canvas = canvas_for(widget, session.tree)
        local class = StaticFindObject(input_class)
        if not valid(class) then reject("input_class_unavailable") end
        session.input = StaticConstructObject(class, session.tree)
        local input = session.input
        if not exact(input, input_class) or not same(input:GetOuter(), session.tree) then reject("input_unqualified") end
        local slot = session.canvas:AddChildToCanvas(input)
        if not exact(slot, slot_class) or not same(slot:GetOuter(), session.canvas)
            or not same(input:GetParent(), session.canvas) then reject("input_slot_unqualified") end
        slot:SetPosition({X = 8, Y = 108})
        slot:SetSize({X = 328, Y = 28})
        slot:SetZOrder(1)
        local position, size = slot:GetPosition(), slot:GetSize()
        if position.X ~= 8 or position.Y ~= 108 or size.X ~= 328 or size.Y ~= 28 or slot:GetZOrder() ~= 1 then
            reject("layout_readback_unverified")
        end
        input:SetHintText(FText(profile.hint))
        input:SetIsReadOnly(false)
        input:SetIsPassword(profile.secret == true)
        input:SetVisibility(0)
        -- The overlay stays transparent to pointers except at its own editor.
        widget:SetVisibility(4)
        write(session, initial)
        if not held(session, title.probe(state, phase)) then reject("mount_readback_unverified") end
    end)
    if not ok then
        remove(session)
        local reason = type(failure) == "string" and failure:match("^title_settings_[a-z_]+$")
        error(reason or "title_settings_mount_failed", 0)
    end
    active = session
    return session.handle
end

function editor.read(handle, state, phase)
    return read(current(handle, state, phase))
end

function editor.write(handle, state, phase, value)
    return write(current(handle, state, phase), value)
end

function editor.focus(handle, state, phase)
    local session, context = current(handle, state, phase)
    session.input:SetUserFocus(context.controller)
    return session.input:HasUserFocus(context.controller) == true
end

function editor.focused(handle, state, phase)
    local session, context = current(handle, state, phase)
    return session.input:HasUserFocus(context.controller) == true
end

function editor.remove(handle)
    if not active or handle ~= active.handle then return false end
    local session = active
    active = nil
    return remove(session)
end

return editor
