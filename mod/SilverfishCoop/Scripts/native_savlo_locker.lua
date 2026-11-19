local locker = {}
local stock_input = require("native_authored_pickup_input_fixture")

local area = "/Game/Classes/Maps/Maps_OverWorld/Trench_01_P"
local world_path = area .. ".Trench_01_P"
local actor_path = world_path .. ":PersistentLevel.LootLocker_Secure_BP_C_16"
local actor_name = "LootLocker_Secure_BP_C " .. actor_path
local locker_class = "/Game/Classes/Actors/Lootables/LootLocker_Secure_BP.LootLocker_Secure_BP_C"
local key_class = "/Game/Classes/Items/Key_Items/LockerKey_543.LockerKey_543_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local loot_table = "/Game/Classes/Libraries/DataTables/LootTables_DT.LootTables_DT"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_INPUT") == "1"
local fixture = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local projection

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(value, class)
    return valid(value) and same(value:GetClass(), StaticFindObject(class))
end

local function component(value, actor, suffix, class)
    return exact(value, "/Script/Engine." .. class) and same(value:GetOwner(), actor)
        and value:GetFullName() == class .. " " .. actor_path .. "." .. suffix
end

local function angle_close(actual, expected)
    return type(actual) == "number" and math.abs((actual - expected + 180) % 360 - 180) <= .2
end

local function door_angle(door, roll)
    local angle = door.RelativeRotation
    return angle and angle_close(angle.Pitch, 0) and angle_close(angle.Yaw, 0) and angle_close(angle.Roll, roll)
end

local function epoch_valid(epoch)
    return type(epoch) == "string" and #epoch == 32 and epoch:match("^[0-9a-f]+$") and epoch:find("[^0]")
end

local function inspect(state, phase)
    if not enabled or state.map ~= area or state.listening ~= true or not valid(state.world)
        or state.world:GetFullName() ~= "World " .. world_path then return end
    local hosting = phase == "hosting" and state.server == false
    if not hosting and (phase ~= "connected" or state.server ~= true) then return end
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_class) or not exact(controller, controller_class) or pawn:HasAuthority() ~= hosting
        or detail.name ~= pawn:GetFullName() or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world) then return nil, "local_pawn" end
    if not hosting then
        local driver = state.world.NetDriver
        local connection = valid(driver) and driver.ServerConnection
        if not valid(connection) or not same(driver.World, state.world) or not same(connection.Driver, driver) then
            return nil, "client_connection"
        end
    end
    local actor = StaticFindObject(actor_path)
    if not exact(actor, locker_class) or actor:GetFullName() ~= actor_name or actor:IsActorBeingDestroyed()
        or not same(actor:GetWorld(), state.world) or not same(actor:GetOuter(), state.world.PersistentLevel)
        or actor.bReplicates ~= false or (hosting and not actor:HasAuthority()) then return nil, "actor_identity" end
    local location, angle, override = actor:K2_GetActorLocation(), actor:K2_GetActorRotation(), actor.Loot_Override
    if not location or (location.X - 30700)^2 + (location.Y + 137650)^2 + (location.Z - 2000)^2 > 1
        or not angle or not angle_close(angle.Yaw, -90) then return nil, "actor_transform" end
    if actor.KeyType ~= 1 or not same(actor.KeyItem, StaticFindObject(key_class))
        or type(actor.Locked) ~= "boolean" then return nil, "stock_lock" end
    if actor.SpawnSalt ~= -1622058784 or actor.GuaranteeSpawn ~= true or actor.Replenish_Mode ~= 2
        or not override or not override.RowName or override.RowName:ToString() ~= "SecureLocker"
        or (valid(override.DataTable) and not same(override.DataTable, StaticFindObject(loot_table))) then
        return nil, "stock_container_fields"
    end
    local door, frame = actor.SM_Prop_Cabinet_Small_Door_01, actor.SM_Prop_Cabinet_Small_02
    if not component(door, actor, "SM_Prop_Cabinet_Small_Door_01", "StaticMeshComponent")
        or not component(frame, actor, "SM_Prop_Cabinet_Small_02", "StaticMeshComponent")
        or not same(door:GetAttachParent(), frame) then return nil, "stock_door_identity" end
    local box_class = StaticFindObject("/Script/Engine.BoxComponent")
    if not valid(box_class) then return nil, "stock_box_class" end
    local boxes = actor:K2_GetComponentsByClass(box_class)
    local shape = type(boxes)
    if shape == "userdata" and type(boxes.type) == "function" then shape = boxes:type() end
    local count = (shape == "table" or shape == "TArray") and #boxes or nil
    if count ~= 0 and count ~= 1 then return nil, "stock_box_census" end
    local box
    if count == 1 then
        box = boxes[1]
        if type(box) == "userdata" and type(box.type) == "function" and box:type() == "RemoteUnrealParam" then box = box:get() end
        if not component(box, actor, "Box", "BoxComponent") or not same(box, actor.Box)
            or not same(box:GetAttachParent(), frame) then return nil, "stock_box_identity" end
    end
    return {actor = actor, box = box, pawn = pawn, world = state.world, hosting = hosting, locked = actor.Locked,
        closed = box and door_angle(door, 0), open = count == 0 and door_angle(door, 95)}
end

local function same_projection(scene)
    return projection and same(scene.world, projection.world) and same(scene.actor, projection.actor)
        and same(scene.pawn, projection.pawn)
end

function locker.clear()
    projection = nil
end

function locker.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-savlo-locker-stock-fixture" and action ~= "native-savlo-locker-project" then return end
    local scene, reason = inspect(state, phase)
    if action == "native-savlo-locker-stock-fixture" then
        local locked = fields[4] == "locked"
        if #fields ~= 4 or (not locked and fields[4] ~= "unlocked") or not fixture or not scene or not scene.hosting
            or not scene.closed or scene.locked ~= locked or scene.pawn.Dead ~= false
            or fields[3] ~= scene.pawn:GetFullName() then
            error("stock_savlo_locker_fixture_scope_unavailable:" .. (reason or "stock_state"), 0)
        end
        stock_input.open_cabinet(state, phase, scene.pawn, scene.actor, scene.box, "trench-savlo-locker")
        return "exact stock secure locker Interact dispatched"
    end
    local epoch = fields[3]
    if #fields ~= 5 or not scene or scene.hosting or not epoch_valid(epoch)
        or fields[4] ~= actor_name or fields[5] ~= scene.pawn:GetFullName() then
        error("stock_savlo_locker_projection_scope_unavailable:" .. (reason or "stock_state"), 0)
    end
    if same_projection(scene) and projection.epoch == epoch then return "stock secure locker projection already accepted" end
    if not scene.closed or not valid(StaticFindObject(locker_class .. ":ClearLoot")) then
        error("stock_savlo_locker_projection_requires_exact_closed_copy", 0)
    end
    -- The cooked ClearLoot entry retires only the Box and moves the single door.
    scene.actor:ClearLoot()
    if scene.actor.Locked ~= scene.locked then error("mutation_unverified_stock_savlo_locker_projection_lock", 0) end
    projection = {epoch = epoch, world = scene.world, actor = scene.actor, pawn = scene.pawn, locked = scene.locked}
    return "exact stock secure locker presentation requested"
end

function locker.rows(state, phase, status)
    local scene, reason = inspect(state, phase)
    if not scene then return reason and {status("native-savlo-locker-diagnostic", area, reason)} or {} end
    local name, epoch = nil, "-"
    if not scene.hosting and same_projection(scene) and scene.locked == projection.locked then
        name, epoch = scene.open and "projected" or "projecting", projection.epoch
    elseif scene.closed then
        name = scene.locked and "locked" or "unlocked"
    elseif scene.hosting then
        name = scene.open and "opened" or "opening"
    elseif scene.open then
        name = "stock_opened"
    end
    if not name then return {} end
    return {status("native-savlo-locker", name, area, actor_name, scene.pawn:GetFullName(), epoch, scene.locked and 1 or 0)}
end

return locker
