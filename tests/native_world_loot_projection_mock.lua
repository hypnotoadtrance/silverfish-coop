package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

os.getenv = function(key)
    if key == "SILVERFISH_COOP_NATIVE_ZONE" then return "1" end
end

local area = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
local epoch, good, failed = string.rep("a", 32), string.rep("b", 32), string.rep("c", 32)
local ammo = "/Game/Classes/Items/Ammo_Magnum_BP.Ammo_Magnum_BP_C"
local revolver = "/Game/Classes/Weapons/Wep_Revolver_9mm.Wep_Revolver_9mm_C"
local world = {IsValid = function() return true end, GetAddress = function() return 1 end,
    GetFullName = function() return "World " .. area .. ".City_01_P" end}
local pawn = {IsValid = function() return true end, GetAddress = function() return 2 end,
    GetWorld = function() return world end, IsLocallyControlled = function() return true end,
    GetFullName = function() return "local pawn" end}
local state = {map = area, world = world, listening = true, server = true, local_pawn = {pawn = pawn}}

local active, reject = {}, true
StaticFindObject = function() return world end
SilverfishSyncNativeWorldLootItem = function(_, item_epoch, id, _, _, _, _, _, _, _, _, _, _, _, count, pickable, slot)
    assert(count == (id == good and 12 or 0) and pickable == true and type(slot) == 'number')
    if id == failed and reject then return false, "stock_drop_projection_stock_model_unavailable" end
    active[slot] = {true, item_epoch, id, "stock actor", 1}
    return true
end
SilverfishReadNativeGuestDropProjection = function(slot) return table.unpack(active[slot] or {}) end
SilverfishTakeNativeGuestDropProjectionPress = function() return epoch, good end

local projection = require("native_guest_drop_projection")
local placement = "|1|2|3|0|0|0|1|1|1"
local frame = {"request", "native-world-loot-frame", area, epoch, good .. "|" .. ammo .. placement .. "|12",
    failed .. "|" .. revolver .. placement .. "|0"}

local function rows_by_item()
    local result = {}
    for _, fields in ipairs(projection.rows(state, "connected", function(...) return {...} end)) do
        result[fields[4] or fields[1]] = fields
    end
    return result
end

assert(projection.command(frame, state, "connected") == "stock world loot synchronized with projection errors")
local initial = rows_by_item()
assert(initial[good][1] == "native-world-loot-item" and initial[good][8] == ammo)
assert(initial[failed][1] == "native-world-loot-projection-error" and initial[failed][5] == revolver)
assert(projection.take_press(pawn) == "world-loot:" .. epoch .. ":" .. good .. ":1")
for _, row in pairs(active) do
    if row[3] == good then row[5] = 0 end
end
assert(projection.take_press(pawn) == nil)

reject = false
assert(projection.command(frame, state, "connected") == "stock world loot synchronized")
local recovered = rows_by_item()
assert(recovered[good][1] == "native-world-loot-item")
assert(recovered[failed][1] == "native-world-loot-item" and recovered[failed][8] == revolver)
print("Item projection errors leave other stock pickups active and recover independently")

local operation, other_operation = string.rep("d", 32), string.rep("e", 32)
local point = {X = 1, Y = 2, Z = 3}
pawn.K2_GetActorLocation = function() return point end
local audio = require("native_pickup_audio")
local loaded, play_count, loads = {}, 0, 0
local function object(fields)
    fields.IsValid = function() return true end
    return fields
end

local attenuation = "/Game/Assets/Sounds/SoundClasses/SmallSound_Attenuation.SmallSound_Attenuation"
local master = "/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"
local sound_base = "/Script/Engine.SoundBase"
loaded[master] = object({})
loaded[revolver] = object({})
loaded[sound_base] = object({})
local sound = object({IsA = function(_, class) return class == loaded[sound_base] end})
loaded[ammo] = object({GetCDO = function()
    return object({Sound_Inventory = sound, IsA = function(_, class) return class == loaded[master] end})
end})
loaded["/Script/Engine.Default__GameplayStatics"] = object({PlaySoundAtLocation = function(_, source, cue)
    assert(source == pawn and cue == sound)
    play_count = play_count + 1
end})
StaticFindObject = function(path) return loaded[path] end
LoadAsset = function(path)
    assert(path == attenuation)
    loads = loads + 1
    loaded[path] = object({})
end

local blocked = false
SilverfishClearNativeGuestDropProjection = function(slot)
    if blocked then return false end
    active[slot] = nil
    return true
end
local function cue(id)
    return id .. "|" .. ammo .. "|1|2|3"
end
local function present(id, phase)
    return projection.command({"request", "native-world-loot-present-host-claim", epoch, good,
        area, epoch, cue(id)}, state, phase)
end

assert(not pcall(present, operation, "connected"))
state.server = false
assert(present(operation, "hosting") == "retired|")
assert(play_count == 0 and loads == 0)
assert(audio.command({"request", "native-sound-pickup", area, epoch, cue(operation)}, state, "hosting") == operation)
assert(play_count == 1 and loads == 1)
assert(projection.command(frame, state, "hosting") == "stock world loot synchronized")
blocked = true
assert(present(other_operation, "hosting") == "world_loot_retirement_pending|" .. other_operation)
assert(play_count == 2 and loads == 1)
assert(audio.command({"request", "native-sound-pickup", area, epoch, cue(other_operation)}, state, "hosting")
    == other_operation)
assert(play_count == 2)
print("Host presentation never loads missing assets and each effect can complete independently")
