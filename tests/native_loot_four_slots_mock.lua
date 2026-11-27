-- Verify stock escrow preserves sparse output positions at different capacities.
os.getenv = function() return "1" end

local function object(address)
    return {IsValid = function() return true end, GetAddress = function() return address end}
end

local map, name, pawn_name = "exact lab map", "exact permanent cabinet", "exact guest"
local slot = "SFCoop_" .. ("a"):rep(32)
local scope = {profile = {path = "/Game/Stock.Cabinet", small_cabinet = true, capacity = 4}, crate = object(1), pawn = object(2),
    box = object(3), save = object(4), stock = object(5), world = object(10),
    lucky_looter = true, lucky_source = "detached", distance = 350}
local scratched, restored = object(6), object(7)
local retired, writes, claims, loads, releases = {}, 0, 0, 0, 0
local expected_outputs, expected_items = {11, 0, 33, 44}, "11,33,44"
local api, class = object(8), object(9)
api.DoesSaveGameExist = function(_, expected) assert(expected == slot); return false end
api.CreateSaveGameObject = function(_, expected) assert(expected == class); return scratched end
api.SaveGameToSlot = function(_, source, expected)
    assert(source == scratched and expected == slot)
    writes = writes + 1
    return true
end
api.LoadGameFromSlot = function(_, expected)
    assert(expected == slot)
    loads = loads + 1
    return restored
end
SilverfishRetainNativeLootEscrow = function(stock, pool) return stock == 5 and pool == 7 end
SilverfishReleaseNativeLootEscrow = function(address)
    assert(address == 7)
    releases = releases + 1
    return true
end
StaticFindObject = function(path)
    if path == "/Script/Engine.Default__GameplayStatics" then return api end
    assert(path == "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C", "unexpected cached object lookup")
    return class
end

local function results(expected, addresses)
    assert(expected == scope and table.concat(addresses, ",") == table.concat(expected_outputs, ","))
    local items = {}
    for _, address in ipairs(addresses) do
        if address > 0 then
            local actor = object(address)
            actor.SetActorHiddenInGame = function(self, hidden) self.bHidden = hidden end
            actor.SetActorEnableCollision = function(self, collision) self.collides = collision end
            actor.GetActorEnableCollision = function(self) return self.collides end
            actor.K2_DestroyActor = function() retired[#retired + 1] = address end
            items[#items + 1] = {object = actor, actor = "stock item " .. address, class_path = "/Game/Stock.Stock_C",
                transform = {address, 0, 0, 0, 0, 0, 1, 1, 1}}
        end
    end
    return items
end

package.loaded.guest_save = {}
package.loaded.native_loot = {
    profile = function(area, target) if area == map and target == name then return scope.profile end end,
    qualify = function(_, _, pawn, target, allow_distant)
        assert(pawn == pawn_name and target == name and allow_distant)
        return scope
    end,
    claim_context = function(_, _, pawn, target) assert(pawn == pawn_name and target == name); return scope end,
    results = results,
}

local ids = {("A"):rep(32), "", ("C"):rep(32), ("D"):rep(32), "", ("F"):rep(32)}
local full_width = true
SilverfishDescribeNativeLootRoll = function() error("loot.results is mocked") end
SilverfishOpenNativeLootCrate = function(path, crate, pawn, box, save, stock, trait)
    assert(path == scope.profile.path and crate == 1 and pawn == 2 and box == 3 and save == 4 and stock == 5 and trait == true)
    if full_width then
        return true, "opened", true, expected_outputs[1], expected_outputs[2] or 0, 5, true, table.unpack(expected_outputs, 3)
    end
    return true, "opened", true, 11, 0, 5, true
end
SilverfishCaptureNativeLootEscrow = function(...)
    assert(table.concat({...}, ",") == "1,6," .. table.concat(expected_outputs, ","))
    local count = 0
    for _, address in ipairs(expected_outputs) do if address > 0 then count = count + 1 end end
    return true, "captured", count, table.unpack(ids, 1, scope.profile.capacity)
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
assert(writes == 1 and table.concat(retired, ",") == expected_items)

actions.command({"id", "native-loot-claim", pawn_name, name, ids[4], slot}, state, "hosting")
assert(rows()[1][7] == "claimed" and claims == 1 and loads == 1)
scope.world = object(12)
actions.command({"id", "native-loot-claim", pawn_name, name, ids[4], slot}, state, "hosting")
assert(rows()[1][7] == "claimed" and claims == 2 and releases == 1 and loads == 2,
    "Changing worlds releases the old retained pool and reloads the durable slot")

full_width = false
actions.command({"id", "native-loot-open", pawn_name, name, slot}, state, "hosting")
report = rows()
assert(report[1][6] == 0 and report[1][7] == "mutation_unverified_native_loot_unexpected_child_output" and report[1][14] == 1)
assert(writes == 1 and #retired == 3)

full_width, retired = true, {}
scope.profile.capacity = 6
expected_outputs, expected_items = {11, 0, 33, 44, 0, 66}, "11,33,44,66"
actions.command({"id", "native-loot-open", pawn_name, name, slot}, state, "hosting")
report = rows()
assert(report[1][6] == 1 and report[1][13] == 4 and report[5][4] == ids[6])
assert(writes == 2 and table.concat(retired, ",") == expected_items)

retired = {}
scope.profile.capacity = 1
expected_outputs, expected_items = {11}, "11"
actions.command({"id", "native-loot-open", pawn_name, name, slot}, state, "hosting")
report = rows()
assert(report[1][6] == 1 and report[1][13] == 1 and report[2][4] == ids[1])
assert(writes == 3 and table.concat(retired, ",") == expected_items)
print("Stock escrow preserves one, four, and six slots and refuses a truncated native reply")
