local controls = {}

local button_path = "/Game/Classes/GUI/SubWidgets/Button_Default_Widget.Button_Default_Widget_C"
local pressed_event = "BndEvt__Button_Default_Widget_Button_63_K2Node_ComponentBoundEvent_0_OnButtonPressedEvent__DelegateSignature"
local panels = {
    ["/Script/UMG.CanvasPanel"] = {attach = "AddChildToCanvas", slot = "/Script/UMG.CanvasPanelSlot"},
    ["/Script/UMG.VerticalBox"] = {attach = "AddChildToVerticalBox", slot = "/Script/UMG.VerticalBoxSlot"},
}
local active = {}
local hooked

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, path) return valid(object) and same(object:GetClass(), StaticFindObject(path)) end
local function reject(reason) error("native_menu_control_" .. reason, 0) end

function controls.title_reference(context)
    local menu = context.menu
    local button = valid(menu) and menu.Button_NewGame
    if exact(button, button_path) and exact(button.Button_63, "/Script/UMG.Button")
        and exact(button.TextBlock_0, "/Script/UMG.TextBlock") then
        return button
    end
end

local function attached(record)
    return record and active[record.address] == record and exact(record.widget, button_path)
        and same(record.widget:GetParent(), record.parent)
        and same(record.widget:GetOwningPlayer(), record.controller)
        and same(record.controller:GetWorld(), record.world)
        and same(record.parent:GetWorld(), record.world)
end

local function enabled(record)
    return attached(record) and record.widget:IsVisible() and record.widget:GetIsEnabled() == true
        and exact(record.inner, "/Script/UMG.Button") and same(record.widget.Button_63, record.inner)
        and record.inner:IsVisible() and record.inner:GetIsEnabled() == true
end

local function register_pressed()
    if hooked then return end
    RegisterCustomEvent(pressed_event, function(context)
        local widget = context:get()
        local record = valid(widget) and active[widget:GetAddress()]
        if not record or record.pending or not same(widget, record.widget) or not enabled(record) then return end
        record.pending = true
        -- The stock event focuses the viewport. Change screens after dispatch
        -- unwinds and the current button can safely be removed.
        local scheduled = pcall(ExecuteInGameThreadWithDelay, 1, function()
            record.pending = false
            if enabled(record) then record.callback(record) end
        end)
        if not scheduled then record.pending = false end
    end)
    hooked = true
end

function controls.create(context, parent, label, callback, font_size)
    local controller = context and context.controller
    local world = valid(controller) and controller:GetWorld()
    if not valid(world) or not controller:IsLocalController() or not valid(parent)
        or not same(parent:GetWorld(), world) or (context.world and not same(context.world, world)) then
        reject("owner_unqualified")
    end
    local attachment
    for path, panel in pairs(panels) do
        if exact(parent, path) then
            attachment = panel
            break
        end
    end
    if not attachment then reject("parent_unqualified") end
    if type(label) ~= "string" or label == "" or not utf8.len(label) or label:find("%c")
        or type(callback) ~= "function" then reject("arguments") end

    local class = StaticFindObject(button_path)
    if not valid(class) then
        LoadAsset(button_path)
        class = StaticFindObject(button_path)
    end
    local widgets = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
    if not valid(class) or not valid(widgets) then reject("class_unavailable") end
    local widget = widgets:Create(controller, class, controller)
    if not exact(widget, button_path) or not same(widget:GetOwningPlayer(), controller)
        or not exact(widget.Button_63, "/Script/UMG.Button") then reject("widget_unqualified") end

    local record = {
        widget = widget, parent = parent, controller = controller, world = world, inner = widget.Button_63,
        address = widget:GetAddress(), callback = callback,
    }
    local mounted, failure = pcall(function()
        register_pressed()
        if font_size then widget.Font_Size = font_size end
        widget:UpdateText(FText(label))
        if widget:GetText():ToString() ~= label then reject("text_unverified") end
        record.inner.IsFocusable = true
        record.slot = parent[attachment.attach](parent, widget)
        if not exact(record.slot, attachment.slot) or not same(record.slot:GetOuter(), parent)
            or not same(widget:GetParent(), parent) then reject("slot_unverified") end
        local reference = controls.title_reference(context)
        if reference then
            -- Copy the stock brushes and Slate sounds, including hover and press.
            record.inner:SetStyle(reference.Button_63.WidgetStyle)
            record.inner:SetBackgroundColor(reference.Button_63.BackgroundColor)
            record.inner:SetColorAndOpacity(reference.Button_63.ColorAndOpacity)
            widget.TextBlock_0:SetFont(reference.TextBlock_0.Font)
            widget.TextBlock_0:SetColorAndOpacity(reference.TextBlock_0.ColorAndOpacity)
        end
        if font_size then
            local font = widget.TextBlock_0.Font
            font.Size = font_size
            widget.TextBlock_0:SetFont(font)
            widget:SetToolTipText(FText(label))
        end
        widget:SetVisibility(0)
        active[record.address] = record
    end)
    if not mounted then
        controls.remove(record)
        error(failure, 0)
    end
    return record
end

function controls.remove(record)
    if not record then return true end
    if active[record.address] == record then active[record.address] = nil end
    record.callback = nil
    if not valid(record.widget) or not valid(record.widget:GetParent()) then return true end
    if not same(record.widget:GetParent(), record.parent) then return false end

    local removed = pcall(function() record.parent:RemoveChild(record.widget) end)
    return removed and not same(record.widget:GetParent(), record.parent)
end

function controls.press_fixture(record)
    if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not enabled(record) or record.pending then return false end
    local fired = pcall(function() record.inner.OnPressed:Broadcast() end)
    return fired and record.pending == true
end

return controls
