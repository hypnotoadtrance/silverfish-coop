local guid = ("A"):rep(32)
local area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local pawn_type = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_type = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local pawn_name = "Player_FP_C " .. area .. ".Observitory_P:PersistentLevel.Player_FP_C_1"
local modules = {
    guest_weapon_registry = dofile("mod/SilverfishCoop/Scripts/guest_weapon_registry.lua"),
    guest_shot_view = dofile("mod/SilverfishCoop/Scripts/guest_shot_view.lua"),
}

local function object(address)
    return {IsValid = function() return true end, GetAddress = function() return address end}
end

for _, memory in ipairs({true, false}) do
    local kind = memory and "light-ar-01" or "smg-standard"
    local world, pawn, controller = object(1), object(2), object(3)
    local pawn_class, controller_class = object(4), object(5)
    world.GetFullName = function() return "World " .. area .. ".Observitory_P" end
    pawn.GetFullName = function() return pawn_name end
    pawn.Controller, controller.Pawn = controller, pawn
    pawn.GetWorld, controller.GetWorld = function() return world end, function() return world end
    pawn.GetClass, controller.GetClass = function() return pawn_class end, function() return controller_class end
    pawn.IsActorBeingDestroyed, controller.IsActorBeingDestroyed = function() return false end, function() return false end
    pawn.HasAuthority = function() return false end
    pawn.IsLocallyControlled, controller.IsLocalController = function() return true end, function() return true end
    pawn.Dead, pawn.IsWeaponBlocked, controller.Loading = false, false, false
    local state = {listening = true, server = true, world = world, map = area, controller = controller,
        local_pawn = {pawn = pawn, name = pawn_name, controller_object = controller}}
    local settings = {SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_INPUT = "1", SILVERFISH_COOP_NATIVE_ZONE = "1",
        SILVERFISH_COOP_HEADLESS = "1", SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_FIXTURES = "1",
        SILVERFISH_COOP_NATIVE_GUEST_HELD_FIRE = "1", SILVERFISH_COOP_EMBEDDED_ENGINE = memory and "1" or nil}
    local writes, taps, held = {}, {{11, 2, guid, kind}}, {{4, 2, guid, "press", 4}, {5, 2, guid, "release", 4}}
    local drain, commits, full = nil, 0, memory
    local env = setmetatable({os = {getenv = function(key) return settings[key] end}}, {__index = _G})
    env.require = function(name) return modules[name] or {} end
    env.StaticFindObject = function(name) return ({[pawn_type] = pawn_class, [controller_type] = controller_class})[name] end
    env.LoopInGameThreadWithDelay = function(_, callback) drain = callback end
    env.SilverfishSetNativeGuestPrimaryScope = function() return true, "stock_held" end
    env.SilverfishPeekNativeGuestPrimaryIntent = function() return table.unpack(taps[1] or {}) end
    env.SilverfishPeekNativeGuestHeldFireIntent = function() return table.unpack(held[1] or {}) end
    env.SilverfishTakeNativeGuestPrimaryIntent = function() assert(not memory); return table.unpack(table.remove(taps, 1) or {}) end
    env.SilverfishTakeNativeGuestHeldFireIntent = function() assert(not memory); return table.unpack(table.remove(held, 1) or {}) end
    env.SilverfishReadNativeGuestHeldFire = function() end
    env.SilverfishEnqueueLocalBridgeInput = function(record)
        assert(memory)
        if full then return false end
        writes[#writes + 1] = record
        return true
    end
    env.SilverfishCommitNativeGuestPrimaryInput = function(address, sequence, channel)
        assert(memory and address == 2)
        local queue = channel == "tap" and taps or held
        assert(queue[1][1] == sequence)
        table.remove(queue, 1)
        commits = commits + 1
        return true
    end
    env.io = {open = function(_, mode)
        assert(not memory and mode == "ab")
        return {write = function(_, record) writes[#writes + 1] = record; return true end, close = function() return true end}
    end}

    local input = assert(loadfile("mod/SilverfishCoop/Scripts/native_guest_primary_input.lua", "t", env))()
    input.start("isolated", function() return false end)
    assert(input.command({"1", "native-guest-primary-scope", area, pawn_name, guid, kind}, state, "connected"))
    if memory then
        drain()
        input.sync(state, "connected")
        assert(#writes == 0 and #taps == 1 and #held == 2 and commits == 0)
        assert(#input.rows(state, "connected", table.pack) == 1)
        full = false
    end
    drain()
    input.sync(state, "connected")
    assert(#writes == 3 and #taps == 0 and #held == 0 and commits == (memory and 3 or 0))
    assert(writes[1]:match("^native%-guest%-primary%-intent\t11\t") and writes[2]:match("\tpress\t4\n$")
        and writes[3]:match("\trelease\t4\n$"))
    drain()
    input.sync(state, "connected")
    assert(#writes == 3)
    local proofs = {}
    for _, row in ipairs(input.rows(state, "connected", table.pack)) do
        if row[1] == "native-guest-primary-publication" then
            assert(row[2] == area and row[3] == pawn_name and row[4] == guid and row[7] == (memory and "memory" or "file"))
            proofs[row[5]] = row[6]
        end
    end
    assert(proofs.tap == 11 and proofs.held == 5)
end
