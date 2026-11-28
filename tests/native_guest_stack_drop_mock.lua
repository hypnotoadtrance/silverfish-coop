package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local env = {
    SILVERFISH_COOP_NATIVE_GUEST_SAVE = "1", SILVERFISH_COOP_NATIVE_ZONE = "1",
    SILVERFISH_COOP_NATIVE_FIXTURES = "1", SILVERFISH_COOP_HEADLESS = "1",
}
os.getenv = function(name) return env[name] end

local function object(address)
    return { IsValid = function() return true end, GetAddress = function() return address end }
end
local game = { map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P", listening = true, server = false }
local pawn, detached, isolated, statics = object(1), object(2), object(3), object(4)
game.world = object(5)
function game.world:GetFullName() return "World " .. game.map .. "." .. game.map:match("[^/]+$") end
local actor = "Ammo_Pistol_BP_C " .. game.map .. ".Observitory_P:PersistentLevel.Ammo_Pistol_BP_C_14"
local guest, slot, guid, changed = "exact guest", "SFCoop_" .. ("a"):rep(32), ("B"):rep(32), ("C"):rep(32)
local loaded, calls, standing, broken = 0, 0, true, false
local location = { X = 0, Y = 0, Z = 0 }
function pawn:K2_GetActorLocation() return location end
isolated.PlayerBackpack = { GetArrayNum = function() return 1 end }
package.loaded.guest_save = {exact_item_context = function(_, phase, name)
    assert(phase == "hosting" and name == guest)
    return pawn, detached
end}
package.loaded.players = {is_standing_guest = function(owned, name)
    assert(owned == pawn and name == guest)
    return standing
end}
function statics:LoadGameFromSlot(name, user)
    assert(name == slot and user == 0)
    loaded = loaded + 1
    return isolated
end
function StaticFindObject(path)
    assert(path == "/Script/Engine.Default__GameplayStatics")
    return statics
end
function SilverfishProbeNativeGuestStackDrop(owner, live_save, frozen, source, count)
    assert(owner == 1 and live_save == 2 and frozen == 3 and source == guid and count == 9)
    calls = calls + 1
    return true, "stock_spawn_verified_and_retired", actor, broken and guid or changed, 9, 10, -20, 30
end

local stack_drop = require("native_guest_stack_drop")
local action = "native-guest-stack-drop-probe"
local birth = "native-guest-stack-drop-birth"
local command = {"request", action, guest, slot, guid, "9"}
local function status(...) return table.pack(...) end
local function attempt(fields, phase)
    return pcall(stack_drop.command, fields, game, phase)
end
local function no_receipt()
    local rows = stack_drop.rows(game, "hosting", status)
    return #rows == 2 and rows[1][1] == birth .. "-capability" and rows[2][1] == action .. "-capability"
end

assert(not attempt(command, "connected") and calls == 0 and loaded == 0 and no_receipt())
assert(not attempt({"request", action, guest, "vanilla", guid, "9"}, "hosting") and calls == 0 and loaded == 0)
standing = false
assert(not attempt(command, "hosting") and calls == 0 and loaded == 0)
standing = true
assert(attempt(command, "hosting") and calls == 1 and loaded == 1)
local row = stack_drop.rows(game, "hosting", status)[3]
assert(row.n == 13 and row[1] == action and row[3] == guest and row[4] == slot and row[5] == guid and row[6] == 9)
assert(row[7] == 1 and row[8] == "stock_spawn_verified_and_retired" and row[9] == actor and row[10] == changed)
assert(row[11] == 10 and row[12] == -20 and row[13] == 30)
assert(#stack_drop.rows(game, "connected", status) == 0)
broken = true
local ok, reason = attempt(command, "hosting")
assert(not ok and reason == "mutation_unverified_native_guest_stack_drop_readback" and no_receipt())

local recovered = 0
function SilverfishRecoverNativeInventoryBag(source, target, owner)
    assert(source == 3 and target == 2 and owner == 1)
    recovered = recovered + 1
    return true, "recovered", 1
end
local settle = {"request", "native-guest-stack-drop-settle", "reclaim", guest, ("e"):rep(32), slot, "-", "20", "0", "0", game.map}
location.X = 300
ok, reason = attempt(settle, "hosting")
assert(not ok and reason == "native_guest_stack_drop_out_of_range" and recovered == 0)
location.X = 0
assert(attempt(settle, "hosting") and recovered == 1)
row = stack_drop.rows(game, "hosting", status)[3]
assert(row[1] == settle[2] and row[3] == "reclaim" and row[8] == 1 and row[9] == "recovered" and row[10] == 1)

env.SILVERFISH_COOP_NATIVE_FIXTURES, env.SILVERFISH_COOP_HEADLESS = nil, nil
package.loaded.native_guest_stack_drop = nil
stack_drop = require("native_guest_stack_drop")
broken = false
assert(not attempt(command, "hosting"))
local rows = stack_drop.rows(game, "hosting", status)
assert(#rows == 1 and rows[1][1] == birth .. "-capability")
assert(attempt({"request", birth, guest, slot, guid, "9", game.map}, "hosting"))
row = stack_drop.rows(game, "hosting", status)[2]
assert(row[1] == birth and row[9] == actor and row[10] == changed)


local observatory = game.map
game.map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
actor = "Ammo_Pistol_BP_C " .. game.map .. ".CrossRoads_P:PersistentLevel.Ammo_Pistol_BP_C_14"
assert(not attempt({"request", birth, guest, slot, guid, "9", observatory}, "hosting"))
assert(attempt({"request", birth, guest, slot, guid, "9", game.map}, "hosting"))
row = stack_drop.rows(game, "hosting", status)[2]
assert(row[1] == birth and row[2] == game.map and row[9] == actor)
assert(not attempt(settle, "hosting") and recovered == 1)
settle[11] = game.map
assert(attempt(settle, "hosting") and recovered == 2)

local host, canonical, controller, instance = object(6), object(7), object(8), object(9)
local host_name = "exact host"
local classes = {
    ["/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"] = object(10),
    ["/Game/Classes/Player/Player_FP.Player_FP_C"] = object(11),
    ["/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"] = object(12),
    ["/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"] = object(13),
}
local function stock_class(target, path)
    function target:GetClass() return classes[path] end
    function target:GetFullName() return path end
end
stock_class(controller, "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")
stock_class(host, "/Game/Classes/Player/Player_FP.Player_FP_C")
stock_class(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
stock_class(canonical, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
function controller:IsLocalController() return true end
function controller:HasAuthority() return true end
function controller:GetWorld() return game.world end
function host:HasAuthority() return true end
function host:GetWorld() return game.world end
function host:K2_GetActorLocation() return location end
host.Dead, host.Controller, host.Save = false, controller, canonical
controller.Pawn, instance.SaveSlot_Ref = host, canonical
function statics:GetGameInstance(target)
    assert(target == controller)
    return instance
end
local engine_object = StaticFindObject
function StaticFindObject(path) return classes[path] or engine_object(path) end
local host_downed = false
function package.loaded.players.is_downed(target)
    assert(target == host)
    return host_downed
end
function SilverfishRecoverNativeInventoryBag(source, target, recipient)
    assert(source == 3 and target == 7 and recipient == 6)
    recovered = recovered + 1
    return true, "recovered", 1
end
game.controller, game.local_pawn = controller, {name = host_name, pawn = host}
settle[4] = host_name
host_downed = true
assert(not attempt(settle, "hosting") and recovered == 2)
host_downed = false
host.Save = detached
assert(not attempt(settle, "hosting") and recovered == 2)
host.Save = canonical
assert(not attempt(settle, "connected") and recovered == 2)
assert(attempt(settle, "hosting") and recovered == 3)
row = stack_drop.rows(game, "hosting", status)[3]
assert(row[1] == settle[2] and row[3] == "reclaim" and row[4] == host_name and row[9] == "recovered")

function SilverfishProbeNativeGuestStackDrop(owner, live_save, frozen, source, count)
    assert(owner == 6 and live_save == 7 and frozen == 3 and source == guid and count == 9)
    return true, "stock_spawn_verified_and_retired", actor, changed, 9, 10, -20, 30
end
assert(attempt({"request", birth, host_name, slot, guid, "9", game.map}, "hosting"))
row = stack_drop.rows(game, "hosting", status)[2]
assert(row[1] == birth and row[3] == host_name and row[7] == 1)

local prepared, replacement = object(14), "SFCoop_" .. ("f"):rep(32)
local load = statics.LoadGameFromSlot
function statics:LoadGameFromSlot(name, user)
    if name == replacement and user == 0 then return prepared end
    return load(self, name, user)
end
function SilverfishApplyPreparedNativeInventory(source, replacement_address, target, recipient)
    assert(source == 3 and replacement_address == 14 and target == 7 and recipient == 6)
    return true, "applied"
end
settle[3], settle[7] = "debit", replacement
assert(attempt(settle, "hosting"))
row = stack_drop.rows(game, "hosting", status)[3]
assert(row[1] == settle[2] and row[3] == "debit" and row[4] == host_name and row[9] == "applied")

local pending, transient, slot_live = object(15), object(16), "SFCoop_" .. ("d"):rep(32)
local item = "/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
local escrow = require("native_loot_actions")
local funded = false
local debits = 0
pending.PlayerBackpack = isolated.PlayerBackpack
function escrow.create_pending_escrow(_, _, world, key)
    assert(world == game.world and key == slot_live and not funded)
    funded = true
    return pending
end
function escrow.pending_escrow(world, key)
    assert(world == game.world and key == slot_live)
    return funded and pending or nil
end
function escrow.release_escrow(key)
    assert(key == slot_live and funded)
    funded = false
end
function StaticFindObject(path)
    return path == "/Engine/Transient" and transient or classes[path] or engine_object(path)
end
function StaticConstructObject(_, outer)
    assert(outer == transient)
    return isolated
end
EObjectFlags = {RF_Transient = 0}
function SilverfishReferenceNativeDetachedSave() end
function SilverfishReadNativeInventoryCensus(address)
    assert(address == 3 or address == 15)
    return true, "complete", table.concat({guid, "backpack", "0", item, "9", "1", "0", "0", "1"}, "\t"), 1
end
function SilverfishPrepareNativeInventoryWipe(frozen, projected, address, kept, dropped, discarded)
    assert(frozen == 3 and projected == 3 and address == 15 and kept == "" and dropped == guid and discarded == "")
    return true, "prepared", 0, 1, 0
end
function SilverfishApplyPreparedNativeInventory(_, _, target, recipient)
    assert(target == 7 and recipient == 6)
    debits = debits + 1
    return true, "applied"
end
function SilverfishProbeNativeGuestStackDrop()
    return true, "stock_spawn_verified_and_retired", actor, changed, 9, 10, -20, 30, item
end
function SilverfishVerifyNativeStashSave() end
require("native_ground_stack").merge = function() return "append" end
function SilverfishRecoverNativeInventoryBag(source, target, recipient)
    assert(source == 15 and target == 7 and recipient == 6 and funded)
    recovered = recovered + 1
    return true, "recovered", 1
end

local function text_row(...) return table.concat({...}, "\t") end
local function compact(fields)
    assert(stack_drop.compact(fields))
    return stack_drop.reply(fields, game, "hosting", text_row)
end
local function readback(fields)
    local retry = {"retry", table.unpack(fields, 2)}
    retry[#retry + 1] = "readback"
    return compact(retry)
end
local live_drop = {"debit", birth, host_name, slot_live, guid, "9", game.map, "memory"}
local live_claim = {"credit", settle[2], "reclaim", host_name, settle[5], slot_live, "-", "20", "0", "0",
    game.map, guid, "9", item, "pending"}
assert(readback(live_drop):find("receipt_unavailable") and debits == 0 and not funded)
assert(compact(live_drop):find("stock_spawn_verified_and_retired") and debits == 1 and funded)
local debit_reply = readback(live_drop)
assert(debit_reply:find("\tretry\t") and debit_reply:find("stock_spawn_verified_and_retired") and debits == 1)
assert(compact(live_drop):find("stock_spawn_verified_and_retired") and debits == 1)
assert(compact(live_claim):find("\trecovered\t") and recovered == 4 and not funded)
local credit_reply = readback(live_claim)
assert(credit_reply:find("\tretry\t") and credit_reply:find("\trecovered\t") and recovered == 4)
live_claim[5] = ("a"):rep(32)
assert(readback(live_claim):find("receipt_unavailable") and recovered == 4)
stack_drop.reset()
assert(readback(live_drop):find("receipt_unavailable") and debits == 1)

function SilverfishProbeNativeGuestStackDrop()
    error("adopting a reported drop must not spawn and retire a stock probe")
end
function SilverfishApplyPreparedNativeInventory(_, _, target, recipient)
    assert(target == 2 and recipient == 1)
    debits = debits + 1
    return true, "applied"
end
stock_class(detached, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
local reported_drop = {"guest drop", birth, guest, slot_live, guid, "9", game.map, "memory", "31.125", "-20", "30"}
assert(readback(reported_drop):find("receipt_unavailable") and debits == 1)
local adopted = compact(reported_drop)
assert(adopted:find("stock_drop_origin_debited	-	-	31.125	-20	30	", 1, true) and debits == 2 and funded)
assert(readback(reported_drop):find("stock_drop_origin_debited") and debits == 2)
