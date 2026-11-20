local prefix = "private"
local offer = prefix .. "/native-coop-pause-session.tsv"
local eligible = prefix .. "/native-session-exit-eligibility.state"
local input = prefix .. "/native-session-exit-input.state"
local request = prefix .. "/native-coop-pause-request.tsv"
local first = string.rep("a", 32)
local second = string.rep("b", 32)
local third = string.rep("c", 32)
local fs, now = {}, 100

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
local map = "/Game/Classes/Maps/PlaytestMap_01"
local world = object(10)
world.GetFullName = function() return "World " .. map .. ".PlaytestMap_01" end
world.NetDriver = object(11)
world.NetDriver.World = world
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

local function fake_open(path, mode)
    if mode == "rb" and not fs[path] then return end
    return {
        read = function(_, n) assert(mode == "rb"); return fs[path]:sub(1, n) end,
        write = function(_, bytes) assert(mode == "wb"); fs[path] = bytes; return true end,
        close = function() return true end,
    }
end

local embedded, fixture_enabled = true, false
local env = setmetatable({
    io = {open = fake_open},
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
    StaticFindObject = function(path)
        if path:find("PlayerControler_FP", 1, true) then return controller_class end
        if path:find("Player_FP", 1, true) then return pawn_class end
    end,
}, {__index = _G})

local script = "mod/SilverfishCoop/Scripts/native_coop_pause.lua"
local bridge = assert(loadfile(script, "t", env))()
local function descriptor(generation, role, expiration)
    fs[offer] = table.concat({1, generation, role, expiration}, "\t") .. "\n"
end
local function capture(generation, sequence, reason)
    fs[input] = table.concat({1, generation, sequence, reason}, "\t") .. "\n"
end
local function row(...)
    return table.concat({...}, "\t")
end

bridge.start(prefix)
descriptor(first, "host", 104)
bridge.sync(state, "ready")
assert(fs[eligible] == nil and fs[request] == nil)
local allowed, error_text = pcall(bridge.command, {"1", "native-coop-pause-fixture", "open"}, state, "hosting")
assert(not allowed and error_text == "native_coop_pause_fixture_unavailable")
bridge.sync(state, "hosting")
assert(fs[eligible] == table.concat({1, first, "host", "a", "14", "1e", 104}, "\t") .. "\n")
assert(bridge.rows(state, "hosting", row)[1] == row("native-coop-pause", "host", first, "armed", 0))

capture(second, 1, "quit_to_menu")
bridge.sync(state, "hosting")
assert(fs[input] == nil and fs[request] == nil)
capture(first, "01", "quit_to_menu")
bridge.sync(state, "hosting")
assert(fs[input] == nil and fs[request] == nil)
capture(first, 2, "save_and_quit")
fs[request] = "existing"
bridge.sync(state, "hosting")
assert(fs[request] == "existing" and fs[input] ~= nil)
fs[request] = nil
bridge.sync(state, "hosting")
assert(fs[request] == table.concat({1, first, 2, "host", "save_and_quit"}, "\t") .. "\n")
assert(fs[input] == nil and fs[eligible] ~= nil)
assert(bridge.rows(state, "hosting", row)[1] == row("native-coop-pause", "host", first, "requested", 2))
fs[request] = nil
capture(first, 3, "quit_to_menu")
bridge.sync(state, "hosting")
assert(fs[request] == nil and fs[input] == nil)

now = 105
bridge.sync(state, "hosting")
assert(fs[eligible] == nil)
descriptor(second, "host", 111)
bridge.sync(state, "hosting")
assert(fs[eligible] == nil, "offer may not extend farther than five seconds")
descriptor(second, "host", 109)
controller.Loading = true
bridge.sync(state, "hosting")
assert(fs[eligible] == nil)
controller.Loading = false
bridge.sync(state, "hosting")
assert(fs[eligible] ~= nil)
local method = pawn.GetAddress
pawn.GetAddress = function() return 31 end
bridge.sync(state, "hosting")
assert(fs[eligible] == nil, "same generation cannot be reused after a pawn change")
pawn.GetAddress = method

authority, state.server, world.NetDriver.ServerConnection = false, true, object(12)
descriptor(third, "guest", 109)
bridge.sync(state, "connected")
assert(fs[eligible] == table.concat({1, third, "client", "a", "14", "1e", 109}, "\t") .. "\n")
capture(third, 4, "quit_to_menu")
bridge.sync(state, "connected")
assert(fs[request] == table.concat({1, third, 4, "guest", "quit_to_menu"}, "\t") .. "\n")
bridge.clear()
assert(fs[eligible] == nil and fs[request] ~= nil, "revocation never removes an already-published worker request")

embedded = false
local standalone = assert(loadfile(script, "t", env))()
standalone.start(prefix)
standalone.sync(state, "connected")
assert(fs[eligible] == nil and #standalone.rows(state, "connected", row) == 0)

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
