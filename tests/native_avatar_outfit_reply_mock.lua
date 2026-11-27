package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
package.loaded.guest_save = {}

local avatar = require("native_player_avatar")
local world = {GetAddress = function(self) return self end}
local state = {world = world, map = "/Game/Maps/Camp", local_pawn = {name = "Player_FP_C guest"}, guests = {}}
local digest = string.rep("a", 32)
local fields = {"ticket", "native-avatar-outfits", state.map, digest, "3", "-", "-", "-", "-", "-", "-", "0", "-"}

local function row(...)
    return table.concat({...}, "\t")
end

local sync, calls = avatar.sync, 0
function avatar.sync(scoped_state, phase)
    assert(scoped_state == state and phase == "connected")
    calls = calls + 1
    sync(scoped_state, phase)
end

local function reply()
    return avatar.outfit_reply(fields, state, "connected", 11, row)
end

local header = row("native-avatar-outfits-reply", 1, "ticket", "connected", state.map, state.local_pawn.name, 11)
assert(reply() == header .. row("", digest, "accepted", "Remote avatar outfits received") .. "\n")
assert(calls == 1 and avatar.rows(row)[1] == row("native-avatar-outfits-receipt", digest))

fields[3] = "/Game/Maps/Depot"
assert(reply():find(header .. row("", "-", "error"), 1, true) == 1 and calls == 1)
assert(avatar.rows(row)[1] == row("native-avatar-outfits-receipt", digest))

fields[3] = state.map
avatar.sync = function() error("fixture sync failure") end
local failed = reply()
assert(failed:find(header .. row("", "-", "error"), 1, true) == 1)
assert(failed:find("fixture sync failure", 1, true))
print("Outfit receipt verifies synchronous projection and rejects mismatched world or failed sync")
