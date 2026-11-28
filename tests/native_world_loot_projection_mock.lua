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

local active, reject, preview_started = {}, true, false
StaticFindObject = function() return world end
local set_physics
SilverfishSetNativeWorldLootPhysics = function(...) return set_physics(...) end
SilverfishSyncNativeWorldLootItem = function(_, item_epoch, id, _, _, _, _, _, _, _, _, _, _, _, count, pickable, slot)
    assert(count == (id == good and 12 or 0) and pickable == true and type(slot) == 'number')
    if id == failed and reject then return false, "stock_drop_projection_stock_model_unavailable" end
    active[slot] = {true, item_epoch, id, "stock actor", 1}
    return true
end
SilverfishReadNativeGuestDropProjection = function(slot) return table.unpack(active[slot] or {}) end
SilverfishTakeNativeGuestDropProjectionPress = function()
    for _, item in pairs(active) do
        if item[3] == good then return epoch, good, item[5], preview_started end
    end
end

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
    active[slot or 0] = nil
    return true
end
local function cue(id)
    return id .. "|" .. ammo .. "|1|2|3"
end
local function present(id, phase)
    return projection.command({"request", "native-world-loot-present-claim", epoch, good,
        area, epoch, cue(id)}, state, phase)
end

assert(not pcall(present, operation, "hosting"))
assert(present(operation, "connected") == "retired|")
assert(play_count == 0 and loads == 0)
assert(audio.command({"request", "native-sound-pickup", area, epoch, cue(operation)}, state, "connected") == operation)
assert(play_count == 1 and loads == 1)
assert(projection.command(frame, state, "connected") == "stock world loot synchronized")
assert(present(other_operation, "connected") == "retired|" .. other_operation)
assert(play_count == 2 and loads == 1)

assert(projection.command(frame, state, "connected") == "stock world loot synchronized")
preview_started = true
assert(projection.take_press(pawn) == "world-loot:" .. epoch .. ":" .. good .. ":1:heard")
assert(play_count == 3 and loads == 1)
loaded[attenuation] = nil
assert(projection.take_press(pawn) == "world-loot:" .. epoch .. ":" .. good .. ":1")
assert(play_count == 3 and loads == 1)
LoadAsset(attenuation)
preview_started = false
assert(projection.take_press(pawn) == "world-loot:" .. epoch .. ":" .. good .. ":1")
assert(play_count == 3 and loads == 2)
local grant = require("native_inventory_grant")
local apply = grant.project_pickup
local guid = string.rep("A", 32)
local imported = 0
grant.project_pickup = function(_, phase, revision, received_guid, class, text, previous)
    assert(phase == "connected" and revision == epoch and received_guid == guid)
    assert(class == ammo and text == "(Item=1)" and previous == "-")
    imported = imported + 1
    return "added"
end
local receipt = {"request", "native-world-loot-present-claim", epoch, good, area, epoch, "-", guid, ammo, "(Item=1)", "-"}
assert(projection.command(receipt, state, "connected") == "retired||added")
assert(imported == 1)
assert(projection.command(frame, state, "connected") == "stock world loot synchronized")
receipt[3] = other_operation
assert(projection.command(receipt, state, "connected") == "world_loot_retirement_scope_unavailable||deferred")
assert(imported == 1)
grant.project_pickup = apply
assert(projection.command({"request", "native-world-loot-retire", epoch, good}, state, "connected")
    == "stock world loot retired")
local cash = require("authored_cash_catalog")[1]
local cash_operation = string.rep("2", 32)
pawn.HasAuthority = function() return false end
pawn.IsActorBeingDestroyed = function() return false end
pawn.Controller = object({GetAddress = function() return 3 end})
state.controller = pawn.Controller
local cash_command = {"request", "native-sound-pickup", area, epoch,
    cash_operation .. "|" .. cash.class .. "|cash|" .. cash.cash_min}
assert(audio.command(cash_command, state, "connected", true) == cash_operation)
assert(play_count == 3 and loads == 2)
state.server = false
assert(projection.command(frame, state, "hosting") == "stock world loot synchronized")
blocked = true
local host_operation = string.rep("1", 32)
assert(present(host_operation, "hosting") == "world_loot_retirement_pending|" .. host_operation)
assert(play_count == 4 and loads == 2)
assert(audio.command({"request", "native-sound-pickup", area, epoch, cue(host_operation)}, state, "hosting")
    == host_operation)
assert(play_count == 4)
print("Pickup presentation uses loaded assets on both seats and the effects can complete independently")

blocked = false
local fall_id = string.rep("f", 32)
local path = area .. ".City_01_P:PersistentLevel.Ammo_Magnum_BP_C_1"
local name = "Ammo_Magnum_BP_C " .. path
local drop_z, speed, now, wakes = 95, 300, 20, 0
local box = {
    WakeAllRigidBodies = function() wakes = wakes + 1 end,
    GetPhysicsLinearVelocity = function() return {X = 0, Y = 0, Z = speed} end,
    GetPhysicsAngularVelocityInDegrees = function() return {X = 0, Y = 0, Z = 0} end,
}
loaded[path] = object({
    Box = box,
    GetFullName = function() return name end,
    GetWorld = function() return world end,
    IsActorBeingDestroyed = function() return false end,
    K2_GetActorLocation = function() return {X = 1, Y = 2, Z = drop_z} end,
    K2_GetActorRotation = function() return {Pitch = 0, Yaw = 0, Roll = 0} end,
    GetActorScale3D = function() return {X = 1, Y = 1, Z = 1} end,
})
os.clock = function() return now end
FName = function(value) return value end
EFindName = { FNAME_Find = 0 }

local poses, physics, claimable = {}, {}, true
SilverfishSyncNativeWorldLootItem = function(_, item_epoch, id, _, _, z, visual_z, class, _, _, _, _, _, _, count, pickable, slot)
    assert(item_epoch == epoch and id == fall_id and class == ammo and count == 12 and pickable == claimable and z == visual_z)
    poses[#poses + 1] = visual_z
    active[slot] = {true, epoch, fall_id, name, 0}
    return true
end
local preview_physics = 0
set_physics = function(slot, item_epoch, id, simulate, preview)
    assert(active[slot][3] == id and id == fall_id and item_epoch == epoch)
    if preview then preview_physics = preview_physics + 1 end
    physics[#physics + 1] = simulate
    return true
end
local fall = {"request", "native-world-loot-frame", area, epoch,
    fall_id .. "|" .. ammo .. "|fall|1|2|95|0|0|0|1|1|1|12"}
state.server = true
assert(not pcall(projection.command, fall, state, "connected"))
state.server = false
assert(projection.frame_reply(fall, state, "hosting", function(...) return table.concat({...}, "\t") end)
    :find("moving\t1\t2\t95", 1, true))
assert(#physics == 1 and physics[1] and wakes == 1 and poses[1] == 95)
now, drop_z = now + .1, 55
local function read_pose()
    for _, fields in ipairs(projection.rows(state, "hosting", function(...) return {...} end)) do
        if fields[1] == "native-world-loot-physics" and fields[4] == fall_id then return fields end
    end
end
local moving = read_pose()
assert(moving[6] == "moving" and moving[9] == 55)
assert(projection.command(fall, state, "hosting") == "stock world loot synchronized")
assert(#physics == 1 and poses[2] == 55)
drop_z, speed = 3, 0
for _ = 1, 3 do
    now = now + .1
    read_pose()
end
local settled = read_pose()
assert(settled[6] == "settled" and settled[9] == 3 and #physics == 2 and physics[2] == false)
assert(projection.command(fall, state, "hosting") == "stock world loot synchronized")
assert(#physics == 2 and poses[3] == 3)
print("A committed drop falls from its release position once and reports its settled transform")

assert(projection.clear())
state.server, claimable, drop_z = true, false, 95
local preview = {"request", "native-world-loot-frame", area, epoch,
    fall_id .. "|" .. ammo .. "|preview-fall|1|2|95|0|0|0|1|1|1|12"}
local projected = projection.frame_reply(preview, state, "connected", function(...) return table.concat({...}, "	") end)
assert(projected:find(ammo .. "	moving", 1, true) and preview_physics == 1 and #physics == 3 and physics[3])
assert(projection.command(preview, state, "connected") == "stock world loot synchronized")
assert(preview_physics == 1 and #physics == 3 and wakes == 2)
claimable = true
local committed = {"request", "native-world-loot-frame", area, epoch, fall_id .. "|" .. ammo .. "|carry|1|2|3|0|0|0|1|1|1|12"}
assert(projection.command(committed, state, "connected") == "stock world loot synchronized")
assert(#physics == 4 and not physics[4] and poses[#poses] == 3 and wakes == 2)
print("A guest preview falls without pickup and adopts the shared pose on the same projection")
