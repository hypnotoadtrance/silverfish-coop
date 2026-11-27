local descriptor, request = "bridge/title-handoff-session.tsv", "bridge/title-handoff-request.tsv"
local join_request, join_response = "bridge/title-join-request.tsv", "bridge/title-join-response.tsv"
local join_invite = "bridge/title-join-fixture.invite.json"
local steam_join, steam_cancel = "bridge/steam-coop-join.tsv", "bridge/steam-coop-join-cancel.tsv"
local steam_opened = "bridge/steam-coop-join-opened.tsv"
local browse_request = "bridge/steam-coop-browser-request.tsv"
local browse_response = "bridge/steam-coop-browser-response.tsv"
local host_catalog = "bridge/title-host-campaigns.tsv"
local save_request = "bridge/title-save-request.tsv"
local save_response = "bridge/title-save-response.tsv"
local files, flags, queued = {}, {}, {}
local now, id, tick, display, solo_back, writes = 100, 0, nil, nil, nil, 0
local refuse_rename = false

local function object(parent, enabled)
    id = id + 1
    local address = id
    return {
        parent = parent, enabled = enabled ~= false, visibility = 4,
        IsValid = function(self) return self.valid ~= false end,
        GetAddress = function() return address end,
        GetOuter = function(self) return self.outer end,
        GetParent = function(self) return self.parent end,
        GetVisibility = function(self) return self.visibility end,
        SetVisibility = function(self, value) self.visibility = value end,
        GetIsEnabled = function(self) return self.enabled end,
        SetIsEnabled = function(self, value) self.enabled = value end,
    }
end

local stock_parent = object()
local context = {world = object(), viewport = object(), menu = object(), controller = object()}
local tree, stock_root = object(), object()
tree.RootWidget, stock_root.outer, context.menu.WidgetTree = stock_root, tree, tree
for _, name in ipairs({"Button_NewGame", "Button_Load_1", "Button_Load_2", "Button_TestMap",
    "Button_SETTINGS", "Button_QUIT"}) do
    context.menu[name] = object(stock_parent, name ~= "Button_Load_2")
end

local guid = object()
local next_guid = 3
function guid:NewGuid() return {A = 1, B = -1, C = 2, D = next_guid} end
local function operation_id() return ("00000001ffffffff00000002%08x"):format(next_guid) end
local function generation(letter) return string.rep(letter, 32) end

local controls = {}
function controls.create(owner, parent, label, callback)
    assert(owner == context and parent == stock_parent and label == "Back to modes")
    local record = {widget = object(parent), callback = callback}
    solo_back = record
    return record
end

function controls.remove(record)
    if record then record.widget.parent = nil end
end
function controls.press_fixture(record)
    if not record or not record.widget.parent then return false end
    queued[#queued + 1] = record.callback
    return true
end

local surface = {}
function surface.mount(owner, spec, dispatch)
    assert(owner == context and not display)
    local view = {title = spec.title, note = spec.note, focus = spec.focus, fields = {}, buttons = {}, toggles = {}}
    for _, row in ipairs(spec.rows) do
        if row.field then
            local field = row.field
            view.fields[field.key] = field.value or ""
        elseif row.toggle then
            local toggle = row.toggle
            view.toggles[toggle.key] = {checked = toggle.checked, target = toggle.target}
        else
            for _, button in ipairs(row) do
                assert(not view.buttons[button.key])
                view.buttons[button.key] = {label = button.label, detail = button.detail, photo = button.photo, widget = object(view),
                    callback = function() dispatch(button.key) end}
            end
        end
    end
    display = view
    surface.sync(view)
    return view
end

function surface.remove(view)
    if not view then return end
    for _, record in pairs(view.buttons) do controls.remove(record) end
    if display == view then display = nil end
end

function surface.sync(view)
    if not view or view ~= display then return false end
    for _, toggle in pairs(view.toggles) do
        if toggle.target and not toggle.checked then view.fields[toggle.target] = "" end
    end
    return true
end

function surface.checked(view, key)
    local toggle = view and view == display and view.toggles[key]
    if toggle then return toggle.checked end
end

function surface.set_checked(view, key, checked)
    local toggle = view and view == display and view.toggles[key]
    if not toggle then return false end
    toggle.checked = checked
    return surface.sync(view)
end

function surface.read(view, key) return view and view == display and view.fields[key] or nil end
function surface.write(view, key, value)
    if surface.read(view, key) == nil then return false end
    for _, toggle in pairs(view.toggles) do
        if toggle.target == key and not toggle.checked then return value == "" end
    end
    view.fields[key] = value
    return true
end

function surface.press_fixture(view, key)
    return view == display and view and controls.press_fixture(view.buttons[key]) or false
end

local env
local modules = {
    native_menu_controls = controls,
    native_title_surface = surface,
    native_title_display = {},
    native_title_settings_widget = {},
    native_title_entry = {
        probe = function(state, phase)
            if state.qualified and phase == "ready" then return context end
        end,
        starting = function(state, phase)
            if state.qualified and phase == "opening" then return context end
        end,
        refresh_singleplayer = function(owner) assert(owner == context) end,
    },
}
env = setmetatable({
    require = function(name)
        assert(modules[name] or name == "native_title_host_form" or name == "native_title_join_form" or name == "native_title_save_manager")
        if not modules[name] then
            modules[name] = assert(loadfile("mod/SilverfishCoop/Scripts/" .. name .. ".lua", "t", env))()
        end
        return modules[name]
    end,
    StaticFindObject = function(path) if path == "/Script/Engine.Default__KismetGuidLibrary" then return guid end end,
    io = {open = function(path, mode)
        if mode == "rb" then
            if not files[path] then return end
            local offset = 1
            return {read = function(_, size)
                local value = files[path]:sub(offset, offset + size - 1)
                offset = offset + #value
                return #value > 0 and value or nil
            end, close = function() return true end}
        end
        assert(mode == "wb" and (path:sub(1, #request) == request or path:sub(1, #join_request) == join_request
            or path:sub(1, #steam_cancel) == steam_cancel or path:sub(1, #steam_opened) == steam_opened
            or path:sub(1, #browse_request) == browse_request or path:sub(1, #save_request) == save_request))
        return {write = function(_, ...) files[path] = table.concat({...}); writes = writes + 1; return true end,
            close = function() return true end}
    end},
    os = {time = function() return now end, getenv = function(name) return flags[name] end,
        remove = function(path) files[path] = nil end,
        rename = function(source, target)
            assert(target == request or target == join_request or target == steam_cancel or target == steam_opened
                or target == browse_request or target == save_request)
            if refuse_rename or files[target] then return false end
            files[target], files[source] = files[source], nil
            return true
        end},
    LoopInGameThreadWithDelay = function(_, callback) tick = callback; return 1 end,
}, {__index = _G})

local function start_panel()
    local panel = assert(loadfile("mod/SilverfishCoop/Scripts/native_title_panel.lua", "t", env))()
    panel.start("bridge")
    return panel
end

local panel = start_panel()
local state = {qualified = true, map = "/Game/Classes/Maps/MainMenu_P"}
local function sync() panel.sync(state, "ready") end
local function report() return panel.rows(state, "ready", table.pack)[1] end
local function offer(choices, until_time, stamp, defaults)
    files[descriptor] = defaults
        and ("2\t%s\t%s\t%d\t%s\n"):format(stamp, choices, until_time, table.concat(defaults, "\t"))
        or ("1\t%s\t%s\t%d\n"):format(stamp, choices, until_time)
    sync()
end

local function fixture(name, action, value, phase)
    return pcall(panel.command, {"test", name, action, value}, state, phase or "ready")
end

local function button(key, phase) return fixture("native-title-button-fixture", key, nil, phase) end
local function host(action, value) return fixture("native-title-host-form-fixture", action, value) end
local function join(action, value) return fixture("native-title-join-form-fixture", action, value) end
local function rejected(reason, ok, observed) assert(not ok and observed == reason, tostring(observed)) end
local function drain()
    local callbacks = queued
    queued = {}
    for _, callback in ipairs(callbacks) do callback() end
end

local function click(key)
    local ok, result = button(key)
    assert(ok and result == "title_button_queued", tostring(result))
    drain()
end

sync()
assert(not display and not report() and stock_root.visibility == 4)
offer("host,join", 111, generation("a"))
assert(not display and stock_root.visibility == 4)

offer("host", 108, generation("a"))
assert(report().n == 6 and report()[3] == "ready" and report()[4] == "host" and report()[5] == 1)
assert(display.title == "Project Silverfish" and display.buttons.multiplayer and display.buttons.singleplayer
    and display.buttons.quit and not solo_back and stock_root.visibility == 2)
rejected("title_panel_fixture_unavailable", button("multiplayer"))
flags.SILVERFISH_COOP_HEADLESS = "1"
rejected("title_panel_fixture_unavailable", button("multiplayer"))
flags.SILVERFISH_COOP_TITLE_FIXTURES = "1"
rejected("title_panel_fixture_unavailable", button("multiplayer", "opening"))
click("multiplayer")
assert(display.title == "Multiplayer" and display.buttons.host and not display.buttons.join)
assert(stock_root.visibility == 2 and context.menu.Button_NewGame.enabled and context.menu.Button_Load_1.enabled
    and not context.menu.Button_Load_2.enabled)
rejected("title_panel_fixture_button_unavailable", button("join"))
click("modes")
click("singleplayer")
assert(not display and stock_root.visibility == 4 and solo_back.widget.parent == stock_parent and not files[request])
click("modes")
assert(display.title == "Project Silverfish" and stock_root.visibility == 2 and not solo_back.widget.parent)

local old_mode = display.buttons.multiplayer
assert(button("multiplayer"))
offer("join", 108, generation("b"))
drain()
assert(not old_mode.widget.parent and display.title == "Project Silverfish" and not files[request])
now = 109
rejected("title_panel_fixture_unavailable", button("multiplayer"))
tick()
assert(not display and stock_root.visibility == 4 and not report())
now = 100
offer("join", 108, generation("c"))
assert(button("multiplayer"))
state.qualified = false
drain()
tick()
assert(not display and stock_root.visibility == 4 and not files[request] and not report())
state.qualified = true
offer("host", 108, generation("d"))
stock_root.valid = false
rejected("title_panel_fixture_unavailable", button("multiplayer"))
stock_root.valid = true
offer("host", 108, generation("e"))

assert(fixture("native-title-panel-press-fixture", "host"))
local pending = "1\t" .. generation("e") .. "\t" .. operation_id() .. "\thost\n"
assert(files[request] == pending and report()[3] == "pending" and writes == 1)
rejected("title_request_already_pending", fixture("native-title-panel-press-fixture", "host"))
offer("host", 108, generation("f"))
click("multiplayer")
click("host")
click("submit")
assert(files[request] == pending and writes == 1 and report()[3] == "unavailable"
    and report()[6] == "title_request_write_failed")
click("modes")
assert(display.title == "Project Silverfish" and stock_root.visibility == 2)
files[request] = nil

local edited = generation("1")
offer("host", 108, edited)
click("multiplayer")
click("host")
assert(display.title == "New Game" and not files[request])
rejected("title_host_form_fixture_arguments", host("campaign", "title-host-settings-1234abcd"))
assert(host("port", "0"))
click("submit")
assert(not files[request] and display.fields.port == "0" and display.note)
assert(host("port", "28181"))
assert(surface.write(display, "bind", "::ffff:500.0.0.1"))
click("submit")
assert(not files[request] and display.fields.bind == "::ffff:500.0.0.1" and display.note)
assert(surface.write(display, "bind", "2001:db8::1"))
click("submit")
assert(files[request] == "2\t" .. edited .. "\t" .. operation_id()
    .. "\thost\t\t2001:db8::1\t28181\n")
assert(report()[3] == "pending")

files[request] = nil
offer("host", 108, generation("2"))
click("multiplayer")
click("host")
refuse_rename = true
click("submit")
assert(report()[6] == "title_request_write_failed" and not files[request]
    and not files[request .. "." .. operation_id() .. ".tmp"])
refuse_rename = false

local join_generation, import_id = generation("3"), generation("a")
local original_character = "b9f344d2-2ed6-4fbf-a237-3c014586ab31"
local created_character = "4ac79601-2979-4bdf-8d7e-155650143df0"
offer("join", 108, join_generation)
click("multiplayer")
click("join")
assert(display.fields.invite == "" and not files[request] and not files[join_request])
local invite_payload = '{"credential":"private fixture token"}'
files[join_invite] = invite_payload .. "\r\n"
next_guid = 7
assert(join("import"))
assert(files[join_request] == ("1\t%s\t%s\timport\t%d\n%s"):format(
    join_generation, operation_id(), #invite_payload, invite_payload))
local expected_catalog = ("1\t%s\t%s\tok\t%s\t1\n%s\tOld character\n")
    :format(join_generation, operation_id(), import_id, original_character)
local function join_state(expected)
    local ok, observed = join("observe")
    assert(ok and observed == "title_join_form_state=" .. expected, tostring(observed))
end
files[join_response] = expected_catalog
join_state("waiting")
files[join_request] = nil
files[join_response] = expected_catalog:gsub(join_generation, generation("4"), 1)
join_state("waiting")
rejected("title_panel_fixture_button_unavailable", button("character:1"))
files[join_response] = expected_catalog
join_state("catalog;count=1")
assert(display.buttons["character:1"].label == "Old character")
rejected("title_panel_fixture_button_unavailable", button("character:2"))

next_guid = 8
click("create")
assert(surface.write(display, "character", "New character"))
click("create")
assert(files[join_request] == ("1\t%s\t%s\tcreate\t%s\tNew character\n"):format(
    join_generation, operation_id(), import_id))
assert(not files[request])
files[join_request] = nil
files[join_response] = ("1\t%s\t%s\tok\t%s\t2\n%s\tOld character\n%s\tNew character\n")
    :format(join_generation, operation_id(), import_id, original_character, created_character)
join_state("catalog;count=2")
click("character:2")
assert(join("profile", "my-guest-profile"))
next_guid = 9
click("confirm")
assert(files[request] == ("2\t%s\t%s\tjoin\t%s\tmy-guest-profile\t%s\n"):format(
    join_generation, operation_id(), import_id, created_character))

files[request] = nil
flags.SILVERFISH_COOP_RESIDENT_TITLE = "1"
state.qualified = false
sync()
state.qualified = true
panel = start_panel()
local fresh_generation = generation("6")
local defaults = {"Configured-Campaign", "127.0.0.1", "7777"}
offer("host", 108, fresh_generation, defaults)
click("multiplayer")
click("host")
click("new")
assert(display.title == "New Game" and display.fields.campaign == ""
    and display.fields.bind == defaults[2] and display.fields.port == defaults[3])
assert(surface.checked(display, "require_password") == false and surface.read(display, "password") == "")
assert(host("campaign", "title-host-settings-1234abcd"))
assert(surface.read(display, "campaign") == "title-host-settings-1234abcd")
assert(surface.set_checked(display, "require_password", true))
click("submit")
assert(not files[request] and display.note and display.focus == "password" and surface.checked(display, "require_password"))
assert(host("password") and surface.read(display, "password") == "Fixture co-op pass")
assert(surface.set_checked(display, "require_password", false) and surface.read(display, "password") == "")
click("submit")
assert(files[request] == ("4\t%s\t%s\thost\ttitle-host-settings-1234abcd\t127.0.0.1\t7777\t\tcreator\n")
    :format(fresh_generation, operation_id()))
files[request] = nil

local save_generation = generation("5")
local catalog = ("2\t%s\t2\nFirst-Campaign\tObservatory\t2026-11-20 18:15:18\n"
    .. "Second-Campaign\tTutorial Dock\t2026-11-20 19:45:30\n"):format(save_generation)
files[host_catalog] = catalog:gsub(save_generation, generation("4"), 1)
offer("host", 108, save_generation, defaults)
click("multiplayer")
click("host")
click("browse")
rejected("title_panel_fixture_button_unavailable", button("load:Second-Campaign"))
files[host_catalog] = catalog
click("browse")
assert(display.buttons["load:First-Campaign"].label == "Observatory"
    and display.buttons["load:First-Campaign"].detail == "2026-11-20 18:15:18"
    and display.buttons["load:Second-Campaign"].label == "Tutorial Dock"
    and display.buttons["load:Second-Campaign"].detail == "2026-11-20 19:45:30")
click("load:Second-Campaign")
assert(display.title == "Load Game" and display.note == "Tutorial Dock  /  2026-11-20 19:45:30"
    and host("port", "28181"))
click("submit")
assert(files[request] == ("5\t%s\t%s\thost\tSecond-Campaign\t127.0.0.1\t28181\t\tresume\n")
    :format(save_generation, operation_id()))

files[request] = nil
state.qualified = false
sync()
state.qualified = true
flags.SILVERFISH_COOP_TITLE_DIRECT_JOIN = "1"
panel = start_panel()
local steam_generation, steam_nonce = generation("7"), generation("8")
offer("host,join", 110, steam_generation, defaults)
files[steam_join] = ("1\t%s\t0\t159\n"):format(steam_nonce)
tick()
assert(display.title == "Project Silverfish" and not files[steam_opened])
now = 101
files[steam_join] = ("1\t%s\t28951\t159\n"):format(steam_nonce)
refuse_rename = true
tick()
assert(display.title == "Join via Steam" and display.buttons.connect and not display.toggles.use_password
    and not display.fields.password
    and display.fields.address == nil and display.fields.port == nil and not files[steam_opened])
local steam_view = display
refuse_rename = false
now = 102
tick()
assert(display == steam_view and files[steam_opened] == "1\t" .. steam_nonce .. "\topened\n")
files[steam_opened] = nil
next_guid = 11
click("connect")
assert(files[join_request] == ("1\t%s\t%s\tdirect\t127.0.0.1\t28951\t\n")
    :format(steam_generation, operation_id()))
local function steam_result(failure)
    files[join_request] = nil
    files[join_response] = ("1\t%s\t%s\terror\t%s\t0\n"):format(steam_generation, operation_id(), failure)
    tick()
end

steam_result("direct_host_unreachable")
assert(display.title == "Join via Steam" and display.buttons.connect.label == "Retry join"
    and not display.fields.password and not display.fields.address)
next_guid = next_guid + 1
click("connect")
assert(files[join_request] == ("1\t%s\t%s\tdirect\t127.0.0.1\t28951\t\n")
    :format(steam_generation, operation_id()))
steam_result("direct_password_required")
assert(display.title == "Join via Steam" and not display.toggles.use_password
    and display.fields.password == "" and display.focus == "password" and display.buttons.connect.label == "Join")
click("connect")
assert(not files[join_request] and display.focus == "password")
assert(surface.write(display, "password", "Wrong fixture"))
next_guid = next_guid + 1
click("connect")
assert(files[join_request] == ("1\t%s\t%s\tdirect\t127.0.0.1\t28951\tWrong fixture\n")
    :format(steam_generation, operation_id()))
steam_result("direct_wrong_password")
assert(display.fields.password == "" and display.focus == "password" and display.note == "Incorrect password.")
click("back")
assert(display.title == "Multiplayer" and display.focus == "join"
    and files[steam_cancel] == "1\t" .. steam_nonce .. "\tcancel\n")
now = 103
tick()
assert(display.title == "Multiplayer" and not files[steam_opened], "Do not reopen the consumed Steam invite")
click("join")
assert(display.title == "Join by Direct IP" and display.fields.port == defaults[3] and display.focus == nil)
assert(join("address", "127.0.0.2") and join("port", "28952"))
local function direct_retry(password, failure)
    next_guid = next_guid + 1
    click("connect")
    assert(files[join_request] == ("1\t%s\t%s\tdirect\t127.0.0.2\t28952\t%s\n")
        :format(steam_generation, operation_id(), password))
    files[join_request] = nil
    files[join_response] = ("1\t%s\t%s\terror\t%s\t0\n"):format(steam_generation, operation_id(), failure)
    tick()
    assert(display.title == "Join by Direct IP" and display.focus == "password"
        and display.fields.address == "127.0.0.2" and display.fields.port == "28952"
        and surface.checked(display, "use_password") and surface.read(display, "password") == "")
end
direct_retry("", "direct_password_required")
assert(surface.write(display, "password", "Wrong fixture"))
direct_retry("Wrong fixture", "direct_wrong_password")
click("back")
assert(display.title == "Multiplayer" and files[steam_cancel] == "1\t" .. steam_nonce .. "\tcancel\n")

offer("host,join", 110, generation("9"), defaults)
click("multiplayer")
click("host")
click("new")
click("network")
click("steam")
assert(display.title == "Host networking" and display.note:find("unavailable", 1, true))
files["bridge/steam-coop-available"] = "1\n"
click("steam")
assert(display.title == "Steam lobby access" and display.buttons.invite.label == "Invite Only"
    and display.buttons.friends.label == "Friends Only" and display.buttons.public.label == "Public")
assert(display.buttons.invite.detail == "Join from a Steam invite"
    and display.buttons.friends.detail == "Friends can use Join Game"
    and display.buttons.public.detail == "Listed in the lobby browser")
click("public")
assert(display.title == "New Game" and display.fields.port == nil and display.fields.bind == nil
    and display.buttons.network.label == "Steam · Public")
assert(host("password"))
click("submit")
assert(files[request] == ("6\t%s\t%s\thost\t\tpublic\tFixture co-op pass\tcreator\n")
    :format(generation("9"), operation_id()))

files[request] = nil
local browse_generation = generation("a")
offer("host,join", 110, browse_generation, defaults)
click("multiplayer")
click("steam-public")
local prefix = ("1\t%s\t%s\t"):format(browse_generation, operation_id())
assert(files[browse_request] == prefix .. "search\n")
files[browse_request] = nil
files[browse_response] = ("2\t%s\t%s\tready\t1\nFixture Host\trc-abcdef0\t1\n"):format(browse_generation, operation_id())
tick()
assert(display.buttons["steam-pick:1"].label == "Fixture Host")
click("steam-pick:1")
assert(files[browse_request] == prefix .. "1\n")

files[steam_cancel], files[steam_join], files[steam_opened] = nil, nil, nil
local abandoned_nonce = generation("b")
now = 104
files[steam_join] = ("1\t%s\t28951\t159\n"):format(abandoned_nonce)
tick()
assert(display.title == "Join via Steam")
assert(files[steam_opened] == "1\t" .. abandoned_nonce .. "\topened\n")
files[steam_join], files[steam_opened], files[join_request] = nil, nil, nil
refuse_rename = true
state.qualified = false
sync()
assert(not display and not files[steam_cancel])
refuse_rename = false
now = 105
sync()
assert(files[steam_cancel] == "1\t" .. abandoned_nonce .. "\tcancel\n")
files[steam_cancel] = nil

state.qualified = true
local retry_generation, retry_nonce = generation("c"), generation("d")
offer("host,join", 115, retry_generation, defaults)
files[steam_join] = ("1\t%s\t28953\t159\n"):format(retry_nonce)
tick()
assert(display.title == "Join via Steam")
files[steam_join], files[steam_opened] = nil, nil
assert(files[join_request] == ("1\t%s\t%s\tdirect\t127.0.0.1\t28953\t\n"):format(retry_generation, operation_id()))
assert(not display.fields.password and not display.buttons.connect)
files[join_request] = nil
files[join_response] = ("2\t%s\t%s\tok\t%s\t1\nFixture Guest\t%s\n%s\tOriginal character\n")
    :format(retry_generation, operation_id(), import_id, string.rep("ab", 4408), original_character)
tick()
assert(display.buttons["character:1"] and not display.buttons["character:1"].photo)
click("character:1")
click("confirm")
assert(files[request] and not files[steam_cancel], "Keep the route through the submitted join")
files[request], files[descriptor] = nil, nil
sync()
assert(not files[steam_cancel], "Retiring a submitted panel must preserve the selected route")
offer("host,join", 115, generation("e"), defaults)
assert(files[steam_cancel] == "1\t" .. retry_nonce .. "\tcancel\n", "A fresh title offer releases an unused route")

local manage_generation, campaign_id = generation("d"), generation("c")
local character = "00000000-1111-2222-3333-444444444444"
now = 100
local function manage_list(revision, state)
    files[host_catalog] = ("3\t%s\t1\nExpedition\tCrossroads\t2026-11-20 18:15:18\t%s\t%s\t%s\n")
        :format(manage_generation, campaign_id, revision, state)
end
local function save_reply(verb, revision, target, rows)
    local id = operation_id()
    local prefix = "1\t" .. manage_generation .. "\t" .. id .. "\t"
    assert(files[save_request] == prefix .. verb .. "\tExpedition\t" .. campaign_id .. "\t" .. revision .. "\t" .. target .. "\n")
    files[save_request] = nil
    files[save_response] = ("2\t%s\t%s\tok\t%s"):format(manage_generation, id, rows)
    tick()
end
manage_list(1, "active")
offer("host,join", 108, manage_generation, defaults)
click("multiplayer")
click("host")
click("manage")
click("save:" .. campaign_id)
click("characters")
local steam_account = "76561198000000002"
local other_character, other_account = "00000000-1111-2222-3333-444444444445", "76561198000000003"
save_reply("characters", 1, "-", "2\n" .. character .. "\tCourier\tFixture Guest\t" .. steam_account
    .. "\n" .. other_character .. "\tScout\tOther Guest\t" .. other_account
    .. "\n" .. steam_account .. "\t" .. string.rep("ab", 4408)
    .. "\n" .. other_account .. "\t89504e470d0a1a0a\n")
assert(display.buttons["character:" .. character] and not display.buttons["character:" .. character].photo)
assert(display.buttons["character:" .. other_character].photo == "89504e470d0a1a0a")
click("character:" .. character)
click("delete-character")
assert(not files[save_request] and display.title == "Delete Character?")
assert(display.focus == "back" and display.note:find("cannot be undone", 1, true))
click("back")
assert(not files[save_request])
click("delete-character")
click("confirm")
manage_list(2, "active")
save_reply("delete-character", 1, character, "0\n")
click("save:" .. campaign_id)
click("delete-save")
assert(not files[save_request] and display.title == "Delete Save?")
click("confirm")
files[host_catalog] = ("3\t%s\t0\n"):format(manage_generation)
save_reply("delete-save", 2, "-", "0\n")
click("back")
click("browse")
rejected("title_panel_fixture_button_unavailable", button("load:Expedition"))
click("back")
click("manage")
rejected("title_panel_fixture_button_unavailable", button("save:" .. campaign_id))
assert(display.note == "No co-op saves.")

now, files[descriptor] = 10000, nil
sync()
flags.SILVERFISH_COOP_INSTALLED_TITLE = "1"
panel = start_panel()
panel.sync(state, "opening")
assert(display.title == "Project Silverfish" and stock_root.visibility == 2)
sync()
local before = display
offer("host,join", now + 8, generation("f"), defaults)
assert(display == before and report()[3] == "ready")
