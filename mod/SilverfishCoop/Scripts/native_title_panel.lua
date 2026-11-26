local panel = {}
local title = require("native_title_entry")
local surface = require("native_title_surface")
local controls = require("native_menu_controls")
local resident = os.getenv("SILVERFISH_COOP_RESIDENT_TITLE") == "1"
local direct_join = os.getenv("SILVERFISH_COOP_TITLE_DIRECT_JOIN") == "1"

local actions = {
    ["host"] = {host = true}, ["join"] = {join = true}, ["host,join"] = {host = true, join = true},
}
local bridge_path, session_path, request_path, loop, current, session, report
local steam_join_path, steam_cancel_path, steam_opened_path, last_steam_invite, steam_polled_at
local steam_handoff, steam_cancel_retry
local dismissed, submitted
local singleplayer_title
local guest_creation
local dispatch
local settings_fixture, credits_fixture
local credits_path = "/Game/Classes/GUI/Credits_MainMenu_Widget.Credits_MainMenu_Widget_C"
local initial_port, edited_port = "7777", "28181"
local steam_access_labels = {invite = "Invite Only", friends = "Friends Only", public = "Public"}

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
    local contents = file:read(257)
    file:close()
    if not contents or #contents > 256 then return end
    local generation, choices, timestamp =
        contents:match("^1\t([0-9a-f]+)\t([a-z,%-]+)\t([0-9]+)\r?\n$")
    local defaults
    if not generation then
        local campaign, bind, port
        generation, choices, timestamp, campaign, bind, port = contents:match(
            "^2\t([0-9a-f]+)\t([a-z,%-]+)\t([0-9]+)\t([A-Za-z0-9][A-Za-z0-9_-]*)\t([A-Fa-f0-9:.]+)\t([1-9][0-9]*)\r?\n$")
        if not generation or #campaign > 64 or #bind > 45 or #port > 5
            or tonumber(port) > 65535 or (choices ~= "host" and choices ~= "host,join") then return end
        defaults = {campaign = campaign, bind = bind, port = port}
    end
    local expires = timestamp and #timestamp <= 10 and math.tointeger(tonumber(timestamp))
    local now = os.time()
    if not generation or #generation ~= 32 or not actions[choices] or not expires
        or expires < now or expires > now + 10 then return end
    return {generation = generation, choices = choices, expires = expires, defaults = defaults}
end

local function read_campaigns(generation)
    local file = io.open(bridge_path .. "/title-host-campaigns.tsv", "rb")
    if not file then return end
    local data = file:read(12289)
    file:close()
    if not data or #data > 12288 then return end
    local version, offered, size, start = data:match("^([12])\t([0-9a-f]+)\t([0-9][0-9]?)\r?\n()")
    if offered ~= generation then return end
    local count = tonumber(size)
    if not count or count > 64 then return end
    local campaigns, seen = {}, {}
    for _ = 1, count do
        local ending = data:find("\n", start, true)
        if not ending then return end
        local row = data:sub(start, ending - 1):gsub("\r$", "")
        local name, area, timestamp
        if version == "1" then
            name = row:match("^[A-Za-z0-9][A-Za-z0-9_-]*$")
            area = name
        else
            name, area, timestamp = row:match(
                "^([A-Za-z0-9][A-Za-z0-9_-]*)\t([ -~]+)\t(%d%d%d%d%-%d%d%-%d%d %d%d:%d%d:%d%d)$")
        end
        if not name or #name > 64 or #area > 64 or seen[name:lower()] then return end
        seen[name:lower()] = true
        campaigns[#campaigns + 1] = {name = name, area = area, timestamp = timestamp}
        start = ending + 1
    end
    if start == #data + 1 then return campaigns end
end

local function publish_steam_join_state(path, nonce, action)
    local temporary = path .. "." .. nonce .. ".tmp"
    local file = io.open(temporary, "wb")
    if not file then return end
    local written = file:write("1\t", nonce, "\t", action, "\n")
    local closed = file:close()
    if written and closed and os.rename(temporary, path) then return true end
    os.remove(temporary)
end

local function cancel_steam_form(nonce)
    if nonce and not publish_steam_join_state(steam_cancel_path, nonce, "cancel") then
        steam_cancel_retry = {nonce = nonce, at = os.time() + 1, attempts = 3}
    end
end

local function close_form(owner, preserve_route)
    local form = owner.form
    owner.form = nil
    if form and form.role == "join" then
        if not preserve_route then cancel_steam_form(form.steam_nonce) end
        form.api.remove(form.draft)
    end
end

local function show_stock(owner, shown)
    if valid(owner.root) then owner.root:SetVisibility(shown and owner.stock_visibility or 2) end
end

local function remove_display(preserve_route)
    local old = session
    session = nil
    if old then
        close_form(old, preserve_route)
        surface.remove(old.view)
        controls.remove(old.solo_back)
        show_stock(old, true)
    end
end

local function present(spec)
    local owner = session
    surface.remove(owner.view)
    owner.view = nil
    if spec then
        owner.view = surface.mount(owner.context, spec, function(action)
            if session == owner then dispatch(action) end
        end)
    end
end

local function button(key, label, detail)
    return {key = key, label = label, detail = detail}
end

local function steam_available()
    local file = io.open(bridge_path .. "/steam-coop-available", "rb")
    if not file then return false end
    local available = file:read(3) == "1\n"
    file:close()
    return available
end

local function multiplayer(offer)
    session.browser = nil
    local choices, rows = actions[offer.choices], {}
    if choices.host then rows[#rows + 1] = {button("host", "Host")} end
    if choices.join then rows[#rows + 1] = {button("join", direct_join and "Join by Direct IP" or "Join")} end
    if choices.join and direct_join and steam_available() then
        rows[#rows + 1] = {button("steam-public", "Public Steam Lobbies")}
    end
    rows[#rows + 1] = {button("modes", "Back")}
    present({title = "Multiplayer", rows = rows})
end

local function modes(owner)
    controls.remove(owner.solo_back)
    owner.solo_back = nil
    show_stock(owner, false)
    present({title = "Project Silverfish", rows = {
        {button("singleplayer", "Singleplayer")},
        {button("multiplayer", "Multiplayer")},
        {button("quit", "Quit")},
    }})
end

local function singleplayer(owner)
    title.refresh_singleplayer(owner.context)
    present(nil)
    show_stock(owner, true)
    owner.solo_back = controls.create(owner.context, owner.parent, "Back to modes", function()
        if session == owner then dispatch("modes") end
    end)
end

local function mount(context, offer)
    local menu = context.menu
    local parent = valid(menu.Button_NewGame) and menu.Button_NewGame:GetParent()
    if not valid(parent) or not same(menu.Button_SETTINGS:GetParent(), parent)
        or not same(menu.Button_QUIT:GetParent(), parent) then error("title_panel_stock_menu_unavailable", 0) end
    local tree = menu.WidgetTree
    local root = valid(tree) and tree.RootWidget
    if not valid(root) or not same(root:GetOuter(), tree) then error("title_panel_stock_menu_unavailable", 0) end
    local owner = {context = context, offer = offer, parent = parent, root = root,
        stock_visibility = root:GetVisibility()}
    session = owner
    if same_title(singleplayer_title, context) then
        singleplayer(owner)
    else
        singleplayer_title = nil
        modes(owner)
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

local function write_request(generation, action, form_values, friendly_fire)
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
    local version = "2"
    if action == "host" and form_values then
        version = steam_access_labels[form_values[2]] and "6"
            or ({creator = "4", resume = "5"})[form_values[5]] or (#form_values == 4 and "3" or "2")
    end
    local row = form_values and {version, generation, id, action, table.unpack(form_values)}
        or {"1", generation, id, action}
    if friendly_fire then row[#row + 1] = "1" end
    local written = file:write(table.concat(row, "\t"), "\n")
    local closed = file:close()
    -- Windows rename refuses an existing destination. Never remove or replace a pending request.
    if written and closed and os.rename(temporary, request_path) then return true end
    os.remove(temporary)
    return false
end

local function can_choose(context)
    return session and same_title(context, session.context) and os.time() <= session.offer.expires
        and valid(session.root)
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

local function choose(action, context, form_values, friendly_fire)
    if not can_choose(context) then return false, "title_panel_not_ready" end
    if action == "hide" then
        dismissed = session.offer.generation
        report = {state = "hidden", choices = session.offer.choices}
        remove_display()
        return true
    end
    local latest, reason = active_offer(action, context)
    if not latest then return false, reason end
    if write_request(latest.generation, action, form_values, friendly_fire) then
        submitted = latest.generation
        if action == "join" and session.form and session.form.steam_nonce then
            steam_handoff = {nonce = session.form.steam_nonce, generation = latest.generation}
        end
        close_form(session, action == "join")
        report = {state = "pending", choices = latest.choices}
        present({title = "Multiplayer", note = action == "host" and "Starting the campaign…" or "Joining the host…",
            rows = {}})
        return true
    end
    close_form(session)
    report = {state = "unavailable", choices = latest.choices, reason = "title_request_write_failed"}
    present({title = "Multiplayer", note = "Could not start co-op. Return to the title and try again.",
        rows = {{button("modes", "Back to title")}}})
    return false, report.reason
end

local function host_menu()
    session.form.screen = "menu"
    present({title = "Host a campaign", rows = {
        {button("new", "New Game"), button("browse", "Load Game")},
        {button("back", "Back")},
    }})
end

local function host_settings(form, note)
    form.screen = "settings"
    local rows = form.api.rows(form.values, resident, form.fixture and not form.saved_name)
    if resident and not form.fixture then
        local access = form.values.steam_access
        table.insert(rows, 1, {button("network", access and "Steam · " .. steam_access_labels[access] or "Network · Direct IP")})
    end
    local label = form.saved_name and "Load Game" or "Start Game"
    rows[#rows + 1] = {button("submit", label), button("back", "Back")}
    local title_text = form.saved_name and "Load Game" or "New Game"
    present({title = title_text, note = note or form.saved_display, rows = rows})
end

local function host_network(form, note)
    form.screen = "network"
    present({title = "Host networking", note = note, rows = {
        {button("direct", "Direct IP"), button("steam", "Steam Networking")},
        {button("back", "Back")},
    }})
end

local function host_steam_access(form)
    if not steam_available() then
        host_network(form, "Steam networking is unavailable. Check Steam and try again.")
        return
    end
    form.screen = "steam-access"
    present({title = "Steam lobby access", rows = {
        {button("invite", steam_access_labels.invite, "Join from a Steam invite")},
        {button("friends", steam_access_labels.friends, "Friends can use Join Game")},
        {button("public", steam_access_labels.public, "Listed in the lobby browser")},
        {button("back", "Back")},
    }})
end

local function show_saved(form)
    form.screen = "saved"
    local saved, rows, note = form.saved, {}, nil
    if not saved then
        note = "The campaign list is unavailable."
        rows[1] = {button("browse", "Retry")}
    elseif #saved == 0 then
        note = "No saved co-op campaigns"
    else
        local first = form.first or 1
        for index = first, math.min(first + 3, #saved) do
            local row = saved[index]
            local area = row.timestamp and #row.area > 19 and row.area:sub(1, 18):gsub(" +$", "") .. "…" or row.area
            rows[#rows + 1] = {button("load:" .. row.name, area, row.timestamp)}
        end
        local pages = {}
        if first > 1 then pages[#pages + 1] = button("previous", "Previous") end
        if first + 3 < #saved then pages[#pages + 1] = button("next", "Next") end
        if #pages > 0 then rows[#rows + 1] = pages end
    end
    rows[#rows + 1] = {button("back", "Back")}
    present({title = "Load co-op campaign", note = note, rows = rows})
end

local function handle_host_form(action, context)
    local offer, reason = active_offer("host", context)
    if not offer then return false, reason end
    local form = session.form
    if not form then
        if action ~= "host" and action ~= "fixture" then return false, "title_host_form_unavailable" end
        local defaults = offer.defaults or {}
        form = {role = "host", api = require("native_title_host_form"), fixture = action == "fixture",
            values = {campaign = defaults.campaign, bind = defaults.bind, port = defaults.port}}
        session.form = form
        if resident and not form.fixture then host_menu() else host_settings(form) end
        report = {state = "ready", choices = offer.choices}
        return true
    end
    if action == "back" then
        if form.screen == "steam-access" then host_network(form)
        elseif form.screen == "network" then host_settings(form)
        elseif form.fixture and form.screen == "saved" then host_settings(form)
        elseif form.fixture or form.screen == "menu" or not resident then
            close_form(session)
            multiplayer(offer)
        elseif form.screen == "settings" and form.saved_name then show_saved(form)
        else host_menu() end
        return true
    end
    if form.screen == "settings" and action == "network" and resident and not form.fixture then
        form.api.selection(session.view, form.values, resident, form.saved_name, false)
        host_network(form)
        return true
    end
    if form.screen == "network" then
        if action == "steam" then host_steam_access(form)
        elseif action == "direct" then
            form.values.steam_access = nil
            host_settings(form)
        else return false, "title_host_form_unavailable" end
        return true
    end
    if form.screen == "steam-access" and steam_access_labels[action] then
        form.values.steam_access = action
        host_settings(form)
        return true
    end
    if action == "new" and form.screen == "menu" then
        form.saved_name, form.saved_display = nil, nil
        host_settings(form)
        return true
    end
    if action == "browse" and resident then
        form.saved, form.first, form.index = read_campaigns(offer.generation), 1, 1
        show_saved(form)
        return true
    end
    if form.screen == "saved" and form.saved then
        local name = action:match("^load:(.+)$")
        if name and session.view.buttons[action] then
            for _, save in ipairs(form.saved) do
                if save.name == name then
                    form.saved_name = name
                    form.saved_display = save.timestamp and save.area .. "  /  " .. save.timestamp or save.area
                    host_settings(form)
                    return true
                end
            end
        end
        if action == "next" or action == "previous" then
            local first = form.first + (action == "next" and 4 or -4)
            if first >= 1 and first <= #form.saved then form.first = first end
            show_saved(form)
            return true
        end
    end
    if form.screen ~= "settings" or action ~= "submit" and action ~= "creator" then
        return false, "title_host_form_unavailable"
    end
    local values, failure, hint = form.api.selection(session.view, form.values, resident, form.saved_name,
        form.fixture and not form.saved_name)
    if not values then
        host_settings(form, hint)
        return false, failure
    end
    if form.values.steam_access then values[4] = form.saved_name and "resume" or "creator"
    elseif form.saved_name then values[5] = "resume"
    elseif resident and (action == "creator" or not form.fixture) then values[5] = "creator" end
    return choose("host", context, values, form.values.friendly_fire)
end

local function handle_join_form(action, state, phase, context, steam_invite)
    local offer, reason = active_offer("join", context)
    if not offer then return false, reason end
    local form = session.form
    if not form then
        if action ~= "join" then return false, "title_join_form_unavailable" end
        local api = require("native_title_join_form")
        present(nil)
        local owner = session
        local draft = api.open(state, phase, context, bridge_path, offer.generation, request_id, direct_join,
            function(key) if session == owner then dispatch(key) end end, offer.defaults, steam_invite and steam_invite.port)
        session.form = {role = "join", api = api, draft = draft, steam_nonce = steam_invite and steam_invite.nonce}
        report = {state = "ready", choices = offer.choices}
        return true
    end
    if form.role ~= "join" then return false, "title_join_form_unavailable" end
    local accepted, failure, values, name = form.api.press(form.draft, action, state, phase)
    if values then return choose("join", title.probe(state, phase), values) end
    if name then
        local file = io.open(bridge_path .. "/title-join-creator-scope.txt", "rb")
        local contents = file and file:read(34)
        if file then file:close() end
        local scope_id = contents and contents:match("^([0-9a-f]+)\n$")
        local attempt = request_id()
        if not scope_id or #scope_id ~= 32 or not attempt then
            return form.api.resume_creation(form.draft, state, phase, name, nil, "Character creation is unavailable.")
        end
        local scope = require("native_save_scope")
        local creator = require("native_stock_creator")
        local opened = pcall(function()
            scope.select(scope_id)
            creator.begin_guest(state, phase, attempt, name)
        end)
        if not opened then
            if scope.selected() == scope_id and not creator.guest_busy() then scope.release(scope_id) end
            return form.api.resume_creation(form.draft, state, phase, name, nil, "Could not open character creation.")
        end
        guest_creation = {form = form, name = name, scope = scope_id, attempt = attempt}
        remove_display(true)
        report = nil
        return true
    end
    if failure == "title_join_form_back_to_title" then
        close_form(session)
        multiplayer(offer)
        return true
    end
    return accepted, failure
end

local function poll_steam_invite(context)
    if not direct_join or session.form or submitted == session.offer.generation
        or not actions[session.offer.choices].join then return end
    local now = os.time()
    if steam_polled_at == now then return end
    steam_polled_at = now
    local file = io.open(steam_join_path, "rb")
    if not file then return end
    local record = file:read(81)
    file:close()
    if not record or #record > 80 then return end
    local nonce, port, stamp = record:match("^1\t([0-9a-f]+)\t([1-9][0-9]*)\t([0-9]+)\n$")
    local expiry = stamp and #stamp <= 10 and tonumber(stamp)
    if not nonce or #nonce ~= 32 or not nonce:find("[^0]") or nonce == last_steam_invite
        or #port > 5 or tonumber(port) > 65535 or not expiry or expiry < now or expiry > now + 60 then return end
    if handle_join_form("join", current.state, current.phase, context, {nonce = nonce, port = port}) then
        last_steam_invite = nonce
        session.browser = nil
        controls.remove(session.solo_back)
        session.solo_back = nil
        singleplayer_title = nil
        show_stock(session, false)
    end
end

local function show_public_lobbies(browser)
    local rows, note = {}, nil
    if browser.state == "joining" then
        note = "Connecting through Steam…"
    elseif browser.state == "searching" then
        note = "Looking for public lobbies…"
    elseif browser.state == "unavailable" then
        note = "Steam lobbies are unavailable. Try again."
    elseif #browser.names == 0 then
        note = "No public lobbies found."
    else
        local first = browser.first
        for index = first, math.min(first + 3, #browser.names) do
            rows[#rows + 1] = {button("steam-pick:" .. index, browser.names[index])}
        end
        local pages = {}
        if first > 1 then pages[#pages + 1] = button("steam-prev", "Previous") end
        if first + 3 < #browser.names then pages[#pages + 1] = button("steam-next", "Next") end
        if #pages > 0 then rows[#rows + 1] = pages end
    end
    if browser.state ~= "joining" then
        rows[#rows + 1] = {button("steam-public", "Refresh"), button("steam-back", "Back")}
    end
    present({title = "Public Steam Lobbies", note = note, rows = rows})
end

local function browser_request(browser, action)
    local path = bridge_path .. "/steam-coop-browser-request.tsv"
    local temporary = path .. "." .. browser.token .. ".tmp"
    local file = io.open(temporary, "wb")
    if not file then return false end
    local written = file:write("1\t", browser.generation, "\t", browser.token, "\t", action, "\n")
    local closed = file:close()
    if written and closed and os.rename(temporary, path) then return true end
    os.remove(temporary)
    return false
end

local function browser_action(action)
    local browser = session.browser
    if action == "steam-back" then
        multiplayer(session.offer)
        return
    end
    if action == "steam-public" then
        local token = request_id()
        if not token then return end
        browser = {generation = session.offer.generation, token = token, first = 1,
            state = "searching", names = {}, expires = os.time() + 15}
        session.browser = browser
        if not browser_request(browser, "search") then browser.state = "unavailable" end
    elseif browser and browser.state == "ready" then
        local selected = action:match("^steam%-pick:([1-9][0-9]?)$")
        if selected and session.view.buttons[action] and browser.names[tonumber(selected)] then
            browser.state = browser_request(browser, selected) and "joining" or "unavailable"
            browser.expires = os.time() + 15
        elseif action == "steam-prev" or action == "steam-next" then
            local first = browser.first + (action == "steam-prev" and -4 or 4)
            if first >= 1 and first <= #browser.names then browser.first = first end
        end
    end
    if browser then show_public_lobbies(browser) end
end

local function poll_public_lobbies(browser)
    if browser.state ~= "searching" and browser.state ~= "joining" then return end
    local now = os.time()
    if now > browser.expires then
        browser.state = "unavailable"
        show_public_lobbies(browser)
        return
    end
    if browser.polled == now then return end
    browser.polled = now
    local file = io.open(bridge_path .. "/steam-coop-browser-response.tsv", "rb")
    if not file then return end
    local data = file:read(1501)
    file:close()
    if not data or #data > 1500 then return end
    local generation, token, result, count, start = data:match("^1\t([0-9a-f]+)\t([0-9a-f]+)\t([a-z]+)\t([0-9][0-9]?)\n()")
    if generation ~= browser.generation or token ~= browser.token or not count or tonumber(count) > 12 then return end
    if result == "unavailable" then
        browser.state = "unavailable"
    elseif result == "ready" and browser.state == "searching" then
        local names = {}
        for name in data:sub(start):gmatch("([^\n]+)\n") do
            if #name > 104 or name:find("[%c]") or not utf8.len(name) then return end
            names[#names + 1] = name
        end
        if #names ~= tonumber(count) then return end
        browser.state, browser.names = "ready", names
    else
        return
    end
    show_public_lobbies(browser)
end

local function resume_guest_creation(context, offer, state, phase)
    local trip = guest_creation
    local creator = require("native_stock_creator")
    local result, appearance, profile, name = creator.guest_result(trip.attempt)
    if result ~= "captured" and result ~= "cancelled" and result ~= "failed" then return end
    local scope = require("native_save_scope")
    if scope.selected() == trip.scope then
        scope.release(trip.scope)
        title.refresh_singleplayer(context)
    end
    if not offer or not actions[offer.choices].join then return end
    creator.clear_guest(state, phase, trip.attempt)
    guest_creation = nil
    mount(context, offer)
    local owner = session
    present(nil)
    local form = trip.form
    owner.form = form
    local draft = form.draft
    draft.context, draft.generation = context, offer.generation
    draft.on_action = function(key) if session == owner then dispatch(key) end end
    local hint = result == "failed" and "Could not save the character. Try again." or nil
    local selection = profile and (appearance .. "\n" .. profile) or appearance
    form.api.resume_creation(draft, state, phase, name or trip.name, selection, hint)
    report = {state = "ready", choices = offer.choices}
end

local function fixture_enabled()
    return os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
end

local function stock_profile_fixture(fields, state, phase)
    local inspect_cache = fields[3] == "cache"
    if (#fields ~= 2 and not (#fields == 3 and inspect_cache)) or not fixture_enabled() or not title.probe(state, phase)
        or require("native_save_scope").selected() then error("title_stock_profile_unavailable", 0) end
    local instance = state.world.OwningGameInstance
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(instance) or not valid(statics) then error("title_stock_profile_unavailable", 0) end
    local loaded = statics:LoadGameFromSlot("Config", 0)
    local active = instance.GamePlay_Config
    local config_class = StaticFindObject("/Game/Classes/Global/Settings_Config_BP.Settings_Config_BP_C")
    if not valid(loaded) or not valid(active) or not same(loaded:GetClass(), config_class)
        or not same(active:GetClass(), config_class) then error("title_stock_config_unavailable", 0) end
    local values = {}
    for _, key in ipairs({
        "FOV", "MouseSensitivity", "AdsSensitivity", "InvertXAxis", "InvertYAxis",
        "VolumeMaster", "VolumeSfx", "VolumeMusic", "VolumeSpeech", "Subtitles",
        "VolumetricFog", "ResQuality", "AAMethod", "Gamma",
    }) do
        local value = active[key]
        if (type(value) ~= "number" and type(value) ~= "boolean") or loaded[key] ~= value then
            error("title_stock_config_readback_failed", 0)
        end
        values[#values + 1] = tostring(value)
    end
    local current, listed = instance.SaveSlot_Ref, instance.SaveFileExists
    local recent = {}
    instance:Find_MostRecentSaveFile(recent, recent)
    if not recent.SaveFileFound or not valid(recent.Save) or type(recent.Save.PlayerHealth) ~= "number" then
        error("title_stock_continue_unavailable", 0)
    end
    if inspect_cache and (listed ~= true or not valid(current) or not same(current:GetClass(), recent.Save:GetClass())
        or current.PlayerHealth ~= recent.Save.PlayerHealth) then error("title_stock_continue_cache_stale", 0) end
    values[#values + 1] = tostring(recent.Save.PlayerHealth)
    return table.concat(values, ":")
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
        local accepted, failure = handle_host_form("fixture", context)
        if not accepted then error(failure, 0) end
        return "title_host_form_open"
    end
    local form = session.form
    if not form or form.role ~= "host" then error("title_host_form_unavailable", 0) end
    if operation == "saved-state" then
        if form.screen ~= "saved" then error("title_host_saved_campaign_unavailable", 0) end
        if not form.saved then return "title_host_saved=unavailable" end
        if #form.saved == 0 then return "title_host_saved=empty" end
        return string.format("title_host_saved=%d:%s", #form.saved, form.saved[form.index].name)
    end
    if operation == "campaign" then
        local suffix = value:match("^title%-host%-settings%-([0-9a-f]+)$")
        if not suffix or #suffix ~= 8 or not surface.write(session.view, "campaign", value) then
            error("title_host_form_fixture_arguments", 0)
        end
        form.values.campaign = value
        return "title_host_form_campaign_entered"
    end
    if operation == "port" then
        if #value > 5 or not value:match("^[0-9]+$") or tonumber(value) > 65535
            or (value ~= "0" and value:sub(1, 1) == "0")
            or not surface.write(session.view, "port", value) then
            error("title_host_form_fixture_arguments", 0)
        end
        return "title_host_form_port_entered"
    end
    if operation == "friendly-fire" then
        if surface.checked(session.view, "friendly_fire") ~= false
            or not surface.set_checked(session.view, "friendly_fire", true) then
            error("title_host_form_fixture_arguments", 0)
        end
        return "title_host_form_friendly_fire_enabled"
    end
    if operation == "password" then
        if not surface.set_checked(session.view, "require_password", true)
            or not surface.write(session.view, "password", "Fixture co-op pass") then
            error("title_host_form_fixture_arguments", 0)
        end
        return "title_host_form_password_entered"
    end
    if operation == "next" then
        if form.screen == "saved" and form.saved and #form.saved > 0 then
            form.index = form.index % #form.saved + 1
            form.first = math.floor((form.index - 1) / 4) * 4 + 1
            show_saved(form)
        end
        return "title_host_form_next"
    end
    local action = ({submit = "submit", cancel = "back", creator = "creator", browse = "browse"})[operation]
    if operation == "choose-saved" then
        local save = form.screen == "saved" and form.saved and form.saved[form.index]
        if not save then error("title_host_saved_campaign_unavailable", 0) end
        action = "load:" .. save.name
    end
    if not action then error("title_host_form_fixture_arguments", 0) end
    local accepted, failure = handle_host_form(action, context)
    if not accepted then error(failure, 0) end
    if operation == "browse" or operation == "choose-saved" then return "title_host_form_" .. operation end
    return operation == "cancel" and "title_host_form_cancelled" or "title_host_form_submitted"
end

local function join_form_fixture_command(fields, state, phase)
    if not fixture_enabled() then error("title_join_form_fixture_unavailable", 0) end
    local operation, value = fields[3], fields[4]
    local has_value = operation == "select" or operation == "create" or operation == "create-editor" or operation == "profile"
        or operation == "address" or operation == "port"
    if #fields ~= (has_value and 4 or 3) then error("title_join_form_fixture_arguments", 0) end
    local context = title.probe(state, phase)
    local offer, reason = active_offer("join", context)
    if not offer then error(reason, 0) end
    if operation == "open" then
        if session.form or settings_fixture or credits_fixture then error("title_join_form_already_open", 0) end
        local accepted, failure = handle_join_form("join", state, phase, context)
        if not accepted then error(failure, 0) end
        return "title_join_form_open"
    end
    local form = session.form
    if not form or form.role ~= "join" then error("title_join_form_unavailable", 0) end
    if operation == "submit" or operation == "cancel" then
        if operation == "submit" and form.draft.screen ~= "profile" and form.draft.screen ~= "confirm" then
            error("title_join_form_selection_unavailable", 0)
        end
        local accepted, failure = handle_join_form(operation == "submit" and "host" or "back", state, phase, context)
        if not accepted then error(failure, 0) end
        return operation == "submit" and "title_join_form_submitted" or "title_join_form_cancelled"
    end
    if operation == "observe" then form.api.poll(form.draft, state, phase) end
    local accepted, failure = form.api.fixture(form.draft, operation, value, state, phase)
    if not accepted then error(failure, 0) end
    if operation == "create-editor" then
        local opened, reason = handle_join_form("host", state, phase, context)
        if not opened then error(reason, 0) end
    end
    if operation == "observe" then return accepted end
    return "title_join_form_" .. operation .. "_accepted"
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

local function update_title()
    if not session then return end
    local context = current and title.probe(current.state, current.phase)
    if not can_choose(context) then
        remove_display()
        report = nil
        return
    end
    poll_steam_invite(context)
    if session.browser then poll_public_lobbies(session.browser) end
    surface.sync(session.view)
    local form = session.form
    if form and form.role == "join" then
        local now = os.time()
        if form.steam_nonce and not form.steam_opened and form.steam_opened_at ~= now then
            form.steam_opened_at = now
            form.steam_opened = publish_steam_join_state(steam_opened_path, form.steam_nonce, "opened")
        end
        form.api.sync(form.draft)
        if form.draft.pending and active_offer("join", context) then
            form.api.poll(form.draft, current.state, current.phase)
        end
    end
end

local function unavailable()
    local choices = session and session.offer.choices
    remove_display()
    report = choices and {state = "unavailable", choices = choices, reason = "title_panel_input_unavailable"} or nil
end

dispatch = function(action)
    local ok = pcall(function()
        local context = current and title.probe(current.state, current.phase)
        if not can_choose(context) or submitted == session.offer.generation then return end
        if action == "singleplayer" then
            close_form(session)
            singleplayer_title = session.context
            singleplayer(session)
        elseif action == "modes" then
            close_form(session)
            singleplayer_title = nil
            modes(session)
        elseif action == "multiplayer" then
            close_form(session)
            singleplayer_title = nil
            multiplayer(session.offer)
        elseif action == "quit" then
            local quit = context.menu.Button_QUIT
            if valid(quit) and valid(quit.Button_63) then quit.Button_63.OnPressed:Broadcast() end
        elseif session.browser or not session.form and action == "steam-public" then
            browser_action(action)
        elseif session.form and session.form.role == "join" or not session.form and action == "join" then
            handle_join_form(action, current.state, current.phase, context)
        elseif session.form or action == "host" then
            handle_host_form(action, context)
        end
    end)
    if not ok then unavailable() end
end

function panel.sync(state, phase)
    current = nil
    if not session_path then return end
    local retry = steam_cancel_retry
    if retry and os.time() >= retry.at then
        if publish_steam_join_state(steam_cancel_path, retry.nonce, "cancel") or retry.attempts == 1 then
            steam_cancel_retry = nil
        else
            retry.at, retry.attempts = os.time() + 1, retry.attempts - 1
        end
    end
    local context = title.probe(state, phase)
    if settings_fixture and not same_title(settings_fixture.context, context) then remove_settings_fixture() end
    local offer = context and read_offer()
    if steam_handoff and offer and offer.generation ~= steam_handoff.generation then
        cancel_steam_form(steam_handoff.nonce)
        steam_handoff = nil
    end
    if guest_creation then
        if context then
            current = {state = state, phase = phase}
            resume_guest_creation(context, offer, state, phase)
        end
        return
    end
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
    if fields[2] == "native-title-stock-profile-fixture" then return stock_profile_fixture(fields, state, phase) end
    if fields[2] == "native-title-settings-widget-fixture" then return settings_fixture_command(fields, state, phase) end
    if fields[2] == "native-title-host-form-fixture" then return host_form_fixture_command(fields, state, phase) end
    if fields[2] == "native-title-join-form-fixture" then return join_form_fixture_command(fields, state, phase) end
    if fields[2] == "native-title-button-fixture" then
        if not fixture_enabled() or #fields ~= 3 or not can_choose(title.probe(state, phase)) then
            error("title_panel_fixture_unavailable", 0)
        end
        local key, form = fields[3], session.form
        local pressed = key == "modes" and controls.press_fixture(session.solo_back)
            or form and form.role == "join" and form.api.click_fixture(form.draft, key)
            or surface.press_fixture(session.view, key)
        if not pressed then error("title_panel_fixture_button_unavailable", 0) end
        return "title_button_queued"
    end
    if fields[2] ~= "native-title-panel-press-fixture" then return end
    if not fixture_enabled() then error("title_panel_fixture_unavailable", 0) end
    local action = fields[3]
    if #fields ~= 3 or action ~= "hide" and action ~= "host" and action ~= "join" then
        error("title_panel_fixture_arguments", 0)
    end
    local accepted, reason = choose(action, title.probe(state, phase))
    if not accepted then error(reason, 0) end
    return "qualified staged title control dispatched"
end

function panel.rows(state, phase, status_row)
    local context = title.probe(state, phase)
    if not report or not context then return {} end
    local mounted = can_choose(context)
    return {status_row("native-title-panel", state.map, report.state, report.choices, mounted and 1 or 0, report.reason or "-")}
end

function panel.start(bridge)
    if loop or type(bridge) ~= "string" or bridge == "" then return end
    bridge_path = bridge
    session_path = bridge .. "/title-handoff-session.tsv"
    request_path = bridge .. "/title-handoff-request.tsv"
    steam_join_path = bridge .. "/steam-coop-join.tsv"
    steam_cancel_path = bridge .. "/steam-coop-join-cancel.tsv"
    steam_opened_path = bridge .. "/steam-coop-join-opened.tsv"
    loop = LoopInGameThreadWithDelay(16, function()
        if not pcall(update_title) then unavailable() end
    end)
end

return panel
