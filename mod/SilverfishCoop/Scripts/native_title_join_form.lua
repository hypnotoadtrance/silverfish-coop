local form = {}
local editor = require("native_title_settings_widget")
local display = require("native_title_display")
local max_invite, max_reply, max_characters = 32768, 20480, 64
local messages = {
    invalid_request = "The Join request was invalid.",
    invalid_invite = "This invitation is invalid for this build.",
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
    local generation, id, status, value, text_count =
        header:match("^1\t([0-9a-f]+)\t([0-9a-f]+)\t([a-z]+)\t([0-9a-z_]+)\t([0-9]+)$")
    if generation ~= draft.generation or id ~= draft.pending.id then return end
    if status == "error" and messages[value] and text_count == "0" and body == "" then
        return {error = value}
    end
    local count = text_count and #text_count <= 2 and tonumber(text_count)
    if status ~= "ok" or not token(value) or not count or count > max_characters
        or tostring(count) ~= text_count or (draft.pending.kind == "create" and value ~= draft.import) then return end
    local characters, seen = {}, {}
    local offset = 1
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
    return {import = value, characters = characters}
end

function form.remove(draft)
    if draft.handle then
        editor.remove(draft.handle)
        draft.handle = nil
    end
    if draft.widget then
        display.remove(draft.widget)
        draft.widget = nil
    end
end

local function catalog_text(draft)
    local choice = draft.characters[draft.index]
    if not choice then return "Create a character\nF7 Create   F8 Next   F9 Back" end
    local end_at = utf8.offset(choice.name, 32)
    local name = end_at and choice.name:sub(1, end_at - 1) .. "…" or choice.name
    return "Choose a character\n" .. name .. "\nF7 Select   F8 Next   F9 Back"
end

local function render(draft, state, phase, next_screen, hint, initial)
    form.remove(draft)
    draft.screen = next_screen
    local title_text, editor_field
    if next_screen == "invite" then
        title_text = "Join co-op\n" .. (hint or "Paste the host's invitation text.") .. "\nF7 Load   F9 Back"
        editor_field = "invite"
    elseif next_screen == "name" then
        title_text = "Create a character\n" .. (hint or "Enter a character name.") .. "\nF7 Create   F9 Back"
        editor_field = "character"
    elseif next_screen == "profile" then
        title_text = "Local profile\n" .. (hint or "Blank keeps your configured profile.") .. "\nF7 Join   F9 Back"
        editor_field = "profile"
    else
        title_text = next_screen == "catalog" and catalog_text(draft) or "Waiting for the host…\nF9 Back"
    end
    if editor_field then
        draft.handle = editor.mount(state, phase, editor_field, title_text, initial)
        editor.focus(draft.handle, state, phase)
    else
        draft.widget = display.mount(draft.context, title_text)
    end
end

function form.open(state, phase, context, bridge, generation, new_id)
    local draft = {context = context, generation = generation, new_id = new_id,
        request = bridge .. "/title-join-request.tsv", response = bridge .. "/title-join-response.tsv",
        fixture = bridge .. "/title-join-fixture.invite.json"}
    local ok, failure = pcall(render, draft, state, phase, "invite")
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

local function queue(draft, state, phase, kind, value)
    local retried = kind == "create" and draft.retry and draft.retry.name == value and draft.retry.import == draft.import
    local id = retried and draft.retry.id or draft.new_id()
    if not token(id) then return false, "title_join_form_request_id_unavailable" end
    local payload = kind == "import" and (tostring(#value) .. "\n" .. value) or (draft.import .. "\t" .. value .. "\n")
    if not publish(draft, id, kind, payload) then
        render(draft, state, phase, draft.screen, "Could not send. Press F7 to retry.", value)
        return false, "title_join_form_request_unavailable"
    end
    if kind == "create" then draft.retry = {name = value, import = draft.import, id = id} end
    draft.pending = {kind = kind, id = id, expires = os.time() + 20}
    draft.failure = nil
    render(draft, state, phase, "waiting")
    return true
end

function form.poll(draft, state, phase)
    local pending = draft.pending
    if not pending then return end
    local result = read_response(draft)
    if not result and os.time() <= pending.expires then return end
    draft.pending = nil
    if result and result.characters then
        draft.import, draft.characters, draft.index = result.import, result.characters, 1
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
        render(draft, state, phase, "invite", messages[draft.failure])
    end
end

function form.press(draft, action, state, phase)
    local screen = draft.screen
    if action == "join" then
        if screen ~= "catalog" then return false, "title_join_form_not_in_catalog" end
        draft.index = draft.index % (#draft.characters + 1) + 1
        display.text(draft.widget, catalog_text(draft))
        return true
    end
    if screen == "waiting" then return false, "title_join_form_operation_pending" end
    if screen == "catalog" then
        local choice = draft.characters[draft.index]
        draft.character = choice and choice.id
        render(draft, state, phase, choice and "profile" or "name")
        return true
    end
    local ok, value = pcall(editor.read, draft.handle, state, phase)
    if not ok and value ~= "title_settings_invalid_text" then return false, "title_join_form_editor_unavailable" end
    if screen == "invite" and ok and #value > 0 then return queue(draft, state, phase, "import", value) end
    if screen == "name" and ok then
        value = value:match("^ *(.-) *$")
        if #value > 0 and utf8.len(value) <= 64 then return queue(draft, state, phase, "create", value) end
    end
    if screen == "profile" and ok and (value == "" or value:match("^[A-Za-z0-9][A-Za-z0-9_-]*$")) then
        if not token(draft.import) or not character_id(draft.character) then return false, "title_join_form_selection_unavailable" end
        return true, nil, {draft.import, value, draft.character}
    end
    local hint = screen == "invite" and "Enter a one-line invitation."
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
        editor.write(draft.handle, state, phase, text)
        return form.press(draft, "host", state, phase)
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
    if operation == "create" and draft.screen == "catalog" and #value <= 64 and value:match("^[A-Za-z0-9][A-Za-z0-9 _-]*$") then
        draft.index = #draft.characters + 1
        form.press(draft, "host", state, phase)
        editor.write(draft.handle, state, phase, value)
        return form.press(draft, "host", state, phase)
    end
    if operation == "profile" and draft.screen == "profile" and #value <= 64
        and value:match("^[A-Za-z0-9][A-Za-z0-9_-]*$") then
        editor.write(draft.handle, state, phase, value)
        return true
    end
    return false, "title_join_form_fixture_arguments"
end

return form
