local function object(address)
    return {IsValid = function() return true end, GetAddress = function() return address end}
end

local world, pawn, driver, connection, instance, save = object(1), object(2), object(3), object(4), object(5), object(6)
world.NetDriver, driver.ServerConnection, instance.SaveSlot_Ref, pawn.Save = driver, connection, save, save
local map = "/Game/Classes/Maps/Test"
local state = {world = world, server = true, map = map, local_pawn = {pawn = pawn}}
local native_reads, lua_reads = 0, 0
local stock_clock = 12
local expected = {1, 2, 3, 4, 5, 6}
local env = setmetatable({require = function() return {} end}, {__index = _G})
local view = assert(loadfile("mod/SilverfishCoop/Scripts/native_world_view.lua", "t", env))()
local lease = {context = {profile = {map = map}, world = world, pawn = pawn, driver = driver,
    connection = connection, instance = instance, save = save, day = {UpdateInRealTime = false},
    statics = {GetRealTimeSeconds = function(_, source)
        assert(source == pawn)
        lua_reads = lua_reads + 1
        return stock_clock
    end}}, expires = 15, native_clock_args = expected, status_recheck = 14, status_report = {sequence = 4}}

local function upvalue(fn, name, replace)
    for index = 1, 32 do
        local key, value = debug.getupvalue(fn, index)
        if key == name then
            if replace then debug.setupvalue(fn, index, replace) end
            return value
        end
    end
    error("Missing upvalue " .. name)
end

upvalue(view.status, "lease", lease)
local lease_time = upvalue(upvalue(view.sync, "lease_current"), "lease_time")
local native_clock = 11
env.SilverfishReadNativeWeatherLeaseTime = function(...)
    native_reads = native_reads + 1
    assert(select("#", ...) == #expected)
    for index, address in ipairs(expected) do assert(select(index, ...) == address) end
    if native_clock == "error" then error("Native reader failure") end
    return native_clock
end

assert(lease_time(state, "connected") == 11 and native_reads == 1 and lua_reads == 0)
assert(view.status(state, "connected") == lease.status_report and native_reads == 2 and lua_reads == 0)
assert(not lease_time(state, "hosting") and native_reads == 2)
state.world = object(7)
assert(not lease_time(state, "connected") and native_reads == 2)
state.world, state.local_pawn.pawn = world, object(8)
assert(not lease_time(state, "connected") and native_reads == 2)
state.local_pawn.pawn = pawn
native_clock = 15
assert(not lease_time(state, "connected") and native_reads == 3 and lua_reads == 0,
    "An exact clock at the deadline expires the lease without fallback")

native_clock = nil
assert(lease_time(state, "connected") == 12 and lua_reads == 1)
native_clock = "error"
assert(lease_time(state, "connected") == 12 and lua_reads == 2)
native_clock = 0 / 0
assert(lease_time(state, "connected") == 12 and lua_reads == 3)
driver.ServerConnection = object(9)
assert(not lease_time(state, "connected") and lua_reads == 3, "A replacement connection invalidates the old lease")
driver.ServerConnection, instance.SaveSlot_Ref = connection, object(10)
assert(not lease_time(state, "connected") and lua_reads == 3, "A replacement canonical save invalidates the old lease")
instance.SaveSlot_Ref = save
stock_clock = 16
assert(not lease_time(state, "connected") and lua_reads == 4, "The stock clock enforces the same expiration")
env.SilverfishReadNativeWeatherLeaseTime = nil
stock_clock = 13
assert(lease_time(state, "connected") == 13 and lua_reads == 5)

local components = upvalue(upvalue(view.sync, "lease_current"), "lease_components_current")
local day, north, sun, light = object(11), object(12), object(13), object(14)
local profile = lease.context.profile
profile.day = "DayCycle_SunControler_BP_C /Game/Classes/Maps/Test.Test:PersistentLevel.DayCycle_SunControler_BP_C_0"
day.GetWorld = function() return world end
day.GetFullName = function() return profile.day end
day.IsActorBeingDestroyed = function() return false end
day.bReplicates, day["Game Instance"] = false, instance
day.Arrow_North, day.Arrow_Sun, day.DLight_Sun = north, sun, light
north.GetOwner = function() return day end
sun.GetAttachParent = function() return north end
light.GetAttachParent = function() return sun end
lease.context.day, lease.context.north, lease.context.sun, lease.context.light = day, north, sun, light
lease.native_component_args = {1, 5, profile, 11, 12, 13, 14}
local native_checks = 0
local native_result = true
env.SilverfishVerifyNativeGuestWeatherComponents = function(...)
    assert(select("#", ...) == #lease.native_component_args)
    for index, identity in ipairs(lease.native_component_args) do assert(select(index, ...) == identity) end
    native_checks = native_checks + 1
    return native_result
end
local old_name = day.GetFullName
day.GetFullName = function() error("Native success should bypass the wrapper-heavy scene read") end
assert(components() and native_checks == 1)
day.GetFullName, native_result = old_name, nil
assert(components() and native_checks == 2, "Native refusal allows an equivalent Lua qualification")
day.DLight_Sun = object(15)
assert(not components() and native_checks == 3, "A replaced light must invalidate a refused native lease")

print("Weather lease verifies role, current scope, clock fallback, component fallback, and expiration.")
