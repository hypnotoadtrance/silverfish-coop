-- Verify the cabinet's four-slot native bridge preserves nil-free output positions.
os.getenv = function() return "1" end

local function object(address)
    return {IsValid = function() return true end, GetAddress = function() return address end}
end

local map, name, pawn_name = "exact lab map", "exact permanent cabinet", "exact guest"
local slot = "SFCoop_" .. ("a"):rep(32)
local scope = {profile = {small_cabinet = true, capacity = 4}, crate = object(1), pawn = object(2),
    box = object(3), save = object(4), stock = object(5), lucky_looter = true, lucky_source = "detached", distance = 105}
local scratched, restored = object(6), object(7)
local retired, writes, claims = {}, 0, 0
local api, class = object(8), object(9)
api.DoesSaveGameExist = function(_, expected) assert(expected == slot); return false end
api.CreateSaveGameObject = function(_, expected) assert(expected == class); return scratched end
api.SaveGameToSlot = function(_, source, expected)
    assert(source == scratched and expected == slot)
    writes = writes + 1
    return true
end
api.LoadGameFromSlot = function(_, expected) assert(expected == slot); return restored end
StaticFindObject = function(path)
    if path == "/Script/Engine.Default__GameplayStatics" then return api end
    return class
end

local function results(_, _, expected, addresses)
    assert(expected == scope and table.concat(addresses, ",") == "11,33,44")
    local items = {}
    for _, address in ipairs(addresses) do
        local actor = object(address)
        actor.SetActorHiddenInGame = function(self, hidden) self.bHidden = hidden end
        actor.SetActorEnableCollision = function(self, collision) self.collides = collision end
        actor.GetActorEnableCollision = function(self) return self.collides end
        actor.K2_DestroyActor = function() retired[#retired + 1] = address end
        items[#items + 1] = {object = actor, actor = "stock item " .. address, class_path = "/Game/Stock.Stock_C"}
    end
    return items
end

package.loaded.native_loot = {
    profile = function(area, target) if area == map and target == name then return scope.profile end end,
    qualify = function(_, _, pawn, target) assert(pawn == pawn_name and target == name); return scope end,
    claim_context = function(_, _, pawn, target) assert(pawn == pawn_name and target == name); return scope end,
    results = results,
}

local ids = {("A"):rep(32), "", ("C"):rep(32), ("D"):rep(32)}
local full_width = true
SilverfishOpenNativeLootCrate = function(crate, pawn, box, save, stock, trait)
    assert(crate == 1 and pawn == 2 and box == 3 and save == 4 and stock == 5 and trait == true)
    if full_width then return true, "opened", true, 11, 0, 5, true, 33, 44 end
    return true, "opened", true, 11, 0, 5, true
end
SilverfishCaptureNativeLootEscrow = function(...)
    assert(table.concat({...}, ",") == "1,6,11,0,33,44")
    return true, "captured", 3, table.unpack(ids)
end
SilverfishVerifyNativeLootEscrow = function(...)
    assert(table.concat({...}, ",") == "6,7,1" and #retired == 0)
    return true
end
SilverfishClaimNativeLootEscrow = function(pawn, save, stock, escrow, guid, ...)
    assert(pawn == 2 and save == 4 and stock == 5 and escrow == 7 and guid == ids[4])
    assert(select("#", ...) == 1 and (...) == 1)
    claims = claims + 1
    return true, "claimed"
end

local actions = assert(loadfile("mod/SilverfishCoop/Scripts/native_loot_actions.lua"))()
local state = {map = map}
local function rows()
    return actions.rows(state, "hosting", function(...) return {...} end)
end

actions.command({"id", "native-loot-open", pawn_name, name, slot}, state, "hosting")
local report = rows()
assert(report[1][6] == 1 and report[1][13] == 3 and report[1][15] == slot)
assert(report[2][4] == ids[1] and report[3][4] == ids[3] and report[4][4] == ids[4])
assert(writes == 1 and table.concat(retired, ",") == "11,33,44")

actions.command({"id", "native-loot-claim", pawn_name, name, ids[4], slot}, state, "hosting")
assert(rows()[1][7] == "claimed" and claims == 1)

full_width = false
actions.command({"id", "native-loot-open", pawn_name, name, slot}, state, "hosting")
report = rows()
assert(report[1][6] == 0 and report[1][7] == "mutation_unverified_native_loot_unverified_child_output" and report[1][14] == 1)
assert(writes == 1 and #retired == 3)
print("Exact cabinet escrow preserves all slot positions and refuses a truncated native reply")
