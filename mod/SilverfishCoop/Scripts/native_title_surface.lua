local surface = {}
local display = require("native_title_display")
local controls = require("native_menu_controls")
local width, padding, gap, control_height, navigation_width = 540, 20, 8, 40, 318
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

local amber = rgba(0.48, 0.21, 0.012)
local pale_amber = rgba(0.66, 0.43, 0.20)
local black = rgba(0.006, 0.004, 0.002, 0.95)

local function slate_color(color)
    return {SpecifiedColor = color, ColorUseRule = 0}
end

local function label(view, value, size, color, wrap)
    local widget = child(view.tree, text_block)
    if view.font then widget:SetFont(view.font.Font) end
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
    text(view, field.label, 19, amber, padding, y + 8, x - padding - gap, 30)
    local input = child(view.tree, editable)
    local style = input.WidgetStyle
    local foreground = pale_amber
    if view.font then style.TextStyle.Font = view.font.Font end
    style.TextStyle.Font.Size = 19
    style.BackgroundColor = slate_color(rgba(0.19, 0.10, 0.024))
    style.ForegroundColor = slate_color(foreground)
    style.FocusedForegroundColor = slate_color(foreground)
    input:SetForegroundColor(foreground)
    input:SetHintText(FText(field.hint or ""))
    input:SetIsReadOnly(false)
    input:SetIsPassword(field.secret == true)
    input:SetText(FText(field.value or ""))
    input:SetVisibility(0)
    place(cell(view.canvas, input), x, y, width - padding - x, control_height, 1)
    view.inputs[field.key] = {widget = input, limit = field.limit}
    return input
end

local function checkbox(view, spec, y)
    if type(spec.key) ~= "string" or spec.key == "" or type(spec.label) ~= "string" or spec.label == ""
        or view.toggles[spec.key] or (spec.target ~= nil and
            (type(spec.target) ~= "string" or spec.target == "" or view.linked[spec.target])) then
        error("title_surface_toggle_unavailable", 0)
    end
    local widget = child(view.tree, check_box)
    local caption = label(view, spec.label, 19, pale_amber)
    widget:SetContent(caption)
    if not same(caption:GetParent(), widget) then error("title_surface_toggle_unavailable", 0) end
    widget.IsFocusable = true
    widget:SetIsChecked(spec.checked == true)
    widget:SetVisibility(0)
    place(cell(view.canvas, widget), padding, y, width - padding * 2, control_height, 1)
    local toggle = {widget = widget, target = spec.target}
    view.toggles[spec.key] = toggle
    if spec.target then view.linked[spec.target] = toggle end
end

local function toggle_attached(view, toggle)
    return toggle and exact(toggle.widget, check_box) and same(toggle.widget:GetParent(), view.canvas)
end

local function reconcile(view, toggle)
    if not toggle_attached(view, toggle) then return nil end
    local checked = toggle.widget:IsChecked()
    if type(checked) ~= "boolean" then return nil end
    if not toggle.target then return checked end
    local input = view.inputs[toggle.target]
    if not input or not exact(input.widget, editable)
        or not same(input.widget:GetParent(), view.canvas) then return nil end
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
    local first = spec.note and 126 or 66
    local height = first + #spec.rows * (control_height + gap) + padding
    local view = {buttons = {}, inputs = {}, toggles = {}, linked = {}, active = false}
    local widget, metrics = display.mount(context, "",
        {width = width, height = height, center = spec.center ~= false, top_level = true,
            navigation = spec.navigation == true, navigation_width = navigation_width})
    view.widget = widget
    local ok, reason = pcall(function()
        local tree = widget.WidgetTree
        local canvas = exact(tree, widget_tree) and tree.RootWidget
        if not exact(canvas, canvas_panel) or not same(canvas:GetOuter(), tree) then
            error("title_surface_canvas_unavailable", 0)
        end
        view.tree, view.canvas = tree, canvas
        local reference = controls.title_reference(context)
        view.font = reference and reference.TextBlock_0
        local background = child(tree, "/Script/UMG.Border")
        background:SetBrushColor(black)
        background:SetVisibility(3)
        local divider = child(tree, "/Script/UMG.Border")
        divider:SetBrushColor(amber)
        divider:SetVisibility(3)
        if spec.navigation then
            place(cell(canvas, background), -metrics.x, -metrics.y, metrics.rail, metrics.height, -1)
            place(cell(canvas, divider), padding, height - 7, navigation_width, 2, 0)
            local title_y = math.min(metrics.height * 0.085 - metrics.y, 10)
            local heading = spec.title == "Project Silverfish" and "PROJECT\nSILVERFISH" or spec.title:upper()
            text(view, heading, 35, amber, padding, title_y, navigation_width, 102, false, nil, 1)
        else
            place(cell(canvas, background), 0, 0, width, height, -1)
            place(cell(canvas, divider), padding, 54, width - padding * 2, 2, 0)
            text(view, spec.title:upper(), 26, amber, padding, 15, width - padding * 2, 41)
        end
        if spec.note then
            local note_width = spec.navigation and navigation_width or width - padding * 2
            text(view, spec.note, 17, pale_amber, padding, 63, note_width, 62, true)
        end
        local input_order, first_button = {}, nil
        for index, row in ipairs(spec.rows) do
            local y = first + (index - 1) * (control_height + gap)
            if row.field then
                local input = prompt(view, row.field, y)
                if row.field.key == spec.focus then
                    table.insert(input_order, 1, input)
                else
                    input_order[#input_order + 1] = input
                end
            elseif row.toggle then
                checkbox(view, row.toggle, y)
            else
                local button_area = spec.navigation and navigation_width or width - padding * 2
                local w = math.floor((button_area - gap * (#row - 1)) / #row)
                for column, action in ipairs(row) do
                    local x = padding + (column - 1) * (w + gap)
                    local caption = action.key:find(":", 1, true) and action.label or action.label:upper()
                    local button = controls.create(context, canvas, caption, function()
                        if view.active then on_action(action.key) end
                    end, #row > 1 and 20 or 22)
                    view.buttons[action.key] = button
                    first_button = first_button or button.inner
                    place(button.slot, x, y, w, control_height, 1)
                    if action.detail then
                        button.widget.TextBlock_0:SetRenderOpacity(0)
                        text(view, caption, 18, amber, x + 14, y + 8, w - 251, 28, false, 2)
                        text(view, action.detail, 16, pale_amber, x + w - 234, y + 10, 220, 26, false, 2, 2)
                    end
                end
            end
        end
        widget:SetVisibility(4)
        view.active = true
        if not surface.sync(view) then error("title_surface_toggle_unavailable", 0) end
        local preferred = view.buttons[spec.focus]
        local focus = preferred and preferred.inner or first_button
        for _, input in ipairs(input_order) do
            if not preferred and input:GetIsEnabled() == true then
                focus = input
                break
            end
        end
        if focus then focus:SetUserFocus(context.controller) end
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

function surface.has_focus(view, key)
    if not view or not view.active then return false end
    local button, input, toggle = view.buttons[key], view.inputs[key], view.toggles[key]
    local widget = button and button.inner or input and input.widget or toggle and toggle.widget
    if not valid(widget) then return false end
    local controller = view.widget:GetOwningPlayer()
    return widget:HasUserFocus(controller) == true or widget:HasUserFocusedDescendants(controller) == true
end

return surface
