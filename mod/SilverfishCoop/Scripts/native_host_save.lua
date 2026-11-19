local host_save = {}

local main_menu = "/Game/Classes/Maps/MainMenu_P"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local receipt

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    if not valid(object) or object:GetFullName():find("Default__", 1, true) then return false end
    return same(object:GetClass(), StaticFindObject(path))
end

local function generation_slot(slot)
    return type(slot) == "string" and #slot == 39 and slot:match("^SFCoop_[0-9a-f]+$") ~= nil
end

local function owner(state)
    local controller, world = state.controller, state.world
    if not valid(world) or not exact(controller, controller_path) or not controller:IsLocalController()
        or not controller:HasAuthority() or not same(controller:GetWorld(), world) or state.server
        or world:GetFullName():match("^World (/[^%.:]+)%.") ~= state.map then
        return
    end
    local statics = StaticFindObject(statics_path)
    local instance = valid(statics) and statics:GetGameInstance(controller)
    if not exact(instance, instance_path) or not exact(instance.SaveSlot_Ref, save_path) then return end
    return instance, statics
end

local function canonical_host(state, phase)
    if phase ~= "hosting" or not state.listening or not state.local_pawn then return end
    local instance, statics = owner(state)
    if not instance then return end
    local pawn = state.local_pawn.pawn
    if not exact(pawn, pawn_path) or not pawn:HasAuthority() or not same(state.controller.Pawn, pawn)
        or not same(pawn.Controller, state.controller) or not same(pawn:GetWorld(), state.world)
        or not same(pawn.Save, instance.SaveSlot_Ref) then
        return
    end
    return instance, pawn, statics
end

local function summary(save)
    local seed = math.tointeger(save.InstanceSeed)
    local backpack = save.PlayerBackpack
    local equipment = save.PlayerEquipment
    local backpack_count = backpack and math.tointeger(backpack:GetArrayNum())
    local equipment_count = equipment and math.tointeger(equipment:GetArrayNum())
    if not seed or seed < -2147483648 or seed > 2147483647
        or not backpack_count or backpack_count < 0 or not equipment_count or equipment_count < 0 then
        error("The canonical stock save summary is unavailable", 0)
    end
    return { seed = seed, backpack = backpack_count, equipment = equipment_count }
end

function host_save.capture(state, phase, slot)
    if not generation_slot(slot) then error("An exact co-op save generation slot is required", 0) end
    local instance, pawn, statics = canonical_host(state, phase)
    if not instance then error("The authoritative host does not have a canonical stock save", 0) end
    local save = instance.SaveSlot_Ref
    local observed = summary(save)
    if statics:DoesSaveGameExist(slot, 0) ~= false then error("The co-op save generation already exists", 0) end
    if statics:SaveGameToSlot(save, slot, 0) ~= true then error("Stock save serialization failed", 0) end

    local verified = statics:LoadGameFromSlot(slot, 0)
    if not exact(verified, save_path) or same(verified, save) or not same(instance.SaveSlot_Ref, save)
        or not same(pawn.Save, save) then
        error("The co-op save generation failed stock readback", 0)
    end
    local readback = summary(verified)
    if observed.seed ~= readback.seed or observed.backpack ~= readback.backpack or observed.equipment ~= readback.equipment then
        error("The co-op save generation summary did not survive stock serialization", 0)
    end
    receipt = { kind = "captured", slot = slot, map = state.map, pawn = pawn:GetFullName(),
        instance = instance:GetAddress(), save = save:GetAddress(), summary = readback }
    return "canonical stock host save captured"
end

function host_save.prepare(state, phase, slot)
    if not generation_slot(slot) then error("An exact co-op save generation slot is required", 0) end
    if phase ~= "ready" or state.map ~= main_menu or state.listening then
        error("Load the co-op save generation before leaving the staged title menu", 0)
    end
    local instance, statics = owner(state)
    if not instance then error("The staged title menu does not have a canonical stock save", 0) end
    local previous = instance.SaveSlot_Ref
    local loaded = statics:LoadGameFromSlot(slot, 0)
    if not exact(loaded, save_path) or same(loaded, previous) then error("Stock co-op save generation could not be loaded", 0) end
    local observed = summary(loaded)
    instance.SaveSlot_Ref = loaded
    if not same(instance.SaveSlot_Ref, loaded) then
        instance.SaveSlot_Ref = previous
        error("Stock GameInstance did not retain the loaded co-op save", 0)
    end
    receipt = { kind = "prepared", slot = slot, instance = instance:GetAddress(), save = loaded:GetAddress(), summary = observed }
    return "canonical stock host save prepared before travel"
end

function host_save.rows(state, phase, status_row)
    if not receipt then return {} end
    local kind, pawn_name = receipt.kind, receipt.pawn or ""
    if kind == "prepared" then
        if phase == "ready" and state.map == main_menu then
            local instance = owner(state)
            if not instance or receipt.instance ~= instance:GetAddress() or receipt.save ~= instance.SaveSlot_Ref:GetAddress() then
                kind = "changed"
            end
        elseif phase == "hosting" then
            local instance = owner(state)
            local _, pawn = canonical_host(state, phase)
            receipt.arrival_seen = receipt.arrival_seen or os.time()
            if instance and (receipt.instance ~= instance:GetAddress() or receipt.save ~= instance.SaveSlot_Ref:GetAddress()) then
                kind = "changed"
            elseif pawn then
                kind, pawn_name = "restored", pawn:GetFullName()
            else
                kind = os.time() - receipt.arrival_seen < 3 and "verifying" or "changed"
            end
        else
            return {}
        end
    elseif phase ~= "hosting" or state.map ~= receipt.map then
        return {}
    else
        local instance, pawn = canonical_host(state, phase)
        if not instance or receipt.instance ~= instance:GetAddress() or receipt.save ~= instance.SaveSlot_Ref:GetAddress()
            or receipt.pawn ~= pawn:GetFullName() then
            kind = "changed"
        end
    end
    local observed = receipt.summary
    return { status_row("native-host-save", kind, receipt.slot, state.map, pawn_name,
        observed.seed, observed.backpack, observed.equipment) }
end

return host_save
