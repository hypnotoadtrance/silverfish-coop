local loading = {}
local fade_class = "/Game/Classes/GUI/FadeToBlack_Widget.FadeToBlack_Widget_C"
local widgets = "/Script/UMG.Default__WidgetBlueprintLibrary"
local level_class = "/Game/Classes/Actors/LevelControler_BP.LevelControler_BP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local current

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finish_guest_loading(state, phase)
    if phase ~= "connected" or not state.listening or not state.server or not valid(state.world) then return end
    local controller = state.controller
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not valid(controller) or controller.Loading ~= true or not valid(pawn)
        or not same(controller:GetClass(), StaticFindObject(controller_class))
        or not same(pawn:GetClass(), StaticFindObject(pawn_class))
        or not controller:IsLocalController() or not pawn:IsLocallyControlled() or pawn:HasAuthority()
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or not same(controller:GetWorld(), state.world) or not same(pawn:GetWorld(), state.world) then return end
    local driver, instance = state.world.NetDriver, state.world.OwningGameInstance
    if not valid(driver) or not valid(driver.ServerConnection) or not same(driver.World, state.world)
        or not same(driver.ServerConnection.Driver, driver) or not valid(instance) then return end
    local statics, class = StaticFindObject("/Script/Engine.Default__GameplayStatics"), StaticFindObject(level_class)
    if not valid(statics) or not valid(class) then return end
    local found = {}
    statics:GetAllActorsOfClass(state.world, class, found)
    for _, value in ipairs(found.OutActors or found) do
        local level = value
        if level and type(level.type) == "function" and level:type() == "RemoteUnrealParam" then level = level:get() end
        if valid(level) and same(level["As Game Instance FP"], instance)
            and same(level:GetOuter(), state.world.PersistentLevel) and not level:IsActorBeingDestroyed() then
            -- The stock level controller clears this flag once. A remote
            -- player controller may arrive after that callback.
            controller.Loading = false
            return
        end
    end
end

local function remove(cover)
    if not cover then return end
    local widget = cover.widget
    cover.widget, cover.shown = nil, false
    if valid(widget) then pcall(function() widget:RemoveFromParent() end) end
end

function loading.clear()
    local previous = current
    current = nil
    remove(previous)
end

function loading.sync(state, phase)
    finish_guest_loading(state, phase)
    if not current then return end
    if os.time() >= current.expires or phase == "connected" and state.map == current.destination then
        loading.clear()
        return
    end
    if not valid(state.world) or not valid(state.controller) then return end
    local address = state.world:GetAddress()
    if current.world == address and current.shown then return end
    remove(current)
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
    current.shown = true
end

function loading.begin(state, destination)
    loading.clear()
    current = {destination = destination, expires = os.time() + 45}
    pcall(loading.sync, state)
end

function loading.rows(row)
    if current and current.shown then
        return {row("native-zone-loading-cover", current.destination)}
    end
    return {}
end

return loading
