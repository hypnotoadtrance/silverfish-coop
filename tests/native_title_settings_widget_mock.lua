local tree_class = "/Script/UMG.WidgetTree"
local canvas_class = "/Script/UMG.CanvasPanel"
local slot_class = "/Script/UMG.CanvasPanelSlot"
local input_class = "/Script/UMG.EditableTextBox"
local classes, address = {}, 0

local function object(class_name, outer, parent)
    address = address + 1
    local id = address
    return {
        outer = outer, parent = parent,
        IsValid = function() return true end,
        GetAddress = function() return id end,
        GetClass = function() return classes[class_name] end,
        GetOuter = function(self) return self.outer end,
        GetParent = function(self) return self.parent end,
        IsVisible = function(self) return self.visibility ~= 2 end,
        SetVisibility = function(self, mode) self.visibility = mode end,
    }
end

for _, class in ipairs({tree_class, canvas_class, slot_class, input_class}) do
    classes[class] = object()
end

local state = {context = {world = object(), controller = object(), menu = object(), viewport = object()}}
local stock = state.context
local widget, input, canvas, slot
local wrong_parent, corrupt_text, wrong_scope = false, false, false
local created = 0
local display = {}

function display.mount(context, caption)
    assert(context == stock and type(caption) == "string")
    created = created + 1
    widget = object()
    widget.mounted = true
    widget.caption = caption
    widget.visibility = 3
    function widget:IsInViewport() return self.mounted end
    function widget:GetOwningPlayer() return context.controller end
    local tree = object(tree_class, wrong_scope and context.menu or widget)
    widget.WidgetTree = tree
    canvas = object(canvas_class, tree)
    tree.RootWidget = canvas
    widget.TextBlock_Notes = object(nil, tree, object(nil, tree, canvas))
    function canvas:AddChildToCanvas(child)
        assert(child.outer == tree and child.parent == nil)
        child.parent = wrong_parent and context.menu or self
        slot = object(slot_class, self)
        function slot:SetPosition(position) self.position = position end
        function slot:SetSize(size) self.size = size end
        function slot:SetZOrder(z) self.z = z end
        function slot:GetPosition() return self.position end
        function slot:GetSize() return self.size end
        function slot:GetZOrder() return self.z end
        return slot
    end
    function canvas:RemoveChild(child)
        assert(child.parent == self)
        child.parent = nil
        child.focus = false
        return true
    end
    return widget
end

function display.remove(target)
    assert(target == widget)
    widget.mounted = false
end

local env = setmetatable({
    require = function(name)
        if name == "native_title_display" then return display end
        assert(name == "native_title_entry")
        return {probe = function(frame, phase) if phase == "ready" then return frame.context end end}
    end,
    StaticFindObject = function(name) return classes[name] end,
    StaticConstructObject = function(class, tree)
        assert(class == classes[input_class] and tree == widget.WidgetTree)
        input = object(input_class, tree)
        input.text = ""
        function input:SetHintText(hint) self.hint = hint end
        function input:SetIsReadOnly(read_only) self.read_only = read_only end
        function input:SetIsPassword(secret) self.secret = secret end
        function input:GetIsEnabled() return true end
        function input:SetText(text) self.text = corrupt_text and text:lower() or text end
        function input:GetText() return {ToString = function() return self.text end} end
        function input:SetUserFocus(controller)
            assert(controller == stock.controller)
            self.focus = controller
        end
        function input:HasUserFocus(controller) return self.focus == controller end
        return input
    end,
    FText = function(text) return text end,
}, {__index = _G})

local editor = assert(loadfile("mod/SilverfishCoop/Scripts/native_title_settings_widget.lua", "t", env))()
local function refused(reason, callable, ...)
    local ok, failure = pcall(callable, ...)
    assert(not ok and failure == "title_settings_" .. reason, tostring(failure))
end

refused("unknown_field", editor.mount, state, "ready", "generic", "Unknown")
refused("title_unavailable", editor.mount, state, "opening", "port", "Listen port")
assert(created == 0)

local handle = editor.mount(state, "ready", "port", "Host co-op\nListen port", "7777")
assert(widget.visibility == 4 and widget.mounted and input.visibility == 0 and input.read_only == false)
assert(input.parent == canvas and input.hint == "UDP port" and input.secret == false)
assert(slot.position.Y == 108 and slot.size.X == 328 and slot.z == 1)
assert(not editor.focused(handle, state, "ready"))
assert(editor.focus(handle, state, "ready") and editor.focused(handle, state, "ready"))
assert(editor.read(handle, state, "ready") == "7777")
assert(editor.write(handle, state, "ready", "27017") == "27017")
refused("invalid_text", editor.write, handle, state, "ready", "110000")
refused("invalid_text", editor.write, handle, state, "ready", "x\ty")
assert(input.text == "27017")
refused("editor_already_mounted", editor.mount, state, "ready", "bind", "Bind")
refused("editor_unavailable", editor.read, {}, state, "ready")

state.context = {world = stock.world, controller = stock.controller, menu = object(), viewport = stock.viewport}
refused("editor_scope_changed", editor.read, handle, state, "ready")
refused("editor_scope_changed", editor.focus, handle, state, "ready")
assert(editor.remove(handle) and not widget.mounted and input.parent == nil and input.text == "")
assert(not editor.remove(handle))
state.context = stock

wrong_parent = true
refused("input_slot_unqualified", editor.mount, state, "ready", "invite", "Join")
assert(not widget.mounted and input.text == "")
wrong_parent = false
wrong_scope = true
refused("tree_unqualified", editor.mount, state, "ready", "invite", "Join")
assert(not widget.mounted)
wrong_scope = false
corrupt_text = true
refused("text_readback_unverified", editor.mount, state, "ready", "campaign", "Campaign", "Mixed")
assert(not widget.mounted and input.parent == nil and input.text == "")
corrupt_text = false

local invite = editor.mount(state, "ready", "invite", "Join co-op")
assert(input.secret and input.hint:find("one%-line"))
assert(editor.write(invite, state, "ready", '{"credential":"fixture"}') == '{"credential":"fixture"}')
refused("invalid_text", editor.write, invite, state, "ready", "{\n}")
assert(editor.remove(invite) and input.text == "")
print("Qualified title settings widget mock passed")
