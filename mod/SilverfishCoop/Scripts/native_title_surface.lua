local surface = {}
local display = require("native_title_display")
local controls = require("native_menu_controls")
local width, padding, gap, control_height = 472, 16, 8, 36
local widget_tree = "/Script/UMG.WidgetTree"
local editable = "/Script/UMG.EditableTextBox"
local text_block = "/Script/UMG.TextBlock"
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

local function prompt(view, field, y)
    local label = child(view.tree, text_block)
    label:SetText(FText(field.label))
    place(cell(view.canvas, label), padding, y + 5, 132, control_height, 1)
    local input = child(view.tree, editable)
    input:SetHintText(FText(field.hint or ""))
    input:SetIsReadOnly(false)
    input:SetIsPassword(field.secret == true)
    input:SetText(FText(field.value or ""))
    local x = padding + 142
    place(cell(view.canvas, input), x, y, width - padding - x, control_height, 1)
    view.inputs[field.key] = {widget = input, limit = field.limit}
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
    local first = spec.note and 82 or 56
    local height = first + #spec.rows * (control_height + gap) + padding
    local view = {buttons = {}, inputs = {}, active = false}
    local widget = display.mount(context, spec.title .. (spec.note and "\n" .. spec.note or ""),
        {width = width, height = height, center = true})
    view.widget = widget
    local ok, reason = pcall(function()
        local tree = widget.WidgetTree
        local canvas = exact(tree, widget_tree) and tree.RootWidget
        if not exact(canvas, canvas_panel) or not same(canvas:GetOuter(), tree) then
            error("title_surface_canvas_unavailable", 0)
        end
        view.tree, view.canvas = tree, canvas
        for index, row in ipairs(spec.rows) do
            local y = first + (index - 1) * (control_height + gap)
            if row.field then
                prompt(view, row.field, y)
            else
                local w = math.floor((width - padding * 2 - gap * (#row - 1)) / #row)
                for column, action in ipairs(row) do
                    local button = controls.create(context, canvas, action.label, function()
                        if view.active then on_action(action.key) end
                    end)
                    view.buttons[action.key] = button
                    place(button.slot, padding + (column - 1) * (w + gap), y, w, control_height, 1)
                end
            end
        end
        widget:SetVisibility(4)
        view.active = true
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
    local value = input.widget:GetText():ToString()
    if #value > input.limit or not utf8.len(value) or value:find("[%z\1-\31\127]") then return nil end
    return value
end

function surface.write(view, key, value)
    local input = view and view.active and view.inputs[key]
    if not input or not valid(input.widget) then return false end
    input.widget:SetText(FText(value))
    return surface.read(view, key) == value
end

function surface.press_fixture(view, key)
    local button = view and view.active and view.buttons[key]
    return button and controls.press_fixture(button)
end

return surface
