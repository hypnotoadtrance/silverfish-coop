package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local counter = 0
local function object(values)
    counter = counter + 1
    local address = counter
    values = values or {}
    values.GetAddress = function() return address end
    values.IsValid = function() return true end
    return values
end

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local site = "00000001000000000000000000000000"
local class = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local origin = {X = 0, Y = 0, Z = 0}
local world, base = object(), object()
local stock = object({CombineStashes = false})
local instance = object({SaveSlot_Ref = stock})
local host = object({Save = stock, GetFullName = function() return "Host" end})
local pawn = object({Save = object(), Dead = false, InteractionRange = 100,
    K2_GetActorLocation = function(self) return self.location or origin end})
local function site_actor(index, id)
    return object({Stash_ID = {A = id, B = 0, C = 0, D = 0}, GameInstanceFP = instance,
        IsA = function(_, type) return type == base end,
        GetWorld = function() return world end, HasAuthority = function() return true end,
        K2_GetActorLocation = function() return origin end,
        IsActorBeingDestroyed = function(self) return self.destroying == true end,
        GetFullName = function() return "Stash_Crate_BP_C " .. map .. ".Observitory_P:PersistentLevel.Stash_Crate_BP_C_" .. index end})
end
local actor, world_scans, class_loaded = site_actor(0, 1), 0, true
local constructed
local statics = object({
    GetAllActorsOfClass = function(_, requested_world, type, found)
        assert(requested_world == world and type == base and constructed)
        world_scans = world_scans + 1
        found[1] = actor
    end,
    LoadGameFromSlot = function() return object() end,
})
local bound = pawn.Save
local state = {map = map, world = world, listening = true, server = false}

package.loaded.native_host_save = {canonical_host = function() return instance, host end}
package.loaded.guest_save = {exact_item_context = function(_, _, name)
    if name == "Guest" then return pawn, bound, "character" end
end}

function StaticFindObject(path)
    if path == "/Game/Classes/Actors/Stash_Master_BP.Stash_Master_BP_C" and class_loaded then return base end
    if path == "/Script/Engine.Default__GameplayStatics" then return statics end
end

local function guid(index) return string.format("%032X", index) end
local function native_rows(count, live)
    local rows = {}
    for index = 1, count do
        rows[index] = table.concat({guid(index), "stash", index - 1, class, 1, 1, 0, 0, 0}, "\t")
            .. (live and "\t1\t0.25\t15\t0" or "")
    end
    return table.concat(rows, "\n")
end

local live_reads, legacy_reads, count, clock = 0, 0, 34, 10
local real_time, real_clock, census_clock = os.time, os.clock, 0
os.time = function() return clock end
os.clock = function() return census_clock end
function NotifyOnNewObject(path, callback)
    assert(path == "/Game/Classes/Actors/Stash_Master_BP.Stash_Master_BP_C" and not constructed)
    constructed = callback
end
function SilverfishReadLiveNativeStashCensus(address, combined, target)
    assert(address == pawn:GetAddress() and combined == stock.CombineStashes and target == site)
    live_reads = live_reads + 1
    return true, "complete", native_rows(count, true), count
end
function SilverfishReadNativeStashCensus()
    legacy_reads = legacy_reads + 1
    return true, "complete", native_rows(count, false), count
end
function SilverfishMoveLiveNativeStashItem()
    return true, "moved", class, 1
end

local stash = require("native_stash")
local token, other_token = string.rep("a", 32), string.rep("b", 32)
local function row(...) return {...} end
assert(not constructed and #stash.rows(state, "singleplayer", row) == 0 and world_scans == 0)
local function read(offset, nonce, slot)
    stash.reset()
    stash.command({"ticket", "native-stash-census", "Guest", slot or "-", site, tostring(offset), nonce}, state, "hosting")
    local header, items, capability = nil, {}, false
    for _, fields in ipairs(stash.rows(state, "hosting", row)) do
        if fields[1] == "native-stash-census" then header = fields end
        if fields[1] == "native-stash-census-item" then items[#items + 1] = fields[2] end
        if fields[1] == "native-stash-census-snapshot" and fields[2] == 1 then capability = true end
    end
    assert(capability and header[3] == "Guest" and header[5] == site and header[8] == offset and header[9] == #items)
    return header[7], items
end

local function refuses(message, offset, nonce, slot)
    local ok, why = pcall(read, offset, nonce, slot)
    assert(not ok and why == message, tostring(why))
end

refuses("native_stash_exact_site_unavailable", 0, string.rep("A", 32))
refuses("native_stash_exact_site_unavailable", 0, token, "SFCoop_" .. token)
refuses("native_stash_census_snapshot_unavailable", 16, token)
for _, offset in ipairs({0, 0, 32}) do
    local total, items = read(offset, token)
    assert(total == 34 and #items == math.min(32, total - offset))
    for index, item in ipairs(items) do assert(item == guid(index + offset)) end
end
assert(live_reads == 1)
assert(read(16) == 34 and live_reads == 2)
assert(read(16, nil, "SFCoop_" .. token) == 34 and legacy_reads == 1)

pawn.location = {X = 300, Y = 0, Z = 0}
refuses("native_stash_participant_out_of_reach", 16, token)
pawn.location = nil
pawn.Save = object()
refuses("native_stash_census_save_unavailable", 16, token)
bound = pawn.Save
refuses("native_stash_census_snapshot_unavailable", 16, token)
assert(read(0, other_token) == 34 and live_reads == 3)

stash.command({"ticket", "native-stash-move", "Guest", "-", site, guid(1), "withdraw", "0"}, state, "hosting")
refuses("native_stash_census_snapshot_unavailable", 16, other_token)
count = 0
local total, items = read(0, token)
assert(total == 0 and #items == 0 and live_reads == 4)
stock.CombineStashes = true
refuses("native_stash_census_snapshot_unavailable", 16, token)
assert(read(0, token) == 0 and live_reads == 5)
clock = clock + 45
refuses("native_stash_census_snapshot_unavailable", 16, token)
assert(read(0, token) == 0 and live_reads == 6)
world = object()
state.world = world
refuses("native_stash_census_snapshot_unavailable", 16, token)
assert(live_reads == 6 and world_scans == 2)

local function visible_sites()
    local names = {}
    for _, fields in ipairs(stash.rows(state, "hosting", row)) do
        if fields[1] == "native-stash-site" then names[fields[3]] = fields[4] end
    end
    return names
end
for _ = 1, 6 do
    census_clock = census_clock + 2
    assert(visible_sites()[site] == actor:GetFullName() and world_scans == 2)
end
local duplicate = site_actor(1, 1)
duplicate.GameInstanceFP, duplicate.Stash_ID = nil, nil
constructed(duplicate)
assert(visible_sites()[site] == actor:GetFullName())
census_clock = census_clock + 3
duplicate.GameInstanceFP, duplicate.Stash_ID = instance, actor.Stash_ID
assert(not visible_sites()[site])
refuses("native_stash_exact_site_unavailable", 0, token)
duplicate.destroying = true
assert(visible_sites()[site] == actor:GetFullName())
actor.destroying = true
assert(not visible_sites()[site] and world_scans == 2)

class_loaded = false
world = object()
state.world = world
assert(not visible_sites()[site] and world_scans == 2)
local late = site_actor(2, 1)
class_loaded = true
constructed(late)
assert(visible_sites()[site] == late:GetFullName() and world_scans == 2)
state.map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
assert(not visible_sites()[site] and world_scans == 3)
assert(#stash.rows(state, "singleplayer", row) == 0)
assert(constructed(late) == true)
constructed = nil
state.map = map
assert(not visible_sites()[site] and world_scans == 4)
constructed(late)
assert(visible_sites()[site] == late:GetFullName() and world_scans == 4)
os.time, os.clock = real_time, real_clock
print("Stock stash snapshots and site discovery preserve ownership, pages, and world lifecycle")

local stash_save = package.loaded.native_host_save
package.loaded.native_host_save = nil
local host_save = require("native_host_save")
stash_save.canonical_host = host_save.canonical_host
local classes, class_reads = {}, 0
local original_find = StaticFindObject
function StaticFindObject(path)
    if classes[path] then class_reads = class_reads + 1; return classes[path] end
    return original_find(path)
end
local function stock_object(value, path)
    classes[path] = classes[path] or object()
    value.GetClass = function() return classes[path] end
    value.GetFullName = value.GetFullName or function() return path end
    return value
end
local function authority() return true end
local function current_world() return world end
local controller = stock_object(object({Pawn = host, HasAuthority = authority, IsLocalController = authority,
    GetWorld = current_world}), "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")
host.Controller, host.HasAuthority, host.GetWorld = controller, authority, current_world
stock_object(host, "/Game/Classes/Player/Player_FP.Player_FP_C")
stock_object(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local items = {GetArrayNum = function() return 0 end}
local restored = stock_object(object({InstanceSeed = 42, PlayerBackpack = items, PlayerEquipment = items, GameRules = {}}),
    save_path)
stock.InstanceSeed, stock.PlayerBackpack, stock.PlayerEquipment, stock.GameRules = 42, items, items, {}
stock_object(stock, save_path)
world.GetFullName = function() return "World " .. map .. ".Observitory_P" end
world.OwningGameInstance = instance
state.local_pawn, state.controller = {pawn = host}, controller
statics.GetGameInstance = function() return world.OwningGameInstance end
statics.DoesSaveGameExist = function() return false end
statics.SaveGameToSlot = function() return true end
statics.LoadGameFromSlot = function() return restored end
host_save.capture(state, "hosting", "SFCoop_" .. token)
local _, observed = stash.rows(state, "hosting", row)
local function status_row(...) return table.concat({...}, "\t") end
local function save_row(context) return host_save.rows(state, "hosting", status_row, context)[1] end
local full, before = save_row(), class_reads
assert(save_row(observed) == full and class_reads == before)
instance.SaveSlot_Ref, host.Save = restored, restored
local changed = save_row(observed)
assert(class_reads > before and changed ~= full and changed:find("\tchanged\t") and changed == save_row())
print("Host status reuses qualified stash ownership and rechecks a replaced canonical save")
