package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local source = "crossroads-brown-mushroom"
local world = {IsValid = function() return true end, GetAddress = function() return 102 end}
local pawn = {IsValid = function() return true end, GetAddress = function() return 101 end,
    GetWorld = function() return world end}
local state = {map = crossroads, world = world, server = false, listening = true,
    local_pawn = {pawn = pawn, name = "Stock player"}}
local env = setmetatable({
    os = {getenv = function(name) return name == "SILVERFISH_COOP_NATIVE_ZONE" and "1" or nil end,
        clock = function() return 1 end},
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
    env.SilverfishObserveNativeDeadDrop = function(address, host)
        assert(address == pawn:GetAddress() and host == not state.server)
        if shown == "diagnostic" then return shown, epoch, fault end
        return shown, epoch, 1, 2, 3, 0, 0, 0, 1, 1, 1
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

local batch_calls = 0
env.SilverfishSampleNativeAuthoredPickups = function(address, host, ...)
    assert(address == pawn:GetAddress() and host)
    batch_calls = batch_calls + 1
    local result = {}
    for _, id in ipairs({...}) do
        local values = id == source and {"", true, true, 7, true, 0}
            or {"authored_pickup_stock_actor_identity_unqualified", false, false, 0, false, 0}
        for _, field in ipairs(values) do result[#result + 1] = field end
    end
    return table.unpack(result)
end
local bind_source, read_source = env.SilverfishSetNativeAuthoredPickupScope, env.SilverfishReadNativeAuthoredPickup
env.SilverfishSetNativeAuthoredPickupScope = function() error("batch should replace scalar binding") end
env.SilverfishReadNativeAuthoredPickup = function() error("batch should replace scalar read") end
local batched = hosting()
assert(batch_calls == 1 and batched["native-authored-pickup"][6] == "claimed")
assert(batched["native-authored-pickup-interaction"][5] == 7 and batched["native-authored-pickup-interaction"][6] == "fixture")
env.SilverfishSetNativeAuthoredPickupScope, env.SilverfishReadNativeAuthoredPickup = bind_source, read_source
env.SilverfishSampleNativeAuthoredPickups = function() return "incomplete" end
assert(hosting()["native-authored-pickup"][6] == "available", "A refused batch must fall back to live scalar status")
env.SilverfishSampleNativeAuthoredPickups = nil

local projected_path = crossroads .. ".CrossRoads_P:PersistentLevel.ProjectedLoot_1"
local actor_name = "Item_Master_BP_C " .. projected_path
local projected = {
    IsValid = function() return true end,
    GetAddress = function() return 104 end,
    GetFullName = function() return actor_name end,
    GetWorld = function() return world end,
    IsActorBeingDestroyed = function() return false end,
    bReplicates = true,
}
world.PersistentLevel = {IsValid = function() return true end, GetAddress = function() return 103 end}
projected.GetOuter = function() return world.PersistentLevel end
local mesh_visible = true
local mesh = {
    IsValid = function() return true end,
    GetOwner = function() return projected end,
    IsVisible = function() return mesh_visible end,
    SetVisibility = function(_, visible) mesh_visible = visible end,
    bHiddenInGame = false,
}
projected.K2_GetComponentsByClass = function() return {mesh} end
env.StaticFindObject = function(name)
    if name == projected_path or name == "/Script/Engine.MeshComponent" then return projected end
end
local target = "world-loot:" .. ("a"):rep(32) .. ":" .. ("b"):rep(32) .. ":pickup"
pickup.preview_input(pawn, target, actor_name)
assert(mesh_visible, "A host preview cannot hide a replicated actor")
projected.bReplicates = false
pickup.preview_input(pawn, target, actor_name)
assert(not mesh_visible, "A host preview should hide a projected loot mesh")
local cancel = {"test", "native-pickup-cancel-preview", target}
assert(not pcall(pickup.command, cancel, state, "connected") and not mesh_visible)
assert(pickup.command(cancel, state, "hosting") == "pickup preview released" and mesh_visible)

state.server = true
local epoch = string.rep("1", 32)
reporting_drop("projected", epoch)
local guest = observe("connected")
assert(not guest["native-authored-pickup-memory"] and guest["native-dead-drop"][6] == epoch)
local authored_binds = 0
env.SilverfishDiscoverLoosePickups = function(address, host)
    assert(address == pawn:GetAddress() and host == false)
    return 12, "", 5
end
local path = crossroads .. ".CrossRoads_P:PersistentLevel.Item_BP_C_1"
local applied = 0
env.SilverfishSetGuestLoosePickupView = function(generation, hidden, actor)
    assert(generation == 5 and hidden == true and actor == path)
    applied = applied + 1
    return true
end
pickup.refresh_loose(function() return world end)
local nearby = table.concat({"native-loose-pickup", crossroads, path, "Stock item", "2", "available",
    "10.000", "20.000", "30.000"}, "\t")
env.SilverfishNearbyLoosePickupStatusRows = function(map)
    assert(map == crossroads)
    return nearby
end
env.SilverfishSetNativeAuthoredPickupScope = function() return false end
local snapshot = table.concat(pickup.rows(state, "connected", function(...) return table.concat({...}, "\t") end), "\n")
assert(snapshot:sub(-#nearby) == nearby and snapshot:find("native-loose-pickup-view-scope\t5\n", 1, true))
env.SilverfishNearbyLoosePickupStatusRows = nil
env.SilverfishSetNativeAuthoredPickupScope = function()
    authored_binds = authored_binds + 1
    error("a loose pickup must not register unrelated authored sources")
end
local command = {"test", "native-loose-pickup-project", "5", "hide", path}
assert(pickup.command(command, state, "connected") == "shared level item visibility synchronized")
command[3] = "4"
assert(not pcall(pickup.command, command, state, "connected"))
assert(applied == 1 and authored_binds == 0)
print("Stock Dead Drop reporting and exact loose pickup projection passed.")
