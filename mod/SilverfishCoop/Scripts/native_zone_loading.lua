local loading = {}
local fade_class = "/Game/Classes/GUI/FadeToBlack_Widget.FadeToBlack_Widget_C"
local widgets = "/Script/UMG.Default__WidgetBlueprintLibrary"
local current

local function valid(object)
    return object and object:IsValid()
end

local function remove()
    if current and valid(current.widget) then current.widget:RemoveFromParent() end
end

function loading.clear()
    remove()
    current = nil
end

function loading.sync(state, phase)
    if not current then return end
    if os.time() >= current.expires or phase == "connected" and state.map == current.destination then
        loading.clear()
        return
    end
    if not valid(state.world) or not valid(state.controller) then return end
    local address = state.world:GetAddress()
    if current.world == address and valid(current.widget) and current.widget:IsInViewport() then return end
    remove()
    local api, class = StaticFindObject(widgets), StaticFindObject(fade_class)
    if not valid(class) then
        LoadAsset(fade_class)
        class = StaticFindObject(fade_class)
    end
    if not valid(api) or not valid(class) then return end
    local widget = api:Create(state.controller, class, state.controller)
    if not valid(widget) or not valid(widget.Image_0) then return end
    current.widget, current.world = widget, address
    widget:AddToViewport(15000)
    widget:StopAllAnimations()
    widget:SetVisibility(3)
    widget:SetRenderOpacity(1)
    widget.Image_0:SetRenderOpacity(1)
end

function loading.begin(state, destination)
    loading.clear()
    current = {destination = destination, expires = os.time() + 45}
    pcall(loading.sync, state)
end

function loading.rows(row)
    if current and valid(current.widget) and current.widget:IsInViewport() then
        return {row("native-zone-loading-cover", current.destination)}
    end
    return {}
end

return loading
