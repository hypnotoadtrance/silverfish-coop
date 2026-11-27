local form = {}
local editor = require("native_title_settings_widget")
local display = require("native_title_display")
local max_invite, max_reply, max_characters = 32768, 30720, 64
local page_size = 5
local endpoint_screens = {address = true, port = true, password = true}
local password_errors = {direct_password_required = true, direct_wrong_password = true}
local messages = {
    invalid_request = "The Join request was invalid.",
    invalid_invite = "This invitation is invalid for this build.",
    invalid_endpoint = "Enter a numeric host IP and a valid UDP port.",
    direct_join_disabled = "The host has disabled Direct IP.",
    direct_wrong_password = "Incorrect password.",
    direct_password_required = "Enter the host's password.",
    direct_password_rate_limited = "Too many attempts. Try again shortly.",
    direct_enrollment_required = "Connect to the host again.",
    direct_campaign_changed = "The host changed campaigns. Connect again.",
    direct_host_unreachable = "Could not connect to the host at this IP and UDP port.",
    direct_host_identity_changed = "The host changed. Connect again to review it.",
    direct_identity_verification_failed = "The server changed before it could connect. Try again.",
    direct_refused = "The host declined the connection.",
    incompatible_game_or_protocol = "The host and guest builds are incompatible.",
    invite_refused = "The host refused this invitation.",
    catalog_unavailable = "Could not reach the character catalog.",
    catalog_invalid = "The character catalog could not be verified.",
    import_limit = "Too many invites. Open Co-op again.",
    unknown_import = "Load the invitation again.",
    invite_changed = "The invitation changed. Load it again.",
    create_refused = "The host declined character creation.",
    request_pending = "A Join operation is already pending.",
    operation_conflict = "This Join operation conflicted with an earlier one.",
    operation_limit = "Too many operations. Open Co-op again.",
}

local function token(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9a-f]+$") and value:find("[^0]")
end

local function character_id(value)
    if type(value) ~= "string" or #value ~= 36 or not value:match("^[0-9a-f%-]+$") then return false end
    local a, b, c, d, e = value:match("^([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)$")
    return a and #a == 8 and #b == 4 and #c == 4 and #d == 4 and #e == 12 and value:find("[1-9a-f]")
end

local function single_line(value, limit)
    return type(value) == "string" and #value <= limit and not value:find("[%z\1-\31\127]") and utf8.len(value)
end

local function read_file(path, limit)
    local file = io.open(path, "rb")
    if not file then return end
    local data = file:read(limit + 1)
    file:close()
    if data and #data <= limit then return data end
end

local function read_response(draft)
    local request = io.open(draft.request, "rb")
    if request then request:close(); return end
    local data = read_file(draft.response, max_reply)
    if not data then return end
    local header, body = data:match("^([^\n]+)\n(.*)$")
    if not header then return end
    local version, generation, id, status, value, text_count =
        header:match("^([12])\t([0-9a-f]+)\t([0-9a-f]+)\t([a-z]+)\t([0-9a-z_]+)\t([0-9]+)$")
    if generation ~= draft.generation or id ~= draft.pending.id then return end
    if status == "error" and messages[value] and text_count == "0" and body == "" then
        return {error = value}
    end
    if status == "trust" and draft.pending.kind == "direct" and text_count == "1"
        and (value == "new" or value == "changed") then
        local fingerprint = body:match("^([0-9a-f]+)\n$")
        if fingerprint and #fingerprint == 64 then return {fingerprint = fingerprint, changed = value == "changed"} end
        return
    end
    local count = text_count and #text_count <= 2 and tonumber(text_count)
    if status ~= "ok" or not token(value) or not count or count > max_characters
        or tostring(count) ~= text_count or (draft.pending.kind == "create" and value ~= draft.import) then return end
    local offset = 1
    local creator, photo
    if version == "2" then
        creator, photo, offset = body:match("^([^\t\n]+)\t([^\t\n]+)\n()")
        local length = single_line(creator, 256)
        if not length or length < 1 or length > 64 then return end
        if #photo > 8500 or not photo:match("^[0-9a-f]+$") then photo = nil end
    end
    local characters, seen = {}, {}
    for _ = 1, count do
        local ending = body:find("\n", offset, true)
        if not ending then return end
        local id_value, name = body:sub(offset, ending - 1):match("^([0-9a-f%-]+)\t([^\t]+)$")
        local length = single_line(name, 256)
        if not character_id(id_value) or not length or length < 1 or length > 64 or seen[id_value] then return end
        seen[id_value] = true
        characters[#characters + 1] = {id = id_value, name = name}
        offset = ending + 1
    end
    if offset ~= #body + 1 then return end
    return {import = value, characters = characters, creator = creator, photo = photo}
end

function form.remove(draft)
    if draft.view then
        draft.surface.remove(draft.view)
        draft.view = nil
    end
    if draft.handle then
        editor.remove(draft.handle)
        draft.handle = nil
    end
    if draft.widget then
        display.remove(draft.widget)
        draft.widget = nil
    end
end

local function short_name(name)
    local end_at = utf8.offset(name, 33)
    return end_at and name:sub(1, end_at - 1) .. "…" or name
end

local function catalog_text(draft)
    local choice = draft.characters[draft.index]
    return choice and "Choose a character\n" .. short_name(choice.name) or "Create a character"
end

local function button(key, label)
    return {key = key, label = label}
end

local function field(key, label, hint, limit, value, secret)
    return {field = {key = key, label = label, hint = hint, limit = limit, value = value, secret = secret}}
end

local function page_count(draft)
    return math.max(1, math.ceil(#draft.characters / page_size))
end

local function render_surface(draft, screen, hint, initial)
    local title = draft.steam_port and "Join via Steam"
        or draft.direct and "Join by Direct IP" or "Join by Invitation"
    local rows = {}
    local note, focus = hint, nil
    local back = button("back", "Back")
    if screen == "steam_password" then
        rows = {field("password", "Lobby password", "", 512, nil, true), {button("connect", "Join"), back}}
    elseif screen == "steam_retry" then
        rows = {{button("connect", "Retry join"), back}}
    elseif endpoint_screens[screen] and not draft.steam_port then
        rows = {
            field("address", "Host IP", "IPv4 or IPv6 address", 45, draft.address),
            field("port", "UDP port", "", 5, draft.port),
            {toggle = {key = "use_password", label = "Use password", target = "password", checked = draft.use_password}},
            field("password", "Password", "", 512, nil, true),
            {button("connect", "Connect"), back},
        }
    elseif screen == "trust_host" then
        title = draft.host_trust.changed and "Host identity changed" or "Trust this host?"
        local endpoint = draft.steam_port and "Steam invitation" or draft.address .. " · UDP " .. draft.port
        note = endpoint .. "\nOnly continue if you expect this host. A fake server could collect your lobby password."
        rows = {{button("trust", "Trust and connect"), back}}
    elseif screen == "invite" then
        rows = {field("invite", "Invitation", "Paste the host's invitation", max_invite, initial, true),
            {button("connect", "Load invitation"), back}}
    elseif screen == "catalog" then
        title = "Choose a character"
        local last_page = page_count(draft)
        draft.page = math.min(draft.page or 1, last_page)
        local start = (draft.page - 1) * page_size + 1
        local last = math.min(#draft.characters, start + page_size - 1)
        if draft.index > #draft.characters then
            focus = "create"
        elseif draft.index >= start and draft.index <= last then
            focus = "character:" .. draft.index
        end
        for index = start, last do
            local row = button("character:" .. index, short_name(draft.characters[index].name))
            if draft.creator then
                local ending = utf8.offset(draft.creator, 21)
                row.detail = "Created by " .. (ending and draft.creator:sub(1, ending - 1) .. "…" or draft.creator)
                row.photo = draft.photo
            end
            rows[#rows + 1] = {row}
        end
        if last_page > 1 then
            local navigation = {}
            if draft.page > 1 then navigation[#navigation + 1] = button("previous_page", "Previous page") end
            if draft.page < last_page then navigation[#navigation + 1] = button("next_page", "Next page") end
            rows[#rows + 1] = navigation
            note = note or "Page " .. draft.page .. " of " .. last_page
        end
        rows[#rows + 1] = {button("create", "Create character"), back}
    elseif screen == "name" then
        title = "Create a character"
        rows = {field("character", "Name", "Character name", 256, initial), {button("create", "Create"), back}}
    elseif screen == "profile" then
        title = "Join with " .. short_name(draft.character_name)
        rows = {field("profile", "Local profile", "Default profile", 64, initial), {button("confirm", "Join"), back}}
    elseif screen == "confirm" then
        title = "Join with " .. short_name(draft.character_name)
        rows = {{button("confirm", "Join"), back}}
    else
        local pending = draft.pending and draft.pending.kind
        note = note or (pending == "create" and "Creating character…"
            or draft.steam_port and pending == "direct" and "Connecting through Steam…" or "Waiting for the host…")
        rows = {{back}}
    end
    if screen == "steam_password" or screen == "password" and password_errors[draft.failure] then
        focus = "password"
    end
    draft.view = draft.surface.mount(draft.context, {title = title, note = note, rows = rows, focus = focus}, draft.on_action)
end

local fields = {
    invite = "invite", address = "address", port = "port", password = "password", name = "character", profile = "profile",
}
local legacy_titles = {
    address = "Host IP", port = "UDP port", password = "Password", name = "Create a character",
    profile = "Local profile", waiting = "Waiting for the host…",
}

local function render(draft, state, phase, next_screen, hint, initial)
    form.remove(draft)
    draft.screen = next_screen
    if draft.surface then
        render_surface(draft, next_screen, hint, initial)
    else
        local label = next_screen == "catalog" and catalog_text(draft) or legacy_titles[next_screen] or "Join co-op"
        if hint then label = label .. "\n" .. hint end
        local key = fields[next_screen]
        if key then
            draft.handle = editor.mount(state, phase, key, label, initial)
            editor.focus(draft.handle, state, phase)
        else
            draft.widget = display.mount(draft.context, label)
        end
    end
end

local queue

function form.open(state, phase, context, bridge, generation, new_id, direct, on_action, defaults, steam_port)
    local surface = on_action and require("native_title_surface")
    local draft = {context = context, generation = generation, new_id = new_id, direct = direct,
        surface = surface, on_action = on_action, steam_port = steam_port, address = steam_port and "127.0.0.1",
        port = steam_port or (surface and direct and (defaults and defaults.port or "7777") or nil),
        request = bridge .. "/title-join-request.tsv", response = bridge .. "/title-join-response.tsv",
        fixture = bridge .. "/title-join-fixture.invite.json"}
    local ok, failure = pcall(function()
        if steam_port and surface then
            draft.screen = "steam_retry"
            local sent = queue(draft, state, phase, "direct", "")
            if not sent and not draft.view then render(draft, state, phase, "steam_retry") end
        else
            render(draft, state, phase, direct and "address" or "invite")
        end
    end)
    if not ok then
        form.remove(draft)
        error(failure, 0)
    end
    return draft
end

local function publish(draft, id, kind, payload)
    local pending = io.open(draft.request, "rb")
    if pending then pending:close(); return false end
    local temporary = draft.request .. "." .. id .. ".tmp"
    local file = io.open(temporary, "wb")
    if not file then return false end
    local written = file:write("1\t", draft.generation, "\t", id, "\t", kind, "\t", payload)
    local closed = file:close()
    if written and closed and os.rename(temporary, draft.request) then return true end
    os.remove(temporary)
    return false
end

queue = function(draft, state, phase, kind, value, detail)
    local retried = kind == "create" and draft.retry and draft.retry.name == value and draft.retry.import == draft.import
        and draft.retry.appearance == detail
    local id = retried and draft.retry.id or draft.new_id()
    if not token(id) then return false, "title_join_form_request_id_unavailable" end
    local payload
    if kind == "import" then
        payload = tostring(#value) .. "\n" .. value
    elseif kind == "direct" then
        payload = table.concat({draft.address, draft.port, value, detail}, "\t") .. "\n"
    else
        payload = draft.import .. "\t" .. value .. "\n" .. (detail or "")
    end
    if not publish(draft, id, kind, payload) then
        render(draft, state, phase, draft.screen, "Could not send. Try again.", kind ~= "direct" and value or nil)
        return false, "title_join_form_request_unavailable"
    end
    if kind == "create" then
        draft.retry = {name = value, import = draft.import, id = id, appearance = detail}
    end
    draft.pending = {kind = kind, id = id, expires = os.time() + 20}
    if kind == "direct" then draft.pending.password = value end
    draft.failure = nil
    render(draft, state, phase, "waiting")
    return true
end

function form.resume_creation(draft, state, phase, name, appearance, hint)
    if appearance then return queue(draft, state, phase, "create", name, appearance) end
    render(draft, state, phase, "name", hint, name)
end

function form.poll(draft, state, phase)
    local pending = draft.pending
    if not pending then return end
    local result = read_response(draft)
    if not result and os.time() <= pending.expires then return end
    draft.pending = nil
    if result and result.fingerprint then
        draft.host_trust = {fingerprint = result.fingerprint, changed = result.changed, password = pending.password}
        render(draft, state, phase, "trust_host")
        return
    end
    if result and result.characters then
        draft.import, draft.characters, draft.index, draft.page = result.import, result.characters, 1, 1
        draft.creator, draft.photo = result.creator, result.photo
        draft.retry, draft.failure = nil, nil
        render(draft, state, phase, "catalog")
        return
    end
    draft.failure = result and result.error or "catalog_unavailable"
    local reload = draft.failure == "invite_changed" or draft.failure == "unknown_import"
    if pending.kind == "create" and not reload then
        render(draft, state, phase, "name", messages[draft.failure], draft.retry.name)
    else
        draft.import, draft.characters, draft.retry = nil, nil, nil
        draft.creator, draft.photo = nil, nil
        if draft.steam_port then
            local screen = pending.kind == "direct" and password_errors[draft.failure] and "steam_password" or "steam_retry"
            render(draft, state, phase, screen, messages[draft.failure])
        elseif draft.direct then
            if password_errors[draft.failure] then
                draft.use_password = true
            end
            local edit_endpoint = pending.kind ~= "direct" or draft.failure == "invalid_endpoint"
            render(draft, state, phase, edit_endpoint and "address" or "password", messages[draft.failure],
                edit_endpoint and draft.address or nil)
        else
            render(draft, state, phase, "invite", messages[draft.failure])
        end
    end
end

local function address_valid(value)
    return value and #value <= 45 and value:match("^[0-9a-fA-F.:]+$") and value:find("[.:]")
end

local function port_valid(value)
    return value and #value <= 5 and value:match("^[1-9][0-9]*$") and tonumber(value) <= 65535
end

local function write_field(draft, key, value, state, phase)
    if draft.view then return draft.surface.write(draft.view, key, value) end
    editor.write(draft.handle, state, phase, value)
    return true
end

local function submit_steam_password(draft, state, phase)
    local password = draft.surface.read(draft.view, "password")
    local length = password and utf8.len(password)
    if not length or length == 0 or length > 128 then
        render(draft, state, phase, "steam_password", "Enter the host's password (up to 128 characters).")
        return false, "title_join_form_password_required"
    end
    return queue(draft, state, phase, "direct", password)
end

local function connect(draft, state, phase)
    local address = draft.surface.read(draft.view, "address")
    local port = draft.surface.read(draft.view, "port")
    local password = draft.surface.read(draft.view, "password")
    draft.use_password = draft.surface.checked(draft.view, "use_password") == true
    draft.address, draft.port = address or draft.address, port or draft.port
    if not address_valid(address) or not port_valid(port) or not password or utf8.len(password) > 128 then
        local hint = not address_valid(address) and "Enter a numeric IPv4 or IPv6 address."
            or not port_valid(port) and "Enter a UDP port from 1 to 65535."
            or "Enter a password of up to 128 characters."
        render(draft, state, phase, draft.screen, hint)
        return false, "title_join_form_invalid_endpoint"
    end
    if draft.use_password and password == "" then
        render(draft, state, phase, draft.screen, "Enter the host’s password or clear Use password.")
        return false, "title_join_form_password_required"
    end
    return queue(draft, state, phase, "direct", draft.use_password and password or "")
end

function form.press(draft, action, state, phase)
    local screen = draft.screen
    if action == "back" then
        if draft.steam_port and (screen == "catalog" or screen == "trust_host") then
            return false, "title_join_form_back_to_title"
        end
        if screen == "catalog" then
            draft.import, draft.characters, draft.retry, draft.character, draft.character_name = nil, nil, nil, nil, nil
            render(draft, state, phase, draft.direct and "address" or "invite")
            return true
        elseif screen == "trust_host" then
            draft.host_trust = nil
            render(draft, state, phase, "address")
            return true
        elseif screen == "name" or screen == "confirm" or screen == "profile" then
            render(draft, state, phase, "catalog")
            return true
        end
        return false, "title_join_form_back_to_title"
    end
    if action == "trust" and screen == "trust_host" then
        local trust = draft.host_trust
        local accepted, failure = queue(draft, state, phase, "direct", trust.password, trust.fingerprint)
        if accepted then draft.host_trust = nil end
        return accepted, failure
    end
    if draft.steam_port and (screen == "steam_retry" or screen == "steam_password") then
        if action ~= "connect" then return false, "title_join_form_selection_unavailable" end
        if screen == "steam_password" then return submit_steam_password(draft, state, phase) end
        return queue(draft, state, phase, "direct", "")
    end
    if action == "connect" then
        if draft.view and draft.direct and not draft.steam_port and endpoint_screens[screen] then
            return connect(draft, state, phase)
        end
        if screen ~= "invite" then return false, "title_join_form_selection_unavailable" end
        action = "host"
    elseif action == "confirm" then
        if screen ~= "confirm" and screen ~= "profile" then return false, "title_join_form_selection_unavailable" end
        action = "host"
    elseif action == "create" then
        if screen == "catalog" then
            draft.index = #draft.characters + 1
        elseif screen ~= "name" then
            return false, "title_join_form_not_in_catalog"
        end
        action = "host"
    elseif action == "previous_page" or action == "next_page" then
        if screen ~= "catalog" then return false, "title_join_form_not_in_catalog" end
        local page = (draft.page or 1) + (action == "next_page" and 1 or -1)
        if page < 1 or page > page_count(draft) then return false, "title_join_form_selection_unavailable" end
        draft.page = page
        draft.index = (page - 1) * page_size + 1
        render(draft, state, phase, "catalog")
        return true
    elseif action ~= "host" and action ~= "join" then
        local index = tonumber(action:match("^character:([0-9]+)$"))
        if not index or screen ~= "catalog" or not draft.characters[index]
            or math.ceil(index / page_size) ~= (draft.page or 1) then
            return false, "title_join_form_selection_unavailable"
        end
        draft.index = index
        action = "host"
    end
    if action == "join" then
        if screen == "port" or screen == "password" then
            render(draft, state, phase, "address", nil, draft.address)
            return true
        end
        if screen == "confirm" then
            render(draft, state, phase, "catalog")
            return true
        end
        if screen ~= "catalog" then return false, "title_join_form_not_in_catalog" end
        draft.index = draft.index % (#draft.characters + 1) + 1
        if draft.view then
            draft.page = math.min(math.ceil(draft.index / page_size), page_count(draft))
            render(draft, state, phase, "catalog")
        else
            display.text(draft.widget, catalog_text(draft))
        end
        return true
    end
    if screen == "waiting" then return false, "title_join_form_operation_pending" end
    if screen == "catalog" then
        local choice = draft.characters[draft.index]
        draft.character, draft.character_name = choice and choice.id, choice and choice.name
        render(draft, state, phase, choice and (draft.direct and "confirm" or "profile") or "name")
        return true
    end
    if screen == "confirm" then
        if not token(draft.import) or not character_id(draft.character) then return false, "title_join_form_selection_unavailable" end
        return true, nil, {draft.import, "", draft.character}
    end
    local ok, value
    if draft.view then
        value = draft.surface.read(draft.view, fields[screen])
        ok = value ~= nil
    else
        ok, value = pcall(editor.read, draft.handle, state, phase)
        if not ok and value ~= "title_settings_invalid_text" then return false, "title_join_form_editor_unavailable" end
    end
    if screen == "invite" and ok and #value > 0 then return queue(draft, state, phase, "import", value) end
    if screen == "address" and ok and address_valid(value) then
        draft.address = value
        render(draft, state, phase, "port", nil, draft.port)
        return true
    end
    if screen == "port" and ok and port_valid(value) then
        draft.port = value
        render(draft, state, phase, "password")
        return true
    end
    if screen == "password" and ok and utf8.len(value) <= 128 then
        return queue(draft, state, phase, "direct", value)
    end
    if screen == "name" and ok then
        value = value:match("^ *(.-) *$")
        if #value > 0 and utf8.len(value) <= 64 then
            if draft.direct and draft.view then
                if draft.retry and draft.retry.name == value and draft.retry.appearance then
                    return queue(draft, state, phase, "create", value, draft.retry.appearance)
                end
                return true, nil, nil, value
            end
            return queue(draft, state, phase, "create", value)
        end
    end
    if screen == "profile" and ok and (value == "" or value:match("^[A-Za-z0-9][A-Za-z0-9_-]*$")) then
        if not token(draft.import) or not character_id(draft.character) then return false, "title_join_form_selection_unavailable" end
        return true, nil, {draft.import, value, draft.character}
    end
    local hint = screen == "invite" and "Enter a one-line invitation."
        or screen == "address" and messages.invalid_endpoint
        or screen == "port" and "Enter a UDP port from 1 to 65535."
        or screen == "password" and "Enter up to 128 characters."
        or screen == "name" and "Enter a name of up to 64 characters."
        or "Use letters, numbers, _ or -."
    render(draft, state, phase, screen, hint, ok and value or nil)
    return false, "title_join_form_invalid_" .. screen
end

function form.fixture(draft, operation, value, state, phase)
    if operation == "observe" then
        local suffix = draft.screen == "catalog" and ";count=" .. #draft.characters or ""
        if draft.failure then suffix = suffix .. ";error=" .. draft.failure end
        return "title_join_form_state=" .. draft.screen .. suffix
    end
    if operation == "import" and draft.screen == "invite" then
        local data = read_file(draft.fixture, max_invite + 2)
        local text = data and data:gsub("\r?\n$", "")
        if not single_line(text, max_invite) or text == "" then return false, "title_join_form_fixture_invite_unavailable" end
        write_field(draft, "invite", text, state, phase)
        return form.press(draft, "host", state, phase)
    end
    if draft.direct and (operation == "address" or operation == "port") and draft.screen == operation then
        if not write_field(draft, operation, value, state, phase) then return false, "title_join_form_fixture_arguments" end
        return form.press(draft, "host", state, phase)
    end
    if draft.direct and (draft.screen == "password" or draft.screen == "steam_password") then
        local passwords = {password = "Fixture co-op pass", wrong_password = "Wrong fixture", no_password = ""}
        if passwords[operation] then
            if draft.view and not draft.steam_port then
                draft.surface.set_checked(draft.view, "use_password", operation ~= "no_password")
            end
            write_field(draft, "password", passwords[operation], state, phase)
            return form.press(draft, draft.view and "connect" or "host", state, phase)
        end
    end
    if operation == "select" and draft.screen == "catalog" and character_id(value) then
        for index, choice in ipairs(draft.characters) do
            if choice.id == value then
                draft.index = index
                return form.press(draft, "host", state, phase)
            end
        end
        return false, "title_join_form_fixture_character_unavailable"
    end
    if (operation == "create" or operation == "create-editor") and draft.screen == "catalog"
        and #value <= 64 and value:match("^[A-Za-z0-9][A-Za-z0-9 _-]*$") then
        draft.index = #draft.characters + 1
        form.press(draft, "host", state, phase)
        write_field(draft, "character", value, state, phase)
        if operation == "create-editor" then return true end
        return queue(draft, state, phase, "create", value)
    end
    if operation == "profile" and draft.screen == "profile" and #value <= 64
        and value:match("^[A-Za-z0-9][A-Za-z0-9_-]*$") then
        return write_field(draft, "profile", value, state, phase)
    end
    return false, "title_join_form_fixture_arguments"
end

function form.click_fixture(draft, key)
    return draft.view and draft.surface.press_fixture(draft.view, key)
end

function form.sync(draft)
    if draft.view then draft.surface.sync(draft.view) end
end

return form
