local loading = {}
local fade_class = "/Game/Classes/GUI/FadeToBlack_Widget.FadeToBlack_Widget_C"
local transition_sound = "/Game/Assets/Sounds/UI_Sounds/sw_UI_GameStart.sw_UI_GameStart"
local widgets = "/Script/UMG.Default__WidgetBlueprintLibrary"
local gameplay_statics = "/Script/Engine.Default__GameplayStatics"
local level_class = "/Game/Classes/Actors/LevelControler_BP.LevelControler_BP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local current, last_cue
local load_hook_registered = false
local seats = {hosting = "host", connected = "guest", disconnected = "guest",
    joining = "guest", leaving = "guest", ready = "guest"}

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    return object
end

local function finish_guest_loading(state, phase)
    if phase ~= "connected" or not state.listening or not state.server or not valid(state.world) then return end
    local controller = state.controller
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not valid(controller) or not valid(pawn)
        or not same(controller:GetClass(), StaticFindObject(controller_class))
        or not same(pawn:GetClass(), StaticFindObject(pawn_class))
        or not controller:IsLocalController() or not pawn:IsLocallyControlled() or pawn:HasAuthority()
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or not same(controller:GetWorld(), state.world) or not same(pawn:GetWorld(), state.world) then return end
    local driver, instance = state.world.NetDriver, state.world.OwningGameInstance
    if not valid(driver) or not valid(driver.ServerConnection) or not same(driver.World, state.world)
        or not same(driver.ServerConnection.Driver, driver) or not valid(instance) then return end
    if controller.Loading == false then return true end
    if controller.Loading ~= true then return end
    local statics, class = StaticFindObject(gameplay_statics), StaticFindObject(level_class)
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
            return controller.Loading == false
        end
    end
end

local function remove(cover)
    if not cover then return end
    local widget = cover.widget
    cover.widget, cover.shown = nil, false
    if valid(widget) then pcall(function() widget:RemoveFromParent() end) end
end

function loading.clear(destination)
    if not current or destination and destination ~= current.destination then return false end
    local previous = current
    current = nil
    remove(previous)
    return true
end

local function active_cover()
    if current and os.time() >= current.expires then
        loading.clear()
    end
    return current
end

local function mount(cover, world, controller)
    if not valid(world) then return end
    local instance = world.OwningGameInstance
    if not valid(instance) or instance:GetAddress() ~= cover.instance then return end
    local address = world:GetAddress()
    if cover.world == address and valid(cover.widget) and cover.widget:IsInViewport() then return end
    remove(cover)
    local api, class = StaticFindObject(widgets), asset(fade_class)
    if not valid(api) or not valid(class) then return end
    if not valid(controller) or not controller:IsLocalController() or not same(controller:GetWorld(), world) then
        local statics = StaticFindObject(gameplay_statics)
        controller = valid(statics) and statics:GetPlayerController(world, 0) or nil
        if not valid(controller) or not controller:IsLocalController() or not same(controller:GetWorld(), world) then
            controller = nil
        end
    end
    -- The title handoff can load a world before its local controller exists.
    -- A world-context widget can cover the viewport during that interval.
    local widget = api:Create(world, class, controller)
    if not valid(widget) or not valid(widget.Image_0) then return end
    cover.widget, cover.world = widget, address
    widget:SetVisibility(3)
    widget:AddToViewport(15000)
    if not widget:IsInViewport() then
        remove(cover)
        return
    end
    if cover.animated or cover.source ~= address then
        -- Only the source gets the stock Construct fade. A replacement must
        -- already be opaque when its viewport is first rendered.
        widget:StopAllAnimations()
        widget:SetRenderOpacity(1)
        widget.Image_0:SetRenderOpacity(1)
    end
    cover.animated, cover.shown = true, true
end

local function register_load_hook()
    if load_hook_registered or type(RegisterLoadMapPostHook) ~= "function" then return end
    load_hook_registered = pcall(RegisterLoadMapPostHook, function(_, loaded)
        local cover = active_cover()
        if cover then pcall(function() mount(cover, loaded:get()) end) end
    end)
end

local function play_cue(cover, world)
    if cover.cue_attempted or cover.source ~= world:GetAddress() then return end
    local statics, sound = StaticFindObject(gameplay_statics), asset(transition_sound)
    if not valid(statics) or not valid(sound) then return end
    cover.cue_attempted = true
    -- Muted replicas may not receive an AudioComponent although the engine
    -- successfully dispatched the same stock cue.
    local dispatched = pcall(function()
        statics:SpawnSound2D(world, sound, 1, 1, 0, nil, true, true)
    end)
    last_cue.dispatched = dispatched
end

function loading.refresh(get_world)
    local cover = active_cover()
    if not cover then return end
    mount(cover, get_world())
end

function loading.sync(state, phase)
    local ready = finish_guest_loading(state, phase)
    local cover = active_cover()
    if not cover then return end
    if cover.seat == "guest" and ready and state.map == cover.destination then
        loading.clear(cover.destination)
        return
    end
    if not valid(state.world) then return end
    play_cue(cover, state.world)
    mount(cover, state.world, state.controller)
end

function loading.begin(state, phase, destination)
    local seat = seats[phase]
    if not seat or type(destination) ~= "string" or not destination:match("^/Game/Classes/Maps/[%w_/]+$")
        or not valid(state.world) then return false end
    local previous = active_cover()
    if previous and previous.destination == destination and previous.seat == seat then
        pcall(loading.sync, state, phase)
        return true
    end
    if not valid(state.world.OwningGameInstance) then return false end
    loading.clear()
    current = {destination = destination, seat = seat, expires = os.time() + 45,
        source = state.world:GetAddress(), instance = state.world.OwningGameInstance:GetAddress()}
    last_cue = {destination = destination, seat = seat, dispatched = false}
    register_load_hook()
    pcall(loading.sync, state, phase)
    return true
end

function loading.rows(row)
    local rows = {}
    if current and current.shown then
        rows[#rows + 1] = row("native-zone-loading-cover", current.destination)
    end
    if last_cue then
        rows[#rows + 1] = row("native-zone-loading-cue", last_cue.destination, last_cue.seat, last_cue.dispatched and 1 or 0)
    end
    return rows
end

return loading
