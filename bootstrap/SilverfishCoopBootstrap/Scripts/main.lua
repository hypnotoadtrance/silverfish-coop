local gameplay_bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if gameplay_bridge and gameplay_bridge ~= "" then return end

local title = require("native_title_entry")
local display = require("native_title_display")
local map = "/Game/Classes/Maps/MainMenu_P"
local world_name = "World " .. map .. ".MainMenu_P"
local messages = {
    ready = "F7   Open Co-op",
    unavailable = "Open Co-op\nCoordinator unavailable",
    queued = "Opening Co-op…",
    started = "Opening the co-op title…",
    handed_off = "Co-op title ready",
    failed = "Open Co-op\nThe coordinator could not start",
    refused = "Open Co-op\nThe coordinator could not accept the request",
}
local shown, attempt, input_key, report

local function valid(object)
    return object and object:IsValid()
end

local function same_title(first, second)
    if not first or not second then return false end
    for _, key in ipairs({"world", "controller", "menu", "viewport"}) do
        if not valid(first[key]) or not valid(second[key]) or first[key]:GetAddress() ~= second[key]:GetAddress() then
            return false
        end
    end
    return true
end

local function stock_title()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) or world:GetFullName() ~= world_name or valid(world.NetDriver) then return end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local controller = valid(statics) and statics:GetPlayerController(world, 0)
    if not valid(controller) or valid(controller.Pawn) then return end
    return title.probe({map = map, world = world, controller = controller, clients = 0, server = false}, "ready")
end

local function offer()
    local read, submit = SilverfishReadCoopBootstrapOffer, SilverfishRequestCoopBootstrapOpen
    if type(read) ~= "function" or type(submit) ~= "function" then return end
    local ok, version, generation = pcall(read)
    if ok and math.type(version) == "integer" and version == 1 and type(generation) == "string" and #generation == 32
        and generation:match("^[0-9a-f]+$") and generation:find("[^0]") then
        return generation, submit
    end
end

local function request_id()
    local library = StaticFindObject("/Script/Engine.Default__KismetGuidLibrary")
    local value = valid(library) and library:NewGuid()
    if not value then return end
    local words = {}
    for _, key in ipairs({"A", "B", "C", "D"}) do
        local number = math.tointeger(value[key])
        if not number or number < -0x80000000 or number > 0xffffffff then return end
        words[#words + 1] = number & 0xffffffff
    end
    local id = string.format("%08x%08x%08x%08x", table.unpack(words))
    if id:find("[^0]") then return id end
end

local function observe_attempt(context)
    local read = SilverfishReadCoopBootstrapLaunchStatus
    if type(read) ~= "function" or (attempt.state ~= "queued" and attempt.state ~= "started") then return end
    local ok, version, state, generation, id = pcall(read)
    if not ok or math.type(version) ~= "integer" or version ~= 1 or generation ~= attempt.generation or id ~= attempt.id then return end
    if state == "started" or state == "failed" then attempt.state = state end
    if state ~= "ready" or not same_title(context, stock_title()) then return end
    local system = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not valid(system) then attempt.state = "failed"; return end
    attempt.state = "handed_off"
    if not pcall(function() system:QuitGame(context.world, context.controller, 0, false) end) then attempt.state = "failed" end
end

local function publish(state)
    if report == state then return end
    report = state
    print("[SilverfishCoopBootstrap] state=" .. state .. "\n")
end

local function clear()
    if not shown then return end
    local old = shown
    shown = nil
    display.remove(old.widget)
end

local function show(context, generation, state)
    if shown and (not same_title(shown.context, context) or not valid(shown.widget)
        or not shown.widget:IsInViewport()) then clear() end
    if not shown then
        shown = {context = context, widget = display.mount(context, messages[state]), state = state}
    elseif shown.state ~= state then
        display.text(shown.widget, messages[state])
        shown.state = state
    end
    if shown.generation ~= generation then shown.held = nil end
    shown.generation = generation
    publish(state)
end

local function tick()
    local context = stock_title()
    if not context then
        clear()
        publish("waiting_title")
        return
    end

    local generation, submit = offer()
    local state = generation and "ready" or "unavailable"
    if attempt and (not generation or attempt.generation == generation) and same_title(attempt.context, context) then
        observe_attempt(context)
        state = attempt.state
    end
    show(context, generation, state)
    if state ~= "ready" then return end

    if not input_key then
        local name = FName("F7", EFindName.FNAME_Find)
        if name:GetComparisonIndex() == 0 then error("open_coop_input_unqualified", 0) end
        input_key = {KeyName = name}
    end
    local proof = SilverfishReadCoopBootstrapTestKey
    local held
    if type(proof) == "function" then
        held = proof()
    else
        held = context.controller:IsInputKeyDown(input_key)
    end
    if type(held) ~= "boolean" then error("open_coop_input_unqualified", 0) end
    local selected = held and shown.held == false
    shown.held = held
    if not selected then return end

    local refreshed, next_submit = offer()
    if refreshed ~= generation or next_submit ~= submit or not same_title(context, stock_title()) then return end
    local id = request_id()
    if not id then error("open_coop_request_id_unavailable", 0) end
    attempt = {generation = generation, context = context, state = "refused", id = id}
    local ok, queued = pcall(submit, generation, id)
    if ok and queued == true then attempt.state = "queued" end
    show(context, generation, attempt.state)
end

LoopInGameThreadWithDelay(33, function()
    if not pcall(tick) then
        pcall(clear)
        publish("error")
    end
end)
