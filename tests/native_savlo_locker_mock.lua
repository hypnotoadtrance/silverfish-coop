-- Run with Lua 5.4 from the repository root.
local area = "/Game/Classes/Maps/Maps_OverWorld/Trench_01_P"
local asset = area .. ".Trench_01_P"
local path = asset .. ":PersistentLevel.LootLocker_Secure_BP_C_16"
local actor_name = "LootLocker_Secure_BP_C " .. path
local actor_class = "/Game/Classes/Actors/Lootables/LootLocker_Secure_BP.LootLocker_Secure_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local key_class = "/Game/Classes/Items/Key_Items/LockerKey_543.LockerKey_543_C"
local epoch, other_epoch = ("ba"):rep(16), ("cd"):rep(16)
os.getenv = function() return "1" end

local objects, index = {}, 0
local function object(name, kind, lookup)
    index = index + 1
    local address = index
    local result = {GetAddress = function() return address end, GetFullName = function() return name end,
        IsValid = function() return true end, GetClass = function() return objects[kind] end,
        IsActorBeingDestroyed = function() return false end}
    if lookup then objects[lookup] = result end
    return result
end
StaticFindObject = function(name) return objects[name] end
for _, class in ipairs({actor_class, pawn_class, controller_class, key_class,
    "/Script/Engine.StaticMeshComponent", "/Script/Engine.BoxComponent", actor_class .. ":ClearLoot"}) do
    object("Class " .. class, nil, class)
end
local world = object("World " .. asset)
local driver, connection = object("driver"), object("connection")
world.PersistentLevel, world.NetDriver = object("persistent level"), driver
driver.ServerConnection, driver.World, connection.Driver = connection, world, driver
local pawn = object("Player_FP_C " .. asset .. ":PersistentLevel.Player_FP_C_1", pawn_class)
local controller = object("controller", controller_class)
local hosting = true
function pawn:HasAuthority() return hosting end
function pawn:IsLocallyControlled() return true end
function controller:IsLocalController() return true end
pawn.Controller, controller.Pawn, pawn.Dead = controller, pawn, false
local actor = object(actor_name, actor_class, path)
function actor:GetOuter() return world.PersistentLevel end
function actor:HasAuthority() return true end
function actor:K2_GetActorLocation() return {X = 30700, Y = -137650, Z = 2000} end
function actor:K2_GetActorRotation() return {Pitch = 0, Roll = 0, Yaw = -90} end
for _, scoped in ipairs({pawn, controller, actor}) do scoped.GetWorld = function() return world end end
actor.bReplicates, actor.KeyType, actor.KeyItem, actor.Locked = false, 1, objects[key_class], true
actor.SpawnSalt, actor.GuaranteeSpawn, actor.Replenish_Mode = -1622058784, true, 2
actor.Loot_Override = {RowName = {ToString = function() return "SecureLocker" end}}
local function component(name, class)
    local result = object(class .. " " .. path .. "." .. name, "/Script/Engine." .. class)
    result.GetOwner = function() return actor end
    actor[name] = result
    return result
end
local frame = component("SM_Prop_Cabinet_Small_02", "StaticMeshComponent")
local door = component("SM_Prop_Cabinet_Small_Door_01", "StaticMeshComponent")
local box = component("Box", "BoxComponent")
door.GetAttachParent, box.GetAttachParent = function() return frame end, function() return frame end
door.RelativeRotation = {Pitch = 0, Roll = 0, Yaw = 0}
local retired, clears, presses = false, 0, 0
function actor:K2_GetComponentsByClass(class)
    assert(class == objects["/Script/Engine.BoxComponent"])
    return retired and {} or {box}
end
function actor:ClearLoot()
    assert(not hosting and not retired)
    retired, clears = true, clears + 1
end
package.loaded.native_authored_pickup_input_fixture = {
    open_cabinet = function(_, phase, who, target, actual_box, source)
        assert(hosting and phase == "hosting" and who == pawn and target == actor and box == actual_box
            and source == "trench-savlo-locker" and not retired)
        if actor.Locked then actor.Locked = false else retired = true end
        presses = presses + 1
    end,
}

local locker = assert(loadfile("mod/SilverfishCoop/Scripts/native_savlo_locker.lua"))()
local state = {map = area, world = world, listening = true, server = false, controller = controller,
    local_pawn = {name = pawn:GetFullName(), pawn = pawn}}
local function read(phase) return locker.rows(state, phase, table.pack)[1] end
local command = {"nonce", "native-savlo-locker-stock-fixture", pawn:GetFullName(), "locked"}
assert(read("hosting")[2] == "locked")
assert(locker.command(command, state, "hosting") and read("hosting")[2] == "unlocked")
assert(not pcall(locker.command, command, state, "hosting") and presses == 1)
command[4] = "unlocked"
assert(locker.command(command, state, "hosting") and read("hosting")[2] == "opening")
door.RelativeRotation.Roll = 95
assert(read("hosting")[2] == "opened" and clears == 0)

hosting, state.server, retired, actor.Locked, door.RelativeRotation.Roll = false, true, false, true, 0
local projection = {"nonce", "native-savlo-locker-project", epoch, actor_name, pawn:GetFullName()}
local original = projection[4]
projection[4] = actor_name:gsub("_16$", "_17")
assert(not pcall(locker.command, projection, state, "connected") and clears == 0)
projection[4] = original
assert(locker.command(projection, state, "connected") and read("connected")[2] == "projecting")
door.RelativeRotation.Roll = 95
local receipt = read("connected")
assert(receipt[2] == "projected" and receipt[6] == epoch and receipt[7] == 1 and actor.Locked)
assert(locker.command(projection, state, "connected") and clears == 1)
projection[3] = other_epoch
assert(not pcall(locker.command, projection, state, "connected") and clears == 1)
print("Exact Trench secure locker retains stock host unlock/open and requests quiet guest ClearLoot once for the current visit")
