local eather = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_TRANSPORT") == "1"
local gameplay_statics = "/Script/Engine.Default__GameplayStatics"
local item_library = "/Game/Classes/Libraries/Fuctions_Lib/FL_Items.Default__FL_Items_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_value = "Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"
local zero_guid = string.rep("0", 32)
local nominal_cost = 100
local canister_category = 10
local inventory_limit = 4096
local i32_min, i32_max = -2147483648, 2147483647

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact_class(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function bounded_int(value, low, high)
    return type(value) == "number" and math.tointeger(value) ~= nil and value >= low and value <= high
end

local function valid_remainder(value)
    return type(value) == "number" and value >= 0 and value < 1
end

local function canonical_save(instance, pawn)
    if not enabled or not exact_class(pawn, pawn_class) or not exact_class(instance, instance_class)
        or not pawn:HasAuthority() or pawn:IsActorBeingDestroyed() then
        error("matrix_eather_host_required", 0)
    end
    local world, controller = pawn:GetWorld(), pawn.Controller
    local driver = valid(world) and world.NetDriver
    local statics = StaticFindObject(gameplay_statics)
    if not valid(driver) or valid(driver.ServerConnection) or not same(driver.World, world)
        or not valid(controller) or not controller:IsLocalController() or not same(controller.Pawn, pawn)
        or not valid(statics) or not same(statics:GetGameInstance(pawn), instance)
        or not same(instance["As Player FP"], pawn) then
        error("matrix_eather_host_required", 0)
    end
    local save = instance.SaveSlot_Ref
    if not exact_class(save, save_class) or not same(pawn.Save, save) then
        error("matrix_eather_canonical_save_required", 0)
    end
    return save
end

local function guid_string(guid)
    if not guid or not bounded_int(guid.A, i32_min, i32_max) or not bounded_int(guid.B, i32_min, i32_max)
        or not bounded_int(guid.C, i32_min, i32_max) or not bounded_int(guid.D, i32_min, i32_max) then
        error("matrix_eather_canister_guid_invalid", 0)
    end
    local text = string.format("%08X%08X%08X%08X", guid.A & 0xffffffff, guid.B & 0xffffffff,
        guid.C & 0xffffffff, guid.D & 0xffffffff)
    if text == zero_guid then error("matrix_eather_canister_guid_invalid", 0) end
    return text
end

local function read_snapshot(instance, pawn)
    local save = canonical_save(instance, pawn)
    local remainder, backpack = instance.EatherRemainder, save.PlayerBackpack
    local count = backpack and backpack:GetArrayNum()
    if not valid_remainder(remainder) or not bounded_int(count, 0, inventory_limit) then
        error("matrix_eather_stock_schema_unavailable", 0)
    end
    local canisters, total = {}, 0
    for index = 1, count do
        local row = backpack[index]
        local class = row and row[item_class]
        if not valid(class) then error("matrix_eather_stock_backpack_row_invalid", 0) end
        local defaults = class:GetCDO()
        local category = valid(defaults) and defaults.ItemType
        if not bounded_int(category, 0, 255) then error("matrix_eather_stock_item_type_unavailable", 0) end
        if category == canister_category then
            local guid = guid_string(row[item_guid])
            local ammo, value = row[item_ammo], row[item_value]
            if canisters[guid] or not bounded_int(ammo, 0, i32_max) or not bounded_int(value, i32_min, i32_max) then
                error("matrix_eather_canister_invalid", 0)
            end
            canisters[guid] = { ammo = ammo, value = value }
            total = total + ammo
            if total > i32_max then error("matrix_eather_stock_total_out_of_range", 0) end
        end
    end
    return { remainder = remainder, canisters = canisters }, total, count, save
end

local function same_canisters(first, second)
    if type(second) ~= "table" then return false end
    for guid, row in pairs(first) do
        local expected = second[guid]
        if type(expected) ~= "table" or row.ammo ~= expected.ammo or row.value ~= expected.value then
            return false
        end
        for key in pairs(expected) do
            if key ~= "ammo" and key ~= "value" then return false end
        end
    end
    for guid in pairs(second) do
        if not first[guid] then return false end
    end
    return true
end

local function same_snapshot(first, second)
    return type(second) == "table" and first.remainder == second.remainder
        and same_canisters(first.canisters, second.canisters)
end

local function qualify(instance, pawn, expected)
    local before, total, count, save = read_snapshot(instance, pawn)
    if expected ~= nil and not same_snapshot(before, expected) then
        error("matrix_eather_before_mismatch", 0)
    end

    local active, instances = {}, {}
    instance:FindPassiveEffect(FName("EatherAffinity"), true, active, instances)
    local affinity = instances.Instances
    if not bounded_int(affinity, 0, i32_max) or active.EffectActive ~= (affinity > 0) then
        error("matrix_eather_affinity_unavailable", 0)
    end
    local factor = 0.75 ^ affinity
    if not (factor > 0 and factor <= 1) then error("matrix_eather_affinity_out_of_range", 0) end

    local library = StaticFindObject(item_library)
    if not valid(library) then error("matrix_eather_stock_library_unavailable", 0) end
    local raw, adjusted = {}, {}
    library:FindEather_InBackPack(instance, false, pawn, raw)
    library:FindEather_InBackPack(instance, true, pawn, adjusted)
    local ratio = count == 0 and 0 or (total + before.remainder) / factor
    if not (ratio >= 0 and ratio < i32_max + 1) or raw.EatherFound ~= total
        or adjusted.EatherFound ~= math.floor(ratio) then
        error("matrix_eather_availability_mismatch", 0)
    end

    local true_cost = nominal_cost * factor - before.remainder
    local charged = math.ceil(true_cost)
    local after_remainder = charged - true_cost
    if adjusted.EatherFound < nominal_cost or charged > total then
        error("matrix_eather_insufficient", 0)
    end
    if not bounded_int(charged, 0, nominal_cost) or not valid_remainder(after_remainder) then
        error("matrix_eather_stock_cost_invalid", 0)
    end
    return {
        before = before,
        available = adjusted.EatherFound,
        affinity_instances = affinity,
        charged = charged,
        after_remainder = after_remainder,
    }, save
end

function eather.snapshot(instance, pawn)
    return (read_snapshot(instance, pawn))
end

function eather.qualify(instance, pawn, expected)
    return (qualify(instance, pawn, expected))
end

function eather.spend(instance, pawn, expected)
    if type(expected) ~= "table" then error("matrix_eather_expected_snapshot_required", 0) end
    local quote, save = qualify(instance, pawn, expected)
    local before, _, _, current_save = read_snapshot(instance, pawn)
    if not same(current_save, save) or not same_snapshot(before, quote.before) then
        error("matrix_eather_before_mismatch", 0)
    end

    local ok, after = pcall(function()
        instance:SpendEather(nominal_cost)
        local snapshot, _, _, observed_save = read_snapshot(instance, pawn)
        if not same(observed_save, save) then return nil end
        return snapshot
    end)
    if not ok or not after or math.abs(after.remainder - quote.after_remainder) > 1e-12 then
        error("matrix_eather_unverified_after_spend", 0)
    end

    local charged = 0
    for guid, old in pairs(before.canisters) do
        local new = after.canisters[guid]
        if not new or new.ammo > old.ammo then error("matrix_eather_unverified_after_spend", 0) end
        charged = charged + old.ammo - new.ammo
    end
    for guid in pairs(after.canisters) do
        if not before.canisters[guid] then error("matrix_eather_unverified_after_spend", 0) end
    end
    if charged ~= quote.charged then error("matrix_eather_unverified_after_spend", 0) end
    return { nominal_cost = nominal_cost, charged = charged, before = before, after = after }
end

function eather.restore_remainder(instance, pawn, expected)
    if type(expected) ~= "table" or not valid_remainder(expected.remainder) then
        error("matrix_eather_expected_snapshot_required", 0)
    end
    local current, _, _, save = read_snapshot(instance, pawn)
    if not same_canisters(current.canisters, expected.canisters) then
        error("matrix_eather_before_mismatch", 0)
    end
    if current.remainder == expected.remainder then return current end
    if current.remainder ~= 0 then error("matrix_eather_recovery_carry_conflict", 0) end

    -- A restarted GameInstance has zero carry even when its canonical save retains every canister.
    instance.EatherRemainder = expected.remainder
    local updated, _, _, observed_save = read_snapshot(instance, pawn)
    if not same(observed_save, save) or not same_snapshot(updated, expected) then
        error("matrix_eather_recovery_unverified", 0)
    end
    return updated
end

return eather
