local loot = {}
local guest_save = require("guest_save")
local registry = require("authored_loot_registry")

local crate_class = "/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C"
local cabinet_class = "/Game/Classes/Actors/Lootables/LootCabnet_Small_BP.LootCabnet_Small_BP_C"
local desk_class = "/Game/Classes/Actors/Lootables/LootCabinet_Desk_BP.LootCabinet_Desk_BP_C"
local spawner_class = "/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C"
local box_class = "/Script/Engine.BoxComponent"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local code_library = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local replenish_rule = "ReplenishLoot_5_0542042A4D8D98E206019BA9B5A3E23F"
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
local crate_fixture_trace_color = {R = 0, G = 0, B = 0, A = 1}
local docks_map = "/Game/Classes/Maps/Maps_OverWorld/Docks_01_P"

local maps, names = {}, {}
for _, profile in ipairs(registry) do
    maps[profile.map] = maps[profile.map] or {}
    table.insert(maps[profile.map], profile)
    names[profile.name] = profile
    profile.world = profile.path:match("^(.-):PersistentLevel%.")
    profile.capacity = (profile.small_cabinet or profile.office_desk) and 4 or 2
end

function loot.profiles(map)
    return maps[map] or {}
end

function loot.profile(map, name)
    local profile = name == nil and loot.profiles(map)[1] or names[name]
    return profile and profile.map == map and profile or nil
end

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

function loot.temporary_activation(crate, pawn)
    if not valid(crate) or crate.Replenish_Mode ~= 1 or not valid(pawn)
        or not pawn:HasAuthority() or not same(crate:GetWorld(), pawn:GetWorld()) then return end
    local instance = crate["As Game Instance FP"]
    local save = exact(instance, StaticFindObject(instance_class)) and instance.SaveSlot_Ref
    if not exact(save, StaticFindObject(save_class)) or not same(pawn.Save, save) or not save.GameRules
        or save.GameRules[replenish_rule] ~= true then return end
    local library = StaticFindObject(code_library)
    if not valid(library) then return end
    local result = {}
    library:Find_ActorOnTempActivations(crate, crate, result)
    if type(result.Found) == "boolean" then return result.Found, save, library end
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

local function host_context(state, phase, profile)
    local world = state.world
    local host = state.local_pawn and state.local_pawn.pawn
    local controller = state.local_pawn and state.local_pawn.controller_object
    if phase ~= "hosting" or not profile or state.map ~= profile.map or not state.listening or state.server
        or not valid(world) or world:GetFullName() ~= "World " .. profile.world
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

local function find_crate(world, profile)
    local class = StaticFindObject(profile.office_desk and desk_class or profile.small_cabinet and cabinet_class or crate_class)
    local crate = StaticFindObject(profile.path)
    if not exact(crate, class) or crate:GetFullName() ~= profile.name or not authority(crate, world)
        or crate.SpawnSalt ~= profile.salt then
        error("native_loot_authored_crate_unavailable", 0)
    end
    return crate
end

local function child_spawners(crate, instance, profile)
    local class = StaticFindObject(spawner_class)
    local spawners = { crate.Loot_Spawner, crate.Loot_Spawner1 }
    if profile.capacity == 4 then
        spawners[3], spawners[4] = crate.Loot_Spawner2, crate.Loot_Spawner3
    end
    for index = 1, profile.capacity do
        local child = spawners[index]
        if not exact(child, class) or not same(child:GetOwner(), crate) or not same(child.Container, crate)
            or not same(child["As Game Instance FP"], instance) then
            error("native_loot_exact_crate_spawners_unavailable", 0)
        end
        for previous = 1, index - 1 do
            if same(spawners[previous], child) then error("native_loot_crate_spawners_ambiguous", 0) end
        end
    end
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
    local profile = loot.profile(state.map, requested_crate)
    if type(pawn_name) ~= "string" or not profile then
        error("native_loot_expected_exact_pawn_and_crate", 0)
    end
    local host, world, instance, stock = host_context(state, phase, profile)
    local pawn = participant(state, pawn_name, host, world)
    local crate = find_crate(world, profile)
    local box = crate.Box
    if crate.Looted ~= false or crate.SpawnedValue ~= 0 or crate.GuaranteeSpawn ~= (profile.guarantee_spawn == true)
        or crate.IgnoreKarma ~= false or profile.replenish_mode and crate.Replenish_Mode ~= profile.replenish_mode
        or not same(crate["As Game Instance FP"], instance) or not exact(box, StaticFindObject(box_class))
        or not same(box:GetOwner(), crate) then error("native_loot_crate_not_ready", 0) end
    local collision = box:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then error("native_loot_crate_box_not_interactable", 0) end
    local position, distance = distance_to_box(pawn, box, allow_distant)
    local spawners = child_spawners(crate, instance, profile)
    local trait, source = loot.lucky_looter(pawn.Save)
    return { world = world, instance = instance, stock = stock, pawn = pawn, save = pawn.Save, crate = crate, profile = profile,
        box = box, spawners = spawners, lucky_looter = trait, lucky_source = source,
        position = position, distance = distance }
end

local function crate_fixture_floor(state, phase, scope, pawn_name, requested_crate, rotation)
    local pawn, box, point = scope.pawn, scope.box, scope.position
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local capsule, camera = pawn.CapsuleComponent, pawn.Camera
    local origin, box_extent = pawn:K2_GetActorLocation(), box:GetScaledBoxExtent()
    if not valid(trace) or not valid(capsule) or not same(capsule:GetOwner(), pawn) or not valid(camera)
        or not same(camera:GetOwner(), pawn) or not origin or not box_extent or not finite(box_extent.Z) or box_extent.Z <= 0 then
        error("native_loot_crate_fixture_floor_scope_unavailable", 0)
    end
    local half_height = capsule:GetScaledCapsuleHalfHeight()
    if not finite(half_height) or half_height < 20 or half_height > 150 then
        error("native_loot_crate_fixture_capsule_unavailable", 0)
    end
    local ignore = {scope.crate}
    for _, detail in ipairs(state.pawns) do
        if valid(detail.pawn) then ignore[#ignore + 1] = detail.pawn end
    end
    local forward = box:GetForwardVector()
    local bearing = forward and finite(forward.X) and finite(forward.Y) and math.atan(forward.Y, forward.X) or 0
    local moved = false
    for _, distance in ipairs({125, 155, 195, 225}) do
        for _, turn in ipairs({0, 45, -45, 90, -90, 135, -135, 180}) do
            local angle = bearing + math.rad(turn)
            local x, y = point.X + distance * math.cos(angle), point.Y + distance * math.sin(angle)
            local top, bottom = point.Z + 135, point.Z - 235
            local hit = {}
            local found = trace:LineTraceSingle(pawn, {X = x, Y = y, Z = top}, {X = x, Y = y, Z = bottom},
                0, false, ignore, 0, hit, true, crate_fixture_trace_color, crate_fixture_trace_color, 0)
            hit = hit.OutHit or hit
            local floor, normal = hit.ImpactPoint, hit.ImpactNormal
            if found == true and floor and normal and finite(floor.Z) and finite(normal.Z) and normal.Z >= 0.7
                and floor.Z >= bottom and floor.Z < top - 1 then
                local at = {X = x, Y = y, Z = floor.Z + half_height + 2}
                if pawn:K2_TeleportTo(at, rotation) then
                    moved = true
                    local actual, eye = pawn:K2_GetActorLocation(), camera:K2_GetComponentLocation()
                    if actual and eye and finite(eye.Z) and eye.Z > point.Z + box_extent.Z + 12
                        and (actual.X - at.X)^2 + (actual.Y - at.Y)^2 + (actual.Z - at.Z)^2 <= 18^2 then
                        local checked, current = pcall(loot.qualify, state, phase, pawn_name, requested_crate)
                        if checked then
                            pawn:ForceNetUpdate()
                            return current
                        end
                    end
                end
            end
        end
    end
    if moved and not pawn:K2_TeleportTo(origin, rotation) then error("mutation_unverified_native_loot_crate_fixture_floor_rollback", 0) end
    if moved then pawn:ForceNetUpdate() end
    error("native_loot_crate_fixture_no_floor_above_focus_plane", 0)
end

function loot.approach_fixture(state, phase, pawn_name, requested_crate)
    local scope = loot.qualify(state, phase, pawn_name, requested_crate, true)
    local pawn, point = scope.pawn, scope.position
    local rotation = pawn:K2_GetActorRotation()
    if scope.profile.capacity == 4 or scope.profile.guarantee_spawn and scope.profile.map == docks_map then
        local input = require("native_authored_pickup_input_fixture")
        input.approach_exact_container(state, phase, scope.profile, pawn, scope.crate, scope.box)
        return loot.qualify(state, phase, pawn_name, requested_crate)
    end
    if scope.profile.index > 3 then return crate_fixture_floor(state, phase, scope, pawn_name, requested_crate, rotation) end
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

function loot.claim_context(state, phase, pawn_name, requested_crate, point)
    local profile = requested_crate and loot.profile(state.map, requested_crate)
    if not profile then error("native_loot_expected_exact_crate", 0) end
    local host, world, _, stock = host_context(state, phase, profile)
    local pawn = participant(state, pawn_name, host, world)
    local crate = find_crate(world, profile)
    if crate.Looted ~= true then error("native_loot_crate_not_open", 0) end
    local target, from = point or crate:K2_GetActorLocation(), pawn:K2_GetActorLocation()
    if not target or not from or not finite(target.X) or not finite(target.Y) or not finite(target.Z) then
        error("native_loot_item_position_invalid", 0)
    end
    local dx, dy, dz = target.X - from.X, target.Y - from.Y, target.Z - from.Z
    local range = pawn.InteractionRange
    local distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if not finite(range) or range <= 0 or range > 1000 or not finite(distance)
        or distance > (point and math.min(range, 250) or range) then
        error("native_loot_crate_out_of_reach", 0)
    end
    return { pawn = pawn, save = pawn.Save, stock = stock, distance = distance, crate = crate, profile = profile }
end

function loot.placement(state, phase, requested_crate)
    local profile = loot.profile(state.map, requested_crate)
    local _, world, instance = host_context(state, phase, profile)
    local crate = find_crate(world, profile)
    if crate.Looted ~= true then error("native_loot_crate_not_open", 0) end
    local origin, positions = crate:K2_GetActorLocation(), {}
    for index, child in ipairs(child_spawners(crate, instance, profile)) do
        local at, rotation = child:K2_GetComponentLocation(), child:K2_GetComponentRotation()
        local position = {at.X, at.Y, at.Z, rotation.Pitch, rotation.Yaw, rotation.Roll, 1, 1, 1}
        for axis = 1, 9 do
            if not finite(position[axis]) then error("native_loot_child_position_invalid", 0) end
        end
        if (at.X - origin.X)^2 + (at.Y - origin.Y)^2 + (at.Z - origin.Z)^2 > 250^2 then
            error("native_loot_child_position_invalid", 0)
        end
        positions[index] = position
    end
    return positions
end

function loot.replenish_fixture(state, phase, requested_crate)
    local profile = loot.profile(state.map, requested_crate)
    local host, world, instance = host_context(state, phase, profile)
    local crate = find_crate(world, profile)
    local active, save, library = loot.temporary_activation(crate, host)
    local day_profile = require("authored_world_view")[state.map]
    local day_path = day_profile and day_profile.day:match("^%S+ (/Game/%S+)$")
    local day = day_path and StaticFindObject(day_path)
    local day_class = StaticFindObject("/Game/Classes/Actors/DayCycle_SunControler_BP.DayCycle_SunControler_BP_C")
    if active ~= true or crate.Looted ~= true or not exact(day, day_class)
        or not authority(day, world) or not same(day["Game Instance"], instance) then
        error("native_loot_temporary_activation_fixture_unavailable", 0)
    end
    local function fill_minimum()
        for _, other in ipairs(loot.profiles(state.map)) do
            if save.TempActivations:GetArrayNum() >= 3 then return end
            local actor = StaticFindObject(other.path)
            if authority(actor, world) and actor:GetFullName() == other.name and actor.Replenish_Mode == 1 then
                library:AddActor_ToTempActivations(actor, actor, {})
            end
        end
        if save.TempActivations:GetArrayNum() < 3 then error("native_loot_replenishment_fixture_too_few_activations", 0) end
    end
    for days = 1, 12 do
        fill_minimum()
        local before = save.TempActivations:GetArrayNum()
        day:Daily_Update()
        if not same(instance.SaveSlot_Ref, save) or save.TempActivations:GetArrayNum() >= before then
            error("native_loot_replenishment_fixture_daily_update_unverified", 0)
        end
        if loot.temporary_activation(crate, host) == false and crate.Looted == true then
            return days
        end
    end
    error("native_loot_replenishment_fixture_activation_still_present", 0)
end

-- Each address comes from an exact owned child's stock SpawnLoot out parameter.
function loot.results(state, phase, scope, item_addresses)
    local profile = type(scope) == "table" and scope.profile
    local host, world, instance, stock = host_context(state, phase, profile)
    if not same(scope.world, world) or not same(scope.instance, instance)
        or not same(scope.stock, stock) or not same(find_crate(world, profile), scope.crate)
        or not authority(scope.pawn, world) or not same(scope.pawn.Save, scope.save)
        or not (same(scope.pawn, host) or guest_save.matches(scope.pawn))
        or scope.crate.Looted ~= true or type(item_addresses) ~= "table" or #item_addresses > profile.capacity then
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
                local location, rotation, scale = actor:K2_GetActorLocation(), actor:K2_GetActorRotation(),
                    actor:GetActorScale3D()
                if not location or not rotation or not scale then error("native_loot_spawn_transform_unavailable", 0) end
                local transform = { location.X, location.Y, location.Z, rotation.Pitch, rotation.Yaw, rotation.Roll,
                    scale.X, scale.Y, scale.Z }
                for axis = 1, 9 do
                    if not finite(transform[axis]) then error("native_loot_spawn_transform_unavailable", 0) end
                end
                result[index] = { address = address, actor = actor:GetFullName(), class_path = path, object = actor,
                    transform = transform }
            end
        end
    end
    for index = 1, #item_addresses do
        if not result[index] then error("native_loot_result_item_unavailable", 0) end
    end
    return result
end

return loot
