-- Invoke from the repository root with the pinned Lua CLI.
local area, pawn = "/Game/Exact", "Player_FP_C /Game/Exact.Scene:PersistentLevel.Player_FP_C_4"
local reads, contents, attached = 0, "100\r\n", true
local environment = setmetatable({
    io = {open = function(path, mode)
        assert(path == "legacy" and mode == "rb")
        reads = reads + 1
        return {read = function(_, limit) assert(limit == 32); return contents end, close = function() end}
    end}
}, {__index = _G})
local lease = assert(loadfile("mod/SilverfishCoop/Scripts/guest_interaction_lease.lua", "t", environment))()
local function stamp(feature, now, requested_area, requested_pawn, strict)
    return lease.timestamp("legacy", feature, requested_area or area, requested_pawn or pawn, now, strict)
end

assert(stamp(lease.shop, 101, nil, nil, true) == 100 and reads == 1)
environment.SilverfishReadLocalGuestInteractionLease = function(current_area, current_pawn)
    if not attached or current_area ~= area or current_pawn ~= pawn then return attached end
    return true, 100, lease.shop | lease.dialogue
end
assert(stamp(lease.shop, 102) == 100 and stamp(lease.dialogue, 100) == 100)
assert(stamp(lease.broker, 101) == nil and stamp(lease.mosby, 101) == nil)
assert(stamp(lease.shop, 99) == nil and stamp(lease.shop, 103) == nil)
assert(stamp(lease.shop, 101, "/Game/Other") == nil and stamp(lease.shop, 101, nil, "replacement") == nil)
assert(reads == 1, "An attached worker must never accept a fallback file for a missing native scope")
attached = false
assert(stamp(lease.dialogue, 101) == nil and reads == 1, "Detachment must not revive the fallback file")
lease = assert(loadfile("mod/SilverfishCoop/Scripts/guest_interaction_lease.lua", "t", environment))()
assert(stamp(lease.dialogue, 101) == 100 and reads == 2)
contents = "1e2"
assert(stamp(lease.broker, 101, nil, nil, true) == nil and stamp(lease.mosby, 101) == 100)
print("verified scoped mixed guest interaction lease, expiry and exact legacy parser fallback")
