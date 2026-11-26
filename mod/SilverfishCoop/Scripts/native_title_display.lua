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

function display.mount(context, text, layout_options)
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
    local ok, metrics = pcall(function()
        local options = layout_options or {}
        widget:SetVisibility(3) -- Keep the overlay outside stock pointer hit testing.
        widget.TextBlock_Notes:SetColorAndOpacity({SpecifiedColor = {R = 0.60, G = 0.90, B = 1.0, A = 1.0}, ColorUseRule = 0})
        if options.top_level then
            widget:AddToViewport(110)
        elseif widget:AddToPlayerScreen(110) ~= true then
            error("title_panel_mount_unverified", 0)
        end
        if not widget:IsInViewport() then error("title_panel_mount_unverified", 0) end
        local size, scale = layout:GetViewportSize(context.controller), layout:GetViewportScale(context.controller)
        local width = size and tonumber(size.X)
        if not width or width < 1 or width > 16384 or type(scale) ~= "number" or not (scale >= 0.25 and scale <= 8) then
            error("title_panel_viewport_size_unavailable", 0)
        end
        local viewport_width, viewport_height = width / scale, tonumber(size.Y) / scale
        local panel_width, panel_height = options.width or 344, options.height or 148
        local x = options.center and (viewport_width - panel_width) / 2 or viewport_width - panel_width - 20
        local y = options.center and (viewport_height - panel_height) / 2 or 62
        local rail
        if options.navigation then
            rail = math.max(options.navigation_width + 48, viewport_width * 0.26)
            x = (rail - options.navigation_width) / 2 - 20
            y = viewport_height * 0.59 - panel_height
        end
        x, y = math.max(0, x), math.max(12, y)
        widget:SetDesiredSizeInViewport({X = panel_width, Y = panel_height})
        widget:SetPositionInViewport({X = x, Y = y}, false)
        display.text(widget, text)
        return {x = x, y = y, height = viewport_height, rail = rail}
    end)
    if not ok then
        display.remove(widget)
        error(metrics, 0)
    end
    return widget, metrics
end

return display
