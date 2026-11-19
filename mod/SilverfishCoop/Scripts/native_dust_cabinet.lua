local cabinet = {}
local stock_input = require("native_authored_pickup_input_fixture")

local maps = {
    ["/Game/Classes/Maps/Maps_OverWorld/RailYard_P"] = {
        kind = "dust", level = "RailYard_P", class = "LootCabnet_Small_BP", suffix = 36,
        salt = -1540208128, source = "railyard-dust-filter",
    },
    ["/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P"] = {
        kind = "molasses", level = "NorthEast_Rail_P", class = "LootCabnet_Large_BP", suffix = 4,
        salt = -1439313920, source = "northeast-rail-molasses", loot_override = "Office",
    },
}
for area, profile in pairs(maps) do
    profile.area = area
    profile.world = area .. "." .. profile.level
    profile.path = profile.world .. ":PersistentLevel." .. profile.class .. "_C_" .. profile.suffix
    profile.name = profile.class .. "_C " .. profile.path
    profile.class_path = "/Game/Classes/Actors/Lootables/" .. profile.class .. "." .. profile.class .. "_C"
    profile.tag = "native-" .. profile.kind .. "-cabinet"
    profile.error = "stock_" .. profile.kind .. "_cabinet"
end
local loot_table = "/Game/Classes/Libraries/DataTables/LootTables_DT.LootTables_DT"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_INPUT") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local client_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local projection

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class)
    return valid(object) and same(object:GetClass(), StaticFindObject(class))
end

local function component(object, actor, profile, suffix, class)
    return exact(object, "/Script/Engine." .. class) and same(object:GetOwner(), actor)
        and object:GetFullName() == class .. " " .. profile.path .. "." .. suffix
end

local function angle_is(actual, expected)
    return type(actual) == "number" and math.abs((actual - expected + 180) % 360 - 180) <= 0.15
end

local function door_roll(door, expected)
    local angle = door.RelativeRotation
    return angle and angle_is(angle.Pitch, 0) and angle_is(angle.Yaw, 0) and angle_is(angle.Roll, expected)
end

local function inspect(state, phase)
    local profile = maps[state.map]
    if not enabled or not profile or not state.listening or not valid(state.world)
        or state.world:GetFullName() ~= "World " .. profile.world then return end
    local host = phase == "hosting" and state.server == false
    if not host and (phase ~= "connected" or state.server ~= true) then return end
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_class) or not exact(controller, controller_class) or pawn:HasAuthority() ~= host
        or detail.name ~= pawn:GetFullName() or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world)
        or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn) then return nil, "local_pawn" end
    if not host then
        local driver = state.world.NetDriver
        local connection = valid(driver) and driver.ServerConnection
        if not valid(connection) or not same(driver.World, state.world) or not same(connection.Driver, driver) then
            return nil, "client_connection"
        end
    end
    local actor = StaticFindObject(profile.path)
    if not exact(actor, profile.class_path) or actor:GetFullName() ~= profile.name or actor:IsActorBeingDestroyed()
        or not same(actor:GetWorld(), state.world) or not same(actor:GetOuter(), state.world.PersistentLevel) then
        return nil, "actor_identity"
    end
    -- The stock actor's local role on a client need not be authority.
    -- ClearLoot runs only against this same package's nonreplicating copy.
    local authority, replicates = actor:HasAuthority(), actor.bReplicates
    if (host and not authority) or replicates ~= false then
        return nil, "actor_role:" .. tostring(authority) .. ":" .. tostring(replicates)
    end
    if actor.SpawnSalt ~= profile.salt or type(actor.Looted) ~= "boolean" then return nil, "actor_stock_fields" end
    if profile.loot_override then
        local row = actor.Loot_Override
        local name = row and row.RowName
        if type(name) == "userdata" and type(name.ToString) == "function" then name = name:ToString() end
        if name ~= profile.loot_override or not same(row.DataTable, StaticFindObject(loot_table)) then
            return nil, "actor_loot_override"
        end
    end
    local first, second = actor.SM_Prop_Cabinet_Small_Door_01, actor.SM_Prop_Cabinet_Small_Door_02
    local frame = actor.SM_Prop_Cabinet_Small_02
    if not component(first, actor, profile, "SM_Prop_Cabinet_Small_Door_01", "StaticMeshComponent")
        or not component(second, actor, profile, "SM_Prop_Cabinet_Small_Door_02", "StaticMeshComponent")
        or not component(frame, actor, profile, "SM_Prop_Cabinet_Small_02", "StaticMeshComponent")
        or not same(first:GetAttachParent(), frame) or not same(second:GetAttachParent(), frame) then
        return nil, "door_identity"
    end
    local box_type = StaticFindObject("/Script/Engine.BoxComponent")
    if not valid(box_type) then return nil, "box_class" end
    -- DestroyComponent can leave the Blueprint Box member pointing at a
    -- retired component. Census the actor's owned live components instead.
    local boxes = actor:K2_GetComponentsByClass(box_type)
    local shape = type(boxes)
    if shape == "userdata" and type(boxes.type) == "function" then shape = boxes:type() end
    local count = (shape == "table" or shape == "TArray") and #boxes or nil
    if count ~= 0 and count ~= 1 then return nil, "box_census:" .. shape .. ":" .. tostring(count) end
    local box
    if count == 1 then
        box = boxes[1]
        if type(box) == "userdata" and type(box.type) == "function" and box:type() == "RemoteUnrealParam" then
            box = box:get()
        end
        if not component(box, actor, profile, "Box", "BoxComponent") or not same(box, actor.Box)
            or not same(box:GetAttachParent(), frame) then return nil, "box_identity" end
    end
    local closed = box and not actor.Looted and door_roll(first, 0) and door_roll(second, 0)
    local doors_open = door_roll(first, 95) and door_roll(second, -95)
    return { actor = actor, pawn = pawn, world = state.world, box = box, host = host,
        closed = closed, box_retired = count == 0, doors_open = doors_open, looted = actor.Looted,
        first_rotation = first.RelativeRotation, second_rotation = second.RelativeRotation }
end

local function qualified_epoch(value)
    return type(value) == "string" and #value == 32 and not value:find("[^0-9a-f]") and value:find("[^0]")
end

local function same_projection(scene)
    return projection and same(scene.world, projection.world) and same(scene.actor, projection.actor)
        and same(scene.pawn, projection.pawn)
end

function cabinet.clear()
    projection = nil
end

function cabinet.command(fields, state, phase)
    local command = fields[2]
    local kind = type(command) == "string" and command:match("^native%-(%l+)%-cabinet%-")
    if kind ~= "dust" and kind ~= "molasses" then return end
    local fixture = command == "native-" .. kind .. "-cabinet-stock-fixture"
    if not fixture and command ~= "native-" .. kind .. "-cabinet-project" then return end
    local profile = maps[state.map]
    if not profile or profile.kind ~= kind then error("stock_cabinet_exact_map_required", 0) end
    local scene, reason = inspect(state, phase)
    if fixture then
        if #fields ~= 3 or not host_fixture or not scene or not scene.host or not scene.closed
            or scene.pawn:GetFullName() ~= fields[3] or scene.pawn.Dead ~= false then
            error(profile.error .. "_fixture_requires_exact_closed_host_cabinet:" .. (reason or "scope"), 0)
        end
        stock_input.open_cabinet(state, phase, scene.pawn, scene.actor, scene.box, profile.source)
        return "stock host cabinet Interact dispatched"
    end

    local epoch = fields[3]
    if #fields ~= 5 or not client_fixture or not scene or scene.host or not qualified_epoch(epoch)
        or fields[4] ~= profile.name or fields[5] ~= scene.pawn:GetFullName() then
        error(profile.error .. "_projection_requires_exact_client_visit:" .. (reason or "scope"), 0)
    end
    if same_projection(scene) and projection.epoch == epoch and not scene.looted then
        return "stock cabinet presentation already requested"
    end
    if not scene.closed or not valid(StaticFindObject(profile.class_path .. ":ClearLoot")) then
        error(profile.error .. "_projection_requires_pristine_replica", 0)
    end
    -- This class's ClearLoot entry retires its own Box and animates its
    -- doors. Stock SpawnLoot, Resolve_Respawn and Event_SignalOpened are
    -- reached by the separate actual Interact entry.
    scene.actor:ClearLoot()
    if scene.actor.Looted ~= false then error("mutation_unverified_" .. profile.error .. "_projection", 0) end
    projection = { epoch = epoch, world = scene.world, actor = scene.actor, pawn = scene.pawn }
    return "stock cabinet presentation requested"
end

function cabinet.rows(state, phase, status_row)
    local profile = maps[state.map]
    if not profile then return {} end
    local tag, area = profile.tag, profile.area
    local scene, reason = inspect(state, phase)
    if not scene then
        return reason and {status_row(tag .. "-diagnostic", area, reason)} or {}
    end
    local name, epoch
    if scene.closed then
        name = "closed"
    elseif scene.host and scene.box_retired and scene.looted then
        name = "opened"
    elseif not scene.host and not scene.looted and same_projection(scene) then
        name, epoch = scene.box_retired and "projected" or "projecting", projection.epoch
    else
        return {status_row(tag .. "-diagnostic", area, "stock_animation_or_box", scene.looted,
            scene.box ~= nil)}
    end
    local rows = {status_row(tag, name, area, profile.name, scene.pawn:GetFullName(), epoch or "-",
        scene.closed and "closed" or scene.doors_open and "open" or "unverified")}
    if not scene.closed and not scene.doors_open then
        local first, second = scene.first_rotation or {}, scene.second_rotation or {}
        rows[#rows + 1] = status_row(tag .. "-diagnostic", area, "stock_door_angles",
            first.Pitch or "-", first.Yaw or "-", first.Roll or "-", second.Pitch or "-", second.Yaw or "-", second.Roll or "-")
    end
    return rows
end

return cabinet
