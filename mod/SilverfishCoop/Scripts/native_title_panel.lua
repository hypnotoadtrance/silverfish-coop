local panel = {}
local title = require("native_title_entry")
local display = require("native_title_display")

local keys = {host = "F7", join = "F8", hide = "F9"}
local actions = {
    ["host"] = {host = true}, ["join"] = {join = true}, ["host,join"] = {host = true, join = true},
}
local session_path, request_path, loop, current, session, report
local dismissed, submitted
local input_keys = {}
local key_order = {"hide", "host", "join"}
local queued_keys, bound_keys = {}, false
local settings_fixture, credits_fixture
local credits_path = "/Game/Classes/GUI/Credits_MainMenu_Widget.Credits_MainMenu_Widget_C"
local initial_port, edited_port = "7777", "28181"

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function same_title(first, second)
    return first and second and same(first.world, second.world) and same(first.controller, second.controller)
        and same(first.menu, second.menu) and same(first.viewport, second.viewport)
end

local function read_offer()
    local file = io.open(session_path, "rb")
    if not file then return end
    local contents = file:read(129)
    file:close()
    if not contents or #contents > 128 then return end
    local generation, choices, timestamp =
        contents:match("^1\t([0-9a-f]+)\t([a-z,%-]+)\t([0-9]+)\r?\n$")
    local expires = timestamp and #timestamp <= 10 and math.tointeger(tonumber(timestamp))
    local now = os.time()
    if not generation or #generation ~= 32 or not actions[choices] or not expires
        or expires < now or expires > now + 10 then return end
    return {generation = generation, choices = choices, expires = expires}
end

local function close_host_form(owner)
    local form = owner.form
    owner.form = nil
    if form then form.api.remove(form.draft) end
end

local function remove_display()
    local old = session
    session = nil
    if old then
        close_host_form(old)
        display.remove(old.widget)
    end
end

local function message(text)
    display.text(session.widget, text)
end

local function menu_text(offer)
    local lines = {"Direct IP co-op"}
    if actions[offer.choices].host then lines[#lines + 1] = "F7   Host a campaign" end
    if actions[offer.choices].join then lines[#lines + 1] = "F8   Join with an invite" end
    lines[#lines + 1] = "F9   Hide"
    return table.concat(lines, "\n")
end

local function mount(context, offer)
    local widget = display.mount(context, menu_text(offer))
    session = {context = context, offer = offer, widget = widget, held = {}}
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

local function write_request(generation, action, host_values)
    local pending = io.open(request_path, "rb")
    if pending then
        pending:close()
        return false
    end
    local id = request_id()
    if not id then return false end
    local temporary = request_path .. "." .. id .. ".tmp"
    local file = io.open(temporary, "wb")
    if not file then return false end
    local row = host_values and {"2", generation, id, "host", table.unpack(host_values)}
        or {"1", generation, id, action}
    local written = file:write(table.concat(row, "\t"), "\n")
    local closed = file:close()
    -- Windows rename refuses an existing destination. Never remove or replace a pending request.
    if written and closed and os.rename(temporary, request_path) then return true end
    os.remove(temporary)
    return false
end

local function can_choose(context)
    return session and same_title(context, session.context) and os.time() <= session.offer.expires
        and valid(session.widget) and session.widget:IsInViewport()
end

local function active_offer(action, context)
    if not can_choose(context) then return nil, "title_panel_not_ready" end
    if not actions[session.offer.choices][action] then return nil, "title_action_not_offered" end
    if submitted == session.offer.generation then return nil, "title_request_already_pending" end
    local latest = read_offer()
    if not latest or latest.generation ~= session.offer.generation or latest.choices ~= session.offer.choices then
        remove_display()
        report = nil
        return nil, "title_offer_changed"
    end
    return latest
end

local function choose(action, context, host_values)
    if not can_choose(context) then return false, "title_panel_not_ready" end
    if action == "hide" then
        dismissed = session.offer.generation
        report = {state = "hidden", choices = session.offer.choices}
        remove_display()
        return true
    end
    local latest, reason = active_offer(action, context)
    if not latest then return false, reason end
    if write_request(latest.generation, action, host_values) then
        submitted = latest.generation
        close_host_form(session)
        report = {state = "pending", choices = latest.choices}
        message("Direct IP co-op\nStarting " .. (action == "host" and "host" or "join") .. "...\nF9   Hide")
        return true
    end
    close_host_form(session)
    report = {state = "unavailable", choices = latest.choices, reason = "title_request_write_failed"}
    message("Could not start co-op. Try again or press F9 to hide.")
    return false, report.reason
end

local function handle_host_form(action, state, phase, context)
    local offer, reason = active_offer("host", context)
    if not offer then return false, reason end
    local form = session.form
    if not form then
        if action ~= "host" then return false, "title_host_form_unavailable" end
        local api = require("native_title_host_form")
        session.form = {api = api, draft = api.open(state, phase)}
        message("")
        report = {state = "ready", choices = offer.choices}
        return true
    end
    if action == "hide" then
        close_host_form(session)
        message(menu_text(offer))
        return true
    end
    if action == "join" then return form.api.next(form.draft, state, phase) end
    local values, failure = form.api.selection(form.draft, state, phase)
    if not values then return false, failure end
    return choose("host", title.probe(state, phase), values)
end

local function fixture_enabled()
    return os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
end

local function host_form_fixture_command(fields, state, phase)
    if not fixture_enabled() then error("title_host_form_fixture_unavailable", 0) end
    local operation, value = fields[3], fields[4]
    local has_value = operation == "campaign" or operation == "port"
    if #fields ~= (has_value and 4 or 3) then error("title_host_form_fixture_arguments", 0) end
    local context = title.probe(state, phase)
    local offer, reason = active_offer("host", context)
    if not offer then error(reason, 0) end
    if operation == "open" then
        if session.form or settings_fixture or credits_fixture then error("title_host_form_already_open", 0) end
        local accepted, failure = handle_host_form("host", state, phase, context)
        if not accepted then error(failure, 0) end
        return "title_host_form_open"
    end
    local form = session.form
    if not form then error("title_host_form_unavailable", 0) end
    if operation == "campaign" then
        local suffix = value:match("^title%-host%-settings%-([0-9a-f]+)$")
        if not suffix or #suffix ~= 8 or not form.api.fixture_write(form.draft, state, phase, "campaign", value) then
            error("title_host_form_fixture_arguments", 0)
        end
        local accepted, failure = handle_host_form("join", state, phase, context)
        if not accepted then error(failure, 0) end
        return "title_host_form_campaign_entered"
    end
    if operation == "port" then
        if #value > 5 or not value:match("^[0-9]+$") or tonumber(value) > 65535
            or (value ~= "0" and value:sub(1, 1) == "0")
            or not form.api.fixture_write(form.draft, state, phase, "port", value) then
            error("title_host_form_fixture_arguments", 0)
        end
        return "title_host_form_port_entered"
    end
    local action = operation == "submit" and "host" or operation == "cancel" and "hide"
    if not action then error("title_host_form_fixture_arguments", 0) end
    local accepted, failure = handle_host_form(action, state, phase, context)
    if not accepted then error(failure, 0) end
    return operation == "submit" and "title_host_form_submitted" or "title_host_form_cancelled"
end

local function remove_settings_fixture()
    local previous = settings_fixture
    settings_fixture = nil
    return previous and previous.editor.remove(previous.handle)
end

local function exact_credits(record)
    local context, credits = record.context, record.dialog
    local stack = context.controller.MenuStack
    return stack and stack:GetArrayNum() == context.count + 1 and same(stack[context.count], context.menu)
        and same(stack[context.count + 1], credits) and same(credits:GetClass(), StaticFindObject(credits_path))
        and same(credits:GetOwningPlayer(), context.controller) and same(credits:GetOuter(), context.menu:GetOuter())
        and credits:IsInViewport()
end

local function cover_settings_fixture(record, state, phase)
    local context = record.context
    local class = StaticFindObject(credits_path)
    if not valid(class) then
        LoadAsset(credits_path)
        class = StaticFindObject(credits_path)
    end
    if not valid(class) or credits_fixture or not same_title(context, title.probe(state, phase)) then
        error("title_settings_fixture_credits_unavailable", 0)
    end
    local created = {}
    context.controller:OpenMenu(class, created)
    local cover = {context = context, dialog = created.CreatedMenu}
    if not exact_credits(cover) then error("title_settings_fixture_credits_open_unverified", 0) end
    credits_fixture = cover
    local read, read_error = pcall(record.editor.read, record.handle, state, phase)
    local wrote, write_error = pcall(record.editor.write, record.handle, state, phase, initial_port)
    local removed = remove_settings_fixture()
    if read or wrote or read_error ~= "title_settings_editor_scope_changed"
        or write_error ~= "title_settings_editor_scope_changed" or not removed then
        error("title_settings_fixture_stale_scope_unverified", 0)
    end
    return "title_settings_fixture_stale_read_write_blocked_removed"
end

local function uncover_settings_fixture(state, phase)
    local cover = credits_fixture
    local before = cover and cover.context
    if not before or phase ~= "ready" or state.map ~= before.map or state.server ~= false or state.clients ~= 0
        or state.listening or state.local_pawn or not same(state.world, before.world)
        or not same(state.controller, before.controller) or valid(state.world.NetDriver) or not exact_credits(cover) then
        error("title_settings_fixture_credits_close_unqualified", 0)
    end
    local result = {}
    before.controller:CloseMenu(result)
    local stack = before.controller.MenuStack
    if result.Success ~= true or (valid(cover.dialog) and cover.dialog:IsInViewport())
        or not stack or stack:GetArrayNum() ~= before.count or not same(stack[before.count], before.menu) then
        error("title_settings_fixture_credits_close_unverified", 0)
    end
    credits_fixture = nil
    return "title_settings_fixture_credits_closed"
end

local function settings_fixture_command(fields, state, phase)
    if not fixture_enabled() then error("title_settings_fixture_unavailable", 0) end
    if #fields ~= 3 then error("title_settings_fixture_arguments", 0) end
    local operation = fields[3]
    if operation == "uncover" then return uncover_settings_fixture(state, phase) end
    local context = title.probe(state, phase)
    if not context then error("title_settings_fixture_title_unavailable", 0) end
    if operation == "mount" then
        if settings_fixture or credits_fixture then error("title_settings_fixture_already_open", 0) end
        local editor = require("native_title_settings_widget")
        local handle = editor.mount(state, phase, "port", "Host co-op\nUDP port", initial_port)
        settings_fixture = {editor = editor, handle = handle, context = context}
        return "title_settings_fixture_mounted"
    end
    local record = settings_fixture
    if not record or not same_title(record.context, context) then error("title_settings_fixture_editor_unavailable", 0) end
    local editor, handle = record.editor, record.handle
    if operation == "observe" then
        local value = editor.read(handle, state, phase)
        if value ~= initial_port and value ~= edited_port then error("title_settings_fixture_readback_unverified", 0) end
        return "title_settings_fixture_port=" .. value
    end
    if operation == "edit" then
        if editor.write(handle, state, phase, edited_port) ~= edited_port then
            error("title_settings_fixture_readback_unverified", 0)
        end
        return "title_settings_fixture_edited"
    end
    if operation == "focus" then
        return "title_settings_fixture_focus=" .. (editor.focus(handle, state, phase) and "1" or "0")
    end
    if operation == "remove" then
        if not remove_settings_fixture() then error("title_settings_fixture_remove_unverified", 0) end
        return "title_settings_fixture_removed"
    end
    if operation == "cover" then return cover_settings_fixture(record, state, phase) end
    error("title_settings_fixture_arguments", 0)
end

local function poll()
    local events = queued_keys
    queued_keys = {}
    if not session then return end
    local context = current and title.probe(current.state, current.phase)
    if not can_choose(context) then
        remove_display()
        report = nil
        return
    end
    local pending = submitted == session.offer.generation
    local selected, rises = nil, 0
    for _, action in ipairs(key_order) do
        if session.form or action == "hide" or not pending and actions[session.offer.choices][action] then
            local pressed = events[action] == session
            if not bound_keys then
                if not input_keys[action] then
                    local key = FName(keys[action], EFindName.FNAME_Find)
                    if key:GetComparisonIndex() == 0 then error("title_panel_key_unavailable", 0) end
                    input_keys[action] = {KeyName = key}
                end
                local down = context.controller:IsInputKeyDown(input_keys[action])
                if type(down) ~= "boolean" then error("title_panel_key_unqualified", 0) end
                pressed = down and session.held[action] == false
                session.held[action] = down
            end
            if pressed then selected, rises = action, rises + 1 end
        end
    end
    if rises ~= 1 then return end
    if session.form or selected == "host" then
        handle_host_form(selected, current.state, current.phase, context)
    else
        choose(selected, context)
    end
end

function panel.sync(state, phase)
    current = nil
    if not session_path then return end
    local context = title.probe(state, phase)
    if settings_fixture and not same_title(settings_fixture.context, context) then remove_settings_fixture() end
    local offer = context and read_offer()
    if not offer then
        remove_display()
        report = nil
        return
    end
    current = {state = state, phase = phase}
    if dismissed == offer.generation then return end
    if session and same_title(context, session.context) and session.offer.generation == offer.generation
        and session.offer.choices == offer.choices then
        session.offer = offer
        return
    end
    remove_display()
    if submitted == offer.generation then return end
    local ok, failure = pcall(mount, context, offer)
    local reason = type(failure) == "string" and failure:match("^title_panel_[a-z_]+$") or "title_panel_mount_failed"
    report = {state = ok and "ready" or "unavailable", choices = offer.choices,
        reason = not ok and reason or nil}
    if not ok then remove_display() end
end

function panel.command(fields, state, phase)
    if fields[2] == "native-title-settings-widget-fixture" then return settings_fixture_command(fields, state, phase) end
    if fields[2] == "native-title-host-form-fixture" then return host_form_fixture_command(fields, state, phase) end
    if fields[2] ~= "native-title-panel-press-fixture" then return end
    if not fixture_enabled() then error("title_panel_fixture_unavailable", 0) end
    local action = fields[3]
    if #fields ~= 3 or not keys[action] then error("title_panel_fixture_arguments", 0) end
    local accepted, reason = choose(action, title.probe(state, phase))
    if not accepted then error(reason, 0) end
    return "qualified staged title control dispatched"
end

function panel.rows(state, phase, status_row)
    if not report or not title.probe(state, phase) then return {} end
    local mounted = session and valid(session.widget) and session.widget:IsInViewport() or false
    return {status_row("native-title-panel", state.map, report.state, report.choices, mounted and 1 or 0, report.reason or "-")}
end

function panel.start(bridge)
    if loop or type(bridge) ~= "string" or bridge == "" then return end
    session_path = bridge .. "/title-handoff-session.tsv"
    request_path = bridge .. "/title-handoff-request.tsv"
    if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" and type(RegisterKeyBind) == "function"
        and Key and Key.F7 and Key.F8 and Key.F9 then
        bound_keys = true
        for _, action in ipairs(key_order) do
            RegisterKeyBind(Key[keys[action]], function()
                -- UE4SS reports a down edge. Qualify Unreal objects on the game thread only.
                if session and current then queued_keys[action] = session end
            end)
        end
    end
    loop = LoopInGameThreadWithDelay(16, function()
        if not pcall(poll) then
            local choices = session and session.offer.choices
            remove_display()
            report = choices and {state = "unavailable", choices = choices, reason = "title_panel_input_unavailable"} or nil
        end
    end)
end

return panel
