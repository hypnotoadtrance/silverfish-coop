-- Run from the repository root with the UE4SS Lua interpreter.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local dead_bridge = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local real_getenv = os.getenv
os.getenv = function(name)
    if name == "SILVERFISH_COOP_NATIVE_ZONE" then return "1" end
    return real_getenv(name)
end

package.loaded.zone_wall = {
    start = function() end,
    clear = function() end,
    sync = function() end,
    rows = function() return {} end,
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
