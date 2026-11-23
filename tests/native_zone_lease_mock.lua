-- Invoke from the repository root with the pinned Lua CLI.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local now, legacy_stamp = 100, "99\r\n"
local map = "/Game/Classes/Maps/First"
local pawn = "Player_FP_C " .. map .. ".First:PersistentLevel.Player_FP_C_7"
local roster = map .. "\n" .. pawn .. "\tguest2\tFern\n"
local reads, roster_reads = 0, 0
os.time = function() return now end
os.getenv = function(name) if name == "SILVERFISH_COOP_HEADLESS" then return "1" end end
io.open = function(path, mode)
    assert(mode == "rb")
    reads = reads + 1
    return {read = function(_, limit)
        if path == "bridge/native-zone-heartbeat" then assert(limit == 32); return legacy_stamp end
        assert(path == "bridge/native-zone-roster" and limit == 8192)
        return roster
    end, close = function() end}
end

local lease = require("native_zone_lease")
local regroup = require("zone_regroup")
local heartbeat = "bridge/native-zone-heartbeat"
regroup.start("bridge")
local function label(point, expected_role, expected_name)
    local blockers = {{pawn = pawn, slot = "guest3", meters = 26}}
    regroup.show({map = point}, "route", blockers)
    assert(#blockers == 1 and blockers[1].pawn == pawn and blockers[1].meters == 26)
    assert(blockers[1].role == expected_role and blockers[1].name == expected_name)
end

assert(lease.fresh(heartbeat))
label(map, "guest2", "Fern")
legacy_stamp = "0000000000100"
assert(not lease.fresh(heartbeat))
local legacy_reads = reads

local attached, stamp, current_roster = true, 100, roster
SilverfishReadLocalNativeZoneHeartbeat = function() return attached, stamp end
SilverfishReadLocalNativeZoneRoster = function()
    roster_reads = roster_reads + 1
    return attached, current_roster
end
assert(lease.fresh(heartbeat))
now = 102
assert(lease.fresh(heartbeat))
now = 103
assert(not lease.fresh(heartbeat) and roster_reads == 0)
stamp = 104
assert(not lease.fresh(heartbeat))
stamp = 103
assert(lease.fresh(heartbeat))
label(map, "guest2", "Fern")
label("/Game/Classes/Maps/Another", "guest3", "Guest 3")
current_roster = nil
label(map, "guest3", "Guest 3")
attached, legacy_stamp = false, "103"
assert(not lease.fresh(heartbeat))
label(map, "guest3", "Guest 3")
assert(reads == legacy_reads, "The local worker must never consume stale legacy files")
print("verified independent zone timestamp and exact regroup labels in memory and legacy modes")
