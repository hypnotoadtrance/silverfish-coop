local loot = {}
local guest_save = require("guest_save")

local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local level = map .. ".CrossRoads_P"
local crate_class = "/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C"
local crate_name = "LootCrate_Small_BP_C " .. level .. ":PersistentLevel.LootCrate_Small_BP_C_3"
local spawn_salt = -1911374248
local spawner_class = "/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C"
local box_class = "/Script/Engine.BoxComponent"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local item_class = "/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"
local armor_class = "/Game/Classes/Armor_Items/Item_ArmorMaster_BP.Item_ArmorMaster_BP_C"
local helmet_class = "/Game/Classes/Armor_Items/Item_HelmetMaster_BP.Item_HelmetMaster_BP_C"
local permanent_field = "PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"
local item_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local max_inventory, max_effects = 4096, 256

loot.map, loot.crate = map, crate_name

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(value, class)
    return valid(value) and same(value:GetClass(), class) and not value:GetFullName():find("Default__", 1, true)
end

local function authority(actor, world)
    return valid(actor) and same(actor:GetWorld(), world) and actor:HasAuthority() and not actor:IsActorBeingDestroyed()
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function lucky_name(value)
    if value ~= nil and type(value) ~= "string" then value = value:ToString() end
    return type(value) == "string" and value:lower() == "luckylooter"
end

local function array_size(array, maximum)
    local count = array and math.tointeger(array:GetArrayNum())
    if not count or count < 0 or count > maximum then
        error("native_loot_trait_array_unavailable", 0)
    end
    return count
end

local function with_luck(array)
    for index = 1, array_size(array, max_effects) do
        if lucky_name(array[index]) then return true end
    end
    return false
end

local function valid_guid(guid)
    if not guid then return false end
    local a, b, c, d = math.tointeger(guid.A), math.tointeger(guid.B), math.tointeger(guid.C), math.tointeger(guid.D)
    return a ~= nil and b ~= nil and c ~= nil and d ~= nil and (a ~= 0 or b ~= 0 or c ~= 0 or d ~= 0)
end

local function item_defaults(row, base, require_guid)
    local class = row and row[item_field]
    if not valid(class) or (require_guid and not valid_guid(row[guid_field])) then return nil end
    local cdo = class:GetCDO()
    if not valid(cdo) or not same(cdo:GetClass(), class) then
        error("native_loot_item_defaults_unavailable", 0)
    end
    if cdo:IsA(base) then return cdo end
end

local function item_luck(items, base, backpack)
    for index = 1, array_size(items, max_inventory) do
        local defaults = item_defaults(items[index], base, not backpack)
        if defaults then
            local contributes = not backpack
            if backpack then
                local kind, forced = defaults.ItemType, defaults.ForcePassives_InBackPack
                if type(kind) ~= "number" or not math.tointeger(kind) or kind < 0 or kind > 255
                    or type(forced) ~= "boolean" then error("native_loot_backpack_passive_gate_unavailable", 0) end
                contributes = kind == 0 or forced
            end
            if contributes and with_luck(defaults.PassiveEffects) then return true end
        end
    end
    return false
end

function loot.lucky_looter(save)
    if not exact(save, StaticFindObject(save_class)) then error("native_loot_trait_save_unavailable", 0) end
    local temporary = save.PlayerTempEffects
    if not temporary or #temporary > max_effects then error("native_loot_temp_effects_unavailable", 0) end
    local temporary_luck = false
    temporary:ForEach(function(key)
        if lucky_name(key:get()) then
            temporary_luck = true
            return true
        end
    end)
    if temporary_luck then return true, "temporary" end
    if save.Character and with_luck(save.Character[permanent_field]) then return true, "permanent" end
    if not save.Character then error("native_loot_character_traits_unavailable", 0) end

    -- Stock Compile_PassiveEffects reads class defaults with the backpack
    -- activation gates above and accepts GUID-valid equipped gear.
    local item = StaticFindObject(item_class)
    local armor = StaticFindObject(armor_class)
    local helmet = StaticFindObject(helmet_class)
    if not valid(item) or not valid(armor) or not valid(helmet) then
        error("native_loot_item_classes_unavailable", 0)
    end
    if item_luck(save.PlayerBackpack, item, true) then return true, "backpack" end
    local worn_armor = item_defaults(save.PlayerArmor, armor, true)
    if worn_armor and with_luck(worn_armor.PassiveEffects) then return true, "armor" end
    local worn_helmet = item_defaults(save.PlayerHelmet, helmet, true)
    if worn_helmet and with_luck(worn_helmet.PassiveEffects) then return true, "helmet" end
    if item_luck(save.PlayerEquipment, item, false) then return true, "equipment" end
    return false, "none"
end

local function host_context(state, phase)
    local world = state.world
    local host = state.local_pawn and state.local_pawn.pawn
    local controller = state.local_pawn and state.local_pawn.controller_object
    if phase ~= "hosting" or state.map ~= map or not state.listening or state.server
        or not valid(world) or world:GetFullName() ~= "World " .. level
        or not exact(host, StaticFindObject(pawn_class)) or not authority(host, world)
        or not exact(controller, StaticFindObject(controller_class)) or not authority(controller, world)
        or not controller:IsLocalController() or not same(controller.Pawn, host)
        or not same(host.Controller, controller) then
        error("native_loot_authority_unavailable", 0)
    end
    local statics = StaticFindObject(statics_path)
    local instance = valid(statics) and statics:GetGameInstance(host)
    local stock = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, StaticFindObject(instance_class)) or not exact(stock, StaticFindObject(save_class))
        or not same(host.Save, stock) then error("native_loot_canonical_save_unavailable", 0) end
    return host, world, instance, stock
end

local function participant(state, name, host, world)
    local match
    local class = StaticFindObject(pawn_class)
    for _, details in ipairs(state.pawns) do
        local pawn = details.pawn
        if details.name == name and exact(pawn, class) and authority(pawn, world) and pawn:GetFullName() == name
            and (same(pawn, host) or (details.kind == "guest" and guest_save.matches(pawn))) then
            if match then error("native_loot_opener_ambiguous", 0) end
            match = pawn
        end
    end
    if not match or match.Dead ~= false or not finite(match.Save.PlayerHealth) or match.Save.PlayerHealth <= 0 then
        error("native_loot_standing_opener_unavailable", 0)
    end
    return match
end

local function find_crate(world)
    local class = StaticFindObject(crate_class)
    local match
    for _, crate in ipairs(FindAllOf("LootCrate_Small_BP_C") or {}) do
        if exact(crate, class) and crate:GetFullName() == crate_name and authority(crate, world) then
            if match then error("native_loot_crate_ambiguous", 0) end
            match = crate
        end
    end
    if not match or match.SpawnSalt ~= spawn_salt then error("native_loot_authored_crate_unavailable", 0) end
    return match
end

local function child_spawners(crate, instance)
    local class = StaticFindObject(spawner_class)
    local spawners = { crate.Loot_Spawner, crate.Loot_Spawner1 }
    for index = 1, 2 do
        local child = spawners[index]
        if not exact(child, class) or not same(child:GetOwner(), crate) or not same(child.Container, crate)
            or not same(child["As Game Instance FP"], instance) then
            error("native_loot_exact_crate_spawners_unavailable", 0)
        end
    end
    if same(spawners[1], spawners[2]) then error("native_loot_crate_spawners_ambiguous", 0) end
    return spawners
end

local function distance_to_box(pawn, box, allow_distant)
    local position, target = pawn:K2_GetActorLocation(), box:K2_GetComponentLocation()
    local x, y, z = position.X - target.X, position.Y - target.Y, position.Z - target.Z
    local squared = x * x + y * y + z * z
    local range = pawn.InteractionRange
    if not finite(range) or range <= 0 or range > 1000 or not finite(squared)
        or not allow_distant and squared > range * range then
        error("native_loot_crate_out_of_reach", 0)
    end
    return target, math.sqrt(squared)
end

function loot.qualify(state, phase, pawn_name, requested_crate, allow_distant)
    if type(pawn_name) ~= "string" or requested_crate ~= nil and requested_crate ~= crate_name then
        error("native_loot_expected_exact_pawn_and_crate", 0)
    end
    local host, world, instance, stock = host_context(state, phase)
    local pawn = participant(state, pawn_name, host, world)
    local crate = find_crate(world)
    local box = crate.Box
    if crate.Looted ~= false or crate.SpawnedValue ~= 0 or crate.GuaranteeSpawn ~= false or crate.IgnoreKarma ~= false
        or not same(crate["As Game Instance FP"], instance) or not exact(box, StaticFindObject(box_class))
        or not same(box:GetOwner(), crate) then error("native_loot_crate_not_ready", 0) end
    local collision = box:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then error("native_loot_crate_box_not_interactable", 0) end
    local position, distance = distance_to_box(pawn, box, allow_distant)
    local spawners = child_spawners(crate, instance)
    local trait, source = loot.lucky_looter(pawn.Save)
    return { world = world, instance = instance, stock = stock, pawn = pawn, save = pawn.Save, crate = crate,
        box = box, spawners = spawners, lucky_looter = trait, lucky_source = source,
        position = position, distance = distance }
end

function loot.approach_fixture(state, phase, pawn_name, requested_crate)
    local scope = loot.qualify(state, phase, pawn_name, requested_crate, true)
    local pawn, point = scope.pawn, scope.position
    local rotation = pawn:K2_GetActorRotation()
    for _, distance in ipairs({ 180, 240, 130 }) do
        for _, offset in ipairs({ { distance, 0 }, { -distance, 0 }, { 0, distance }, { 0, -distance } }) do
            for _, height in ipairs({ 50, 100, 0 }) do
                if pawn:K2_TeleportTo({ X = point.X + offset[1], Y = point.Y + offset[2], Z = point.Z + height }, rotation) then
                    pawn:ForceNetUpdate()
                    return loot.qualify(state, phase, pawn_name, requested_crate)
                end
            end
        end
    end
    error("native_loot_crate_has_no_qualified_fixture_position", 0)
end

function loot.claim_context(state, phase, pawn_name, requested_crate)
    if requested_crate ~= crate_name then error("native_loot_expected_exact_crate", 0) end
    local host, world, instance, stock = host_context(state, phase)
    local pawn = participant(state, pawn_name, host, world)
    local crate = find_crate(world)
    local target, from = crate:K2_GetActorLocation(), pawn:K2_GetActorLocation()
    local dx, dy, dz = target.X - from.X, target.Y - from.Y, target.Z - from.Z
    local range = pawn.InteractionRange
    local distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if not finite(range) or range <= 0 or range > 1000 or not finite(distance) or distance > range then
        error("native_loot_crate_out_of_reach", 0)
    end
    return { pawn = pawn, save = pawn.Save, stock = stock, distance = distance }
end

-- The native hook supplies only the item addresses captured from this exact
-- crate's two SpawnLoot out parameters. Nearby world items have no provenance.
function loot.results(state, phase, scope, item_addresses)
    local host, world, instance, stock = host_context(state, phase)
    if type(scope) ~= "table" or not same(scope.world, world) or not same(scope.instance, instance)
        or not same(scope.stock, stock) or not same(find_crate(world), scope.crate)
        or not authority(scope.pawn, world) or not same(scope.pawn.Save, scope.save)
        or not (same(scope.pawn, host) or guest_save.matches(scope.pawn))
        or scope.crate.Looted ~= true or type(item_addresses) ~= "table" or #item_addresses > 2 then
        error("native_loot_result_scope_unavailable", 0)
    end
    local expected = {}
    for index, address in pairs(item_addresses) do
        if type(index) ~= "number" or not math.tointeger(index) or index < 1 or index > #item_addresses
            or type(address) ~= "number" or not math.tointeger(address) or address <= 0 or expected[address] then
            error("native_loot_result_identity_invalid", 0)
        end
        expected[address] = index
    end
    local result = {}
    if #item_addresses == 0 then return result end
    local class = StaticFindObject(item_class)
    if not valid(class) then error("native_loot_item_class_unavailable", 0) end
    for _, actor in ipairs(FindAllOf("Item_Master_BP_C") or {}) do
        if authority(actor, world) and actor:IsA(class) then
            local address = actor:GetAddress()
            local index = expected[address]
            if index then
                if result[index] then error("native_loot_result_item_ambiguous", 0) end
                local path = actor:GetClass():GetFullName():match("^%S+ (/Game/%S+)$")
                if not path then error("native_loot_result_item_class_unavailable", 0) end
                result[index] = { address = address, actor = actor:GetFullName(), class_path = path, object = actor }
            end
        end
    end
    for index = 1, #item_addresses do
        if not result[index] then error("native_loot_result_item_unavailable", 0) end
    end
    return result
end

return loot
