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
