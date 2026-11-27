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

function display.reflow(context, widget, options, previous)
    local layout = StaticFindObject("/Script/UMG.Default__WidgetLayoutLibrary")
    if not valid(layout) then error("title_panel_widget_unavailable", 0) end
    local size, dpi = layout:GetViewportSize(context.controller), layout:GetViewportScale(context.controller)
    local width, height = size and tonumber(size.X), size and tonumber(size.Y)
    if not width or not height or not (width >= 1 and width <= 16384 and height >= 1 and height <= 16384)
        or type(dpi) ~= "number" or not (dpi >= 0.25 and dpi <= 8) then
        if previous then return previous end
        error("title_panel_viewport_size_unavailable", 0)
    end
    width, height = width / dpi, height / dpi
    if previous and previous.width == width and previous.height == height then return previous end

    local panel_width, panel_height = options.width or 344, options.height or 148
    local margin = math.min(12, width / 8, height / 8)
    local content_width = options.navigation and options.navigation_width + 48 or panel_width
    local fit = options.responsive and math.min(1, (width - 2 * margin) / content_width,
        (height - 2 * margin) / panel_height) or 1
    local x = options.center and (width - panel_width * fit) / 2 or width - panel_width - 20
    local y = options.center and (height - panel_height * fit) / 2 or 62
    local rail
    if options.navigation then
        rail = math.min(width, math.max(content_width * fit, width * 0.26))
        x = (rail - options.navigation_width * fit) / 2 - 20 * fit
        y = height * 0.59 - panel_height * fit
    end
    x, y = math.max(0, x), math.max(margin, y)
    if not previous then widget:SetDesiredSizeInViewport({X = panel_width, Y = panel_height}) end
    if options.responsive then
        if not previous then widget:SetRenderTransformPivot({X = 0, Y = 0}) end
        if fit ~= (previous and previous.fit or 1) then widget:SetRenderScale({X = fit, Y = fit}) end
    end
    widget:SetPositionInViewport({X = x, Y = y}, false)
    return {x = x, y = y, width = width, height = height, rail = rail, fit = fit}
end

function display.mount(context, text, layout_options)
    local class = StaticFindObject(widget_path)
    if not valid(class) then
        LoadAsset(widget_path)
        class = StaticFindObject(widget_path)
    end
    local widgets = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
    if not valid(class) or not valid(widgets) then error("title_panel_widget_unavailable", 0) end
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
        local frame = display.reflow(context, widget, options)
        display.text(widget, text)
        return frame
    end)
    if not ok then
        display.remove(widget)
        error(metrics, 0)
    end
    return widget, metrics
end

return display
