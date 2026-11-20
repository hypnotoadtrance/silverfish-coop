local display = {}
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

function display.text(widget, text)
    widget.TextBlock_Notes:SetText(FText(text))
    if widget.TextBlock_Notes:GetText():ToString() ~= text then error("title_panel_text_unavailable", 0) end
end

function display.remove(widget)
    if valid(widget) then widget:RemoveFromParent() end
end

function display.mount(context, text)
    local class = StaticFindObject(widget_path)
    if not valid(class) then
        LoadAsset(widget_path)
        class = StaticFindObject(widget_path)
    end
    local widgets = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
    local layout = StaticFindObject("/Script/UMG.Default__WidgetLayoutLibrary")
    if not valid(class) or not valid(widgets) or not valid(layout) then error("title_panel_widget_unavailable", 0) end
    local widget = widgets:Create(context.controller, class, context.controller)
    if not valid(widget) or not same(widget:GetClass(), class) or not same(widget:GetOwningPlayer(), context.controller)
        or not valid(widget.TextBlock_Notes)
        or not same(widget.TextBlock_Notes:GetClass(), StaticFindObject("/Script/UMG.TextBlock")) then
        error("title_panel_widget_unqualified", 0)
    end
    local ok, failure = pcall(function()
        widget:SetVisibility(3) -- Keep the overlay outside stock pointer hit testing.
        widget.TextBlock_Notes:SetColorAndOpacity({SpecifiedColor = {R = 0.60, G = 0.90, B = 1.0, A = 1.0}, ColorUseRule = 0})
        if widget:AddToPlayerScreen(110) ~= true or not widget:IsInViewport() then error("title_panel_mount_unverified", 0) end
        local size, scale = layout:GetViewportSize(context.controller), layout:GetViewportScale(context.controller)
        local width = size and tonumber(size.X)
        if not width or width < 1 or width > 16384 or type(scale) ~= "number" or not (scale >= 0.25 and scale <= 8) then
            error("title_panel_viewport_size_unavailable", 0)
        end
        widget:SetDesiredSizeInViewport({X = 344, Y = 148})
        widget:SetPositionInViewport({X = math.max(20, width / scale - 364), Y = 62}, false)
        display.text(widget, text)
    end)
    if not ok then
        display.remove(widget)
        error(failure, 0)
    end
    return widget
end

return display
