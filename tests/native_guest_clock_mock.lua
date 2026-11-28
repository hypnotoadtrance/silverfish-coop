local filename = "mod/SilverfishCoop/Scripts/native_guest_clock.lua"
local area = "/Game/Test/Map"
local next_address = 0
local function object(fields)
    next_address = next_address + 1
    fields.address = next_address
    fields.GetAddress = function(self) return self.address end
    fields.IsValid = function() return true end
    return fields
end

local world = object({GetFullName = function() return "World " .. area .. ".Map" end})
local save = object({})
local instance = object({SaveSlot_Ref = save})
local connection = object({})
local driver = object({World = world, ServerConnection = connection})
world.NetDriver, world.OwningGameInstance, connection.Driver = driver, instance, driver
local pawn_class, controller_class, widget = object({}), object({}), object({})
local name, name_reads = "Player_FP_C " .. area .. ".Map:PersistentLevel.Player", 0
local function local_actor(class)
    return object({GetClass = function() return class end, GetWorld = function() return world end,
        HasAuthority = function() return false end, IsActorBeingDestroyed = function() return false end})
end
local pawn, controller = local_actor(pawn_class), local_actor(controller_class)
pawn.Controller, pawn.Save, controller.Pawn, controller.Loading = controller, save, pawn, false
pawn.IsLocallyControlled, controller.IsLocalController = function() return true end, function() return true end
pawn.GetFullName = function() name_reads = name_reads + 1; return name end
local state = {map = area, world = world, listening = true, server = true, controller = controller,
    local_pawn = {pawn = pawn, controller_object = controller, name = name}}

local verified, checks, cleared = true, 0, 0
local types = {Player_FP_C = pawn_class, PlayerControler_FP_C = controller_class, Time_Location_Header_Widget_C = widget}
local env = setmetatable({
    os = {getenv = function() return "1" end},
    require = function() return {leaf = function(path) return path:match("[^/]+$") end} end,
    StaticFindObject = function(path) return types[path:match("[^.]+$")] end,
    SilverfishVerifyNativeGuestClockSeat = function(w, p, c, i, n, sequence)
        assert(w == world.address and p == pawn.address and c == controller.address and i == instance.address
            and n == connection.address and sequence == 1)
        checks = checks + 1
        return verified or nil
    end,
    SilverfishSetNativeGuestClock = function() return true, "armed" end,
    SilverfishReadNativeGuestClock = function() return 1, 42000, 0 end,
    SilverfishClearNativeGuestClock = function() cleared = cleared + 1 end,
}, {__index = _G})
local clock = assert(loadfile(filename, "t", env))()
local arguments = {"request", "native-guest-clock-apply", "epoch", "1", area, "42000"}
local function arm()
    assert(clock.command(arguments, state, "connected") == "authenticated guest clock projection armed")
end
local function row(...)
    return table.concat({...}, "\t")
end

arm()
clock.sync(state, "connected")
assert(checks == 1 and name_reads == 1 and cleared == 0)
assert(#clock.rows(state, "connected", row) == 1)

verified = false
clock.sync(state, "connected")
assert(checks == 2 and name_reads == 2 and cleared == 0)
pawn.Save = object({})
clock.sync(state, "connected")
assert(cleared == 1 and #clock.rows(state, "connected", row) == 0)

pawn.Save = save
arm()
verified = true
state.map = "/Game/Test/Travel"
clock.sync(state, "connected")
assert(cleared == 2 and checks == 3 and #clock.rows(state, "connected", row) == 0)

-- A native renewal advances the clock without replacing the Lua lease's original sample.
state.map = area
local sequence, milliseconds, hits, epoch = 1, 42000, 0, "epoch"
local label_class = object({})
local label = object({
    GetClass = function() return label_class end,
    GetText = function()
        return {ToString = function() return "DAY 0  00:22" end}
    end,
})
local stock_widget = object({
    GetClass = function() return widget end,
    GetOwningPlayer = function() return controller end,
    IsInViewport = function() return false end,
    TextBlock_Name = label,
})
types.TextBlock = label_class
types.Default__WidgetBlueprintLibrary = object({
    Create = function(_, owner, class, player)
        assert(owner == pawn and class == widget and player == controller)
        return stock_widget
    end,
})
env.SilverfishSetNativeGuestClock = function(p, c, w, seq, time, visit, map, identity)
    assert(p == pawn.address and c == controller.address and w == world.address
        and seq == sequence and time == milliseconds and visit == epoch and map == area and identity == name)
    return true, "armed", true
end
env.SilverfishReadNativeGuestClock = function(address)
    assert(address == pawn.address)
    return sequence, milliseconds, hits, hits > 0 and 42000 or nil, epoch
end
env.SilverfishProbeNativeGuestClock = function(address)
    assert(address == stock_widget.address and stock_widget.GameInstance == instance)
    hits = hits + 1
    return true, "stock_widget_tick_verified"
end

arm()
sequence, milliseconds = 2, 1326000
local fixture_command = {"request", "native-guest-clock-fixture"}
assert(clock.command(fixture_command, state, "connected") == "stock guest time widget verified")
local rows = clock.rows(state, "connected", row)
assert(rows[1] == row("native-guest-clock", area, name, epoch, 2, 1326000, "projected", 1, 42000))
assert(rows[2] == row("native-guest-clock-fixture", area, name, epoch, 2, 1326000, 1, "DAY 0  00:22"))

epoch = "different_visit"
local accepted, reason = pcall(clock.command, fixture_command, state, "connected")
assert(not accepted and reason == "guest_clock_fixture_scope_unavailable" and hits == 1)
