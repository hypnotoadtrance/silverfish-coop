local prefix = "private"
local steam_host = prefix .. "/steam-coop-host.tsv"
local steam_request = prefix .. "/steam-coop-invite-request.tsv"
local first = string.rep("a", 32)
local second = string.rep("b", 32)
local third = string.rep("c", 32)
local fs, now = {}, 100
local lease, bound
local function bind(role, world, controller, pawn)
    if not role or not lease or lease.role ~= role or lease.expiration <= now then return end
    local key = table.concat({world, controller, pawn}, ":")
    if bound and bound.generation == lease.generation and bound.key ~= key then return end
    bound = {generation = lease.generation, key = key}
    return lease.generation, lease.press
end

local function object(address, class)
    return {
        IsValid = function() return true end,
        GetAddress = function() return address end,
        GetClass = function() return class end,
        IsActorBeingDestroyed = function() return false end,
    }
end

local controller_class = object(50)
local pawn_class = object(51)
local pause_class = object(52)
local vertical_class = object(54)
local map = "/Game/Classes/Maps/PlaytestMap_01"
local world = object(10)
world.GetFullName = function() return "World " .. map .. ".PlaytestMap_01" end
world.NetDriver = object(11)
world.NetDriver.World = world
world.OwningGameInstance = object(13)
local controller = object(20, controller_class)
local pawn = object(30, pawn_class)
local authority = true
controller.Pawn, pawn.Controller = pawn, controller
controller.GetWorld, pawn.GetWorld = function() return world end, function() return world end
controller.HasAuthority, pawn.HasAuthority = function() return authority end, function() return authority end
controller.IsLocalController = function() return true end
pawn.IsLocallyControlled = function() return true end
controller.Loading = false
local state = {listening = true, server = false, world = world, map = map, controller = controller, local_pawn = {pawn = pawn}}
local menu = object(40, pause_class)
menu.GetOwningPlayer = function() return controller end
menu.GetOuter = function() return world.OwningGameInstance end
menu.IsInViewport = function() return true end
menu.IsVisible = function() return true end
local pause_parent = object(55, vertical_class)
menu.Button_SaveAndQuit = object(56)
menu.Button_SaveAndQuit.GetParent = function() return pause_parent end
local pause_hook
local already_paused = false
local statics = object(53)
statics.IsGamePaused = function(_, current) assert(current == world); return already_paused end

local function fake_open(path, mode)
    if mode == "rb" and not fs[path] then return end
    return {
        read = function(_, n) assert(mode == "rb"); return fs[path]:sub(1, n) end,
        write = function(_, bytes) assert(mode == "wb"); fs[path] = bytes; return true end,
        close = function() return true end,
    }
end

local embedded, fixture_enabled = true, false
local invite_button
local controls = {
    create = function(scene, parent, label, callback)
        assert(scene.role == "host" and parent == pause_parent and invite_button == nil)
        local widget = {
            text = label,
            UpdateText = function(self, text) self.text = text end,
            SetToolTipText = function(self, text) self.tooltip = text end,
            SetIsEnabled = function(self, enabled) self.enabled = enabled end,
        }
        invite_button = {widget = widget, callback = callback}
        return invite_button
    end,
    remove = function(button)
        if button then assert(button == invite_button); invite_button = nil end
    end,
}
local env = setmetatable({
    io = {open = fake_open},
    SilverfishBindStockPauseExit = bind,
    os = {
        time = function() return now end,
        getenv = function(key)
            if key == "SILVERFISH_COOP_EMBEDDED_ENGINE" then return embedded and "1" or nil end
            if fixture_enabled then return "1" end
        end,
        remove = function(path) fs[path] = nil; return true end,
        rename = function(old, new)
            if not fs[old] or fs[new] then return false end
            fs[old], fs[new] = nil, fs[old]
            return true
        end,
    },
    require = function(name)
        if name == "world_path" then return dofile("mod/SilverfishCoop/Scripts/world_path.lua") end
        assert(name == "native_menu_controls")
        return controls
    end,
    FText = function(text) return text end,
    StaticFindObject = function(path)
        if path:find("PlayerControler_FP", 1, true) then return controller_class end
        if path:find("Player_FP", 1, true) then return pawn_class end
        if path:find("Menu_Pause_Widget", 1, true) then return pause_class end
        if path == "/Script/UMG.VerticalBox" then return vertical_class end
        if path == "/Script/Engine.Default__GameplayStatics" then return statics end
    end,
    RegisterHook = function(path, callback)
        assert(path == "/Script/Engine.GameplayStatics:SetGamePaused")
        pause_hook = callback
    end,
}, {__index = _G})

local script = "mod/SilverfishCoop/Scripts/native_coop_pause.lua"
local bridge = assert(loadfile(script, "t", env))()
local function descriptor(generation, role, expiration)
    lease = {generation = generation, role = role == "host" and 1 or 2, expiration = expiration, press = 0}
end
local function row(...)
    return table.concat({...}, "\t")
end
local function requests_pause(widget)
    local requested_pause = true
    pause_hook(nil, {get = function() return widget end}, {
        get = function() return requested_pause end,
        set = function(_, value) requested_pause = value end,
    })
    return requested_pause
end

bridge.start(prefix)
descriptor(first, "host", 104)
bridge.sync(state, "ready")
assert(#bridge.rows(state, "ready", row) == 0)
assert(requests_pause(menu), "A title or solo session must keep stock pause")
local allowed, error_text = pcall(bridge.command, {"1", "native-coop-pause-fixture", "open"}, state, "hosting")
assert(not allowed and error_text == "native_coop_pause_fixture_unavailable")
bridge.sync(state, "hosting")
assert(bridge.rows(state, "hosting", row)[1] == row("native-coop-pause", "host", first, "armed", 0))
assert(not requests_pause(menu), "The host's exact stock pause menu keeps the world running")
already_paused = true
assert(requests_pause(menu), "The menu cannot undo an existing pause")
already_paused = false
assert(requests_pause(object(41)), "Other widgets keep their stock pause requests")
local driver = world.NetDriver
world.NetDriver = object(42)
assert(requests_pause(menu), "An expired world binding cannot change pause")
world.NetDriver = driver

controller.MenuStack = {menu, GetArrayNum = function(self) return #self end}
bridge.sync(state, "hosting")
assert(not invite_button, "A Direct IP host has no Steam lobby row")
fs[steam_host] = row(1, second, "ready") .. "\n"
bridge.sync(state, "hosting")
assert(not invite_button, "Ignore a Steam marker from a different host session")
fs[steam_host] = row(1, first, "ready") .. "\n"
state.listening = false
bridge.sync(state, "ready")
assert(not invite_button, "Singleplayer has no Steam lobby row even with a leftover host marker")
state.listening = true

local function expect_steam(status, label, hint, enabled)
    fs[steam_request] = nil
    fs[steam_host] = row(1, first, status) .. "\n"
    bridge.sync(state, "hosting")
    assert(invite_button.widget.text == label and invite_button.widget.enabled == enabled)
    assert(invite_button.widget.tooltip:find(hint, 1, true))
    invite_button.callback()
    assert(fs[steam_request] == (enabled and row(1, first, "invite") .. "\n" or nil))
    return invite_button
end

local invite_widget = expect_steam("ready", "Invite Steam Friends", "Steam friends list", true)
assert(invite_widget == expect_steam("friends", "Steam friends can join", "Join Game", true))
assert(invite_widget == expect_steam("overlay", "Steam invites unavailable", "Check the Steam overlay", true))
assert(not invite_widget.widget.tooltip:find("Join Game", 1, true))
assert(invite_widget == expect_steam("opening", "Opening Steam…", "Waiting for Steam", false))
assert(invite_widget == expect_steam("retry", "Retry Steam Lobby", "could not create", true))
assert(invite_widget == expect_steam("ready", "Invite Steam Friends", "Steam friends list", true))

fs[steam_request] = nil
local stale_click = invite_button.callback
controller.MenuStack[2] = object(57)
bridge.sync(state, "hosting")
assert(invite_button == nil, "The button must leave the stock pause when another menu covers it")
stale_click()
assert(fs[steam_request] == nil)
controller.MenuStack[2] = nil
bridge.sync(state, "hosting")
assert(invite_button and invite_button.widget.enabled)
fs[steam_host] = nil

lease.press = 2
bridge.sync(state, "hosting")
assert(bridge.rows(state, "hosting", row)[1] == row("native-coop-pause", "host", first, "requested", 2))

now = 105
bridge.sync(state, "hosting")
assert(#bridge.rows(state, "hosting", row) == 0, "an expired offer leaves no armed exit")
descriptor(second, "host", 109)
controller.Loading = true
bridge.sync(state, "hosting")
assert(#bridge.rows(state, "hosting", row) == 0)
controller.Loading = false
bridge.sync(state, "hosting")
assert(bridge.rows(state, "hosting", row)[1] == row("native-coop-pause", "host", second, "armed", 0))
local method = pawn.GetAddress
pawn.GetAddress = function() return 31 end
bridge.sync(state, "hosting")
assert(#bridge.rows(state, "hosting", row) == 0, "same generation cannot be reused after a pawn change")
pawn.GetAddress = method

authority, state.server, world.NetDriver.ServerConnection = false, true, object(12)
descriptor(third, "guest", 109)
fs[steam_host] = row(1, third, "ready") .. "\n"
bridge.sync(state, "connected")
assert(not invite_button and fs[steam_request] == nil, "A connected guest cannot open a Steam host invite")
fs[steam_host] = nil
assert(bridge.rows(state, "connected", row)[1] == row("native-coop-pause", "guest", third, "armed", 0))
assert(not requests_pause(menu), "The client's exact stock pause menu keeps the world running")
lease.press = 4
bridge.sync(state, "connected")
assert(bridge.rows(state, "connected", row)[1] == row("native-coop-pause", "guest", third, "requested", 4))
bridge.clear()
assert(#bridge.rows(state, "connected", row) == 0 and lease.press == 4, "revocation keeps an already-published press")
assert(requests_pause(menu), "Session exit restores the stock pause request")

embedded = false
local standalone = assert(loadfile(script, "t", env))()
standalone.start(prefix)
standalone.sync(state, "connected")
assert(#standalone.rows(state, "connected", row) == 0)

local command_path = prefix .. "/native-coop-pause-fixture-command.tsv"
local reply_path = prefix .. "/native-coop-pause-fixture-reply.tsv"
local gameplay_path = prefix .. "/command.tsv"
local raised, count, blocked = false, 0, false
env.io.open = function(path, mode)
    if blocked and path == reply_path .. ".new" then return end
    return fake_open(path, mode)
end
embedded, fixture_enabled = true, true
local gated = assert(loadfile(script, "t", env))()
gated.start(prefix)
controller.MenuStack = {GetArrayNum = function(self) return #self end}
controller.OpenMenu = function(self, class, created)
    assert(class == pause_class)
    self.MenuStack[1], created.CreatedMenu = menu, menu
end
descriptor(third, "guest", 109)
gated.command({first, "native-coop-pause-fixture", "open"}, state, "connected")
gated.sync(state, "connected")
assert(gated.rows(state, "connected", row)[2] == row("native-coop-pause-fixture", "guest", "open"))
gated.command = function(fields, observed, phase)
    assert(fields[2] == "native-coop-pause-fixture" and fields[3] == "open" and observed == state and phase == "connected")
    count = count + 1
    if raised then error("stock callback\tfailed\n", 0) end
    return "exact stock co-op pause open"
end
descriptor(third, "guest", 109)
fs[gameplay_path] = "unrelated gameplay command"
fs[command_path] = row(1, first, "open") .. "\n"
blocked = true
gated.sync(state, "connected")
assert(count == 1 and fs[reply_path] == nil)
gated.sync(state, "connected")
assert(count == 1 and fs[reply_path] == nil)
blocked = false
gated.sync(state, "connected")
assert(count == 1 and fs[reply_path] == row(1, first, "ok", "exact stock co-op pause open") .. "\n")
assert(fs[gameplay_path] == "unrelated gameplay command")
fs[command_path] = nil
gated.sync(state, "connected")
fs[command_path] = row(1, first, "open") .. "\n"
gated.sync(state, "connected")
assert(count == 1 and fs[reply_path] == row(1, first, "error", "native_coop_pause_fixture_duplicate_nonce") .. "\n")
fs[command_path] = row(1, second, "unsupported") .. "\n"
gated.sync(state, "connected")
assert(count == 1 and fs[reply_path] == row(1, second, "error", "native_coop_pause_fixture_arguments") .. "\n")
raised = true
fs[command_path] = row(1, third, "open") .. "\n"
gated.sync(state, "connected")
assert(count == 2 and fs[reply_path] == row(1, third, "error", "stock callback?failed?") .. "\n")
