local surface = {}
local display = require("native_title_display")
local controls = require("native_menu_controls")
local width, padding, gap, control_height = 540, 20, 8, 40
local widget_tree = "/Script/UMG.WidgetTree"
local editable = "/Script/UMG.EditableTextBox"
local text_block = "/Script/UMG.TextBlock"
local check_box = "/Script/UMG.CheckBox"
local canvas_panel = "/Script/UMG.CanvasPanel"
local canvas_slot = "/Script/UMG.CanvasPanelSlot"

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(value, path) return valid(value) and same(value:GetClass(), StaticFindObject(path)) end

local function cell(canvas, widget)
    local slot = canvas:AddChildToCanvas(widget)
    if not exact(slot, canvas_slot) or not same(widget:GetParent(), canvas) then
        error("title_surface_layout_unavailable", 0)
    end
    return slot
end

local function place(slot, x, y, w, h, z)
    slot:SetPosition({X = x, Y = y})
    slot:SetSize({X = w, Y = h})
    slot:SetZOrder(z)
end

local function child(tree, path)
    local class = StaticFindObject(path)
    local widget = valid(class) and StaticConstructObject(class, tree)
    if not exact(widget, path) or not same(widget:GetOuter(), tree) then
        error("title_surface_child_unavailable", 0)
    end
    return widget
end

local function rgba(r, g, b, a)
    return {R = r, G = g, B = b, A = a or 1}
end

local function slate_color(color)
    return {SpecifiedColor = color, ColorUseRule = 0}
end

local function label(view, value, size, color, wrap)
    local widget = child(view.tree, text_block)
    local font = widget.Font
    font.Size = size
    widget:SetFont(font)
    widget:SetColorAndOpacity(slate_color(color))
    widget:SetText(FText(value))
    widget:SetAutoWrapText(wrap == true)
    widget:SetVisibility(3)
    return widget
end

local function text(view, value, size, color, x, y, w, h, wrap, z, justified)
    local widget = label(view, value, size, color, wrap)
    if justified then widget:SetJustification(justified) end
    if z then widget:SetClipping(1) end
    place(cell(view.canvas, widget), x, y, w, h, z or 0)
end

local function prompt(view, field, y)
    local x = padding + 186
    text(view, field.label, 19, rgba(0.87, 0.91, 0.89), padding, y + 8, x - padding - gap, 30)
    local input = child(view.tree, editable)
    local style = input.WidgetStyle
    local foreground = rgba(0.93, 0.95, 0.92)
    style.TextStyle.Font.Size = 19
    style.BackgroundColor = slate_color(rgba(0.20, 0.24, 0.23))
    style.ForegroundColor = slate_color(foreground)
    style.FocusedForegroundColor = slate_color(foreground)
    input:SetForegroundColor(foreground)
    input:SetHintText(FText(field.hint or ""))
    input:SetIsReadOnly(false)
    input:SetIsPassword(field.secret == true)
    input:SetText(FText(field.value or ""))
    place(cell(view.canvas, input), x, y, width - padding - x, control_height, 1)
    view.inputs[field.key] = {widget = input, limit = field.limit}
    return input
end

local function checkbox(view, spec, y)
    if type(spec.key) ~= "string" or spec.key == "" or type(spec.target) ~= "string" or spec.target == ""
        or type(spec.label) ~= "string" or spec.label == "" or view.toggles[spec.key] or view.linked[spec.target] then
        error("title_surface_toggle_unavailable", 0)
    end
    local widget = child(view.tree, check_box)
    local caption = label(view, spec.label, 19, rgba(0.87, 0.91, 0.89))
    widget:SetContent(caption)
    if not same(caption:GetParent(), widget) then error("title_surface_toggle_unavailable", 0) end
    widget.IsFocusable = true
    widget:SetIsChecked(spec.checked == true)
    widget:SetVisibility(0)
    place(cell(view.canvas, widget), padding, y, width - padding * 2, control_height, 1)
    local toggle = {widget = widget, target = spec.target}
    view.toggles[spec.key] = toggle
    view.linked[spec.target] = toggle
end

local function toggle_attached(view, toggle)
    return toggle and exact(toggle.widget, check_box) and same(toggle.widget:GetParent(), view.canvas)
end

local function reconcile(view, toggle)
    local input = toggle and view.inputs[toggle.target]
    if not toggle_attached(view, toggle) or not input or not exact(input.widget, editable)
        or not same(input.widget:GetParent(), view.canvas) then return nil end
    local checked = toggle.widget:IsChecked()
    if type(checked) ~= "boolean" then return nil end
    if checked ~= toggle.checked then
        if not checked then input.widget:SetText(FText("")) end
        input.widget:SetIsEnabled(checked)
        if input.widget:GetIsEnabled() ~= checked then return nil end
        toggle.checked = checked
    end
    return checked
end

function surface.sync(view)
    if not view or not view.active then return false end
    for _, toggle in pairs(view.toggles) do
        if reconcile(view, toggle) == nil then return false end
    end
    return true
end

function surface.checked(view, key)
    if not view or not view.active then return nil end
    return reconcile(view, view.toggles[key])
end

function surface.set_checked(view, key, checked)
    local toggle = view and view.active and view.toggles[key]
    if type(checked) ~= "boolean" or not toggle_attached(view, toggle) then return false end
    toggle.widget:SetIsChecked(checked)
    return reconcile(view, toggle) == checked
end

function surface.remove(view)
    if not view then return end
    view.active = false
    for _, button in pairs(view.buttons) do controls.remove(button) end
    for _, input in pairs(view.inputs) do
        if valid(input.widget) then input.widget:SetText(FText("")) end
    end
    display.remove(view.widget)
end

function surface.mount(context, spec, on_action)
    local first = spec.note and 110 or 66
    local height = first + #spec.rows * (control_height + gap) + padding
    local view = {buttons = {}, inputs = {}, toggles = {}, linked = {}, active = false}
    local widget = display.mount(context, "",
        {width = width, height = height, center = spec.center ~= false, top_level = true})
    view.widget = widget
    local ok, reason = pcall(function()
        local tree = widget.WidgetTree
        local canvas = exact(tree, widget_tree) and tree.RootWidget
        if not exact(canvas, canvas_panel) or not same(canvas:GetOuter(), tree) then
            error("title_surface_canvas_unavailable", 0)
        end
        view.tree, view.canvas = tree, canvas
        local background = child(tree, "/Script/UMG.Border")
        background:SetBrushColor(rgba(0.015, 0.023, 0.022, 0.98))
        background:SetVisibility(3)
        place(cell(canvas, background), 0, 0, width, height, -1)
        local divider = child(tree, "/Script/UMG.Border")
        divider:SetBrushColor(rgba(0.83, 0.48, 0.13))
        divider:SetVisibility(3)
        place(cell(canvas, divider), 0, 0, width, 3, 0)
        text(view, spec.title, 27, rgba(0.94, 0.71, 0.36), padding, 15, width - padding * 2, 41)
        if spec.note then
            text(view, spec.note, 17, rgba(0.83, 0.87, 0.86), padding, 58, width - padding * 2, 48, true)
        end
        local input_order = {}
        for index, row in ipairs(spec.rows) do
            local y = first + (index - 1) * (control_height + gap)
            if row.field then
                input_order[#input_order + 1] = prompt(view, row.field, y)
            elseif row.toggle then
                checkbox(view, row.toggle, y)
            else
                local w = math.floor((width - padding * 2 - gap * (#row - 1)) / #row)
                for column, action in ipairs(row) do
                    local x = padding + (column - 1) * (w + gap)
                    local button = controls.create(context, canvas, action.label, function()
                        if view.active then on_action(action.key) end
                    end, #row > 1 and 20 or 22)
                    view.buttons[action.key] = button
                    place(button.slot, x, y, w, control_height, 1)
                    if action.detail then
                        button.widget.TextBlock_0:SetRenderOpacity(0)
                        text(view, action.label, 18, rgba(0.91, 0.93, 0.92), x + 14, y + 8, w - 251, 28, false, 2)
                        text(view, action.detail, 16, rgba(0.78, 0.82, 0.80), x + w - 234, y + 10, 220, 26, false, 2, 2)
                    end
                end
            end
        end
        widget:SetVisibility(4)
        view.active = true
        if not surface.sync(view) then error("title_surface_toggle_unavailable", 0) end
        for _, input in ipairs(input_order) do
            if input:GetIsEnabled() == true then
                input:SetUserFocus(context.controller)
                break
            end
        end
    end)
    if not ok then
        surface.remove(view)
        error(reason, 0)
    end
    return view
end

function surface.read(view, key)
    local input = view and view.active and view.inputs[key]
    if not input or not valid(input.widget) then return nil end
    local toggle = view.linked[key]
    if toggle then
        local checked = reconcile(view, toggle)
        if checked ~= true then return checked == false and "" or nil end
    end
    local value = input.widget:GetText():ToString()
    if #value > input.limit or not utf8.len(value) or value:find("[%z\1-\31\127]") then return nil end
    return value
end

function surface.write(view, key, value)
    local input = view and view.active and view.inputs[key]
    if not input or not valid(input.widget) then return false end
    local toggle = view.linked[key]
    if toggle then
        local checked = reconcile(view, toggle)
        if checked ~= true then return checked == false and value == "" end
    end
    input.widget:SetText(FText(value))
    return surface.read(view, key) == value
end

function surface.press_fixture(view, key)
    local button = view and view.active and view.buttons[key]
    return button and controls.press_fixture(button)
end

return surface
