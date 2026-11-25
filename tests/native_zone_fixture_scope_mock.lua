-- Run from the repository root with the UE4SS Lua interpreter.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local dead_bridge = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local real_getenv = os.getenv
os.getenv = function(name)
    if name == "SILVERFISH_COOP_NATIVE_ZONE" or name == "SILVERFISH_COOP_NATIVE_EXPANDED_ZONES"
        or name == "SILVERFISH_COOP_EMBEDDED_ENGINE" then return "1" end
    return real_getenv(name)
end

package.loaded.zone_wall = {
    start = function() end,
    clear = function() end,
    sync = function() end,
    rows = function() return {} end,
    placed_actor = function(_, class, name)
        for _, actor in ipairs(FindAllOf(class)) do
            if actor:GetFullName():match(":PersistentLevel%.(.+)$") == name then return actor end
        end
    end,
}

local function object(address, name, world)
    return {
        address = address,
        name = name,
        world = world,
        IsValid = function() return true end,
        GetAddress = function(self) return self.address end,
        GetFullName = function(self) return self.name end,
        GetWorld = function(self) return self.world end,
    }
end

local world = object(1)
local pawn = object(2, "Player_FP_C", world)
local function exit_name(map, name)
    return "Level_Exit_BP_C " .. map .. ":PersistentLevel." .. name
end

local old_exit = object(3, exit_name(dead_bridge, "Level_Exit_BP_C_3"), world)
local crossroads_exit = object(4, exit_name(crossroads, "Level_Exit_BP_C_275"), world)
local available = {old_exit}
local expected = old_exit.address
local last_address
local cleared_scopes = 0
FindAllOf = function(class)
    assert(class == "Level_Exit_BP_C")
    return available
end
SilverfishSetNativeZoneScope = function(_, _, address)
    last_address = address
    if address == expected then return true end
    return false, "The cached exit no longer identifies a live level exit"
end
SilverfishClearNativeZoneScope = function() cleared_scopes = cleared_scopes + 1 end
SilverfishTakeNativeZoneEvent = function() end

local zone = require("native_zone")
zone.start("unused")
local state = {map = dead_bridge, local_pawn = {pawn = pawn}, guests = {}, clients = 0}
zone.sync(state, "connected")
assert(last_address == old_exit.address)

-- Lua still sees a valid wrapper at the old address, now in the new world.
old_exit.name = "StaticMeshActor " .. crossroads .. ":PersistentLevel.StaticMeshActor_1"
state.map = crossroads
available = {crossroads_exit}
expected = crossroads_exit.address
zone.sync(state, "connected")
assert(last_address == crossroads_exit.address and cleared_scopes == 1)
assert(zone.rows(state, "connected", function(...) return {...} end)[1][2] == "ready")

-- A native rejection should refresh the same-map candidate on the next sync.
local replacement = object(5, crossroads_exit.name, world)
available = {replacement}
expected = replacement.address
zone.sync(state, "connected")
assert(zone.rows(state, "connected", function(...) return {...} end)[1][2] == "arming")
zone.sync(state, "connected")
assert(last_address == replacement.address)
assert(zone.rows(state, "connected", function(...) return {...} end)[1][2] == "ready")

-- An exit stays latched when the resident command queue is full.
local event, authored, attempts, accepted, releases = nil, nil, 0, {}, 0
local now, queue_ready = 100, false
os.time = function() return now end
io.open = function() error("Resident exit requests must stay in memory") end
SilverfishReadLocalNativeZoneHeartbeat = function() return true, now end
SilverfishTakeNativeZoneEvent = function()
    local taken = event
    event = nil
    return taken
end
SilverfishTakeNativeAuthoredRoute = function()
    local taken = authored
    authored = nil
    if taken then return table.unpack(taken, 1, 7) end
end
SilverfishReleaseNativeZoneRequest = function() releases = releases + 1 end
SilverfishEnqueueLocalBridgeInput = function(record)
    attempts = attempts + 1
    if not queue_ready then return false end
    accepted[#accepted + 1] = record
    return true
end
local function zone_status()
    zone.sync(state, "hosting")
    return zone.rows(state, "hosting", function(...) return {...} end)[1]
end

authored = {crossroads, dead_bridge, "DB_CR", "Level_Exit_BP_C_275", "overlap", nil, 71}
assert(zone_status()[3] == "request_pending")
assert(zone_status()[4] == 0 and attempts == 2 and releases == 0)
queue_ready = true
assert(zone_status()[4] == 1 and #accepted == 1 and releases == 0)
assert(accepted[1] == table.concat({"authored-request", "1", crossroads, dead_bridge, "DB_CR",
    "Level_Exit_BP_C_275", "overlap", "-", "71"}, "\t") .. "\n")
assert(zone_status()[4] == 1 and #accepted == 1)

queue_ready, event = false, "request"
assert(zone_status()[3] == "request_pending")
now = now + 3
queue_ready = true
assert(zone_status()[3] == "request_unavailable" and #accepted == 1 and releases == 1)
event = "request"
assert(zone_status()[4] == 2 and #accepted == 2)
assert(accepted[2] == table.concat({"request", "2", crossroads, dead_bridge, "DB_CR"}, "\t") .. "\n")

queue_ready, event = false, "request"
assert(zone_status()[3] == "request_pending")
state.local_pawn.pawn = object(6, "Player_FP_C", world)
queue_ready = true
assert(zone_status()[3] == "request_unavailable" and #accepted == 2 and releases == 2)
print("verified exit scope replacement, resident retry and stale request release")
