local invite = {}
local surface = require("native_title_surface")
local players = require("players")

local journal_class = "/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local stats_path = "/Script/Engine.Default__GameplayStatics"
local viewport_class = "/Script/Engine.GameViewportClient"
local fields = {"participant", "starter", "address"}
local labels = {participant = "Participant", starter = "Starter character", address = "Host address"}
local limits = {participant = 256, starter = 256, address = 253}
local errors = {
    invalid_request = "Check the invitation details.",
    invalid_address = "Enter a reachable host address.",
    invite_unavailable = "The invitation could not be issued.",
    export_unverified = "The invitation export could not be verified.",
    export_changed = "The invitation export changed.",
    operation_conflict = "This request was already used.",
    operation_limit = "Close the Journal and try again.",
}
local handoff_maps = {
    ["/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"] = true,
    ["/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"] = true,
}
local handoff_screens = {package_choosing = "choosing", package_submitting = "submitting",
    package_transferred = "transferred", package_refused = "refused"}
local bridge, offer_path, request_path, response_path, current, session, fixture_menu
local dispatch

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
        or not valid(world.NetDriver) or valid(world.NetDriver.ServerConnection) then
        return nil, "listen_scope_unavailable"
    end
    if not exact(controller, controller_class) or not controller:IsLocalController()
        or not controller:HasAuthority() or controller:IsActorBeingDestroyed() then
        return nil, "controller_unqualified"
    end
    if controller.Loading ~= false then return nil, "controller_loading" end
    if not exact(pawn, pawn_class) or pawn.Dead ~= false or not pawn:HasAuthority()
        or not pawn:IsLocallyControlled() or pawn:IsActorBeingDestroyed() then
        return nil, "pawn_unqualified"
    end
    if players.is_downed(pawn) ~= false then return nil, "standing_pawn_unverified" end
    if not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(controller:GetWorld(), world) or not same(pawn:GetWorld(), world) then
        return nil, "pawn_scope_changed"
    end
    local statics, viewport = StaticFindObject(stats_path), FindFirstOf("GameViewportClient")
    if not valid(statics) or statics:IsGamePaused(controller) ~= false then return nil, "world_pause_unverified" end
    if not exact(viewport, viewport_class) or not same(viewport.World, world) then return nil, "viewport_unqualified" end
    local instance = statics:GetGameInstance(controller)
    if not valid(instance) or not same(viewport:GetOuter(), instance:GetOuter()) then
        return nil, "game_instance_unqualified"
    end
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

local function package_guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]")
end

local function character_id(value)
    return type(value) == "string" and #value == 36 and value:find("[1-9a-f]")
        and value:match("^%x%x%x%x%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x$")
        and value == value:lower()
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
    if active.view then
        surface.remove(active.view)
        active.view = nil
    end
end

local function reset()
    if not session then return end
    local previous = session
    session = nil
    clear_view(previous)
    previous.export = nil
end

local function button(key, label)
    return {key = key, label = label}
end

local function render(active, screen, hint, initial)
    clear_view(active)
    active.screen = screen
    local title, rows, note = "Co-op invitation", {}, hint
    local back = button("back", "Back")
    if labels[screen] then
        if screen == "address" and not note then note = "Leave blank to use the configured address." end
        rows = {
            {field = {key = screen, label = labels[screen], limit = limits[screen],
                value = initial or active.values[screen]}},
            {button("next", "Next"), back},
        }
    elseif screen == "journal" then
        title = "Co-op"
        if active.offer then rows[#rows + 1] = {button("confirm", "Invite a co-op player")} end
        if active.package then rows[#rows + 1] = {button("next", "Give the sealed package")} end
        if #rows == 0 then return end
    elseif screen == "review" then
        title = "Create this invitation?"
        rows = {{button("confirm", "Create"), back}}
    elseif screen == "pending" then
        title = "Creating invitation…"
        rows = {{back}}
    elseif screen == "exported" then
        title = "Invitation ready"
        rows = {{button("confirm", "Copy invitation"), back}}
    elseif screen == "refused" then
        note = note or "Could not create invitation."
        rows = {{button("confirm", "Try again"), back}}
    elseif screen == "package_choosing" then
        title = "Give the sealed package"
        local recipient = active.package.recipients[active.selected]
        local end_at = utf8.offset(recipient, 49)
        note = "Recipient: " .. (end_at and recipient:sub(1, end_at - 1) .. "…" or recipient)
        local actions = {button("confirm", "Give package")}
        if #active.package.order > 1 then actions[#actions + 1] = button("next", "Next recipient") end
        actions[#actions + 1] = back
        rows = {actions}
    elseif screen == "package_submitting" then
        title = "Giving the sealed package…"
    else
        title = screen == "package_transferred" and "Sealed package given." or "Could not give the sealed package."
        rows = {{back}}
    end
    local view
    view = surface.mount(active.context, {title = title, note = note, rows = rows, center = screen ~= "journal"},
        function(action) dispatch(active, view, action) end)
    active.view = view
end

local function scope(state, phase)
    if not bridge then return end
    local context = journal(state, phase)
    if not context then reset(); return end
    local offer = read_offer()
    if not session or not same_journal(session.context, context) then
        reset()
        session = {context = context, offer = offer, values = {}}
        render(session, "journal")
    elseif session.offer ~= offer and not same_offer(session.offer, offer) then
        session.offer = offer
        session.op, session.export, session.retry, session.failure, session.deadline = nil, nil, nil, nil, nil
        session.values = {}
        if not handoff_screens[session.screen] then render(session, "journal") end
    end
    return session
end

local function handoff_command(verb, arguments, state, phase)
    local active = scope(state, phase)
    if not active or not handoff_maps[state.map] then error("stock_missing_contact_handoff_journal_unavailable", 0) end
    if verb == "native-package-handoff-clear" then
        if #arguments ~= 2 then error("stock_missing_contact_handoff_journal_arguments", 0) end
        local had_offer = active.package ~= nil
        active.package, active.selected = nil, nil
        if active.screen == "package_choosing" or active.screen == "journal" and had_offer then
            render(active, "journal")
        end
        return "package handoff offer cleared"
    end
    local id = arguments[3]
    if verb == "native-package-handoff-result" then
        local outcome = arguments[4]
        if #arguments ~= 4 or (outcome ~= "transferred" and outcome ~= "refused") then
            error("stock_missing_contact_handoff_journal_arguments", 0)
        end
        if active.result and active.result.offer == id and active.result.outcome == outcome then
            return "package handoff result shown"
        end
        if not active.intent or id ~= active.intent.offer then
            error("stock_missing_contact_handoff_journal_result_unavailable", 0)
        end
        active.package, active.selected, active.intent = nil, nil, nil
        active.result = {offer = id, outcome = outcome}
        render(active, "package_" .. outcome)
        return "package handoff result shown"
    end
    local guid = arguments[4]
    if #arguments < 6 or #arguments > 12 or #arguments % 2 ~= 0 or not token(id) or not package_guid(guid) then
        error("stock_missing_contact_handoff_journal_arguments", 0)
    end
    if active.intent then error("stock_missing_contact_handoff_journal_pending", 0) end
    local offer = {id = id, guid = guid, order = {}, recipients = {}}
    for index = 5, #arguments, 2 do
        local character, label = arguments[index], arguments[index + 1]
        local length = type(label) == "string" and #label <= 384 and utf8.len(label)
        if not character_id(character) or offer.recipients[character] or not length or length == 0 or length > 120
            or label:find("[%z\1-\31\127]") or not label:find("%S") then
            error("stock_missing_contact_handoff_journal_arguments", 0)
        end
        offer.order[#offer.order + 1], offer.recipients[character] = character, label
    end
    local prior = active.package
    if prior and prior.id == id then
        if prior.guid ~= guid or #prior.order ~= #offer.order then
            error("stock_missing_contact_handoff_journal_arguments", 0)
        end
        for index, character in ipairs(offer.order) do
            if prior.order[index] ~= character or prior.recipients[character] ~= offer.recipients[character] then
                error("stock_missing_contact_handoff_journal_arguments", 0)
            end
        end
        return "package handoff offer installed"
    end
    active.package, active.selected = offer, nil
    if active.screen == "journal" or active.screen == "package_choosing" then
        render(active, "journal")
    end
    return "package handoff offer installed"
end

local function valid_input(field, value)
    if field == "address" then return value == "" or value:match("^[A-Za-z0-9.:_-]+$") ~= nil end
    local count = utf8.len(value)
    return count and count > 0 and count <= 64
end

local function advance(active)
    local field = active.screen
    local value = surface.read(active.view, field)
    if value and field ~= "address" then value = value:match("^ *(.-) *$") end
    if not value or not valid_input(field, value) then
        local tip = field == "address" and "Enter a reachable address." or "Enter a name of up to 64 characters."
        render(active, field, tip, value)
        return false, "native_host_invite_invalid_" .. field
    end
    active.values[field] = value
    for index, name in ipairs(fields) do
        if name == field then
            render(active, fields[index + 1] or "review")
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

local function publish(active)
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
        render(active, "refused", "Could not send the invitation request.")
        return false, "native_host_invite_request_unavailable"
    end
    active.op, active.deadline = id, os.time() + 12
    active.retry = {id = id, body = body}
    active.failure = nil
    render(active, "pending")
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

local function progress(active)
    if active.screen ~= "pending" then return end
    local outcome, code = read_response(active)
    if outcome == "ok" then
        active.export = read_export(active.op)
        if active.export then
            active.retry = nil
            render(active, "exported")
            return
        end
        code = "export_unverified"
    elseif not outcome and os.time() <= active.deadline then
        return
    end
    active.failure = code or "invite_unavailable"
    render(active, "refused", errors[active.failure])
end

local function foreground()
    return os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" and type(SilverfishNativeGameWindowFocused) == "function"
        and SilverfishNativeGameWindowFocused() == true
end

local function copy(active, state, phase)
    if not foreground() or type(SilverfishCopyHostInvitation) ~= "function" or read_response(active) ~= "ok" then
        render(active, "exported", "Copy is unavailable.")
        return false, "native_host_invite_copy_unavailable"
    end
    local raw, text = read_export(active.op)
    if not raw or raw ~= active.export then
        active.export, active.failure = nil, "export_changed"
        render(active, "refused", errors.export_changed)
        return false, "native_host_invite_export_changed"
    end
    if not foreground() or not same_journal(active.context, journal(state, phase)) or not same_offer(active.offer, read_offer())
        or SilverfishCopyHostInvitation(text, active.op) ~= true then
        return false, "native_host_invite_copy_unavailable"
    end
    render(active, "exported", "Invitation copied.")
    return true
end

local function press(active, action, state, phase)
    local screen = active.screen
    if screen == "journal" and action == "next" and active.package then
        active.selected = active.package.order[1]
        render(active, "package_choosing")
        return true
    end
    if handoff_screens[screen] then
        if action == "back" and screen ~= "package_submitting" then
            active.selected, active.result = nil, nil
            render(active, "journal")
            return true
        end
        if screen == "package_choosing" and action == "next" then
            local order = active.package.order
            for index, character in ipairs(order) do
                if character == active.selected then
                    active.selected = order[index % #order + 1]
                    render(active, "package_choosing")
                    return true
                end
            end
        end
        local widget = active.view and active.view.widget
        if screen == "package_choosing" and action == "confirm" and active.package.recipients[active.selected]
            and valid(widget) and widget:IsInViewport() and widget:IsVisible() then
            active.intent = {offer = active.package.id, character = active.selected}
            render(active, "package_submitting")
            return true
        end
        return false, "stock_missing_contact_handoff_journal_action_unavailable"
    end
    if not active.offer then return false, "native_host_invite_offer_unavailable" end
    if action == "back" then
        active.op, active.export, active.retry, active.failure = nil, nil, nil, nil
        active.values = {}
        render(active, "journal")
        return true
    end
    if action == "next" and labels[screen] then return advance(active) end
    if action ~= "confirm" then return false, "native_host_invite_action_unavailable" end
    if screen == "journal" or screen == "refused" then
        render(active, "participant")
        return true
    end
    if screen == "review" then return publish(active) end
    if screen == "exported" then return copy(active, state, phase) end
    return false, "native_host_invite_action_unavailable"
end

local function report(active)
    if not active or active.screen == "journal" or handoff_screens[active.screen] then return "journal" end
    if active.screen == "pending" or active.screen == "exported" or active.screen == "refused" then return active.screen end
    return "editing"
end

local function fixture_enabled()
    return os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
end

local function fixture_open(state, phase)
    local context, failure = host(state, phase)
    if not context then error("native_host_invite_" .. failure, 0) end
    local stack = context.controller.MenuStack
    if not stack or stack:GetArrayNum() ~= 0 or fixture_menu then
        error("native_host_invite_journal_unavailable", 0)
    end
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
    local verb = arguments[2]
    if verb == "native-package-handoff-offer" or verb == "native-package-handoff-result"
        or verb == "native-package-handoff-clear" then return handoff_command(verb, arguments, state, phase) end
    if verb ~= "native-host-invite-fixture" then return end
    if not fixture_enabled() or not bridge then error("native_host_invite_fixture_unavailable", 0) end
    local operation, value = arguments[3], arguments[4]
    local expected = operation == "participant" or operation == "starter" or operation == "address" and value ~= nil
    if #arguments ~= (expected and 4 or 3) then error("native_host_invite_fixture_arguments", 0) end
    if operation == "open" then return fixture_open(state, phase) end
    if operation == "close" then return fixture_close(state, phase) end
    local context = journal(state, phase)
    if not same_journal(fixture_menu, context) then error("native_host_invite_journal_unavailable", 0) end
    local active = scope(state, phase)
    local package_action
    if active then
        if operation == "handoff-open" and active.screen == "journal" and active.package then package_action = "next" end
        if operation == "handoff-next" and active.screen == "package_choosing" then package_action = "next" end
        if operation == "handoff-confirm" and active.screen == "package_choosing" then package_action = "confirm" end
        if operation == "handoff-back" and handoff_screens[active.screen] then package_action = "back" end
    end
    if package_action then
        local accepted, failure = press(active, package_action, state, phase)
        if not accepted then error(failure, 0) end
        return "native_host_invite_" .. operation .. "_accepted"
    end
    if operation == "observe" then
        if active then progress(active) end
        local output = "native_host_invite_phase=" .. report(active)
        if active and active.op then output = output .. ";op=" .. active.op end
        if active and active.failure then output = output .. ";error=" .. active.failure end
        return output
    end
    if not active or not active.offer then error("native_host_invite_offer_unavailable", 0) end
    local action
    if operation == "begin" and active.screen == "journal" then action = "confirm" end
    if operation == "submit" and active.screen == "review" then action = "confirm" end
    if labels[operation] and active.screen == operation then
        if operation == "address" then
            if value and value ~= "127.0.0.1" then error("native_host_invite_fixture_arguments", 0) end
        elseif #value > 64 or not value:match("^[A-Za-z0-9][A-Za-z0-9 _-]*$") then
            error("native_host_invite_fixture_arguments", 0)
        end
        if not surface.write(active.view, operation, value or "") then
            error("native_host_invite_editor_unavailable", 0)
        end
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
    local rows = {status_row("native-host-invite-journal", state.map, report(active))}
    if not handoff_maps[state.map] then return rows end
    local pawn = context.pawn:GetFullName()
    local intent = active and active.intent
    local offer = intent and intent.offer or active and active.package and active.package.id or "-"
    local handoff_state = active and handoff_screens[active.screen] or "idle"
    rows[#rows + 1] = status_row("native-package-handoff-journal", state.map, pawn, handoff_state, offer)
    if intent then
        rows[#rows + 1] = status_row("native-package-handoff-intent", state.map, pawn, intent.offer, intent.character)
    end
    return rows
end

function invite.sync(state, phase)
    if not bridge then return end
    current = {state = state, phase = phase}
    local active = scope(state, phase)
    if active then progress(active) end
end

dispatch = function(owner, view, action)
    if not current or not foreground() or session ~= owner or owner.view ~= view then return end
    local ok = pcall(function()
        local state, phase = current.state, current.phase
        local active = scope(state, phase)
        if active == owner and active.view == view then press(active, action, state, phase) end
    end)
    if not ok then reset() end
end

function invite.start(path)
    if bridge or type(path) ~= "string" or path == "" then return end
    bridge = path
    offer_path = path .. "/host-invite-offer.tsv"
    request_path = path .. "/host-invite-request.tsv"
    response_path = path .. "/host-invite-response.tsv"
end

return invite
