local manager = {}
local page_size = 4

local function button(key, label, detail)
    return {key = key, label = label, detail = detail}
end

local function clip(value, limit)
    local offset = utf8.offset(value, limit + 1)
    return offset and value:sub(1, offset - 1) .. "…" or value
end

local function save_label(entry)
    return entry.name:match("^campaign%-[0-9a-f]+$") and entry.area or entry.name
end

local function page_rows(rows, entries, first, row_for)
    for index = first, math.min(#entries, first + page_size - 1) do
        rows[#rows + 1] = {row_for(entries[index])}
    end
    local navigation = {}
    if first > 1 then navigation[#navigation + 1] = button("previous", "Previous") end
    if first + page_size <= #entries then navigation[#navigation + 1] = button("next", "Next") end
    if #navigation > 0 then rows[#rows + 1] = navigation end
end

local function draw(state, note, focus)
    local rows, heading = {}, "Manage Saves"
    local save = state.save
    if state.screen == "list" then
        if not state.saves then
            note = note or "Loading co-op saves…"
        elseif #state.saves == 0 then
            note = note or "No co-op saves."
        else
            page_rows(rows, state.saves, state.first, function(entry)
                local detail = entry.deleted and "Previously deleted" or entry.timestamp
                return button("save:" .. entry.id, clip(save_label(entry), 25), detail)
            end)
        end
        rows[#rows + 1] = {button("back", "Back")}
    elseif state.screen == "save" then
        heading = clip(save_label(save), 32)
        note = note or (save.deleted and "Deleted co-op campaign" or save.area .. "  ·  " .. save.timestamp)
        if not save.deleted then rows[#rows + 1] = {button("characters", "Manage Characters")} end
        rows[#rows + 1] = {button("delete-save", "Delete Save")}
        rows[#rows + 1] = {button("back", "Back")}
    elseif state.screen == "characters" then
        heading = "Manage Characters"
        if #state.characters == 0 then
            note = (note and note .. " " or "") .. "No guest characters in this save."
        else
            note = note or clip(save_label(save), 32)
        end
        page_rows(rows, state.characters, state.character_first, function(character)
            local row = button("character:" .. character.id, clip(character.name, 25), "Created by " .. clip(character.creator, 20))
            row.photo = character.photo
            return row
        end)
        rows[#rows + 1] = {button("back", "Back")}
    elseif state.screen == "character" then
        local character = state.character
        heading = clip(character.name, 32)
        note = note or "Created by " .. clip(character.creator, 40)
        rows[1] = {button("delete-character", "Delete Character")}
        rows[2] = {button("back", "Back")}
    elseif state.screen == "confirm" then
        local character = state.confirm == "delete-character"
        heading = character and "Delete Character?" or "Delete Save?"
        note = (character and clip(state.character.name, 32) .. " and their progress will be permanently deleted."
            or clip(save_label(save), 32) .. " and all of its characters will be permanently deleted.")
            .. " This cannot be undone."
        rows = {{button("back", "Cancel"), button("confirm", character and "Delete Character" or "Delete Save")}}
    else
        note = note or "Updating co-op saves…"
    end
    state.present({title = heading, note = note, rows = rows, focus = focus})
end

local function open_list(state, note, selected_id, entries)
    state.screen, state.save, state.character = "list", nil, nil
    state.saves = entries or state.catalog()
    state.list_return = not state.saves and {note = note, selected_id = selected_id} or nil
    if selected_id then
        for index, entry in ipairs(state.saves or {}) do
            if entry.id == selected_id then
                state.first = (index - 1) // page_size * page_size + 1
                break
            end
        end
    end
    if state.saves and state.first > #state.saves then
        state.first = math.max(1, (#state.saves - 1) // page_size * page_size + 1)
    end
    draw(state, note, selected_id and "save:" .. selected_id)
end

local function request(state, action)
    local id = state.new_id()
    if not id or not state.save then return false end
    local pending = io.open(state.request, "rb")
    if pending then pending:close(); return false end
    local tmp = state.request .. "." .. id .. ".tmp"
    local file = io.open(tmp, "wb")
    if not file then return false end
    local character = action:match("%-character$") and state.character.id or "-"
    local fields = {"1", state.generation, id, action, state.save.name, state.save.id, state.save.revision, character}
    local written = file:write(table.concat(fields, "\t"), "\n")
    local closed = file:close()
    if not written or not closed or not os.rename(tmp, state.request) then
        os.remove(tmp)
        return false
    end
    state.pending = {id = id, action = action, until_at = os.time() + 20}
    state.screen = "waiting"
    local note = state.character_refresh and "Character deleted. Refreshing guest characters…"
        or action == "characters" and "Loading guest characters…"
    draw(state, note)
    return true
end

local function start_request(state, action)
    if not request(state, action) then draw(state, "The save request could not be submitted. Try again.") end
end

local function find(entries, key)
    for index, entry in ipairs(entries) do
        if entry.id == key then return entry, index end
    end
end

function manager.open(bridge, generation, new_id, catalog, present)
    local state = {generation = generation, new_id = new_id, catalog = catalog, present = present,
        request = bridge .. "/title-save-request.tsv", response = bridge .. "/title-save-response.tsv",
        first = 1, character_first = 1}
    open_list(state)
    return state
end

function manager.press(state, action)
    local screen = state.screen
    if screen == "waiting" then return end
    local focus
    if action == "back" then
        if screen == "list" then return "leave" end
        if screen == "save" then
            open_list(state, nil, state.save.id)
            return
        elseif screen == "characters" then
            state.screen, focus = "save", "characters"
        elseif screen == "character" then
            state.screen, focus = "characters", "character:" .. state.character.id
        elseif screen == "confirm" then
            state.screen, focus = state.confirm == "delete-save" and "save" or "character", "back"
        end
    elseif (action == "previous" or action == "next") and (screen == "list" or screen == "characters") then
        local list = screen == "list"
        local entries = list and state.saves or state.characters
        local field = list and "first" or "character_first"
        local index = state[field] + (action == "next" and page_size or -page_size)
        if entries and index >= 1 and index <= #entries then state[field] = index end
    elseif screen == "list" then
        local entry = find(state.saves or {}, action:match("^save:(.+)$"))
        if not entry then return end
        state.save, state.screen, state.character_first = entry, "save", 1
    elseif screen == "save" and action == "characters" and not state.save.deleted then
        start_request(state, action)
        return
    elseif screen == "characters" then
        local entry = find(state.characters, action:match("^character:(.+)$"))
        if not entry then return end
        state.character, state.screen = entry, "character"
    elseif screen == "save" and action == "delete-save" or screen == "character" and action == "delete-character" then
        state.confirm, state.screen = action, "confirm"
        focus = "back"
    elseif screen == "confirm" and action == "confirm" then
        start_request(state, state.confirm)
        return
    else
        return
    end
    draw(state, nil, focus)
end

local function response(state)
    local file = io.open(state.response, "rb")
    if not file then return end
    local prefix = file:read(128) or ""
    local generation, id, status, value, first = prefix:match("^2\t([0-9a-f]+)\t([0-9a-f]+)\t([a-z]+)\t([a-z0-9]+)\n()")
    if generation ~= state.generation or id ~= state.pending.id then file:close(); return end
    local limit = 2300000
    local data = prefix .. (file:read(limit - #prefix + 1) or "")
    file:close()
    if #data > limit or data:sub(-1) ~= "\n" then return end
    if status == "error" then return nil, value end
    local count = tonumber(value)
    if status ~= "ok" or not count or count > 256 then return end
    local entries, accounts, photos = {}, {}, {}
    for _ = 1, count do
        local ending = data:find("\n", first, true)
        if not ending then return end
        local key, name, creator, account = data:sub(first, ending - 1):match("^([0-9a-f%-]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)$")
        if not key or #key ~= 36 or not utf8.len(name) or name:find("%c") or not utf8.len(creator) or creator:find("%c")
            or (account ~= "-" and (#account > 20 or not account:match("^[1-9][0-9]*$"))) then return end
        entries[#entries + 1] = {id = key, name = name, creator = creator, account = account}
        accounts[account] = true
        first = ending + 1
    end
    while first <= #data do
        local ending = data:find("\n", first, true)
        if not ending then return end
        local account, hex = data:sub(first, ending - 1):match("^([0-9]+)\t([0-9a-f]+)$")
        if account and accounts[account] and not photos[account] and #hex <= 8500 then
            photos[account] = hex
        end
        first = ending + 1
    end
    for _, entry in ipairs(entries) do entry.photo = photos[entry.account] end
    return entries
end

function manager.poll(state)
    if state.list_return then
        local entries = state.catalog()
        local previous = state.list_return
        if entries then open_list(state, previous.note, previous.selected_id, entries) end
        return
    end
    local pending, refresh = state.pending, state.character_refresh
    if not pending then
        if not refresh then return end
        local saves = state.catalog()
        local save = saves and find(saves, state.save.id)
        if save and not save.deleted and tonumber(save.revision) > refresh.revision then
            state.save = save
            if request(state, "characters") then return end
        end
        if os.time() > refresh.until_at or saves and (not save or save.deleted) then
            state.character_refresh = nil
            open_list(state, "Character deleted. Reopen the save to refresh the list.", state.save.id, saves)
        end
        return
    end
    local entries, failure = response(state)
    if not entries and not failure and os.time() <= pending.until_at then return end
    state.pending, state.character_refresh = nil, nil
    if failure or not entries then
        local note = refresh and "Character deleted. Reopen the save to refresh the list."
            or failure == "busy" and "Close the hosted campaign before managing this save."
            or "Could not complete the save request. Reopen the save and try again."
        open_list(state, note, state.save.id)
    elseif pending.action == "characters" then
        state.characters, state.screen = entries, "characters"
        if refresh then
            local index = math.min(refresh.index, #entries)
            state.character_first = index > 0 and (index - 1) // page_size * page_size + 1 or 1
            draw(state, "Character deleted.", index > 0 and "character:" .. entries[index].id or "back")
        else
            draw(state)
        end
    elseif pending.action == "delete-character" then
        local _, index = find(state.characters, state.character.id)
        state.character_refresh = {revision = tonumber(state.save.revision), index = index or 1, until_at = os.time() + 20}
        draw(state, "Character deleted. Refreshing guest characters…")
    else
        open_list(state, "Save deleted.", state.save.id)
    end
end

return manager
