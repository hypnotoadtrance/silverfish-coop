local invite = {}
local display = require("native_title_display")
local editor = require("native_title_settings_widget")
local players = require("players")

local journal_class = "/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local stats_path = "/Script/Engine.Default__GameplayStatics"
local viewport_class = "/Script/Engine.GameViewportClient"
local fields = {"participant", "starter", "address"}
local labels = {participant = "Participant", starter = "Starter character", address = "Host address"}
local errors = {
    invalid_request = "Check the invitation details.",
    invalid_address = "Enter a reachable host address.",
    invite_unavailable = "The invitation could not be issued.",
    export_unverified = "The invitation export could not be verified.",
    export_changed = "The invitation export changed.",
    operation_conflict = "This request was already used.",
    operation_limit = "Close the Journal and try again.",
}
local keys = {{"confirm", "F7"}, {"next", "F8"}, {"back", "F9"}}
local bridge, offer_path, request_path, response_path, loop, current, session, fixture_menu
local queued = {}

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class_path)
    return valid(object) and same(object:GetClass(), StaticFindObject(class_path))
end

local function host(state, phase)
    local world, controller = state.world, state.controller
    local details = state.local_pawn
    local pawn = details and details.pawn
    if phase ~= "hosting" or not state.listening or state.server ~= false or not valid(world)
        or not valid(world.NetDriver) or valid(world.NetDriver.ServerConnection)
        or not exact(controller, controller_class) or controller.Loading ~= false
        or not controller:IsLocalController() or not controller:HasAuthority() or controller:IsActorBeingDestroyed()
        or not exact(pawn, pawn_class) or pawn.Dead ~= false or not pawn:HasAuthority() or not pawn:IsLocallyControlled()
        or pawn:IsActorBeingDestroyed() or players.is_downed(pawn) ~= false or not same(pawn.Controller, controller)
        or not same(controller.Pawn, pawn) or not same(controller:GetWorld(), world) or not same(pawn:GetWorld(), world) then return end
    local statics, viewport = StaticFindObject(stats_path), FindFirstOf("GameViewportClient")
    if not valid(statics) or statics:IsGamePaused(controller) ~= false
        or not exact(viewport, viewport_class) or not same(viewport.World, world) then return end
    local instance = statics:GetGameInstance(controller)
    if not valid(instance) or not same(viewport:GetOuter(), instance:GetOuter()) then return end
    return {world = world, controller = controller, pawn = pawn, viewport = viewport, instance = instance, map = state.map}
end

local function journal(state, phase)
    local context = host(state, phase)
    if not context then return end
    local stack = context.controller.MenuStack
    local count = stack and stack:GetArrayNum()
    if not count or count < 1 or count > 32 then return end
    local menu = stack[count]
    if not exact(menu, journal_class) or not same(menu:GetOwningPlayer(), context.controller)
        or not same(menu:GetOuter(), context.instance) or not menu:IsInViewport() or not menu:IsVisible() then return end
    for index = 1, count - 1 do if exact(stack[index], journal_class) then return end end
    context.menu = menu
    return context
end

local function same_journal(first, second)
    return first and second and first.map == second.map and same(first.world, second.world)
        and same(first.controller, second.controller) and same(first.pawn, second.pawn)
        and same(first.viewport, second.viewport) and same(first.menu, second.menu)
end

local function token(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9a-f]+$") and value:find("[^0]")
end

local function read_file(path, limit)
    local file = io.open(path, "rb")
    if not file then return end
    local data = file:read(limit + 1)
    file:close()
    if data and #data <= limit then return data end
end

local function read_offer()
    local data = read_file(offer_path, 96)
    if not data then return end
    local generation, epoch, timestamp = data:match("^1\t([0-9a-f]+)\t([0-9a-f]+)\t([0-9]+)\n$")
    local expires = timestamp and #timestamp <= 10 and tonumber(timestamp)
    local now = os.time()
    if token(generation) and token(epoch) and expires and expires >= now and expires <= now + 5 then
        return {generation = generation, epoch = epoch}
    end
end

local function same_offer(first, second)
    return first and second and first.generation == second.generation and first.epoch == second.epoch
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
    if token(id) then return id end
end

local function clear_view(active)
    if active.handle then
        editor.remove(active.handle)
        active.handle = nil
    end
    if active.widget then
        display.remove(active.widget)
        active.widget = nil
    end
end

local function reset()
    if not session then return end
    local previous = session
    session = nil
    clear_view(previous)
    previous.export = nil
end

local function render(active, state, phase, screen, hint, initial)
    clear_view(active)
    active.screen = screen
    if labels[screen] then
        local note = hint or (screen == "address" and "Blank uses the configured address." or "Enter a name.")
        local caption = labels[screen] .. "\n" .. note .. "\nF8 Next   F9 Back"
        active.handle = editor.mount(state, phase, screen, caption, initial or active.values[screen], journal)
        editor.focus(active.handle, state, phase)
        return
    end
    local captions = {
        journal = "F7   Invite a co-op player",
        review = "Create this invitation?\nF7 Create   F9 Back",
        pending = "Creating invitation…\nF9 Back",
        exported = "Invitation ready\nF7 Copy   F9 Back",
        refused = (hint or "Could not create invitation.") .. "\nF7 Try again   F9 Back",
    }
    active.widget = display.mount(active.context, hint and screen == "exported" and hint .. "\nF7 Copy   F9 Back" or captions[screen])
end

local function scope(state, phase)
    if not bridge then return end
    local context = journal(state, phase)
    local offer = context and read_offer()
    if not offer then reset(); return end
    if not session or not same_journal(session.context, context) or not same_offer(session.offer, offer) then
        reset()
        session = {context = context, offer = offer, values = {}}
        render(session, state, phase, "journal")
    end
    return session
end

local function valid_input(field, value)
    if field == "address" then return value == "" or value:match("^[A-Za-z0-9.:_-]+$") ~= nil end
    local count = utf8.len(value)
    return count and count > 0 and count <= 64
end

local function advance(active, state, phase)
    local field = active.screen
    local ok, value = pcall(editor.read, active.handle, state, phase)
    if not ok and value ~= "title_settings_invalid_text" then return false, "native_host_invite_editor_unavailable" end
    if ok and field ~= "address" then value = value:match("^ *(.-) *$") end
    if not ok or not valid_input(field, value) then
        local tip = field == "address" and "Enter a reachable address." or "Enter a name of up to 64 characters."
        render(active, state, phase, field, tip, ok and value or nil)
        return false, "native_host_invite_invalid_" .. field
    end
    active.values[field] = value
    for index, name in ipairs(fields) do
        if name == field then
            render(active, state, phase, fields[index + 1] or "review")
            return true
        end
    end
    return false, "native_host_invite_editor_unavailable"
end

local function exists(path)
    local file = io.open(path, "rb")
    if not file then return false end
    file:close()
    return true
end

local function publish(active, state, phase)
    local values = active.values
    local body = table.concat({values.participant, values.starter, values.address}, "\t")
    local id = active.retry and active.retry.body == body and active.retry.id or request_id()
    if not id or exists(request_path) then return false, "native_host_invite_request_unavailable" end
    local temporary = request_path .. "." .. id .. ".tmp"
    local file = io.open(temporary, "wb")
    if not file then return false, "native_host_invite_request_unavailable" end
    local written = file:write("1\t", active.offer.generation, "\t", active.offer.epoch, "\t", id, "\t", body, "\n")
    local closed = file:close()
    if not written or not closed or not os.rename(temporary, request_path) then
        os.remove(temporary)
        render(active, state, phase, "refused", "Could not send the invitation request.")
        return false, "native_host_invite_request_unavailable"
    end
    active.op, active.deadline = id, os.time() + 12
    active.retry = {id = id, body = body}
    active.failure = nil
    render(active, state, phase, "pending")
    return true
end

local function read_response(active)
    if exists(request_path) then return end
    local data = read_file(response_path, 180)
    if not data then return end
    local generation, epoch, id, outcome, code =
        data:match("^1\t([0-9a-f]+)\t([0-9a-f]+)\t([0-9a-f]+)\t([a-z]+)\t([a-z_]+)\n$")
    if generation ~= active.offer.generation or epoch ~= active.offer.epoch or id ~= active.op then return end
    if outcome == "ok" and code == "exported" then return outcome end
    if outcome == "error" and errors[code] then return outcome, code end
end

local function read_export(id)
    if not token(id) then return end
    local raw = read_file(bridge .. "/host-invite-" .. id .. ".invite.json", 32768)
    local text = raw and raw:gsub("\r?\n$", "")
    if not text or not text:match("^%{.*%}$") or text:find("[%z\1-\31\127]") or not utf8.len(text) then return end
    return raw, text
end

local function progress(active, state, phase)
    if active.screen ~= "pending" then return end
    local outcome, code = read_response(active)
    if outcome == "ok" then
        active.export = read_export(active.op)
        if active.export then
            active.retry = nil
            render(active, state, phase, "exported")
            return
        end
        code = "export_unverified"
    elseif not outcome and os.time() <= active.deadline then
        return
    end
    active.failure = code or "invite_unavailable"
    render(active, state, phase, "refused", errors[active.failure])
end

local function foreground()
    return os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" and type(SilverfishNativeGameWindowFocused) == "function"
        and SilverfishNativeGameWindowFocused() == true
end

local function copy(active, state, phase)
    if not foreground() or type(SilverfishCopyHostInvitation) ~= "function" or read_response(active) ~= "ok" then
        render(active, state, phase, "exported", "Invitation ready\nCopy is unavailable.")
        return false, "native_host_invite_copy_unavailable"
    end
    local raw, text = read_export(active.op)
    if not raw or raw ~= active.export then
        active.export, active.failure = nil, "export_changed"
        render(active, state, phase, "refused", errors.export_changed)
        return false, "native_host_invite_export_changed"
    end
    if not foreground() or not same_journal(active.context, journal(state, phase)) or not same_offer(active.offer, read_offer())
        or SilverfishCopyHostInvitation(text, active.op) ~= true then
        return false, "native_host_invite_copy_unavailable"
    end
    render(active, state, phase, "exported", "Invitation copied.")
    return true
end

local function press(active, action, state, phase)
    local screen = active.screen
    if action == "back" then
        active.op, active.export, active.retry, active.failure = nil, nil, nil, nil
        active.values = {}
        render(active, state, phase, "journal")
        return true
    end
    if action == "next" and labels[screen] then return advance(active, state, phase) end
    if action ~= "confirm" then return false, "native_host_invite_action_unavailable" end
    if screen == "journal" or screen == "refused" then
        render(active, state, phase, "participant")
        return true
    end
    if screen == "review" then return publish(active, state, phase) end
    if screen == "exported" then return copy(active, state, phase) end
    return false, "native_host_invite_action_unavailable"
end

local function report(active)
    if not active or active.screen == "journal" then return "journal" end
    if active.screen == "pending" or active.screen == "exported" or active.screen == "refused" then return active.screen end
    return "editing"
end

local function fixture_enabled()
    return os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
end

local function fixture_open(state, phase)
    local context = host(state, phase)
    local stack = context and context.controller.MenuStack
    if not stack or stack:GetArrayNum() ~= 0 or fixture_menu then error("native_host_invite_journal_unavailable", 0) end
    local class = StaticFindObject(journal_class)
    if not valid(class) then
        LoadAsset(journal_class)
        class = StaticFindObject(journal_class)
    end
    if not valid(class) then error("native_host_invite_journal_class_unavailable", 0) end
    local result = {}
    context.controller:OpenMenu(class, result)
    local shown = journal(state, phase)
    if not shown or not same(shown.menu, result.CreatedMenu) then error("native_host_invite_journal_open_unverified", 0) end
    fixture_menu = shown
    return "native_host_invite_journal_open"
end

local function fixture_close(state, phase)
    if not same_journal(fixture_menu, journal(state, phase)) then error("native_host_invite_journal_close_unqualified", 0) end
    local result = {}
    fixture_menu.controller:CloseMenu(result)
    if result.Success ~= true or fixture_menu.controller.MenuStack:GetArrayNum() ~= 0
        or (valid(fixture_menu.menu) and fixture_menu.menu:IsInViewport()) then
        error("native_host_invite_journal_close_unverified", 0)
    end
    fixture_menu = nil
    reset()
    return "native_host_invite_journal_closed"
end

function invite.command(arguments, state, phase)
    if arguments[2] ~= "native-host-invite-fixture" then return end
    if not fixture_enabled() or not bridge then error("native_host_invite_fixture_unavailable", 0) end
    local operation, value = arguments[3], arguments[4]
    local expected = operation == "participant" or operation == "starter" or operation == "address" and value ~= nil
    if #arguments ~= (expected and 4 or 3) then error("native_host_invite_fixture_arguments", 0) end
    if operation == "open" then return fixture_open(state, phase) end
    if operation == "close" then return fixture_close(state, phase) end
    local context = journal(state, phase)
    if not same_journal(fixture_menu, context) then error("native_host_invite_journal_unavailable", 0) end
    local active = scope(state, phase)
    if operation == "observe" then
        if active then progress(active, state, phase) end
        local output = "native_host_invite_phase=" .. report(active)
        if active and active.op then output = output .. ";op=" .. active.op end
        if active and active.failure then output = output .. ";error=" .. active.failure end
        return output
    end
    if not active then error("native_host_invite_offer_unavailable", 0) end
    local action
    if operation == "begin" and active.screen == "journal" then action = "confirm" end
    if operation == "submit" and active.screen == "review" then action = "confirm" end
    if labels[operation] and active.screen == operation then
        if operation == "address" then
            if value and value ~= "127.0.0.1" then error("native_host_invite_fixture_arguments", 0) end
        elseif #value > 64 or not value:match("^[A-Za-z0-9][A-Za-z0-9 _-]*$") then
            error("native_host_invite_fixture_arguments", 0)
        end
        editor.write(active.handle, state, phase, value or "")
        action = "next"
    end
    if not action then error("native_host_invite_fixture_arguments", 0) end
    local accepted, failure = press(active, action, state, phase)
    if not accepted then error(failure, 0) end
    return "native_host_invite_" .. operation .. "_accepted"
end

function invite.rows(state, phase, status_row)
    local context = journal(state, phase)
    if not context then return {} end
    local active = session and same_journal(session.context, context) and session or nil
    return {status_row("native-host-invite-journal", state.map, report(active))}
end

function invite.sync(state, phase)
    if not bridge then return end
    current = {state = state, phase = phase}
    local active = scope(state, phase)
    if active then progress(active, state, phase) end
end

local function poll()
    local events = queued
    queued = {}
    if not current or not next(events) or not foreground() then return end
    local state, phase = current.state, current.phase
    local active = scope(state, phase)
    if not active then return end
    local action
    for _, key in ipairs(keys) do
        if events[key[1]] == active then
            if action then return end
            action = key[1]
        end
    end
    if action then press(active, action, state, phase) end
end

function invite.start(path)
    if bridge or type(path) ~= "string" or path == "" then return end
    bridge = path
    offer_path = path .. "/host-invite-offer.tsv"
    request_path = path .. "/host-invite-request.tsv"
    response_path = path .. "/host-invite-response.tsv"
    if os.getenv("SILVERFISH_COOP_HEADLESS") == "1" or type(RegisterKeyBind) ~= "function" or not Key
        or type(SilverfishNativeGameWindowFocused) ~= "function" or not (Key.F7 and Key.F8 and Key.F9) then return end
    for _, key in ipairs(keys) do
        RegisterKeyBind(Key[key[2]], function()
            if session and current then queued[key[1]] = session end
        end)
    end
    loop = LoopInGameThreadWithDelay(32, function()
        if not pcall(poll) then reset() end
    end)
end

return invite
