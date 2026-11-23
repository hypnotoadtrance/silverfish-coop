package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local source = "crossroads-brown-mushroom"
local pawn = {IsValid = function() return true end, GetAddress = function() return 101 end}
local world = {IsValid = function() return true end, GetAddress = function() return 102 end}
local state = {map = crossroads, world = world, server = false, listening = true,
    local_pawn = {pawn = pawn, name = "Stock player"}}
local env = setmetatable({
    os = {getenv = function(name) return name == "SILVERFISH_COOP_NATIVE_ZONE" and "1" or nil end},
    SilverfishVerifyNativeStashSave = function() error("status must not mutate the inventory") end,
}, {__index = _G})
local pickup = assert(loadfile("mod/SilverfishCoop/Scripts/native_authored_pickup.lua", "t", env))()

local function observe(phase)
    local rows = {}
    for _, row in ipairs(pickup.rows(state, phase, table.pack)) do
        assert(not rows[row[1]], "duplicate status row: " .. row[1])
        rows[row[1]] = row
    end
    return rows
end

local function reporting_drop(shown, epoch, fault)
    env.SilverfishObserveNativeCrossroadsDeadDrop = function(address, host)
        assert(address == pawn:GetAddress() and host == not state.server)
        return shown, epoch, fault
    end
end

local function hosting()
    local rows = observe("hosting")
    assert(rows["native-authored-pickup-memory"] and rows["native-authored-pickup-memory"][2] == 1,
        "Dead Drop must not remove the host's memory pickup capability")
    return rows
end

local unavailable = hosting()
assert(unavailable["native-dead-drop-diagnostic"][3] == "native_observer_unavailable")
reporting_drop("diagnostic", "-", "canonical_quest_inactive")
local diagnostic = hosting()
assert(diagnostic["native-dead-drop-diagnostic"][3] == "canonical_quest_inactive")
reporting_drop(nil)
local unsettled = hosting()
assert(not unsettled["native-dead-drop"] and not unsettled["native-dead-drop-diagnostic"])

env.SilverfishSetNativeAuthoredPickupScope = function(address, host, requested)
    assert(address == pawn:GetAddress() and host == not state.server)
    return requested == source
end
env.SilverfishReadNativeAuthoredPickup = function(requested)
    assert(requested == source)
    return true, false, 3, false
end
for _, shown in ipairs({"hidden", "stock-active"}) do
    reporting_drop(shown, "-")
    local rows = hosting()
    assert(rows["native-dead-drop"][2] == shown and rows["native-dead-drop"][3] == crossroads)
    assert(rows["native-authored-pickup"][2] == source and rows["native-authored-pickup"][6] == "available")
    assert(rows["native-authored-pickup-interaction"][2] == source
        and rows["native-authored-pickup-interaction"][6] == "stock")
end

state.server = true
local epoch = string.rep("1", 32)
reporting_drop("projected", epoch)
local guest = observe("connected")
assert(not guest["native-authored-pickup-memory"] and guest["native-dead-drop"][6] == epoch)
print("Stock Dead Drop reporting preserves the host's memory pickup capability.")
