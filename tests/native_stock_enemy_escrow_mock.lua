-- Run with Lua 5.4 from the repository root.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
os.getenv = function(name)
    if name == "SILVERFISH_COOP_NATIVE_LOOT" then return "1" end
end

local function object(address, name)
    return {
        GetAddress = function() return address end,
        GetFullName = function() return name end,
        IsValid = function() return true end,
    }
end

local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local actor_path = map .. ".CrossRoads_P:PersistentLevel.StockAmmo_C_5"
local item_class = "/Game/Classes/Items/Ammo/StockAmmo.StockAmmo_C"
local world, instance, stock = object(1), object(2), object(3)
local host, guest, save = object(4, "host"), object(5, "guest"), object(6)
local actor = object(7, "StockAmmo_C " .. actor_path)
local class, transient, api = object(8), object(9), object(10)
local first_pool, second_pool, third_pool = object(11), object(12), object(13)
local pools, next_pool = {first_pool, second_pool, third_pool}, 0
host.GetWorld, guest.GetWorld = function() return world end, function() return world end
host.Save, guest.Save, guest.Dead = stock, save, false
actor.bReplicates, actor.bHidden, actor.collides = false, false, true
actor.K2_GetActorLocation = function() return {X = 1, Y = 2, Z = 3} end
actor.K2_GetActorRotation = function() return {Pitch = 0, Yaw = 0, Roll = 0} end
actor.GetActorScale3D = function() return {X = 1, Y = 1, Z = 1} end
actor.SetActorEnableCollision = function(self, enabled) self.collides = enabled end
actor.GetActorEnableCollision = function(self) return self.collides end
actor.SetActorHiddenInGame = function(self, hidden) self.bHidden = hidden end
actor.K2_DestroyActor = function(self) self.destroyed = true end
api.GetGameInstance = function(_, pawn) assert(pawn == host); return instance end
for _, method in ipairs({"CreateSaveGameObject", "LoadGameFromSlot", "SaveGameToSlot", "DoesSaveGameExist"}) do
    api[method] = function() error("RAM escrow called " .. method) end
end

package.loaded.native_loot = {}
package.loaded.guest_save = {
    exact_item_context = function(_, _, name)
        assert(name == "guest")
        return guest, save
    end,
}
package.loaded.native_enemy_shell_view = {clear_line = function() return true end}
package.loaded.native_guest_drop_projection = {}
package.loaded.native_transport = {scoped_host = function() return {pawn = host}, true end}
package.loaded.native_enemy_drop_motion = {settled = function() return true end}
package.loaded.world_path = {visible_actor_level = function(source, level) return source == actor and level == world end}
local statics = {
    [actor_path] = actor,
    ["/Script/Engine.Default__GameplayStatics"] = api,
    ["/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"] = class,
    ["/Engine/Transient"] = transient,
}
StaticFindObject = function(path) return statics[path] end
EObjectFlags = {RF_Transient = 1}
StaticConstructObject = function(kind, outer, _, flags)
    assert(kind == class and outer == transient and flags == EObjectFlags.RF_Transient)
    next_pool = next_pool + 1
    return assert(pools[next_pool])
end

local references, releases = {}, {}
SilverfishRetainNativeLootEscrow = function(stock_address, pool)
    assert(stock_address == stock:GetAddress() and not references[pool])
    if pool == first_pool:GetAddress() then return false, "loot_escrow_reference_unavailable" end
    references[pool] = true
    return true
end
SilverfishReleaseNativeLootEscrow = function(pool)
    assert(references[pool])
    releases[pool] = (releases[pool] or 0) + 1
    if pool == second_pool:GetAddress() or releases[pool] == 1 then
        return false, "loot_escrow_reference_unavailable"
    end
    references[pool] = nil
    return true
end

local stock_guid, stack_guid = ("A"):rep(32), ("B"):rep(32)
local captures, claims = 0, 0
local allow_capture = false
SilverfishReadStockEnemyDrop = function(pawn, latest)
    assert(pawn == host:GetAddress())
    if latest == 0 then return 1, 1, "NPC_C worker", "guest", actor:GetFullName(), item_class, true end
end
SilverfishVerifyNativeLootEscrow = function() error("RAM escrow used a save round trip") end
SilverfishCaptureStockEnemyDrop = function(pawn, scratch, source, sequence)
    assert(pawn == host:GetAddress() and source == actor:GetAddress() and sequence == 1 and references[scratch])
    captures = captures + 1
    if not allow_capture then return false, "native_loot_stock_actor_conversion_unverified" end
    return true, "captured", stock_guid, 4
end
SilverfishClaimNativeLootEscrow = function(pawn, detached, canonical, scratch, guid)
    assert(pawn == guest:GetAddress() and detached == save:GetAddress() and canonical == stock:GetAddress())
    assert(scratch == third_pool:GetAddress() and references[scratch] and guid == stock_guid)
    claims = claims + 1
    return true, "merged", 4, 5, 9, stack_guid
end

local drops, escrow = require("native_stock_enemy_drops"), require("native_loot_actions")
local state = {world = world, map = map, local_pawn = {pawn = host}, listening = true, server = false}
local phase = "hosting"
local function row(...)
    return table.concat({...}, "\t")
end
local function bank(slot)
    return pcall(drops.command, {"req", "native-stock-enemy-loot-bank", "1", slot, "memory-escrow"}, state, phase)
end
local function claim(slot, legacy)
    local fields = {"req", "native-stock-enemy-loot-claim", "guest", slot, stock_guid, "1", "2", "3", ("a"):rep(32)}
    if not legacy then fields[#fields + 1] = "memory-escrow" end
    return pcall(drops.command, fields, state, phase)
end
local function has_row(expected)
    return table.concat(drops.rows(state, phase, row), "\n"):find(expected, 1, true) ~= nil
end
local first_slot, second_slot, third_slot = "SFCoop_" .. ("1"):rep(32),
    "SFCoop_" .. ("2"):rep(32), "SFCoop_" .. ("3"):rep(32)

assert(has_row(actor:GetFullName()))
local ok, refusal = bank(first_slot)
assert(not ok and refusal == "native_loot_escrow_memory_loot_escrow_reference_unavailable")
assert(captures == 0 and not actor.destroyed and not actor.bHidden and actor.collides)

ok, refusal = bank(second_slot)
assert(not ok and refusal == "native_loot_stock_actor_conversion_unverified")
assert(captures == 1 and not actor.destroyed and not actor.bHidden and actor.collides)
assert(releases[second_pool:GetAddress()] == 1 and not escrow.pending_escrow(world, second_slot))

allow_capture = true
assert(bank(third_slot))
assert(captures == 2 and actor.destroyed and actor.bHidden and not actor.collides)
assert(escrow.pending_escrow(world, third_slot) == third_pool)
assert(claim(third_slot))
assert(has_row(row("native-stock-enemy-loot-claimed", map, "guest", third_slot, stock_guid,
    "merged", 4, 5, 9, stack_guid)))
assert(claims == 1 and releases[third_pool:GetAddress()] == 1 and not escrow.pending_escrow(world, third_slot))

ok, refusal = claim(third_slot)
assert(not ok and refusal == "stock_enemy_loot_escrow_unavailable" and claims == 1)
ok, refusal = claim(third_slot, true)
assert(not ok and refusal == "stock_enemy_loot_escrow_unavailable" and claims == 1)
ok, refusal = pcall(escrow.command, {"req", "native-loot-flush-escrows", third_slot}, state, phase)
assert(not ok and refusal == "native_loot_escrow_not_pending")
escrow.release_pending()
escrow.release_pending()
assert(releases[third_pool:GetAddress()] == 2 and not references[third_pool:GetAddress()])
assert(releases[second_pool:GetAddress()] == 2 and references[second_pool:GetAddress()])
print("Stock enemy drop refusal, exact awarded receipt, quarantine and bounded cleanup passed")
